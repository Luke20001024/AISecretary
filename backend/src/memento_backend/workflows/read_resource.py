"""Read-only workflow for cited resource answers."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from memento_backend.agents.capture_understanding_agent import source_record_ref
from memento_backend.agents.resource_reader import ResourceReadInput, ResourceReader, resource_card_ref
from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import sha256_json
from memento_backend.policies.resource_policy import RESOURCE_READER_POLICY_VERSION, RESOURCE_READER_PROMPT_VERSION
from memento_backend.storage.action_inbox import ActionInbox
from memento_backend.storage.revision_store import RevisionStore
from memento_backend.storage.run_ledger import RunLedger


class ResourceReadWorkflow:
    """Return a cited result without creating or revising formal knowledge."""

    def __init__(
        self,
        revisions: RevisionStore,
        actions: ActionInbox,
        agent: ResourceReader,
        run_ledger: RunLedger,
    ) -> None:
        self.revisions = revisions
        self.actions = actions
        self.agent = agent
        self.run_ledger = run_ledger

    def read(self, value: ResourceReadInput, *, created_at: str) -> Mapping[str, Any]:
        value.validate()
        resource_ref = resource_card_ref(value.resource_card)
        record_ref = source_record_ref(value.source_record)
        if self.revisions.current_ref("resource_card", str(resource_ref["id"])) != resource_ref:
            raise ContractError("resource reader resource is not current", kind="conflict")
        if self.revisions.current_ref("source_record", str(record_ref["id"])) != record_ref:
            raise ContractError("resource reader source is not current", kind="conflict")
        watermark = self.actions.current_watermark()
        candidate = self.agent.evaluate(value, user_action_watermark_sha256=watermark, created_at=created_at)
        validate_contract("agent-action-candidate-v1.schema.json", candidate)
        self._validate_candidate(candidate, value, resource_ref, record_ref, watermark)
        if candidate["action"] == "stop":
            self.run_ledger.record(candidate, terminal_status="stopped", finished_at=created_at)
            return candidate
        self.actions.assert_watermark(watermark)
        if self.revisions.current_ref("resource_card", str(resource_ref["id"])) != resource_ref:
            raise ContractError("resource changed before answer return", kind="conflict")
        self.run_ledger.record(candidate, terminal_status="returned", finished_at=created_at)
        return candidate

    @staticmethod
    def _validate_candidate(
        candidate: Mapping[str, Any],
        value: ResourceReadInput,
        resource_ref: Mapping[str, Any],
        record_ref: Mapping[str, Any],
        watermark: str,
    ) -> None:
        if candidate["agent_role"] != "resource_reader":
            raise ContractError("resource workflow received another Agent role", kind="authorization")
        if candidate["prompt_version"] != RESOURCE_READER_PROMPT_VERSION or candidate["policy_version"] != RESOURCE_READER_POLICY_VERSION:
            raise ContractError("resource reader versions are unauthorized", kind="authorization")
        if candidate["input_sha256"] != sha256_json(value.canonical_payload()) or candidate["user_action_watermark_sha256"] != watermark:
            raise ContractError("resource reader candidate snapshot is stale", kind="conflict")
        if candidate["source_refs"] != [resource_ref, record_ref]:
            raise ContractError("resource reader source authority expanded", kind="authorization")
        if candidate["action"] == "stop":
            if candidate["proposed_object"] is not None:
                raise ContractError("stopped resource reader contains a result")
            return
        if candidate["action"] != "propose_create" or candidate["proposed_kind"] != "resource_read_result":
            raise ContractError("resource reader candidate exceeds read authority", kind="authorization")
        if candidate["usage"]["attempt_status"] != "succeeded":
            raise ContractError("resource result requires a successful provider attempt", kind="authorization")
        result = candidate["proposed_object"]
        validate_contract("resource-read-result-v1.schema.json", result)
        if result["resource_ref"] != resource_ref or result["source_record_ref"] != record_ref:
            raise ContractError("resource result refs are stale", kind="evidence")
        if result["citations"] != candidate["source_spans"]:
            raise ContractError("resource result citations differ from candidate", kind="evidence")
        for span in result["citations"]:
            if span["record_revision_sha256"] != record_ref["revision_sha256"] or span["quote"] not in value.authorized_text:
                raise ContractError("resource result citation cannot be reverified", kind="evidence")
