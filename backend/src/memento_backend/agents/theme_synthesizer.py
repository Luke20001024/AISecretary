"""L3: turn cross-day formal memory into a traceable Theme revision."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from memento_backend.agents.daily_integrator import memory_atom_ref, relation_ref
from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_json, validate_datetime
from memento_backend.policies.memory_policy import (
    THEME_POLICY_VERSION,
    THEME_PROMPT_VERSION,
    is_dormant,
    normalize_topic,
    theme_material_gate,
)
from memento_backend.providers.protocol import ProviderUsage

from .protocol import AgentRunContext, make_candidate


@dataclass(frozen=True)
class ThemeSynthesisInput:
    topic: str
    as_of: str
    atoms: Sequence[Mapping[str, Any]]
    relations: Sequence[Mapping[str, Any]]
    existing_theme: Optional[Mapping[str, Any]] = None

    def validate(self) -> None:
        if not self.topic.strip() or len(self.topic) > 80:
            raise ContractError("theme topic is invalid")
        try:
            as_of_date = dt.date.fromisoformat(self.as_of)
        except ValueError as exc:
            raise ContractError("theme as_of must be an ISO date") from exc
        matching = 0
        atom_ids = set()
        for atom in self.atoms:
            validate_contract("memory-atom-v2.schema.json", atom)
            atom_ids.add(str(atom["memory_atom_id"]))
            if dt.date.fromisoformat(str(atom["last_seen_on"])) > as_of_date:
                raise ContractError("theme input includes future memory", kind="evidence")
            if atom["status"] == "active" and normalize_topic(self.topic) in {normalize_topic(str(value)) for value in atom["topics"]}:
                matching += 1
        if matching < 1:
            raise ContractError("theme input requires at least one matching atom", kind="material_gate")
        relation_ids = set()
        for relation in self.relations:
            validate_contract("relation-v2.schema.json", relation)
            relation_ids.add(str(relation["relation_id"]))
        if self.existing_theme is not None:
            validate_contract("theme-v2.schema.json", self.existing_theme)
            expected_id = make_id("theme", "theme-topic-v1", {"topic": normalize_topic(self.topic)})
            if self.existing_theme["theme_id"] != expected_id:
                raise ContractError("existing theme identity differs from the topic", kind="evidence")
            existing_atom_ids = {
                str(ref["id"])
                for ref in [
                    *self.existing_theme["evidence_refs"],
                    *self.existing_theme["counterevidence_refs"],
                ]
            }
            if not existing_atom_ids.issubset(atom_ids):
                raise ContractError("existing theme evidence is missing from the bounded input", kind="evidence")
            existing_relation_ids = {str(ref["id"]) for ref in self.existing_theme["relation_refs"]}
            if not existing_relation_ids.issubset(relation_ids):
                raise ContractError("existing theme relations are missing from the bounded input", kind="evidence")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "topic": normalize_topic(self.topic), "as_of": self.as_of,
            "atom_refs": [memory_atom_ref(value) for value in self.atoms],
            "relation_refs": [relation_ref(value) for value in self.relations],
            "existing_theme_ref": None if self.existing_theme is None else theme_ref(self.existing_theme),
        }

    def source_refs(self) -> list[dict[str, Any]]:
        refs = [
            *[memory_atom_ref(value) for value in self.atoms],
            *[relation_ref(value) for value in self.relations],
        ]
        if self.existing_theme is not None:
            refs.append(theme_ref(self.existing_theme))
        return sorted(refs, key=lambda ref: (str(ref["kind"]), str(ref["id"])))


def theme_ref(value: Mapping[str, Any]) -> dict[str, Any]:
    return {"kind": "theme", "id": value["theme_id"], "revision": value["revision"], "revision_sha256": sha256_json(value)}


class ThemeSynthesizer:
    def evaluate(
        self,
        value: ThemeSynthesisInput,
        *,
        user_action_watermark_sha256: str,
        created_at: str,
    ) -> Mapping[str, Any]:
        value.validate()
        validate_datetime(created_at, "created_at")
        payload = value.canonical_payload()
        input_sha = sha256_json(payload)
        run_id = make_id("agent_run", THEME_PROMPT_VERSION, {"input_sha256": input_sha, "watermark": user_action_watermark_sha256, "created_at": created_at})
        context = AgentRunContext(
            run_id=run_id, agent_role="theme_synthesizer", prompt_version=THEME_PROMPT_VERSION,
            policy_version=THEME_POLICY_VERSION, input_sha256=input_sha,
            user_action_watermark_sha256=user_action_watermark_sha256, created_at=created_at,
            usage=ProviderUsage.deterministic(),
        )
        matching = [
            atom for atom in value.atoms
            if atom["status"] == "active" and normalize_topic(value.topic) in {normalize_topic(str(topic)) for topic in atom["topics"]}
        ]
        matching_ids = {str(atom["memory_atom_id"]) for atom in matching}
        relevant_relations = [
            relation for relation in value.relations
            if relation["status"] == "active"
            and str(relation["from_ref"]["id"]) in matching_ids
            and str(relation["to_ref"]["id"]) in matching_ids
        ]
        counter_ids = {
            str(relation["from_ref"]["id"])
            for relation in relevant_relations
            if relation["relation_type"] == "counterexample"
        }
        supporting = [atom for atom in matching if str(atom["memory_atom_id"]) not in counter_ids]
        gate = theme_material_gate(supporting, relevant_relations)
        if gate != "passed":
            return make_candidate(
                context, action="no_change", proposed_kind="none", proposed_object=None,
                source_refs=value.source_refs(), source_spans=[], reason_code=gate, confidence="high",
            )
        proposal, reason = self._proposal(value, supporting, matching, relevant_relations, counter_ids, created_at)
        if proposal is None:
            return make_candidate(
                context, action="no_change", proposed_kind="none", proposed_object=None,
                source_refs=value.source_refs(), source_spans=[], reason_code=reason, confidence="high",
            )
        action = "propose_create" if value.existing_theme is None else "propose_revise"
        return make_candidate(
            context, action=action, proposed_kind="theme", proposed_object=proposal,
            source_refs=value.source_refs(), source_spans=[], reason_code=reason, confidence="high",
        )

    @staticmethod
    def _proposal(
        value: ThemeSynthesisInput,
        supporting: Sequence[Mapping[str, Any]],
        matching: Sequence[Mapping[str, Any]],
        relations: Sequence[Mapping[str, Any]],
        counter_ids: set[str],
        created_at: str,
    ) -> tuple[Optional[dict[str, Any]], str]:
        existing = value.existing_theme
        evidence_refs = _sorted_unique_refs([
            *([] if existing is None else [ref for ref in existing["evidence_refs"] if str(ref["id"]) not in counter_ids]),
            *[memory_atom_ref(atom) for atom in supporting],
        ])
        counter_refs = _sorted_unique_refs([
            *([] if existing is None else existing["counterevidence_refs"]),
            *[memory_atom_ref(atom) for atom in matching if str(atom["memory_atom_id"]) in counter_ids],
        ])
        relation_refs = _sorted_unique_refs([
            *([] if existing is None else existing["relation_refs"]),
            *[relation_ref(relation) for relation in relations],
        ])
        by_id = {str(atom["memory_atom_id"]): atom for atom in matching}
        evidence_days = sorted({str(by_id[str(ref["id"])]["last_seen_on"]) for ref in evidence_refs})
        latest_day = max(evidence_days)
        dormant = is_dormant(last_seen_on=latest_day, as_of=value.as_of)
        lifecycle = "dormant" if dormant else ("tension" if counter_refs else "active")
        confidence = "stable" if len(evidence_days) >= 3 else "observed"
        existing_relation_ids = {
            str(ref["id"])
            for ref in ([] if existing is None else existing["relation_refs"])
        }
        new_relations = [
            relation for relation in relations
            if str(relation["relation_id"]) not in existing_relation_ids
        ]
        revises = [relation for relation in new_relations if relation["relation_type"] == "revises"]
        boundaries = [relation for relation in new_relations if relation["relation_type"] == "scope_boundary"]
        if existing is None:
            statement = f"围绕{value.topic.strip()}，同类判断已经跨日期反复出现：{supporting[-1]['statement']}"
            scope = "由跨日期、可回溯的正式记忆形成"
            reason = "跨日期记忆通过正式关系达到主题形成门槛"
            revision = 1
            previous = None
        else:
            statement = str(existing["statement"])
            scope = str(existing["scope"])
            reason = "新的正式记忆强化了当前主题"
            if revises:
                newest = max(supporting, key=lambda atom: str(atom["last_seen_on"]))
                statement = f"围绕{value.topic.strip()}，当前理解更新为：{newest['statement']}"
                reason = "新的修订关系改变了主题当前表述"
            if boundaries:
                scope = f"{scope}；新增边界：{boundaries[-1]['statement']}"[:600]
                reason = "新的范围边界收窄了主题适用范围"
            if counter_refs:
                reason = "新反例与既有支持证据同时存在"
            if existing["lifecycle"] == "dormant" and lifecycle == "active":
                reason = "新的跨日证据让休眠主题重新活跃"
            if lifecycle == "dormant" and existing["lifecycle"] != "dormant":
                reason = "长期没有新增正式证据，主题进入休眠"
            unchanged = (
                evidence_refs == existing["evidence_refs"]
                and counter_refs == existing["counterevidence_refs"]
                and relation_refs == existing["relation_refs"]
                and lifecycle == existing["lifecycle"]
                and confidence == existing["confidence"]
                and statement == existing["statement"]
                and scope == existing["scope"]
            )
            if unchanged:
                return None, "theme_has_no_material_change"
            revision = int(existing["revision"]) + 1
            previous = sha256_json(existing)
        proposal = {
            "schema_version": "2.0", "kind": "memento_theme_revision",
            "theme_id": make_id("theme", "theme-topic-v1", {"topic": normalize_topic(value.topic)}),
            "revision": revision, "previous_revision_sha256": previous,
            "title": _title(value.topic), "statement": statement[:800], "scope": scope[:600],
            "lifecycle": lifecycle, "confidence": confidence, "evidence_refs": evidence_refs,
            "evidence_days": evidence_days, "counterevidence_refs": counter_refs,
            "relation_refs": relation_refs, "change_reason": reason,
            "policy_version": THEME_POLICY_VERSION, "prompt_version": THEME_PROMPT_VERSION,
            "created_at": created_at, "committed_by": "workflow",
        }
        validate_contract("theme-v2.schema.json", proposal)
        return proposal, reason


def _title(value: str) -> str:
    title = value.strip()[:18]
    return title if len(title) >= 2 else f"{title}主题"


def _sorted_unique_refs(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_key = {(str(value["kind"]), str(value["id"])): dict(value) for value in values}
    return [by_key[key] for key in sorted(by_key)]
