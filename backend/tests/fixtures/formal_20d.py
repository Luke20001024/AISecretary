"""A compact 20-day replay made only from synthetic formal objects."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from memento_backend.domain.ids import sha256_bytes, sha256_json
from memento_backend.projections.common import ProjectionInputs


SHA_USER_ACTION = "d" * 64
DATES = (
    "2026-07-30", "2026-08-03", "2026-08-09",
    "2026-08-13", "2026-08-17", "2026-08-18",
)
STATEMENTS = (
    "先写清目标指标和验证周期",
    "先把失败条件和撤回点留下",
    "找到原文位置后再形成结论",
    "边界明确后要求立即开始",
    "同类问题反复出现后沉淀方法",
    "重要判断需要保留变化理由",
)


def formal_20d_inputs() -> ProjectionInputs:
    records = tuple(_record(index, date) for index, date in enumerate(DATES))
    interpretations = tuple(
        _interpretation(index, record, STATEMENTS[index])
        for index, record in enumerate(records)
    )
    atoms = tuple(
        _memory_atom(index, interpretation, STATEMENTS[index], DATES[index])
        for index, interpretation in enumerate(interpretations)
    )
    relations = tuple(_relation(index, atoms[index * 2], atoms[index * 2 + 1]) for index in range(3))
    themes = (
        _theme(0, "产品决策", "重要决策先固定目标和失败条件", atoms[0:2], relations[0]),
        _theme(1, "证据优先", "形成结论前回到可核查的原始位置", atoms[2:4], relations[1]),
        _theme(2, "长期积累", "反复出现的判断会沉淀为可修订方法", atoms[4:6], relations[2]),
    )
    insight = _self_insight(themes, atoms)
    resource = _resource(records[0])
    read_later = _read_later(resource)
    return ProjectionInputs(
        source_records=records,
        interpretations=interpretations,
        memory_atoms=atoms,
        relations=relations,
        themes=themes,
        self_insights=(insight,),
        resource_cards=(resource,),
        read_later_intents=(read_later,),
    )


def _hex_id(prefix: str, index: int) -> str:
    return f"{prefix}_{index + 1:024x}"


def _sha(seed: str) -> str:
    return sha256_bytes(seed.encode("utf-8"))


def _ref(kind: str, object_id: str, seed: str) -> Dict[str, Any]:
    return {"kind": kind, "id": object_id, "revision": 1, "revision_sha256": _sha(seed)}


def _object_ref(kind: str, value: Dict[str, Any], id_field: str) -> Dict[str, Any]:
    return {
        "kind": kind,
        "id": value[id_field],
        "revision": value["revision"],
        "revision_sha256": sha256_json(value),
    }


def _record(index: int, date: str) -> Dict[str, Any]:
    source_types = ("url", "text", "screenshot_ocr", "voice_transcript", "file_note", "text")
    source_apps = ("Chrome", "Memento", "Chrome 截图", "语音备忘", "Obsidian", "Memento")
    record_id = _hex_id("rec", index)
    return {
        "schema_version": "2.0", "kind": "memento_source_record_revision", "record_id": record_id,
        "revision": 1, "previous_revision_sha256": None, "status": "active", "operation": "ingest",
        "created_at": f"{date}T09:{index:02d}:01+08:00", "captured_at": f"{date}T09:{index:02d}:00+08:00",
        "local_date": date, "source_type": source_types[index], "source_app": source_apps[index],
        "source_file": f"daily/{date}.md", "line_start": index * 3 + 1, "line_end": index * 3 + 2,
        "entry_sha256": _sha(f"entry-{index}"), "source_snapshot_sha256": _sha(f"snapshot-{index}"),
        "attachments": [], "ingest_origin": "capture_service", "committed_by": "workflow",
    }


def _source_span(index: int, record: Dict[str, Any], statement: str) -> Dict[str, Any]:
    return {
        "record_id": record["record_id"], "record_revision": 1,
        "record_revision_sha256": sha256_json(record), "source_file": record["source_file"],
        "line_start": record["line_start"], "line_end": record["line_start"], "quote": statement,
        "quote_sha256": _sha(statement),
    }


def _interpretation(index: int, record: Dict[str, Any], statement: str) -> Dict[str, Any]:
    interpretation_id = _hex_id("int", index)
    return {
        "schema_version": "2.0", "kind": "memento_record_interpretation_revision",
        "interpretation_id": interpretation_id, "revision": 1, "previous_revision_sha256": None,
        "status": "ready", "operation": "interpret",
        "source_record_ref": _object_ref("source_record", record, "record_id"),
        "capture_decision_ref": _ref("capture_decision", _hex_id("cap", index), f"decision-{index}"),
        "summary": statement, "content_types": ["own_idea", "decision"],
        "topics": ["产品方法" if index < 2 else "认知积累"], "purposes": ["future_decision"],
        "stance": "self_observation", "uncertainty": "medium",
        "source_spans": [_source_span(index, record, statement)],
        "prompt_version": "record-interpreter-v1", "policy_version": "interpretation-policy-v1",
        "user_action_watermark_sha256": SHA_USER_ACTION,
        "created_at": f"{record['local_date']}T10:{index:02d}:00+08:00", "committed_by": "workflow",
    }


def _memory_atom(index: int, interpretation: Dict[str, Any], statement: str, date: str) -> Dict[str, Any]:
    return {
        "schema_version": "2.0", "kind": "memento_memory_atom_revision",
        "memory_atom_id": _hex_id("mat", index), "revision": 1, "previous_revision_sha256": None,
        "status": "active", "operation": "materialize", "statement": statement,
        "memory_kind": "judgment", "topics": list(interpretation["topics"]),
        "purposes": list(interpretation["purposes"]), "uncertainty": "medium",
        "evidence_refs": [_object_ref("record_interpretation", interpretation, "interpretation_id")],
        "source_spans": list(interpretation["source_spans"]), "first_seen_on": date, "last_seen_on": date,
        "change_reason": "日级整理确认这是一条可继续关联的判断", "policy_version": "memory-policy-v1",
        "created_at": f"{date}T21:{index:02d}:00+08:00", "committed_by": "workflow",
    }


def _relation(index: int, left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    date = DATES[index * 2 + 1]
    return {
        "schema_version": "2.0", "kind": "memento_relation_revision", "relation_id": _hex_id("rel", index),
        "revision": 1, "previous_revision_sha256": None, "status": "active", "operation": "materialize",
        "relation_type": "same_topic", "direction": "undirected",
        "from_ref": _object_ref("memory_atom", left, "memory_atom_id"),
        "to_ref": _object_ref("memory_atom", right, "memory_atom_id"),
        "statement": "两条跨日记录形成同一长期判断", "confidence": "high",
        "evidence_refs": list(left["evidence_refs"]) + list(right["evidence_refs"]),
        "change_reason": "相同判断在不同日期再次出现", "policy_version": "relation-policy-v1",
        "created_at": f"{date}T21:20:00+08:00", "committed_by": "workflow",
    }


def _theme(index: int, title: str, statement: str, atoms: Tuple[Dict[str, Any], ...], relation: Dict[str, Any]) -> Dict[str, Any]:
    evidence_days = [str(atom["last_seen_on"]) for atom in atoms]
    return {
        "schema_version": "2.0", "kind": "memento_theme_revision", "theme_id": _hex_id("thm", index),
        "revision": 1, "previous_revision_sha256": None, "title": title, "statement": statement,
        "scope": "重要产品、研究和协作工作", "lifecycle": "active", "confidence": "observed",
        "evidence_refs": [_object_ref("memory_atom", atom, "memory_atom_id") for atom in atoms],
        "evidence_days": evidence_days, "counterevidence_refs": [],
        "relation_refs": [_object_ref("relation", relation, "relation_id")],
        "change_reason": "两个不同日期出现同一行动规则", "policy_version": "theme-policy-v1",
        "prompt_version": "theme-agent-v1", "created_at": f"{max(evidence_days)}T21:30:00+08:00",
        "committed_by": "workflow",
    }


def _self_insight(themes: Tuple[Dict[str, Any], ...], atoms: Tuple[Dict[str, Any], ...]) -> Dict[str, Any]:
    return {
        "schema_version": "2.0", "kind": "memento_self_insight_revision", "insight_id": _hex_id("sin", 0),
        "revision": 1, "previous_revision_sha256": None, "title": "让重要判断保留依据与修订空间",
        "statement": "在重要工作中会先固定依据和边界，再快速推进，并把变化理由留给下一次工作",
        "scope": "产品、研究和需要承担失败成本的工作", "uncertainty": "仍需观察低风险探索中的行为",
        "maturity": "observed", "confirmation": "observed",
        "theme_refs": [_object_ref("theme", theme, "theme_id") for theme in themes],
        "support_refs": [_object_ref("theme", themes[0], "theme_id"), _object_ref("theme", themes[1], "theme_id")],
        "boundary_refs": [_object_ref("memory_atom", atoms[3], "memory_atom_id")],
        "change_reason": "三条长期主题共同指向同一工作方式", "sensitivity": "normal", "visibility": "grant_only",
        "policy_version": "self-policy-v1", "prompt_version": "self-agent-v1",
        "created_at": "2026-08-18T21:40:00+08:00", "committed_by": "workflow", "committing_action_id": None,
    }


def _resource(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": "1.0", "kind": "memento_resource_card_revision", "resource_id": _hex_id("res", 0),
        "revision": 1, "previous_revision_sha256": None, "status": "active", "operation": "index",
        "source_record_ref": _object_ref("source_record", record, "record_id"),
        "resource_type": "web_page", "url": "https://example.com/memento-fixture", "title": "稍后阅读的合成页面",
        "local_asset_refs": [{
            "path": "assets/fixture-page.png", "mime_type": "image/png",
            "byte_size": 1024, "sha256": _sha("fixture-page"),
        }],
        "ocr_index_ref": None, "user_selected_spans": [], "user_note": "待会再看",
        "processing_route": "ask_on_use", "created_at": "2026-07-30T09:10:00+08:00", "committed_by": "workflow",
    }


def _read_later(resource: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": "1.0", "kind": "memento_read_later_intent_revision", "intent_id": _hex_id("rli", 0),
        "revision": 1, "previous_revision_sha256": None, "status": "open", "operation": "create",
        "resource_ref": _object_ref("resource_card", resource, "resource_id"),
        "intent_type": "read_later", "user_note": "待会再看", "created_at": "2026-07-30T09:11:00+08:00",
        "committed_by": "workflow",
    }
