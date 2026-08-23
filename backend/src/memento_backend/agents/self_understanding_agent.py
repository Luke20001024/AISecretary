"""L4: synthesize a bounded, revisable understanding from multiple Themes."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from memento_backend.agents.daily_integrator import memory_atom_ref
from memento_backend.agents.theme_synthesizer import theme_ref
from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_json, validate_datetime
from memento_backend.policies.self_policy import (
    SELF_POLICY_VERSION,
    SELF_PROMPT_VERSION,
    normalize_insight_key,
    self_material_gate,
    sensitive_inference_reason,
)
from memento_backend.providers.protocol import ProviderUsage

from .protocol import AgentRunContext, make_candidate


@dataclass(frozen=True)
class SelfUnderstandingInput:
    insight_key: str
    as_of: str
    themes: Sequence[Mapping[str, Any]]
    support_atoms: Sequence[Mapping[str, Any]] = ()
    boundary_atoms: Sequence[Mapping[str, Any]] = ()
    existing_insight: Optional[Mapping[str, Any]] = None

    def validate(self) -> None:
        key = normalize_insight_key(self.insight_key)
        if len(key) < 2 or len(key) > 80:
            raise ContractError("self insight key is invalid")
        try:
            as_of_date = dt.date.fromisoformat(self.as_of)
        except ValueError as exc:
            raise ContractError("self understanding as_of must be an ISO date") from exc
        theme_ids = set()
        for theme in self.themes:
            validate_contract("theme-v2.schema.json", theme)
            if dt.date.fromisoformat(str(theme["created_at"])[:10]) > as_of_date:
                raise ContractError("self understanding input includes a future theme", kind="evidence")
            theme_ids.add(str(theme["theme_id"]))
        if not theme_ids:
            raise ContractError("self understanding requires bounded themes", kind="material_gate")
        atom_ids = set()
        for atom in [*self.support_atoms, *self.boundary_atoms]:
            validate_contract("memory-atom-v2.schema.json", atom)
            if dt.date.fromisoformat(str(atom["last_seen_on"])) > as_of_date:
                raise ContractError("self understanding input includes future memory", kind="evidence")
            atom_ids.add(str(atom["memory_atom_id"]))
        if self.existing_insight is not None:
            validate_contract("self-insight-v2.schema.json", self.existing_insight)
            expected_id = make_id("self_insight", "self-insight-key-v1", {"key": key})
            if self.existing_insight["insight_id"] != expected_id:
                raise ContractError("existing self insight identity differs from insight key", kind="evidence")
            existing_theme_ids = {str(ref["id"]) for ref in self.existing_insight["theme_refs"]}
            if not existing_theme_ids.issubset(theme_ids):
                raise ContractError("existing self insight themes are missing from bounded input", kind="evidence")
            current_support_ids = theme_ids | atom_ids
            existing_support_ids = {
                str(ref["id"])
                for ref in [
                    *self.existing_insight["support_refs"],
                    *self.existing_insight["boundary_refs"],
                ]
            }
            if not existing_support_ids.issubset(current_support_ids):
                raise ContractError("existing self insight support is missing from bounded input", kind="evidence")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "insight_key": normalize_insight_key(self.insight_key),
            "as_of": self.as_of,
            "theme_refs": [theme_ref(value) for value in self.themes],
            "support_atom_refs": [memory_atom_ref(value) for value in self.support_atoms],
            "boundary_atom_refs": [memory_atom_ref(value) for value in self.boundary_atoms],
            "existing_insight_ref": None if self.existing_insight is None else self_insight_ref(self.existing_insight),
        }

    def source_refs(self) -> list[dict[str, Any]]:
        refs = [
            *[theme_ref(value) for value in self.themes],
            *[memory_atom_ref(value) for value in self.support_atoms],
            *[memory_atom_ref(value) for value in self.boundary_atoms],
        ]
        if self.existing_insight is not None:
            refs.append(self_insight_ref(self.existing_insight))
        by_key = {(str(ref["kind"]), str(ref["id"])): ref for ref in refs}
        return [by_key[key] for key in sorted(by_key)]


def self_insight_ref(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": "self_insight",
        "id": value["insight_id"],
        "revision": value["revision"],
        "revision_sha256": sha256_json(value),
    }


class SelfUnderstandingAgent:
    def evaluate(
        self,
        value: SelfUnderstandingInput,
        *,
        user_action_watermark_sha256: str,
        created_at: str,
    ) -> Mapping[str, Any]:
        value.validate()
        validate_datetime(created_at, "created_at")
        input_sha = sha256_json(value.canonical_payload())
        run_id = make_id(
            "agent_run",
            SELF_PROMPT_VERSION,
            {"input_sha256": input_sha, "watermark": user_action_watermark_sha256, "created_at": created_at},
        )
        context = AgentRunContext(
            run_id=run_id,
            agent_role="self_understanding",
            prompt_version=SELF_PROMPT_VERSION,
            policy_version=SELF_POLICY_VERSION,
            input_sha256=input_sha,
            user_action_watermark_sha256=user_action_watermark_sha256,
            created_at=created_at,
            usage=ProviderUsage.deterministic(),
        )
        eligible = [theme for theme in value.themes if theme["lifecycle"] in {"active", "tension"}]
        gate = self_material_gate(eligible)
        if gate != "passed":
            if value.existing_insight is not None and value.existing_insight["maturity"] != "dormant":
                dormant_proposal = self._dormant_proposal(value, created_at)
                return make_candidate(
                    context, action="propose_revise", proposed_kind="self_insight", proposed_object=dormant_proposal,
                    source_refs=value.source_refs(), source_spans=[], reason_code="supporting_themes_became_inactive", confidence="high",
                )
            return make_candidate(
                context, action="no_change", proposed_kind="none", proposed_object=None,
                source_refs=value.source_refs(), source_spans=[], reason_code=gate, confidence="high",
            )
        sensitive_reason = sensitive_inference_reason([
            value.insight_key,
            *[str(theme["title"]) for theme in eligible],
            *[str(theme["statement"]) for theme in eligible],
            *[str(theme["scope"]) for theme in eligible],
        ])
        if sensitive_reason is not None:
            return make_candidate(
                context, action="stop", proposed_kind="none", proposed_object=None,
                source_refs=value.source_refs(), source_spans=[], reason_code=sensitive_reason, confidence="high",
            )
        proposal, reason = self._proposal(value, eligible, created_at)
        if proposal is None:
            return make_candidate(
                context, action="no_change", proposed_kind="none", proposed_object=None,
                source_refs=value.source_refs(), source_spans=[], reason_code=reason, confidence="high",
            )
        action = "propose_create" if value.existing_insight is None else "propose_revise"
        return make_candidate(
            context, action=action, proposed_kind="self_insight", proposed_object=proposal,
            source_refs=value.source_refs(), source_spans=[], reason_code=reason, confidence="high",
        )

    @staticmethod
    def _proposal(
        value: SelfUnderstandingInput,
        themes: Sequence[Mapping[str, Any]],
        created_at: str,
    ) -> tuple[Optional[dict[str, Any]], str]:
        ordered = sorted(themes, key=lambda item: str(item["theme_id"]))
        existing = value.existing_insight
        theme_refs = [theme_ref(theme) for theme in ordered]
        support_refs = _unique_refs([
            *theme_refs,
            *[memory_atom_ref(atom) for atom in value.support_atoms],
        ])
        tension_refs = [theme_ref(theme) for theme in ordered if theme["lifecycle"] == "tension"]
        boundary_refs = _unique_refs([
            *([] if existing is None else existing["boundary_refs"]),
            *tension_refs,
            *[memory_atom_ref(atom) for atom in value.boundary_atoms],
        ])
        title = _title(ordered)
        statement = _statement(ordered)
        scope = _scope(ordered)
        uncertainty = _uncertainty(ordered, boundary_refs)
        maturity = "stable" if len(ordered) >= 4 else ("observed" if len(ordered) >= 3 else "forming")
        if existing is None:
            revision = 1
            previous = None
            confirmation = "draft"
            visibility = "local_only"
            reason = "多个长期主题首次共同支持一条当前理解"
        else:
            revision = int(existing["revision"]) + 1
            previous = sha256_json(existing)
            confirmation = str(existing["confirmation"])
            visibility = str(existing["visibility"])
            reason = "长期主题的新 revision 更新了当前理解"
            if set(ref["id"] for ref in theme_refs) - set(ref["id"] for ref in existing["theme_refs"]):
                reason = "新的长期主题加入了当前理解"
            if tension_refs:
                reason = "长期主题中的张力为当前理解增加了边界"
            if existing["maturity"] == "dormant":
                reason = "相关长期主题重新活跃，当前理解恢复"
            unchanged = (
                theme_refs == existing["theme_refs"]
                and support_refs == existing["support_refs"]
                and boundary_refs == existing["boundary_refs"]
                and title == existing["title"]
                and statement == existing["statement"]
                and scope == existing["scope"]
                and uncertainty == existing["uncertainty"]
                and maturity == existing["maturity"]
            )
            if unchanged:
                return None, "self_insight_has_no_material_change"
        proposal = {
            "schema_version": "2.0", "kind": "memento_self_insight_revision",
            "insight_id": make_id("self_insight", "self-insight-key-v1", {"key": normalize_insight_key(value.insight_key)}),
            "revision": revision, "previous_revision_sha256": previous,
            "title": title, "statement": statement, "scope": scope, "uncertainty": uncertainty,
            "maturity": maturity, "confirmation": confirmation, "theme_refs": theme_refs,
            "support_refs": support_refs, "boundary_refs": boundary_refs, "change_reason": reason,
            "sensitivity": "normal", "visibility": visibility,
            "policy_version": SELF_POLICY_VERSION, "prompt_version": SELF_PROMPT_VERSION,
            "created_at": created_at, "committed_by": "workflow", "committing_action_id": None,
        }
        validate_contract("self-insight-v2.schema.json", proposal)
        return proposal, reason

    @staticmethod
    def _dormant_proposal(value: SelfUnderstandingInput, created_at: str) -> dict[str, Any]:
        existing = value.existing_insight
        if existing is None:
            raise ContractError("cannot dormancy-transition a missing self insight")
        theme_by_id = {str(theme["theme_id"]): theme for theme in value.themes}
        atom_by_id = {
            str(atom["memory_atom_id"]): atom
            for atom in [*value.support_atoms, *value.boundary_atoms]
        }

        def current_ref(raw_ref: Mapping[str, Any]) -> dict[str, Any]:
            if raw_ref["kind"] == "theme":
                return theme_ref(theme_by_id[str(raw_ref["id"])])
            return memory_atom_ref(atom_by_id[str(raw_ref["id"])])

        proposal = {
            **dict(existing),
            "revision": int(existing["revision"]) + 1,
            "previous_revision_sha256": sha256_json(existing),
            "maturity": "dormant",
            "theme_refs": [current_ref(ref) for ref in existing["theme_refs"]],
            "support_refs": [current_ref(ref) for ref in existing["support_refs"]],
            "boundary_refs": [current_ref(ref) for ref in existing["boundary_refs"]],
            "change_reason": "支持这一理解的长期主题已经不足两个 active 主题",
            "policy_version": SELF_POLICY_VERSION,
            "prompt_version": SELF_PROMPT_VERSION,
            "created_at": created_at,
            "committed_by": "workflow",
            "committing_action_id": None,
        }
        validate_contract("self-insight-v2.schema.json", proposal)
        return proposal


def _unique_refs(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_key = {(str(value["kind"]), str(value["id"])): dict(value) for value in values}
    return [by_key[key] for key in sorted(by_key)]


def _title(themes: Sequence[Mapping[str, Any]]) -> str:
    titles = "、".join(str(theme["title"]) for theme in themes[:3])
    return f"{titles}共同形成的工作方式"[:80]


def _statement(themes: Sequence[Mapping[str, Any]]) -> str:
    parts = [str(theme["statement"]).rstrip("。") for theme in themes[:4]]
    return ("多个长期主题共同显示：" + "；".join(parts))[:1000]


def _scope(themes: Sequence[Mapping[str, Any]]) -> str:
    titles = "、".join(str(theme["title"]) for theme in themes[:4])
    return f"当前只适用于已经形成{titles}这些长期主题的工作场景"[:800]


def _uncertainty(themes: Sequence[Mapping[str, Any]], boundary_refs: Sequence[Mapping[str, Any]]) -> str:
    suffix = "，且仍存在需要继续观察的边界" if boundary_refs else ""
    return f"当前由{len(themes)}个长期主题支持，超出这些主题的行为仍未知{suffix}"[:600]
