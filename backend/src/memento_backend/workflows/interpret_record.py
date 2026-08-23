"""Trusted workflow for committing one authorized L1 interpretation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from memento_backend.agents.capture_understanding_agent import source_record_ref
from memento_backend.agents.record_interpreter import (
    InterpretationInput,
    RecordInterpreter,
    capture_decision_ref,
)
from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import sha256_json
from memento_backend.policies.interpretation_policy import INTERPRETATION_POLICY_VERSION, INTERPRETATION_PROMPT_VERSION
from memento_backend.storage.action_inbox import ActionInbox
from memento_backend.storage.revision_store import RevisionStore
from memento_backend.storage.run_ledger import RunLedger


@dataclass(frozen=True)
class InterpretationWorkflowResult:
    candidate: Mapping[str, Any]
    committed_ref: Optional[Mapping[str, Any]]


class InterpretationWorkflow:
    def __init__(
        self,
        revisions: RevisionStore,
        actions: ActionInbox,
        agent: RecordInterpreter,
        run_ledger: RunLedger,
    ) -> None:
        self.revisions = revisions
        self.actions = actions
        self.agent = agent
        self.run_ledger = run_ledger

    def interpret(self, value: InterpretationInput, *, created_at: str) -> InterpretationWorkflowResult:
        value.validate()
        record_ref = source_record_ref(value.source_record)
        decision_ref = capture_decision_ref(value.capture_decision)
        if self.revisions.current_ref("source_record", str(record_ref["id"])) != record_ref:
            raise ContractError("interpretation source record is not current", kind="conflict")
        if self.revisions.current_ref("capture_decision", str(decision_ref["id"])) != decision_ref:
            raise ContractError("interpretation capture decision is not current", kind="conflict")
        watermark = self.actions.current_watermark()
        candidate = self.agent.evaluate(value, user_action_watermark_sha256=watermark, created_at=created_at)
        validate_contract("agent-action-candidate-v1.schema.json", candidate)
        self._validate_candidate(candidate, value, record_ref, decision_ref, watermark)
        if candidate["action"] == "stop":
            self._record(candidate, "stopped", (), created_at)
            return InterpretationWorkflowResult(candidate, None)
        try:
            self.actions.assert_watermark(watermark)
            if self.revisions.current_ref("source_record", str(record_ref["id"])) != record_ref:
                raise ContractError("interpretation source changed before commit", kind="conflict")
            if self.revisions.current_ref("capture_decision", str(decision_ref["id"])) != decision_ref:
                raise ContractError("interpretation route changed before commit", kind="conflict")
            committed_ref = self.revisions.commit(candidate["proposed_object"], committed_at=created_at)
        except ContractError:
            self._record(candidate, "conflict", (), created_at)
            raise
        self._record(candidate, "committed", (committed_ref,), created_at)
        return InterpretationWorkflowResult(candidate, committed_ref)

    def _record(
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
        value: InterpretationInput,
        record_ref: Mapping[str, Any],
        decision_ref: Mapping[str, Any],
        watermark: str,
    ) -> None:
        if candidate["agent_role"] != "record_interpreter":
            raise ContractError("interpretation workflow received another Agent role", kind="authorization")
        if candidate["prompt_version"] != INTERPRETATION_PROMPT_VERSION or candidate["policy_version"] != INTERPRETATION_POLICY_VERSION:
            raise ContractError("interpretation candidate versions are unauthorized", kind="authorization")
        if candidate["input_sha256"] != sha256_json(value.canonical_payload()):
            raise ContractError("interpretation candidate input snapshot differs", kind="conflict")
        if candidate["user_action_watermark_sha256"] != watermark:
            raise ContractError("interpretation candidate watermark is stale", kind="conflict")
        if candidate["source_refs"] != [record_ref, decision_ref]:
            raise ContractError("interpretation source authority expanded", kind="authorization")
        if candidate["source_spans"] != value.capture_decision["user_signal_spans"]:
            raise ContractError("interpretation spans differ from L0 authority", kind="evidence")
        if candidate["usage"]["attempt_status"] in {"failed", "unknown"} and candidate["action"] != "stop":
            raise ContractError("uncertain provider attempt cannot commit", kind="authorization")
        if candidate["action"] == "stop":
            if candidate["proposed_object"] is not None:
                raise ContractError("stopped interpretation contains a formal object")
            return
        if candidate["action"] != "propose_create" or candidate["proposed_kind"] != "record_interpretation":
            raise ContractError("interpretation candidate exceeds L1 authority", kind="authorization")
        proposed = candidate["proposed_object"]
        validate_contract("record-interpretation-v2.schema.json", proposed)
        if proposed["source_record_ref"] != record_ref or proposed["capture_decision_ref"] != decision_ref:
            raise ContractError("interpretation formal refs differ from current inputs", kind="evidence")
        if proposed["source_spans"] != candidate["source_spans"]:
            raise ContractError("interpretation formal spans differ from candidate", kind="evidence")
