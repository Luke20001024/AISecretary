"""Deterministic end-to-end controller for Cognitive Secretary MVP.

The pipeline coordinates the model-owning :mod:`cognitive_runtime_v1` with
the model-free record, feedback and formal bundle stores.  It intentionally
contains no semantic heuristics: provider agents propose interpretations and
daily operations, while this module only freezes inputs, validates immutable
references, materializes new revisions and advances the workflow in order.

Candidate IDs never become formal IDs.  A daily proposal is first persisted in
the candidate staging area and is then converted into independent
``rmem_``/``rel_`` revisions before one atomic bundle manifest makes them
visible.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, Tuple, Union

from cognitive_actions_v1 import ActionReconcileReport, CognitiveActionStore
from cognitive_bundle_store_v1 import BundleCommitResult, CognitiveBundleStore
from cognitive_runtime_v1 import CognitiveRuntime
from cognitive_store_v1 import RecordStore
from cognitive_v1 import (
    COGNITIVE_SCHEMA_VERSION,
    DailySummaryRevision,
    InterpretationReceiptRevision,
    ObjectRef,
    RelationRevision,
    ReusableMemoryRevision,
    SourceSpan,
    make_daily_summary_id,
    make_receipt_id,
    make_relation_id,
    make_reusable_memory_id,
    persisted_sha256,
    validate_long_term_evidence_refs,
)
from core import ContractError, canonical_json, sha256_bytes


UnderstandingResolver = Callable[
    [ObjectRef], Union[Mapping[str, Any], Tuple[Mapping[str, Any], str]]
]
PostCommitHook = Callable[[DailySummaryRevision, BundleCommitResult], None]


def _default_clock() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _now_text(clock: Callable[[], dt.datetime]) -> str:
    value = clock()
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise ContractError("clock 必须返回带时区的 datetime", kind="runtime")
    return value.isoformat(timespec="seconds")


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


def _candidate_id(prefix: str, run_id: str, index: int, payload: Mapping[str, Any]) -> str:
    digest = sha256_bytes(
        canonical_json(
            {
                "namespace": "daily-pipeline-candidate-v1",
                "run_id": run_id,
                "index": index,
                "payload": dict(payload),
            }
        ).encode("utf-8")
    )
    return prefix + digest[:24]


def _formal_ref(value: ReusableMemoryRevision | RelationRevision) -> ObjectRef:
    if isinstance(value, ReusableMemoryRevision):
        return ObjectRef(
            "reusable_memory", value.memory_id, value.revision, value.sha256
        )
    return ObjectRef("relation", value.relation_id, value.revision, value.sha256)


@dataclass(frozen=True)
class MaterialBrief:
    """Evidence-only hand-off for the existing Agent V1 long-term judge."""

    local_date: str
    bundle_ref: ObjectRef
    source_refs: tuple[ObjectRef, ...]
    receipt_refs: tuple[ObjectRef, ...]
    memory_refs: tuple[ObjectRef, ...]
    relation_refs: tuple[ObjectRef, ...]
    requires_long_term_review: bool
    material_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_date": self.local_date,
            "bundle_ref": self.bundle_ref.to_dict(),
            "source_refs": [ref.to_dict() for ref in self.source_refs],
            "receipt_refs": [ref.to_dict() for ref in self.receipt_refs],
            "memory_refs": [ref.to_dict() for ref in self.memory_refs],
            "relation_refs": [ref.to_dict() for ref in self.relation_refs],
            "requires_long_term_review": self.requires_long_term_review,
            "material_sha256": self.material_sha256,
        }


@dataclass(frozen=True)
class DayPipelineResult:
    status: str
    local_date: str
    action_report: ActionReconcileReport
    record_ids: tuple[str, ...]
    interpretation_results: tuple[Mapping[str, Any], ...]
    receipt_refs: tuple[ObjectRef, ...]
    daily_result: Mapping[str, Any] | None
    commit_result: BundleCommitResult | None
    material_brief: MaterialBrief | None


class CognitivePipeline:
    """One recoverable workflow for a single local day.

    ``provider`` is required only when ``runtime`` is not injected.  Tests and
    the CLI may inject a fake provider without ever touching the network.
    """

    def __init__(
        self,
        vault: Path,
        provider: Any | None = None,
        *,
        state_root: Path | None = None,
        runtime: CognitiveRuntime | None = None,
        record_store: RecordStore | None = None,
        action_store: CognitiveActionStore | None = None,
        bundle_store: CognitiveBundleStore | None = None,
        understanding_resolver: UnderstandingResolver | None = None,
        clock: Callable[[], dt.datetime] = _default_clock,
        post_commit_hooks: Sequence[PostCommitHook] = (),
    ) -> None:
        try:
            resolved = Path(vault).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ContractError("Vault 不存在", kind="not_found") from exc
        if not resolved.is_dir():
            raise ContractError("Vault 必须是目录", kind="not_found")
        self.vault = resolved
        self.state_root = state_root
        self.clock = clock
        self.records = record_store or RecordStore(resolved, state_root=state_root)
        self.actions = action_store or CognitiveActionStore(resolved, state_root=state_root)
        self.bundles = bundle_store or CognitiveBundleStore(resolved, state_root=state_root)
        self.bundles.set_action_watermark_reader(
            lambda: self.actions.action_watermark()[1]
        )
        self.understanding_resolver = understanding_resolver
        self.post_commit_hooks = tuple(post_commit_hooks)
        if runtime is None:
            if provider is None:
                raise ContractError("runtime 与 provider 不能同时缺失", kind="runtime")
            self.runtime = CognitiveRuntime(
                resolved,
                provider,
                state_root=state_root,
                object_resolver=self._resolve_object,
                clock=clock,
            )
        else:
            self.runtime = runtime
            # An injected runtime may deliberately provide a broader resolver
            # (for example Agent V1 understandings).  Only fill the fail-closed
            # default; do not replace caller authorization.
            if self.runtime.object_resolver is None:
                self.runtime.object_resolver = self._resolve_object

    # ------------------------------------------------------------------
    # Verified object and revision helpers

    def _resolve_object(
        self, ref: ObjectRef
    ) -> Mapping[str, Any] | tuple[Mapping[str, Any], str]:
        if ref.kind == "reusable_memory":
            value = self.bundles.load_memory_head(ref.id)
            actual = ObjectRef("reusable_memory", value.memory_id, value.revision, value.sha256)
            if actual != ref:
                raise ContractError("reusable memory ref 已过期", kind="stale")
            return value.to_dict(), value.sha256
        if ref.kind == "relation":
            value = self.bundles.load_relation_head(ref.id)
            actual = ObjectRef("relation", value.relation_id, value.revision, value.sha256)
            if actual != ref:
                raise ContractError("relation ref 已过期", kind="stale")
            return value.to_dict(), value.sha256
        if ref.kind == "understanding" and self.understanding_resolver is not None:
            return self.understanding_resolver(ref)
        raise ContractError("未授权的 object ref", kind="evidence")

    def _receipt_head(self, record_id: str) -> InterpretationReceiptRevision | None:
        try:
            return self.actions.load_receipt_head(make_receipt_id(record_id))
        except ContractError as exc:
            if exc.kind == "not_found":
                return None
            raise

    @staticmethod
    def _receipt_ref(receipt: InterpretationReceiptRevision) -> ObjectRef:
        return ObjectRef(
            "interpretation_receipt",
            receipt.receipt_id,
            receipt.revision,
            receipt.sha256,
        )

    def _active_formal_refs(self) -> tuple[ObjectRef, ...]:
        refs = [
            ObjectRef("reusable_memory", row.memory_id, row.revision, row.sha256)
            for row in self.bundles.list_active_memories()
        ]
        refs.extend(
            ObjectRef("relation", row.relation_id, row.revision, row.sha256)
            for row in self.bundles.list_active_relations()
        )
        return tuple(sorted(refs, key=lambda row: (row.kind, row.id)))

    def _retract_terminal_receipt_derivatives(self) -> None:
        """Apply terminal receipt priority before freezing daily inputs."""

        receipt_ids: set[str] = set()
        for memory in self.bundles.list_active_memories():
            receipt_ids.update(ref.id for ref in memory.origin_receipt_refs)
            receipt_ids.update(
                make_receipt_id(span.record_id)
                for span in memory.source_spans
            )
        for relation in self.bundles.list_active_relations():
            receipt_ids.update(
                make_receipt_id(span.record_id)
                for span in relation.source_spans
            )
        terminal: list[InterpretationReceiptRevision] = []
        for receipt_id in sorted(receipt_ids):
            try:
                receipt = self.actions.load_receipt_head(receipt_id)
            except ContractError as exc:
                if exc.kind == "not_found":
                    continue
                raise
            if receipt.status in {"original_only", "tombstone"}:
                terminal.append(receipt)
        self.bundles.retract_terminal_receipt_derivatives(terminal)

    # ------------------------------------------------------------------
    # Candidate -> formal revision materialization

    def _stage_and_materialize(
        self,
        candidate: Mapping[str, Any],
        *,
        receipt_refs: Sequence[ObjectRef],
        bundle_revision: int,
    ) -> tuple[
        tuple[ReusableMemoryRevision, ...],
        tuple[RelationRevision, ...],
        tuple[Mapping[str, Any], ...],
    ]:
        run_id = candidate["run_id"]
        local_date = candidate["local_date"]
        memory_ops = list(candidate["memory_operations"])
        relation_ops = list(candidate["relation_operations"])
        staged_memories: list[dict[str, Any]] = []
        staged_relations: list[dict[str, Any]] = []
        memory_candidate_ids: dict[int, str] = {}
        relation_candidate_ids: dict[int, str] = {}
        for index, operation in enumerate(memory_ops):
            identifier = _candidate_id("cmem_", run_id, index, operation)
            memory_candidate_ids[index] = identifier
            staged_memories.append({"candidate_id": identifier, **dict(operation)})
        for index, operation in enumerate(relation_ops):
            identifier = _candidate_id("crel_", run_id, index, operation)
            relation_candidate_ids[index] = identifier
            staged_relations.append({"candidate_id": identifier, **dict(operation)})
        self.bundles.stage_candidates(
            run_id=run_id,
            local_date=local_date,
            memory_candidates=staged_memories,
            relation_candidates=staged_relations,
            now=self.clock(),
        )

        now_text = _now_text(self.clock)
        bundle_id = "db_" + local_date.replace("-", "")
        receipt_by_record: dict[str, ObjectRef] = {}
        for ref in receipt_refs:
            receipt = self.actions.load_receipt_head(ref.id)
            if self._receipt_ref(receipt) != ref:
                raise ContractError("receipt ref 在物化前变化", kind="stale")
            receipt_by_record[receipt.record_ref.id] = ref

        memories: list[ReusableMemoryRevision] = []
        memory_results: dict[int, ObjectRef] = {}
        materials: list[Mapping[str, Any]] = []
        for index, operation in enumerate(memory_ops):
            action = operation["operation"]
            spans = tuple(SourceSpan.from_dict(row) for row in operation["source_spans"])
            origins = tuple(
                sorted(
                    {
                        receipt_by_record[span.record_id]
                        for span in spans
                        if span.record_id in receipt_by_record
                    },
                    key=lambda row: (row.id, row.revision, row.revision_sha256),
                )
            )
            if action == "reuse":
                target = ObjectRef.from_dict(operation["target_memory_ref"])
                current = self.bundles.load_memory_head(target.id)
                current_ref = ObjectRef(
                    "reusable_memory", current.memory_id, current.revision, current.sha256
                )
                if current_ref != target:
                    raise ContractError("reuse memory ref 已过期", kind="stale")
                memory_results[index] = target
                continue
            if action == "new":
                memory_id = make_reusable_memory_id(f"{run_id}:memory:{index}")
                revision = 1
                previous_sha = None
            elif action == "revise":
                target = ObjectRef.from_dict(operation["target_memory_ref"])
                current = self.bundles.load_memory_head(target.id)
                current_ref = ObjectRef(
                    "reusable_memory", current.memory_id, current.revision, current.sha256
                )
                if current_ref != target:
                    raise ContractError("revise memory ref 已过期", kind="stale")
                memory_id = target.id
                revision = target.revision + 1
                previous_sha = target.revision_sha256
            else:
                raise ContractError("daily memory operation 无效", kind="schema")
            memory = ReusableMemoryRevision(
                schema_version=COGNITIVE_SCHEMA_VERSION,
                kind="memento_reusable_memory_revision",
                memory_id=memory_id,
                revision=revision,
                status="active",
                operation=action,
                created_at=now_text,
                statement=operation["statement"],
                memory_kind=operation["memory_kind"],
                topics=tuple(operation["topics"]),
                purposes=tuple(operation["purposes"]),
                uncertainty=operation["uncertainty"],
                source_spans=spans,
                origin_receipt_refs=origins,
                provenance={
                    "origin": "daily_integrator",
                    "run_id": run_id,
                    "bundle_id": bundle_id,
                    "bundle_revision": bundle_revision,
                    "user_action_id": None,
                },
                previous_revision_sha256=previous_sha,
            )
            memories.append(memory)
            formal = _formal_ref(memory)
            memory_results[index] = formal
            materials.append(
                {
                    "candidate_kind": "memory",
                    "candidate_id": memory_candidate_ids[index],
                    "formal_ref": formal.to_dict(),
                }
            )

        def endpoint(value: Mapping[str, Any]) -> ObjectRef:
            if value["kind"] == "memory_operation":
                try:
                    return memory_results[value["operation_index"]]
                except KeyError as exc:
                    raise ContractError("relation endpoint 引用未物化 memory") from exc
            ref = ObjectRef.from_dict(value["object_ref"])
            # The formal store will repeat current-head validation in the
            # transaction lock.  Resolve here too so malformed endpoints fail
            # before any formal revision is constructed.
            self._resolve_object(ref)
            return ref

        relations: list[RelationRevision] = []
        for index, operation in enumerate(relation_ops):
            action = operation["operation"]
            if action == "new":
                relation_id = make_relation_id(f"{run_id}:relation:{index}")
                revision = 1
                previous_sha = None
            elif action == "revise":
                target = ObjectRef.from_dict(operation["target_relation_ref"])
                current = self.bundles.load_relation_head(target.id)
                current_ref = ObjectRef(
                    "relation", current.relation_id, current.revision, current.sha256
                )
                if current_ref != target:
                    raise ContractError("revise relation ref 已过期", kind="stale")
                relation_id = target.id
                revision = target.revision + 1
                previous_sha = target.revision_sha256
            else:
                raise ContractError("daily relation operation 无效", kind="schema")
            relation = RelationRevision(
                schema_version=COGNITIVE_SCHEMA_VERSION,
                kind="memento_relation_revision",
                relation_id=relation_id,
                revision=revision,
                status="active",
                operation=action,
                created_at=now_text,
                type=operation["type"],
                from_ref=endpoint(operation["from_endpoint"]),
                to_ref=endpoint(operation["to_endpoint"]),
                direction=operation["direction"],
                statement=operation["statement"],
                uncertainty=operation["uncertainty"],
                source_spans=tuple(
                    SourceSpan.from_dict(row) for row in operation["source_spans"]
                ),
                valid_from=local_date,
                provenance={
                    "origin": "daily_integrator",
                    "run_id": run_id,
                    "bundle_id": bundle_id,
                    "bundle_revision": bundle_revision,
                    "user_action_id": None,
                },
                previous_revision_sha256=previous_sha,
            )
            relations.append(relation)
            formal = _formal_ref(relation)
            materials.append(
                {
                    "candidate_kind": "relation",
                    "candidate_id": relation_candidate_ids[index],
                    "formal_ref": formal.to_dict(),
                }
            )
        return tuple(memories), tuple(relations), tuple(materials)

    # ------------------------------------------------------------------
    # Public workflow

    def run_day(
        self,
        local_date: str,
        *,
        trigger: str = "manual",
        source_files: Sequence[str] | None = None,
        record_ids: Sequence[str] | None = None,
        record_target_refs: Sequence[ObjectRef | Mapping[str, Any]] = (),
        daily_object_refs: Sequence[ObjectRef | Mapping[str, Any]] = (),
        profile_sha256: str | None = None,
        replay_profile_sha256: str | None = None,
        daily_context: Mapping[str, Any] | None = None,
        inspect_memory: Callable[[ObjectRef], Mapping[str, Any]] | None = None,
        search_history: Callable[..., Sequence[SourceSpan | Mapping[str, Any]]] | None = None,
    ) -> DayPipelineResult:
        local_date = _validate_date(local_date)
        if trigger not in {"manual", "scheduled", "recovery"}:
            raise ContractError("daily trigger 无效")
        if profile_sha256 is None:
            # Direct use of the exported Pipeline must freeze the same
            # authoritative Agent profile as DayOrchestrator.  A zero default
            # would silently disable the formal-store profile CAS for callers
            # outside the composition root.
            from agent_v1 import build_agent_profile

            profile_sha256 = build_agent_profile(self.vault)["profile_sha256"]
        if (
            not isinstance(profile_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", profile_sha256)
        ):
            raise ContractError("profile_sha256 无效", kind="evidence")
        if replay_profile_sha256 is not None and (
            not isinstance(replay_profile_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", replay_profile_sha256)
        ):
            raise ContractError("replay_profile_sha256 无效", kind="evidence")
        now = self.clock()
        if now.tzinfo is None:
            raise ContractError("clock 必须返回带时区 datetime", kind="runtime")

        # User-priority revisions are always materialized before any model
        # input is frozen.
        action_report = self.actions.reconcile(
            receipt_store=self.actions,
            formal_store=self.bundles,
            now=now,
        )
        self._retract_terminal_receipt_derivatives()
        _, action_watermark = self.actions.action_watermark()

        selected_files = (
            tuple(source_files)
            if source_files is not None
            else (f"{local_date}.md",)
        )
        existing_heads = self.records.list_heads(local_date=local_date)
        for source_file in selected_files:
            source_path = self.vault / source_file
            if (
                source_files is None
                and not existing_heads
                and not source_path.exists()
                and not source_path.is_symlink()
            ):
                # A scheduled/recovery run on a day with no capture file is a
                # normal empty day.  Keep explicit source selections, unsafe
                # links, and vanished sources with indexed records on the
                # strict RecordStore path so they still fail closed.
                continue
            self.records.reconcile_day(source_file, now=now, timezone=now.tzinfo)

        if record_ids is None:
            heads = self.records.list_heads(local_date=local_date)
        else:
            if len(set(record_ids)) != len(record_ids):
                raise ContractError("record_ids 不得重复")
            heads = [self.records.load_head(record_id) for record_id in record_ids]
            if any(row["local_date"] != local_date for row in heads):
                raise ContractError("record_id 不属于目标日期", kind="evidence")
            heads.sort(key=lambda row: (row["captured_at"], row["record_id"]))
        active_heads = [row for row in heads if row["status"] == "active"]

        interpretation_results: list[Mapping[str, Any]] = []
        receipts: list[InterpretationReceiptRevision] = []
        terminal_record_ids: set[str] = set()
        no_candidate_record_refs: dict[str, ObjectRef] = {}
        for head in active_heads:
            record_id = head["record_id"]
            current_ref = ObjectRef.from_dict(self.records.load_head_ref(record_id))
            receipt = self._receipt_head(record_id)
            if receipt is not None and receipt.status in {"original_only", "tombstone"}:
                # These are explicit, user-authoritative terminal outcomes.
                # They do not belong in the daily bundle and must not prevent
                # the remaining interpretable records from being integrated.
                terminal_record_ids.add(record_id)
                continue
            if receipt is not None and receipt.record_ref == current_ref:
                receipts.append(receipt)
                continue
            current_terminal = self.runtime.get_current_interpretation_terminal(
                record_id,
                feedback_watermark_sha256=action_watermark,
                target_objects=record_target_refs,
            )
            if current_terminal is not None:
                interpretation_results.append(
                    {
                        "status": "no_candidate",
                        "cached": True,
                        "request": {
                            "id": current_terminal["request_id"],
                            "record_ref": current_ref.to_dict(),
                        },
                        "run": {
                            "run_id": current_terminal["run_id"],
                            "status": "no_candidate",
                            "error_kind": None,
                            "receipt_ref": None,
                        },
                        "receipt": None,
                    }
                )
                no_candidate_record_refs[record_id] = current_ref
                continue
            request = self.runtime.create_interpretation_request(
                record_id,
                trigger="source_changed" if receipt is not None else "reconcile",
                feedback_watermark_sha256=action_watermark,
            )
            result = self.runtime.run_interpretation(
                request["id"], target_objects=record_target_refs
            )
            interpretation_results.append(result)
            result_request_id = request["id"]
            retry_request = self.runtime.create_known_invalid_retry_request(
                result,
                target_objects=record_target_refs,
            )
            if retry_request is not None:
                result = self.runtime.run_interpretation(
                    retry_request["id"], target_objects=record_target_refs
                )
                interpretation_results.append(result)
                result_request_id = retry_request["id"]
            if result.get("receipt") is not None:
                receipts.append(
                    InterpretationReceiptRevision.from_dict(result["receipt"])
                )
            elif result.get("status") == "no_candidate":
                # A valid no-candidate run is a terminal interpretation for the
                # exact current source revision.  It contributes no receipt or
                # source span to the Daily Integrator, but it must not make a
                # different, ready record wait forever.  Re-read the head after
                # the runtime returns: a cached terminal for an older source
                # revision is never allowed to exempt a newly edited record.
                returned_request = result.get("request")
                returned_run = result.get("run")
                latest_ref = ObjectRef.from_dict(
                    self.records.load_head_ref(record_id)
                )
                if (
                    isinstance(returned_request, Mapping)
                    and isinstance(returned_run, Mapping)
                    and type(result.get("cached")) is bool
                    and returned_request.get("id") == result_request_id
                    and returned_request.get("record_ref") == current_ref.to_dict()
                    and latest_ref == current_ref
                    and returned_run.get("status") == "no_candidate"
                    and returned_run.get("error_kind") is None
                    and returned_run.get("receipt_ref") is None
                ):
                    no_candidate_record_refs[record_id] = current_ref

        valid_receipts = [
            row
            for row in receipts
            if row.status in {"ready", "needs_review"}
            and row.record_ref
            == ObjectRef.from_dict(self.records.load_head_ref(row.record_ref.id))
        ]
        valid_receipts.sort(key=lambda row: row.receipt_id)
        receipt_refs = tuple(self._receipt_ref(row) for row in valid_receipts)
        record_ids_result = tuple(row["record_id"] for row in active_heads)
        no_candidate_record_ids = {
            record_id
            for record_id, terminal_ref in no_candidate_record_refs.items()
            if ObjectRef.from_dict(self.records.load_head_ref(record_id))
            == terminal_ref
        }
        eligible_record_ids = {
            row["record_id"]
            for row in active_heads
            if row["record_id"] not in terminal_record_ids
            and row["record_id"] not in no_candidate_record_ids
        }
        valid_receipt_record_ids = {row.record_ref.id for row in valid_receipts}
        if eligible_record_ids - valid_receipt_record_ids:
            # Daily integration is an all-eligible-record boundary.  A valid
            # subset would be internally hash-consistent, but it would publish
            # an incomplete daily summary and could feed partial evidence to
            # the long-term Agent.  Reuse the existing finite ``no_receipts``
            # status so Schedule, Orchestrator and Home contracts remain
            # compatible; no partial receipt refs cross this boundary.
            return DayPipelineResult(
                status="no_receipts",
                local_date=local_date,
                action_report=action_report,
                record_ids=record_ids_result,
                interpretation_results=tuple(interpretation_results),
                receipt_refs=(),
                daily_result=None,
                commit_result=None,
                material_brief=None,
            )
        if not valid_receipts:
            active_record_ids = {row["record_id"] for row in active_heads}
            all_terminal = bool(active_record_ids) and active_record_ids <= (
                terminal_record_ids | no_candidate_record_ids
            )
            return DayPipelineResult(
                status=(
                    "no_candidate"
                    if all_terminal and no_candidate_record_ids
                    else "no_change"
                    if all_terminal
                    else "no_receipts" if active_heads else "no_records"
                ),
                local_date=local_date,
                action_report=action_report,
                record_ids=record_ids_result,
                interpretation_results=tuple(interpretation_results),
                receipt_refs=(),
                daily_result=None,
                commit_result=None,
                material_brief=None,
            )

        source_spans: list[SourceSpan] = []
        seen_spans: set[str] = set()
        for receipt in valid_receipts:
            for span in receipt.source_spans:
                if span.sha256 not in seen_spans:
                    source_spans.append(span)
                    seen_spans.add(span.sha256)
        # CognitiveRuntime freezes manifests in the supplied order and the
        # formal store independently requires ID-sorted refs.  Ordering spans
        # by record identity makes both hashes agree without weakening either
        # validator.
        source_spans.sort(
            key=lambda row: (
                row.record_id,
                row.record_revision,
                row.line_start,
                row.line_end,
                row.quote_sha256,
            )
        )
        source_refs = tuple(
            sorted(
                {
                    ObjectRef(
                        "source_record",
                        span.record_id,
                        span.record_revision,
                        span.record_revision_sha256,
                    )
                    for span in source_spans
                },
                key=lambda row: (row.id, row.revision, row.revision_sha256),
            )
        )

        # A committed manifest is the cross-process idempotency boundary.  Do
        # this check before materializing the current formal-object catalogue:
        # the first successful bundle necessarily adds memories to that
        # catalogue, but those self-produced objects must not make an unchanged
        # replay look like new daily input and trigger another paid call.
        current_bundle = self.bundles.load_day_bundle_ref(local_date)
        current_manifest = self.bundles.load_day_manifest(local_date)
        frozen_hashes = {
            "source_manifest_sha256": sha256_bytes(
                canonical_json([ref.to_dict() for ref in source_refs]).encode("utf-8")
            ),
            "receipt_manifest_sha256": sha256_bytes(
                canonical_json([ref.to_dict() for ref in receipt_refs]).encode("utf-8")
            ),
            "profile_sha256": profile_sha256,
            "user_action_watermark_sha256": action_watermark,
            "policy_sha256": self.runtime.daily_policy_sha256,
        }
        replay_hashes = dict(frozen_hashes)
        if replay_profile_sha256 is not None:
            # A Day Orchestrator may have updated the Agent profile as the
            # downstream result of this exact bundle.  Permit that orchestrator
            # to match the bundle's original input profile for cache detection
            # only.  Any genuine source/receipt/action/policy change still
            # falls through and runs with the current profile_sha256 above.
            replay_hashes["profile_sha256"] = replay_profile_sha256
        if (
            current_bundle is not None
            and current_manifest is not None
            and current_manifest["input_hashes"] in (frozen_hashes, replay_hashes)
            and current_manifest["source_refs"]
            == [ref.to_dict() for ref in source_refs]
            and current_manifest["receipt_refs"]
            == [ref.to_dict() for ref in receipt_refs]
        ):
            commit_result = BundleCommitResult(
                status="no_change",
                bundle_ref=current_bundle,
                summary_ref=ObjectRef.from_dict(current_manifest["summary_ref"]),
                memory_refs=tuple(
                    ObjectRef.from_dict(row) for row in current_manifest["memory_refs"]
                ),
                relation_refs=tuple(
                    ObjectRef.from_dict(row) for row in current_manifest["relation_refs"]
                ),
            )
            evidence_refs = tuple(
                [
                    *source_refs,
                    *receipt_refs,
                    *commit_result.memory_refs,
                    *commit_result.relation_refs,
                ]
            )
            validate_long_term_evidence_refs(evidence_refs)
            payload = {
                "local_date": local_date,
                "bundle_ref": current_bundle.to_dict(),
                "evidence_refs": [row.to_dict() for row in evidence_refs],
                "material_change": False,
            }
            brief = MaterialBrief(
                local_date=local_date,
                bundle_ref=current_bundle,
                source_refs=source_refs,
                receipt_refs=receipt_refs,
                memory_refs=commit_result.memory_refs,
                relation_refs=commit_result.relation_refs,
                requires_long_term_review=False,
                material_sha256=sha256_bytes(
                    canonical_json(payload).encode("utf-8")
                ),
            )
            return DayPipelineResult(
                status="no_change",
                local_date=local_date,
                action_report=action_report,
                record_ids=record_ids_result,
                interpretation_results=tuple(interpretation_results),
                receipt_refs=receipt_refs,
                daily_result={
                    "status": "no_change",
                    "cached": True,
                    "reason": "committed_input_manifest_match",
                },
                commit_result=commit_result,
                material_brief=brief,
            )

        combined_objects: list[ObjectRef | Mapping[str, Any]] = []
        seen_objects: set[tuple[str, str, int, str]] = set()
        for raw in [*self._active_formal_refs(), *daily_object_refs]:
            ref = raw if isinstance(raw, ObjectRef) else ObjectRef.from_dict(raw)
            key = (ref.kind, ref.id, ref.revision, ref.revision_sha256)
            if key not in seen_objects:
                combined_objects.append(ref)
                seen_objects.add(key)
        context = {
            "record_count": len(active_heads),
            "receipt_count": len(valid_receipts),
            **dict(daily_context or {}),
        }
        daily_request = self.runtime.create_daily_request(local_date, trigger=trigger)
        daily_result = self.runtime.run_daily(
            daily_request["id"],
            source_spans=source_spans,
            object_refs=combined_objects,
            receipt_refs=receipt_refs,
            daily_context=context,
            profile_sha256=profile_sha256,
            user_action_watermark_sha256=action_watermark,
            inspect_memory=inspect_memory,
            search_history=search_history,
        )
        candidate = daily_result.get("candidate_bundle")
        if candidate is None:
            return DayPipelineResult(
                status=daily_result["status"],
                local_date=local_date,
                action_report=action_report,
                record_ids=record_ids_result,
                interpretation_results=tuple(interpretation_results),
                receipt_refs=receipt_refs,
                daily_result=daily_result,
                commit_result=None,
                material_brief=None,
            )

        bundle_revision = 1 if current_bundle is None else current_bundle.revision + 1
        memories, relations, materials = self._stage_and_materialize(
            candidate,
            receipt_refs=receipt_refs,
            bundle_revision=bundle_revision,
        )
        loaded_summary = self.bundles.load_daily_summary_head(local_date)
        previous_summary = None if loaded_summary is None else loaded_summary[0]
        summary = DailySummaryRevision(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_daily_summary_revision",
            summary_id=make_daily_summary_id(local_date),
            revision=1 if previous_summary is None else previous_summary.revision + 1,
            status="active",
            operation="generate" if previous_summary is None else "regenerate",
            created_at=_now_text(self.clock),
            local_date=local_date,
            overview=candidate["summary"]["overview"],
            themes=tuple(candidate["summary"]["themes"]),
            changes=tuple(candidate["summary"]["changes"]),
            unresolved_questions=tuple(candidate["summary"]["unresolved_questions"]),
            action_clues=tuple(candidate["summary"]["action_clues"]),
            source_refs=source_refs,
            receipt_refs=receipt_refs,
            review_file=f"Reviews/Daily/{local_date}.md",
            review_sha256=None,
            user_supplement_sha256=None,
            previous_revision_sha256=(
                None if previous_summary is None else previous_summary.sha256
            ),
        )
        input_hashes = {
            key: candidate["input_manifest"][key]
            for key in (
                "source_manifest_sha256",
                "receipt_manifest_sha256",
                "profile_sha256",
                "user_action_watermark_sha256",
                "policy_sha256",
            )
        }
        commit_result = self.bundles.commit_day_bundle(
            request_id=candidate["request_id"],
            run_id=candidate["run_id"],
            input_hashes=input_hashes,
            source_refs=source_refs,
            receipt_refs=receipt_refs,
            summary=summary,
            memories=memories,
            relations=relations,
            candidate_materializations=materials,
            expected_bundle_ref=current_bundle,
            now=self.clock(),
        )
        if commit_result.committed:
            for hook in self.post_commit_hooks:
                hook(summary, commit_result)

        evidence_refs = tuple(
            [*source_refs, *receipt_refs, *commit_result.memory_refs, *commit_result.relation_refs]
        )
        validate_long_term_evidence_refs(evidence_refs)
        material_payload = {
            "local_date": local_date,
            "bundle_ref": commit_result.bundle_ref.to_dict(),
            "evidence_refs": [row.to_dict() for row in evidence_refs],
            "material_change": bool(
                commit_result.committed
                and (commit_result.memory_refs or commit_result.relation_refs)
            ),
        }
        brief = MaterialBrief(
            local_date=local_date,
            bundle_ref=commit_result.bundle_ref,
            source_refs=source_refs,
            receipt_refs=receipt_refs,
            memory_refs=commit_result.memory_refs,
            relation_refs=commit_result.relation_refs,
            requires_long_term_review=(
                commit_result.committed
                and bool(commit_result.memory_refs or commit_result.relation_refs)
            ),
            material_sha256=sha256_bytes(
                canonical_json(material_payload).encode("utf-8")
            ),
        )
        return DayPipelineResult(
            status=commit_result.status,
            local_date=local_date,
            action_report=action_report,
            record_ids=record_ids_result,
            interpretation_results=tuple(interpretation_results),
            receipt_refs=receipt_refs,
            daily_result=daily_result,
            commit_result=commit_result,
            material_brief=brief,
        )


__all__ = ["CognitivePipeline", "DayPipelineResult", "MaterialBrief"]
