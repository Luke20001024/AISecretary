"""Trusted L2 workflow for atomic MemoryAtom and Relation commits."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from memento_backend.agents.daily_integrator import DailyIntegrationInput, DailyIntegrator, memory_atom_ref
from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import sha256_json
from memento_backend.policies.memory_policy import DAILY_POLICY_VERSION, DAILY_PROMPT_VERSION
from memento_backend.storage.action_inbox import ActionInbox
from memento_backend.storage.revision_store import RevisionStore
from memento_backend.storage.run_ledger import RunLedger


class ConsolidateDayWorkflow:
    def __init__(self, revisions: RevisionStore, actions: ActionInbox, agent: DailyIntegrator, run_ledger: RunLedger) -> None:
        self.revisions = revisions
        self.actions = actions
        self.agent = agent
        self.run_ledger = run_ledger

    def consolidate(self, value: DailyIntegrationInput, *, created_at: str) -> Mapping[str, Any]:
        value.validate()
        self._assert_current_inputs(value)
        watermark = self.actions.current_watermark()
        candidate = self.agent.evaluate(value, user_action_watermark_sha256=watermark, created_at=created_at)
        validate_contract("agent-action-candidate-v1.schema.json", candidate)
        self._validate_candidate(candidate, value, watermark)
        if candidate["action"] == "no_change":
            self.run_ledger.record(candidate, terminal_status="no_change", finished_at=created_at)
            return candidate
        bundle = candidate["proposed_object"]
        values = [*bundle["memory_atoms"], *bundle["relations"]]
        expected_refs = []
        for atom in bundle["memory_atoms"]:
            if int(atom["revision"]) > 1:
                current = next(item for item in value.existing_atoms if item["memory_atom_id"] == atom["memory_atom_id"])
                expected_refs.append(memory_atom_ref(current))
        try:
            self.actions.assert_watermark(watermark)
            self._assert_current_inputs(value)
            refs = self.revisions.commit_many(values, expected_refs=expected_refs, committed_at=created_at)
        except ContractError:
            self.run_ledger.record(candidate, terminal_status="conflict", finished_at=created_at)
            raise
        self.run_ledger.record(candidate, terminal_status="committed", committed_refs=refs, finished_at=created_at)
        return {**dict(candidate), "committed_refs": refs}

    def _assert_current_inputs(self, value: DailyIntegrationInput) -> None:
        for ref in value.source_refs():
            if self.revisions.current_ref(str(ref["kind"]), str(ref["id"])) != ref:
                raise ContractError("daily integration input is not a current formal head", kind="conflict")

    @staticmethod
    def _validate_candidate(candidate: Mapping[str, Any], value: DailyIntegrationInput, watermark: str) -> None:
        if candidate["agent_role"] != "daily_integrator":
            raise ContractError("daily workflow received another Agent role", kind="authorization")
        if candidate["prompt_version"] != DAILY_PROMPT_VERSION or candidate["policy_version"] != DAILY_POLICY_VERSION:
            raise ContractError("daily candidate versions are unauthorized", kind="authorization")
        if candidate["input_sha256"] != sha256_json(value.canonical_payload()) or candidate["user_action_watermark_sha256"] != watermark:
            raise ContractError("daily candidate snapshot is stale", kind="conflict")
        if candidate["source_refs"] != value.source_refs():
            raise ContractError("daily candidate source authority expanded", kind="authorization")
        authorized_spans = {sha256_json(span) for item in value.interpretations for span in item["source_spans"]}
        if any(sha256_json(span) not in authorized_spans for span in candidate["source_spans"]):
            raise ContractError("daily candidate introduced an unauthorized span", kind="evidence")
        if candidate["action"] == "no_change":
            if candidate["proposed_object"] is not None:
                raise ContractError("daily no-change candidate contains objects")
            return
        if candidate["action"] != "propose_create" or candidate["proposed_kind"] != "daily_integration_bundle":
            raise ContractError("daily candidate exceeds L2 authority", kind="authorization")
        bundle = candidate["proposed_object"]
        validate_contract("daily-integration-candidate-v1.schema.json", bundle)
        if bundle["local_date"] != value.local_date or bundle["user_action_watermark_sha256"] != watermark:
            raise ContractError("daily bundle metadata differs from the frozen input", kind="conflict")
        interpretation_refs = {str(ref["id"]): ref for ref in value.source_refs() if ref["kind"] == "record_interpretation"}
        current_atom_refs = {str(ref["id"]): ref for ref in value.source_refs() if ref["kind"] == "memory_atom"}
        current_atoms = {str(atom["memory_atom_id"]): atom for atom in value.existing_atoms}
        proposed_atom_refs = {}
        for atom in bundle["memory_atoms"]:
            validate_contract("memory-atom-v2.schema.json", atom)
            if atom["last_seen_on"] != value.local_date:
                raise ContractError("daily atom escaped the target date", kind="authorization")
            current_atom = current_atoms.get(str(atom["memory_atom_id"]))
            historical_refs = {} if current_atom is None else {
                str(ref["id"]): ref for ref in current_atom["evidence_refs"]
            }
            for ref in atom["evidence_refs"]:
                if interpretation_refs.get(str(ref["id"])) != ref and historical_refs.get(str(ref["id"])) != ref:
                    raise ContractError("daily atom evidence is not a current interpretation", kind="evidence")
            historical_spans = set() if current_atom is None else {
                sha256_json(span) for span in current_atom["source_spans"]
            }
            if any(sha256_json(span) not in authorized_spans | historical_spans for span in atom["source_spans"]):
                raise ContractError("daily atom source span is unauthorized", kind="evidence")
            if int(atom["revision"]) > 1:
                current = current_atom_refs.get(str(atom["memory_atom_id"]))
                if current is None or current_atom is None or atom["previous_revision_sha256"] != current["revision_sha256"]:
                    raise ContractError("daily atom revision is stale", kind="conflict")
                historical_evidence_hashes = {sha256_json(ref) for ref in historical_refs.values()}
                if not historical_evidence_hashes.issubset({sha256_json(ref) for ref in atom["evidence_refs"]}):
                    raise ContractError("daily atom revision removed historical evidence", kind="evidence")
                if not historical_spans.issubset({sha256_json(span) for span in atom["source_spans"]}):
                    raise ContractError("daily atom revision removed historical source spans", kind="evidence")
            proposed_atom_refs[str(atom["memory_atom_id"])] = memory_atom_ref(atom)
        allowed_atoms = {**current_atom_refs, **proposed_atom_refs}
        current_relation_ids = {str(ref["id"]) for ref in value.source_refs() if ref["kind"] == "relation"}
        for relation in bundle["relations"]:
            validate_contract("relation-v2.schema.json", relation)
            if str(relation["relation_id"]) in current_relation_ids or relation["relation_type"] != "same_topic":
                raise ContractError("daily relation operation is unauthorized", kind="authorization")
            for endpoint in (relation["from_ref"], relation["to_ref"]):
                if allowed_atoms.get(str(endpoint["id"])) != endpoint:
                    raise ContractError("daily relation endpoint is stale", kind="evidence")
            if any(allowed_atoms.get(str(ref["id"])) != ref for ref in relation["evidence_refs"]):
                raise ContractError("daily relation evidence is stale", kind="evidence")
