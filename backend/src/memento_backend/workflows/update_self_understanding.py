"""Trusted L4 workflow for SelfInsight material gate and revision commits."""

from __future__ import annotations

from typing import Any, Mapping

from memento_backend.agents.self_understanding_agent import (
    SelfUnderstandingAgent,
    SelfUnderstandingInput,
    self_insight_ref,
)
from memento_backend.agents.theme_synthesizer import theme_ref
from memento_backend.agents.daily_integrator import memory_atom_ref
from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import sha256_json
from memento_backend.policies.self_policy import (
    SELF_POLICY_VERSION,
    SELF_PROMPT_VERSION,
    self_material_gate,
    sensitive_inference_reason,
)
from memento_backend.storage.action_inbox import ActionInbox
from memento_backend.storage.revision_store import RevisionStore
from memento_backend.storage.run_ledger import RunLedger


class UpdateSelfUnderstandingWorkflow:
    def __init__(self, revisions: RevisionStore, actions: ActionInbox, agent: SelfUnderstandingAgent, run_ledger: RunLedger) -> None:
        self.revisions = revisions
        self.actions = actions
        self.agent = agent
        self.run_ledger = run_ledger

    def update(self, value: SelfUnderstandingInput, *, created_at: str) -> Mapping[str, Any]:
        value.validate()
        self._assert_current(value)
        watermark = self.actions.current_watermark()
        candidate = self.agent.evaluate(value, user_action_watermark_sha256=watermark, created_at=created_at)
        validate_contract("agent-action-candidate-v1.schema.json", candidate)
        self._validate_candidate(candidate, value, watermark)
        if candidate["action"] in {"no_change", "stop"}:
            terminal = "no_change" if candidate["action"] == "no_change" else "stopped"
            self.run_ledger.record(candidate, terminal_status=terminal, finished_at=created_at)
            return candidate
        expected = None if value.existing_insight is None else self_insight_ref(value.existing_insight)
        try:
            self.actions.assert_watermark(watermark)
            self._assert_current(value)
            ref = self.revisions.commit(candidate["proposed_object"], expected_ref=expected, committed_at=created_at)
        except ContractError:
            self.run_ledger.record(candidate, terminal_status="conflict", finished_at=created_at)
            raise
        self.run_ledger.record(candidate, terminal_status="committed", committed_refs=(ref,), finished_at=created_at)
        return {**dict(candidate), "committed_ref": ref}

    def _assert_current(self, value: SelfUnderstandingInput) -> None:
        for ref in value.source_refs():
            if self.revisions.current_ref(str(ref["kind"]), str(ref["id"])) != ref:
                raise ContractError("self understanding input is not current", kind="conflict")

    @staticmethod
    def _validate_candidate(candidate: Mapping[str, Any], value: SelfUnderstandingInput, watermark: str) -> None:
        if candidate["agent_role"] != "self_understanding":
            raise ContractError("self workflow received another Agent role", kind="authorization")
        if candidate["prompt_version"] != SELF_PROMPT_VERSION or candidate["policy_version"] != SELF_POLICY_VERSION:
            raise ContractError("self candidate versions are unauthorized", kind="authorization")
        if candidate["input_sha256"] != sha256_json(value.canonical_payload()) or candidate["user_action_watermark_sha256"] != watermark:
            raise ContractError("self candidate snapshot is stale", kind="conflict")
        if candidate["source_refs"] != value.source_refs() or candidate["source_spans"]:
            raise ContractError("self candidate source authority expanded", kind="authorization")
        if candidate["action"] in {"no_change", "stop"}:
            if candidate["proposed_object"] is not None:
                raise ContractError("stopped self candidate contains a formal object")
            return
        expected_action = "propose_create" if value.existing_insight is None else "propose_revise"
        if candidate["action"] != expected_action or candidate["proposed_kind"] != "self_insight":
            raise ContractError("self candidate operation is unauthorized", kind="authorization")
        proposed = candidate["proposed_object"]
        validate_contract("self-insight-v2.schema.json", proposed)
        theme_by_id = {str(theme["theme_id"]): theme for theme in value.themes}
        atom_by_id = {
            str(atom["memory_atom_id"]): atom
            for atom in [*value.support_atoms, *value.boundary_atoms]
        }
        for ref in proposed["theme_refs"]:
            theme = theme_by_id.get(str(ref["id"]))
            if theme is None or theme_ref(theme) != ref:
                raise ContractError("self insight theme ref is stale", kind="evidence")
        for ref in [*proposed["support_refs"], *proposed["boundary_refs"]]:
            current = theme_by_id.get(str(ref["id"])) if ref["kind"] == "theme" else atom_by_id.get(str(ref["id"]))
            expected_ref = None
            if current is not None:
                expected_ref = theme_ref(current) if ref["kind"] == "theme" else memory_atom_ref(current)
            if expected_ref != ref:
                raise ContractError("self insight support ref is stale", kind="evidence")
        if proposed["maturity"] != "dormant" and self_material_gate([
            theme_by_id[str(ref["id"])] for ref in proposed["theme_refs"]
        ]) != "passed":
            raise ContractError("self insight does not pass the cross-theme material gate", kind="material_gate")
        sensitive_reason = sensitive_inference_reason([
            str(proposed["title"]), str(proposed["statement"]), str(proposed["scope"]), str(proposed["uncertainty"]),
        ])
        if sensitive_reason is not None or proposed["sensitivity"] != "normal":
            raise ContractError("unconfirmed sensitive self inference cannot be committed", kind="authorization")
        if proposed["confirmation"] not in {"draft", "observed"} or proposed["visibility"] != "local_only":
            raise ContractError("Agent cannot confirm or externally expose a self insight", kind="authorization")
        if value.existing_insight is None:
            if proposed["revision"] != 1 or proposed["previous_revision_sha256"] is not None:
                raise ContractError("new self insight revision metadata is invalid", kind="conflict")
        else:
            if proposed["insight_id"] != value.existing_insight["insight_id"]:
                raise ContractError("self insight identity changed", kind="conflict")
            if proposed["revision"] != int(value.existing_insight["revision"]) + 1 or proposed["previous_revision_sha256"] != sha256_json(value.existing_insight):
                raise ContractError("self insight revision does not extend current head", kind="conflict")
            if value.existing_insight["confirmation"] == "user_confirmed":
                raise ContractError("Agent cannot overwrite a user-confirmed self insight", kind="authorization")
