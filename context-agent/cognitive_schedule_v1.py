"""Bounded schedule controller for the Cognitive Secretary day workflow.

This module deliberately owns no semantic work and creates no Agent V1
request.  It reuses the existing, user-controlled Agent V1 master gate and
``schedule.json`` switch, chooses at most today's or yesterday's local date,
and enters one injected unified ``day_runner(local_date, trigger)``.

The day runner remains the idempotency authority for source/material changes.
This controller only serializes entries for a date and limits its status
surface so Provider output, source text, paths and exception messages cannot
leak through a schedule tick.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from agent_v1 import (
    build_agent_profile,
    inspect_agent_schedule,
    inspect_agent_v1_gate,
)
from cognitive_actions_v1 import CognitiveActionStore
from cognitive_bundle_store_v1 import CognitiveBundleStore
from cognitive_runtime_v1 import CognitiveRuntime
from cognitive_store_v1 import RecordStore
from cognitive_v1 import ObjectRef, make_receipt_id
from core import ContractError, sha256_file


SCHEDULE_SCHEMA_VERSION = "1.0"
SCHEDULE_KIND = "memento_cognitive_schedule_tick"
RECOVERY_HOUR = 8
DAILY_HOUR = 21
DAILY_MINUTE = 0
ALLOWED_TRIGGERS = frozenset({"manual", "scheduled", "recovery"})
ALLOWED_RUNNER_STATUSES = frozenset(
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
REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "status",
        "checked_at",
        "local_date",
        "trigger",
        "runner_status",
        "bundle_committed",
        "review_valid",
        "error_kind",
    }
)

DayRunner = Callable[[str, str], Any]
CompletionReader = Callable[[str], "DayCompletionState"]
StatusInspector = Callable[[Path], Mapping[str, Any]]


def _validate_date(value: str) -> str:
    if not isinstance(value, str):
        raise ContractError("local_date 必须是 YYYY-MM-DD")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError("local_date 必须是有效 YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ContractError("local_date 必须是 YYYY-MM-DD")
    return value


def _local_now(value: dt.datetime | None) -> dt.datetime:
    result = dt.datetime.now().astimezone() if value is None else value
    if not isinstance(result, dt.datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ContractError("schedule now 必须是带时区 datetime")
    return result


@dataclass(frozen=True)
class DayCompletionState:
    """Minimal recovery signal derived from committed local objects."""

    bundle_committed: bool
    review_valid: bool
    inputs_current: bool = True
    terminal_complete: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.bundle_committed) is not bool
            or type(self.review_valid) is not bool
            or type(self.inputs_current) is not bool
            or type(self.terminal_complete) is not bool
        ):
            raise ContractError("day completion 状态必须是 boolean")

    @property
    def complete(self) -> bool:
        return self.inputs_current and (
            self.terminal_complete
            or (self.bundle_committed and self.review_valid)
        )


class _ReadOnlyCompletionProvider:
    def complete(self, messages: Any) -> Any:
        del messages
        raise ContractError("completion reader 不得调用 Provider", kind="runtime")


def _resolve_formal_object(
    store: CognitiveBundleStore,
    ref: ObjectRef,
) -> tuple[Mapping[str, Any], str]:
    """Resolve only the formal object kinds used by production Daily input."""

    if ref.kind == "reusable_memory":
        value = store.load_memory_head(ref.id)
        current = ObjectRef(
            "reusable_memory",
            value.memory_id,
            value.revision,
            value.sha256,
        )
    elif ref.kind == "relation":
        value = store.load_relation_head(ref.id)
        current = ObjectRef(
            "relation",
            value.relation_id,
            value.revision,
            value.sha256,
        )
    else:
        raise ContractError("completion object ref 未授权", kind="evidence")
    if current != ref:
        raise ContractError("completion object ref 已过期", kind="stale")
    return value.to_dict(), value.sha256


def inspect_day_completion(
    vault: Path,
    local_date: str,
    *,
    runtime: CognitiveRuntime | None = None,
) -> DayCompletionState:
    """Conservatively verify the formal bundle and hash-bound Daily Review.

    A Markdown file by itself is not a valid Review.  The current formal Daily
    Summary must bind its path and SHA-256, and those bytes must still match.
    """

    date_value = _validate_date(local_date)
    store = CognitiveBundleStore(vault)
    bundle_ref = store.load_day_bundle_ref(date_value)
    manifest = store.load_day_manifest(date_value)
    loaded = store.load_daily_summary_head(date_value)
    review_valid = False
    if loaded is not None:
        summary, _ = loaded
        expected_file = f"Reviews/Daily/{date_value}.md"
        if (
            summary.status == "active"
            and summary.review_file == expected_file
            and summary.review_sha256 is not None
        ):
            target = store.vault / expected_file
            if target.exists() or target.is_symlink():
                details = target.lstat()
                review_valid = bool(
                    not stat.S_ISLNK(details.st_mode)
                    and stat.S_ISREG(details.st_mode)
                    and details.st_uid == os.getuid()
                    and details.st_nlink == 1
                    and sha256_file(target) == summary.review_sha256
                )
    records = RecordStore(store.vault, state_root=store.root)
    actions = CognitiveActionStore(store.vault, state_root=store.root)
    inspection_runtime = runtime or CognitiveRuntime(
        store.vault,
        _ReadOnlyCompletionProvider(),
        state_root=store.root,
    )
    if inspection_runtime.files.root != store.root:
        raise ContractError("completion runtime state_root 不一致", kind="evidence")
    if inspection_runtime.object_resolver is None:
        inspection_runtime.object_resolver = lambda ref: _resolve_formal_object(
            store,
            ref,
        )
    _, action_watermark = actions.action_watermark()
    expected_source_refs: set[tuple[str, str, int, str]] = set()
    expected_receipt_refs: set[tuple[str, str, int, str]] = set()
    daily_receipts: list[Any] = []
    daily_receipt_refs: list[ObjectRef] = []
    current_record_ids: set[str] = set()
    terminal_record_ids: set[str] = set()
    inputs_current = True

    # The index is only a projection of the Markdown source.  A record added
    # after the last reconcile must make 08:00 recovery due even when an old
    # bundle still covers every indexed head.
    source_file = f"{date_value}.md"
    source_path = store.vault / source_file
    heads = records.list_heads(local_date=date_value)
    if source_path.exists() or source_path.is_symlink():
        parsed = records.parse_day(source_file)
        if (
            parsed.issues
            or len(parsed.records) != len(heads)
            or any(
                head["source_snapshot_sha256"]
                != parsed.source_snapshot_sha256
                for head in heads
            )
        ):
            inputs_current = False
    elif heads:
        inputs_current = False

    for raw_ref in records.list_head_refs(local_date=date_value):
        source_ref = ObjectRef.from_dict(raw_ref)
        current_record_ids.add(source_ref.id)
        receipt = None
        receipt_ref = None
        try:
            receipt = actions.load_receipt_head(make_receipt_id(source_ref.id))
            receipt_ref = actions.load_receipt_head_ref(
                make_receipt_id(source_ref.id)
            )
        except ContractError as exc:
            if exc.kind != "not_found":
                raise

        if receipt is not None:
            if receipt.status not in {
                "ready",
                "needs_review",
                "original_only",
                "tombstone",
            }:
                raise ContractError("receipt terminal status 无效", kind="evidence")
            if receipt.status in {"original_only", "tombstone"}:
                # User terminal receipts are record-level, irreversible
                # choices.  Runtime intentionally forbids appending an
                # interpretation revision after either status, so a later
                # source revision must not create an endless recovery loop.
                terminal_record_ids.add(source_ref.id)
                continue
        if receipt is not None and receipt.record_ref == source_ref:
            assert receipt_ref is not None
            expected_source_refs.add(
                (
                    source_ref.kind,
                    source_ref.id,
                    source_ref.revision,
                    source_ref.revision_sha256,
                )
            )
            expected_receipt_refs.add(
                (
                    receipt_ref.kind,
                    receipt_ref.id,
                    receipt_ref.revision,
                    receipt_ref.revision_sha256,
                )
            )
            daily_receipts.append(receipt)
            daily_receipt_refs.append(receipt_ref)
            continue

        # A stale receipt never covers an edited source.  The only no-receipt
        # terminal that may replace it is a trusted no_candidate run for the
        # exact current source, action watermark and policy.
        terminal = inspection_runtime.get_current_interpretation_terminal(
            source_ref.id,
            feedback_watermark_sha256=action_watermark,
        )
        if terminal is not None:
            if terminal.get("status") != "no_candidate":
                raise ContractError("interpretation terminal 无效", kind="evidence")
            terminal_record_ids.add(source_ref.id)
            continue
        inputs_current = False

    terminal_complete = bool(
        inputs_current
        and not expected_source_refs
        and terminal_record_ids == current_record_ids
    )
    daily_no_change_current = False
    if inputs_current and expected_source_refs:
        daily_receipts.sort(key=lambda row: row.receipt_id)
        daily_receipt_refs.sort(
            key=lambda row: (row.id, row.revision, row.revision_sha256)
        )
        source_spans = []
        seen_spans: set[str] = set()
        for receipt in daily_receipts:
            for span in receipt.source_spans:
                if span.sha256 in seen_spans:
                    continue
                source_spans.append(span)
                seen_spans.add(span.sha256)
        source_spans.sort(
            key=lambda row: (
                row.record_id,
                row.record_revision,
                row.line_start,
                row.line_end,
                row.quote_sha256,
            )
        )
        active_objects = [
            ObjectRef(
                "reusable_memory",
                row.memory_id,
                row.revision,
                row.sha256,
            )
            for row in store.list_active_memories()
        ]
        active_objects.extend(
            ObjectRef(
                "relation",
                row.relation_id,
                row.revision,
                row.sha256,
            )
            for row in store.list_active_relations()
        )
        active_objects.sort(key=lambda row: (row.kind, row.id))
        profile = build_agent_profile(store.vault)
        daily_terminal = inspection_runtime.get_current_daily_terminal(
            date_value,
            source_spans=source_spans,
            object_refs=active_objects,
            receipt_refs=daily_receipt_refs,
            daily_context={
                "record_count": len(current_record_ids),
                "receipt_count": len(daily_receipts),
            },
            profile_sha256=profile["profile_sha256"],
            user_action_watermark_sha256=action_watermark,
        )
        if daily_terminal is not None:
            if daily_terminal.get("status") != "no_change":
                raise ContractError("daily terminal 无效", kind="evidence")
            if bundle_ref is None:
                terminal_complete = True
            else:
                daily_no_change_current = True
    if manifest is not None:
        # A newer user terminal or no_candidate outcome is allowed to retire
        # an older bundle row without rewriting immutable bundle history.
        active_manifest_record_ids = current_record_ids - terminal_record_ids
        manifest_source_refs = {
            (
                ref.kind,
                ref.id,
                ref.revision,
                ref.revision_sha256,
            )
            for ref in (
                ObjectRef.from_dict(row) for row in manifest["source_refs"]
            )
            if ref.id in active_manifest_record_ids
        }
        active_manifest_receipt_ids = {
            make_receipt_id(record_id) for record_id in active_manifest_record_ids
        }
        manifest_receipt_refs = {
            (
                ref.kind,
                ref.id,
                ref.revision,
                ref.revision_sha256,
            )
            for ref in (
                ObjectRef.from_dict(row) for row in manifest["receipt_refs"]
            )
            if ref.id in active_manifest_receipt_ids
        }
        if not terminal_complete and not daily_no_change_current:
            inputs_current = bool(
                inputs_current
                and expected_source_refs == manifest_source_refs
                and expected_receipt_refs == manifest_receipt_refs
            )
    elif bundle_ref is not None:
        # A terminal record set may retire a complete immutable bundle, but
        # must never hide a broken catalog/ref pair whose manifest vanished.
        inputs_current = False
    return DayCompletionState(
        bundle_committed=bundle_ref is not None,
        review_valid=review_valid,
        inputs_current=inputs_current,
        terminal_complete=terminal_complete,
    )


class _DayLock:
    """Owner-only, no-follow cross-process mutex for one local date."""

    def __init__(self, vault: Path, local_date: str) -> None:
        self.vault = vault
        self.local_date = _validate_date(local_date)
        self.descriptor: int | None = None

    def _secure_directory(self, path: Path) -> None:
        try:
            path.relative_to(self.vault)
        except ValueError as exc:
            raise ContractError("调度锁目录越过 Vault 边界", kind="evidence") from exc
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ContractError("调度锁目录不安全", kind="evidence")
        if not path.exists():
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                # Another same-user tick may have created the directory after
                # the lstat above.  The security checks below still decide
                # whether the raced path is acceptable.
                pass
        details = path.lstat()
        if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
            raise ContractError("调度锁目录不安全", kind="evidence")
        with contextlib.suppress(OSError):
            path.chmod(0o700)
        if path.lstat().st_mode & 0o077:
            raise ContractError("调度锁目录必须是当前用户私有目录", kind="evidence")

    def __enter__(self) -> "_DayLock":
        context_root = self.vault / ".context-agent"
        runtime_root = context_root / "cognitive-secretary-v1"
        locks_root = runtime_root / "locks"
        for path in (context_root, runtime_root, locks_root):
            self._secure_directory(path)
        lock_path = locks_root / f"schedule-{self.local_date}.lock"
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise ContractError("日级调度锁无法安全打开", kind="evidence") from exc
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.getuid()
            or details.st_nlink != 1
            or stat.S_IMODE(details.st_mode) & 0o077
        ):
            os.close(descriptor)
            raise ContractError("日级调度锁必须是当前用户私有单链接文件", kind="evidence")
        with contextlib.suppress(OSError):
            os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        self.descriptor = descriptor
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.descriptor is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


def _report(
    *,
    status: str,
    checked_at: str,
    local_date: str,
    trigger: str,
    runner_status: str | None = None,
    completion: DayCompletionState | None = None,
    error_kind: str | None = None,
) -> dict[str, Any]:
    result = {
        "schema_version": SCHEDULE_SCHEMA_VERSION,
        "kind": SCHEDULE_KIND,
        "status": status,
        "checked_at": checked_at,
        "local_date": _validate_date(local_date),
        "trigger": trigger,
        "runner_status": runner_status,
        "bundle_committed": None if completion is None else completion.bundle_committed,
        "review_valid": None if completion is None else completion.review_valid,
        "error_kind": error_kind,
    }
    if set(result) != REPORT_FIELDS:
        raise ContractError("schedule report 字段不完整", kind="runtime")
    return result


def _runner_status(value: Any) -> str:
    raw: Any
    if isinstance(value, Mapping):
        raw = value.get("status")
    else:
        raw = getattr(value, "status", None)
    if raw not in ALLOWED_RUNNER_STATUSES:
        raise ContractError("day runner 返回了无效状态", kind="runtime")
    return str(raw)


class CognitiveScheduleCore:
    """Route manual, scheduled and recovery work into one day runner."""

    def __init__(
        self,
        vault: Path,
        *,
        day_runner: DayRunner,
        completion_reader: CompletionReader | None = None,
        gate_inspector: StatusInspector = inspect_agent_v1_gate,
        schedule_inspector: StatusInspector = inspect_agent_schedule,
    ) -> None:
        try:
            resolved = vault.expanduser().resolve(strict=True)
        except OSError as exc:
            raise ContractError("Vault 目录不存在", kind="not_found") from exc
        details = resolved.lstat()
        if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
            raise ContractError("Vault 必须是当前用户目录", kind="evidence")
        if not callable(day_runner):
            raise ContractError("day_runner 必须可调用")
        self.vault = resolved
        self.day_runner = day_runner
        self.completion_reader = completion_reader or (
            lambda local_date: inspect_day_completion(self.vault, local_date)
        )
        self.gate_inspector = gate_inspector
        self.schedule_inspector = schedule_inspector

    @staticmethod
    def _due_target(local_now: dt.datetime) -> tuple[str, str, dt.datetime]:
        today_slot = local_now.replace(
            hour=DAILY_HOUR,
            minute=DAILY_MINUTE,
            second=0,
            microsecond=0,
        )
        recovery_slot = local_now.replace(
            hour=RECOVERY_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
        if local_now >= today_slot:
            return local_now.date().isoformat(), "scheduled", today_slot
        previous_slot = today_slot - dt.timedelta(days=1)
        if local_now >= recovery_slot:
            return previous_slot.date().isoformat(), "recovery", previous_slot
        # A launchd calendar event missed during sleep is coalesced after wake.
        return previous_slot.date().isoformat(), "scheduled", previous_slot

    @staticmethod
    def _enabled_status(report: Mapping[str, Any], *, label: str) -> bool:
        state = report.get("state")
        reason = report.get("reason")
        enabled = report.get("enabled")
        if state == "invalid":
            raise ContractError(f"{label} 无效：{reason}", kind="evidence")
        if state not in {"enabled", "disabled"} or type(enabled) is not bool:
            raise ContractError(f"{label} 状态合同无效", kind="evidence")
        if enabled != (state == "enabled"):
            raise ContractError(f"{label} 状态合同不一致", kind="evidence")
        return enabled

    @staticmethod
    def _schedule_updated_at(
        report: Mapping[str, Any], local_now: dt.datetime
    ) -> dt.datetime:
        schedule = report.get("schedule")
        if not isinstance(schedule, Mapping):
            raise ContractError("schedule 状态缺少配置", kind="evidence")
        raw = schedule.get("updated_at")
        if not isinstance(raw, str):
            raise ContractError("schedule.updated_at 无效", kind="evidence")
        try:
            parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractError("schedule.updated_at 无效", kind="evidence") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ContractError("schedule.updated_at 必须带时区", kind="evidence")
        return parsed.astimezone(local_now.tzinfo)

    def _run_locked(
        self,
        *,
        local_date: str,
        trigger: str,
        checked_at: str,
        completion: DayCompletionState | None,
    ) -> dict[str, Any]:
        if trigger not in ALLOWED_TRIGGERS:
            raise ContractError("daily trigger 无效")
        try:
            result = self.day_runner(local_date, trigger)
            status = _runner_status(result)
        except Exception as exc:  # finite, retryable schedule failure surface
            kind = "contract" if isinstance(exc, ContractError) else "runtime"
            return _report(
                status="runner_failed",
                checked_at=checked_at,
                local_date=local_date,
                trigger=trigger,
                completion=completion,
                error_kind=kind,
            )
        return _report(
            status="completed",
            checked_at=checked_at,
            local_date=local_date,
            trigger=trigger,
            runner_status=status,
            completion=completion,
        )

    def tick(self, *, now: dt.datetime | None = None) -> dict[str, Any]:
        """Run only the most recent bounded schedule target, if due."""

        local_now = _local_now(now)
        checked_at = local_now.isoformat(timespec="seconds")
        local_date, trigger, due_slot = self._due_target(local_now)
        with _DayLock(self.vault, local_date):
            gate = self.gate_inspector(self.vault)
            if not self._enabled_status(gate, label="Agent V1 总开关"):
                return _report(
                    status="master_gate_disabled",
                    checked_at=checked_at,
                    local_date=local_date,
                    trigger=trigger,
                )
            schedule = self.schedule_inspector(self.vault)
            if not self._enabled_status(schedule, label="Agent schedule"):
                return _report(
                    status="schedule_disabled",
                    checked_at=checked_at,
                    local_date=local_date,
                    trigger=trigger,
                )
            if self._schedule_updated_at(schedule, local_now) > due_slot:
                return _report(
                    status="not_due",
                    checked_at=checked_at,
                    local_date=local_date,
                    trigger=trigger,
                )

            completion: DayCompletionState | None = None
            if trigger == "recovery":
                completion = self.completion_reader(local_date)
                if not isinstance(completion, DayCompletionState):
                    raise ContractError("completion_reader 返回合同无效", kind="runtime")
                if completion.complete:
                    return _report(
                        status="not_due",
                        checked_at=checked_at,
                        local_date=local_date,
                        trigger=trigger,
                        completion=completion,
                    )
            return self._run_locked(
                local_date=local_date,
                trigger=trigger,
                checked_at=checked_at,
                completion=completion,
            )

    def run_manual(self, *, now: dt.datetime | None = None) -> dict[str, Any]:
        """Merge today manually; the automatic schedule switch is irrelevant."""

        local_now = _local_now(now)
        checked_at = local_now.isoformat(timespec="seconds")
        local_date = local_now.date().isoformat()
        with _DayLock(self.vault, local_date):
            gate = self.gate_inspector(self.vault)
            if not self._enabled_status(gate, label="Agent V1 总开关"):
                return _report(
                    status="master_gate_disabled",
                    checked_at=checked_at,
                    local_date=local_date,
                    trigger="manual",
                )
            return self._run_locked(
                local_date=local_date,
                trigger="manual",
                checked_at=checked_at,
                completion=None,
            )


def tick_cognitive_schedule(
    vault: Path,
    *,
    day_runner: DayRunner,
    completion_reader: CompletionReader | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    return CognitiveScheduleCore(
        vault,
        day_runner=day_runner,
        completion_reader=completion_reader,
    ).tick(now=now)


def run_manual_cognitive_day(
    vault: Path,
    *,
    day_runner: DayRunner,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    return CognitiveScheduleCore(vault, day_runner=day_runner).run_manual(now=now)


__all__ = [
    "CognitiveScheduleCore",
    "DayCompletionState",
    "inspect_day_completion",
    "run_manual_cognitive_day",
    "tick_cognitive_schedule",
]
