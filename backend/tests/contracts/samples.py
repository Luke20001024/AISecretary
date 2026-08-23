"""Small deterministic R1 contract samples."""

from __future__ import annotations

from typing import Any


SHA_A = "a" * 64
SHA_B = "b" * 64
RECORD_ID = "rec_111111111111111111111111"
DECISION_ID = "cap_222222222222222222222222"
RESOURCE_ID = "res_333333333333333333333333"
INTENT_ID = "rli_444444444444444444444444"
INTERPRETATION_ID = "int_555555555555555555555555"
MEMORY_ATOM_A_ID = "mat_666666666666666666666666"
MEMORY_ATOM_B_ID = "mat_777777777777777777777777"
RELATION_ID = "rel_888888888888888888888888"
THEME_A_ID = "thm_999999999999999999999999"
THEME_B_ID = "thm_aaaaaaaaaaaaaaaaaaaaaaaa"
SELF_INSIGHT_ID = "sin_bbbbbbbbbbbbbbbbbbbbbbbb"


def source_ref() -> dict[str, Any]:
    return {
        "kind": "source_record",
        "id": RECORD_ID,
        "revision": 1,
        "revision_sha256": SHA_A,
    }


def resource_ref() -> dict[str, Any]:
    return {
        "kind": "resource_card",
        "id": RESOURCE_ID,
        "revision": 1,
        "revision_sha256": SHA_B,
    }


def source_record() -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "kind": "memento_source_record_revision",
        "record_id": RECORD_ID,
        "revision": 1,
        "previous_revision_sha256": None,
        "status": "active",
        "operation": "ingest",
        "created_at": "2026-08-22T10:00:01+08:00",
        "captured_at": "2026-08-22T10:00:00+08:00",
        "local_date": "2026-08-22",
        "source_type": "url",
        "source_app": "Browser",
        "source_file": "2026-08-22.md",
        "line_start": 4,
        "line_end": 6,
        "entry_sha256": SHA_A,
        "source_snapshot_sha256": SHA_B,
        "attachments": [],
        "ingest_origin": "capture_service",
        "committed_by": "workflow",
    }


def capture_decision() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": "memento_capture_decision_revision",
        "decision_id": DECISION_ID,
        "revision": 1,
        "previous_revision_sha256": None,
        "status": "active",
        "operation": "route",
        "source_record_ref": source_ref(),
        "content_role": "read_later",
        "processing_route": "ask_on_use",
        "user_signal_spans": [],
        "resource_scope": "whole_resource",
        "reason_code": "explicit_read_later_intent",
        "confidence": "high",
        "needs_user_confirmation": False,
        "prompt_version": "capture-agent-v1",
        "policy_version": "capture-policy-v1",
        "user_action_watermark_sha256": SHA_B,
        "created_at": "2026-08-22T10:00:02+08:00",
        "committed_by": "workflow",
    }


def resource_card() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": "memento_resource_card_revision",
        "resource_id": RESOURCE_ID,
        "revision": 1,
        "previous_revision_sha256": None,
        "status": "active",
        "operation": "index",
        "source_record_ref": source_ref(),
        "resource_type": "web_page",
        "url": "https://example.com/article",
        "title": "Example article",
        "local_asset_refs": [],
        "ocr_index_ref": None,
        "user_selected_spans": [],
        "user_note": "待会再看",
        "processing_route": "ask_on_use",
        "created_at": "2026-08-22T10:00:03+08:00",
        "committed_by": "workflow",
    }


def read_later_intent() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": "memento_read_later_intent_revision",
        "intent_id": INTENT_ID,
        "revision": 1,
        "previous_revision_sha256": None,
        "status": "open",
        "operation": "create",
        "resource_ref": resource_ref(),
        "intent_type": "read_later",
        "user_note": "待会再看",
        "created_at": "2026-08-22T10:00:04+08:00",
        "committed_by": "workflow",
    }


def ref(kind: str, object_id: str, revision_sha256: str = SHA_A) -> dict[str, Any]:
    return {
        "kind": kind,
        "id": object_id,
        "revision": 1,
        "revision_sha256": revision_sha256,
    }


def source_span() -> dict[str, Any]:
    return {
        "record_id": RECORD_ID,
        "record_revision": 1,
        "record_revision_sha256": SHA_A,
        "source_file": "2026-08-22.md",
        "line_start": 4,
        "line_end": 4,
        "quote": "先确认边界，再快速推进",
        "quote_sha256": "c" * 64,
    }


