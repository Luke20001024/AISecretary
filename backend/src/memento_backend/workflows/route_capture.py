"""Commit one L0 routing decision and its deterministic resource side effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from memento_backend.agents.capture_understanding_agent import (
    CAPTURE_PROMPT_VERSION,
    CaptureInput,
    CaptureUnderstandingAgent,
    source_record_ref,
)
from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_json
from memento_backend.policies.capture_policy import CAPTURE_POLICY_VERSION
from memento_backend.storage.action_inbox import ActionInbox
from memento_backend.storage.revision_store import RevisionStore
from memento_backend.storage.run_ledger import RunLedger


@dataclass(frozen=True)
class CaptureWorkflowResult:
    candidate: Mapping[str, Any]
    committed_refs: tuple[Mapping[str, Any], ...]
    processing_route: Optional[str]


class CaptureWorkflow:
    """The only R5 component allowed to turn an L0 candidate into revisions."""

    def __init__(
        self,
        revisions: RevisionStore,
        actions: ActionInbox,
        agent: CaptureUnderstandingAgent,
        run_ledger: RunLedger,
    ) -> None:
        self.revisions = revisions
        self.actions = actions
        self.agent = agent
        self.run_ledger = run_ledger

    def route(self, value: CaptureInput, *, created_at: str) -> CaptureWorkflowResult:
        value.validate()
        record_ref = source_record_ref(value.source_record)
        current_ref = self.revisions.current_ref("source_record", str(record_ref["id"]))
        if current_ref != record_ref:
            raise ContractError("capture source record is not the current formal head", kind="conflict")
        watermark = self.actions.current_watermark()
        candidate = self.agent.evaluate(
            value,
            user_action_watermark_sha256=watermark,
            created_at=created_at,
        )
        validate_contract("agent-action-candidate-v1.schema.json", candidate)
        self._validate_candidate(candidate, value, record_ref, watermark)
        if candidate["action"] == "stop":
            self._record_run(candidate, "stopped", (), created_at)
            return CaptureWorkflowResult(candidate, (), None)

        decision = dict(candidate["proposed_object"])
        formal_values: list[Mapping[str, Any]] = [decision]
        resource = self._resource_for_route(value, decision, created_at=created_at)
        if resource is not None:
            formal_values.append(resource)
            read_later = self._read_later_for_route(value, decision, resource, created_at=created_at)
            if read_later is not None:
                formal_values.append(read_later)

        try:
            self.actions.assert_watermark(watermark)
            if self.revisions.current_ref("source_record", str(record_ref["id"])) != record_ref:
                raise ContractError("capture source changed before commit", kind="conflict")
            refs = self.revisions.commit_many(formal_values, committed_at=created_at)
        except ContractError:
            self._record_run(candidate, "conflict", (), created_at)
            raise
        self._record_run(candidate, "committed", tuple(refs), created_at)
        return CaptureWorkflowResult(candidate, tuple(refs), str(decision["processing_route"]))

    def _record_run(
        self,
        candidate: Mapping[str, Any],
        status: str,
        refs: tuple[Mapping[str, Any], ...],
        finished_at: str,
    ) -> None:
        self.run_ledger.record(candidate, terminal_status=status, committed_refs=refs, finished_at=finished_at)

    @staticmethod
    def _validate_candidate(
        candidate: Mapping[str, Any],
        value: CaptureInput,
        record_ref: Mapping[str, Any],
        watermark: str,
    ) -> None:
        expected_input_sha = sha256_json(value.canonical_payload())
        if candidate["agent_role"] != "capture_understanding":
            raise ContractError("capture workflow received another Agent role", kind="authorization")
        if candidate["prompt_version"] != CAPTURE_PROMPT_VERSION or candidate["policy_version"] != CAPTURE_POLICY_VERSION:
            raise ContractError("capture candidate versions are not authorized", kind="authorization")
        if candidate["input_sha256"] != expected_input_sha or candidate["user_action_watermark_sha256"] != watermark:
            raise ContractError("capture candidate input snapshot is stale", kind="conflict")
        if candidate["source_refs"] != [record_ref]:
            raise ContractError("capture candidate source authority expanded", kind="authorization")
        if candidate["usage"]["attempt_status"] in {"failed", "unknown"} and candidate["action"] != "stop":
            raise ContractError("uncertain provider attempt cannot commit", kind="authorization")
        if candidate["action"] == "stop":
            if candidate["proposed_object"] is not None:
                raise ContractError("stopped capture candidate contains a formal object")
            return
        if candidate["action"] != "propose_create" or candidate["proposed_kind"] != "capture_decision":
            raise ContractError("capture candidate action is outside L0 authority", kind="authorization")
        decision = candidate["proposed_object"]
        validate_contract("capture-decision-v1.schema.json", decision)
        if decision["source_record_ref"] != record_ref:
            raise ContractError("capture decision source ref is stale", kind="evidence")
        if decision["user_action_watermark_sha256"] != watermark:
            raise ContractError("capture decision watermark is stale", kind="conflict")
        if decision["prompt_version"] != CAPTURE_PROMPT_VERSION or decision["policy_version"] != CAPTURE_POLICY_VERSION:
            raise ContractError("capture decision versions are not authorized", kind="authorization")
        if candidate["source_spans"] != decision["user_signal_spans"]:
            raise ContractError("capture candidate spans differ from the decision", kind="evidence")
        for span in candidate["source_spans"]:
            if span["record_revision_sha256"] != record_ref["revision_sha256"] or span["quote"] not in value.authorized_text:
                raise ContractError("capture source span cannot be reverified", kind="evidence")

    @staticmethod
    def _resource_for_route(
        value: CaptureInput,
        decision: Mapping[str, Any],
        *,
        created_at: str,
    ) -> Optional[Mapping[str, Any]]:
        route = str(decision["processing_route"])
        if route not in {"resource_index", "ask_on_use", "resource_index_and_interpret"}:
            return None
        record = value.source_record
        record_ref = source_record_ref(record)
        source_type = str(record["source_type"])
        resource_type = {
            "url": "web_page",
            "web_page": "web_page",
            "screenshot_ocr": "screenshot",
            "image_note": "image",
            "file_note": "file",
            "external_trace": "conversation",
        }.get(source_type, "file")
        resource = {
            "schema_version": "1.0",
            "kind": "memento_resource_card_revision",
            "resource_id": make_id("resource_card", "resource-card-v1", {"source_record_ref": record_ref}),
            "revision": 1,
            "previous_revision_sha256": None,
            "status": "active",
            "operation": "index",
            "source_record_ref": record_ref,
            "resource_type": resource_type,
            "url": value.resource_url,
            "title": value.resource_title or value.resource_url or f"{record['source_app']} 资料",
            "local_asset_refs": [dict(item) for item in record["attachments"]],
            "ocr_index_ref": None,
            "user_selected_spans": [dict(item) for item in decision["user_signal_spans"]],
            "user_note": value.user_note,
            "processing_route": "ask_on_use" if route == "ask_on_use" else "resource_index",
            "created_at": created_at,
            "committed_by": "workflow",
        }
        validate_contract("resource-card-v1.schema.json", resource)
        return resource

    @staticmethod
    def _read_later_for_route(
        value: CaptureInput,
        decision: Mapping[str, Any],
        resource: Mapping[str, Any],
        *,
        created_at: str,
    ) -> Optional[Mapping[str, Any]]:
        if decision["processing_route"] != "ask_on_use":
            return None
        resource_ref = {
            "kind": "resource_card",
            "id": resource["resource_id"],
            "revision": resource["revision"],
            "revision_sha256": sha256_json(resource),
        }
        intent = {
            "schema_version": "1.0",
            "kind": "memento_read_later_intent_revision",
            "intent_id": make_id("read_later_intent", "read-later-intent-v1", {"resource_ref": resource_ref}),
            "revision": 1,
            "previous_revision_sha256": None,
            "status": "open",
            "operation": "create",
            "resource_ref": resource_ref,
            "intent_type": "read_later",
            "user_note": value.user_note,
            "created_at": created_at,
            "committed_by": "workflow",
        }
        validate_contract("read-later-intent-v1.schema.json", intent)
        return intent
