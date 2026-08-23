"""Recoverable day-level orchestrator for Cognitive Secretary V1.

This module is deliberately semantic-free.  It serializes one local day,
invokes the already bounded pipeline, renders the committed Daily Review,
gates the existing Agent V1 adapter, and finally publishes the deterministic
landscape/home projections.  It reads formal state only through public store
methods and persists a small audit checkpoint containing IDs, hashes, counts,
statuses, and allow-listed warning codes -- never raw record text.

The committed daily bundle is the transaction boundary.  Review, long-term
judgement, and projection failures are recorded as independent warnings and
can be retried without rolling that bundle back or repeating paid model work.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import errno
import fcntl
import hashlib
import json
import os
import stat
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from agent_v1 import build_agent_profile
from cognitive_agent_adapter_v1 import CognitiveAgentAdapter
from cognitive_bundle_store_v1 import CognitiveBundleStore
from cognitive_pipeline_v1 import CognitivePipeline, DayPipelineResult
from cognitive_projection_v1 import CognitiveProjectionPublisher
from cognitive_v1 import (
    DailySummaryRevision,
    InterpretationReceiptRevision,
    ObjectRef,
    SourceRecordRevision,
    persisted_sha256,
)
from core import ContractError, canonical_json, sha256_bytes


ORCHESTRATOR_VERSION = "cognitive-day-orchestrator-v1.1"
MAX_STATUS_BYTES = 1024 * 1024
ZERO_SHA256 = "0" * 64

TRIGGERS = frozenset({"manual", "scheduled", "recovery"})
STAGES = frozenset(
    {
        "not_started",
        "running_pipeline",
        "pipeline_completed",
        "rendering_review",
        "judging_long_term",
        "projecting",
        "finished",
    }
)
RUN_STATUSES = frozenset(
    {
        "not_started",
        "running",
        "committed",
        "committed_with_warnings",
        "no_change",
        "no_candidate",
        "no_records",
        "no_receipts",
        "stale",
        "error",
        "budget_exhausted",
    }
)
SUBTASK_STATUSES = frozenset(
    {
        "not_started",
        "completed",
        "no_change",
        "no_material",
        "already_linked",
        "recovered",
        "skipped",
        "failed",
    }
)
WARNINGS = frozenset(
    {
        "review_failed",
        "long_term_failed",
        "landscape_failed",
        "partial_source_unavailable",
    }
)
STATUS_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "orchestrator_version",
        "local_date",
        "trigger",
        "status",
        "stage",
        "started_at",
        "updated_at",
        "completed_at",
        "attempt",
        "pipeline_status",
        "pipeline_profile_sha256",
        "result_profile_sha256",
        "bundle_ref",
        "record_count",
        "receipt_count",
        "review_status",
        "review_sha256",
        "long_term_required",
        "long_term_status",
        "material_sha256",
        "agent_result_ref",
        "projection_status",
        "landscape_ref",
        "home_projection_sha256",
        "warnings",
        "error_kind",
    }
)


class DailyReviewRenderer(Protocol):
    def render(
        self,
        *,
        summary: DailySummaryRevision,
        summary_ref: ObjectRef,
        sources: Sequence[SourceRecordRevision],
        receipts: Sequence[InterpretationReceiptRevision],
    ) -> Any: ...


class LongTermAdapter(Protocol):
    def process(self, **kwargs: Any) -> Any: ...


class ProjectionPublisher(Protocol):
    def publish(self, **kwargs: Any) -> Any: ...


class ReviewBindingStore(Protocol):
    """Minimum public CAS required to make a rendered Review formal state."""

    def append_review_result(
        self,
        *,
        expected_bundle_ref: ObjectRef,
        expected_summary_ref: ObjectRef,
        review_file: str,
        review_sha256: str,
        user_supplement_sha256: str | None,
        now: dt.datetime,
    ) -> Any: ...


def _clock_now(clock: Callable[[], dt.datetime]) -> dt.datetime:
    value = clock()
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise ContractError("clock 必须返回带时区的 datetime", kind="runtime")
    return value


def _time_text(value: dt.datetime) -> str:
    if value.tzinfo is None:
        raise ContractError("时间必须带时区", kind="runtime")
    return value.isoformat(timespec="seconds")


def _local_date(value: str) -> str:
    if not isinstance(value, str):
        raise ContractError("local_date 必须是 YYYY-MM-DD")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError("local_date 必须是有效 YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ContractError("local_date 必须是 YYYY-MM-DD")
    return value


def _sha(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ContractError(f"{name} 必须是 SHA-256", kind="evidence")
    return value


def _ref(value: ObjectRef | Mapping[str, Any] | None, name: str) -> ObjectRef | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, ObjectRef) else ObjectRef.from_dict(value)
    except ContractError as exc:
        raise ContractError(f"{name} 无效", kind="evidence") from exc


def _ref_dict(value: ObjectRef | Mapping[str, Any] | None) -> dict[str, Any] | None:
    ref = _ref(value, "object ref")
    return None if ref is None else ref.to_dict()


def _status_ref(value: Any, name: str, *, kind: str | None = None) -> dict[str, Any] | None:
    if value is None:
        return None
    ref = _ref(value, name)
    assert ref is not None
    if kind is not None and ref.kind != kind:
        raise ContractError(f"{name}.kind 无效", kind="evidence")
    return ref.to_dict()


def _safe_error_kind(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 80:
        return "runtime"
    if any(
        not (character.islower() or character.isdigit() or character == "_")
        for character in value
    ):
        return "runtime"
    return value


def _agent_result(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    from cognitive_agent_adapter_v1 import validate_agent_result_ref

    return validate_agent_result_ref(value)


def _landscape_ref(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"snapshot_id", "snapshot_sha256"}
    ):
        raise ContractError("landscape_ref 字段无效", kind="evidence")
    identifier = value["snapshot_id"]
    if (
        not isinstance(identifier, str)
        or not identifier.startswith("lnd_")
        or len(identifier) != 28
        or any(character not in "0123456789abcdef" for character in identifier[4:])
    ):
        raise ContractError("landscape_ref.snapshot_id 无效", kind="evidence")
    return {
        "snapshot_id": identifier,
        "snapshot_sha256": _sha(value["snapshot_sha256"], "snapshot_sha256"),
    }


@dataclass(frozen=True)
class CognitiveDayResult:
    """Bounded public result; no raw record or model text is exposed."""

    status: str
    local_date: str
    trigger: str
    stage: str
    pipeline_status: str | None
    bundle_ref: Mapping[str, Any] | None
    record_count: int
    receipt_count: int
    review_status: str
    review_sha256: str | None
    long_term_required: bool
    long_term_status: str
    material_sha256: str | None
    projection_status: str
    warnings: tuple[str, ...]
    agent_result_ref: Mapping[str, Any] | None
    landscape_ref: Mapping[str, str] | None
    home_projection_sha256: str | None
    error_kind: str | None
    cached: bool = False

    @classmethod
    def from_status(
        cls, value: Mapping[str, Any], *, cached: bool = False
    ) -> "CognitiveDayResult":
        status = _validate_status(value)
        return cls(
            status=status["status"],
            local_date=status["local_date"],
            trigger=status["trigger"],
            stage=status["stage"],
            pipeline_status=status["pipeline_status"],
            bundle_ref=status["bundle_ref"],
            record_count=status["record_count"],
            receipt_count=status["receipt_count"],
            review_status=status["review_status"],
            review_sha256=status["review_sha256"],
            long_term_required=status["long_term_required"],
            long_term_status=status["long_term_status"],
            material_sha256=status["material_sha256"],
            projection_status=status["projection_status"],
            warnings=tuple(status["warnings"]),
            agent_result_ref=status["agent_result_ref"],
            landscape_ref=status["landscape_ref"],
            home_projection_sha256=status["home_projection_sha256"],
            error_kind=status["error_kind"],
            cached=cached,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "local_date": self.local_date,
            "trigger": self.trigger,
            "stage": self.stage,
            "pipeline_status": self.pipeline_status,
            "bundle_ref": None if self.bundle_ref is None else dict(self.bundle_ref),
            "record_count": self.record_count,
            "receipt_count": self.receipt_count,
            "review_status": self.review_status,
            "review_sha256": self.review_sha256,
            "long_term_required": self.long_term_required,
            "long_term_status": self.long_term_status,
            "material_sha256": self.material_sha256,
            "projection_status": self.projection_status,
            "warnings": list(self.warnings),
            "agent_result_ref": (
                None if self.agent_result_ref is None else dict(self.agent_result_ref)
            ),
            "landscape_ref": (
                None if self.landscape_ref is None else dict(self.landscape_ref)
            ),
            "home_projection_sha256": self.home_projection_sha256,
            "error_kind": self.error_kind,
            "cached": self.cached,
        }


def _validate_status(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError("日任务状态必须是 JSON object", kind="evidence")
    item = dict(value)
    actual = frozenset(item)
    if actual != STATUS_FIELDS:
        raise ContractError(
            f"日任务状态字段无效；缺失={sorted(STATUS_FIELDS - actual)}；"
            f"未知={sorted(actual - STATUS_FIELDS)}",
            kind="evidence",
        )
    if (
        item["schema_version"] != "1.0"
        or item["kind"] != "memento_cognitive_day_orchestrator_status"
        or item["orchestrator_version"] != ORCHESTRATOR_VERSION
    ):
        raise ContractError("日任务状态合同版本无效", kind="evidence")
    _local_date(item["local_date"])
    if item["trigger"] not in TRIGGERS:
        raise ContractError("日任务 trigger 无效", kind="evidence")
    if item["status"] not in RUN_STATUSES or item["stage"] not in STAGES:
        raise ContractError("日任务状态/阶段无效", kind="evidence")
    for name in ("started_at", "updated_at"):
        try:
            parsed = dt.datetime.fromisoformat(str(item[name]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractError(f"{name} 无效", kind="evidence") from exc
        if parsed.tzinfo is None:
            raise ContractError(f"{name} 必须带时区", kind="evidence")
    if item["completed_at"] is not None:
        try:
            completed = dt.datetime.fromisoformat(
                str(item["completed_at"]).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ContractError("completed_at 无效", kind="evidence") from exc
        if completed.tzinfo is None:
            raise ContractError("completed_at 必须带时区", kind="evidence")
    if type(item["attempt"]) is not int or item["attempt"] < 0:
        raise ContractError("attempt 无效", kind="evidence")
    if item["pipeline_status"] is not None and not isinstance(
        item["pipeline_status"], str
    ):
        raise ContractError("pipeline_status 无效", kind="evidence")
    for name in ("pipeline_profile_sha256", "result_profile_sha256"):
        if item[name] is not None:
            item[name] = _sha(item[name], name)
    item["bundle_ref"] = _status_ref(item["bundle_ref"], "bundle_ref", kind="daily_bundle")
    for name in ("record_count", "receipt_count"):
        if type(item[name]) is not int or item[name] < 0:
            raise ContractError(f"{name} 无效", kind="evidence")
    for name in ("review_status", "long_term_status", "projection_status"):
        if item[name] not in SUBTASK_STATUSES:
            raise ContractError(f"{name} 无效", kind="evidence")
    if item["review_sha256"] is not None:
        item["review_sha256"] = _sha(item["review_sha256"], "review_sha256")
    if type(item["long_term_required"]) is not bool:
        raise ContractError("long_term_required 必须是 boolean", kind="evidence")
    if item["material_sha256"] is not None:
        item["material_sha256"] = _sha(item["material_sha256"], "material_sha256")
    item["agent_result_ref"] = _agent_result(item["agent_result_ref"])
    item["landscape_ref"] = _landscape_ref(item["landscape_ref"])
    if item["home_projection_sha256"] is not None:
        item["home_projection_sha256"] = _sha(
            item["home_projection_sha256"], "home_projection_sha256"
        )
    if (
        not isinstance(item["warnings"], list)
        or item["warnings"] != sorted(set(item["warnings"]))
        or any(row not in WARNINGS for row in item["warnings"])
    ):
        raise ContractError("warnings 无效", kind="evidence")
    item["error_kind"] = _safe_error_kind(item["error_kind"])
    if item["status"] == "running" and item["completed_at"] is not None:
        raise ContractError("running 状态不得已完成", kind="evidence")
    if item["status"] != "running" and item["status"] != "not_started":
        if item["stage"] == "finished" and item["completed_at"] is None:
            raise ContractError("终态必须有 completed_at", kind="evidence")
    return item


_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}


def _process_lock(path: Path) -> threading.RLock:
    key = str(path)
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


class _DayLock:
    def __init__(self, owner: "CognitiveDayOrchestrator", local_date: str) -> None:
        self.owner = owner
        self.local_date = local_date
        self.path = owner.locks_dir / f"day-{local_date}.lock"
        self.thread_lock = _process_lock(self.path)
        self.descriptor: int | None = None

    def __enter__(self) -> "_DayLock":
        self.thread_lock.acquire()
        try:
            self.owner._ensure_layout()
            flags = (
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(self.path, flags, 0o600)
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.getuid()
                or details.st_nlink != 1
                or stat.S_IMODE(details.st_mode) & 0o077
            ):
                os.close(descriptor)
                raise ContractError("日任务锁不安全", kind="evidence")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self.descriptor = descriptor
            return self
        except BaseException:
            self.thread_lock.release()
            raise

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.descriptor is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None
        self.thread_lock.release()


class CognitiveDayOrchestrator:
    """Run one bounded, idempotent Cognitive Secretary day workflow."""

    def __init__(
        self,
        vault: Path,
        provider: Any | None = None,
        *,
        state_root: Path | None = None,
        pipeline: CognitivePipeline | Any | None = None,
        renderer: DailyReviewRenderer | None = None,
        long_term_adapter: LongTermAdapter | None = None,
        projector: ProjectionPublisher | None = None,
        bundle_store: CognitiveBundleStore | Any | None = None,
        profile_loader: Callable[[Path], Mapping[str, Any]] = build_agent_profile,
        agent_runner: Callable[[Path, str], Any] | None = None,
        schedule_loader: Callable[[str, dt.datetime], Mapping[str, Any]] | None = None,
        clock: Callable[[], dt.datetime] = lambda: dt.datetime.now().astimezone(),
        fault_hook: Callable[[str], None] | None = None,
    ) -> None:
        try:
            resolved = Path(vault).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ContractError("Vault 不存在", kind="not_found") from exc
        if not resolved.is_dir():
            raise ContractError("Vault 必须是目录", kind="not_found")
        root = state_root or (
            resolved / ".context-agent" / "cognitive-secretary-v1"
        )
        if not root.is_absolute():
            root = resolved / root
        candidate = root.parent.resolve() / root.name
        try:
            candidate.relative_to(resolved)
        except ValueError as exc:
            raise ContractError("state_root 必须位于 Vault 内", kind="evidence") from exc

        self.vault = resolved
        self.root = candidate
        self.status_dir = self.root / "day-orchestrator" / "status"
        self.locks_dir = self.root / "locks"
        self.clock = clock
        self.fault_hook = fault_hook
        self.profile_loader = profile_loader
        self.schedule_loader = schedule_loader

        if pipeline is None:
            if provider is None:
                raise ContractError("pipeline 与 provider 不能同时缺失", kind="runtime")
            pipeline = CognitivePipeline(
                resolved,
                provider,
                state_root=candidate,
                clock=clock,
            )
        self.pipeline = pipeline
        self.bundle_store = bundle_store or getattr(pipeline, "bundles", None)
        if self.bundle_store is None:
            raise ContractError("缺少正式 daily bundle reader/writer", kind="runtime")
        profile_reader_setter = getattr(
            self.bundle_store,
            "set_profile_sha256_reader",
            None,
        )
        if callable(profile_reader_setter):
            profile_reader_setter(lambda: self._profile()["profile_sha256"])
        elif isinstance(pipeline, CognitivePipeline):
            raise ContractError(
                "正式 daily bundle store 缺少 profile CAS reader",
                kind="runtime",
            )

        if renderer is None:
            try:
                from cognitive_daily_review_v1 import CognitiveDailyReviewRenderer
            except ImportError as exc:
                raise ContractError("Daily Review renderer 未安装", kind="runtime") from exc
            # The renderer owns private staging and journal directories whose
            # permissions differ from the Bundle Store's public staging tree.
            # Keep both components under the same Cognitive root, but never
            # point them at the same directory.
            renderer = CognitiveDailyReviewRenderer(
                resolved,
                state_root=candidate / "daily-review-projection",
            )
        self.renderer = renderer

        self.long_term_adapter = long_term_adapter or CognitiveAgentAdapter(
            resolved,
            bundle_store=self.bundle_store,
            action_store=self.pipeline.actions,
            state_root=candidate,
            agent_runner=agent_runner,
            profile_loader=profile_loader,
            clock=clock,
        )
        self.projector = projector or CognitiveProjectionPublisher(
            resolved,
            record_store=self.pipeline.records,
            action_store=self.pipeline.actions,
            bundle_store=self.bundle_store,
            state_root=candidate,
            profile_loader=profile_loader,
        )

    # --------------------------------------------------------------
    # Private, bounded audit persistence

    def _secure_directory(self, path: Path) -> None:
        try:
            path.relative_to(self.vault)
        except ValueError as exc:
            raise ContractError("日任务目录越过 Vault 边界", kind="evidence") from exc
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ContractError("日任务目录不安全", kind="evidence")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        details = path.lstat()
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) & 0o077
        ):
            raise ContractError("日任务目录必须仅当前用户可读", kind="evidence")

    def _ensure_layout(self) -> None:
        for path in (
            self.root.parent,
            self.root,
            self.status_dir.parent,
            self.status_dir,
            self.locks_dir,
        ):
            self._secure_directory(path)

    def _status_path(self, local_date: str) -> Path:
        return self.status_dir / f"{_local_date(local_date)}.json"

    @staticmethod
    def _safe_read(path: Path) -> dict[str, Any]:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError as exc:
            raise ContractError("日任务状态不存在", kind="not_found") from exc
        except OSError as exc:
            kind = "evidence" if exc.errno in {errno.ELOOP, errno.EISDIR} else "runtime"
            raise ContractError("日任务状态无法安全读取", kind=kind) from exc
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) & 0o077
                or before.st_size > MAX_STATUS_BYTES
            ):
                raise ContractError("日任务状态文件不安全", kind="evidence")
            payload = bytearray()
            while True:
                chunk = os.read(descriptor, min(65536, MAX_STATUS_BYTES + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > MAX_STATUS_BYTES:
                    raise ContractError("日任务状态文件过大", kind="evidence")
            after = os.fstat(descriptor)
            stable = (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            if any(getattr(before, key) != getattr(after, key) for key in stable):
                raise ContractError("日任务状态读取期间变化", kind="stale")
        finally:
            os.close(descriptor)
        try:
            raw = json.loads(bytes(payload).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("日任务状态 JSON 损坏", kind="evidence") from exc
        return _validate_status(raw)

    def _write_status(self, status: Mapping[str, Any]) -> dict[str, Any]:
        item = _validate_status(status)
        self._ensure_layout()
        path = self._status_path(item["local_date"])
        if path.is_symlink():
            raise ContractError("拒绝覆盖符号链接状态", kind="evidence")
        if path.exists():
            details = path.lstat()
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.getuid()
                or details.st_nlink != 1
                or stat.S_IMODE(details.st_mode) & 0o077
            ):
                raise ContractError("拒绝覆盖不安全状态", kind="evidence")
        payload = (canonical_json(item) + "\n").encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory = os.open(
                path.parent,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                with contextlib.suppress(OSError):
                    os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
        return item

    def _load_status(self, local_date: str) -> dict[str, Any] | None:
        path = self._status_path(local_date)
        if not path.exists() and not path.is_symlink():
            return None
        return self._safe_read(path)

    def status(self, local_date: str) -> CognitiveDayResult:
        """Return the latest finite audit snapshot without touching a Provider."""

        date = _local_date(local_date)
        current = self._load_status(date)
        if current is None:
            now = _clock_now(self.clock)
            current = self._blank_status(date, "manual", now, attempt=0)
        return CognitiveDayResult.from_status(current, cached=True)

    def _blank_status(
        self,
        local_date: str,
        trigger: str,
        now: dt.datetime,
        *,
        attempt: int,
    ) -> dict[str, Any]:
        timestamp = _time_text(now)
        return _validate_status(
            {
                "schema_version": "1.0",
                "kind": "memento_cognitive_day_orchestrator_status",
                "orchestrator_version": ORCHESTRATOR_VERSION,
                "local_date": local_date,
                "trigger": trigger,
                "status": "not_started",
                "stage": "not_started",
                "started_at": timestamp,
                "updated_at": timestamp,
                "completed_at": None,
                "attempt": attempt,
                "pipeline_status": None,
                "pipeline_profile_sha256": None,
                "result_profile_sha256": None,
                "bundle_ref": None,
                "record_count": 0,
                "receipt_count": 0,
                "review_status": "not_started",
                "review_sha256": None,
                "long_term_required": False,
                "long_term_status": "not_started",
                "material_sha256": None,
                "agent_result_ref": None,
                "projection_status": "not_started",
                "landscape_ref": None,
                "home_projection_sha256": None,
                "warnings": [],
                "error_kind": None,
            }
        )

    def _checkpoint(self, state: Mapping[str, Any], **changes: Any) -> dict[str, Any]:
        now = _clock_now(self.clock)
        updated = {**dict(state), **changes, "updated_at": _time_text(now)}
        return self._write_status(updated)

    def _fault(self, stage: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(stage)

    # --------------------------------------------------------------
    # Verified public-store helpers

    def _profile(self) -> dict[str, Any]:
        value = dict(self.profile_loader(self.vault))
        _sha(value.get("profile_sha256"), "profile_sha256")
        if not isinstance(value.get("memories"), list):
            raise ContractError("Agent profile.memories 无效", kind="evidence")
        return value

    def _current_bundle(
        self, local_date: str
    ) -> tuple[ObjectRef | None, dict[str, Any] | None]:
        ref = self.bundle_store.load_day_bundle_ref(local_date)
        manifest = self.bundle_store.load_day_manifest(local_date)
        if (ref is None) != (manifest is None):
            raise ContractError("daily bundle ref/manifest 不一致", kind="evidence")
        if ref is not None:
            parsed = _ref(ref, "daily bundle ref")
            assert parsed is not None
            if parsed.kind != "daily_bundle":
                raise ContractError("daily bundle ref kind 无效", kind="evidence")
            if (
                not isinstance(manifest, Mapping)
                or persisted_sha256(manifest) != parsed.revision_sha256
            ):
                raise ContractError("daily bundle manifest hash 无效", kind="evidence")
            return parsed, dict(manifest)
        return None, None

    def _review_inputs(
        self, local_date: str, manifest: Mapping[str, Any]
    ) -> tuple[
        DailySummaryRevision,
        ObjectRef,
        tuple[SourceRecordRevision, ...],
        tuple[InterpretationReceiptRevision, ...],
    ]:
        loaded = self.bundle_store.load_daily_summary_head(local_date)
        if loaded is None:
            raise ContractError("daily bundle 缺少 Daily Summary", kind="evidence")
        summary, summary_ref = loaded
        if summary_ref.to_dict() != manifest.get("summary_ref"):
            raise ContractError("Daily Summary head 与 bundle 不一致", kind="stale")
        sources: list[SourceRecordRevision] = []
        for raw in manifest.get("source_refs", []):
            expected = _ref(raw, "bundle source ref")
            assert expected is not None
            value = SourceRecordRevision.from_dict(
                self.pipeline.records.load_head(expected.id)
            )
            actual = ObjectRef(
                "source_record", value.record_id, value.revision, value.sha256
            )
            if actual != expected:
                raise ContractError("bundle source ref 已变化", kind="stale")
            sources.append(value)
        receipts: list[InterpretationReceiptRevision] = []
        for raw in manifest.get("receipt_refs", []):
            expected = _ref(raw, "bundle receipt ref")
            assert expected is not None
            value = self.pipeline.actions.load_receipt_head(expected.id)
            actual = ObjectRef(
                "interpretation_receipt",
                value.receipt_id,
                value.revision,
                value.sha256,
            )
            if actual != expected:
                raise ContractError("bundle receipt ref 已变化", kind="stale")
            receipts.append(value)
        return summary, summary_ref, tuple(sources), tuple(receipts)

    def _bind_review(
        self,
        *,
        local_date: str,
        base_summary: DailySummaryRevision,
        base_summary_ref: ObjectRef,
        review: Any,
    ) -> tuple[ObjectRef, dict[str, Any], str, str]:
        """CAS-bind renderer output through the formal store API.

        A successfully written Markdown file is still only a projection.  The
        day is review-complete only after the store appends a DailySummary
        revision containing the returned hashes and advances the day bundle to
        that new summary ref.
        """

        result_base_ref = _ref(
            getattr(review, "base_summary_ref", None),
            "Daily Review base_summary_ref",
        )
        if result_base_ref != base_summary_ref:
            raise ContractError("Daily Review 未精确绑定输入 summary", kind="stale")
        binding_builder = getattr(review, "summary_binding", None)
        if not callable(binding_builder):
            raise ContractError("Daily Review 结果缺少 summary_binding", kind="evidence")
        binding = binding_builder()
        if not isinstance(binding, Mapping) or frozenset(binding) != frozenset(
            {"review_file", "review_sha256", "user_supplement_sha256"}
        ):
            raise ContractError("Daily Review summary_binding 字段无效", kind="evidence")
        review_file = binding["review_file"]
        if review_file != base_summary.review_file:
            raise ContractError("Daily Review 路径与 summary 不一致", kind="evidence")
        review_sha = _sha(binding["review_sha256"], "review_sha256")
        supplement_sha = binding["user_supplement_sha256"]
        if supplement_sha is not None:
            supplement_sha = _sha(
                supplement_sha, "user_supplement_sha256"
            )

        current_ref, current_manifest = self._current_bundle(local_date)
        if current_ref is None or current_manifest is None:
            raise ContractError("Daily Review 绑定时 bundle 不存在", kind="stale")
        if current_manifest.get("summary_ref") != base_summary_ref.to_dict():
            raise ContractError("Daily Review 绑定前 summary 已变化", kind="stale")

        renderer_status = str(getattr(review, "status", "updated"))
        normalized = {
            "created": "completed",
            "updated": "completed",
            "unchanged": "no_change",
            "recovered": "recovered",
            "completed": "completed",
            "no_change": "no_change",
        }.get(renderer_status, "completed")

        if base_summary.review_sha256 is not None:
            # A current Daily ``finish(no_change)`` may run after the Review
            # projection was deleted or corrupted.  The renderer is allowed
            # to restore the exact bytes already bound by the immutable
            # summary, but must never append another summary revision.  Check
            # both metadata and the live file before accepting that idempotent
            # recovery; a different hash remains a hard conflict in the
            # formal store.
            if (
                base_summary.review_sha256 == review_sha
                and base_summary.user_supplement_sha256 == supplement_sha
            ):
                self._assert_review_file_binding(review_file, review_sha)
                return current_ref, current_manifest, normalized, review_sha

        binder = getattr(self.bundle_store, "append_review_result", None)
        if not callable(binder):
            raise ContractError(
                "bundle store 缺少 append_review_result 公共 CAS API",
                kind="runtime",
            )
        committed = binder(
            expected_bundle_ref=current_ref,
            expected_summary_ref=base_summary_ref,
            review_file=review_file,
            review_sha256=review_sha,
            user_supplement_sha256=supplement_sha,
            now=_clock_now(self.clock),
        )
        committed_ref = _ref(
            getattr(committed, "bundle_ref", None), "Review bundle ref"
        )
        committed_summary_ref = _ref(
            getattr(committed, "summary_ref", None), "Review summary ref"
        )
        if committed_ref is None or committed_summary_ref is None:
            raise ContractError("Daily Review 绑定结果不完整", kind="evidence")

        actual_ref, actual_manifest = self._current_bundle(local_date)
        loaded = self.bundle_store.load_daily_summary_head(local_date)
        if (
            actual_ref != committed_ref
            or actual_manifest is None
            or loaded is None
        ):
            raise ContractError("Daily Review 绑定结果未发布", kind="stale")
        summary, summary_ref = loaded
        if (
            summary_ref != committed_summary_ref
            or actual_manifest.get("summary_ref") != summary_ref.to_dict()
            or summary.review_file != review_file
            or summary.review_sha256 != review_sha
            or summary.user_supplement_sha256 != supplement_sha
        ):
            raise ContractError("Daily Review 绑定后 summary 校验失败", kind="evidence")
        if summary_ref != base_summary_ref and (
            summary_ref.id != base_summary_ref.id
            or summary_ref.revision != base_summary_ref.revision + 1
            or summary.previous_revision_sha256
            != base_summary_ref.revision_sha256
        ):
            raise ContractError("Daily Review summary revision 链无效", kind="evidence")

        return actual_ref, actual_manifest, normalized, review_sha

    def _assert_review_file_binding(
        self,
        review_file: str,
        review_sha256: str,
    ) -> None:
        """Verify exact private Review bytes without following symlinks."""

        expected = _sha(review_sha256, "review_sha256")
        relative = Path(review_file)
        if relative.is_absolute() or ".." in relative.parts:
            raise ContractError("Daily Review 路径越过 Vault 边界", kind="evidence")
        current = self.vault
        for part in relative.parts[:-1]:
            current = current / part
            if current.is_symlink() or not current.is_dir():
                raise ContractError("Daily Review 父目录不安全", kind="evidence")
            details = current.lstat()
            if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
                raise ContractError("Daily Review 父目录归属无效", kind="evidence")

        path = self.vault / relative
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError as exc:
            raise ContractError("Daily Review 文件不存在", kind="not_found") from exc
        except OSError as exc:
            kind = "evidence" if exc.errno in {errno.ELOOP, errno.EISDIR} else "runtime"
            raise ContractError("Daily Review 无法安全读取", kind=kind) from exc
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) & 0o077
            ):
                raise ContractError(
                    "Daily Review 必须是当前用户的私有单链接文件",
                    kind="evidence",
                )
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(descriptor)
            stable = (
                "st_dev",
                "st_ino",
                "st_uid",
                "st_mode",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            if any(getattr(before, key) != getattr(after, key) for key in stable):
                raise ContractError("Daily Review 读取期间发生变化", kind="stale")
            if digest.hexdigest() != expected:
                raise ContractError("Daily Review 字节与已绑定 hash 不一致", kind="evidence")
        finally:
            os.close(descriptor)

    def _active_formal_refs(self) -> tuple[tuple[ObjectRef, ...], tuple[ObjectRef, ...]]:
        memories = tuple(
            sorted(
                (
                    ObjectRef(
                        "reusable_memory", row.memory_id, row.revision, row.sha256
                    )
                    for row in self.bundle_store.list_active_memories()
                ),
                key=lambda row: (row.id, row.revision, row.revision_sha256),
            )
        )
        relations = tuple(
            sorted(
                (
                    ObjectRef("relation", row.relation_id, row.revision, row.sha256)
                    for row in self.bundle_store.list_active_relations()
                ),
                key=lambda row: (row.id, row.revision, row.revision_sha256),
            )
        )
        return memories, relations

    def _append_agent_result(
        self,
        *,
        local_date: str,
        result_ref: Mapping[str, Any],
        warning: str | None,
    ) -> ObjectRef:
        current_ref, manifest = self._current_bundle(local_date)
        if current_ref is None or manifest is None:
            raise ContractError("追加 Agent 结果时 daily bundle 不存在", kind="stale")
        if manifest.get("long_term_result_ref") is not None:
            if _agent_result(manifest["long_term_result_ref"]) != _agent_result(result_ref):
                raise ContractError("daily bundle 已绑定其他 Agent 结果", kind="conflict")
            return current_ref
        loaded = self.bundle_store.load_daily_summary_head(local_date)
        if loaded is None:
            raise ContractError("daily summary 不存在", kind="evidence")
        summary, summary_ref = loaded
        if summary_ref.to_dict() != manifest["summary_ref"]:
            raise ContractError("daily summary 已变化", kind="stale")
        warnings = sorted(
            set(manifest.get("warnings", []))
            | ({warning} if warning in WARNINGS else set())
        )
        committed = self.bundle_store.commit_day_bundle(
            request_id=manifest["request_id"],
            run_id=manifest["run_id"],
            input_hashes=manifest["input_hashes"],
            source_refs=manifest["source_refs"],
            receipt_refs=manifest["receipt_refs"],
            summary=summary,
            memories=(),
            relations=(),
            candidate_materializations=(),
            long_term_result_ref=result_ref,
            warnings=warnings,
            expected_bundle_ref=current_ref,
            operation="append_long_term_result",
            now=_clock_now(self.clock),
        )
        return committed.bundle_ref

    def _schedule(
        self,
        local_date: str,
        now: dt.datetime,
        supplied: Mapping[str, Any] | None,
        last_status: str,
    ) -> dict[str, Any]:
        if supplied is not None:
            value = dict(supplied)
        elif self.schedule_loader is not None:
            value = dict(self.schedule_loader(local_date, now))
        else:
            next_date = dt.date.fromisoformat(local_date) + dt.timedelta(days=1)
            next_due = dt.datetime.combine(
                next_date, dt.time(hour=21), tzinfo=now.tzinfo
            )
            value = {
                "enabled": False,
                "hour": 21,
                "minute": 0,
                "next_due_at": _time_text(next_due),
                "last_run_status": "not_started",
            }
        if not isinstance(value, dict):
            raise ContractError("schedule 必须是 object")
        projected = {
            **value,
            "last_run_status": (
                last_status
                if last_status
                in {
                    "committed",
                    "committed_with_warnings",
                    "no_change",
                    "no_candidate",
                    "no_records",
                    "no_receipts",
                    "stale",
                    "error",
                    "budget_exhausted",
                }
                else "no_change"
            ),
        }
        return projected

    @staticmethod
    def _pipeline_bundle_ref(result: DayPipelineResult | Any) -> ObjectRef | None:
        commit = getattr(result, "commit_result", None)
        if commit is None:
            return None
        return _ref(getattr(commit, "bundle_ref", None), "pipeline bundle ref")

    @staticmethod
    def _record_runtime_statuses(
        result: DayPipelineResult | Any,
    ) -> dict[str, dict[str, str | None]]:
        """Project current per-record outcomes that have no receipt."""

        record_ids = set(getattr(result, "record_ids", ()))
        statuses: dict[str, dict[str, str | None]] = {}
        for raw in getattr(result, "interpretation_results", ()):
            if not isinstance(raw, Mapping):
                continue
            request = raw.get("request")
            record_ref = (
                request.get("record_ref") if isinstance(request, Mapping) else None
            )
            record_id = (
                record_ref.get("id") if isinstance(record_ref, Mapping) else None
            )
            if not isinstance(record_id, str) or record_id not in record_ids:
                continue
            status = raw.get("status")
            if raw.get("receipt") is not None or status == "completed":
                # A bounded retry may follow a cached failure in the same day
                # run.  Its later receipt is authoritative for Home.
                statuses.pop(record_id, None)
                continue
            if status == "no_candidate":
                statuses[record_id] = {
                    "status": "no_candidate",
                    "error_kind": None,
                }
                continue
            if status == "running":
                statuses[record_id] = {
                    "status": "processing",
                    "error_kind": None,
                }
                continue
            if status not in {"error", "stale", "budget_exhausted"}:
                continue
            run = raw.get("run")
            error_kind = run.get("error_kind") if isinstance(run, Mapping) else None
            if status == "stale" or error_kind == "stale":
                public_error = "stale"
            elif status == "budget_exhausted":
                public_error = "budget_exhausted"
            elif error_kind == "unknown_attempt":
                public_error = "unknown_attempt"
            elif error_kind in {"provider_error", "provider_lock"}:
                public_error = "provider_error"
            elif error_kind in {"schema", "invalid_response", "usage_missing"}:
                public_error = "invalid_response"
            else:
                public_error = "runtime"
            statuses[record_id] = {
                "status": "failed",
                "error_kind": public_error,
            }
        return statuses

    @staticmethod
    def _projection_audit(publication: Any) -> tuple[dict[str, str], str]:
        landscape = getattr(publication, "landscape", None)
        home = getattr(publication, "home", None)
        identifier = getattr(landscape, "snapshot_id", None)
        landscape_sha = getattr(landscape, "sha256", None)
        home_sha = getattr(home, "sha256", None)
        return (
            _landscape_ref(
                {"snapshot_id": identifier, "snapshot_sha256": landscape_sha}
            ),
            _sha(home_sha, "home_projection_sha256"),
        )  # type: ignore[return-value]

    # --------------------------------------------------------------
    # Public workflow

    def run_day(
        self,
        local_date: str,
        *,
        trigger: str = "manual",
        schedule: Mapping[str, Any] | None = None,
        pipeline_options: Mapping[str, Any] | None = None,
    ) -> CognitiveDayResult:
        date = _local_date(local_date)
        if trigger not in TRIGGERS:
            raise ContractError("daily trigger 无效")
        options = dict(pipeline_options or {})
        forbidden = {"local_date", "trigger", "replay_profile_sha256"} & set(options)
        if forbidden:
            raise ContractError("pipeline_options 不得覆盖 local_date/trigger")

        with _DayLock(self, date):
            now = _clock_now(self.clock)
            previous = self._load_status(date)
            attempt = 1 if previous is None else previous["attempt"] + 1
            state = self._blank_status(date, trigger, now, attempt=attempt)
            state = self._checkpoint(
                state,
                status="running",
                stage="running_pipeline",
            )
            try:
                profile_before = self._profile()
                replay_profile_sha = None
                if (
                    previous is not None
                    and previous["pipeline_profile_sha256"] is not None
                    and previous["result_profile_sha256"]
                    == profile_before["profile_sha256"]
                ):
                    replay_profile_sha = previous["pipeline_profile_sha256"]
                pipeline_result = self.pipeline.run_day(
                    date,
                    trigger=trigger,
                    profile_sha256=profile_before["profile_sha256"],
                    replay_profile_sha256=replay_profile_sha,
                    **options,
                )
            except Exception as exc:
                state = self._checkpoint(
                    state,
                    status="error",
                    stage="finished",
                    completed_at=_time_text(_clock_now(self.clock)),
                    # The public day status intentionally exposes the failed
                    # stage, not an internal storage/contract subtype.
                    error_kind="pipeline_failed",
                )
                return CognitiveDayResult.from_status(state)

            pipeline_status = str(getattr(pipeline_result, "status", "error"))
            record_ids = tuple(getattr(pipeline_result, "record_ids", ()))
            receipt_refs = tuple(getattr(pipeline_result, "receipt_refs", ()))
            record_runtime_statuses = self._record_runtime_statuses(pipeline_result)
            current_daily_terminal = bool(
                pipeline_status == "no_change"
                and getattr(pipeline_result, "commit_result", None) is None
                and getattr(pipeline_result, "daily_result", None) is not None
            )
            record_terminal_no_change = bool(
                pipeline_status == "no_change"
                and getattr(pipeline_result, "commit_result", None) is None
                and getattr(pipeline_result, "daily_result", None) is None
            )
            pipeline_ref = self._pipeline_bundle_ref(pipeline_result)
            current_ref, current_manifest = self._current_bundle(date)
            projection_only = pipeline_status in {
                "no_candidate",
                "no_records",
                "no_receipts",
            } or record_terminal_no_change or (
                current_daily_terminal and current_ref is None
            )
            if pipeline_ref is not None and current_ref != pipeline_ref:
                state = self._checkpoint(
                    state,
                    status="error",
                    stage="finished",
                    completed_at=_time_text(_clock_now(self.clock)),
                    pipeline_status=pipeline_status,
                    error_kind="bundle_binding",
                )
                return CognitiveDayResult.from_status(state)

            same_bundle = (
                previous is not None
                and previous["bundle_ref"]
                == (None if current_ref is None else current_ref.to_dict())
            )
            if same_bundle:
                for name in (
                    "review_status",
                    "review_sha256",
                    "long_term_required",
                    "long_term_status",
                    "material_sha256",
                    "agent_result_ref",
                    "projection_status",
                    "landscape_ref",
                    "home_projection_sha256",
                    "pipeline_profile_sha256",
                    "result_profile_sha256",
                ):
                    state[name] = previous[name]
            material_brief = getattr(pipeline_result, "material_brief", None)
            current_required = bool(
                not projection_only
                and getattr(material_brief, "requires_long_term_review", False)
            )
            pending_previous_gate = bool(
                not projection_only
                and same_bundle
                and previous is not None
                and previous["long_term_required"]
                and previous["long_term_status"]
                not in {"completed", "already_linked", "recovered"}
            )
            long_term_required = current_required or pending_previous_gate
            current_material_sha = getattr(material_brief, "material_sha256", None)
            if current_material_sha is not None:
                current_material_sha = _sha(
                    current_material_sha, "material_sha256"
                )
            material_sha = (
                previous["material_sha256"]
                if pending_previous_gate and previous is not None
                else current_material_sha
            )
            state = self._checkpoint(
                state,
                status="running",
                stage="pipeline_completed",
                pipeline_status=pipeline_status,
                pipeline_profile_sha256=profile_before["profile_sha256"],
                bundle_ref=None if current_ref is None else current_ref.to_dict(),
                record_count=len(record_ids),
                receipt_count=len(receipt_refs),
                long_term_required=long_term_required,
                material_sha256=material_sha,
            )
            self._fault("after_pipeline_checkpoint")

            if (
                pipeline_status == "no_change"
                and not projection_only
                and not current_daily_terminal
                and same_bundle
                and previous is not None
                and previous["status"] in {"committed", "no_change", "no_records", "no_receipts"}
                and not previous["warnings"]
                and previous["projection_status"] == "completed"
            ):
                state = self._checkpoint(
                    state,
                    trigger=trigger,
                    status="no_change",
                    stage="finished",
                    completed_at=_time_text(_clock_now(self.clock)),
                    warnings=[],
                    error_kind=None,
                )
                return CognitiveDayResult.from_status(state, cached=True)

            warnings: set[str] = set(previous["warnings"] if same_bundle and previous else ())

            if projection_only:
                state = self._checkpoint(
                    state,
                    review_status="skipped",
                    review_sha256=None,
                    long_term_required=False,
                    long_term_status="skipped",
                    material_sha256=None,
                    agent_result_ref=None,
                )
            elif current_daily_terminal and current_manifest is not None:
                # A current Daily finish may be the recovery attempt after a
                # previously bound Review file was removed or corrupted.
                # Re-run the deterministic renderer even when the prior
                # checkpoint said completed; long-term state remains intact.
                state = self._checkpoint(
                    state,
                    review_status="not_started",
                    review_sha256=None,
                )

            # Render only a committed public summary.  The renderer's file is
            # not considered complete until a public store CAS binds its hash
            # into a new DailySummary revision and advances the day bundle.
            if projection_only:
                pass
            elif current_manifest is None:
                state = self._checkpoint(state, review_status="skipped")
            elif state["review_status"] not in {"completed", "no_change", "recovered"}:
                state = self._checkpoint(state, stage="rendering_review")
                try:
                    summary, summary_ref, sources, receipts = self._review_inputs(
                        date, current_manifest
                    )
                    review = self.renderer.render(
                        summary=summary,
                        summary_ref=summary_ref,
                        sources=sources,
                        receipts=receipts,
                    )
                    self._fault("after_review_file")
                    (
                        current_ref,
                        current_manifest,
                        normalized_review_status,
                        review_sha,
                    ) = self._bind_review(
                        local_date=date,
                        base_summary=summary,
                        base_summary_ref=summary_ref,
                        review=review,
                    )
                    state = self._checkpoint(
                        state,
                        bundle_ref=current_ref.to_dict(),
                        review_status=normalized_review_status,
                        review_sha256=review_sha,
                    )
                    warnings.discard("review_failed")
                except Exception:
                    warnings.add("review_failed")
                    state = self._checkpoint(state, review_status="failed")
                self._fault("after_review")

            # Agent V1 receives only its existing request identity.  The
            # material brief/profile/watermark remain local audit gates.
            current_ref, current_manifest = self._current_bundle(date)
            requires_long_term = bool(
                current_manifest is not None
                and current_manifest.get("long_term_result_ref") is None
                and state["long_term_required"]
            )
            if projection_only:
                pass
            elif current_manifest is None:
                state = self._checkpoint(state, long_term_status="skipped")
            elif current_manifest.get("long_term_result_ref") is not None:
                linked = _agent_result(current_manifest["long_term_result_ref"])
                state = self._checkpoint(
                    state,
                    long_term_status="already_linked",
                    agent_result_ref=linked,
                    material_sha256=(
                        state["material_sha256"]
                        or getattr(material_brief, "material_sha256", None)
                    ),
                )
            elif not requires_long_term:
                state = self._checkpoint(
                    state,
                    long_term_status="no_material",
                    material_sha256=getattr(material_brief, "material_sha256", None),
                )
                warnings.discard("long_term_failed")
            elif state["long_term_status"] not in {
                "completed",
                "already_linked",
                "recovered",
            }:
                state = self._checkpoint(state, stage="judging_long_term")
                try:
                    assert current_ref is not None
                    memories, relations = self._active_formal_refs()
                    _, action_watermark = self.pipeline.actions.action_watermark()
                    adapter_result = self.long_term_adapter.process(
                        bundle_ref=current_ref,
                        manifest=current_manifest,
                        reusable_memory_heads=memories,
                        relation_heads=relations,
                        profile_sha256=profile_before["profile_sha256"],
                        user_action_watermark_sha256=_sha(
                            action_watermark, "user_action_watermark_sha256"
                        ),
                        trigger=trigger,
                    )
                    adapter_status = str(getattr(adapter_result, "status", "completed"))
                    result_ref = _agent_result(
                        getattr(adapter_result, "agent_result_ref", None)
                    )
                    warning = getattr(adapter_result, "warning", None)
                    material_sha = getattr(adapter_result, "material_sha256", None)
                    if material_sha is not None:
                        material_sha = _sha(material_sha, "material_sha256")
                    if result_ref is not None:
                        appended_ref = self._append_agent_result(
                            local_date=date,
                            result_ref=result_ref,
                            warning=warning,
                        )
                        current_ref, current_manifest = self._current_bundle(date)
                        if current_ref != appended_ref or current_manifest is None:
                            raise ContractError(
                                "Agent result bundle 追加后未发布",
                                kind="stale",
                            )
                    normalized = (
                        adapter_status
                        if adapter_status
                        in {
                            "completed",
                            "no_material",
                            "already_linked",
                            "recovered",
                        }
                        else "failed"
                    )
                    state = self._checkpoint(
                        state,
                        bundle_ref=current_ref.to_dict(),
                        long_term_status=normalized,
                        material_sha256=material_sha,
                        agent_result_ref=result_ref,
                    )
                    if warning == "long_term_failed" or normalized == "failed":
                        warnings.add("long_term_failed")
                    else:
                        warnings.discard("long_term_failed")
                except Exception:
                    warnings.add("long_term_failed")
                    state = self._checkpoint(state, long_term_status="failed")
                self._fault("after_long_term")

            # Projection is deterministic and model-free.  A failed
            # publication leaves the previous published snapshot untouched.
            if (
                previous is not None
                and state["projection_status"] == "completed"
                and (
                    projection_only
                    or current_daily_terminal
                    or state["bundle_ref"] != previous["bundle_ref"]
                    or sorted(warnings) != previous["warnings"]
                )
            ):
                state = self._checkpoint(
                    state,
                    projection_status="not_started",
                    landscape_ref=None,
                    home_projection_sha256=None,
                )
            if state["projection_status"] != "completed":
                state = self._checkpoint(state, stage="projecting")
                try:
                    profile_after = self._profile()
                    state = self._checkpoint(
                        state,
                        result_profile_sha256=profile_after["profile_sha256"],
                    )
                    passthrough_statuses = {
                        "no_candidate",
                        "no_records",
                        "no_receipts",
                        "stale",
                        "error",
                        "budget_exhausted",
                    }
                    if projection_only or pipeline_status in passthrough_statuses:
                        predicted_status = pipeline_status
                    elif warnings:
                        predicted_status = "committed_with_warnings"
                    elif pipeline_status in {"committed", "no_change"}:
                        predicted_status = pipeline_status
                    else:
                        predicted_status = "no_change"
                    schedule_value = self._schedule(
                        date,
                        _clock_now(self.clock),
                        schedule,
                        predicted_status,
                    )
                    nonce = sha256_bytes(
                        canonical_json(
                            {
                                "orchestrator": ORCHESTRATOR_VERSION,
                                "local_date": date,
                                "attempt": attempt,
                                "bundle_ref": state["bundle_ref"],
                                "profile_sha256": profile_after["profile_sha256"],
                            }
                        ).encode("utf-8")
                    )
                    publication = self.projector.publish(
                        local_date=date,
                        schedule=schedule_value,
                        warnings=sorted(warnings),
                        record_runtime_statuses=record_runtime_statuses,
                        now=_clock_now(self.clock),
                        profile=profile_after,
                        publication_nonce=nonce,
                    )
                    landscape, home_sha = self._projection_audit(publication)
                    state = self._checkpoint(
                        state,
                        projection_status="completed",
                        landscape_ref=landscape,
                        home_projection_sha256=home_sha,
                    )
                    warnings.discard("landscape_failed")
                except Exception:
                    warnings.add("landscape_failed")
                    state = self._checkpoint(state, projection_status="failed")
                self._fault("after_projection")

            if pipeline_status in {
                "no_candidate",
                "no_records",
                "no_receipts",
                "no_change",
                "stale",
                "error",
                "budget_exhausted",
            }:
                final_status = pipeline_status
            else:
                final_status = "committed"
            if (
                warnings
                and current_manifest is not None
                and final_status in {"committed", "no_change"}
                and not projection_only
            ):
                final_status = "committed_with_warnings"
            state = self._checkpoint(
                state,
                status=final_status,
                stage="finished",
                completed_at=_time_text(_clock_now(self.clock)),
                warnings=sorted(warnings),
                error_kind=(
                    pipeline_status
                    if pipeline_status in {"stale", "error", "budget_exhausted"}
                    else None
                ),
            )
            return CognitiveDayResult.from_status(state)


def inspect_cognitive_day_status(
    vault: Path,
    local_date: str,
    *,
    state_root: Path | None = None,
) -> dict[str, Any] | None:
    """Read one validated day checkpoint without constructing an orchestrator."""

    date = _local_date(local_date)
    try:
        resolved = vault.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ContractError("Vault 目录不存在", kind="not_found") from exc
    details = resolved.lstat()
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
        raise ContractError("Vault 必须是当前用户目录", kind="evidence")
    root = state_root or (
        resolved / ".context-agent" / "cognitive-secretary-v1"
    )
    if not root.is_absolute():
        root = resolved / root
    candidate = root.parent.resolve() / root.name
    try:
        candidate.relative_to(resolved)
    except ValueError as exc:
        raise ContractError("state_root 必须位于 Vault 内", kind="evidence") from exc
    status_dir = candidate / "day-orchestrator" / "status"
    for path in (candidate, status_dir.parent, status_dir):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ContractError("日任务状态目录不安全", kind="evidence")
    path = status_dir / f"{date}.json"
    if not path.exists() and not path.is_symlink():
        return None
    return CognitiveDayOrchestrator._safe_read(path)


__all__ = [
    "CognitiveDayOrchestrator",
    "CognitiveDayResult",
    "ORCHESTRATOR_VERSION",
    "inspect_cognitive_day_status",
]