def record_interpretation() -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "kind": "memento_record_interpretation_revision",
        "interpretation_id": INTERPRETATION_ID,
        "revision": 1,
        "previous_revision_sha256": None,
        "status": "ready",
        "operation": "interpret",
        "source_record_ref": source_ref(),
        "capture_decision_ref": ref("capture_decision", DECISION_ID),
        "summary": "重要行动前先确认边界",
        "content_types": ["own_idea", "decision"],
        "topics": ["协作边界"],
        "purposes": ["future_decision"],
        "stance": "self_observation",
        "uncertainty": "medium",
        "source_spans": [source_span()],
        "prompt_version": "record-interpreter-v1",
        "policy_version": "interpretation-policy-v1",
        "user_action_watermark_sha256": SHA_B,
        "created_at": "2026-08-22T10:01:00+08:00",
        "committed_by": "workflow",
    }


def memory_atom() -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "kind": "memento_memory_atom_revision",
        "memory_atom_id": MEMORY_ATOM_A_ID,
        "revision": 1,
        "previous_revision_sha256": None,
        "status": "active",
        "operation": "materialize",
        "statement": "重要行动前先确认边界",
        "memory_kind": "judgment",
        "topics": ["协作边界"],
        "purposes": ["future_decision"],
        "uncertainty": "medium",
        "evidence_refs": [ref("record_interpretation", INTERPRETATION_ID)],
        "source_spans": [source_span()],
        "first_seen_on": "2026-08-20",
        "last_seen_on": "2026-08-22",
        "change_reason": "日级整理确认这是一条可继续关联的判断",
        "policy_version": "memory-policy-v1",
        "created_at": "2026-08-22T21:00:00+08:00",
        "committed_by": "workflow",
    }


def relation() -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "kind": "memento_relation_revision",
        "relation_id": RELATION_ID,
        "revision": 1,
        "previous_revision_sha256": None,
        "status": "active",
        "operation": "materialize",
        "relation_type": "same_topic",
        "direction": "undirected",
        "from_ref": ref("memory_atom", MEMORY_ATOM_A_ID),
        "to_ref": ref("memory_atom", MEMORY_ATOM_B_ID, SHA_B),
        "statement": "两条记录都强调行动前先确认边界",
        "confidence": "high",
        "evidence_refs": [ref("record_interpretation", INTERPRETATION_ID)],
        "change_reason": "跨日期出现同类行动规则",
        "policy_version": "relation-policy-v1",
        "created_at": "2026-08-22T21:00:01+08:00",
        "committed_by": "workflow",
    }


def theme() -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "kind": "memento_theme_revision",
        "theme_id": THEME_A_ID,
        "revision": 1,
        "previous_revision_sha256": None,
        "title": "确认边界",
        "statement": "面对重要行动时会先确认边界，再快速推进",
        "scope": "高风险改动、重要决策和新协作",
        "lifecycle": "active",
        "confidence": "observed",
        "evidence_refs": [
            ref("memory_atom", MEMORY_ATOM_A_ID),
            ref("memory_atom", MEMORY_ATOM_B_ID, SHA_B),
        ],
        "evidence_days": ["2026-08-20", "2026-08-22"],
        "counterevidence_refs": [],
        "relation_refs": [ref("relation", RELATION_ID)],
        "change_reason": "两个不同日期出现同一行动规则",
        "policy_version": "theme-policy-v1",
        "prompt_version": "theme-agent-v1",
        "created_at": "2026-08-22T21:00:02+08:00",
        "committed_by": "workflow",
    }


def self_insight() -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "kind": "memento_self_insight_revision",
        "insight_id": SELF_INSIGHT_ID,
        "revision": 1,
        "previous_revision_sha256": None,
        "title": "让重要判断保留可修订空间",
        "statement": "在重要工作中会先确认依据和边界，再进入快速推进",
        "scope": "产品、研究和需要承担失败成本的工作",
        "uncertainty": "仍需观察低风险探索中的行为",
        "maturity": "observed",
        "confirmation": "observed",
        "theme_refs": [
            ref("theme", THEME_A_ID),
            ref("theme", THEME_B_ID, SHA_B),
        ],
        "support_refs": [
            ref("theme", THEME_A_ID),
            ref("theme", THEME_B_ID, SHA_B),
        ],
        "boundary_refs": [],
        "change_reason": "两个长期主题出现了相同的依据规则",
        "sensitivity": "normal",
        "visibility": "grant_only",
        "policy_version": "self-policy-v1",
        "prompt_version": "self-agent-v1",
        "created_at": "2026-08-22T21:00:03+08:00",
        "committed_by": "workflow",
        "committing_action_id": None,
    }
