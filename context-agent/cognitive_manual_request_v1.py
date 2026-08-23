"""Durable browser-to-worker requests for a manual Cognitive Day run.

The browser may only append a tiny immutable request.  This module validates
that object, serializes one consumer per request, re-reads the shared master
gate through :class:`CognitiveScheduleCore`, and writes a finite immutable
result.  Source text, model text and filesystem paths are deliberately absent
from both contracts.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import errno
import fcntl
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from cognitive_schedule_v1 import CognitiveScheduleCore
from cognitive_v1 import COGNITIVE_SCHEMA_VERSION, persisted_json_bytes
from core import ContractError, sha256_bytes


REQUEST_KIND = "memento_cognitive_manual_day_request"
RESULT_KIND = "memento_cognitive_manual_day_result"
REQUEST_RE = re.compile(r"^cman_[0-9a-f]{24}$")
RESULT_RE = re.compile(r"^cmanr_[0-9a-f]{24}$")
REQUEST_FILE_RE = re.compile(r"^(cman_[0-9a-f]{24})\.json$")
MAX_OBJECT_BYTES = 16 * 1024
RESULT_STATUSES = frozenset(
    {"completed", "master_gate_disabled", "rejected_date", "runner_failed"}
)
RUNNER_STATUSES = frozenset(
    {
        "completed",
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


def _aware_time(value: dt.datetime | None = None) -> dt.datetime:
    result = dt.datetime.now().astimezone() if value is None else value
    if not isinstance(result, dt.datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ContractError("manual worker now 必须是带时区 datetime")
    return result


def _timestamp(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{name} 必须是带时区 ISO 时间")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{name} 必须是带时区 ISO 时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{name} 必须是带时区 ISO 时间")
    return value


def _date(value: str) -> str:
    if not isinstance(value, str):
        raise ContractError("local_date 必须是 YYYY-MM-DD")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError("local_date 必须是有效 YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ContractError("local_date 必须是 YYYY-MM-DD")
    return value


@dataclass(frozen=True)
class ManualDayRequest:
    id: str
    created_at: str
    local_date: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ManualDayRequest":
        fields = {"schema_version", "kind", "id", "created_at", "local_date", "status"}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ContractError("manual day request 字段不符合合同")
        if value["schema_version"] != COGNITIVE_SCHEMA_VERSION or value["kind"] != REQUEST_KIND:
            raise ContractError("manual day request 版本或 kind 无效")
        if not isinstance(value["id"], str) or not REQUEST_RE.fullmatch(value["id"]):
            raise ContractError("manual day request id 无效")
        if value["status"] != "pending":
            raise ContractError("manual day request status 必须是 pending")
        created_at = _timestamp(value["created_at"], "created_at")
        local_date = _date(value["local_date"])
        created = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if created.date().isoformat() != local_date:
            raise ContractError("manual day request created_at/local_date 不一致")
        return cls(
            id=value["id"],
            created_at=created_at,
            local_date=local_date,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": COGNITIVE_SCHEMA_VERSION,
            "kind": REQUEST_KIND,
            "id": self.id,
            "created_at": self.created_at,
            "local_date": self.local_date,
            "status": "pending",
        }


@dataclass(frozen=True)
class ManualDayResult:
    id: str
    request_id: str
    request_sha256: str
    completed_at: str
    local_date: str
    status: str
    runner_status: str | None
    error_kind: str | None

    def __post_init__(self) -> None:
        if not RESULT_RE.fullmatch(self.id) or not REQUEST_RE.fullmatch(self.request_id):
            raise ContractError("manual day result id 无效")
        if not re.fullmatch(r"[0-9a-f]{64}", self.request_sha256):
            raise ContractError("manual day result request_sha256 无效")
        _timestamp(self.completed_at, "completed_at")
        _date(self.local_date)
        if self.status not in RESULT_STATUSES:
            raise ContractError("manual day result status 无效")
        if self.runner_status is not None and self.runner_status not in RUNNER_STATUSES:
            raise ContractError("manual day result runner_status 无效")
        if self.error_kind not in {None, "date", "contract", "runtime"}:
            raise ContractError("manual day result error_kind 无效")
        if self.status == "completed" and self.runner_status is None:
            raise ContractError("completed manual result 缺少 runner_status")
        if self.status != "completed" and self.runner_status is not None:
            raise ContractError("未完成 manual result 不得携带 runner_status")
        expected_errors = {
            "completed": {None},
            "master_gate_disabled": {None},
            "rejected_date": {"date"},
            "runner_failed": {"contract", "runtime"},
        }
        if self.error_kind not in expected_errors[self.status]:
            raise ContractError("manual day result status/error_kind 不一致")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": COGNITIVE_SCHEMA_VERSION,
            "kind": RESULT_KIND,
            "id": self.id,
            "request_id": self.request_id,
            "request_sha256": self.request_sha256,
            "completed_at": self.completed_at,
            "local_date": self.local_date,
            "status": self.status,
            "runner_status": self.runner_status,
            "error_kind": self.error_kind,
        }


@dataclass(frozen=True)
class ManualWorkerReport:
    seen: int
    processed: int
    already_resolved: int
    completed: int
    rejected: int
    failed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": COGNITIVE_SCHEMA_VERSION,
            "kind": "memento_cognitive_manual_day_worker_result",
            "seen": self.seen,
            "processed": self.processed,
            "already_resolved": self.already_resolved,
            "completed": self.completed,
            "rejected": self.rejected,
            "failed": self.failed,
        }


class _RequestLock:
    def __init__(self, store: "ManualDayRequestStore", request_id: str) -> None:
        self.store = store
        self.request_id = request_id
        self.descriptor: int | None = None

    def __enter__(self) -> "_RequestLock":
        self.store._ensure_layout()
        path = self.store.locks_dir / f"manual-{self.request_id}.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise ContractError("manual request 锁无法安全打开", kind="evidence") from exc
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600
            ):
                raise ContractError("manual request 锁必须是 owner-only 单链接文件", kind="evidence")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            current = path.lstat()
            after = os.fstat(descriptor)
            if (
                current.st_dev != after.st_dev
                or current.st_ino != after.st_ino
                or any(
                    getattr(before, field) != getattr(after, field)
                    for field in ("st_dev", "st_ino", "st_uid", "st_nlink")
                )
                or not stat.S_ISREG(after.st_mode)
                or stat.S_IMODE(after.st_mode) != 0o600
            ):
                raise ContractError("manual request 锁在等待期间变化", kind="evidence")
        except Exception:
            os.close(descriptor)
            raise
        self.descriptor = descriptor
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.descriptor is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


class ManualDayRequestStore:
    def __init__(self, vault: Path, *, state_root: Path | None = None) -> None:
        try:
            self.vault = vault.expanduser().resolve(strict=True)
        except OSError as exc:
            raise ContractError("Vault 目录不存在", kind="not_found") from exc
        details = self.vault.lstat()
        if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
            raise ContractError("Vault 必须是当前用户目录", kind="evidence")
        raw_root = state_root or self.vault / ".context-agent" / "cognitive-secretary-v1"
        if not raw_root.is_absolute():
            raw_root = self.vault / raw_root
        self.root = Path(os.path.abspath(os.fspath(raw_root.expanduser())))
        try:
            self.root.relative_to(self.vault)
        except ValueError as exc:
            raise ContractError("state_root 必须在 Vault 内", kind="evidence") from exc
        self.requests_dir = self.root / "manual-day-requests"
        self.results_dir = self.root / "manual-day-results"
        self.locks_dir = self.root / "locks"

    def _secure_directory(self, path: Path) -> None:
        try:
            path.relative_to(self.vault)
        except ValueError as exc:
            raise ContractError("manual day 目录越过 Vault 边界", kind="evidence") from exc
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ContractError("manual day 目录不安全", kind="evidence")
        path.mkdir(mode=0o700, exist_ok=True)
        details = path.lstat()
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) & 0o077
        ):
            raise ContractError("manual day 目录必须 owner-only", kind="evidence")

    def _ensure_layout(self) -> None:
        relative = self.root.relative_to(self.vault)
        current = self.vault
        for component in relative.parts:
            current = current / component
            self._secure_directory(current)
        for path in (self.requests_dir, self.results_dir, self.locks_dir):
            self._secure_directory(path)

    def _read(self, path: Path, *, name: str) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            kind = "evidence" if exc.errno in {errno.ELOOP, errno.EISDIR} else "runtime"
            raise ContractError(f"{name} 无法安全读取", kind=kind) from exc
        try:
            before = os.fstat(descriptor)
            current = path.lstat()
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size > MAX_OBJECT_BYTES
                or current.st_dev != before.st_dev
                or current.st_ino != before.st_ino
            ):
                raise ContractError(f"{name} 必须是 owner-only 单链接普通文件", kind="evidence")
            raw = os.read(descriptor, MAX_OBJECT_BYTES + 1)
            if len(raw) > MAX_OBJECT_BYTES or os.read(descriptor, 1):
                raise ContractError(f"{name} 超过允许大小", kind="evidence")
            after = os.fstat(descriptor)
            stable = ("st_dev", "st_ino", "st_uid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(before, field) != getattr(after, field) for field in stable):
                raise ContractError(f"{name} 读取期间变化", kind="stale")
            return raw
        finally:
            os.close(descriptor)

    def _write_immutable(self, path: Path, payload: bytes, *, name: str) -> None:
        self._secure_directory(path.parent)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                descriptor = -1
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError:
                if self._read(path, name=name) != payload:
                    raise ContractError(f"不可变 {name} 冲突", kind="conflict")
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
            try:
                with contextlib.suppress(OSError):
                    os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()

    @staticmethod
    def _result_id(request_sha256: str) -> str:
        return "cmanr_" + sha256_bytes(f"manual-result:{request_sha256}".encode("utf-8"))[:24]

    def create_request(self, request: ManualDayRequest | Mapping[str, Any]) -> tuple[ManualDayRequest, Path]:
        value = ManualDayRequest.from_dict(
            request.to_dict() if isinstance(request, ManualDayRequest) else request
        )
        self._ensure_layout()
        path = self.requests_dir / f"{value.id}.json"
        self._write_immutable(path, persisted_json_bytes(value.to_dict()), name="manual day request")
        return value, path

    def _load_request(self, path: Path, expected_id: str) -> tuple[ManualDayRequest, bytes, str]:
        raw = self._read(path, name="manual day request")
        try:
            payload = json.loads(raw.decode("utf-8"))
            request = ManualDayRequest.from_dict(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ContractError) as exc:
            raise ContractError("manual day request 合同无效", kind="evidence") from exc
        if request.id != expected_id or raw != persisted_json_bytes(request.to_dict()):
            raise ContractError("manual day request 文件名或字节不一致", kind="evidence")
        return request, raw, sha256_bytes(raw)

    def _load_result(self, path: Path, *, request_id: str, request_sha256: str) -> ManualDayResult:
        raw = self._read(path, name="manual day result")
        try:
            payload = json.loads(raw.decode("utf-8"))
            result = ManualDayResult(
                id=payload["id"], request_id=payload["request_id"], request_sha256=payload["request_sha256"],
                completed_at=payload["completed_at"], local_date=payload["local_date"], status=payload["status"],
                runner_status=payload["runner_status"], error_kind=payload["error_kind"],
            )
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ContractError) as exc:
            raise ContractError("manual day result 合同无效", kind="evidence") from exc
        if set(payload) != set(result.to_dict()) or payload.get("schema_version") != COGNITIVE_SCHEMA_VERSION or payload.get("kind") != RESULT_KIND:
            raise ContractError("manual day result 字段不符合合同", kind="evidence")
        if raw != persisted_json_bytes(result.to_dict()):
            raise ContractError("manual day result 字节不符合合同", kind="evidence")
        if result.request_id != request_id or result.request_sha256 != request_sha256:
            raise ContractError("manual day result 未绑定精确 request", kind="evidence")
        return result

    def consume(
        self,
        *,
        day_runner: Callable[[str, str], Any],
        now: dt.datetime | None = None,
    ) -> ManualWorkerReport:
        local_now = _aware_time(now)
        self._ensure_layout()
        paths = []
        for path in sorted(self.requests_dir.iterdir(), key=lambda item: item.name):
            if path.name.startswith("."):
                continue
            match = REQUEST_FILE_RE.fullmatch(path.name)
            if match is None:
                raise ContractError("manual request 目录含有未授权文件", kind="evidence")
            paths.append((match.group(1), path))
        processed = resolved = completed = rejected = failed = 0
        for request_id, path in paths:
            with _RequestLock(self, request_id):
                request, _, request_sha = self._load_request(path, request_id)
                result_path = self.results_dir / f"{self._result_id(request_sha)}.json"
                if result_path.exists() or result_path.is_symlink():
                    self._load_result(result_path, request_id=request.id, request_sha256=request_sha)
                    resolved += 1
                    continue
                completed_at = local_now.isoformat(timespec="seconds")
                if request.local_date != local_now.date().isoformat():
                    result = ManualDayResult(
                        id=self._result_id(request_sha), request_id=request.id, request_sha256=request_sha,
                        completed_at=completed_at, local_date=request.local_date, status="rejected_date",
                        runner_status=None, error_kind="date",
                    )
                    rejected += 1
                else:
                    report = CognitiveScheduleCore(self.vault, day_runner=day_runner).run_manual(now=local_now)
                    schedule_status = report.get("status")
                    if schedule_status == "completed":
                        result = ManualDayResult(
                            id=self._result_id(request_sha), request_id=request.id, request_sha256=request_sha,
                            completed_at=completed_at, local_date=request.local_date, status="completed",
                            runner_status=report.get("runner_status"), error_kind=None,
                        )
                        completed += 1
                    elif schedule_status == "master_gate_disabled":
                        result = ManualDayResult(
                            id=self._result_id(request_sha), request_id=request.id, request_sha256=request_sha,
                            completed_at=completed_at, local_date=request.local_date, status="master_gate_disabled",
                            runner_status=None, error_kind=None,
                        )
                        rejected += 1
                    else:
                        result = ManualDayResult(
                            id=self._result_id(request_sha), request_id=request.id, request_sha256=request_sha,
                            completed_at=completed_at, local_date=request.local_date, status="runner_failed",
                            runner_status=None, error_kind=report.get("error_kind") if report.get("error_kind") in {"contract", "runtime"} else "runtime",
                        )
                        failed += 1
                self._write_immutable(result_path, persisted_json_bytes(result.to_dict()), name="manual day result")
                processed += 1
        return ManualWorkerReport(len(paths), processed, resolved, completed, rejected, failed)


__all__ = [
    "ManualDayRequest",
    "ManualDayResult",
    "ManualDayRequestStore",
    "ManualWorkerReport",
    "REQUEST_KIND",
    "RESULT_KIND",
]
