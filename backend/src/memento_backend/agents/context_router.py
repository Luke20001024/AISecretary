"""Deterministic, bounded projection of authorised personal Context."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_json, validate_id
from memento_backend.policies.context_policy import normalize_topics, parse_timestamp, sensitivity_allowed
from memento_backend.projections.common import ProjectionInputs


CONTEXT_ROUTER_VERSION = "context-router-v1"


@dataclass(frozen=True)
class ContextRequest:
    grant_id: str
    session_id: str
    client_id: str
    task: str
    topic_scope: tuple[str, ...]
    time_scope: Optional[Mapping[str, str]] = None
    include_source_quotes: bool = False
    tool_name: str = "memento.create_context_pack"
    target_id: Optional[str] = None

    def canonical_payload(self) -> dict[str, Any]:
        time_scope: Any = self.time_scope
        if isinstance(self.time_scope, Mapping):
            time_scope = dict(self.time_scope)
        return {
            "grant_id": self.grant_id,
            "session_id": self.session_id,
            "client_id": self.client_id,
            "task": self.task,
            "topic_scope": list(self.topic_scope),
            "time_scope": time_scope,
            "include_source_quotes": self.include_source_quotes,
            "tool_name": self.tool_name,
            "target_id": self.target_id,
        }

    def validate(self) -> None:
        validate_id("context_grant", self.grant_id, "grant_id")
        validate_id("external_session", self.session_id, "session_id")
        if not isinstance(self.client_id, str) or not self.client_id.strip() or len(self.client_id) > 240:
            raise ContractError("client_id is empty or too long", kind="authorization")
        if not isinstance(self.task, str) or not self.task.strip() or len(self.task) > 1000:
            raise ContractError("task is empty or too long", kind="authorization")
        if type(self.include_source_quotes) is not bool:
            raise ContractError("include_source_quotes must be boolean", kind="authorization")
        read_targets = {
            "memento.search_context": None,
            "memento.get_self_insight": "self_insight",
            "memento.get_theme": "theme",
            "memento.trace_evidence": None,
            "memento.create_context_pack": None,
        }
        target_kind = read_targets.get(self.tool_name)
        if self.tool_name not in read_targets:
            raise ContractError("Context read tool is outside the allow-list", kind="authorization")
        if target_kind is None:
            if self.target_id is not None:
                raise ContractError("Context read tool does not accept a target id", kind="authorization")
        else:
            if self.target_id is None:
                raise ContractError("Context read tool requires a target id", kind="authorization")
            validate_id(target_kind, self.target_id, "target_id")
        if not all(isinstance(topic, str) for topic in self.topic_scope):
            raise ContractError("topic scope must contain text", kind="authorization")
        normalize_topics(self.topic_scope)


class ContextRouter:
    """Select only current, confirmed objects inside an explicit grant."""

    def project(
        self,
        request: ContextRequest,
        *,
        grant: Mapping[str, Any],
        grant_ref: Mapping[str, Any],
        session_ref: Mapping[str, Any],
        inputs: ProjectionInputs,
        generated_at: str,
    ) -> Mapping[str, Any]:
        request.validate()
        topics = normalize_topics(request.topic_scope)
        allowed = set(str(value) for value in grant["allowed_kinds"])

        memory_candidates = [
            value for value in inputs.memory_atoms
            if value.get("status") == "active"
            and self._within_time(value, request.time_scope)
            and self._matches(value, topics)
        ]
        matching_memory_ids = {str(value["memory_atom_id"]) for value in memory_candidates}

        theme_candidates = [
            value for value in inputs.themes
            if value.get("lifecycle") in {"active", "tension"}
            and self._within_time(value, request.time_scope)
            and (
                self._matches(value, topics)
                or any(str(ref["id"]) in matching_memory_ids for ref in value.get("evidence_refs", ()))
            )
        ]
        matching_theme_ids = {str(value["theme_id"]) for value in theme_candidates}

        insight_candidates = [
            value for value in inputs.self_insights
            if value.get("maturity") != "tombstone"
            and value.get("confirmation") == "user_confirmed"
            and value.get("visibility") == "grant_only"
            and sensitivity_allowed(str(value["sensitivity"]), str(grant["max_sensitivity"]))
            and self._within_time(value, request.time_scope)
            and (
                self._matches(value, topics)
                or any(str(ref["id"]) in matching_theme_ids for ref in value.get("theme_refs", ()))
            )
        ]

        selected_insights = insight_candidates[:3] if "self_insight" in allowed else []
        linked_theme_ids = {
            str(ref["id"])
            for insight in selected_insights
            for ref in insight.get("theme_refs", ())
        }
        selected_themes = (
            [value for value in theme_candidates if str(value["theme_id"]) in linked_theme_ids or self._matches(value, topics)][:6]
            if "theme" in allowed else []
        )
        linked_memory_ids = {
            str(ref["id"])
            for theme in selected_themes
            for ref in (*theme.get("evidence_refs", ()), *theme.get("counterevidence_refs", ()))
        }
        selected_memories = (
            [value for value in memory_candidates if str(value["memory_atom_id"]) in linked_memory_ids or self._matches(value, topics)][:12]
            if "memory_atom" in allowed else []
        )

        insight_refs = {str(value["insight_id"]): self._ref("self_insight", "insight_id", value) for value in selected_insights}
        theme_refs = {str(value["theme_id"]): self._ref("theme", "theme_id", value) for value in selected_themes}
        memory_refs = {str(value["memory_atom_id"]): self._ref("memory_atom", "memory_atom_id", value) for value in selected_memories}

        insight_rows = [
            {
                "ref": insight_refs[str(value["insight_id"])],
                "title": value["title"], "statement": value["statement"], "scope": value["scope"],
                "uncertainty": value["uncertainty"],
                "theme_refs": [theme_refs[str(ref["id"])] for ref in value["theme_refs"] if str(ref["id"]) in theme_refs],
                "boundary_refs": [
                    memory_refs[str(ref["id"])] for ref in value["boundary_refs"]
                    if str(ref["id"]) in memory_refs
                ],
            }
            for value in selected_insights
        ]
        theme_rows = [
            {
                "ref": theme_refs[str(value["theme_id"])],
                "title": value["title"], "statement": value["statement"], "scope": value["scope"],
                "evidence_refs": [memory_refs[str(ref["id"])] for ref in value["evidence_refs"] if str(ref["id"]) in memory_refs],
                "counterevidence_refs": [memory_refs[str(ref["id"])] for ref in value["counterevidence_refs"] if str(ref["id"]) in memory_refs],
            }
            for value in selected_themes
        ]
        memory_rows = [
            {
                "ref": memory_refs[str(value["memory_atom_id"])], "statement": value["statement"],
                "memory_kind": value["memory_kind"], "topics": list(value["topics"]),
                "purposes": list(value["purposes"]), "uncertainty": value["uncertainty"],
            }
            for value in selected_memories
        ]

        source_refs: dict[str, Mapping[str, Any]] = {}
        source_quotes: list[Mapping[str, Any]] = []
        source_by_id = {str(value["record_id"]): value for value in inputs.source_records}
        if "source_record" in allowed and request.include_source_quotes and grant["allow_source_quotes"]:
            for memory in selected_memories:
                for span in memory["source_spans"]:
                    if len(str(span["quote"])) > 2000:
                        continue
                    record = source_by_id.get(str(span["record_id"]))
                    if record is None or not self._within_time(record, request.time_scope):
                        continue
                    ref = self._ref("source_record", "record_id", record)
                    if ref["revision"] != span["record_revision"] or ref["revision_sha256"] != span["record_revision_sha256"]:
                        continue
                    source_refs[str(record["record_id"])] = ref
                    source_quotes.append({
                        "record_ref": ref, "source_file": span["source_file"],
                        "line_start": span["line_start"], "line_end": span["line_end"],
                        "quote": span["quote"], "quote_sha256": span["quote_sha256"],
                    })
                    if len(source_quotes) == 8:
                        break
                if len(source_quotes) == 8:
                    break

        selected_refs = self._unique_refs([
            *insight_refs.values(), *theme_refs.values(), *memory_refs.values(), *source_refs.values(),
        ])
        request_sha256 = sha256_json(request.canonical_payload())
        input_sha256 = sha256_json({
            "router_version": CONTEXT_ROUTER_VERSION,
            "request": request.canonical_payload(),
            "grant_ref": dict(grant_ref), "session_ref": dict(session_ref),
            "selected_refs": selected_refs,
        })
        expires_at = self._pack_expiry(generated_at, grant.get("expires_at"))
        pack_id = make_id("context_pack", "context-pack-v1", {
            "request_sha256": request_sha256, "input_sha256": input_sha256, "generated_at": generated_at,
        })
        pack = {
            "schema_version": "1.0", "kind": "memento_context_pack_snapshot", "pack_id": pack_id,
            "session_ref": dict(session_ref), "grant_ref": dict(grant_ref), "request_sha256": request_sha256,
            "task": request.task, "topic_scope": list(request.topic_scope),
            "time_scope": None if request.time_scope is None else dict(request.time_scope),
            "selected_refs": selected_refs, "self_insights": insight_rows, "themes": theme_rows,
            "memories": memory_rows, "source_quotes": source_quotes,
            "unknowns": ["当前 Context 只覆盖已确认且与本次任务相关的记录"],
            "prohibited_inferences": [
                "不得把未返回的记录视为不存在",
                "不得将本次 Context Pack 推广为用户全部人格",
                "不得绕过 Memento 直接修改 Theme 或 SelfInsight",
            ],
            "allowed_writeback": list(grant["allowed_writeback"]), "input_sha256": input_sha256,
            "generated_at": generated_at, "expires_at": expires_at,
        }
        validate_contract("context-pack-v1.schema.json", pack)
        return pack

    @staticmethod
    def _matches(value: Mapping[str, Any], topics: Sequence[str]) -> bool:
        if "*" in topics:
            return True
        fields = [str(value.get(name, "")) for name in ("title", "statement", "scope")]
        fields.extend(str(item) for item in value.get("topics", ()))
        fields.extend(str(item) for item in value.get("purposes", ()))
        haystack = "\n".join(fields).casefold()
        return any(topic in haystack for topic in topics)

    @staticmethod
    def _within_time(value: Mapping[str, Any], scope: Optional[Mapping[str, str]]) -> bool:
        if scope is None:
            return True
        when = str(value.get("last_seen_on") or value.get("created_at") or "")[:10]
        return bool(when) and str(scope["from"]) <= when <= str(scope["to"])

    @staticmethod
    def _ref(kind: str, id_field: str, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"kind": kind, "id": value[id_field], "revision": value["revision"], "revision_sha256": sha256_json(value)}

    @staticmethod
    def _unique_refs(values: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        seen: set[tuple[str, str, int, str]] = set()
        result: list[Mapping[str, Any]] = []
        for value in values:
            key = (str(value["kind"]), str(value["id"]), int(value["revision"]), str(value["revision_sha256"]))
            if key not in seen:
                seen.add(key)
                result.append(dict(value))
        return result

    @staticmethod
    def _pack_expiry(generated_at: str, grant_expiry: Any) -> str:
        candidate = parse_timestamp(generated_at) + dt.timedelta(minutes=5)
        if grant_expiry is not None:
            candidate = min(candidate, parse_timestamp(str(grant_expiry)))
        return candidate.isoformat()
