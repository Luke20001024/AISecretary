#!/usr/bin/env python3
"""Focused contract tests for the cognitive secretary pure core."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "context-agent"))

from cognitive_v1 import (  # noqa: E402
    COGNITIVE_SCHEMA_VERSION,
    CognitiveActionResult,
    CognitiveUserAction,
    HOME_PROJECTION_VERSION,
    DailySummaryRevision,
    InterpretationReceiptRevision,
    ObjectRef,
    RelationRevision,
    ReusableMemoryRevision,
    SourceRecordRevision,
    SourceSpan,
    build_landscape_snapshot,
    canonical_sha256,
    make_capture_record_id,
    make_cognitive_action_id,
    make_cognitive_action_result_id,
    make_daily_summary_id,
    make_landscape_id,
    make_legacy_record_id,
    make_peak_id,
    make_receipt_id,
    make_relation_id,
    make_reusable_memory_id,
    persisted_json_bytes,
    persisted_sha256,
    validate_cognitive_action_result,
    validate_cognitive_user_action,
    validate_home_projection,
    validate_interpretation_receipt_revision,
    validate_landscape_snapshot,
    validate_long_term_evidence_refs,
    validate_source_record_transition,
)
from core import ContractError, sha256_bytes  # noqa: E402

STAMP = "2026-08-18T10:00:00+08:00"
HA, HB, HC = (sha256_bytes(value) for value in (b"a", b"b", b"c"))


class CognitiveV1ContractTest(unittest.TestCase):
    def source(self, *, revision=1, record_id=None, entry_sha=HA, previous=None, operation="ingest", status="active"):
        return SourceRecordRevision(
            COGNITIVE_SCHEMA_VERSION, "memento_source_record_revision",
            record_id or make_capture_record_id("capture-1"), revision, status,
            operation, STAMP, STAMP, "2026-08-18", "voice_transcript",
            "Memento Voice Capture", "2026-08-18.md", 12, 14, entry_sha, HC,
            (), "capture_service", previous,
        )

    def source_ref(self, source):
        return ObjectRef("source_record", source.record_id, source.revision, source.sha256)

    def span(self, source):
        quote = "先暴露可验证部分，再补齐完整方案。"
        return SourceSpan(source.record_id, source.revision, source.sha256, source.source_file, 12, 12, quote, sha256_bytes(quote.encode()))

    def receipt(self, source, *, status="ready"):
        active = status in {"ready", "needs_review"}
        return InterpretationReceiptRevision(
            COGNITIVE_SCHEMA_VERSION, "memento_interpretation_receipt_revision",
            make_receipt_id(source.record_id), 1, status, "interpret", STAMP,
            "ireq_" + "1" * 24, "irun_" + "1" * 24, self.source_ref(source),
            None, "完备性可能推迟真实反馈" if active else None,
            {"content_types": ["observation"], "topics": ["产品设计"], "objects": ["方案评审"], "stance": "self_observation", "cognitive_state": "repeated", "purposes": ["future_decision"]} if active else {},
            (), (), (self.span(source),) if active else (), "record-interpreter-v1", HA, None,
        )

    def memory(self, source, receipt):
        return ReusableMemoryRevision(
            COGNITIVE_SCHEMA_VERSION, "memento_reusable_memory_revision",
            make_reusable_memory_id("daily:20260818:one"), 1, "active", "new",
            STAMP, "评审前先定义最早可验证部分。", "decision", ("产品设计",),
            ("future_decision",), "low", (self.span(source),),
            (ObjectRef("interpretation_receipt", receipt.receipt_id, receipt.revision, receipt.sha256),),
            {"origin": "daily_integrator", "run_id": "drun_" + "1" * 24, "bundle_id": "db_20260818", "bundle_revision": 1, "user_action_id": None}, None,
        )

    def relation(self, source, memory, understanding):
        return RelationRevision(
            COGNITIVE_SCHEMA_VERSION, "memento_relation_revision",
            make_relation_id("daily:20260818:one"), 1, "active", "new", STAMP,
            "supports", ObjectRef("reusable_memory", memory.memory_id, memory.revision, memory.sha256),
            understanding, "directed", "这条记忆为长期理解提供一次明确支持。", "low",
            (self.span(source),), "2026-08-18",
            {"origin": "daily_integrator", "run_id": "drun_" + "1" * 24, "bundle_id": "db_20260818", "bundle_revision": 1, "user_action_id": None}, None,
        )

    def landscape_hashes(self):
        return {"agent_profile_sha256": HA, "reusable_memory_head_sha256": HB, "relation_head_sha256": HC, "user_action_watermark_sha256": HA}

    def test_record_identity_is_allocated_once_and_legacy_locator_is_stable(self):
        self.assertEqual(make_capture_record_id("capture-1"), make_capture_record_id("capture-1"))
        self.assertNotEqual(make_capture_record_id("capture-1"), make_capture_record_id("capture-2"))
        self.assertEqual(make_legacy_record_id("2026-08-18.md", "record:10:50"), make_legacy_record_id("2026-08-18.md", "record:10:50"))

    def test_append_stability_and_edit_revision_keep_one_record_id(self):
        old_file = b"# today\n10:50 first\n"; new_file = old_file + b"11:40 later\n"
        self.assertNotEqual(sha256_bytes(old_file), sha256_bytes(new_file))
        first = self.source(entry_sha=sha256_bytes(b"10:50 first\n"))
        edited = self.source(revision=2, record_id=first.record_id, entry_sha=HB, previous=first.sha256, operation="source_edit")
        validate_source_record_transition(first, edited)
        self.assertEqual(first.record_id, edited.record_id)
        self.assertNotEqual(first.entry_sha256, edited.entry_sha256)

    def test_formal_revision_hash_binds_persisted_file_bytes(self):
        source = self.source()
        self.assertEqual(source.sha256, persisted_sha256(source))
        self.assertEqual(source.sha256, sha256_bytes(persisted_json_bytes(source)))
        self.assertNotEqual(source.sha256, canonical_sha256(source))

    def test_receipt_is_bound_to_exact_source_revision_and_rejects_extra_fields(self):
        receipt = self.receipt(self.source())
        invalid = receipt.to_dict(); invalid["unexpected"] = True
        with self.assertRaises(ContractError):
            validate_interpretation_receipt_revision(invalid)
        stale = receipt.to_dict(); stale["source_spans"][0]["record_revision_sha256"] = HB
        with self.assertRaises(ContractError):
            validate_interpretation_receipt_revision(stale)

    def test_candidate_and_formal_memory_namespaces_are_distinct(self):
        source = self.source(); receipt = self.receipt(source)
        raw = receipt.to_dict()
        raw["memory_candidates"] = [{"candidate_id": "cmem_" + "1" * 24, "statement": "候选", "memory_kind": "observation", "topics": ["产品设计"], "purposes": ["future_decision"], "uncertainty": "medium", "source_spans": [self.span(source).to_dict()]}]
        valid = InterpretationReceiptRevision.from_dict(raw)
        self.assertTrue(valid.memory_candidates[0]["candidate_id"].startswith("cmem_"))
        self.assertTrue(self.memory(source, receipt).memory_id.startswith("rmem_"))

    def test_daily_summary_cannot_become_long_term_evidence(self):
        source = self.source(); receipt = self.receipt(source)
        summary = DailySummaryRevision(
            COGNITIVE_SCHEMA_VERSION, "memento_daily_summary_revision", make_daily_summary_id("2026-08-18"),
            1, "active", "generate", STAMP, "2026-08-18", "今天回到验证标准。", ("验证",), (), (), (),
            (self.source_ref(source),), (ObjectRef("interpretation_receipt", receipt.receipt_id, 1, receipt.sha256),),
            "Reviews/Daily/2026-08-18.md", None, None, None,
        )
        with self.assertRaises(ContractError):
            validate_long_term_evidence_refs([ObjectRef("daily_summary", summary.summary_id, 1, summary.sha256)])
        self.assertEqual(validate_long_term_evidence_refs([self.source_ref(source)]), (self.source_ref(source),))

    def test_cognitive_user_action_binds_exact_target_revision_and_payload(self):
        source = self.source(); receipt = self.receipt(source)
        action_id = make_cognitive_action_id("dashboard-once")
        action = CognitiveUserAction(
            COGNITIVE_SCHEMA_VERSION,
            "memento_cognitive_user_action",
            action_id,
            STAMP,
            "edit_receipt",
            ObjectRef("interpretation_receipt", receipt.receipt_id, receipt.revision, receipt.sha256),
            {
                "summary": "我更在意尽早看到真实反馈。",
                "facets": {
                    "content_types": ["observation"],
                    "topics": ["产品设计"],
                    "objects": ["方案评审"],
                    "stance": "self_observation",
                    "cognitive_state": "revises_existing",
                    "purposes": ["future_decision"],
                },
            },
        )
        self.assertEqual(validate_cognitive_user_action(action.to_dict())["id"], action_id)
        wrong_target = action.to_dict()
        wrong_target["target_ref"] = self.source_ref(source).to_dict()
        with self.assertRaises(ContractError):
            validate_cognitive_user_action(wrong_target)
        extra_payload = action.to_dict()
        extra_payload["payload"]["unexpected"] = True
        with self.assertRaises(ContractError):
            validate_cognitive_user_action(extra_payload)

    def test_original_only_action_requires_null_payload(self):
        receipt = self.receipt(self.source())
        value = {
            "schema_version": COGNITIVE_SCHEMA_VERSION,
            "kind": "memento_cognitive_user_action",
            "id": make_cognitive_action_id("original-only"),
            "created_at": STAMP,
            "action": "original_only",
            "target_ref": ObjectRef("interpretation_receipt", receipt.receipt_id, receipt.revision, receipt.sha256).to_dict(),
            "payload": {"reason": "no"},
        }
        with self.assertRaises(ContractError):
            validate_cognitive_user_action(value)

    def test_cognitive_action_result_is_immutable_terminal_receipt(self):
        source = self.source(); receipt = self.receipt(source)
        action = CognitiveUserAction(
            COGNITIVE_SCHEMA_VERSION,
            "memento_cognitive_user_action",
            make_cognitive_action_id("confirm"),
            STAMP,
            "confirm_receipt",
            ObjectRef("interpretation_receipt", receipt.receipt_id, receipt.revision, receipt.sha256),
            None,
        )
        result = CognitiveActionResult(
            COGNITIVE_SCHEMA_VERSION,
            "memento_cognitive_action_result",
            make_cognitive_action_result_id(action.id),
            action.id,
            action.sha256,
            "applied",
            STAMP,
            (ObjectRef("interpretation_receipt", receipt.receipt_id, 2, HB),),
            None,
        )
        self.assertEqual(validate_cognitive_action_result(result.to_dict())["status"], "applied")
        invalid = result.to_dict(); invalid["error_kind"] = "evidence"
        with self.assertRaises(ContractError):
            validate_cognitive_action_result(invalid)

    def test_only_active_agent_profile_understandings_make_peaks(self):
        source = self.source(); receipt = self.receipt(source); memory = self.memory(source, receipt)
        understanding = ObjectRef("understanding", "mem_" + "2" * 24, 3, HB)
        relation = self.relation(source, memory, understanding)
        snapshot = build_landscape_snapshot(as_of="2026-08-18", created_at=STAMP, input_hashes=self.landscape_hashes(), publication_nonce="publish-1", active_understandings=[{"memory_id": understanding.id, "revision": 3, "revision_sha256": HB, "status": "active", "evidence_count": 2}], reusable_memories=[memory], relations=[relation])
        self.assertEqual(snapshot.peaks[0]["peak_id"], make_peak_id(understanding.id))
        self.assertEqual(len(snapshot.nodes), 1)
        absent = build_landscape_snapshot(as_of="2026-08-18", created_at=STAMP, input_hashes=self.landscape_hashes(), publication_nonce="publish-2", active_understandings=[{"memory_id": understanding.id, "revision": 3, "revision_sha256": HB, "status": "candidate"}], reusable_memories=[memory], relations=[relation])
        self.assertEqual(absent.peaks, ())
        self.assertEqual(absent.nodes, ())

    def test_each_landscape_publish_gets_a_new_immutable_snapshot_id(self):
        self.assertNotEqual(
            make_landscape_id(self.landscape_hashes(), "publish-1"),
            make_landscape_id(self.landscape_hashes(), "publish-2"),
        )

    def test_landscape_rejects_edge_outside_current_formal_graph(self):
        source = self.source(); receipt = self.receipt(source); memory = self.memory(source, receipt)
        understanding = ObjectRef("understanding", "mem_" + "2" * 24, 3, HB)
        snapshot = build_landscape_snapshot(as_of="2026-08-18", created_at=STAMP, input_hashes=self.landscape_hashes(), publication_nonce="publish-1", active_understandings=[{"memory_id": understanding.id, "revision": 3, "revision_sha256": HB, "status": "active", "evidence_count": 2}], reusable_memories=[memory], relations=[self.relation(source, memory, understanding)])
        invalid = snapshot.to_dict(); invalid["edges"][0]["to_id"] = "mem_" + "3" * 24
        with self.assertRaises(ContractError):
            validate_landscape_snapshot(invalid)

    def test_home_projection_is_refs_and_receipts_not_original_text(self):
        source = self.source(); receipt = self.receipt(source)
        input_hashes = {"record_head_sha256": HA, "receipt_head_sha256": HB, "daily_bundle_head_sha256": HC, "agent_profile_sha256": HA, "landscape_snapshot_sha256": HB, "user_action_watermark_sha256": HC}
        home = {"schema_version": COGNITIVE_SCHEMA_VERSION, "kind": "memento_home_projection", "projection_version": HOME_PROJECTION_VERSION, "generated_at": STAMP, "local_date": "2026-08-18", "input_hashes": input_hashes, "landscape_ref": {"snapshot_id": make_landscape_id(self.landscape_hashes(), "publish-1"), "snapshot_sha256": HB}, "landscape_summary": {"active_understandings": 0, "recent_changes": 0, "observing_candidates": 0}, "today_status": {"saved": 1, "interpreted": 1, "merged": 0, "needs_review": 0, "daily_run_status": "not_started"}, "records": [{"record_ref": self.source_ref(source).to_dict(), "receipt_ref": ObjectRef("interpretation_receipt", receipt.receipt_id, 1, receipt.sha256).to_dict(), "captured_at": STAMP, "source_type": "voice_transcript", "source_app": "Memento Voice Capture", "status": "ready", "summary": receipt.summary, "content_types": ["observation"], "topics": ["产品设计"], "purposes": ["future_decision"], "memory_refs": [], "understanding_refs": []}], "schedule": {"enabled": True, "hour": 21, "minute": 0, "next_due_at": "2026-08-19T21:00:00+08:00", "last_run_status": "not_started"}, "warnings": []}
        self.assertEqual(validate_home_projection(home)["kind"], "memento_home_projection")
        invalid = copy.deepcopy(home); invalid["records"][0]["original_content"] = "原文"
        with self.assertRaises(ContractError):
            validate_home_projection(invalid)

    def test_home_projection_allows_missing_receipt_only_before_terminal_interpretation(self):
        source = self.source()
        input_hashes = {key: HA for key in ("record_head_sha256", "receipt_head_sha256", "daily_bundle_head_sha256", "agent_profile_sha256", "landscape_snapshot_sha256", "user_action_watermark_sha256")}
        home = {"schema_version": COGNITIVE_SCHEMA_VERSION, "kind": "memento_home_projection", "projection_version": HOME_PROJECTION_VERSION, "generated_at": STAMP, "local_date": "2026-08-18", "input_hashes": input_hashes, "landscape_ref": {"snapshot_id": make_landscape_id(self.landscape_hashes(), "publish-raw"), "snapshot_sha256": HB}, "landscape_summary": {"active_understandings": 0, "recent_changes": 0, "observing_candidates": 0}, "today_status": {"saved": 1, "interpreted": 0, "merged": 0, "needs_review": 0, "daily_run_status": "not_started"}, "records": [{"record_ref": self.source_ref(source).to_dict(), "receipt_ref": None, "captured_at": STAMP, "source_type": "voice_transcript", "source_app": "Memento Voice Capture", "status": "raw_saved", "summary": None, "content_types": [], "topics": [], "purposes": [], "memory_refs": [], "understanding_refs": []}], "schedule": {"enabled": False, "hour": 21, "minute": 0, "next_due_at": "2026-08-18T21:00:00+08:00", "last_run_status": "not_started"}, "warnings": []}
        self.assertEqual(validate_home_projection(home)["records"][0]["status"], "raw_saved")
        invalid = copy.deepcopy(home); invalid["records"][0]["status"] = "ready"
        with self.assertRaises(ContractError):
            validate_home_projection(invalid)


if __name__ == "__main__":
    unittest.main()
