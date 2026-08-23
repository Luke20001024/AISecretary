#!/usr/bin/env python3
"""Focused durable-action tests using only temporary local state."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "context-agent"))

from cognitive_actions_v1 import CognitiveActionStore  # noqa: E402
from cognitive_v1 import (  # noqa: E402
    COGNITIVE_SCHEMA_VERSION,
    CognitiveUserAction,
    InterpretationReceiptRevision,
    ObjectRef,
    RelationRevision,
    ReusableMemoryRevision,
    SourceRecordRevision,
    SourceSpan,
    make_capture_record_id,
    make_cognitive_action_id,
    make_receipt_id,
    make_relation_id,
    make_reusable_memory_id,
    persisted_json_bytes,
)
from core import ContractError, sha256_bytes  # noqa: E402


STAMP = "2026-08-18T10:00:00+08:00"
STAMP_2 = "2026-08-18T10:01:00+08:00"
STAMP_3 = "2026-08-18T10:02:00+08:00"
NOW = dt.datetime(2026, 8, 18, 10, 5, tzinfo=dt.timezone(dt.timedelta(hours=8)))
HA, HB, HC = (sha256_bytes(value) for value in (b"a", b"b", b"c"))


@dataclass(frozen=True)
class _CommitResult:
    status: str
    object_ref: ObjectRef


class _FakeFormalStore:
    def __init__(self) -> None:
        self.memories: dict[str, ReusableMemoryRevision] = {}
        self.relations: dict[str, RelationRevision] = {}

    def add(self, value):
        if isinstance(value, ReusableMemoryRevision):
            self.memories[value.memory_id] = value
        else:
            self.relations[value.relation_id] = value

    def load_memory_head(self, memory_id: str) -> ReusableMemoryRevision:
        try:
            return self.memories[memory_id]
        except KeyError as exc:
            raise ContractError("memory missing", kind="not_found") from exc

    def load_relation_head(self, relation_id: str) -> RelationRevision:
        try:
            return self.relations[relation_id]
        except KeyError as exc:
            raise ContractError("relation missing", kind="not_found") from exc

    def commit_user_memory_revision(self, value, *, expected_ref):
        proposal = value if isinstance(value, ReusableMemoryRevision) else ReusableMemoryRevision.from_dict(value)
        current = self.load_memory_head(proposal.memory_id)
        if self._ref(current) != expected_ref:
            raise ContractError("memory CAS", kind="conflict")
        if current.status == "tombstone":
            raise ContractError("no revive", kind="conflict")
        self.memories[proposal.memory_id] = proposal
        return _CommitResult("applied", self._ref(proposal))

    def commit_user_relation_revision(self, value, *, expected_ref):
        proposal = value if isinstance(value, RelationRevision) else RelationRevision.from_dict(value)
        current = self.load_relation_head(proposal.relation_id)
        if self._ref(current) != expected_ref:
            raise ContractError("relation CAS", kind="conflict")
        if current.status == "tombstone":
            raise ContractError("no revive", kind="conflict")
        self.relations[proposal.relation_id] = proposal
        return _CommitResult("applied", self._ref(proposal))

    def find_user_action_materialization(self, kind: str, identifier: str, action_id: str):
        current = (
            self.memories.get(identifier)
            if kind == "reusable_memory"
            else self.relations.get(identifier)
        )
        if current is None or current.provenance.get("user_action_id") != action_id:
            return None
        return self._ref(current)

    @staticmethod
    def _ref(value):
        if isinstance(value, ReusableMemoryRevision):
            return ObjectRef("reusable_memory", value.memory_id, value.revision, value.sha256)
        return ObjectRef("relation", value.relation_id, value.revision, value.sha256)


class CognitiveActionStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.vault = Path(self.temporary.name) / "vault"
        self.vault.mkdir(mode=0o700)
        self.store = CognitiveActionStore(self.vault)
        self.formal = _FakeFormalStore()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def source(self, nonce: str = "one") -> SourceRecordRevision:
        return SourceRecordRevision(
            COGNITIVE_SCHEMA_VERSION,
            "memento_source_record_revision",
            make_capture_record_id(nonce),
            1,
            "active",
            "ingest",
            STAMP,
            STAMP,
            "2026-08-18",
            "text",
            "Memento",
            "2026-08-18.md",
            1,
            3,
            HA,
            HB,
            (),
            "capture_service",
            None,
        )

    def source_ref(self, source: SourceRecordRevision) -> ObjectRef:
        return ObjectRef("source_record", source.record_id, source.revision, source.sha256)

    def span(self, source: SourceRecordRevision) -> SourceSpan:
        quote = f"一条可核对的原文 {source.record_id[-4:]}"
        return SourceSpan(
            source.record_id,
            source.revision,
            source.sha256,
            source.source_file,
            2,
            2,
            quote,
            sha256_bytes(quote.encode("utf-8")),
        )

    def receipt(self, nonce: str = "one") -> InterpretationReceiptRevision:
        source = self.source(nonce)
        return InterpretationReceiptRevision(
            COGNITIVE_SCHEMA_VERSION,
            "memento_interpretation_receipt_revision",
            make_receipt_id(source.record_id),
            1,
            "ready",
            "interpret",
            STAMP,
            "ireq_" + "1" * 24,
            "irun_" + "1" * 24,
            self.source_ref(source),
            None,
            "先验证最小闭环。",
            {
                "content_types": ["observation"],
                "topics": ["产品设计"],
                "objects": ["方案评审"],
                "stance": "self_observation",
                "cognitive_state": "first_seen",
                "purposes": ["future_decision"],
            },
            (),
            (),
            (self.span(source),),
            "record-interpreter-v1",
            HC,
            None,
        )

    def seed_receipt(self, receipt: InterpretationReceiptRevision) -> None:
        self.store._ensure_layout()
        path = self.store.receipts_dir / f"{receipt.receipt_id}.r000001.json"
        path.write_bytes(persisted_json_bytes(receipt))
        path.chmod(0o600)

    def edited_source(self, source: SourceRecordRevision) -> SourceRecordRevision:
        value = source.to_dict()
        value.update(
            revision=source.revision + 1,
            operation="source_edit",
            created_at=STAMP_2,
            entry_sha256=HC,
            source_snapshot_sha256=HA,
            previous_revision_sha256=source.sha256,
        )
        return SourceRecordRevision.from_dict(value)

    def receipt_ref(self, receipt: InterpretationReceiptRevision) -> ObjectRef:
        return ObjectRef(
            "interpretation_receipt", receipt.receipt_id, receipt.revision, receipt.sha256
        )

    def memory(self, nonce: str = "one") -> ReusableMemoryRevision:
        source = self.source(f"source-{nonce}")
        receipt = self.receipt(f"receipt-{nonce}")
        return ReusableMemoryRevision(
            COGNITIVE_SCHEMA_VERSION,
            "memento_reusable_memory_revision",
            make_reusable_memory_id(nonce),
            1,
            "active",
            "new",
            STAMP,
            "评审前先定义最早可验证部分。",
            "decision",
            ("产品设计",),
            ("future_decision",),
            "low",
            (self.span(source),),
            (self.receipt_ref(receipt),),
            {
                "origin": "daily_integrator",
                "run_id": "drun_" + "1" * 24,
                "bundle_id": "db_20260818",
                "bundle_revision": 1,
                "user_action_id": None,
            },
            None,
        )

    def relation(self, memory: ReusableMemoryRevision, nonce: str = "one") -> RelationRevision:
        source = self.source(f"relation-{nonce}")
        return RelationRevision(
            COGNITIVE_SCHEMA_VERSION,
            "memento_relation_revision",
            make_relation_id(nonce),
            1,
            "active",
            "new",
            STAMP,
            "supports",
            ObjectRef("reusable_memory", memory.memory_id, memory.revision, memory.sha256),
            ObjectRef("understanding", "mem_" + "2" * 24, 1, HA),
            "directed",
            "这条记忆支持当前理解。",
            "low",
            (self.span(source),),
            "2026-08-18",
            {
                "origin": "daily_integrator",
                "run_id": "drun_" + "1" * 24,
                "bundle_id": "db_20260818",
                "bundle_revision": 1,
                "user_action_id": None,
            },
            None,
        )

    def action(self, nonce: str, action: str, target: ObjectRef, payload, *, created_at=STAMP):
        return CognitiveUserAction(
            COGNITIVE_SCHEMA_VERSION,
            "memento_cognitive_user_action",
            make_cognitive_action_id(nonce),
            created_at,
            action,
            target,
            payload,
        )

    def edit_receipt_payload(self):
        return {
            "summary": "我更在意尽早看到真实反馈。",
            "facets": {
                "content_types": ["observation"],
                "topics": ["产品设计"],
                "objects": ["方案评审"],
                "stance": "self_observation",
                "cognitive_state": "revises_existing",
                "purposes": ["future_decision"],
            },
        }

    def test_confirm_receipt_appends_revision_and_result_is_idempotent(self):
        receipt = self.receipt()
        self.seed_receipt(receipt)
        action = self.action("confirm", "confirm_receipt", self.receipt_ref(receipt), None)
        stored = self.store.submit_action(action)
        report = self.store.reconcile(now=NOW)
        self.assertEqual((report.applied, report.conflict, report.rejected), (1, 0, 0))
        head = self.store.load_receipt_head(receipt.receipt_id)
        self.assertEqual((head.revision, head.operation, head.user_action_id), (2, "user_confirm", action.id))
        result = self.store.load_result(action.id)
        self.assertEqual(result.status, "applied")
        self.assertEqual(result.action_sha256, stored.sha256)
        self.assertEqual(result.materialized_refs[0].revision, 2)
        first_result_bytes = self.store._result_path(action.id).read_bytes()
        again = self.store.reconcile(now=NOW + dt.timedelta(minutes=5))
        self.assertEqual((again.already_resolved, again.processed), (1, 0))
        self.assertEqual(self.store._result_path(action.id).read_bytes(), first_result_bytes)

    def test_edit_receipt_materializes_exact_user_payload(self):
        receipt = self.receipt("edit")
        self.seed_receipt(receipt)
        payload = self.edit_receipt_payload()
        action = self.action(
            "edit-receipt",
            "edit_receipt",
            self.receipt_ref(receipt),
            payload,
        )
        self.store.submit_action(action)

        report = self.store.reconcile(now=NOW)

        self.assertEqual(report.applied, 1)
        head = self.store.load_receipt_head(receipt.receipt_id)
        self.assertEqual((head.revision, head.operation), (2, "user_edit"))
        self.assertEqual(head.summary, payload["summary"])
        self.assertEqual(dict(head.facets), payload["facets"])
        self.assertEqual((head.memory_candidates, head.relation_candidates), ((), ()))

    def test_automatic_interpret_revision_may_advance_same_source_record(self):
        receipt = self.receipt("source-edit")
        self.seed_receipt(receipt)
        source = self.source("source-edit")
        edited = self.edited_source(source)
        value = receipt.to_dict()
        value.update(
            revision=2,
            created_at=STAMP_2,
            request_id="ireq_" + "2" * 24,
            run_id="irun_" + "2" * 24,
            record_ref=self.source_ref(edited).to_dict(),
            source_spans=[self.span(edited).to_dict()],
            previous_revision_sha256=receipt.sha256,
        )
        revised = InterpretationReceiptRevision.from_dict(value)
        path = self.store._receipt_revision_path(revised.receipt_id, 2)
        self.store._safe_write_immutable(
            path,
            persisted_json_bytes(revised),
            name="interpretation receipt revision",
        )

        head = self.store.load_receipt_head(receipt.receipt_id)
        self.assertEqual((head.revision, head.operation), (2, "interpret"))
        self.assertEqual(head.record_ref, self.source_ref(edited))

    def test_automatic_interpret_cannot_swap_source_identity_or_rewind_revision(self):
        receipt = self.receipt("source-transition")
        self.seed_receipt(receipt)
        value = receipt.to_dict()
        value.update(
            revision=2,
            created_at=STAMP_2,
            request_id="ireq_" + "3" * 24,
            run_id="irun_" + "3" * 24,
            record_ref={
                "kind": "source_record",
                "id": receipt.record_ref.id,
                "revision": receipt.record_ref.revision,
                "revision_sha256": HB,
            },
            source_spans=[
                {
                    **receipt.source_spans[0].to_dict(),
                    "record_revision_sha256": HB,
                }
            ],
            previous_revision_sha256=receipt.sha256,
        )
        invalid = InterpretationReceiptRevision.from_dict(value)
        path = self.store._receipt_revision_path(invalid.receipt_id, 2)
        self.store._safe_write_immutable(
            path,
            persisted_json_bytes(invalid),
            name="interpretation receipt revision",
        )

        with self.assertRaises(ContractError) as raised:
            self.store.load_receipt_head(receipt.receipt_id)
        self.assertEqual(raised.exception.kind, "evidence")

    def test_original_only_has_priority_and_cannot_be_reactivated(self):
        receipt = self.receipt("terminal")
        self.seed_receipt(receipt)
        ref = self.receipt_ref(receipt)
        edit = self.action("edit-old", "edit_receipt", ref, self.edit_receipt_payload(), created_at=STAMP)
        terminal = self.action("original", "original_only", ref, None, created_at=STAMP_2)
        self.store.submit_action(edit)
        self.store.submit_action(terminal)
        report = self.store.reconcile(now=NOW)
        self.assertEqual((report.applied, report.conflict), (1, 1))
        head = self.store.load_receipt_head(receipt.receipt_id)
        self.assertEqual((head.status, head.operation, head.revision), ("original_only", "original_only", 2))
        self.assertIsNone(head.summary)
        self.assertEqual(head.source_spans, ())
        current = self.store.load_receipt_head_ref(receipt.receipt_id)
        revive = self.action("revive", "edit_receipt", current, self.edit_receipt_payload(), created_at=STAMP_3)
        self.store.submit_action(revive)
        after = self.store.reconcile(now=NOW)
        self.assertEqual(after.rejected, 1)
        self.assertEqual(self.store.load_result(revive.id).error_kind, "action")
        self.assertEqual(self.store.load_receipt_head(receipt.receipt_id).revision, 2)

    def test_receipt_base_hash_change_is_conflict(self):
        receipt = self.receipt("cas")
        self.seed_receipt(receipt)
        stale = self.action("stale", "confirm_receipt", self.receipt_ref(receipt), None)
        self.store.submit_action(stale)
        other = self.action("other", "confirm_receipt", self.receipt_ref(receipt), None)
        raw = receipt.to_dict()
        raw.update(
            revision=2,
            operation="user_confirm",
            user_action_id=other.id,
            previous_revision_sha256=receipt.sha256,
            created_at=STAMP_2,
        )
        self.store.commit_user_receipt_revision(
            InterpretationReceiptRevision.from_dict(raw), expected_ref=self.receipt_ref(receipt)
        )
        report = self.store.reconcile(now=NOW)
        self.assertEqual(report.conflict, 1)
        self.assertEqual(self.store.load_result(stale.id).error_kind, "conflict")

    def test_missing_result_recovers_from_materialized_receipt_revision(self):
        receipt = self.receipt("recovery")
        self.seed_receipt(receipt)
        action = self.action("recover", "confirm_receipt", self.receipt_ref(receipt), None)
        self.store.submit_action(action)
        raw = receipt.to_dict()
        raw.update(
            revision=2,
            operation="user_confirm",
            user_action_id=action.id,
            previous_revision_sha256=receipt.sha256,
            created_at=STAMP_2,
        )
        self.store.commit_user_receipt_revision(
            InterpretationReceiptRevision.from_dict(raw), expected_ref=self.receipt_ref(receipt)
        )
        report = self.store.reconcile(now=NOW)
        self.assertEqual(report.applied, 1)
        self.assertEqual(self.store.load_result(action.id).materialized_refs[0].revision, 2)

    def test_memory_edit_and_report_outcome(self):
        memory = self.memory()
        self.formal.add(memory)
        base = _FakeFormalStore._ref(memory)
        edit = self.action(
            "memory-edit",
            "edit_reusable_memory",
            base,
            {
                "statement": "评审前先发可验证的最小稿。",
                "topics": ["产品设计", "验证"],
                "purposes": ["future_decision", "action_clue"],
            },
        )
        self.store.submit_action(edit)
        self.assertEqual(self.store.reconcile(formal_store=self.formal, now=NOW).applied, 1)
        head = self.formal.load_memory_head(memory.memory_id)
        self.assertEqual((head.revision, head.operation, head.provenance["user_action_id"]), (2, "user_edit", edit.id))
        outcome = self.action(
            "outcome",
            "report_outcome",
            _FakeFormalStore._ref(head),
            {"outcome": "这次更早收到了反馈。", "occurred_at": STAMP_3},
        )
        before_refs, before_hash = self.store.action_watermark()
        self.store.submit_action(outcome)
        after_refs, after_hash = self.store.action_watermark()
        self.assertEqual(len(after_refs), len(before_refs) + 1)
        self.assertNotEqual(before_hash, after_hash)
        self.assertEqual(self.store.reconcile(formal_store=self.formal, now=NOW).applied, 1)
        result = self.store.load_result(outcome.id)
        self.assertEqual(result.materialized_refs, ())
        self.assertEqual(self.formal.load_memory_head(memory.memory_id).revision, 2)

    def test_memory_delete_wins_over_edit_and_tombstone_never_revives(self):
        memory = self.memory("terminal")
        self.formal.add(memory)
        ref = _FakeFormalStore._ref(memory)
        edit = self.action(
            "memory-edit-loses",
            "edit_reusable_memory",
            ref,
            {"statement": "试图修改", "topics": ["产品"], "purposes": ["find_later"]},
            created_at=STAMP,
        )
        delete = self.action("memory-delete", "delete_reusable_memory", ref, None, created_at=STAMP_2)
        self.store.submit_action(edit)
        self.store.submit_action(delete)
        report = self.store.reconcile(formal_store=self.formal, now=NOW)
        self.assertEqual((report.applied, report.conflict), (1, 1))
        head = self.formal.load_memory_head(memory.memory_id)
        self.assertEqual((head.status, head.operation, head.provenance["user_action_id"]), ("tombstone", "tombstone", delete.id))
        revive = self.action(
            "memory-revive",
            "edit_reusable_memory",
            _FakeFormalStore._ref(head),
            {"statement": "不应复活", "topics": ["产品"], "purposes": ["find_later"]},
        )
        self.store.submit_action(revive)
        self.assertEqual(self.store.reconcile(formal_store=self.formal, now=NOW).rejected, 1)
        self.assertEqual(self.formal.load_memory_head(memory.memory_id).revision, 2)

    def test_relation_edit_derives_direction_then_delete_tombstones(self):
        memory = self.memory("relation")
        relation = self.relation(memory)
        self.formal.add(memory)
        self.formal.add(relation)
        edit = self.action(
            "relation-edit",
            "edit_relation",
            _FakeFormalStore._ref(relation),
            {"type": "same_topic", "statement": "两者属于同一讨论主题。"},
        )
        self.store.submit_action(edit)
        self.assertEqual(self.store.reconcile(formal_store=self.formal, now=NOW).applied, 1)
        edited = self.formal.load_relation_head(relation.relation_id)
        self.assertEqual((edited.type, edited.direction, edited.operation), ("same_topic", "undirected", "user_edit"))
        delete = self.action(
            "relation-delete",
            "delete_relation",
            _FakeFormalStore._ref(edited),
            None,
        )
        self.store.submit_action(delete)
        self.assertEqual(self.store.reconcile(formal_store=self.formal, now=NOW).applied, 1)
        deleted = self.formal.load_relation_head(relation.relation_id)
        self.assertEqual((deleted.status, deleted.operation), ("tombstone", "tombstone"))

    def test_invalid_action_gets_rejected_terminal_result(self):
        self.store._ensure_layout()
        action_id = make_cognitive_action_id("invalid")
        path = self.store.actions_dir / f"{action_id}.json"
        path.write_text(json.dumps({"id": action_id}), encoding="utf-8")
        path.chmod(0o600)
        report = self.store.reconcile(now=NOW)
        self.assertEqual(report.rejected, 1)
        result = self.store.load_result(action_id)
        self.assertEqual((result.status, result.error_kind), ("rejected", "schema"))

    def test_exact_browser_staging_file_is_ignored_but_other_names_fail_closed(self):
        self.store._ensure_layout()
        staging = (
            self.store.actions_dir
            / (
                ".memento-cact_"
                + "a" * 24
                + ".json-12345678-1234-1234-1234-123456789abc.tmp"
            )
        )
        staging.write_text('{"partial": true}\n', encoding="utf-8")
        staging.chmod(0o600)

        refs, watermark = self.store.action_watermark()
        self.assertEqual(refs, ())
        self.assertRegex(watermark, r"^[0-9a-f]{64}$")
        report = self.store.reconcile(now=NOW)
        self.assertEqual(report.seen, 0)

        unexpected = self.store.actions_dir / ".memento-unrelated.tmp"
        unexpected.write_text("unexpected\n", encoding="utf-8")
        unexpected.chmod(0o600)
        with self.assertRaises(ContractError) as caught:
            self.store.action_watermark()
        self.assertEqual(caught.exception.kind, "evidence")

    def test_action_filename_id_mismatch_is_rejected_not_materialized(self):
        receipt = self.receipt("mismatch")
        self.seed_receipt(receipt)
        action = self.action("inside", "confirm_receipt", self.receipt_ref(receipt), None)
        outside_id = make_cognitive_action_id("outside")
        self.store._ensure_layout()
        path = self.store.actions_dir / f"{outside_id}.json"
        path.write_bytes(persisted_json_bytes(action))
        path.chmod(0o600)
        report = self.store.reconcile(now=NOW)
        self.assertEqual(report.rejected, 1)
        self.assertEqual(self.store.load_receipt_head(receipt.receipt_id).revision, 1)
        self.assertEqual(self.store.load_result(outside_id).error_kind, "action")

    def test_permission_symlink_hardlink_and_owner_checks_fail_closed(self):
        receipt = self.receipt("security")
        self.seed_receipt(receipt)
        action = self.action("security", "confirm_receipt", self.receipt_ref(receipt), None)
        self.store.submit_action(action)
        action_path = self.store._action_path(action.id)

        action_path.chmod(0o644)
        with self.assertRaises(ContractError) as broad:
            self.store.reconcile(now=NOW)
        self.assertEqual(broad.exception.kind, "evidence")
        action_path.chmod(0o600)

        hardlink = Path(self.temporary.name) / "action-hardlink.json"
        os.link(action_path, hardlink)
        with self.assertRaises(ContractError) as linked:
            self.store.reconcile(now=NOW)
        self.assertEqual(linked.exception.kind, "evidence")
        hardlink.unlink()

        with mock.patch("cognitive_actions_v1.os.getuid", return_value=os.getuid() + 1):
            with self.assertRaises(ContractError) as foreign:
                self.store.reconcile(now=NOW)
        self.assertEqual(foreign.exception.kind, "evidence")

        action_path.unlink()
        target = Path(self.temporary.name) / "outside-action.json"
        target.write_text("{}", encoding="utf-8")
        action_path.symlink_to(target)
        with self.assertRaises(ContractError) as symbolic:
            self.store.reconcile(now=NOW)
        self.assertEqual(symbolic.exception.kind, "evidence")

    def test_unsafe_directory_and_result_symlink_fail_closed(self):
        receipt = self.receipt("directory")
        self.seed_receipt(receipt)
        action = self.action("directory", "confirm_receipt", self.receipt_ref(receipt), None)
        self.store.submit_action(action)

        self.store.actions_dir.chmod(0o755)
        with self.assertRaises(ContractError) as directory:
            self.store.reconcile(now=NOW)
        self.assertEqual(directory.exception.kind, "evidence")
        self.store.actions_dir.chmod(0o700)

        outside = Path(self.temporary.name) / "outside-result.json"
        outside.write_text("{}", encoding="utf-8")
        result_path = self.store._result_path(action.id)
        result_path.symlink_to(outside)
        with self.assertRaises(ContractError) as result:
            self.store.reconcile(now=NOW)
        self.assertEqual(result.exception.kind, "evidence")

    def test_state_root_outside_vault_is_rejected(self):
        with self.assertRaises(ContractError) as raised:
            CognitiveActionStore(self.vault, state_root=Path(self.temporary.name) / "outside")
        self.assertEqual(raised.exception.kind, "evidence")


if __name__ == "__main__":
    unittest.main()
