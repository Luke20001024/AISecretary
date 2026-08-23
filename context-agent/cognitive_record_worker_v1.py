"""Bounded, per-record fast path for the Cognitive Secretary MVP.

The worker deliberately stops at interpretation receipts.  It does not run the
Daily Integrator, commit a daily bundle, create positive formal
memories/relations, or invoke the legacy Agent V1 path.  A terminal user action
is the exception: its already-derived formal objects are withdrawn in the same
bounded run so ``original_only`` is immediately authoritative.  A caller may
provide a small projection hook to republish Home with transient
``processing``/``failed`` record statuses.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import errno
import fcntl
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from cognitive_actions_v1 import ActionReconcileReport, CognitiveActionStore
from cognitive_bundle_store_v1 import CognitiveBundleStore
from cognitive_runtime_v1 import CognitiveRuntime, CompletionProvider
from cognitive_store_v1 import RecordStore, ReconcileResult
from cognitive_v1 import InterpretationReceiptRevision, ObjectRef, make_receipt_id
from core import ContractError


MAX_RECORDS_PER_RUN = 64
DEFAULT_RECORDS_PER_RUN = 8
RUNTIME_ERROR_KINDS = frozenset(
    {
        "provider_error",
        "unknown_attempt",
        "invalid_response",
        "budget_exhausted",
        "stale",
        "runtime",
    }
)


def _default_clock() -> dt.datetime:
    return dt.datetime.now().astimezone()


class HomeProjectionHook(Protocol):
    """Publish only the immediate Home view for the supplied record statuses."""

    def __call__(
        self,
        local_date: str,
        record_runtime_statuses: Mapping[str, Mapping[str, Any]],
    ) -> Any:
        ...


@dataclass(frozen=True)
class ActionSummary:
    seen: int
    already_resolved: int
    applied: int
    rejected: int
    conflict: int

    def to_dict(self) -> dict[str, int]:
        return {
            "seen": self.seen,
            "already_resolved": self.already_resolved,
            "applied": self.applied,
            "rejected": self.rejected,
            "conflict": self.conflict,
        }


@dataclass(frozen=True)
class ReconcileSummary:
    parsed_count: int
    created: int
    revised: int
    tombstoned: int
    unchanged: int
    needs_review: int

    def to_dict(self) -> dict[str, int]:
        return {
            "parsed_count": self.parsed_count,
            "created": self.created,
            "revised": self.revised,
            "tombstoned": self.tombstoned,
            "unchanged": self.unchanged,
            "needs_review": self.needs_review,
        }


@dataclass(frozen=True)
class RecordWorkItem:
    """A user-safe result; it intentionally contains no source/model text."""

    record_id: str
    record_revision: int
    outcome: str
    cached: bool = False
    receipt_ref: Mapping[str, Any] | None = None
    error_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "record_revision": self.record_revision,
            "outcome": self.outcome,
            "cached": self.cached,
            "receipt_ref": None if self.receipt_ref is None else dict(self.receipt_ref),
            "error_kind": self.error_kind,
        }


@dataclass(frozen=True)
class RecordWorkerResult:
    status: str
    local_date: str
    source_file: str
    selected_count: int
    deferred_count: int
    actions: ActionSummary
    reconcile: ReconcileSummary
    items: tuple[RecordWorkItem, ...]
    record_runtime_statuses: Mapping[str, Mapping[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "local_date": self.local_date,
            "source_file": self.source_file,
            "selected_count": self.selected_count,
            "deferred_count": self.deferred_count,
            "actions": self.actions.to_dict(),
            "reconcile": self.reconcile.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "record_runtime_statuses": {
                key: dict(value)
                for key, value in self.record_runtime_statuses.items()
            },
        }


class _DayLock:
    """One owner-only, single-link advisory lock per local day."""

    def __init__(self, *, vault: Path, root: Path, local_date: str) -> None:
        self.vault = vault
        self.root = root
        self.locks = root / "locks"
        self.path = self.locks / f"record-worker-{local_date}.lock"
        self.descriptor: int | None = None

    def _secure_directory(self, path: Path, *, create: bool) -> None:
        try:
            path.relative_to(self.vault)
        except ValueError as exc:
            raise ContractError("record worker 运行目录越过 Vault", kind="evidence") from exc
        if create:
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                pass
            except OSError as exc:
                raise ContractError("record worker 运行目录无法创建", kind="runtime") from exc
        try:
            details = path.lstat()
        except OSError as exc:
            raise ContractError("record worker 运行目录无法校验", kind="evidence") from exc
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) & 0o077
        ):
            raise ContractError("record worker 运行目录不安全", kind="evidence")

    def __enter__(self) -> "_DayLock":
        self._secure_directory(self.root, create=False)
        self._secure_directory(self.locks, create=True)
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as exc:
            kind = "evidence" if exc.errno in {errno.ELOOP, errno.EISDIR} else "runtime"
            raise ContractError("record worker 日锁无法安全打开", kind=kind) from exc
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) & 0o077
            ):
                raise ContractError("record worker 日锁不安全", kind="evidence")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            after = os.fstat(descriptor)
            try:
                visible = self.path.lstat()
            except OSError as exc:
                raise ContractError("record worker 日锁在等待期间变化", kind="evidence") from exc
            if (
                (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                or (after.st_dev, after.st_ino) != (visible.st_dev, visible.st_ino)
                or after.st_nlink != 1
                or stat.S_IMODE(after.st_mode) & 0o077
            ):
                raise ContractError("record worker 日锁在等待期间变化", kind="evidence")
        except BaseException:
            with contextlib.suppress(OSError):
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


def _validate_day(local_date: str, source_file: str) -> None:
    if not isinstance(local_date, str):
        raise ContractError("local_date 必须是 YYYY-MM-DD")
    try:
        parsed = dt.date.fromisoformat(local_date)
    except ValueError as exc:
        raise ContractError("local_date 必须是 YYYY-MM-DD") from exc
    if parsed.isoformat() != local_date:
        raise ContractError("local_date 必须是 YYYY-MM-DD")
    if source_file != f"{local_date}.md":
        raise ContractError("source_file 必须与local_date 对应的 Vault 根目录日记", kind="evidence")


def _runtime_error_kind(value: Any) -> str:
    raw = str(value or "runtime")
    if raw == "unknown_attempt":
        return "unknown_attempt"
    if raw in {"budget", "budget_exhausted", "usage_missing"}:
        return "budget_exhausted"
    if raw == "stale":
        return "stale"
    if raw in {"provider", "provider_error"}:
        return "provider_error"
    if raw in {
        "schema",
        "action",
        "invalid_response",
        "validation_failed",
        "parse",
    }:
        return "invalid_response"
    return "runtime"


def _action_summary(report: ActionReconcileReport) -> ActionSummary:
    return ActionSummary(
        seen=report.seen,
        already_resolved=report.already_resolved,
        applied=report.applied,
        rejected=report.rejected,
        conflict=report.conflict,
    )


def _reconcile_summary(result: ReconcileResult) -> ReconcileSummary:
    return ReconcileSummary(
        parsed_count=result.parsed_count,
        created=len(result.created_record_ids),
        revised=len(result.revised_record_ids),
        tombstoned=len(result.tombstoned_record_ids),
        unchanged=len(result.unchanged_record_ids),
        needs_review=len(result.needs_review),
    )


class CognitiveRecordWorker:
    """Interpret newly captured/revised records without running day-level work."""

    def __init__(
        self,
        vault: Path,
        provider: CompletionProvider | None = None,
        *,
        runtime: CognitiveRuntime | None = None,
        record_store: RecordStore | None = None,
        action_store: CognitiveActionStore | None = None,
        formal_store: Any | None = None,
        state_root: Path | None = None,
        home_projection_hook: HomeProjectionHook | None = None,
        clock: Callable[[], dt.datetime] | None = None,
        default_limit: int = DEFAULT_RECORDS_PER_RUN,
    ) -> None:
        try:
            resolved = Path(vault).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ContractError("Vault 不存在", kind="not_found") from exc
        if not resolved.is_dir():
            raise ContractError("Vault 必须是目录", kind="not_found")
        if runtime is None:
            if provider is None:
                raise ContractError("provider 或 runtime 必须提供", kind="runtime")
            runtime = CognitiveRuntime(
                resolved,
                provider,
                state_root=state_root,
                clock=clock or _default_clock,
            )
        elif provider is not None:
            raise ContractError("provider 和 runtime 不得同时提供")

        root = runtime.files.root
        if runtime.vault != resolved:
            raise ContractError("runtime 与 worker 必须使用同一 Vault", kind="evidence")
        if state_root is not None:
            requested = Path(state_root)
            if not requested.is_absolute():
                requested = resolved / requested
            candidate = requested.parent.resolve() / requested.name
            if candidate != root:
                raise ContractError("runtime 与 worker 必须使用同一 state_root", kind="evidence")

        records = record_store or runtime.store
        actions = action_store or CognitiveActionStore(resolved, state_root=root)
        formal = (
            formal_store
            if formal_store is not None
            else CognitiveBundleStore(resolved, state_root=root)
        )
        if records.vault != resolved or records.root != root:
            raise ContractError("record_store 与 runtime 状态边界不一致", kind="evidence")
        if actions.vault != resolved or actions.root != root:
            raise ContractError("action_store 与 runtime 状态边界不一致", kind="evidence")
        if getattr(formal, "vault", resolved) != resolved or getattr(formal, "root", root) != root:
            raise ContractError("formal_store 与 runtime 状态边界不一致", kind="evidence")
        if type(default_limit) is not int or not 1 <= default_limit <= MAX_RECORDS_PER_RUN:
            raise ContractError("default_limit 超出安全边界", kind="budget")

        self.vault = resolved
        self.root = root
        self.runtime = runtime
        self.records = records
        self.actions = actions
        self.formal = formal
        self.home_projection_hook = home_projection_hook
        self.clock = clock or runtime.clock
        self.default_limit = default_limit

    def _now(self) -> dt.datetime:
        value = self.clock()
        if not isinstance(value, dt.datetime) or value.tzinfo is None:
            raise ContractError("clock 必须返回带时区的 datetime", kind="runtime")
        return value

    def _load_receipt(self, record_id: str) -> InterpretationReceiptRevision | None:
        try:
            return self.actions.load_receipt_head(make_receipt_id(record_id))
        except ContractError as exc:
            if exc.kind == "not_found":
                return None
            raise

    def _publish(
        self,
        local_date: str,
        statuses: Mapping[str, Mapping[str, Any]],
    ) -> None:
        if self.home_projection_hook is None:
            return
        safe: dict[str, dict[str, Any]] = {}
        for record_id, raw in statuses.items():
            status = raw.get("status")
            error_kind = raw.get("error_kind")
            if status not in {"processing", "failed"}:
                raise ContractError("Home 即时状态无效")
            if status == "processing" and error_kind is not None:
                raise ContractError("processing 不得携带 error_kind")
            if status == "failed" and error_kind not in RUNTIME_ERROR_KINDS:
                raise ContractError("failed.error_kind 越过 allowlist")
            safe[record_id] = {"status": status, "error_kind": error_kind}
        self.home_projection_hook(local_date, safe)

    def _run_one(
        self,
        *,
        record: Mapping[str, Any],
        previous_receipt: InterpretationReceiptRevision | None,
        watermark: str,
    ) -> RecordWorkItem:
        record_id = str(record["record_id"])
        revision = int(record["revision"])
        trigger = "source_changed" if previous_receipt is not None else "reconcile"
        request = self.runtime.create_interpretation_request(
            record_id,
            trigger=trigger,
            feedback_watermark_sha256=watermark,
        )

        def run_request(
            request_id: str,
        ) -> tuple[Mapping[str, Any] | None, str | None]:
            try:
                return self.runtime.run_interpretation(request_id), None
            except ContractError as exc:
                # Filesystem/evidence failures are not user-facing model failures.
                if exc.kind in {"evidence", "stale", "conflict"}:
                    raise
                return None, _runtime_error_kind(exc.kind)
            except Exception:
                # A provider exception may have happened after its durable
                # attempt marker was written.  Re-entering the same request
                # performs recovery only; it must never issue a second call.
                try:
                    return self.runtime.run_interpretation(request_id), None
                except ContractError as exc:
                    if exc.kind in {"evidence", "stale", "conflict"}:
                        raise
                    return None, _runtime_error_kind(exc.kind)
                except Exception:
                    return None, "runtime"

        result, request_error = run_request(request["id"])
        if request_error is not None or result is None:
            return RecordWorkItem(
                record_id=record_id,
                record_revision=revision,
                outcome="failed",
                error_kind=request_error or "runtime",
            )

        retry_request = self.runtime.create_known_invalid_retry_request(result)
        if retry_request is not None:
            result, request_error = run_request(retry_request["id"])
            if request_error is not None or result is None:
                return RecordWorkItem(
                    record_id=record_id,
                    record_revision=revision,
                    outcome="failed",
                    error_kind=request_error or "runtime",
                )

        status = result.get("status")
        cached = bool(result.get("cached", False))
        if status == "completed" and result.get("receipt") is not None:
            receipt = InterpretationReceiptRevision.from_dict(result["receipt"])
            ref = self.actions.load_receipt_head_ref(receipt.receipt_id)
            return RecordWorkItem(
                record_id=record_id,
                record_revision=revision,
                outcome="ready",
                cached=cached,
                receipt_ref=ref.to_dict(),
            )
        if status == "no_candidate":
            return RecordWorkItem(
                record_id=record_id,
                record_revision=revision,
                outcome="no_candidate",
                cached=cached,
            )
        run = result.get("run")
        raw_error = run.get("error_kind") if isinstance(run, Mapping) else None
        return RecordWorkItem(
            record_id=record_id,
            record_revision=revision,
            outcome="failed",
            cached=cached,
            error_kind=_runtime_error_kind(raw_error or status),
        )

    def run(
        self,
        *,
        local_date: str,
        source_file: str,
        limit: int | None = None,
    ) -> RecordWorkerResult:
        _validate_day(local_date, source_file)
        bounded_limit = self.default_limit if limit is None else limit
        if type(bounded_limit) is not int or not 1 <= bounded_limit <= MAX_RECORDS_PER_RUN:
            raise ContractError("limit 必须在 1..64 之间", kind="budget")

        with _DayLock(vault=self.vault, root=self.root, local_date=local_date):
            now = self._now()
            action_report = self.actions.reconcile(
                receipt_store=self.actions,
                formal_store=self.formal,
                now=now,
            )
            terminal_receipts = tuple(
                receipt
                for receipt, _ in self.actions.list_receipt_heads(
                    statuses=("original_only", "tombstone")
                )
            )
            retraction = self.formal.retract_terminal_receipt_derivatives(
                terminal_receipts
            )
            _, watermark = self.actions.action_watermark()
            reconciled = self.records.reconcile_day(
                source_file,
                now=now,
                timezone=now.tzinfo,
            )

            items_by_id: dict[str, RecordWorkItem] = {}
            pending: list[tuple[Mapping[str, Any], InterpretationReceiptRevision | None]] = []
            heads = self.records.list_heads(local_date=local_date)
            for record in heads:
                current_ref = ObjectRef.from_dict(self.records.load_head_ref(record["record_id"]))
                receipt = self._load_receipt(record["record_id"])
                if receipt is not None and receipt.status in {"original_only", "tombstone"}:
                    items_by_id[record["record_id"]] = RecordWorkItem(
                        record_id=record["record_id"],
                        record_revision=record["revision"],
                        outcome=receipt.status,
                        receipt_ref=self.actions.load_receipt_head_ref(
                            receipt.receipt_id
                        ).to_dict(),
                    )
                elif receipt is not None and receipt.record_ref == current_ref:
                    items_by_id[record["record_id"]] = RecordWorkItem(
                        record_id=record["record_id"],
                        record_revision=record["revision"],
                        outcome="current",
                        receipt_ref=self.actions.load_receipt_head_ref(
                            receipt.receipt_id
                        ).to_dict(),
                    )
                else:
                    terminal = self.runtime.get_current_interpretation_terminal(
                        record["record_id"],
                        feedback_watermark_sha256=watermark,
                    )
                    if (
                        terminal is not None
                        and terminal["status"] == "no_candidate"
                    ):
                        items_by_id[record["record_id"]] = RecordWorkItem(
                            record_id=record["record_id"],
                            record_revision=record["revision"],
                            outcome="no_candidate",
                            cached=True,
                        )
                    else:
                        pending.append((record, receipt))

            selected = pending[:bounded_limit]
            deferred = len(pending) - len(selected)
            statuses: dict[str, dict[str, Any]] = {
                record["record_id"]: {"status": "processing", "error_kind": None}
                for record, _ in selected
            }
            if statuses:
                self._publish(local_date, statuses)
            elif action_report.applied or retraction.status == "applied":
                # Applied feedback can change a receipt or formal derivative
                # without making any record eligible for interpretation.  In
                # that zero-selection path Home still needs one bounded
                # refresh so the user action is immediately authoritative.
                # An empty mapping is the finite steady-state runtime status;
                # resolved-action replays and no-op retractions do not publish.
                self._publish(local_date, {})

            for record, receipt in selected:
                item = self._run_one(
                    record=record,
                    previous_receipt=receipt,
                    watermark=watermark,
                )
                items_by_id[item.record_id] = item
                if item.outcome == "failed":
                    statuses[item.record_id] = {
                        "status": "failed",
                        "error_kind": item.error_kind,
                    }
                else:
                    statuses.pop(item.record_id, None)
                self._publish(local_date, statuses)

            items = [
                items_by_id[record["record_id"]]
                for record in heads
                if record["record_id"] in items_by_id
            ]
            failures = sum(item.outcome == "failed" for item in items)
            if not heads:
                status = "no_records"
            elif failures:
                status = "completed_with_failures"
            elif deferred:
                status = "partial"
            else:
                status = "completed"
            return RecordWorkerResult(
                status=status,
                local_date=local_date,
                source_file=source_file,
                selected_count=len(selected),
                deferred_count=deferred,
                actions=_action_summary(action_report),
                reconcile=_reconcile_summary(reconciled),
                items=tuple(items),
                record_runtime_statuses={key: dict(value) for key, value in statuses.items()},
            )


__all__ = [
    "ActionSummary",
    "CognitiveRecordWorker",
    "DEFAULT_RECORDS_PER_RUN",
    "HomeProjectionHook",
    "MAX_RECORDS_PER_RUN",
    "RecordWorkerResult",
    "RecordWorkItem",
    "ReconcileSummary",
]
