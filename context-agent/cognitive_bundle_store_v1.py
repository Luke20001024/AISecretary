"""Atomic daily-bundle and formal-memory store for Cognitive Secretary v1.

This module is deliberately model-free.  It accepts already materialized,
strict domain objects, re-validates every referenced byte sequence, and makes a
whole daily bundle visible through one atomically replaced head catalogue.

Object revision files may be written before the catalogue switch during crash
recovery.  They are *not* visible heads: every public reader starts from
``formal-head-index.json`` and verifies the referenced committed manifest.
Candidate payloads live under ``daily-bundles/staging/candidates`` and are
never copied into the formal revision directories.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import errno
import fcntl
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from core import ContractError, canonical_json, sha256_bytes
from cognitive_store_v1 import RecordStore
from cognitive_v1 import (
    COGNITIVE_SCHEMA_VERSION,
    DailySummaryRevision,
    InterpretationReceiptRevision,
    ObjectRef,
    RelationRevision,
    ReusableMemoryRevision,
    SourceRecordRevision,
    SourceSpan,
    make_daily_summary_id,
    persisted_json_bytes,
    validate_long_term_evidence_refs,
)


MAX_JSON_BYTES = 16 * 1024 * 1024
EMPTY_CREATED_AT = "1970-01-01T00:00:00+00:00"

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DREQ_RE = re.compile(r"^dreq_[0-9a-f]{24}$")
DRUN_RE = re.compile(r"^drun_[0-9a-f]{24}$")
BUNDLE_RE = re.compile(r"^db_\d{8}$")
TRANSACTION_RE = re.compile(r"^btx_[0-9a-f]{24}$")
CANDIDATE_MEMORY_RE = re.compile(r"^cmem_[0-9a-f]{24}$")
CANDIDATE_RELATION_RE = re.compile(r"^crel_[0-9a-f]{24}$")
MEMORY_RE = re.compile(r"^rmem_[0-9a-f]{24}$")
RELATION_RE = re.compile(r"^rel_[0-9a-f]{24}$")
UNDERSTANDING_RE = re.compile(r"^mem_[0-9a-f]{24}$")
ACTION_RE = re.compile(r"^cact_[0-9a-f]{24}$")

INPUT_HASH_FIELDS = frozenset(
    {
        "source_manifest_sha256",
        "receipt_manifest_sha256",
        "profile_sha256",
        "user_action_watermark_sha256",
        "policy_sha256",
    }
)
CATALOG_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "revision",
        "generated_at",
        "daily_bundles",
        "daily_summaries",
        "reusable_memories",
        "relations",
    }
)
MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "bundle_id",
        "revision",
        "status",
        "operation",
        "created_at",
        "committed_at",
        "local_date",
        "request_id",
        "run_id",
        "input_hashes",
        "source_refs",
        "receipt_refs",
        "memory_refs",
        "relation_refs",
        "summary_ref",
        "candidate_materializations",
        "long_term_result_ref",
        "warnings",
        "previous_revision_sha256",
    }
)
MATERIALIZATION_FIELDS = frozenset(
    {"candidate_kind", "candidate_id", "formal_ref"}
)
CANDIDATE_STAGE_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "run_id",
        "local_date",
        "created_at",
        "memory_candidates",
        "relation_candidates",
    }
)
TRANSACTION_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "transaction_id",
        "created_at",
        "base_catalog_sha256",
        "target_catalog",
        "manifest",
        "summary",
        "memories",
        "relations",
        "candidate_stage_sha256",
    }
)
AGENT_RESULT_FIELDS = frozenset(
    {"request_id", "run_id", "response_sha256", "status", "memory_ref"}
)

ALLOWED_WARNINGS = frozenset(
    {
        "review_failed",
        "long_term_failed",
        "landscape_failed",
        "partial_source_unavailable",
    }
)


def _object(value: Any, fields: frozenset[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{name} 必须是 JSON object")
    actual = frozenset(value)
    if actual != fields:
        raise ContractError(
            f"{name} 字段不符合合同；缺失={sorted(fields - actual)}；未知={sorted(actual - fields)}"
        )
    return dict(value)


def _text(value: Any, name: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or "\x00" in value:
        raise ContractError(f"{name} 必须是无首尾空白的非空字符串")
    if len(value) > maximum:
        raise ContractError(f"{name} 超过 {maximum} 个字符")
    return value


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ContractError(f"{name} 必须是 SHA-256")
    return value


def _date(value: Any, name: str = "local_date") -> str:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise ContractError(f"{name} 必须是 YYYY-MM-DD")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"{name} 不是有效日期") from exc
    return value


def _timestamp(value: Any, name: str) -> str:
    text = _text(value, name, 64)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{name} 必须是带时区时间") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{name} 必须带时区")
    return text


def _now_text(now: dt.datetime | None) -> str:
    value = now or dt.datetime.now().astimezone()
    if value.tzinfo is None:
        raise ContractError("now 必须带时区")
    return value.isoformat(timespec="seconds")


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _object_ref(value: ObjectRef | Mapping[str, Any], name: str) -> ObjectRef:
    try:
        return value if isinstance(value, ObjectRef) else ObjectRef.from_dict(value)
    except ContractError as exc:
        raise ContractError(f"{name} 无效：{exc}", kind=exc.kind) from exc


def _bundle_id(local_date: str) -> str:
    return "db_" + _date(local_date).replace("-", "")


def _ref_list(value: Any, kind: str, name: str) -> list[ObjectRef]:
    if not isinstance(value, list) or len(value) > 50_000:
        raise ContractError(f"{name} 必须是 array")
    refs = [_object_ref(row, f"{name}[{index}]") for index, row in enumerate(value)]
    if any(ref.kind != kind for ref in refs):
        raise ContractError(f"{name} 只能包含 {kind}")
    keys = [(ref.id, ref.revision, ref.revision_sha256) for ref in refs]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ContractError(f"{name} 必须唯一且有序")
    return refs


@dataclass(frozen=True)
class BundleCommitResult:
    status: str
    bundle_ref: ObjectRef
    summary_ref: ObjectRef
    memory_refs: tuple[ObjectRef, ...]
    relation_refs: tuple[ObjectRef, ...]

    @property
    def committed(self) -> bool:
        return self.status == "committed"


@dataclass(frozen=True)
class UserRevisionResult:
    status: str
    object_ref: ObjectRef


@dataclass(frozen=True)
class TerminalRetractionResult:
    status: str
    memory_refs: tuple[ObjectRef, ...]
    relation_refs: tuple[ObjectRef, ...]


class _BundleLock:
    def __init__(self, store: "CognitiveBundleStore") -> None:
        self.store = store
        self.descriptor: int | None = None

    def __enter__(self) -> "_BundleLock":
        self.store._ensure_layout()
        path = self.store.locks_dir / "daily-bundle.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise ContractError("daily bundle 锁无法安全打开", kind="evidence") from exc
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid() or details.st_nlink != 1:
            os.close(descriptor)
            raise ContractError("daily bundle 锁必须是当前用户的单链接普通文件", kind="evidence")
        with contextlib.suppress(OSError):
            os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            current = path.lstat()
            locked = os.fstat(descriptor)
        except OSError as exc:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            raise ContractError(
                "daily bundle 锁在等待期间无法重新验证",
                kind="evidence",
            ) from exc
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_dev != locked.st_dev
            or current.st_ino != locked.st_ino
            or current.st_uid != os.getuid()
            or current.st_nlink != 1
            or stat.S_IMODE(current.st_mode) & 0o077
        ):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            raise ContractError(
                "daily bundle 锁在等待期间发生变化",
                kind="evidence",
            )
        self.descriptor = descriptor
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.descriptor is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


class _AgentProfileLock:
    """Acquire the canonical Agent V1 profile commit lock safely.

    Agent V1 does not currently expose a public profile guard.  Its immutable
    memory writers all serialize on ``agent-v1/locks/profile.lock``; using the
    same inode here gives the daily formal commit the corresponding read-side
    guard without changing either store's public API.
    """

    def __init__(self, store: "CognitiveBundleStore") -> None:
        self.store = store
        self.descriptor: int | None = None

    def __enter__(self) -> "_AgentProfileLock":
        directory = self.store.vault / ".context-agent" / "agent-v1" / "locks"
        for path in (
            self.store.vault / ".context-agent",
            self.store.vault / ".context-agent" / "agent-v1",
            directory,
        ):
            self.store._secure_directory(path)
        path = directory / "profile.lock"
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise ContractError(
                "Agent profile 锁无法安全打开",
                kind="evidence",
            ) from exc
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.getuid()
            or details.st_nlink != 1
            or stat.S_IMODE(details.st_mode) & 0o077
        ):
            os.close(descriptor)
            raise ContractError(
                "Agent profile 锁必须是 owner-only 的单链接普通文件",
                kind="evidence",
            )
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        current = path.lstat()
        locked = os.fstat(descriptor)
        if (
            current.st_dev != locked.st_dev
            or current.st_ino != locked.st_ino
            or current.st_uid != os.getuid()
            or current.st_nlink != 1
            or stat.S_IMODE(current.st_mode) & 0o077
        ):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            raise ContractError(
                "Agent profile 锁在等待期间发生变化",
                kind="evidence",
            )
        self.descriptor = descriptor
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.descriptor is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


class CognitiveBundleStore:
    """Secure formal store whose only visibility root is the head catalogue."""

    def __init__(
        self,
        vault: Path,
        *,
        state_root: Path | None = None,
        fault_hook: Callable[[str], None] | None = None,
        action_watermark_reader: Callable[[], str] | None = None,
        profile_sha256_reader: Callable[[], str] | None = None,
    ) -> None:
        try:
            resolved = vault.expanduser().resolve(strict=True)
        except OSError as exc:
            raise ContractError("Vault 目录不存在", kind="not_found") from exc
        if not resolved.is_dir():
            raise ContractError("Vault 目录不存在", kind="not_found")
        self.vault = resolved
        root = state_root or (resolved / ".context-agent" / "cognitive-secretary-v1")
        if not root.is_absolute():
            root = resolved / root
        candidate = root.parent.resolve() / root.name
        try:
            candidate.relative_to(resolved)
        except ValueError as exc:
            raise ContractError("state_root 必须位于 Vault 内", kind="evidence") from exc
        self.root = candidate
        self.memory_dir = self.root / "memory-revisions"
        self.relation_dir = self.root / "relation-revisions"
        self.summary_dir = self.root / "daily-summary-revisions"
        self.receipt_dir = self.root / "receipts"
        self.records_dir = self.root / "records"
        self.bundle_root = self.root / "daily-bundles"
        self.staging_root = self.bundle_root / "staging"
        self.transaction_staging_dir = self.staging_root / "transactions"
        self.candidate_staging_dir = self.staging_root / "candidates"
        self.committed_dir = self.bundle_root / "committed"
        self.quarantine_dir = self.bundle_root / "quarantine"
        self.journal_dir = self.bundle_root / "journals"
        self.feedback_journal_dir = self.bundle_root / "feedback-journals"
        self.locks_dir = self.root / "locks"
        self.catalog_path = self.root / "formal-head-index.json"
        self._fault_hook = fault_hook
        if action_watermark_reader is not None and not callable(
            action_watermark_reader
        ):
            raise ContractError("action watermark reader 必须可调用")
        if profile_sha256_reader is not None and not callable(
            profile_sha256_reader
        ):
            raise ContractError("profile SHA reader 必须可调用")
        self._action_watermark_reader = (
            self._canonical_action_watermark_sha256
            if action_watermark_reader is None
            else action_watermark_reader
        )
        self._profile_sha256_reader = (
            self._canonical_profile_sha256
            if profile_sha256_reader is None
            else profile_sha256_reader
        )

    def _canonical_action_watermark_sha256(self) -> str:
        """Read the authoritative Cognitive action head for this store."""

        # Local imports keep the formal-store module acyclic while making the
        # public constructor fail closed without orchestration wiring.
        from cognitive_actions_v1 import CognitiveActionStore

        return CognitiveActionStore(
            self.vault,
            state_root=self.root,
        ).action_watermark()[1]

    def _canonical_profile_sha256(self) -> str:
        """Read the authoritative Agent V1 profile projection for this Vault."""

        from agent_v1 import build_agent_profile

        return build_agent_profile(self.vault)["profile_sha256"]

    def set_action_watermark_reader(
        self,
        reader: Callable[[], str] | None,
    ) -> None:
        """Bind formal commits to the current Cognitive user-action head.

        Passing ``None`` restores the canonical local action-store reader; it
        never disables the commit guard.  Tests that need a fixed reader must
        inject it explicitly.
        """

        if reader is not None and not callable(reader):
            raise ContractError("action watermark reader 必须可调用")
        self._action_watermark_reader = (
            self._canonical_action_watermark_sha256 if reader is None else reader
        )

    def set_profile_sha256_reader(
        self,
        reader: Callable[[], str] | None,
    ) -> None:
        """Bind daily formal commits to the current Agent profile head.

        Review and long-term-result append revisions inherit an already
        committed daily decision, so they deliberately do not re-run this
        check. Initial and feedback-recompute commits must still see the exact
        profile frozen before Daily Integrator inference. Passing ``None``
        restores the canonical Agent V1 profile reader; it never disables the
        guard.
        """

        if reader is not None and not callable(reader):
            raise ContractError("profile SHA reader 必须可调用")
        self._profile_sha256_reader = (
            self._canonical_profile_sha256 if reader is None else reader
        )

    # ------------------------------------------------------------------
    # Hardened filesystem primitives
    # ------------------------------------------------------------------
    def _ensure_layout(self) -> None:
        for path in (
            self.root.parent,
            self.root,
            self.memory_dir,
            self.relation_dir,
            self.summary_dir,
            self.receipt_dir,
            self.records_dir,
            self.bundle_root,
            self.staging_root,
            self.transaction_staging_dir,
            self.candidate_staging_dir,
            self.committed_dir,
            self.quarantine_dir,
            self.journal_dir,
            self.feedback_journal_dir,
            self.locks_dir,
        ):
            self._secure_directory(path)

    def _secure_directory(self, path: Path) -> None:
        try:
            path.relative_to(self.vault)
        except ValueError as exc:
            raise ContractError("运行目录越过 Vault 边界", kind="evidence") from exc
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ContractError(f"运行路径不安全：{path.name}", kind="evidence")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        details = path.lstat()
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
            raise ContractError(f"运行路径不是安全目录：{path.name}", kind="evidence")
        with contextlib.suppress(OSError):
            path.chmod(0o700)

    def _safe_read_bytes(self, path: Path, *, name: str, maximum: int = MAX_JSON_BYTES) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError as exc:
            raise ContractError(f"{name} 不存在：{path.name}", kind="not_found") from exc
        except OSError as exc:
            kind = "evidence" if exc.errno in {errno.ELOOP, errno.EISDIR} else "runtime"
            raise ContractError(f"{name} 无法安全读取", kind=kind) from exc
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1 or before.st_size > maximum:
                raise ContractError(f"{name} 必须是当前用户的单链接普通文件", kind="evidence")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum:
                    raise ContractError(f"{name} 超过允许大小", kind="evidence")
            after = os.fstat(descriptor)
            stable = ("st_dev", "st_ino", "st_uid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(before, key) != getattr(after, key) for key in stable):
                raise ContractError(f"{name} 在读取期间变化", kind="stale")
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def _safe_read_json_with_sha(self, path: Path, *, name: str) -> tuple[dict[str, Any], str]:
        content = self._safe_read_bytes(path, name=name)
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError(f"{name} JSON 无法解析", kind="schema") from exc
        if not isinstance(value, dict):
            raise ContractError(f"{name} 必须是 JSON object")
        return value, sha256_bytes(content)

    def _safe_write_immutable_bytes(self, path: Path, payload: bytes) -> None:
        self._secure_directory(path.parent)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            existing = self._safe_read_bytes(path, name="immutable JSON")
            if existing != payload:
                raise ContractError(f"拒绝覆盖已有不可变文件：{path.name}", kind="conflict")
            return
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("不可变 JSON 写入失败")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._fsync_directory(path.parent)

    def _safe_write_immutable(self, path: Path, value: Mapping[str, Any]) -> None:
        self._safe_write_immutable_bytes(path, _json_bytes(value))

    def _safe_write_replace(self, path: Path, value: Mapping[str, Any]) -> None:
        self._secure_directory(path.parent)
        if path.is_symlink():
            raise ContractError(f"拒绝覆盖符号链接：{path.name}", kind="evidence")
        if path.exists():
            details = path.lstat()
            if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid() or details.st_nlink != 1:
                raise ContractError(f"拒绝覆盖不安全文件：{path.name}", kind="evidence")
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(_json_bytes(value))
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()

    def _fsync_directory(self, path: Path) -> None:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            with contextlib.suppress(OSError):
                os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _fault(self, stage: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(stage)

    # ------------------------------------------------------------------
    # Catalogue and exact object resolution
    # ------------------------------------------------------------------
    def _empty_catalog(self) -> dict[str, Any]:
        return {
            "schema_version": COGNITIVE_SCHEMA_VERSION,
            "kind": "memento_cognitive_formal_head_index",
            "revision": 0,
            "generated_at": EMPTY_CREATED_AT,
            "daily_bundles": [],
            "daily_summaries": [],
            "reusable_memories": [],
            "relations": [],
        }

    def _validate_catalog(self, value: Any) -> dict[str, Any]:
        item = _object(value, CATALOG_FIELDS, "formal head index")
        if item["schema_version"] != COGNITIVE_SCHEMA_VERSION or item["kind"] != "memento_cognitive_formal_head_index":
            raise ContractError("formal head index schema/kind 无效")
        if type(item["revision"]) is not int or item["revision"] < 0:
            raise ContractError("formal head index revision 无效")
        _timestamp(item["generated_at"], "formal head index.generated_at")
        _ref_list(item["daily_bundles"], "daily_bundle", "daily_bundles")
        _ref_list(item["daily_summaries"], "daily_summary", "daily_summaries")
        _ref_list(item["reusable_memories"], "reusable_memory", "reusable_memories")
        _ref_list(item["relations"], "relation", "relations")
        return item

    def _load_catalog_unchecked(self) -> tuple[dict[str, Any], str]:
        if self.catalog_path.is_symlink():
            raise ContractError("formal-head-index.json 不能是符号链接", kind="evidence")
        if not self.catalog_path.exists():
            value = self._empty_catalog()
            return value, sha256_bytes(_json_bytes(value))
        value, digest = self._safe_read_json_with_sha(self.catalog_path, name="formal head index")
        return self._validate_catalog(value), digest

    @staticmethod
    def _catalog_map(catalog: Mapping[str, Any], key: str) -> dict[str, ObjectRef]:
        return {row["id"]: ObjectRef.from_dict(row) for row in catalog[key]}

    @staticmethod
    def _replace_catalog_ref(catalog: dict[str, Any], key: str, ref: ObjectRef) -> None:
        rows = {row["id"]: dict(row) for row in catalog[key]}
        rows[ref.id] = ref.to_dict()
        catalog[key] = [rows[identifier] for identifier in sorted(rows)]

    def _revision_path(self, kind: str, identifier: str, revision: int) -> Path:
        if type(revision) is not int or not 1 <= revision <= 999_999:
            raise ContractError("revision 无效")
        if kind == "daily_summary":
            if not re.fullmatch(r"dsum_\d{8}", identifier):
                raise ContractError("summary_id 无效")
            digits = identifier[5:]
            local_date = _date(
                f"{digits[:4]}-{digits[4:6]}-{digits[6:]}",
                "summary_id date",
            )
            if identifier != make_daily_summary_id(local_date):
                raise ContractError("summary_id 无效")
            directory = self.summary_dir
        elif kind == "reusable_memory":
            if not MEMORY_RE.fullmatch(identifier):
                raise ContractError("memory_id 无效")
            directory = self.memory_dir
        elif kind == "relation":
            if not RELATION_RE.fullmatch(identifier):
                raise ContractError("relation_id 无效")
            directory = self.relation_dir
        elif kind == "source_record":
            if not re.fullmatch(r"rec_[0-9a-f]{24}", identifier):
                raise ContractError("record_id 无效")
            directory = self.records_dir
        elif kind == "interpretation_receipt":
            if not re.fullmatch(r"rcp_[0-9a-f]{24}", identifier):
                raise ContractError("receipt_id 无效")
            directory = self.receipt_dir
        elif kind == "understanding":
            if not UNDERSTANDING_RE.fullmatch(identifier):
                raise ContractError("understanding id 无效")
            directory = self.vault / ".context-agent" / "agent-v1" / "memories"
        else:
            raise ContractError("不支持的正式对象 kind")
        return directory / f"{identifier}.r{revision:06d}.json"

    def _load_revision_object(self, ref: ObjectRef) -> tuple[dict[str, Any], str]:
        if ref.kind == "daily_bundle":
            return self._load_bundle_manifest_by_ref(ref), ref.revision_sha256
        path = self._revision_path(ref.kind, ref.id, ref.revision)
        value, digest = self._safe_read_json_with_sha(path, name=f"{ref.kind} revision")
        if digest != ref.revision_sha256:
            raise ContractError(f"{ref.kind} revision hash 不一致", kind="evidence")
        if ref.kind == "source_record":
            parsed = SourceRecordRevision.from_dict(value)
            identifier, revision = parsed.record_id, parsed.revision
        elif ref.kind == "interpretation_receipt":
            parsed = InterpretationReceiptRevision.from_dict(value)
            identifier, revision = parsed.receipt_id, parsed.revision
        elif ref.kind == "daily_summary":
            parsed = DailySummaryRevision.from_dict(value)
            identifier, revision = parsed.summary_id, parsed.revision
        elif ref.kind == "reusable_memory":
            parsed = ReusableMemoryRevision.from_dict(value)
            identifier, revision = parsed.memory_id, parsed.revision
        elif ref.kind == "relation":
            parsed = RelationRevision.from_dict(value)
            identifier, revision = parsed.relation_id, parsed.revision
        elif ref.kind == "understanding":
            identifier, revision = value.get("memory_id"), value.get("revision")
            if identifier != ref.id or revision != ref.revision or value.get("status") != "active":
                raise ContractError("understanding revision 不再是 active 正式对象", kind="evidence")
        else:
            raise ContractError("不支持的对象引用")
        if identifier != ref.id or revision != ref.revision:
            raise ContractError("revision 文件名与内容不一致", kind="evidence")
        return value, digest

    def _latest_revision_number(self, kind: str, identifier: str) -> int:
        directory = self._revision_path(kind, identifier, 1).parent
        if directory.is_symlink() or not directory.is_dir():
            raise ContractError(f"{kind} revision 目录不安全", kind="evidence")
        pattern = re.compile(rf"^{re.escape(identifier)}\.r(\d{{6}})\.json$")
        numbers: list[int] = []
        for entry in os.scandir(directory):
            match = pattern.fullmatch(entry.name)
            if match:
                numbers.append(int(match.group(1)))
        if not numbers:
            raise ContractError(f"{kind} revision 不存在", kind="not_found")
        return max(numbers)

    def _assert_current_external_ref(self, ref: ObjectRef) -> dict[str, Any]:
        if ref.kind in {"reusable_memory", "relation", "daily_summary", "daily_bundle"}:
            raise ContractError("内部正式 ref 必须通过 catalogue 校验")
        value, _ = self._load_revision_object(ref)
        if self._latest_revision_number(ref.kind, ref.id) != ref.revision:
            raise ContractError(f"{ref.kind} ref 已过期", kind="stale")
        if value.get("status") == "tombstone":
            raise ContractError(f"{ref.kind} ref 已删除", kind="stale")
        return value

    def _assert_source_span(self, span: SourceSpan, source_ref: ObjectRef) -> None:
        if (
            span.record_id != source_ref.id
            or span.record_revision != source_ref.revision
            or span.record_revision_sha256 != source_ref.revision_sha256
        ):
            raise ContractError("SourceSpan 未绑定 bundle source ref", kind="evidence")
        source_value = self._assert_current_external_ref(source_ref)
        if source_value["source_file"] != span.source_file:
            raise ContractError("SourceSpan source_file 与 revision 不一致", kind="evidence")
        parsed = RecordStore(self.vault, state_root=self.root).parse_day(span.source_file)
        matches = [record for record in parsed.records if record.entry_sha256 == source_value["entry_sha256"]]
        if len(matches) != 1:
            raise ContractError("SourceSpan 无法唯一回到当前原始记录", kind="stale")
        record = matches[0]
        if (
            record.source_file != source_value["source_file"]
            or record.local_date != source_value["local_date"]
            or record.source_app != source_value["source_app"]
            or record.source_type != source_value["source_type"]
        ):
            raise ContractError("SourceRecord revision 与当前原始记录不一致", kind="stale")
        if not (
            source_value["line_start"]
            <= span.line_start
            <= span.line_end
            <= source_value["line_end"]
        ):
            raise ContractError("SourceSpan 行号越过原始记录", kind="evidence")
        source_bytes = self._safe_read_bytes(self.vault / span.source_file, name="日级 Markdown", maximum=32 * 1024 * 1024)
        lines = source_bytes.splitlines(keepends=False)
        # SourceSpan line numbers are bound to the immutable source revision.
        # If a byte-identical block later moves because another record was
        # inserted above it, map the original relative offsets to the unique
        # current block instead of treating harmless movement as an edit.
        relative_start = span.line_start - source_value["line_start"]
        relative_end = span.line_end - source_value["line_start"]
        current_start = record.line_start - 1 + relative_start
        current_end = record.line_start + relative_end
        selected = b"\n".join(lines[current_start:current_end])
        try:
            selected_text = selected.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError("SourceSpan 原文不是 UTF-8", kind="evidence") from exc
        if span.quote != selected_text:
            raise ContractError("SourceSpan quote 未与指定行逐字一致", kind="stale")

    def _assert_current_span(self, span: SourceSpan) -> None:
        """Resolve a span through its byte-bound source revision and current head."""

        self._assert_source_span(
            span,
            ObjectRef(
                "source_record",
                span.record_id,
                span.record_revision,
                span.record_revision_sha256,
            ),
        )

    def _assert_current_bundle_inputs(
        self,
        local_date: str,
        source_refs: Sequence[ObjectRef],
        receipt_refs: Sequence[ObjectRef],
    ) -> tuple[dict[str, ObjectRef], dict[str, ObjectRef]]:
        source_by_id: dict[str, ObjectRef] = {}
        for ref in source_refs:
            value = self._assert_current_external_ref(ref)
            if value["local_date"] != local_date:
                raise ContractError("bundle source_ref 不属于当前日期", kind="evidence")
            source_by_id[ref.id] = ref
        receipt_by_id: dict[str, ObjectRef] = {}
        for ref in receipt_refs:
            value = self._assert_current_external_ref(ref)
            receipt = InterpretationReceiptRevision.from_dict(value)
            if (
                receipt.record_ref.id not in source_by_id
                or receipt.record_ref != source_by_id[receipt.record_ref.id]
            ):
                raise ContractError("bundle receipt 未绑定当前 source manifest", kind="evidence")
            for span in receipt.source_spans:
                self._assert_source_span(span, receipt.record_ref)
            receipt_by_id[ref.id] = ref
        return source_by_id, receipt_by_id

    def _assert_current_action_watermark(
        self,
        input_hashes: Mapping[str, Any],
    ) -> None:
        reader = self._action_watermark_reader
        if reader is None:
            raise ContractError(
                "formal store 缺少 action watermark reader",
                kind="runtime",
            )
        expected = input_hashes.get("user_action_watermark_sha256")
        _sha(expected, "input_hashes.user_action_watermark_sha256")
        current = reader()
        _sha(current, "current user action watermark")
        if current != expected:
            raise ContractError(
                "Daily 结果生成后用户 action 已变化",
                kind="stale",
            )

    @contextlib.contextmanager
    def _guard_current_action_watermark(
        self,
        input_hashes: Mapping[str, Any],
    ):
        """Keep the frozen action head fixed through the catalogue switch.

        The final check and formal publication share the exact owner-only lock
        used by ``CognitiveActionStore.submit_action``.  The bundle lock is
        deliberately acquired first throughout this module; action
        reconciliation releases its action lock before entering the formal
        store, so this order does not introduce an inverse lock path.

        The public constructor and ``set_action_watermark_reader(None)`` both
        bind the canonical local reader.  A missing internal reader therefore
        indicates invalid runtime wiring and fails closed.
        """

        reader = self._action_watermark_reader
        if reader is None:
            raise ContractError(
                "formal store 缺少 action watermark reader",
                kind="runtime",
            )

        expected = input_hashes.get("user_action_watermark_sha256")
        _sha(expected, "input_hashes.user_action_watermark_sha256")

        # Preserve the configured-reader contract first.  A change between
        # this read and guard acquisition is still rejected by the guarded
        # canonical read below.
        self._assert_current_action_watermark(input_hashes)

        # Local import keeps the model-free formal-store dependency surface
        # narrow and avoids coupling module import order.  CognitiveActionStore
        # does not import this module.
        from cognitive_actions_v1 import CognitiveActionStore

        action_store = CognitiveActionStore(self.vault, state_root=self.root)
        with action_store.guard_action_watermark(expected):
            yield

    def _assert_current_profile_sha256(
        self,
        input_hashes: Mapping[str, Any],
        *,
        operation: str,
    ) -> None:
        if operation in {"append_long_term_result", "append_review_result"}:
            return
        reader = self._profile_sha256_reader
        if reader is None:
            raise ContractError(
                "formal store 缺少 profile SHA reader",
                kind="runtime",
            )
        expected = input_hashes.get("profile_sha256")
        _sha(expected, "input_hashes.profile_sha256")
        current = reader()
        _sha(current, "current profile_sha256")
        if current != expected:
            raise ContractError(
                "Daily 结果生成后长期理解 profile 已变化",
                kind="stale",
            )

    @contextlib.contextmanager
    def _guard_current_profile_sha256(
        self,
        input_hashes: Mapping[str, Any],
        *,
        operation: str,
    ):
        """Keep Agent V1 profile state fixed through formal publication."""

        if operation in {"append_long_term_result", "append_review_result"}:
            yield
            return
        if self._profile_sha256_reader is None:
            raise ContractError(
                "formal store 缺少 profile SHA reader",
                kind="runtime",
            )

        # Check once before waiting, then again under Agent V1's canonical
        # writer lock.  A writer that completed while this commit waited is
        # rejected; after the locked check every conforming Agent V1 writer is
        # blocked until the daily catalogue has switched.
        self._assert_current_profile_sha256(
            input_hashes,
            operation=operation,
        )
        with _AgentProfileLock(self):
            self._assert_current_profile_sha256(
                input_hashes,
                operation=operation,
            )
            yield

    def _validate_formal_chain(self, head: ObjectRef) -> None:
        previous_sha: str | None = None
        for revision in range(1, head.revision + 1):
            path = self._revision_path(head.kind, head.id, revision)
            value, digest = self._safe_read_json_with_sha(path, name=f"{head.kind} revision chain")
            if head.kind == "daily_summary":
                parsed: DailySummaryRevision | ReusableMemoryRevision | RelationRevision = DailySummaryRevision.from_dict(value)
                identifier = parsed.summary_id
            elif head.kind == "reusable_memory":
                parsed = ReusableMemoryRevision.from_dict(value)
                identifier = parsed.memory_id
            elif head.kind == "relation":
                parsed = RelationRevision.from_dict(value)
                identifier = parsed.relation_id
            else:
                raise ContractError("不支持的正式 revision chain", kind="evidence")
            if identifier != head.id or parsed.revision != revision:
                raise ContractError("正式 revision chain identity 不一致", kind="evidence")
            if parsed.previous_revision_sha256 != previous_sha:
                raise ContractError("正式 revision chain 不连续", kind="evidence")
            previous_sha = digest
        if previous_sha != head.revision_sha256:
            raise ContractError("正式 head hash 与 revision chain 不一致", kind="evidence")

    def _validate_bundle_chain(self, head: ObjectRef) -> None:
        previous_sha: str | None = None
        expected_date = f"{head.id[3:7]}-{head.id[7:9]}-{head.id[9:11]}"
        for revision in range(1, head.revision + 1):
            ref = ObjectRef("daily_bundle", head.id, revision, "0" * 64)
            path = self._bundle_directory(ref.id, ref.revision) / "manifest.json"
            value, digest = self._safe_read_json_with_sha(path, name="daily bundle revision chain")
            manifest = self._validate_manifest_shape(value)
            if (
                manifest["bundle_id"] != head.id
                or manifest["revision"] != revision
                or manifest["local_date"] != expected_date
                or manifest["previous_revision_sha256"] != previous_sha
            ):
                raise ContractError("daily bundle revision chain 不连续", kind="evidence")
            previous_sha = digest
        if previous_sha != head.revision_sha256:
            raise ContractError("daily bundle head hash 与 revision chain 不一致", kind="evidence")

    def _validate_catalog_targets(self, catalog: Mapping[str, Any]) -> None:
        catalog = self._validate_catalog(catalog)
        for row in catalog["daily_bundles"]:
            self._validate_bundle_chain(ObjectRef.from_dict(row))
        for key, kind in (
            ("daily_summaries", "daily_summary"),
            ("reusable_memories", "reusable_memory"),
            ("relations", "relation"),
        ):
            for row in catalog[key]:
                ref = ObjectRef.from_dict(row)
                if ref.kind != kind:
                    raise ContractError("catalogue ref kind 无效", kind="evidence")
                self._validate_formal_chain(ref)
                value, _ = self._load_revision_object(ref)
                if value.get("status") not in {"active", "tombstone"}:
                    raise ContractError("catalogue head status 无效", kind="evidence")

    # ------------------------------------------------------------------
    # Candidate staging (never promoted as formal JSON)
    # ------------------------------------------------------------------
    def _candidate_stage_path(self, run_id: str) -> Path:
        if not DRUN_RE.fullmatch(run_id):
            raise ContractError("run_id 无效")
        return self.candidate_staging_dir / f"{run_id}.json"

    def _validate_candidate_stage(self, value: Any) -> dict[str, Any]:
        item = _object(value, CANDIDATE_STAGE_FIELDS, "candidate stage")
        if item["schema_version"] != COGNITIVE_SCHEMA_VERSION or item["kind"] != "memento_daily_candidate_stage":
            raise ContractError("candidate stage schema/kind 无效")
        if not DRUN_RE.fullmatch(item["run_id"]):
            raise ContractError("candidate stage run_id 无效")
        _date(item["local_date"])
        _timestamp(item["created_at"], "candidate stage.created_at")
        for key, pattern, id_key in (
            ("memory_candidates", CANDIDATE_MEMORY_RE, "candidate_id"),
            ("relation_candidates", CANDIDATE_RELATION_RE, "candidate_id"),
        ):
            rows = item[key]
            if not isinstance(rows, list) or len(rows) > 10_000:
                raise ContractError(f"{key} 必须是 array")
            identifiers: list[str] = []
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise ContractError(f"{key}[{index}] 必须是 object")
                identifier = row.get(id_key)
                if not isinstance(identifier, str) or not pattern.fullmatch(identifier):
                    raise ContractError(f"{key}[{index}].candidate_id 无效")
                if any(name in row for name in ("memory_id", "relation_id", "revision", "revision_sha256")):
                    raise ContractError("候选对象不得伪装成正式 revision")
                identifiers.append(identifier)
            if identifiers != sorted(identifiers) or len(identifiers) != len(set(identifiers)):
                raise ContractError(f"{key} 必须按 candidate_id 唯一排序")
        return item

    def stage_candidates(
        self,
        *,
        run_id: str,
        local_date: str,
        memory_candidates: Sequence[Mapping[str, Any]],
        relation_candidates: Sequence[Mapping[str, Any]],
        now: dt.datetime | None = None,
    ) -> Path:
        value = self._validate_candidate_stage(
            {
                "schema_version": COGNITIVE_SCHEMA_VERSION,
                "kind": "memento_daily_candidate_stage",
                "run_id": run_id,
                "local_date": local_date,
                "created_at": _now_text(now),
                "memory_candidates": [dict(row) for row in sorted(memory_candidates, key=lambda row: row.get("candidate_id", ""))],
                "relation_candidates": [dict(row) for row in sorted(relation_candidates, key=lambda row: row.get("candidate_id", ""))],
            }
        )
        with _BundleLock(self):
            self._recover_staging_locked()
            path = self._candidate_stage_path(run_id)
            self._safe_write_immutable(path, value)
            return path

    def _load_candidate_stage(self, run_id: str, local_date: str) -> tuple[dict[str, Any], str]:
        value, digest = self._safe_read_json_with_sha(self._candidate_stage_path(run_id), name="candidate stage")
        item = self._validate_candidate_stage(value)
        if item["run_id"] != run_id or item["local_date"] != local_date:
            raise ContractError("candidate stage 未绑定当前日级 run", kind="evidence")
        return item, digest

    # ------------------------------------------------------------------
    # Manifest and transaction contracts
    # ------------------------------------------------------------------
    def _validate_agent_result(self, value: Any, *, require_current: bool = False) -> dict[str, Any]:
        item = _object(value, AGENT_RESULT_FIELDS, "long_term_result_ref")
        _text(item["request_id"], "agent request_id", 64)
        _text(item["run_id"], "agent run_id", 64)
        _sha(item["response_sha256"], "agent response_sha256")
        if item["status"] not in {"updated", "no_change", "insufficient_evidence", "budget_exhausted", "stale", "error"}:
            raise ContractError("Agent result status 无效")
        if item["status"] == "updated":
            ref = _object_ref(item["memory_ref"], "Agent memory_ref")
            if ref.kind != "understanding":
                raise ContractError("updated Agent result 必须引用 understanding")
            if require_current:
                self._assert_current_external_ref(ref)
        elif item["memory_ref"] is not None:
            raise ContractError("非 updated Agent result 的 memory_ref 必须为 null")
        return item

    def _validate_manifest_shape(self, value: Any) -> dict[str, Any]:
        item = _object(value, MANIFEST_FIELDS, "daily bundle manifest")
        if item["schema_version"] != COGNITIVE_SCHEMA_VERSION or item["kind"] != "memento_daily_bundle_revision":
            raise ContractError("daily bundle manifest schema/kind 无效")
        local_date = _date(item["local_date"])
        if item["bundle_id"] != _bundle_id(local_date) or not BUNDLE_RE.fullmatch(item["bundle_id"]):
            raise ContractError("bundle_id 与 local_date 不一致")
        if type(item["revision"]) is not int or not 1 <= item["revision"] <= 999_999:
            raise ContractError("bundle revision 无效")
        if item["status"] != "committed" or item["operation"] not in {
            "initial_commit",
            "append_long_term_result",
            "append_review_result",
            "feedback_recompute",
        }:
            raise ContractError("bundle status/operation 无效")
        if (item["revision"] == 1) != (item["operation"] == "initial_commit"):
            raise ContractError("bundle revision 1 必须且只能 initial_commit")
        _timestamp(item["created_at"], "bundle.created_at")
        _timestamp(item["committed_at"], "bundle.committed_at")
        if not DREQ_RE.fullmatch(item["request_id"]) or not DRUN_RE.fullmatch(item["run_id"]):
            raise ContractError("bundle request/run id 无效")
        hashes = _object(item["input_hashes"], INPUT_HASH_FIELDS, "bundle.input_hashes")
        for key in INPUT_HASH_FIELDS:
            _sha(hashes[key], f"input_hashes.{key}")
        _ref_list(item["source_refs"], "source_record", "bundle.source_refs")
        _ref_list(item["receipt_refs"], "interpretation_receipt", "bundle.receipt_refs")
        _ref_list(item["memory_refs"], "reusable_memory", "bundle.memory_refs")
        _ref_list(item["relation_refs"], "relation", "bundle.relation_refs")
        summary_ref = _object_ref(item["summary_ref"], "bundle.summary_ref")
        if summary_ref.kind != "daily_summary" or summary_ref.id != make_daily_summary_id(local_date):
            raise ContractError("bundle summary_ref 无效")
        if not isinstance(item["candidate_materializations"], list):
            raise ContractError("candidate_materializations 必须是 array")
        materialization_keys: list[tuple[str, str]] = []
        for index, raw in enumerate(item["candidate_materializations"]):
            row = _object(raw, MATERIALIZATION_FIELDS, f"candidate_materializations[{index}]")
            kind = row["candidate_kind"]
            if kind == "memory":
                pattern, formal_kind = CANDIDATE_MEMORY_RE, "reusable_memory"
            elif kind == "relation":
                pattern, formal_kind = CANDIDATE_RELATION_RE, "relation"
            else:
                raise ContractError("candidate_kind 无效")
            if not isinstance(row["candidate_id"], str) or not pattern.fullmatch(row["candidate_id"]):
                raise ContractError("candidate_id 无效")
            formal = _object_ref(row["formal_ref"], "candidate formal_ref")
            if formal.kind != formal_kind:
                raise ContractError("candidate 与 formal kind 不一致")
            materialization_keys.append((kind, row["candidate_id"]))
        if materialization_keys != sorted(materialization_keys) or len(materialization_keys) != len(set(materialization_keys)):
            raise ContractError("candidate_materializations 必须唯一且有序")
        if item["long_term_result_ref"] is not None:
            self._validate_agent_result(item["long_term_result_ref"])
        if item["operation"] == "append_long_term_result" and item["long_term_result_ref"] is None:
            raise ContractError("append_long_term_result 必须携带 Agent result")
        if not isinstance(item["warnings"], list) or any(row not in ALLOWED_WARNINGS for row in item["warnings"]):
            raise ContractError("bundle warnings 无效")
        if item["warnings"] != sorted(set(item["warnings"])):
            raise ContractError("bundle warnings 必须唯一且有序")
        if item["revision"] == 1:
            if item["previous_revision_sha256"] is not None:
                raise ContractError("bundle revision 1 previous hash 必须为 null")
        else:
            _sha(item["previous_revision_sha256"], "bundle.previous_revision_sha256")
        return item

    def _validate_transaction_shape(self, value: Any) -> dict[str, Any]:
        item = _object(value, TRANSACTION_FIELDS, "bundle transaction")
        if item["schema_version"] != COGNITIVE_SCHEMA_VERSION or item["kind"] != "memento_daily_bundle_transaction":
            raise ContractError("bundle transaction schema/kind 无效")
        if not isinstance(item["transaction_id"], str) or not TRANSACTION_RE.fullmatch(item["transaction_id"]):
            raise ContractError("transaction_id 无效")
        _timestamp(item["created_at"], "transaction.created_at")
        _sha(item["base_catalog_sha256"], "base_catalog_sha256")
        self._validate_catalog(item["target_catalog"])
        self._validate_manifest_shape(item["manifest"])
        DailySummaryRevision.from_dict(item["summary"])
        if not isinstance(item["memories"], list) or not isinstance(item["relations"], list):
            raise ContractError("transaction formal objects 必须是 array")
        [ReusableMemoryRevision.from_dict(row) for row in item["memories"]]
        [RelationRevision.from_dict(row) for row in item["relations"]]
        if item["candidate_stage_sha256"] is not None:
            _sha(item["candidate_stage_sha256"], "candidate_stage_sha256")
        identity = {key: item[key] for key in TRANSACTION_FIELDS if key != "transaction_id"}
        expected = "btx_" + sha256_bytes(canonical_json(identity).encode("utf-8"))[:24]
        if item["transaction_id"] != expected:
            raise ContractError("transaction_id 与内容不一致", kind="evidence")
        return item

    def _formal_ref(self, value: DailySummaryRevision | ReusableMemoryRevision | RelationRevision) -> ObjectRef:
        if isinstance(value, DailySummaryRevision):
            return ObjectRef("daily_summary", value.summary_id, value.revision, value.sha256)
        if isinstance(value, ReusableMemoryRevision):
            return ObjectRef("reusable_memory", value.memory_id, value.revision, value.sha256)
        return ObjectRef("relation", value.relation_id, value.revision, value.sha256)

    def _assert_revision_transition(
        self,
        current: ObjectRef | None,
        value: DailySummaryRevision | ReusableMemoryRevision | RelationRevision,
        *,
        daily_commit: bool,
    ) -> None:
        ref = self._formal_ref(value)
        previous_sha = value.previous_revision_sha256
        if current is None:
            if value.revision != 1 or previous_sha is not None:
                raise ContractError(f"{ref.kind} 新对象必须从 revision 1 开始", kind="conflict")
            return
        if current.kind != ref.kind or current.id != ref.id:
            raise ContractError("正式对象 head kind/id 不一致", kind="conflict")
        if value.revision != current.revision + 1 or previous_sha != current.revision_sha256:
            raise ContractError(f"{ref.kind} CAS 失败", kind="stale")
        previous, _ = self._load_revision_object(current)
        if previous.get("status") == "tombstone" or previous.get("operation") == "tombstone":
            raise ContractError("tombstone 不得复活", kind="conflict")
        if daily_commit and previous.get("operation") == "user_edit":
            raise ContractError("用户修改优先，Daily Integrator 不得覆盖", kind="conflict")

    def _assert_review_file_binding(
        self,
        review_file: str,
        review_sha256: str,
    ) -> None:
        """Bind a Review ref to the exact safe Vault file bytes."""

        _sha(review_sha256, "review_sha256")
        # ``DailySummaryRevision`` already validates a POSIX Vault-relative
        # path.  Walk every parent as well so O_NOFOLLOW on the final file
        # cannot be bypassed through a symlinked directory.
        path = self.vault / review_file
        current = self.vault
        for part in Path(review_file).parts[:-1]:
            current = current / part
            if current.is_symlink() or not current.is_dir():
                raise ContractError("Review 父目录不安全", kind="evidence")
            details = current.lstat()
            if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
                raise ContractError("Review 父目录必须属于当前用户", kind="evidence")
        content = self._safe_read_bytes(path, name="Daily Review")
        if sha256_bytes(content) != review_sha256:
            raise ContractError("Daily Review 字节与 review_sha256 不一致", kind="evidence")

    def _assert_review_summary_transition(
        self,
        current: ObjectRef,
        summary: DailySummaryRevision,
    ) -> None:
        self._assert_revision_transition(current, summary, daily_commit=True)
        previous_value, _ = self._load_revision_object(current)
        previous = DailySummaryRevision.from_dict(previous_value)
        if previous.review_sha256 is not None:
            raise ContractError("已绑定 Review 的 summary 不得再次追加", kind="conflict")
        if summary.operation != "regenerate" or summary.review_sha256 is None:
            raise ContractError("Review 绑定必须生成带 hash 的 regenerate summary", kind="conflict")
        unchanged_fields = (
            "schema_version",
            "kind",
            "summary_id",
            "status",
            "local_date",
            "overview",
            "themes",
            "changes",
            "unresolved_questions",
            "action_clues",
            "source_refs",
            "receipt_refs",
            "review_file",
        )
        previous_dict = previous.to_dict()
        summary_dict = summary.to_dict()
        if any(previous_dict[field] != summary_dict[field] for field in unchanged_fields):
            raise ContractError("Review 绑定不得改写 summary 内容", kind="evidence")
        self._assert_review_file_binding(summary.review_file, summary.review_sha256)

    def _validate_bundle_semantics(
        self,
        *,
        catalog: Mapping[str, Any],
        manifest: Mapping[str, Any],
        summary: DailySummaryRevision,
        memories: Sequence[ReusableMemoryRevision],
        relations: Sequence[RelationRevision],
        candidate_stage: Mapping[str, Any] | None,
    ) -> None:
        local_date = manifest["local_date"]
        if manifest["long_term_result_ref"] is not None:
            self._validate_agent_result(
                manifest["long_term_result_ref"],
                # A Review binding preserves the exact result already visible
                # in the base bundle.  A later understanding head must not
                # rewrite or invalidate that historical day-level fact.
                require_current=manifest["operation"]
                != "append_review_result",
            )
        source_refs = [ObjectRef.from_dict(row) for row in manifest["source_refs"]]
        receipt_refs = [ObjectRef.from_dict(row) for row in manifest["receipt_refs"]]
        source_by_id, receipt_by_id = self._assert_current_bundle_inputs(
            local_date,
            source_refs,
            receipt_refs,
        )
        if summary.local_date != local_date or tuple(ref.to_dict() for ref in summary.source_refs) != tuple(ref.to_dict() for ref in source_refs) or tuple(ref.to_dict() for ref in summary.receipt_refs) != tuple(ref.to_dict() for ref in receipt_refs):
            raise ContractError("daily summary refs 必须精确等于 bundle 输入 manifest", kind="evidence")
        if summary.status != "active" or summary.operation not in {"generate", "regenerate"}:
            raise ContractError("daily bundle 只能提交 active generate/regenerate summary", kind="conflict")
        summary_ref = self._formal_ref(summary)
        if summary_ref != ObjectRef.from_dict(manifest["summary_ref"]):
            raise ContractError("manifest summary_ref hash 不一致", kind="evidence")

        catalog_memories = self._catalog_map(catalog, "reusable_memories")
        catalog_relations = self._catalog_map(catalog, "relations")
        batch_memories = {memory.memory_id: memory for memory in memories}
        batch_relations = {relation.relation_id: relation for relation in relations}
        if len(batch_memories) != len(memories) or len(batch_relations) != len(relations):
            raise ContractError("bundle formal revision 不得重复")
        manifest_memory_refs = [ObjectRef.from_dict(row) for row in manifest["memory_refs"]]
        manifest_relation_refs = [ObjectRef.from_dict(row) for row in manifest["relation_refs"]]
        if manifest["operation"] in {"append_long_term_result", "append_review_result"}:
            current_bundle = self._current_bundle_ref(catalog, local_date)
            if (
                current_bundle is None
                or current_bundle.revision + 1 != manifest["revision"]
                or current_bundle.revision_sha256
                != manifest["previous_revision_sha256"]
            ):
                raise ContractError(
                    f"{manifest['operation']} 未精确绑定上一版 bundle",
                    kind="stale",
                )
            previous_manifest = self._load_bundle_manifest_by_ref(current_bundle)
            if manifest["operation"] == "append_long_term_result":
                inherited_fields = (
                    "input_hashes",
                    "source_refs",
                    "receipt_refs",
                    "memory_refs",
                    "relation_refs",
                    "summary_ref",
                    "candidate_materializations",
                )
            else:
                inherited_fields = (
                    "request_id",
                    "run_id",
                    "input_hashes",
                    "source_refs",
                    "receipt_refs",
                    "memory_refs",
                    "relation_refs",
                    "candidate_materializations",
                    "long_term_result_ref",
                    "warnings",
                )
            if any(
                manifest[field] != previous_manifest[field]
                for field in inherited_fields
            ):
                raise ContractError(
                    f"{manifest['operation']} 未精确继承当前日级对象",
                    kind="evidence",
                )
            if memories or relations:
                raise ContractError(
                    f"{manifest['operation']} 不得复制或改写正式对象",
                    kind="conflict",
                )
            for ref in manifest_memory_refs:
                head = catalog_memories.get(ref.id)
                if manifest["operation"] == "append_long_term_result":
                    valid = head == ref
                else:
                    valid = head is not None and head.revision >= ref.revision
                if not valid:
                    raise ContractError(
                        "append 复用的 reusable memory ref 不在正式链上",
                        kind="stale",
                    )
                self._load_revision_object(ref)
            for ref in manifest_relation_refs:
                head = catalog_relations.get(ref.id)
                if manifest["operation"] == "append_long_term_result":
                    valid = head == ref
                else:
                    valid = head is not None and head.revision >= ref.revision
                if not valid:
                    raise ContractError(
                        "append 复用的 relation ref 不在正式链上",
                        kind="stale",
                    )
                self._load_revision_object(ref)
            if manifest["operation"] == "append_review_result":
                previous_summary_ref = ObjectRef.from_dict(
                    previous_manifest["summary_ref"]
                )
                current_summary_ref = self._catalog_map(
                    catalog, "daily_summaries"
                ).get(summary.summary_id)
                if current_summary_ref != previous_summary_ref:
                    raise ContractError(
                        "append_review_result base summary 已过期",
                        kind="stale",
                    )
                self._assert_review_summary_transition(
                    previous_summary_ref,
                    summary,
                )
        else:
            if manifest_memory_refs != sorted((self._formal_ref(row) for row in memories), key=lambda ref: (ref.id, ref.revision, ref.revision_sha256)):
                raise ContractError("manifest memory_refs 与批次对象不一致", kind="evidence")
            if manifest_relation_refs != sorted((self._formal_ref(row) for row in relations), key=lambda ref: (ref.id, ref.revision, ref.revision_sha256)):
                raise ContractError("manifest relation_refs 与批次对象不一致", kind="evidence")

        source_span_cache: set[str] = set()
        for memory in memories:
            self._assert_revision_transition(catalog_memories.get(memory.memory_id), memory, daily_commit=True)
            if memory.status == "tombstone" or memory.operation in {"user_edit", "tombstone"}:
                raise ContractError("daily bundle 不得伪造用户 memory revision", kind="conflict")
            if memory.provenance["bundle_id"] != manifest["bundle_id"] or memory.provenance["bundle_revision"] != manifest["revision"] or memory.provenance["run_id"] != manifest["run_id"]:
                raise ContractError("memory provenance 未绑定当前 bundle", kind="evidence")
            if any(ref.id not in receipt_by_id or receipt_by_id[ref.id] != ref for ref in memory.origin_receipt_refs):
                raise ContractError("memory origin receipt 未绑定 bundle", kind="evidence")
            for span in memory.source_spans:
                source = source_by_id.get(span.record_id)
                if source is None:
                    raise ContractError("memory span 不在 bundle source manifest", kind="evidence")
                if span.sha256 not in source_span_cache:
                    self._assert_source_span(span, source)
                    source_span_cache.add(span.sha256)

        current_memory_refs = dict(catalog_memories)
        current_memory_refs.update({identifier: self._formal_ref(value) for identifier, value in batch_memories.items()})
        for relation in relations:
            self._assert_revision_transition(catalog_relations.get(relation.relation_id), relation, daily_commit=True)
            if relation.status == "tombstone" or relation.operation in {"user_edit", "tombstone"}:
                raise ContractError("daily bundle 不得伪造用户 relation revision", kind="conflict")
            if relation.provenance["bundle_id"] != manifest["bundle_id"] or relation.provenance["bundle_revision"] != manifest["revision"] or relation.provenance["run_id"] != manifest["run_id"]:
                raise ContractError("relation provenance 未绑定当前 bundle", kind="evidence")
            for endpoint in (relation.from_ref, relation.to_ref):
                if endpoint.kind == "reusable_memory":
                    if current_memory_refs.get(endpoint.id) != endpoint:
                        raise ContractError("relation reusable_memory endpoint 已过期", kind="stale")
                elif endpoint.kind == "understanding":
                    self._assert_current_external_ref(endpoint)
                else:
                    # This also prevents daily_summary and candidate IDs from
                    # ever becoming formal relation evidence.
                    raise ContractError("relation endpoint 只能是正式 memory/understanding", kind="evidence")
            for span in relation.source_spans:
                source = source_by_id.get(span.record_id)
                if source is None:
                    raise ContractError("relation span 不在 bundle source manifest", kind="evidence")
                if span.sha256 not in source_span_cache:
                    self._assert_source_span(span, source)
                    source_span_cache.add(span.sha256)

        validate_long_term_evidence_refs(source_refs + receipt_refs + manifest_memory_refs + manifest_relation_refs)

        materializations = manifest["candidate_materializations"]
        if manifest["operation"] in {"append_long_term_result", "append_review_result"}:
            return
        if materializations:
            if candidate_stage is None:
                raise ContractError("candidate materialization 缺少独立 staging", kind="evidence")
            staged_ids = {
                "memory": {row["candidate_id"] for row in candidate_stage["memory_candidates"]},
                "relation": {row["candidate_id"] for row in candidate_stage["relation_candidates"]},
            }
            batch_refs = {
                "memory": {self._formal_ref(row) for row in memories},
                "relation": {self._formal_ref(row) for row in relations},
            }
            for row in materializations:
                formal = ObjectRef.from_dict(row["formal_ref"])
                if row["candidate_id"] not in staged_ids[row["candidate_kind"]] or formal not in batch_refs[row["candidate_kind"]]:
                    raise ContractError("candidate materialization 未精确绑定 staging/formal revision", kind="evidence")

    # ------------------------------------------------------------------
    # Bundle creation, publication and recovery
    # ------------------------------------------------------------------
    def _bundle_directory(self, bundle_id: str, revision: int) -> Path:
        if not BUNDLE_RE.fullmatch(bundle_id) or type(revision) is not int or revision < 1:
            raise ContractError("bundle path 参数无效")
        return self.committed_dir / f"day_{bundle_id[3:]}.r{revision:06d}"

    def _load_bundle_manifest_by_ref(self, ref: ObjectRef) -> dict[str, Any]:
        if ref.kind != "daily_bundle" or not BUNDLE_RE.fullmatch(ref.id):
            raise ContractError("daily bundle ref 无效")
        path = self._bundle_directory(ref.id, ref.revision) / "manifest.json"
        value, digest = self._safe_read_json_with_sha(path, name="daily bundle manifest")
        item = self._validate_manifest_shape(value)
        if item["bundle_id"] != ref.id or item["revision"] != ref.revision or digest != ref.revision_sha256:
            raise ContractError("daily bundle ref hash/identity 不一致", kind="evidence")
        return item

    def _result_from_manifest(self, manifest: Mapping[str, Any], status: str) -> BundleCommitResult:
        bundle_ref = ObjectRef(
            "daily_bundle",
            manifest["bundle_id"],
            manifest["revision"],
            sha256_bytes(_json_bytes(manifest)),
        )
        return BundleCommitResult(
            status,
            bundle_ref,
            ObjectRef.from_dict(manifest["summary_ref"]),
            tuple(ObjectRef.from_dict(row) for row in manifest["memory_refs"]),
            tuple(ObjectRef.from_dict(row) for row in manifest["relation_refs"]),
        )

    def _current_bundle_ref(self, catalog: Mapping[str, Any], local_date: str) -> ObjectRef | None:
        return self._catalog_map(catalog, "daily_bundles").get(_bundle_id(local_date))

    def _stage_transaction(self, transaction: Mapping[str, Any]) -> Path:
        tx = self._validate_transaction_shape(transaction)
        stage = self.transaction_staging_dir / tx["transaction_id"]
        if stage.is_symlink() or (stage.exists() and not stage.is_dir()):
            raise ContractError("bundle staging 路径不安全", kind="evidence")
        self._secure_directory(stage)
        publish = stage / "publish"
        self._secure_directory(publish)
        self._safe_write_immutable(stage / "transaction.json", tx)
        self._safe_write_immutable(publish / "manifest.json", tx["manifest"])
        self._safe_write_immutable(publish / "summary.json", tx["summary"])
        memories_dir = publish / "memories"
        relations_dir = publish / "relations"
        self._secure_directory(memories_dir)
        self._secure_directory(relations_dir)
        for row in tx["memories"]:
            self._safe_write_immutable(memories_dir / f"{row['memory_id']}.r{row['revision']:06d}.json", row)
        for row in tx["relations"]:
            self._safe_write_immutable(relations_dir / f"{row['relation_id']}.r{row['revision']:06d}.json", row)
        return stage

    def _verify_stage_files(self, stage: Path, transaction: Mapping[str, Any]) -> None:
        publish = stage / "publish"
        if publish.exists():
            checks: list[tuple[Path, Mapping[str, Any], str]] = [
                (publish / "manifest.json", transaction["manifest"], "staged manifest"),
                (publish / "summary.json", transaction["summary"], "staged summary"),
            ]
            checks.extend(
                (publish / "memories" / f"{row['memory_id']}.r{row['revision']:06d}.json", row, "staged memory")
                for row in transaction["memories"]
            )
            checks.extend(
                (publish / "relations" / f"{row['relation_id']}.r{row['revision']:06d}.json", row, "staged relation")
                for row in transaction["relations"]
            )
            for path, expected, name in checks:
                content = self._safe_read_bytes(path, name=name)
                if content != _json_bytes(expected):
                    raise ContractError(f"{name} 字节与 transaction 不一致", kind="evidence")

    def _archive_transaction(self, stage: Path, transaction: Mapping[str, Any]) -> None:
        archive = self.journal_dir / f"{transaction['transaction_id']}.json"
        self._safe_write_immutable(archive, transaction)
        if stage.exists():
            if stage.is_symlink() or not stage.is_dir():
                raise ContractError("bundle staging 清理路径不安全", kind="evidence")
            shutil.rmtree(stage)
            self._fsync_directory(self.transaction_staging_dir)

    def _resume_transaction_locked(self, stage: Path, transaction: Mapping[str, Any]) -> None:
        tx = self._validate_transaction_shape(transaction)
        current_catalog, current_sha = self._load_catalog_unchecked()
        target_sha = sha256_bytes(_json_bytes(tx["target_catalog"]))
        if current_sha == target_sha:
            self._validate_catalog_targets(current_catalog)
            self._archive_transaction(stage, tx)
            return
        if current_sha != tx["base_catalog_sha256"]:
            raise ContractError("恢复事务的 catalogue CAS 已失效", kind="stale")

        # Both guards are acquired while the formal-store lock is already
        # held and remain held until the catalogue switch has completed.
        # Agent V1 checks Cognitive actions while holding its profile lock, so
        # the matching order here is bundle -> profile -> action. Recovery
        # uses this same publication path and cannot resurrect stale work.
        with self._guard_current_profile_sha256(
            tx["manifest"]["input_hashes"],
            operation=tx["manifest"]["operation"],
        ):
            with self._guard_current_action_watermark(
                tx["manifest"]["input_hashes"]
            ):
                self._publish_transaction_locked(stage, tx, current_catalog)

    def _publish_transaction_locked(
        self,
        stage: Path,
        tx: Mapping[str, Any],
        current_catalog: Mapping[str, Any],
    ) -> None:
        """Publish one CAS-validated transaction under all commit guards."""

        self._verify_stage_files(stage, tx)
        summary = DailySummaryRevision.from_dict(tx["summary"])
        memories = [ReusableMemoryRevision.from_dict(row) for row in tx["memories"]]
        relations = [RelationRevision.from_dict(row) for row in tx["relations"]]
        candidate_stage = None
        if tx["candidate_stage_sha256"] is not None:
            candidate_stage, candidate_sha = self._load_candidate_stage(tx["manifest"]["run_id"], tx["manifest"]["local_date"])
            if candidate_sha != tx["candidate_stage_sha256"]:
                raise ContractError("candidate stage 在提交期间变化", kind="stale")
        self._validate_bundle_semantics(
            catalog=current_catalog,
            manifest=tx["manifest"],
            summary=summary,
            memories=memories,
            relations=relations,
            candidate_stage=candidate_stage,
        )

        self._safe_write_immutable(self._revision_path("daily_summary", summary.summary_id, summary.revision), tx["summary"])
        for memory in memories:
            self._safe_write_immutable(self._revision_path("reusable_memory", memory.memory_id, memory.revision), memory.to_dict())
        for relation in relations:
            self._safe_write_immutable(self._revision_path("relation", relation.relation_id, relation.revision), relation.to_dict())
        self._fault("after_formal_revisions")

        destination = self._bundle_directory(tx["manifest"]["bundle_id"], tx["manifest"]["revision"])
        publish = stage / "publish"
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                raise ContractError("committed bundle 路径不安全", kind="evidence")
            existing, _ = self._safe_read_json_with_sha(destination / "manifest.json", name="committed manifest")
            if existing != tx["manifest"]:
                raise ContractError("committed bundle 内容冲突", kind="conflict")
            if publish.exists():
                shutil.rmtree(publish)
        else:
            if not publish.exists() or publish.is_symlink() or not publish.is_dir():
                raise ContractError("staged publish 目录缺失", kind="evidence")
            os.replace(publish, destination)
            self._fsync_directory(self.committed_dir)
        self._fault("after_committed_directory")

        self._safe_write_replace(self.catalog_path, tx["target_catalog"])
        self._fault("after_catalog_switch")
        self._validate_catalog_targets(tx["target_catalog"])
        self._archive_transaction(stage, tx)

    def _quarantine_stage(self, stage: Path, reason: str) -> None:
        if not stage.exists():
            return
        if stage.is_symlink() or not stage.is_dir():
            raise ContractError("staging 只能包含安全事务目录", kind="evidence")
        suffix = sha256_bytes(f"{stage.name}:{reason}:{stage.stat().st_mtime_ns}".encode("utf-8"))[:12]
        destination = self.quarantine_dir / f"{stage.name}.{reason}.{suffix}"
        os.replace(stage, destination)
        self._fsync_directory(self.transaction_staging_dir)

    def _quarantine_unpublished_artifacts(
        self,
        transaction: Mapping[str, Any],
        catalog: Mapping[str, Any],
    ) -> None:
        """Move exact, non-visible crash artifacts out of immutable slots."""

        tx = self._validate_transaction_shape(transaction)
        artifact_dir = self.quarantine_dir / f"{tx['transaction_id']}.artifacts"
        self._secure_directory(artifact_dir)
        catalog_maps = {
            "daily_summary": self._catalog_map(catalog, "daily_summaries"),
            "reusable_memory": self._catalog_map(catalog, "reusable_memories"),
            "relation": self._catalog_map(catalog, "relations"),
        }
        objects: list[
            tuple[str, DailySummaryRevision | ReusableMemoryRevision | RelationRevision]
        ] = [("daily_summary", DailySummaryRevision.from_dict(tx["summary"]))]
        objects.extend(
            ("reusable_memory", ReusableMemoryRevision.from_dict(row))
            for row in tx["memories"]
        )
        objects.extend(
            ("relation", RelationRevision.from_dict(row))
            for row in tx["relations"]
        )
        for kind, value in objects:
            ref = self._formal_ref(value)
            current = catalog_maps[kind].get(ref.id)
            # A later catalogue head proves this revision has become part of a
            # visible append-only chain; never move it back out.
            if current is not None and current.revision >= ref.revision:
                continue
            path = self._revision_path(kind, ref.id, ref.revision)
            if not path.exists() and not path.is_symlink():
                continue
            expected = persisted_json_bytes(value)
            if self._safe_read_bytes(path, name="unpublished formal artifact") != expected:
                raise ContractError("未发布 revision 占位内容冲突", kind="evidence")
            destination = artifact_dir / path.name
            if destination.exists() or destination.is_symlink():
                if self._safe_read_bytes(destination, name="quarantined formal artifact") != expected:
                    raise ContractError("隔离 revision 内容冲突", kind="evidence")
                path.unlink()
            else:
                os.replace(path, destination)
            self._fsync_directory(path.parent)
            self._fsync_directory(artifact_dir)

        manifest = tx["manifest"]
        bundle_ref = ObjectRef(
            "daily_bundle",
            manifest["bundle_id"],
            manifest["revision"],
            sha256_bytes(_json_bytes(manifest)),
        )
        current_bundle = self._catalog_map(catalog, "daily_bundles").get(bundle_ref.id)
        if current_bundle is None or current_bundle.revision < bundle_ref.revision:
            path = self._bundle_directory(bundle_ref.id, bundle_ref.revision)
            if path.exists() or path.is_symlink():
                if path.is_symlink() or not path.is_dir():
                    raise ContractError("未发布 bundle 路径不安全", kind="evidence")
                raw, digest = self._safe_read_json_with_sha(
                    path / "manifest.json",
                    name="unpublished bundle manifest",
                )
                if raw != manifest or digest != bundle_ref.revision_sha256:
                    raise ContractError("未发布 bundle 内容冲突", kind="evidence")
                destination = artifact_dir / path.name
                if destination.exists() or destination.is_symlink():
                    raise ContractError("bundle 隔离位置已占用", kind="evidence")
                os.replace(path, destination)
                self._fsync_directory(self.committed_dir)
                self._fsync_directory(artifact_dir)

    def _recover_staging_locked(self) -> None:
        self._ensure_layout()
        for stage in sorted(self.transaction_staging_dir.iterdir(), key=lambda path: path.name):
            if stage.is_symlink() or not stage.is_dir():
                raise ContractError("bundle staging 只能包含安全目录", kind="evidence")
            transaction_path = stage / "transaction.json"
            if not transaction_path.exists():
                self._quarantine_stage(stage, "incomplete")
                continue
            try:
                raw, _ = self._safe_read_json_with_sha(transaction_path, name="staged transaction")
                tx = self._validate_transaction_shape(raw)
                if stage.name != tx["transaction_id"]:
                    raise ContractError("staging 目录名与 transaction_id 不一致", kind="evidence")
                self._resume_transaction_locked(stage, tx)
            except ContractError as exc:
                if exc.kind == "stale":
                    catalog, _ = self._load_catalog_unchecked()
                    self._quarantine_unpublished_artifacts(tx, catalog)
                    self._quarantine_stage(stage, "stale")
                    continue
                raise

    def recover(self) -> None:
        with _BundleLock(self):
            old_hook = self._fault_hook
            self._fault_hook = None
            try:
                self._recover_staging_locked()
                catalog, _ = self._load_catalog_unchecked()
                self._validate_catalog_targets(catalog)
            finally:
                self._fault_hook = old_hook

    def commit_day_bundle(
        self,
        *,
        request_id: str,
        run_id: str,
        input_hashes: Mapping[str, str],
        source_refs: Sequence[ObjectRef | Mapping[str, Any]],
        receipt_refs: Sequence[ObjectRef | Mapping[str, Any]],
        summary: DailySummaryRevision | Mapping[str, Any],
        memories: Sequence[ReusableMemoryRevision | Mapping[str, Any]],
        relations: Sequence[RelationRevision | Mapping[str, Any]],
        candidate_materializations: Sequence[Mapping[str, Any]] = (),
        long_term_result_ref: Mapping[str, Any] | None = None,
        warnings: Sequence[str] = (),
        expected_bundle_ref: ObjectRef | Mapping[str, Any] | None = None,
        operation: str | None = None,
        now: dt.datetime | None = None,
    ) -> BundleCommitResult:
        if not DREQ_RE.fullmatch(request_id) or not DRUN_RE.fullmatch(run_id):
            raise ContractError("daily request/run id 无效")
        hashes = _object(dict(input_hashes), INPUT_HASH_FIELDS, "input_hashes")
        for key in INPUT_HASH_FIELDS:
            _sha(hashes[key], key)
        summary_object = summary if isinstance(summary, DailySummaryRevision) else DailySummaryRevision.from_dict(summary)
        memory_objects = [row if isinstance(row, ReusableMemoryRevision) else ReusableMemoryRevision.from_dict(row) for row in memories]
        relation_objects = [row if isinstance(row, RelationRevision) else RelationRevision.from_dict(row) for row in relations]
        local_date = summary_object.local_date
        created_at = _now_text(now)
        sorted_source_refs = sorted((_object_ref(row, "source_ref") for row in source_refs), key=lambda ref: (ref.id, ref.revision, ref.revision_sha256))
        sorted_receipt_refs = sorted((_object_ref(row, "receipt_ref") for row in receipt_refs), key=lambda ref: (ref.id, ref.revision, ref.revision_sha256))
        expected_source_manifest_sha = sha256_bytes(
            canonical_json([ref.to_dict() for ref in sorted_source_refs]).encode("utf-8")
        )
        expected_receipt_manifest_sha = sha256_bytes(
            canonical_json([ref.to_dict() for ref in sorted_receipt_refs]).encode("utf-8")
        )
        if (
            hashes["source_manifest_sha256"] != expected_source_manifest_sha
            or hashes["receipt_manifest_sha256"] != expected_receipt_manifest_sha
        ):
            raise ContractError(
                "input manifest hash 未精确绑定 source/receipt refs",
                kind="evidence",
            )

        with _BundleLock(self):
            self._recover_staging_locked()
            self._assert_current_action_watermark(hashes)
            catalog, catalog_sha = self._load_catalog_unchecked()
            self._validate_catalog_targets(catalog)
            current_bundle = self._current_bundle_ref(catalog, local_date)
            if current_bundle is not None:
                current_manifest = self._load_bundle_manifest_by_ref(current_bundle)
                if current_manifest["input_hashes"] == hashes:
                    source_ref_dicts = [ref.to_dict() for ref in sorted_source_refs]
                    receipt_ref_dicts = [ref.to_dict() for ref in sorted_receipt_refs]
                    if (
                        current_manifest["source_refs"] != source_ref_dicts
                        or current_manifest["receipt_refs"] != receipt_ref_dicts
                    ):
                        raise ContractError(
                            "相同 input_hashes 不得绑定不同 source/receipt refs",
                            kind="conflict",
                        )
                    self._assert_current_bundle_inputs(
                        local_date,
                        sorted_source_refs,
                        sorted_receipt_refs,
                    )
                    if operation != "append_long_term_result":
                        return self._result_from_manifest(current_manifest, "no_change")
                    if (
                        not memory_objects
                        and not relation_objects
                        and not candidate_materializations
                        and current_manifest["operation"]
                        == "append_long_term_result"
                        and current_manifest["summary_ref"]
                        == self._formal_ref(summary_object).to_dict()
                        and current_manifest["long_term_result_ref"]
                        == (
                            None
                            if long_term_result_ref is None
                            else dict(long_term_result_ref)
                        )
                    ):
                        return self._result_from_manifest(
                            current_manifest, "no_change"
                        )
                    retry_memory_refs = sorted(
                        (self._formal_ref(row).to_dict() for row in memory_objects),
                        key=lambda row: (row["id"], row["revision"], row["revision_sha256"]),
                    )
                    retry_relation_refs = sorted(
                        (self._formal_ref(row).to_dict() for row in relation_objects),
                        key=lambda row: (row["id"], row["revision"], row["revision_sha256"]),
                    )
                    retry_materials = sorted(
                        (dict(row) for row in candidate_materializations),
                        key=lambda row: (row.get("candidate_kind", ""), row.get("candidate_id", "")),
                    )
                    if (
                        current_manifest["operation"] == "append_long_term_result"
                        and current_manifest["summary_ref"] == self._formal_ref(summary_object).to_dict()
                        and current_manifest["memory_refs"] == retry_memory_refs
                        and current_manifest["relation_refs"] == retry_relation_refs
                        and current_manifest["candidate_materializations"] == retry_materials
                        and current_manifest["long_term_result_ref"]
                        == (None if long_term_result_ref is None else dict(long_term_result_ref))
                    ):
                        return self._result_from_manifest(current_manifest, "no_change")
            expected = None if expected_bundle_ref is None else _object_ref(expected_bundle_ref, "expected_bundle_ref")
            if current_bundle != expected:
                raise ContractError("daily bundle CAS 失败", kind="stale")
            bundle_revision = 1 if current_bundle is None else current_bundle.revision + 1
            bundle_operation = operation or ("initial_commit" if bundle_revision == 1 else "feedback_recompute")
            if bundle_revision == 1 and bundle_operation != "initial_commit":
                raise ContractError("首个 bundle 必须 initial_commit")
            if bundle_revision > 1 and bundle_operation == "initial_commit":
                raise ContractError("后续 bundle 不能 initial_commit")

            source_ref_dicts = [ref.to_dict() for ref in sorted_source_refs]
            receipt_ref_dicts = [ref.to_dict() for ref in sorted_receipt_refs]
            if bundle_operation == "append_long_term_result":
                if current_bundle is None:
                    raise ContractError(
                        "append_long_term_result 必须基于已提交 bundle",
                        kind="stale",
                    )
                if memory_objects or relation_objects or candidate_materializations:
                    raise ContractError(
                        "append_long_term_result 只能追加 Agent result ref",
                        kind="conflict",
                    )
                previous_manifest = self._load_bundle_manifest_by_ref(current_bundle)
                memory_refs = [
                    ObjectRef.from_dict(row)
                    for row in previous_manifest["memory_refs"]
                ]
                relation_refs = [
                    ObjectRef.from_dict(row)
                    for row in previous_manifest["relation_refs"]
                ]
                materials = [
                    dict(row)
                    for row in previous_manifest["candidate_materializations"]
                ]
            else:
                memory_refs = sorted((self._formal_ref(row) for row in memory_objects), key=lambda ref: (ref.id, ref.revision, ref.revision_sha256))
                relation_refs = sorted((self._formal_ref(row) for row in relation_objects), key=lambda ref: (ref.id, ref.revision, ref.revision_sha256))
                materials = [dict(row) for row in sorted(candidate_materializations, key=lambda row: (row.get("candidate_kind", ""), row.get("candidate_id", "")))]
            manifest = self._validate_manifest_shape(
                {
                    "schema_version": COGNITIVE_SCHEMA_VERSION,
                    "kind": "memento_daily_bundle_revision",
                    "bundle_id": _bundle_id(local_date),
                    "revision": bundle_revision,
                    "status": "committed",
                    "operation": bundle_operation,
                    "created_at": created_at,
                    "committed_at": created_at,
                    "local_date": local_date,
                    "request_id": request_id,
                    "run_id": run_id,
                    "input_hashes": hashes,
                    "source_refs": source_ref_dicts,
                    "receipt_refs": receipt_ref_dicts,
                    "memory_refs": [ref.to_dict() for ref in memory_refs],
                    "relation_refs": [ref.to_dict() for ref in relation_refs],
                    "summary_ref": self._formal_ref(summary_object).to_dict(),
                    "candidate_materializations": materials,
                    "long_term_result_ref": None if long_term_result_ref is None else dict(long_term_result_ref),
                    "warnings": sorted(set(warnings)),
                    "previous_revision_sha256": None if current_bundle is None else current_bundle.revision_sha256,
                }
            )
            candidate_stage = None
            candidate_stage_sha = None
            if materials and bundle_operation != "append_long_term_result":
                candidate_stage, candidate_stage_sha = self._load_candidate_stage(run_id, local_date)

            catalog_summaries = self._catalog_map(catalog, "daily_summaries")
            current_summary = catalog_summaries.get(summary_object.summary_id)
            if bundle_operation == "append_long_term_result":
                if current_summary != self._formal_ref(summary_object):
                    raise ContractError(
                        "append_long_term_result 必须复用当前 daily summary head",
                        kind="stale",
                    )
            else:
                self._assert_revision_transition(current_summary, summary_object, daily_commit=True)
            self._validate_bundle_semantics(
                catalog=catalog,
                manifest=manifest,
                summary=summary_object,
                memories=memory_objects,
                relations=relation_objects,
                candidate_stage=candidate_stage,
            )

            target_catalog = json.loads(json.dumps(catalog))
            target_catalog["revision"] = catalog["revision"] + 1
            target_catalog["generated_at"] = created_at
            bundle_ref = ObjectRef("daily_bundle", manifest["bundle_id"], manifest["revision"], sha256_bytes(_json_bytes(manifest)))
            self._replace_catalog_ref(target_catalog, "daily_bundles", bundle_ref)
            self._replace_catalog_ref(target_catalog, "daily_summaries", self._formal_ref(summary_object))
            for memory in memory_objects:
                self._replace_catalog_ref(target_catalog, "reusable_memories", self._formal_ref(memory))
            for relation in relation_objects:
                self._replace_catalog_ref(target_catalog, "relations", self._formal_ref(relation))
            target_catalog = self._validate_catalog(target_catalog)
            identity = {
                "schema_version": COGNITIVE_SCHEMA_VERSION,
                "kind": "memento_daily_bundle_transaction",
                "created_at": created_at,
                "base_catalog_sha256": catalog_sha,
                "target_catalog": target_catalog,
                "manifest": manifest,
                "summary": summary_object.to_dict(),
                "memories": [row.to_dict() for row in sorted(memory_objects, key=lambda row: row.memory_id)],
                "relations": [row.to_dict() for row in sorted(relation_objects, key=lambda row: row.relation_id)],
                "candidate_stage_sha256": candidate_stage_sha,
            }
            transaction = {
                **identity,
                "transaction_id": "btx_" + sha256_bytes(canonical_json(identity).encode("utf-8"))[:24],
            }
            transaction = self._validate_transaction_shape(transaction)
            stage = self._stage_transaction(transaction)
            self._fault("after_staging")
            self._resume_transaction_locked(stage, transaction)
            return self._result_from_manifest(manifest, "committed")

    def append_review_result(
        self,
        *,
        expected_bundle_ref: ObjectRef | Mapping[str, Any],
        expected_summary_ref: ObjectRef | Mapping[str, Any],
        review_file: str,
        review_sha256: str,
        user_supplement_sha256: str | None,
        now: dt.datetime | None = None,
    ) -> BundleCommitResult:
        """Atomically bind rendered Review bytes to the current daily heads.

        This is a two-head CAS: both the daily bundle and Daily Summary must
        still equal the supplied bases.  Only a new Summary revision and a new
        bundle manifest are materialized; every other formal ref is inherited
        byte-for-byte from the base manifest.
        """

        expected_bundle = _object_ref(
            expected_bundle_ref,
            "expected_bundle_ref",
        )
        expected_summary = _object_ref(
            expected_summary_ref,
            "expected_summary_ref",
        )
        if expected_bundle.kind != "daily_bundle" or not BUNDLE_RE.fullmatch(
            expected_bundle.id
        ):
            raise ContractError("expected_bundle_ref 不是日级 bundle")
        if expected_summary.kind != "daily_summary":
            raise ContractError("expected_summary_ref 不是 Daily Summary")
        _sha(review_sha256, "review_sha256")
        if user_supplement_sha256 is not None:
            _sha(user_supplement_sha256, "user_supplement_sha256")
        created_at = _now_text(now)

        with _BundleLock(self):
            self._recover_staging_locked()
            catalog, catalog_sha = self._load_catalog_unchecked()
            self._validate_catalog_targets(catalog)

            base_manifest = self._load_bundle_manifest_by_ref(expected_bundle)
            base_summary_value, _ = self._load_revision_object(expected_summary)
            base_summary = DailySummaryRevision.from_dict(base_summary_value)
            if (
                base_manifest["local_date"] != base_summary.local_date
                or base_manifest["summary_ref"] != expected_summary.to_dict()
                or expected_bundle.id != _bundle_id(base_summary.local_date)
            ):
                raise ContractError(
                    "Review base bundle/summary 未精确绑定",
                    kind="stale",
                )
            if review_file != base_summary.review_file:
                raise ContractError(
                    "Review 路径与 base summary 不一致",
                    kind="evidence",
                )
            self._assert_review_file_binding(review_file, review_sha256)

            current_bundle = self._current_bundle_ref(
                catalog,
                base_summary.local_date,
            )
            current_summary = self._catalog_map(
                catalog,
                "daily_summaries",
            ).get(base_summary.summary_id)

            if current_bundle != expected_bundle or current_summary != expected_summary:
                # An exact retry deliberately carries the old pair of CAS
                # refs.  Recognize only the one direct append produced from
                # those bases; any other advancement remains stale.
                if current_bundle is not None and current_summary is not None:
                    current_manifest = self._load_bundle_manifest_by_ref(
                        current_bundle
                    )
                    current_summary_value, _ = self._load_revision_object(
                        current_summary
                    )
                    current_summary_object = DailySummaryRevision.from_dict(
                        current_summary_value
                    )
                    is_direct_review_append = (
                        current_manifest["operation"] == "append_review_result"
                        and current_manifest["previous_revision_sha256"]
                        == expected_bundle.revision_sha256
                        and current_manifest["revision"]
                        == expected_bundle.revision + 1
                        and current_manifest["summary_ref"]
                        == current_summary.to_dict()
                        and current_summary.id == expected_summary.id
                        and current_summary.revision
                        == expected_summary.revision + 1
                        and current_summary_object.previous_revision_sha256
                        == expected_summary.revision_sha256
                    )
                    if is_direct_review_append:
                        if (
                            current_summary_object.review_file == review_file
                            and current_summary_object.review_sha256
                            == review_sha256
                            and current_summary_object.user_supplement_sha256
                            == user_supplement_sha256
                        ):
                            return self._result_from_manifest(
                                current_manifest,
                                "no_change",
                            )
                        raise ContractError(
                            "Review base 已绑定不同的字节 hash",
                            kind="conflict",
                        )
                raise ContractError("Review 绑定双 CAS 失败", kind="stale")

            if base_summary.review_sha256 is not None:
                raise ContractError(
                    "base summary 已绑定 Review",
                    kind="conflict",
                )

            summary = DailySummaryRevision.from_dict(
                {
                    **base_summary.to_dict(),
                    "revision": base_summary.revision + 1,
                    # The shared v1 summary contract currently names any
                    # system-created follow-up revision ``regenerate``.
                    "operation": "regenerate",
                    "created_at": created_at,
                    "review_file": review_file,
                    "review_sha256": review_sha256,
                    "user_supplement_sha256": user_supplement_sha256,
                    "previous_revision_sha256": expected_summary.revision_sha256,
                }
            )
            summary_ref = self._formal_ref(summary)
            manifest = self._validate_manifest_shape(
                {
                    **base_manifest,
                    "revision": expected_bundle.revision + 1,
                    "operation": "append_review_result",
                    "created_at": created_at,
                    "committed_at": created_at,
                    "summary_ref": summary_ref.to_dict(),
                    "previous_revision_sha256": expected_bundle.revision_sha256,
                }
            )
            self._validate_bundle_semantics(
                catalog=catalog,
                manifest=manifest,
                summary=summary,
                memories=(),
                relations=(),
                candidate_stage=None,
            )

            target_catalog = json.loads(json.dumps(catalog))
            target_catalog["revision"] = catalog["revision"] + 1
            target_catalog["generated_at"] = created_at
            bundle_ref = ObjectRef(
                "daily_bundle",
                manifest["bundle_id"],
                manifest["revision"],
                sha256_bytes(_json_bytes(manifest)),
            )
            self._replace_catalog_ref(
                target_catalog,
                "daily_bundles",
                bundle_ref,
            )
            self._replace_catalog_ref(
                target_catalog,
                "daily_summaries",
                summary_ref,
            )
            target_catalog = self._validate_catalog(target_catalog)

            identity = {
                "schema_version": COGNITIVE_SCHEMA_VERSION,
                "kind": "memento_daily_bundle_transaction",
                "created_at": created_at,
                "base_catalog_sha256": catalog_sha,
                "target_catalog": target_catalog,
                "manifest": manifest,
                "summary": summary.to_dict(),
                "memories": [],
                "relations": [],
                "candidate_stage_sha256": None,
            }
            transaction = self._validate_transaction_shape(
                {
                    **identity,
                    "transaction_id": "btx_"
                    + sha256_bytes(canonical_json(identity).encode("utf-8"))[:24],
                }
            )
            stage = self._stage_transaction(transaction)
            self._fault("after_staging")
            self._resume_transaction_locked(stage, transaction)
            return self._result_from_manifest(manifest, "committed")

    # ------------------------------------------------------------------
    # User-priority append-only revisions
    # ------------------------------------------------------------------
    def _commit_user_revision_locked(
        self,
        value: ReusableMemoryRevision | RelationRevision,
        expected_ref: ObjectRef,
        now_text: str,
    ) -> UserRevisionResult:
        catalog, _ = self._load_catalog_unchecked()
        key = "reusable_memories" if isinstance(value, ReusableMemoryRevision) else "relations"
        current = self._catalog_map(catalog, key).get(expected_ref.id)
        if current != expected_ref:
            raise ContractError("用户修改 base revision 已变化", kind="conflict")
        self._assert_revision_transition(current, value, daily_commit=False)
        if value.operation not in {"user_edit", "tombstone"} or value.provenance["origin"] != "user" or value.provenance["user_action_id"] is None:
            raise ContractError("用户 revision 必须绑定合法 user action")
        if not ACTION_RE.fullmatch(value.provenance["user_action_id"]):
            raise ContractError("用户 revision 的 user_action_id 无效")
        if (value.operation == "tombstone") != (value.status == "tombstone"):
            raise ContractError("tombstone operation/status 不一致")
        ref = self._formal_ref(value)
        self._safe_write_immutable(self._revision_path(ref.kind, ref.id, ref.revision), value.to_dict())
        self._fault("after_user_revision")
        target = json.loads(json.dumps(catalog))
        target["revision"] = catalog["revision"] + 1
        target["generated_at"] = now_text
        self._replace_catalog_ref(target, key, ref)
        target = self._validate_catalog(target)
        journal = {
            "schema_version": COGNITIVE_SCHEMA_VERSION,
            "kind": "memento_user_revision_commit",
            "created_at": now_text,
            "base_ref": expected_ref.to_dict(),
            "materialized_ref": ref.to_dict(),
        }
        self._safe_write_replace(self.catalog_path, target)
        self._safe_write_immutable(self.feedback_journal_dir / f"{value.provenance['user_action_id']}.json", journal)
        return UserRevisionResult("applied", ref)

    def commit_user_memory_revision(
        self,
        value: ReusableMemoryRevision | Mapping[str, Any],
        *,
        expected_ref: ObjectRef | Mapping[str, Any],
        now: dt.datetime | None = None,
    ) -> UserRevisionResult:
        memory = value if isinstance(value, ReusableMemoryRevision) else ReusableMemoryRevision.from_dict(value)
        expected = _object_ref(expected_ref, "expected_ref")
        if expected.kind != "reusable_memory" or expected.id != memory.memory_id:
            raise ContractError("expected_ref 未绑定 memory")
        with _BundleLock(self):
            self._recover_staging_locked()
            if memory.operation == "user_edit":
                for span in memory.source_spans:
                    self._assert_current_span(span)
                for receipt_ref in memory.origin_receipt_refs:
                    self._assert_current_external_ref(receipt_ref)
            return self._commit_user_revision_locked(memory, expected, _now_text(now))

    def commit_user_relation_revision(
        self,
        value: RelationRevision | Mapping[str, Any],
        *,
        expected_ref: ObjectRef | Mapping[str, Any],
        now: dt.datetime | None = None,
    ) -> UserRevisionResult:
        relation = value if isinstance(value, RelationRevision) else RelationRevision.from_dict(value)
        expected = _object_ref(expected_ref, "expected_ref")
        if expected.kind != "relation" or expected.id != relation.relation_id:
            raise ContractError("expected_ref 未绑定 relation")
        with _BundleLock(self):
            self._recover_staging_locked()
            # User relation edits still need current formal endpoints and exact
            # raw SourceSpans.  They cannot smuggle a summary into the graph.
            if relation.operation == "user_edit":
                catalog, _ = self._load_catalog_unchecked()
                current_memories = self._catalog_map(catalog, "reusable_memories")
                for endpoint in (relation.from_ref, relation.to_ref):
                    if endpoint.kind == "reusable_memory":
                        if current_memories.get(endpoint.id) != endpoint:
                            raise ContractError("用户 relation endpoint 已过期", kind="stale")
                    elif endpoint.kind == "understanding":
                        self._assert_current_external_ref(endpoint)
                    else:
                        raise ContractError("用户 relation endpoint 无效", kind="evidence")
                for span in relation.source_spans:
                    self._assert_current_span(span)
            return self._commit_user_revision_locked(relation, expected, _now_text(now))

    def retract_terminal_receipt_derivatives(
        self,
        receipts: Sequence[
            InterpretationReceiptRevision | Mapping[str, Any]
        ],
    ) -> TerminalRetractionResult:
        """Withdraw formal objects that still depend on terminal receipts.

        ``original_only`` and receipt tombstones are user-priority terminal
        decisions. Their previous receipt and formal-object revisions remain
        immutable for audit, while one catalogue switch moves every affected
        active memory and relation to append-only tombstone heads.

        A memory with any excluded source is withdrawn conservatively. If
        other active sources remain, a later Daily Integrator run may create a
        freshly evidenced object; this method never guesses that the previous
        statement remains valid after evidence removal.
        """

        terminal = [
            row
            if isinstance(row, InterpretationReceiptRevision)
            else InterpretationReceiptRevision.from_dict(row)
            for row in receipts
        ]
        terminal.sort(key=lambda row: row.receipt_id)
        if len({row.receipt_id for row in terminal}) != len(terminal):
            raise ContractError("terminal receipt 不得重复")
        if any(
            row.status not in {"original_only", "tombstone"}
            or row.user_action_id is None
            for row in terminal
        ):
            raise ContractError("formal 撤回只接受用户终态 receipt")
        if not terminal:
            return TerminalRetractionResult("no_change", (), ())

        terminal_by_receipt = {row.receipt_id: row for row in terminal}
        terminal_by_record = {row.record_ref.id: row for row in terminal}

        with _BundleLock(self):
            self._recover_staging_locked()
            # Bind the cascade to the exact latest terminal receipt bytes. A
            # later receipt revision would make this operation stale.
            for receipt in terminal:
                ref = ObjectRef(
                    "interpretation_receipt",
                    receipt.receipt_id,
                    receipt.revision,
                    receipt.sha256,
                )
                value, digest = self._load_revision_object(ref)
                if (
                    digest != receipt.sha256
                    or InterpretationReceiptRevision.from_dict(value) != receipt
                    or self._latest_revision_number(
                        "interpretation_receipt", receipt.receipt_id
                    )
                    != receipt.revision
                ):
                    raise ContractError("terminal receipt head 已变化", kind="stale")

            catalog, catalog_sha = self._load_catalog_unchecked()
            self._validate_catalog_targets(catalog)
            memory_tombstones: list[ReusableMemoryRevision] = []
            retracted_memory_actions: dict[str, str] = {}

            for ref in self._catalog_map(
                catalog, "reusable_memories"
            ).values():
                value, _ = self._load_revision_object(ref)
                memory = ReusableMemoryRevision.from_dict(value)
                if memory.status != "active":
                    continue
                relevant_by_id = {
                    origin.id: terminal_by_receipt[origin.id]
                    for origin in memory.origin_receipt_refs
                    if origin.id in terminal_by_receipt
                }
                relevant_by_id.update(
                    {
                        terminal_by_record[span.record_id].receipt_id: (
                            terminal_by_record[span.record_id]
                        )
                        for span in memory.source_spans
                        if span.record_id in terminal_by_record
                    }
                )
                relevant = list(relevant_by_id.values())
                if not relevant:
                    continue
                action_id = min(
                    row.user_action_id for row in relevant if row.user_action_id
                )
                created_at = max(row.created_at for row in relevant)
                tombstone = ReusableMemoryRevision.from_dict(
                    {
                        **memory.to_dict(),
                        "revision": memory.revision + 1,
                        "status": "tombstone",
                        "operation": "tombstone",
                        "created_at": created_at,
                        "provenance": {
                            **dict(memory.provenance),
                            "origin": "feedback_recompute",
                            "user_action_id": action_id,
                        },
                        "previous_revision_sha256": memory.sha256,
                    }
                )
                self._assert_revision_transition(
                    ref, tombstone, daily_commit=False
                )
                memory_tombstones.append(tombstone)
                retracted_memory_actions[memory.memory_id] = action_id

            relation_tombstones: list[RelationRevision] = []
            for ref in self._catalog_map(catalog, "relations").values():
                value, _ = self._load_revision_object(ref)
                relation = RelationRevision.from_dict(value)
                if relation.status != "active":
                    continue
                related_receipts = {
                    terminal_by_record[span.record_id].receipt_id: (
                        terminal_by_record[span.record_id]
                    )
                    for span in relation.source_spans
                    if span.record_id in terminal_by_record
                }
                related_actions = {
                    retracted_memory_actions[endpoint.id]
                    for endpoint in (relation.from_ref, relation.to_ref)
                    if endpoint.kind == "reusable_memory"
                    and endpoint.id in retracted_memory_actions
                }
                related_actions.update(
                    row.user_action_id
                    for row in related_receipts.values()
                    if row.user_action_id
                )
                if not related_actions:
                    continue
                action_id = min(related_actions)
                relevant_dates = [
                    row.created_at for row in related_receipts.values()
                ]
                created_at = max(relevant_dates) if relevant_dates else max(
                    row.created_at for row in terminal
                )
                tombstone = RelationRevision.from_dict(
                    {
                        **relation.to_dict(),
                        "revision": relation.revision + 1,
                        "status": "tombstone",
                        "operation": "tombstone",
                        "created_at": created_at,
                        "provenance": {
                            **dict(relation.provenance),
                            "origin": "feedback_recompute",
                            "user_action_id": action_id,
                        },
                        "previous_revision_sha256": relation.sha256,
                    }
                )
                self._assert_revision_transition(
                    ref, tombstone, daily_commit=False
                )
                relation_tombstones.append(tombstone)

            if not memory_tombstones and not relation_tombstones:
                return TerminalRetractionResult("no_change", (), ())

            memory_refs = tuple(
                self._formal_ref(row)
                for row in sorted(
                    memory_tombstones, key=lambda row: row.memory_id
                )
            )
            relation_refs = tuple(
                self._formal_ref(row)
                for row in sorted(
                    relation_tombstones, key=lambda row: row.relation_id
                )
            )
            for row in memory_tombstones:
                self._safe_write_immutable(
                    self._revision_path(
                        "reusable_memory", row.memory_id, row.revision
                    ),
                    row.to_dict(),
                )
            for row in relation_tombstones:
                self._safe_write_immutable(
                    self._revision_path(
                        "relation", row.relation_id, row.revision
                    ),
                    row.to_dict(),
                )
            self._fault("after_terminal_retraction_revisions")

            generated_at = max(row.created_at for row in terminal)
            target = json.loads(json.dumps(catalog))
            target["revision"] = catalog["revision"] + 1
            target["generated_at"] = generated_at
            for ref in memory_refs:
                self._replace_catalog_ref(
                    target, "reusable_memories", ref
                )
            for ref in relation_refs:
                self._replace_catalog_ref(target, "relations", ref)
            target = self._validate_catalog(target)
            self._safe_write_replace(self.catalog_path, target)
            self._fault("after_terminal_retraction_catalog_switch")
            self._validate_catalog_targets(target)

            audit = {
                "schema_version": COGNITIVE_SCHEMA_VERSION,
                "kind": "memento_terminal_receipt_retraction",
                "created_at": generated_at,
                "base_catalog_sha256": catalog_sha,
                "terminal_receipt_refs": [
                    ObjectRef(
                        "interpretation_receipt",
                        row.receipt_id,
                        row.revision,
                        row.sha256,
                    ).to_dict()
                    for row in terminal
                ],
                "memory_refs": [ref.to_dict() for ref in memory_refs],
                "relation_refs": [ref.to_dict() for ref in relation_refs],
            }
            audit_id = sha256_bytes(
                canonical_json(audit).encode("utf-8")
            )[:24]
            self._safe_write_immutable(
                self.feedback_journal_dir
                / f"terminal-retraction-{audit_id}.json",
                audit,
            )
            return TerminalRetractionResult(
                "applied", memory_refs, relation_refs
            )

    def find_user_action_materialization(
        self,
        kind: str,
        identifier: str,
        action_id: str,
    ) -> ObjectRef | None:
        """Return only a catalogue-visible revision created by ``action_id``.

        An immutable revision file left behind by a crash before the catalogue
        CAS is intentionally invisible here.  The action worker may safely
        retry the same materialization bytes.
        """

        if not ACTION_RE.fullmatch(action_id):
            raise ContractError("action_id 无效")
        if kind == "reusable_memory":
            if not MEMORY_RE.fullmatch(identifier):
                raise ContractError("memory_id 无效")
            key = "reusable_memories"
        elif kind == "relation":
            if not RELATION_RE.fullmatch(identifier):
                raise ContractError("relation_id 无效")
            key = "relations"
        else:
            raise ContractError("action materialization kind 无效")
        with _BundleLock(self):
            self._recover_staging_locked()
            catalog, _ = self._load_catalog_unchecked()
            ref = self._catalog_map(catalog, key).get(identifier)
            if ref is None:
                return None
            value, _ = self._load_revision_object(ref)
            if value.get("provenance", {}).get("user_action_id") != action_id:
                return None
            return ref

    # ------------------------------------------------------------------
    # Public verified readers
    # ------------------------------------------------------------------
    def load_catalog(self) -> dict[str, Any]:
        with _BundleLock(self):
            self._recover_staging_locked()
            catalog, _ = self._load_catalog_unchecked()
            self._validate_catalog_targets(catalog)
            return catalog

    def load_day_bundle_ref(self, local_date: str) -> ObjectRef | None:
        with _BundleLock(self):
            self._recover_staging_locked()
            catalog, _ = self._load_catalog_unchecked()
            self._validate_catalog_targets(catalog)
            return self._current_bundle_ref(catalog, _date(local_date))

    def load_day_manifest(self, local_date: str) -> dict[str, Any] | None:
        with _BundleLock(self):
            self._recover_staging_locked()
            catalog, _ = self._load_catalog_unchecked()
            self._validate_catalog_targets(catalog)
            ref = self._current_bundle_ref(catalog, _date(local_date))
            return None if ref is None else self._load_bundle_manifest_by_ref(ref)

    def load_daily_summary_head(
        self,
        local_date: str | None = None,
        *,
        summary_id: str | None = None,
    ) -> tuple[DailySummaryRevision, ObjectRef] | None:
        """Return the catalogue-visible Daily Summary head and its exact ref.

        ``local_date`` is the preferred lookup key.  ``summary_id`` exists for
        callers that already hold the contract identifier; when both are
        supplied they must identify the same day.  Revision directories are
        never searched to infer a head: absence from the verified catalogue is
        absence from the public store.
        """

        if local_date is not None:
            identifier = make_daily_summary_id(_date(local_date))
            if summary_id is not None and summary_id != identifier:
                raise ContractError("local_date 与 summary_id 不一致", kind="evidence")
        elif summary_id is not None:
            # Reuse the formal path contract solely for identifier validation;
            # the path itself is not read until catalogue resolution succeeds.
            self._revision_path("daily_summary", summary_id, 1)
            identifier = summary_id
        else:
            raise ContractError("必须提供 local_date 或 summary_id")

        with _BundleLock(self):
            self._recover_staging_locked()
            catalog, _ = self._load_catalog_unchecked()
            self._validate_catalog_targets(catalog)
            ref = self._catalog_map(catalog, "daily_summaries").get(identifier)
            if ref is None:
                return None
            value, _ = self._load_revision_object(ref)
            summary = DailySummaryRevision.from_dict(value)
            if self._formal_ref(summary) != ref:
                raise ContractError("Daily Summary head 与 catalogue ref 不一致", kind="evidence")
            return summary, ref

    def load_memory_head(self, memory_id: str) -> ReusableMemoryRevision:
        with _BundleLock(self):
            self._recover_staging_locked()
            catalog, _ = self._load_catalog_unchecked()
            ref = self._catalog_map(catalog, "reusable_memories").get(memory_id)
            if ref is None:
                raise ContractError("可用记忆不存在", kind="not_found")
            value, _ = self._load_revision_object(ref)
            return ReusableMemoryRevision.from_dict(value)

    def load_relation_head(self, relation_id: str) -> RelationRevision:
        with _BundleLock(self):
            self._recover_staging_locked()
            catalog, _ = self._load_catalog_unchecked()
            ref = self._catalog_map(catalog, "relations").get(relation_id)
            if ref is None:
                raise ContractError("正式关系不存在", kind="not_found")
            value, _ = self._load_revision_object(ref)
            return RelationRevision.from_dict(value)

    def list_active_memories(self) -> tuple[ReusableMemoryRevision, ...]:
        with _BundleLock(self):
            self._recover_staging_locked()
            catalog, _ = self._load_catalog_unchecked()
            self._validate_catalog_targets(catalog)
            result = []
            for ref in self._catalog_map(catalog, "reusable_memories").values():
                value, _ = self._load_revision_object(ref)
                memory = ReusableMemoryRevision.from_dict(value)
                if memory.status == "active":
                    result.append(memory)
            return tuple(sorted(result, key=lambda row: row.memory_id))

    def list_active_relations(self) -> tuple[RelationRevision, ...]:
        with _BundleLock(self):
            self._recover_staging_locked()
            catalog, _ = self._load_catalog_unchecked()
            self._validate_catalog_targets(catalog)
            memories: dict[str, ObjectRef] = {}
            for ref in self._catalog_map(catalog, "reusable_memories").values():
                value, _ = self._load_revision_object(ref)
                memory = ReusableMemoryRevision.from_dict(value)
                if memory.status == "active":
                    memories[memory.memory_id] = ref
            result = []
            for ref in self._catalog_map(catalog, "relations").values():
                value, _ = self._load_revision_object(ref)
                relation = RelationRevision.from_dict(value)
                if relation.status != "active":
                    continue
                valid = True
                for endpoint in (relation.from_ref, relation.to_ref):
                    if endpoint.kind == "reusable_memory" and memories.get(endpoint.id) != endpoint:
                        valid = False
                    elif endpoint.kind == "understanding":
                        try:
                            self._assert_current_external_ref(endpoint)
                        except ContractError:
                            valid = False
                if valid:
                    result.append(relation)
            return tuple(sorted(result, key=lambda row: row.relation_id))


__all__ = [
    "BundleCommitResult",
    "CognitiveBundleStore",
    "TerminalRetractionResult",
    "UserRevisionResult",
]
