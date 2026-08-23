"""Durable user-action inbox and trusted materializer for Cognitive Secretary V1.

The browser may append immutable ``CognitiveUserAction`` files, but it never
writes receipt, reusable-memory, or relation revisions directly.  A trusted
worker reads this inbox, performs exact ``ObjectRef`` compare-and-swap through
the owning revision stores, then appends one immutable terminal
``CognitiveActionResult``.

This module deliberately does not know how daily bundles are generated.  It
depends on the narrow persistence APIs owned by the receipt and formal-object
stores:

* ``load_receipt_head`` / ``commit_user_receipt_revision``;
* ``load_memory_head`` / ``commit_user_memory_revision``;
* ``load_relation_head`` / ``commit_user_relation_revision``.

Those stores remain responsible for revision-chain validation and visibility
indexes.  The action inbox remains responsible for safe immutable files,
terminal-action priority, retry idempotency, and exact result reporting.
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
from typing import Any, Mapping, Protocol, Sequence

from cognitive_v1 import (
    COGNITIVE_ACTION_RE,
    COGNITIVE_SCHEMA_VERSION,
    CognitiveActionResult,
    CognitiveUserAction,
    InterpretationReceiptRevision,
    ObjectRef,
    RelationRevision,
    ReusableMemoryRevision,
    make_cognitive_action_result_id,
    persisted_json_bytes,
    validate_interpretation_receipt_transition,
)
from core import ContractError, canonical_json, sha256_bytes


MAX_ACTION_BYTES = 1024 * 1024
ACTION_FILE_RE = re.compile(r"^(cact_[0-9a-f]{24})\.json$")
ACTION_TEMP_FILE_RE = re.compile(
    r"^\.memento-cact_[0-9a-f]{24}\.json-[A-Za-z0-9-]{1,96}\.tmp$"
)
RESULT_FILE_RE = re.compile(r"^(cares_[0-9a-f]{24})\.json$")
RECEIPT_REVISION_FILE_RE = re.compile(
    r"^(rcp_[0-9a-f]{24})\.r([0-9]{6})\.json$"
)
TERMINAL_ACTIONS = frozenset(
    {"original_only", "delete_reusable_memory", "delete_relation"}
)


class ReceiptRevisionBackend(Protocol):
    def load_receipt_head(self, receipt_id: str) -> Any: ...

    def commit_user_receipt_revision(
        self, value: Any, *, expected_ref: ObjectRef
    ) -> Any: ...


class FormalRevisionBackend(Protocol):
    def load_memory_head(self, memory_id: str) -> Any: ...

    def commit_user_memory_revision(
        self, value: Any, *, expected_ref: ObjectRef
    ) -> Any: ...

    def load_relation_head(self, relation_id: str) -> Any: ...

    def commit_user_relation_revision(
        self, value: Any, *, expected_ref: ObjectRef
    ) -> Any: ...


@dataclass(frozen=True)
class StoredActionRef:
    action_id: str
    sha256: str


@dataclass(frozen=True)
class ActionReconcileReport:
    seen: int = 0
    already_resolved: int = 0
    applied: int = 0
    rejected: int = 0
    conflict: int = 0

    @property
    def processed(self) -> int:
        return self.applied + self.rejected + self.conflict


@dataclass(frozen=True)
class _ActionEnvelope:
    action_id: str
    raw: bytes
    sha256: str
    action: CognitiveUserAction | None
    validation_error_kind: str | None


class _RejectedAction(Exception):
    def __init__(self, error_kind: str) -> None:
        super().__init__(error_kind)
        self.error_kind = error_kind


class _ConflictingAction(Exception):
    pass


def _now_text(now: dt.datetime | None) -> str:
    value = now or dt.datetime.now().astimezone()
    if value.tzinfo is None:
        raise ContractError("now 必须带时区", kind="runtime")
    return value.isoformat(timespec="seconds")


def _coerce_receipt(value: Any) -> InterpretationReceiptRevision:
    if isinstance(value, InterpretationReceiptRevision):
        return value
    if isinstance(value, Mapping):
        return InterpretationReceiptRevision.from_dict(value)
    raise ContractError("receipt backend 返回了非法 head", kind="evidence")


def _coerce_memory(value: Any) -> ReusableMemoryRevision:
    if isinstance(value, ReusableMemoryRevision):
        return value
    if isinstance(value, Mapping):
        return ReusableMemoryRevision.from_dict(value)
    raise ContractError("formal backend 返回了非法 memory head", kind="evidence")


def _coerce_relation(value: Any) -> RelationRevision:
    if isinstance(value, RelationRevision):
        return value
    if isinstance(value, Mapping):
        return RelationRevision.from_dict(value)
    raise ContractError("formal backend 返回了非法 relation head", kind="evidence")


def _revision_ref(value: Any) -> ObjectRef:
    if isinstance(value, ObjectRef):
        return value
    object_ref = getattr(value, "object_ref", None)
    if object_ref is not None:
        return _revision_ref(object_ref)
    if isinstance(value, Mapping) and frozenset(value) == frozenset(
        {"kind", "id", "revision", "revision_sha256"}
    ):
        return ObjectRef.from_dict(value)
    if isinstance(value, InterpretationReceiptRevision):
        return ObjectRef(
            "interpretation_receipt", value.receipt_id, value.revision, value.sha256
        )
    if isinstance(value, ReusableMemoryRevision):
        return ObjectRef("reusable_memory", value.memory_id, value.revision, value.sha256)
    if isinstance(value, RelationRevision):
        return ObjectRef("relation", value.relation_id, value.revision, value.sha256)
    if isinstance(value, Mapping):
        kind = value.get("kind")
        if kind == "memento_interpretation_receipt_revision":
            return _revision_ref(InterpretationReceiptRevision.from_dict(value))
        if kind == "memento_reusable_memory_revision":
            return _revision_ref(ReusableMemoryRevision.from_dict(value))
        if kind == "memento_relation_revision":
            return _revision_ref(RelationRevision.from_dict(value))
    raise ContractError("backend 没有返回可验证的 ObjectRef", kind="evidence")


def _same_ref(left: ObjectRef, right: ObjectRef) -> bool:
    return left.to_dict() == right.to_dict()


def _revision_action_id(value: Any) -> str | None:
    if isinstance(value, InterpretationReceiptRevision):
        return value.user_action_id
    if isinstance(value, (ReusableMemoryRevision, RelationRevision)):
        raw = value.provenance.get("user_action_id")
        return raw if isinstance(raw, str) else None
    return None


class _ActionLock:
    def __init__(self, store: "CognitiveActionStore") -> None:
        self.store = store
        self.descriptor: int | None = None

    def __enter__(self) -> "_ActionLock":
        self.store._ensure_layout()
        path = self.store.locks_dir / "cognitive-actions.lock"
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise ContractError("用户动作锁无法安全打开", kind="evidence") from exc
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.getuid()
            or details.st_nlink != 1
            or stat.S_IMODE(details.st_mode) & 0o077
        ):
            os.close(descriptor)
            raise ContractError(
                "用户动作锁必须是 owner-only 的单链接普通文件",
                kind="evidence",
            )
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        current = path.lstat()
        locked = os.fstat(descriptor)
        if (
            current.st_dev != locked.st_dev
            or current.st_ino != locked.st_ino
            or current.st_nlink != 1
            or current.st_uid != os.getuid()
            or stat.S_IMODE(current.st_mode) & 0o077
        ):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            raise ContractError("用户动作锁在等待期间发生变化", kind="evidence")
        self.descriptor = descriptor
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.descriptor is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


class _ReceiptLock:
    def __init__(self, store: "CognitiveActionStore", receipt_id: str) -> None:
        self.store = store
        self.receipt_id = receipt_id
        self.descriptor: int | None = None

    def __enter__(self) -> "_ReceiptLock":
        self.store._ensure_layout()
        if not re.fullmatch(r"rcp_[0-9a-f]{24}", self.receipt_id):
            raise ContractError("receipt id 无效", kind="evidence")
        path = self.store.locks_dir / f"receipt-{self.receipt_id}.lock"
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise ContractError("receipt 锁无法安全打开", kind="evidence") from exc
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.getuid()
            or details.st_nlink != 1
            or stat.S_IMODE(details.st_mode) & 0o077
        ):
            os.close(descriptor)
            raise ContractError(
                "receipt 锁必须是 owner-only 的单链接普通文件",
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
            raise ContractError("receipt 锁在等待期间发生变化", kind="evidence")
        self.descriptor = descriptor
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.descriptor is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


class CognitiveActionStore:
    """Append-only action/result store with trusted materialization helpers."""

    def __init__(self, vault: Path, *, state_root: Path | None = None) -> None:
        try:
            resolved_vault = vault.expanduser().resolve(strict=True)
        except OSError as exc:
            raise ContractError("Vault 目录不存在", kind="not_found") from exc
        if not resolved_vault.is_dir():
            raise ContractError("Vault 必须是目录", kind="not_found")
        self.vault = resolved_vault
        raw_root = state_root or (
            resolved_vault / ".context-agent" / "cognitive-secretary-v1"
        )
        if not raw_root.is_absolute():
            raw_root = resolved_vault / raw_root
        candidate = Path(os.path.abspath(os.fspath(raw_root.expanduser())))
        try:
            candidate.relative_to(resolved_vault)
        except ValueError as exc:
            raise ContractError("state_root 必须位于 Vault 内", kind="evidence") from exc
        self.root = candidate
        self.actions_dir = self.root / "user-actions"
        self.results_dir = self.root / "action-results"
        self.receipts_dir = self.root / "receipts"
        self.locks_dir = self.root / "locks"

    def _ensure_layout(self) -> None:
        relative = self.root.relative_to(self.vault)
        current = self.vault
        for component in relative.parts:
            current = current / component
            self._secure_directory(current)
        for path in (
            self.actions_dir,
            self.results_dir,
            self.receipts_dir,
            self.locks_dir,
        ):
            self._secure_directory(path)

    def _secure_directory(self, path: Path) -> None:
        try:
            path.relative_to(self.vault)
        except ValueError as exc:
            raise ContractError("运行目录越过 Vault 边界", kind="evidence") from exc
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ContractError("运行目录无法创建", kind="runtime") from exc
        try:
            details = path.lstat()
        except OSError as exc:
            raise ContractError("运行目录无法校验", kind="evidence") from exc
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) & 0o077
        ):
            raise ContractError(
                f"运行目录必须是 owner-only 的安全目录：{path.name}",
                kind="evidence",
            )

    def _fsync_directory(self, path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            with contextlib.suppress(OSError):
                os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _safe_read_bytes(self, path: Path, *, name: str) -> bytes:
        self._secure_directory(path.parent)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError as exc:
            raise ContractError(f"{name} 不存在", kind="not_found") from exc
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
                or stat.S_IMODE(before.st_mode) & 0o077
                or before.st_size > MAX_ACTION_BYTES
                or current.st_dev != before.st_dev
                or current.st_ino != before.st_ino
            ):
                raise ContractError(
                    f"{name} 必须是 owner-only 的单链接普通文件",
                    kind="evidence",
                )
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(256 * 1024, MAX_ACTION_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_ACTION_BYTES:
                    raise ContractError(f"{name} 超过允许大小", kind="evidence")
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
            if any(getattr(before, field) != getattr(after, field) for field in stable):
                raise ContractError(f"{name} 在读取期间发生变化", kind="stale")
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def _safe_write_immutable(self, path: Path, payload: bytes, *, name: str) -> None:
        self._secure_directory(path.parent)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError as exc:
                existing = self._safe_read_bytes(path, name=name)
                if existing != payload:
                    raise ContractError(
                        f"拒绝覆盖已有不可变{name}：{path.name}",
                        kind="conflict",
                    ) from exc
            self._fsync_directory(path.parent)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()

    def _action_path(self, action_id: str) -> Path:
        if not COGNITIVE_ACTION_RE.fullmatch(action_id):
            raise ContractError("cognitive action id 无效", kind="action")
        return self.actions_dir / f"{action_id}.json"

    def _result_path(self, action_id: str) -> Path:
        result_id = make_cognitive_action_result_id(action_id)
        return self.results_dir / f"{result_id}.json"

    def submit_action(
        self, action: CognitiveUserAction | Mapping[str, Any]
    ) -> StoredActionRef:
        item = action if isinstance(action, CognitiveUserAction) else CognitiveUserAction.from_dict(action)
        payload = persisted_json_bytes(item)
        with _ActionLock(self):
            self._safe_write_immutable(
                self._action_path(item.id), payload, name="cognitive user action"
            )
        return StoredActionRef(item.id, sha256_bytes(payload))

    def _receipt_revision_path(self, receipt_id: str, revision: int) -> Path:
        if not re.fullmatch(r"rcp_[0-9a-f]{24}", receipt_id):
            raise ContractError("receipt id 无效", kind="evidence")
        if type(revision) is not int or not 1 <= revision <= 999_999:
            raise ContractError("receipt revision 无效", kind="evidence")
        return self.receipts_dir / f"{receipt_id}.r{revision:06d}.json"

    def _load_receipt_chain_unlocked(
        self, receipt_id: str
    ) -> list[tuple[InterpretationReceiptRevision, str]]:
        self._ensure_layout()
        rows: list[tuple[int, Path]] = []
        for path in self.receipts_dir.iterdir():
            match = RECEIPT_REVISION_FILE_RE.fullmatch(path.name)
            if match is None or match.group(1) != receipt_id:
                continue
            rows.append((int(match.group(2)), path))
        rows.sort(key=lambda row: row[0])
        if not rows:
            raise ContractError("receipt 不存在", kind="not_found")
        if [number for number, _ in rows] != list(range(1, len(rows) + 1)):
            raise ContractError("receipt revision 链不连续", kind="evidence")
        chain: list[tuple[InterpretationReceiptRevision, str]] = []
        previous_sha: str | None = None
        previous: InterpretationReceiptRevision | None = None
        for number, path in rows:
            raw = self._safe_read_bytes(path, name="interpretation receipt revision")
            try:
                revision = InterpretationReceiptRevision.from_dict(
                    json.loads(raw.decode("utf-8"))
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ContractError) as exc:
                raise ContractError("receipt revision 损坏", kind="evidence") from exc
            if raw != persisted_json_bytes(revision):
                raise ContractError("receipt revision 字节不符合持久化合同", kind="evidence")
            digest = sha256_bytes(raw)
            if (
                revision.receipt_id != receipt_id
                or revision.revision != number
                or revision.previous_revision_sha256 != previous_sha
            ):
                raise ContractError("receipt revision 文件名或链无效", kind="evidence")
            if previous is not None:
                try:
                    validate_interpretation_receipt_transition(previous, revision)
                except ContractError as exc:
                    raise ContractError("receipt revision 迁移无效", kind="evidence") from exc
            chain.append((revision, digest))
            previous = revision
            previous_sha = digest
        return chain

    def load_receipt_head(self, receipt_id: str) -> InterpretationReceiptRevision:
        with _ReceiptLock(self, receipt_id):
            return self._load_receipt_chain_unlocked(receipt_id)[-1][0]

    def load_receipt_head_ref(self, receipt_id: str) -> ObjectRef:
        with _ReceiptLock(self, receipt_id):
            revision, digest = self._load_receipt_chain_unlocked(receipt_id)[-1]
            return ObjectRef(
                "interpretation_receipt",
                revision.receipt_id,
                revision.revision,
                digest,
            )

    def list_receipt_heads(
        self,
        *,
        statuses: Sequence[str] | None = None,
    ) -> tuple[tuple[InterpretationReceiptRevision, ObjectRef], ...]:
        """Return every validated receipt head through the public store API.

        The long-term Agent authorization boundary uses this snapshot instead
        of inspecting receipt files directly.  A caller may restrict the
        result to active receipt statuses, but unknown status values fail
        closed rather than silently producing an incomplete authorization
        list.
        """

        allowed_statuses = frozenset(
            {"ready", "needs_review", "original_only", "tombstone"}
            if statuses is None
            else statuses
        )
        if not allowed_statuses or not allowed_statuses <= frozenset(
            {"ready", "needs_review", "original_only", "tombstone"}
        ):
            raise ContractError("receipt statuses 无效", kind="evidence")
        with _ActionLock(self):
            self._ensure_layout()
            receipt_ids = sorted(
                {
                    match.group(1)
                    for path in self.receipts_dir.iterdir()
                    if (match := RECEIPT_REVISION_FILE_RE.fullmatch(path.name))
                    is not None
                }
            )
            rows: list[tuple[InterpretationReceiptRevision, ObjectRef]] = []
            for receipt_id in receipt_ids:
                revision, digest = self._load_receipt_chain_unlocked(receipt_id)[-1]
                if revision.status not in allowed_statuses:
                    continue
                rows.append(
                    (
                        revision,
                        ObjectRef(
                            "interpretation_receipt",
                            revision.receipt_id,
                            revision.revision,
                            digest,
                        ),
                    )
                )
            return tuple(rows)

    def commit_user_receipt_revision(
        self,
        value: InterpretationReceiptRevision | Mapping[str, Any],
        *,
        expected_ref: ObjectRef,
    ) -> ObjectRef:
        proposal = _coerce_receipt(value)
        if expected_ref.kind != "interpretation_receipt":
            raise ContractError("expected_ref 不是 receipt", kind="conflict")
        if proposal.receipt_id != expected_ref.id:
            raise ContractError("receipt proposal 与 expected_ref 身份不一致", kind="conflict")
        with _ReceiptLock(self, proposal.receipt_id):
            head, head_sha = self._load_receipt_chain_unlocked(proposal.receipt_id)[-1]
            current_ref = ObjectRef(
                "interpretation_receipt",
                head.receipt_id,
                head.revision,
                head_sha,
            )
            if not _same_ref(current_ref, expected_ref):
                if _revision_action_id(head) == proposal.user_action_id:
                    return current_ref
                raise ContractError("receipt base revision/hash 已变化", kind="conflict")
            if head.status in {"original_only", "tombstone"}:
                raise ContractError("receipt 终态不得复活", kind="conflict")
            if (
                proposal.revision != head.revision + 1
                or proposal.previous_revision_sha256 != head_sha
                or proposal.record_ref != head.record_ref
                or proposal.operation
                not in {"user_confirm", "user_edit", "original_only", "tombstone"}
                or proposal.user_action_id is None
            ):
                raise ContractError("receipt user revision 不符合 CAS 合同", kind="conflict")
            path = self._receipt_revision_path(proposal.receipt_id, proposal.revision)
            self._safe_write_immutable(
                path,
                persisted_json_bytes(proposal),
                name="interpretation receipt revision",
            )
            stored = self._safe_read_bytes(path, name="interpretation receipt revision")
            if stored != persisted_json_bytes(proposal):
                raise ContractError("receipt commit 字节不一致", kind="evidence")
            return ObjectRef(
                "interpretation_receipt",
                proposal.receipt_id,
                proposal.revision,
                sha256_bytes(stored),
            )

    def _path_exists_securely(self, path: Path, *, name: str) -> bool:
        try:
            details = path.lstat()
        except FileNotFoundError:
            return False
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.getuid()
            or details.st_nlink != 1
            or stat.S_IMODE(details.st_mode) & 0o077
        ):
            raise ContractError(f"{name} 路径不安全", kind="evidence")
        return True

    def _load_result_for_hash(
        self, action_id: str, action_sha256: str
    ) -> CognitiveActionResult | None:
        path = self._result_path(action_id)
        if not self._path_exists_securely(path, name="cognitive action result"):
            return None
        raw = self._safe_read_bytes(path, name="cognitive action result")
        try:
            result = CognitiveActionResult.from_dict(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, ContractError) as exc:
            raise ContractError("cognitive action result 损坏", kind="evidence") from exc
        if result.action_id != action_id or result.action_sha256 != action_sha256:
            raise ContractError("action result 与 action 字节不一致", kind="evidence")
        return result

    def load_result(self, action_id: str) -> CognitiveActionResult | None:
        with _ActionLock(self):
            action_raw = self._safe_read_bytes(
                self._action_path(action_id), name="cognitive user action"
            )
            return self._load_result_for_hash(action_id, sha256_bytes(action_raw))

    def _load_action_envelopes(self) -> list[_ActionEnvelope]:
        self._ensure_layout()
        envelopes: list[_ActionEnvelope] = []
        for path in sorted(self.actions_dir.iterdir(), key=lambda item: item.name):
            # The browser validates an explicit hidden staging file before it
            # publishes the immutable action. WatchPaths may wake this worker
            # while that staging file still exists, or cleanup may fail after
            # the final action is durable. Ignore only this exact writer-owned
            # namespace; every other unexpected filename remains fail-closed.
            if ACTION_TEMP_FILE_RE.fullmatch(path.name):
                continue
            match = ACTION_FILE_RE.fullmatch(path.name)
            if match is None:
                raise ContractError("user-actions 包含非法文件名", kind="evidence")
            action_id = match.group(1)
            raw = self._safe_read_bytes(path, name="cognitive user action")
            action: CognitiveUserAction | None = None
            error_kind: str | None = None
            try:
                parsed = json.loads(raw.decode("utf-8"))
                action = CognitiveUserAction.from_dict(parsed)
                if action.id != action_id:
                    raise ContractError("action id 与文件名不一致", kind="action")
            except ContractError as exc:
                action = None
                error_kind = "action" if exc.kind == "action" else "schema"
            except (UnicodeDecodeError, json.JSONDecodeError):
                action = None
                error_kind = "schema"
            envelopes.append(
                _ActionEnvelope(
                    action_id=action_id,
                    raw=raw,
                    sha256=sha256_bytes(raw),
                    action=action,
                    validation_error_kind=error_kind,
                )
            )
        return envelopes

    def action_watermark(self) -> tuple[tuple[StoredActionRef, ...], str]:
        with _ActionLock(self):
            return self._action_watermark_unlocked()

    def _action_watermark_unlocked(
        self,
    ) -> tuple[tuple[StoredActionRef, ...], str]:
        envelopes = self._load_action_envelopes()
        refs = tuple(StoredActionRef(item.action_id, item.sha256) for item in envelopes)
        digest = sha256_bytes(
            canonical_json(
                [{"id": item.action_id, "sha256": item.sha256} for item in refs]
            ).encode("utf-8")
        )
        return refs, digest

    @contextlib.contextmanager
    def guard_action_watermark(self, expected_sha256: str):
        """Keep immutable user actions fixed across a trusted commit.

        The guard shares the same lock as ``submit_action``.  A caller may
        therefore validate a frozen watermark and publish its derived object
        while a newly submitted user action waits; an action that already
        exists makes the derived commit stale before it becomes visible.
        """

        if not isinstance(expected_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_sha256
        ):
            raise ContractError("action watermark sha256 无效", kind="evidence")
        with _ActionLock(self):
            _, current_sha256 = self._action_watermark_unlocked()
            if current_sha256 != expected_sha256:
                raise ContractError("用户动作 watermark 已变化", kind="stale")
            yield

    def _write_result(
        self,
        *,
        action_id: str,
        action_sha256: str,
        status: str,
        completed_at: str,
        materialized_refs: Sequence[ObjectRef] = (),
        error_kind: str | None = None,
    ) -> CognitiveActionResult:
        result = CognitiveActionResult(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_cognitive_action_result",
            id=make_cognitive_action_result_id(action_id),
            action_id=action_id,
            action_sha256=action_sha256,
            status=status,
            completed_at=completed_at,
            materialized_refs=tuple(materialized_refs),
            error_kind=error_kind,
        )
        with _ActionLock(self):
            existing = self._load_result_for_hash(action_id, action_sha256)
            if existing is not None:
                comparable_existing = existing.to_dict()
                comparable_result = result.to_dict()
                comparable_existing.pop("completed_at")
                comparable_result.pop("completed_at")
                if comparable_existing != comparable_result:
                    raise ContractError(
                        "同一 action 已有不同的 terminal result", kind="conflict"
                    )
                return existing
            self._safe_write_immutable(
                self._result_path(action_id),
                persisted_json_bytes(result),
                name="cognitive action result",
            )
        return result

    def _load_target(
        self,
        action: CognitiveUserAction,
        receipt_store: ReceiptRevisionBackend | None,
        formal_store: FormalRevisionBackend | None,
    ) -> tuple[Any, ObjectRef, Any]:
        target = action.target_ref
        try:
            if target.kind == "interpretation_receipt":
                receipt_store = receipt_store or self
                head = _coerce_receipt(receipt_store.load_receipt_head(target.id))
                return head, _revision_ref(head), receipt_store
            if target.kind == "reusable_memory":
                if formal_store is None:
                    raise ContractError("formal backend 未配置", kind="runtime")
                head = _coerce_memory(formal_store.load_memory_head(target.id))
                return head, _revision_ref(head), formal_store
            if target.kind == "relation":
                if formal_store is None:
                    raise ContractError("formal backend 未配置", kind="runtime")
                head = _coerce_relation(formal_store.load_relation_head(target.id))
                return head, _revision_ref(head), formal_store
        except ContractError as exc:
            if exc.kind == "not_found":
                raise _RejectedAction("evidence") from exc
            raise
        raise _RejectedAction("action")

    def _existing_materialization(
        self,
        action: CognitiveUserAction,
        receipt_store: ReceiptRevisionBackend | None,
        formal_store: FormalRevisionBackend | None,
    ) -> ObjectRef | None:
        if action.target_ref.kind in {"reusable_memory", "relation"} and formal_store is not None:
            finder = getattr(formal_store, "find_user_action_materialization", None)
            if callable(finder):
                found = finder(action.target_ref.kind, action.target_ref.id, action.id)
                if found is not None:
                    return _revision_ref(found)
        try:
            head, ref, _ = self._load_target(action, receipt_store, formal_store)
        except _RejectedAction:
            return None
        if _revision_action_id(head) == action.id:
            return ref
        return None

    def _receipt_proposal(
        self, head: InterpretationReceiptRevision, action: CognitiveUserAction
    ) -> InterpretationReceiptRevision:
        if head.status in {"original_only", "tombstone"}:
            raise _RejectedAction("action")
        raw = head.to_dict()
        raw.update(
            revision=head.revision + 1,
            created_at=action.created_at,
            user_action_id=action.id,
            previous_revision_sha256=action.target_ref.revision_sha256,
        )
        if action.action == "confirm_receipt":
            raw.update(status="ready", operation="user_confirm")
        elif action.action == "edit_receipt":
            raw.update(
                status="ready",
                operation="user_edit",
                summary=action.payload["summary"],  # type: ignore[index]
                facets=dict(action.payload["facets"]),  # type: ignore[index]
                memory_candidates=[],
                relation_candidates=[],
            )
        elif action.action == "original_only":
            raw.update(
                status="original_only",
                operation="original_only",
                summary=None,
                facets={},
                memory_candidates=[],
                relation_candidates=[],
                source_spans=[],
            )
        else:
            raise _RejectedAction("action")
        return InterpretationReceiptRevision.from_dict(raw)

    def _memory_proposal(
        self, head: ReusableMemoryRevision, action: CognitiveUserAction
    ) -> ReusableMemoryRevision | None:
        if action.action == "report_outcome":
            if head.status == "tombstone":
                raise _RejectedAction("action")
            return None
        if head.status == "tombstone":
            raise _RejectedAction("action")
        raw = head.to_dict()
        raw.update(
            revision=head.revision + 1,
            created_at=action.created_at,
            previous_revision_sha256=action.target_ref.revision_sha256,
            provenance={
                **dict(head.provenance),
                "origin": "user",
                "user_action_id": action.id,
            },
        )
        if action.action == "edit_reusable_memory":
            raw.update(
                status="active",
                operation="user_edit",
                statement=action.payload["statement"],  # type: ignore[index]
                topics=list(action.payload["topics"]),  # type: ignore[index]
                purposes=list(action.payload["purposes"]),  # type: ignore[index]
            )
        elif action.action == "delete_reusable_memory":
            raw.update(status="tombstone", operation="tombstone")
        else:
            raise _RejectedAction("action")
        return ReusableMemoryRevision.from_dict(raw)

    def _relation_proposal(
        self, head: RelationRevision, action: CognitiveUserAction
    ) -> RelationRevision:
        if head.status == "tombstone":
            raise _RejectedAction("action")
        raw = head.to_dict()
        raw.update(
            revision=head.revision + 1,
            created_at=action.created_at,
            previous_revision_sha256=action.target_ref.revision_sha256,
            provenance={
                **dict(head.provenance),
                "origin": "user",
                "user_action_id": action.id,
            },
        )
        if action.action == "edit_relation":
            relation_type = action.payload["type"]  # type: ignore[index]
            raw.update(
                status="active",
                operation="user_edit",
                type=relation_type,
                direction="undirected" if relation_type == "same_topic" else "directed",
                statement=action.payload["statement"],  # type: ignore[index]
            )
        elif action.action == "delete_relation":
            raw.update(status="tombstone", operation="tombstone")
        else:
            raise _RejectedAction("action")
        return RelationRevision.from_dict(raw)

    def _commit_proposal(
        self,
        action: CognitiveUserAction,
        head: Any,
        backend: Any,
    ) -> ObjectRef | None:
        if isinstance(head, InterpretationReceiptRevision):
            proposal = self._receipt_proposal(head, action)
            committed = backend.commit_user_receipt_revision(
                proposal, expected_ref=action.target_ref
            )
        elif isinstance(head, ReusableMemoryRevision):
            proposal = self._memory_proposal(head, action)
            if proposal is None:
                return None
            committed = backend.commit_user_memory_revision(
                proposal, expected_ref=action.target_ref
            )
        elif isinstance(head, RelationRevision):
            proposal = self._relation_proposal(head, action)
            committed = backend.commit_user_relation_revision(
                proposal, expected_ref=action.target_ref
            )
        else:
            raise ContractError("action target head 类型无效", kind="evidence")
        ref = _revision_ref(committed)
        expected = _revision_ref(proposal)
        if not _same_ref(ref, expected):
            raise ContractError("backend 提交结果与建议 revision 不一致", kind="evidence")
        return ref

    def _materialize(
        self,
        action: CognitiveUserAction,
        receipt_store: ReceiptRevisionBackend | None,
        formal_store: FormalRevisionBackend | None,
    ) -> tuple[ObjectRef, ...]:
        head, current_ref, backend = self._load_target(action, receipt_store, formal_store)
        if not _same_ref(current_ref, action.target_ref):
            recovered = self._existing_materialization(action, receipt_store, formal_store)
            if recovered is not None:
                return (recovered,)
            raise _ConflictingAction()
        committed = self._commit_proposal(action, head, backend)
        return () if committed is None else (committed,)

    def _process_envelope(
        self,
        envelope: _ActionEnvelope,
        *,
        receipt_store: ReceiptRevisionBackend | None,
        formal_store: FormalRevisionBackend | None,
        completed_at: str,
    ) -> CognitiveActionResult:
        with _ActionLock(self):
            existing = self._load_result_for_hash(envelope.action_id, envelope.sha256)
        if existing is not None:
            return existing
        if envelope.action is None:
            return self._write_result(
                action_id=envelope.action_id,
                action_sha256=envelope.sha256,
                status="rejected",
                completed_at=completed_at,
                error_kind=envelope.validation_error_kind or "schema",
            )
        try:
            refs = self._materialize(envelope.action, receipt_store, formal_store)
            return self._write_result(
                action_id=envelope.action_id,
                action_sha256=envelope.sha256,
                status="applied",
                completed_at=completed_at,
                materialized_refs=refs,
            )
        except _ConflictingAction:
            return self._write_result(
                action_id=envelope.action_id,
                action_sha256=envelope.sha256,
                status="conflict",
                completed_at=completed_at,
                error_kind="conflict",
            )
        except _RejectedAction as exc:
            return self._write_result(
                action_id=envelope.action_id,
                action_sha256=envelope.sha256,
                status="rejected",
                completed_at=completed_at,
                error_kind=exc.error_kind,
            )
        except ContractError as exc:
            if exc.kind in {"conflict", "stale"}:
                recovered = self._existing_materialization(
                    envelope.action, receipt_store, formal_store
                )
                if recovered is not None:
                    return self._write_result(
                        action_id=envelope.action_id,
                        action_sha256=envelope.sha256,
                        status="applied",
                        completed_at=completed_at,
                        materialized_refs=(recovered,),
                    )
                return self._write_result(
                    action_id=envelope.action_id,
                    action_sha256=envelope.sha256,
                    status="conflict",
                    completed_at=completed_at,
                    error_kind="conflict",
                )
            if exc.kind in {"schema", "action", "runtime", "not_found"}:
                error_kind = "evidence" if exc.kind == "not_found" else exc.kind
                return self._write_result(
                    action_id=envelope.action_id,
                    action_sha256=envelope.sha256,
                    status="rejected",
                    completed_at=completed_at,
                    error_kind=error_kind,
                )
            # Evidence failures can indicate a corrupt object store, unsafe
            # path, owner mismatch, or hard link.  Do not turn them into a
            # harmless-looking rejected UI action.
            raise

    def reconcile(
        self,
        *,
        receipt_store: ReceiptRevisionBackend | None = None,
        formal_store: FormalRevisionBackend | None = None,
        now: dt.datetime | None = None,
        limit: int | None = None,
    ) -> ActionReconcileReport:
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ContractError("limit 必须是正整数")
        completed_at = _now_text(now)
        with _ActionLock(self):
            envelopes = self._load_action_envelopes()
            resolved = {
                envelope.action_id
                for envelope in envelopes
                if self._load_result_for_hash(envelope.action_id, envelope.sha256)
                is not None
            }
        pending = [item for item in envelopes if item.action_id not in resolved]
        pending.sort(
            key=lambda item: (
                "" if item.action is None else item.action.target_ref.kind,
                item.action_id if item.action is None else item.action.target_ref.id,
                0 if item.action is not None and item.action.action in TERMINAL_ACTIONS else 1,
                "" if item.action is None else item.action.created_at,
                item.action_id,
            )
        )
        if limit is not None:
            pending = pending[:limit]
        statuses: list[str] = []
        for envelope in pending:
            result = self._process_envelope(
                envelope,
                receipt_store=receipt_store,
                formal_store=formal_store,
                completed_at=completed_at,
            )
            statuses.append(result.status)
        return ActionReconcileReport(
            seen=len(envelopes),
            already_resolved=len(resolved),
            applied=statuses.count("applied"),
            rejected=statuses.count("rejected"),
            conflict=statuses.count("conflict"),
        )


__all__ = [
    "ActionReconcileReport",
    "CognitiveActionStore",
    "FormalRevisionBackend",
    "ReceiptRevisionBackend",
    "StoredActionRef",
]
