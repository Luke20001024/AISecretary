"""L2: materialize usable daily memory without producing a daily persona."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from memento_backend.agents.capture_understanding_agent import source_record_ref
from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_json, validate_datetime
from memento_backend.policies.memory_policy import (
    DAILY_POLICY_VERSION,
    DAILY_PROMPT_VERSION,
    normalize_topic,
    shared_topics,
)
from memento_backend.providers.protocol import ProviderUsage

from .protocol import AgentRunContext, make_candidate


@dataclass(frozen=True)
class DailyIntegrationInput:
    local_date: str
    source_records: Sequence[Mapping[str, Any]]
    interpretations: Sequence[Mapping[str, Any]]
    existing_atoms: Sequence[Mapping[str, Any]] = ()
    existing_relations: Sequence[Mapping[str, Any]] = ()

    def validate(self) -> None:
        records = {}
        for record in self.source_records:
            validate_contract("source-record-v2.schema.json", record)
            if record["status"] != "active" or record["local_date"] != self.local_date:
                raise ContractError("daily integration record is outside the target day", kind="authorization")
            records[record["record_id"]] = record
        if not records:
            raise ContractError("daily integration requires source records")
        for interpretation in self.interpretations:
            validate_contract("record-interpretation-v2.schema.json", interpretation)
            matched_record = records.get(interpretation["source_record_ref"]["id"])
            if matched_record is None or interpretation["source_record_ref"] != source_record_ref(matched_record):
                raise ContractError("daily interpretation source is outside the target day", kind="evidence")
        for atom in self.existing_atoms:
            validate_contract("memory-atom-v2.schema.json", atom)
        for relation in self.existing_relations:
            validate_contract("relation-v2.schema.json", relation)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "local_date": self.local_date,
            "source_record_refs": [source_record_ref(value) for value in self.source_records],
            "interpretation_refs": [interpretation_ref(value) for value in self.interpretations],
            "existing_atom_refs": [memory_atom_ref(value) for value in self.existing_atoms],
            "existing_relation_refs": [relation_ref(value) for value in self.existing_relations],
        }

    def source_refs(self) -> list[dict[str, Any]]:
        refs = [
            *[source_record_ref(value) for value in self.source_records],
            *[interpretation_ref(value) for value in self.interpretations],
            *[memory_atom_ref(value) for value in self.existing_atoms],
            *[relation_ref(value) for value in self.existing_relations],
        ]
        return sorted(refs, key=lambda ref: (str(ref["kind"]), str(ref["id"])))


def interpretation_ref(value: Mapping[str, Any]) -> dict[str, Any]:
    return {"kind": "record_interpretation", "id": value["interpretation_id"], "revision": value["revision"], "revision_sha256": sha256_json(value)}


def memory_atom_ref(value: Mapping[str, Any]) -> dict[str, Any]:
    return {"kind": "memory_atom", "id": value["memory_atom_id"], "revision": value["revision"], "revision_sha256": sha256_json(value)}


def relation_ref(value: Mapping[str, Any]) -> dict[str, Any]:
    return {"kind": "relation", "id": value["relation_id"], "revision": value["revision"], "revision_sha256": sha256_json(value)}


class DailyIntegrator:
    def evaluate(
        self,
        value: DailyIntegrationInput,
        *,
        user_action_watermark_sha256: str,
        created_at: str,
    ) -> Mapping[str, Any]:
        value.validate()
        validate_datetime(created_at, "created_at")
        payload = value.canonical_payload()
        input_sha = sha256_json(payload)
        run_id = make_id("agent_run", DAILY_PROMPT_VERSION, {"input_sha256": input_sha, "watermark": user_action_watermark_sha256, "created_at": created_at})
        context = AgentRunContext(
            run_id=run_id,
            agent_role="daily_integrator",
            prompt_version=DAILY_PROMPT_VERSION,
            policy_version=DAILY_POLICY_VERSION,
            input_sha256=input_sha,
            user_action_watermark_sha256=user_action_watermark_sha256,
            created_at=created_at,
            usage=ProviderUsage.deterministic(),
        )
        atoms = self._materialize_atoms(value, created_at)
        relations = self._connect_atoms(value, atoms, created_at)
        spans = self._source_spans(value.interpretations)
        if not atoms and not relations:
            return make_candidate(
                context, action="no_change", proposed_kind="none", proposed_object=None,
                source_refs=value.source_refs(), source_spans=spans,
                reason_code="no_usable_daily_memory", confidence="high",
            )
        bundle = {
            "schema_version": "1.0", "kind": "memento_daily_integration_candidate",
            "local_date": value.local_date, "memory_atoms": atoms, "relations": relations,
            "prompt_version": DAILY_PROMPT_VERSION, "policy_version": DAILY_POLICY_VERSION,
            "user_action_watermark_sha256": user_action_watermark_sha256, "created_at": created_at,
        }
        validate_contract("daily-integration-candidate-v1.schema.json", bundle)
        return make_candidate(
            context, action="propose_create", proposed_kind="daily_integration_bundle", proposed_object=bundle,
            source_refs=value.source_refs(), source_spans=spans,
            reason_code="daily_memory_materialized", confidence="high",
        )

    @staticmethod
    def _materialize_atoms(value: DailyIntegrationInput, created_at: str) -> list[dict[str, Any]]:
        existing_by_statement = {
            str(atom["statement"]).strip().casefold(): atom
            for atom in value.existing_atoms
            if atom["status"] == "active"
        }
        results: list[dict[str, Any]] = []
        for interpretation in sorted(value.interpretations, key=lambda item: str(item["interpretation_id"])):
            if interpretation["status"] not in {"ready", "needs_review"} or not interpretation["topics"] or interpretation["summary"] is None:
                continue
            ref = interpretation_ref(interpretation)
            statement = str(interpretation["summary"]).strip()
            existing = existing_by_statement.get(statement.casefold())
            if existing is not None:
                if ref in existing["evidence_refs"]:
                    continue
                revised = {
                    **dict(existing),
                    "revision": int(existing["revision"]) + 1,
                    "previous_revision_sha256": sha256_json(existing),
                    "operation": "reinforce",
                    "topics": _unique([*existing["topics"], *interpretation["topics"]]),
                    "purposes": _unique([*existing["purposes"], *interpretation["purposes"]]),
                    "evidence_refs": [*existing["evidence_refs"], ref],
                    "source_spans": _unique_dicts([*existing["source_spans"], *interpretation["source_spans"]]),
                    "last_seen_on": value.local_date,
                    "change_reason": "同一记忆在新的记录中再次出现",
                    "policy_version": DAILY_POLICY_VERSION,
                    "created_at": created_at,
                    "committed_by": "workflow",
                }
                validate_contract("memory-atom-v2.schema.json", revised)
                results.append(revised)
                continue
            atom = {
                "schema_version": "2.0", "kind": "memento_memory_atom_revision",
                "memory_atom_id": make_id("memory_atom", "memory-atom-v2", {"interpretation_ref": ref}),
                "revision": 1, "previous_revision_sha256": None, "status": "active", "operation": "materialize",
                "statement": statement, "memory_kind": _memory_kind(interpretation["content_types"]),
                "topics": list(interpretation["topics"]), "purposes": list(interpretation["purposes"]),
                "uncertainty": interpretation["uncertainty"], "evidence_refs": [ref],
                "source_spans": [dict(span) for span in interpretation["source_spans"]],
                "first_seen_on": value.local_date, "last_seen_on": value.local_date,
                "change_reason": "逐条理解在日级整理中成为可继续关联的记忆",
                "policy_version": DAILY_POLICY_VERSION, "created_at": created_at, "committed_by": "workflow",
            }
            validate_contract("memory-atom-v2.schema.json", atom)
            results.append(atom)
            existing_by_statement[statement.casefold()] = atom
        return results

    @staticmethod
    def _connect_atoms(
        value: DailyIntegrationInput,
        proposed_atoms: Sequence[Mapping[str, Any]],
        created_at: str,
    ) -> list[dict[str, Any]]:
        existing_pairs = {
            tuple(sorted((str(relation["from_ref"]["id"]), str(relation["to_ref"]["id"]))))
            for relation in value.existing_relations
            if relation["status"] == "active" and relation["relation_type"] == "same_topic"
        }
        revised_ids = {str(atom["memory_atom_id"]) for atom in proposed_atoms if int(atom["revision"]) > 1}
        pool = [atom for atom in value.existing_atoms if atom["status"] == "active" and str(atom["memory_atom_id"]) not in revised_ids]
        pool.extend(proposed_atoms)
        new_ids = {str(atom["memory_atom_id"]) for atom in proposed_atoms if int(atom["revision"]) == 1}
        results: list[dict[str, Any]] = []
        for index, left in enumerate(pool):
            for right in pool[index + 1:]:
                left_id = str(left["memory_atom_id"])
                right_id = str(right["memory_atom_id"])
                pair = tuple(sorted((left_id, right_id)))
                if pair in existing_pairs or not new_ids.intersection(pair):
                    continue
                topics = shared_topics(left, right)
                if not topics:
                    continue
                first, second = (left, right) if left_id < right_id else (right, left)
                first_ref = memory_atom_ref(first)
                second_ref = memory_atom_ref(second)
                relation = {
                    "schema_version": "2.0", "kind": "memento_relation_revision",
                    "relation_id": make_id("relation", "same-topic-v1", {"from_ref": first_ref, "to_ref": second_ref}),
                    "revision": 1, "previous_revision_sha256": None, "status": "active", "operation": "materialize",
                    "relation_type": "same_topic", "direction": "undirected", "from_ref": first_ref, "to_ref": second_ref,
                    "statement": f"两条记忆共同涉及{topics[0]}", "confidence": "high",
                    "evidence_refs": [first_ref, second_ref], "change_reason": "同类记忆跨记录再次出现",
                    "policy_version": DAILY_POLICY_VERSION, "created_at": created_at, "committed_by": "workflow",
                }
                validate_contract("relation-v2.schema.json", relation)
                results.append(relation)
                existing_pairs.add(pair)
        return results

    @staticmethod
    def _source_spans(interpretations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return _unique_dicts([span for interpretation in interpretations for span in interpretation["source_spans"]])


def _memory_kind(content_types: Sequence[Any]) -> str:
    for value in ("decision", "action", "question", "experience", "learning"):
        if value in content_types:
            return value
    return "judgment"


def _unique(values: Sequence[Any]) -> list[Any]:
    return list(dict.fromkeys(values))


def _unique_dicts(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen = set()
    for value in values:
        key = sha256_json(value)
        if key not in seen:
            result.append(dict(value))
            seen.add(key)
    return result
