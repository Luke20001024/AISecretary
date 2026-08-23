"""Trusted L3 material-gate and Theme commit workflow."""

from __future__ import annotations

from typing import Any, Mapping

from memento_backend.agents.theme_synthesizer import ThemeSynthesisInput, ThemeSynthesizer, theme_ref
from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import sha256_json
from memento_backend.policies.memory_policy import THEME_POLICY_VERSION, THEME_PROMPT_VERSION, theme_material_gate
from memento_backend.storage.action_inbox import ActionInbox
from memento_backend.storage.revision_store import RevisionStore
from memento_backend.storage.run_ledger import RunLedger


class UpdateThemeWorkflow:
    def __init__(self, revisions: RevisionStore, actions: ActionInbox, agent: ThemeSynthesizer, run_ledger: RunLedger) -> None:
        self.revisions = revisions
        self.actions = actions
        self.agent = agent
        self.run_ledger = run_ledger

    def update(self, value: ThemeSynthesisInput, *, created_at: str) -> Mapping[str, Any]:
        value.validate()
        self._assert_current(value)
        watermark = self.actions.current_watermark()
        candidate = self.agent.evaluate(value, user_action_watermark_sha256=watermark, created_at=created_at)
        validate_contract("agent-action-candidate-v1.schema.json", candidate)
        self._validate_candidate(candidate, value, watermark)
        if candidate["action"] == "no_change":
            self.run_ledger.record(candidate, terminal_status="no_change", finished_at=created_at)
            return candidate
        expected = None if value.existing_theme is None else theme_ref(value.existing_theme)
        try:
            self.actions.assert_watermark(watermark)
            self._assert_current(value)
            ref = self.revisions.commit(candidate["proposed_object"], expected_ref=expected, committed_at=created_at)
        except ContractError:
            self.run_ledger.record(candidate, terminal_status="conflict", finished_at=created_at)
            raise
        self.run_ledger.record(candidate, terminal_status="committed", committed_refs=(ref,), finished_at=created_at)
        return {**dict(candidate), "committed_ref": ref}

    def _assert_current(self, value: ThemeSynthesisInput) -> None:
        for ref in value.source_refs():
            if self.revisions.current_ref(str(ref["kind"]), str(ref["id"])) != ref:
                raise ContractError("theme synthesis input is not current", kind="conflict")

    @staticmethod
    def _validate_candidate(candidate: Mapping[str, Any], value: ThemeSynthesisInput, watermark: str) -> None:
        if candidate["agent_role"] != "theme_synthesizer":
            raise ContractError("theme workflow received another Agent role", kind="authorization")
        if candidate["prompt_version"] != THEME_PROMPT_VERSION or candidate["policy_version"] != THEME_POLICY_VERSION:
            raise ContractError("theme candidate versions are unauthorized", kind="authorization")
        if candidate["input_sha256"] != sha256_json(value.canonical_payload()) or candidate["user_action_watermark_sha256"] != watermark:
            raise ContractError("theme candidate snapshot is stale", kind="conflict")
        if candidate["source_refs"] != value.source_refs() or candidate["source_spans"]:
            raise ContractError("theme candidate source authority expanded", kind="authorization")
        if candidate["action"] == "no_change":
            if candidate["proposed_object"] is not None:
                raise ContractError("theme no-change candidate contains a formal object")
            return
        expected_action = "propose_create" if value.existing_theme is None else "propose_revise"
        if candidate["action"] != expected_action or candidate["proposed_kind"] != "theme":
            raise ContractError("theme candidate operation is unauthorized", kind="authorization")
        proposed = candidate["proposed_object"]
        validate_contract("theme-v2.schema.json", proposed)
        atom_by_id = {str(atom["memory_atom_id"]): atom for atom in value.atoms}
        relation_by_id = {str(relation["relation_id"]): relation for relation in value.relations}
        for ref in [*proposed["evidence_refs"], *proposed["counterevidence_refs"]]:
            atom = atom_by_id.get(str(ref["id"]))
            if atom is None or {"kind": "memory_atom", "id": atom["memory_atom_id"], "revision": atom["revision"], "revision_sha256": sha256_json(atom)} != ref:
                raise ContractError("theme evidence ref is stale", kind="evidence")
        for ref in proposed["relation_refs"]:
            relation = relation_by_id.get(str(ref["id"]))
            if relation is None or {"kind": "relation", "id": relation["relation_id"], "revision": relation["revision"], "revision_sha256": sha256_json(relation)} != ref:
                raise ContractError("theme relation ref is stale", kind="evidence")
        support_atoms = [atom_by_id[str(ref["id"])] for ref in proposed["evidence_refs"]]
        material_relations = [relation_by_id[str(ref["id"])] for ref in proposed["relation_refs"]]
        if theme_material_gate(support_atoms, material_relations) != "passed":
            raise ContractError("theme formal proposal does not pass the material gate", kind="material_gate")
        expected_days = sorted({str(atom["last_seen_on"]) for atom in support_atoms})
        if proposed["evidence_days"] != expected_days:
            raise ContractError("theme evidence days differ from formal atoms", kind="evidence")
        if value.existing_theme is None:
            if proposed["revision"] != 1 or proposed["previous_revision_sha256"] is not None:
                raise ContractError("new theme revision metadata is invalid", kind="conflict")
        else:
            if proposed["theme_id"] != value.existing_theme["theme_id"]:
                raise ContractError("theme identity changed", kind="conflict")
            if proposed["revision"] != int(value.existing_theme["revision"]) + 1 or proposed["previous_revision_sha256"] != sha256_json(value.existing_theme):
                raise ContractError("theme revision does not extend current head", kind="conflict")
