from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_AGENT = ROOT / "context-agent"
if str(CONTEXT_AGENT) not in sys.path:
    sys.path.insert(0, str(CONTEXT_AGENT))

from cognitive_projection_v1 import (  # noqa: E402
    PEAK_HIT_GAP_PX,
    PEAK_HIT_STROKE_WIDTH_PX,
    PEAK_KDE_CLEARANCE_MULTIPLIER,
    PEAK_MAP_HEIGHT_PX,
    PEAK_MAP_WIDTH_PX,
    CognitiveProjectionPublisher,
)
from cognitive_v1 import (  # noqa: E402
    COGNITIVE_SCHEMA_VERSION,
    InterpretationReceiptRevision,
    LandscapeSnapshot,
    ObjectRef,
    RelationRevision,
    ReusableMemoryRevision,
    SourceRecordRevision,
    SourceSpan,
    make_receipt_id,
    persisted_json_bytes,
)
from core import ContractError, canonical_json, sha256_bytes  # noqa: E402


TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 8, 18, 21, 0, tzinfo=TZ)
LATER = dt.datetime(2026, 8, 18, 21, 5, tzinfo=TZ)
DATE = "2026-08-18"
RECORD_ID = "rec_111111111111111111111111"
SECOND_RECORD_ID = "rec_222222222222222222222222"
MEMORY_ID = "rmem_333333333333333333333333"
RELATION_ID = "rel_444444444444444444444444"
UNDERSTANDING_ID = "mem_555555555555555555555555"
ACTION_ID = "cact_666666666666666666666666"
QUOTE = "先交付一个可验证的小版本，再补齐完整方案。"
REAL_PEAK_A_ID = "mem_5a9ffe54d9a4de8b7a96454f"
REAL_PEAK_B_ID = "mem_86b018dbdf1267c84504bd68"
CLOSE_PEAK_A_ID = "mem_000000000000000000000001"
CLOSE_PEAK_B_ID = "mem_000000000000000000000045"


def digest(value: object) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def exact_ref(kind: str, identifier: str, value: object) -> ObjectRef:
    return ObjectRef(kind, identifier, value.revision, value.sha256)  # type: ignore[attr-defined]


class FakeRecordStore:
    def __init__(self, records: list[tuple[SourceRecordRevision, ObjectRef]]) -> None:
        self.records = records

    def list_heads(
        self, *, local_date: str | None = None, include_tombstones: bool = False
    ) -> list[dict]:
        return [
            row.to_dict()
            for row, _ in self.records
            if (local_date is None or row.local_date == local_date)
            and (include_tombstones or row.status != "tombstone")
        ]

    def list_head_refs(
        self, *, local_date: str | None = None, include_tombstones: bool = False
    ) -> list[dict]:
        return [
            ref.to_dict()
            for row, ref in self.records
            if (local_date is None or row.local_date == local_date)
            and (include_tombstones or row.status != "tombstone")
        ]


class FakeActionStore:
    def __init__(self) -> None:
        self.receipts: dict[str, tuple[InterpretationReceiptRevision, ObjectRef]] = {}
        self.actions: list[SimpleNamespace] = []

    def load_receipt_head(self, receipt_id: str) -> InterpretationReceiptRevision:
        try:
            return self.receipts[receipt_id][0]
        except KeyError as exc:
            raise ContractError("receipt 不存在", kind="not_found") from exc

    def load_receipt_head_ref(self, receipt_id: str) -> ObjectRef:
        try:
            return self.receipts[receipt_id][1]
        except KeyError as exc:
            raise ContractError("receipt 不存在", kind="not_found") from exc

    def action_watermark(self):
        rows = [
            {"id": row.action_id, "sha256": row.sha256}
            for row in self.actions
        ]
        return tuple(self.actions), digest(rows)


class FakeBundleStore:
    def __init__(self) -> None:
        self.memories: tuple[ReusableMemoryRevision, ...] = ()
        self.relations: tuple[RelationRevision, ...] = ()
        self.bundle_ref: ObjectRef | None = None
        self.manifest: dict | None = None
        self.candidates = ["cmem_aaaaaaaaaaaaaaaaaaaaaaaa"]

    def load_catalog(self) -> dict:
        return {
            "schema_version": COGNITIVE_SCHEMA_VERSION,
            "kind": "memento_cognitive_formal_head_index",
            "revision": 1,
            "generated_at": NOW.isoformat(timespec="seconds"),
            "daily_bundles": []
            if self.bundle_ref is None
            else [self.bundle_ref.to_dict()],
            "daily_summaries": [],
            "reusable_memories": [
                exact_ref("reusable_memory", row.memory_id, row).to_dict()
                for row in self.memories
            ],
            "relations": [
                exact_ref("relation", row.relation_id, row).to_dict()
                for row in self.relations
            ],
        }

    def load_day_bundle_ref(self, local_date: str) -> ObjectRef | None:
        return self.bundle_ref if local_date == DATE else None

    def load_day_manifest(self, local_date: str) -> dict | None:
        return self.manifest if local_date == DATE else None

    def list_active_memories(self) -> tuple[ReusableMemoryRevision, ...]:
        return tuple(row for row in self.memories if row.status == "active")

    def list_active_relations(self) -> tuple[RelationRevision, ...]:
        return tuple(row for row in self.relations if row.status == "active")


class ProjectionCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="memento-projection-")
        self.vault = Path(self.temporary.name) / "vault"
        self.vault.mkdir(mode=0o700)
        self.vault.chmod(0o700)
        self.source, self.source_ref = self.make_source(RECORD_ID)
        self.records = FakeRecordStore([(self.source, self.source_ref)])
        self.actions = FakeActionStore()
        self.bundles = FakeBundleStore()
        self.profile = self.make_profile()
        self.publisher = CognitiveProjectionPublisher(
            self.vault,
            record_store=self.records,
            action_store=self.actions,
            bundle_store=self.bundles,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @property
    def schedule(self) -> dict:
        return {
            "enabled": True,
            "hour": 21,
            "minute": 0,
            "next_due_at": "2026-08-19T21:00:00+08:00",
            "last_run_status": "not_started",
        }

    def make_source(
        self,
        record_id: str,
        *,
        revision: int = 1,
        previous_sha: str | None = None,
        captured_at: str = "2026-08-18T10:00:00+08:00",
    ) -> tuple[SourceRecordRevision, ObjectRef]:
        source = SourceRecordRevision(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_source_record_revision",
            record_id=record_id,
            revision=revision,
            status="active",
            operation="ingest" if revision == 1 else "source_edit",
            created_at=NOW.isoformat(timespec="seconds"),
            captured_at=captured_at,
            local_date=DATE,
            source_type="text",
            source_app="Chrome",
            source_file=f"{DATE}.md",
            line_start=1,
            line_end=3,
            entry_sha256=("1" if revision == 1 else "2") * 64,
            source_snapshot_sha256=("3" if revision == 1 else "4") * 64,
            attachments=(),
            ingest_origin="reconciler",
            previous_revision_sha256=previous_sha,
        )
        return source, exact_ref("source_record", record_id, source)

    def make_span(self, source_ref: ObjectRef | None = None) -> SourceSpan:
        ref = source_ref or self.source_ref
        return SourceSpan(
            record_id=ref.id,
            record_revision=ref.revision,
            record_revision_sha256=ref.revision_sha256,
            source_file=f"{DATE}.md",
            line_start=2,
            line_end=2,
            quote=QUOTE,
            quote_sha256=sha256_bytes(QUOTE.encode("utf-8")),
        )

    def make_receipt(
        self,
        *,
        revision: int = 1,
        previous: InterpretationReceiptRevision | None = None,
        status: str = "ready",
        operation: str = "interpret",
        summary: str = "先交付可验证部分。",
        source_ref: ObjectRef | None = None,
        with_candidates: bool = False,
    ) -> tuple[InterpretationReceiptRevision, ObjectRef]:
        ref = source_ref or self.source_ref
        active = status in {"ready", "needs_review"}
        candidate = {
            "candidate_id": "cmem_aaaaaaaaaaaaaaaaaaaaaaaa",
            "statement": "一个尚未正式归并的候选。",
            "memory_kind": "observation",
            "topics": ["产品"],
            "purposes": ["future_decision"],
            "uncertainty": "medium",
            "source_spans": [self.make_span(ref).to_dict()],
        }
        receipt = InterpretationReceiptRevision(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_interpretation_receipt_revision",
            receipt_id=make_receipt_id(ref.id),
            revision=revision,
            status=status,
            operation=operation,
            created_at=(NOW if revision == 1 else LATER).isoformat(timespec="seconds"),
            request_id="ireq_111111111111111111111111",
            run_id="irun_111111111111111111111111",
            record_ref=ref,
            user_action_id=ACTION_ID if operation in {"user_edit", "original_only"} else None,
            summary=summary if active else None,
            facets={
                "content_types": ["observation"],
                "topics": ["产品设计"],
                "objects": ["方案"],
                "stance": "self_observation",
                "cognitive_state": "first_seen",
                "purposes": ["future_decision"],
            }
            if active
            else {},
            memory_candidates=(candidate,) if with_candidates and active else (),
            relation_candidates=(),
            source_spans=(self.make_span(ref),) if active else (),
            contract_version="record-interpreter-v1",
            feedback_watermark_sha256="7" * 64,
            previous_revision_sha256=None if previous is None else previous.sha256,
        )
        return receipt, exact_ref(
            "interpretation_receipt", receipt.receipt_id, receipt
        )

    def make_profile(self, *, memories: list[dict] | None = None) -> dict:
        rows = memories
        if rows is None:
            rows = [
                {
                    "memory_id": UNDERSTANDING_ID,
                    "revision": 1,
                    "revision_sha256": "8" * 64,
                    "status": "active",
                    "insight_kind": "observation",
                    "created_at": "2026-08-17T21:00:00+08:00",
                    "evidence": [{"file": f"{DATE}.md", "line": 2, "quote": QUOTE}],
                    "counterevidence": [],
                }
            ]
        return {"profile_sha256": digest(rows), "memories": rows}

    def make_memory(
        self,
        *,
        revision: int = 1,
        previous: ReusableMemoryRevision | None = None,
        operation: str | None = None,
        action_id: str | None = None,
        source_ref: ObjectRef | None = None,
    ) -> ReusableMemoryRevision:
        receipt, receipt_ref = self.actions.receipts.get(
            make_receipt_id(RECORD_ID), self.make_receipt()
        )
        del receipt
        return ReusableMemoryRevision(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_reusable_memory_revision",
            memory_id=MEMORY_ID,
            revision=revision,
            status="active",
            operation=operation or ("new" if revision == 1 else "revise"),
            created_at=(NOW if revision == 1 else LATER).isoformat(timespec="seconds"),
            statement="先交付可验证部分。" if revision == 1 else "用户改写后的可用记忆。",
            memory_kind="decision",
            topics=("产品设计",),
            purposes=("future_decision",),
            uncertainty="low",
            source_spans=(self.make_span(source_ref),),
            origin_receipt_refs=(receipt_ref,),
            provenance={
                "origin": "user" if action_id else "daily_integrator",
                "run_id": "drun_111111111111111111111111",
                "bundle_id": "db_20260818",
                "bundle_revision": revision,
                "user_action_id": action_id,
            },
            previous_revision_sha256=None if previous is None else previous.sha256,
        )

    def make_relation(
        self,
        memory: ReusableMemoryRevision,
        *,
        revision: int = 1,
        previous: RelationRevision | None = None,
        operation: str | None = None,
        action_id: str | None = None,
    ) -> RelationRevision:
        return RelationRevision(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_relation_revision",
            relation_id=RELATION_ID,
            revision=revision,
            status="active",
            operation=operation or ("new" if revision == 1 else "revise"),
            created_at=(NOW if revision == 1 else LATER).isoformat(timespec="seconds"),
            type="supports",
            from_ref=exact_ref("reusable_memory", MEMORY_ID, memory),
            to_ref=ObjectRef("understanding", UNDERSTANDING_ID, 1, "8" * 64),
            direction="directed",
            statement="这条记忆支持当前理解。",
            uncertainty="low",
            source_spans=(self.make_span(),),
            valid_from=DATE,
            provenance={
                "origin": "user" if action_id else "daily_integrator",
                "run_id": "drun_111111111111111111111111",
                "bundle_id": "db_20260818",
                "bundle_revision": revision,
                "user_action_id": action_id,
            },
            previous_revision_sha256=None if previous is None else previous.sha256,
        )

    def make_graph_relation(
        self,
        relation_id: str,
        from_ref: ObjectRef,
        to_ref: ObjectRef,
    ) -> RelationRevision:
        return RelationRevision(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_relation_revision",
            relation_id=relation_id,
            revision=1,
            status="active",
            operation="new",
            created_at=NOW.isoformat(timespec="seconds"),
            type="supports",
            from_ref=from_ref,
            to_ref=to_ref,
            direction="directed",
            statement="正式关系。",
            uncertainty="low",
            source_spans=(self.make_span(),),
            valid_from=DATE,
            provenance={
                "origin": "daily_integrator",
                "run_id": "drun_111111111111111111111111",
                "bundle_id": "db_20260818",
                "bundle_revision": 1,
                "user_action_id": None,
            },
            previous_revision_sha256=None,
        )

    @staticmethod
    def peak_by_id(landscape: LandscapeSnapshot, identifier: str) -> dict:
        return next(
            row
            for row in landscape.peaks
            if row["understanding_ref"]["id"] == identifier
        )

    @staticmethod
    def peak_hit_radii(peak: dict) -> tuple[float, float]:
        bounded = min(peak["evidence_count"], 12)
        outer_x = 74.0 + peak["elevation"] * 42.0 + bounded * 2.4
        outer_y = 48.0 + peak["elevation"] * 31.0 + bounded * 1.7
        stroke_radius = PEAK_HIT_STROKE_WIDTH_PX / 2.0
        return (
            max(66.0, outer_x * 0.72) + stroke_radius,
            max(42.0, outer_y * 0.7) + stroke_radius,
        )

    def assert_peak_hit_targets_clear(self, left: dict, right: dict) -> None:
        left_rx, left_ry = self.peak_hit_radii(left)
        right_rx, right_ry = self.peak_hit_radii(right)
        dx = abs(left["x"] - right["x"]) * PEAK_MAP_WIDTH_PX
        dy = abs(left["y"] - right["y"]) * PEAK_MAP_HEIGHT_PX
        self.assertTrue(
            dx >= left_rx + right_rx + PEAK_HIT_GAP_PX
            or dy >= left_ry + right_ry + PEAK_HIT_GAP_PX
        )

    @staticmethod
    def peak_kde_clearance_squared(left: dict, right: dict) -> float:
        left_bounded = min(left["evidence_count"], 18)
        right_bounded = min(right["evidence_count"], 18)
        left_sigma_x = 92.0 + left_bounded * 2.6
        right_sigma_x = 92.0 + right_bounded * 2.6
        left_sigma_y = 58.0 + left_bounded * 1.8
        right_sigma_y = 58.0 + right_bounded * 1.8
        dx = abs(left["x"] - right["x"]) * PEAK_MAP_WIDTH_PX
        dy = abs(left["y"] - right["y"]) * PEAK_MAP_HEIGHT_PX
        return (
            dx
            / (
                PEAK_KDE_CLEARANCE_MULTIPLIER
                * (left_sigma_x + right_sigma_x)
            )
        ) ** 2 + (
            dy
            / (
                PEAK_KDE_CLEARANCE_MULTIPLIER
                * (left_sigma_y + right_sigma_y)
            )
        ) ** 2

    def install_legacy_landscape_head(
        self,
        baseline: LandscapeSnapshot,
        positions: dict[str, tuple[float, float]],
    ) -> LandscapeSnapshot:
        raw = baseline.to_dict()
        raw["snapshot_id"] = "lnd_eeeeeeeeeeeeeeeeeeeeeeee"
        raw["previous_snapshot_sha256"] = baseline.sha256
        for peak in raw["peaks"]:
            identifier = peak["understanding_ref"]["id"]
            peak["x"], peak["y"] = positions[identifier]
        legacy = LandscapeSnapshot.from_dict(raw)
        payload = persisted_json_bytes(legacy)
        self.publisher._safe_write_immutable(
            self.publisher.snapshots_dir / f"{legacy.snapshot_id}.json", payload
        )
        self.publisher._safe_replace(
            self.publisher.landscape_head_path,
            persisted_json_bytes(
                {
                    "schema_version": COGNITIVE_SCHEMA_VERSION,
                    "kind": "memento_landscape_head",
                    "snapshot_id": legacy.snapshot_id,
                    "snapshot_sha256": sha256_bytes(payload),
                }
            ),
        )
        return legacy

    def install_formal_graph(self) -> tuple[ReusableMemoryRevision, RelationRevision]:
        memory = self.make_memory()
        relation = self.make_relation(memory)
        self.bundles.memories = (memory,)
        self.bundles.relations = (relation,)
        return memory, relation

    def publish_landscape(self, nonce: str = "one"):
        return self.publisher.publish_landscape(
            local_date=DATE,
            now=NOW,
            profile=self.profile,
            publication_nonce=nonce,
        )[0]

    def test_candidate_isolation_and_multi_peak_formal_node(self) -> None:
        receipt, receipt_ref = self.make_receipt(with_candidates=True)
        self.actions.receipts[receipt.receipt_id] = (receipt, receipt_ref)
        memory, relation = self.install_formal_graph()
        second_understanding = "mem_999999999999999999999999"
        rows = list(self.profile["memories"])
        rows.append(
            {
                **rows[0],
                "memory_id": second_understanding,
                "revision_sha256": "9" * 64,
            }
        )
        self.profile = self.make_profile(memories=rows)
        second_relation = RelationRevision.from_dict(
            {
                **relation.to_dict(),
                "relation_id": "rel_999999999999999999999999",
                "to_ref": ObjectRef(
                    "understanding", second_understanding, 1, "9" * 64
                ).to_dict(),
            }
        )
        self.bundles.relations = (relation, second_relation)

        landscape = self.publish_landscape()
        rendered = json.dumps(landscape.to_dict(), ensure_ascii=False)
        self.assertEqual(len(landscape.peaks), 2)
        self.assertEqual(len(landscape.nodes), 1)
        self.assertEqual(len(landscape.edges), 2)
        self.assertNotIn("cmem_aaaaaaaaaaaaaaaaaaaaaaaa", rendered)
        self.assertNotIn("尚未正式归并", rendered)
        self.assertEqual(memory.memory_id, landscape.nodes[0]["memory_ref"]["id"])

    def test_each_publish_is_immutable_chained_and_coordinates_stay_stable(self) -> None:
        self.install_formal_graph()
        first = self.publish_landscape("first")
        second = self.publish_landscape("second")
        self.assertNotEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(second.previous_snapshot_sha256, first.sha256)
        self.assertEqual(
            (first.peaks[0]["x"], first.peaks[0]["y"]),
            (second.peaks[0]["x"], second.peaks[0]["y"]),
        )
        self.assertEqual(len(list(self.publisher.snapshots_dir.glob("lnd_*.json"))), 2)

    def test_real_two_peak_legacy_collision_reflows_and_fails_closed(self) -> None:
        template = self.profile["memories"][0]
        self.profile = self.make_profile(
            memories=[
                {
                    **template,
                    "memory_id": REAL_PEAK_A_ID,
                    "revision_sha256": "a" * 64,
                    "evidence": [template["evidence"][0]] * 4,
                },
                {
                    **template,
                    "memory_id": REAL_PEAK_B_ID,
                    "revision_sha256": "b" * 64,
                    "evidence": [template["evidence"][0]] * 2,
                },
            ]
        )
        baseline = self.publish_landscape("real-baseline")
        legacy_positions = {
            REAL_PEAK_A_ID: (0.829981314055544, 0.589208888758608),
            REAL_PEAK_B_ID: (0.8263117504413194, 0.45041270492477287),
        }
        legacy = self.install_legacy_landscape_head(baseline, legacy_positions)
        head_before = self.publisher.landscape_head_path.read_bytes()
        snapshots_before = tuple(sorted(self.publisher.snapshots_dir.iterdir()))

        with mock.patch(
            "cognitive_projection_v1._peak_layout_candidates", return_value=()
        ), mock.patch(
            "cognitive_projection_v1._peak_layout_fallback_templates",
            return_value=(),
        ):
            with self.assertRaises(ContractError) as failed:
                self.publish_landscape("real-no-slot")
        self.assertEqual(failed.exception.kind, "evidence")
        self.assertEqual(self.publisher.landscape_head_path.read_bytes(), head_before)
        self.assertEqual(
            tuple(sorted(self.publisher.snapshots_dir.iterdir())), snapshots_before
        )

        migrated = self.publish_landscape("real-migrated")
        stable = self.publish_landscape("real-stable")
        self.assertEqual(migrated.previous_snapshot_sha256, legacy.sha256)
        migrated_a = self.peak_by_id(migrated, REAL_PEAK_A_ID)
        migrated_b = self.peak_by_id(migrated, REAL_PEAK_B_ID)
        self.assertEqual(
            (migrated_a["x"], migrated_a["y"]), legacy_positions[REAL_PEAK_A_ID]
        )
        self.assertEqual((migrated_b["x"], migrated_b["y"]), (0.12, 0.12))
        self.assert_peak_hit_targets_clear(migrated_a, migrated_b)
        self.assertGreaterEqual(
            self.peak_kde_clearance_squared(migrated_a, migrated_b), 1.0
        )
        self.assertEqual(
            [(row["x"], row["y"]) for row in migrated.peaks],
            [(row["x"], row["y"]) for row in stable.peaks],
        )

    def test_direct_relation_allows_kde_nearness_then_withdrawal_reflows(self) -> None:
        template = self.profile["memories"][0]
        first_ref = ObjectRef("understanding", CLOSE_PEAK_A_ID, 1, "a" * 64)
        second_ref = ObjectRef("understanding", CLOSE_PEAK_B_ID, 1, "b" * 64)
        self.profile = self.make_profile(
            memories=[
                {
                    **template,
                    "memory_id": first_ref.id,
                    "revision_sha256": first_ref.revision_sha256,
                },
                {
                    **template,
                    "memory_id": second_ref.id,
                    "revision_sha256": second_ref.revision_sha256,
                },
            ]
        )
        self.bundles.relations = (
            self.make_graph_relation(
                "rel_000000000000000000000001", first_ref, second_ref
            ),
        )

        related = self.publish_landscape("direct-related")
        related_a = self.peak_by_id(related, first_ref.id)
        related_b = self.peak_by_id(related, second_ref.id)
        self.assertEqual(
            (related_a["x"], related_a["y"]),
            (0.49044889182729823, 0.4106887502202263),
        )
        self.assertEqual(
            (related_b["x"], related_b["y"]),
            (0.3011453518829178, 0.3146481498064757),
        )
        self.assert_peak_hit_targets_clear(related_a, related_b)
        self.assertLess(self.peak_kde_clearance_squared(related_a, related_b), 1.0)

        self.bundles.relations = ()
        withdrawn = self.publish_landscape("direct-withdrawn")
        repeated = self.publish_landscape("direct-withdrawn-again")
        withdrawn_a = self.peak_by_id(withdrawn, first_ref.id)
        withdrawn_b = self.peak_by_id(withdrawn, second_ref.id)
        self.assertEqual(
            (withdrawn_a["x"], withdrawn_a["y"]),
            (related_a["x"], related_a["y"]),
        )
        self.assertEqual((withdrawn_b["x"], withdrawn_b["y"]), (0.88, 0.88))
        self.assert_peak_hit_targets_clear(withdrawn_a, withdrawn_b)
        self.assertGreaterEqual(
            self.peak_kde_clearance_squared(withdrawn_a, withdrawn_b), 1.0
        )
        self.assertEqual(
            [(row["x"], row["y"]) for row in withdrawn.peaks],
            [(row["x"], row["y"]) for row in repeated.peaks],
        )

    def test_shared_formal_memory_two_hop_allows_kde_nearness(self) -> None:
        template = self.profile["memories"][0]
        first_ref = ObjectRef("understanding", CLOSE_PEAK_A_ID, 1, "a" * 64)
        second_ref = ObjectRef("understanding", CLOSE_PEAK_B_ID, 1, "b" * 64)
        self.profile = self.make_profile(
            memories=[
                {
                    **template,
                    "memory_id": first_ref.id,
                    "revision_sha256": first_ref.revision_sha256,
                },
                {
                    **template,
                    "memory_id": second_ref.id,
                    "revision_sha256": second_ref.revision_sha256,
                },
            ]
        )
        memory = self.make_memory()
        memory_ref = exact_ref("reusable_memory", memory.memory_id, memory)
        self.bundles.memories = (memory,)
        self.bundles.relations = (
            self.make_graph_relation(
                "rel_000000000000000000000002", memory_ref, first_ref
            ),
            self.make_graph_relation(
                "rel_000000000000000000000003", memory_ref, second_ref
            ),
        )

        landscape = self.publish_landscape("shared-memory")
        first = self.peak_by_id(landscape, first_ref.id)
        second = self.peak_by_id(landscape, second_ref.id)
        self.assertEqual(
            (first["x"], first["y"]),
            (0.49044889182729823, 0.4106887502202263),
        )
        self.assertEqual(
            (second["x"], second["y"]),
            (0.3011453518829178, 0.3146481498064757),
        )
        self.assert_peak_hit_targets_clear(first, second)
        self.assertLess(self.peak_kde_clearance_squared(first, second), 1.0)

    def test_dense_unrelated_layout_uses_deterministic_grid_fallback(self) -> None:
        template = self.profile["memories"][0]
        self.profile = self.make_profile(
            memories=[
                {
                    **template,
                    "memory_id": f"mem_{index:024x}",
                    "revision_sha256": f"{index:064x}",
                }
                for index in range(1, 13)
            ]
        )

        first = self.publish_landscape("dense-grid")
        second = self.publish_landscape("dense-grid-again")
        expected_positions = {
            (x, y)
            for x in (0.12, 0.37333333333333335, 0.6266666666666667, 0.88)
            for y in (0.12, 0.5, 0.88)
        }
        self.assertEqual(
            {(row["x"], row["y"]) for row in first.peaks}, expected_positions
        )
        self.assertEqual(
            [(row["x"], row["y"]) for row in first.peaks],
            [(row["x"], row["y"]) for row in second.peaks],
        )
        for index, left in enumerate(first.peaks):
            for right in first.peaks[index + 1 :]:
                self.assert_peak_hit_targets_clear(left, right)
                self.assertGreaterEqual(
                    self.peak_kde_clearance_squared(left, right), 1.0
                )

    def test_fallback_backtracks_when_greedy_slot_assignment_has_no_solution(
        self,
    ) -> None:
        template = self.profile["memories"][0]
        evidence_counts = (24, 4, 8, 24, 12, 0, 12, 1, 4, 8)
        identifiers = tuple(
            f"mem_000000000002{index:012x}" for index in range(10)
        )
        revision_hashes = tuple(f"{index + 1:064x}" for index in range(10))
        refs = tuple(
            ObjectRef("understanding", identifier, 1, revision_hashes[index])
            for index, identifier in enumerate(identifiers)
        )
        self.profile = self.make_profile(
            memories=[
                {
                    **template,
                    "memory_id": identifier,
                    "revision_sha256": revision_hashes[index],
                    "evidence": [template["evidence"][0]]
                    * evidence_counts[index],
                }
                for index, identifier in enumerate(identifiers)
            ]
        )
        close_indices = frozenset(
            {
                (0, 8),
                (0, 9),
                (1, 2),
                (2, 4),
                (3, 8),
                (4, 5),
                (5, 7),
                (6, 9),
                (7, 9),
            }
        )
        self.bundles.relations = tuple(
            self.make_graph_relation(
                f"rel_000000000002{relation_index:012x}",
                refs[left],
                refs[right],
            )
            for relation_index, (left, right) in enumerate(sorted(close_indices))
        )

        first = self.publish_landscape("backtracking-grid")
        second = self.publish_landscape("backtracking-grid-again")
        self.assertEqual(len(first.peaks), 10)
        self.assertEqual(
            [(row["x"], row["y"]) for row in first.peaks],
            [(row["x"], row["y"]) for row in second.peaks],
        )
        by_id = {
            row["understanding_ref"]["id"]: row for row in first.peaks
        }
        for left_index, left_id in enumerate(identifiers):
            for right_index in range(left_index + 1, len(identifiers)):
                right_id = identifiers[right_index]
                left = by_id[left_id]
                right = by_id[right_id]
                self.assert_peak_hit_targets_clear(left, right)
                if (left_index, right_index) not in close_indices:
                    self.assertGreaterEqual(
                        self.peak_kde_clearance_squared(left, right), 1.0
                    )

    def test_projector_peak_geometry_matches_renderer_contract(self) -> None:
        dashboard = (ROOT / "chrome-newtab" / "dashboard.js").read_text(
            encoding="utf-8"
        )
        styles = (ROOT / "chrome-newtab" / "dashboard.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "return { x: 72 + Number(x) * 956, y: 50 + Number(y) * 390 };",
            dashboard,
        )
        self.assertIn(
            "const COGNITIVE_MAP_BOUNDS = Object.freeze({ x: 0, y: 0, width: 1100, height: 520 });",
            dashboard,
        )
        self.assertIn(
            "const outerX = 74 + peak.elevation * 42 + Math.min(peak.evidence_count, 12) * 2.4;",
            dashboard,
        )
        self.assertIn(
            "const outerY = 48 + peak.elevation * 31 + Math.min(peak.evidence_count, 12) * 1.7;",
            dashboard,
        )
        self.assertIn(
            '<g class="cognitive-map-screen-space" data-cognitive-screen-space>',
            dashboard,
        )
        self.assertIn(
            'rx="${Math.max(66, outerX * .72)}" ry="${Math.max(42, outerY * .7)}"',
            dashboard,
        )
        self.assertIn('class="cognitive-peak-summit"', dashboard)
        self.assertIn(
            ".cognitive-peak-hit {\n  stroke-width: 30;",
            styles,
        )

    def test_deleted_understanding_does_not_revive(self) -> None:
        self.install_formal_graph()
        first = self.publish_landscape("before-delete")
        self.profile = self.make_profile(memories=[])
        second = self.publish_landscape("after-delete")
        third = self.publish_landscape("after-delete-again")
        self.assertEqual(len(first.peaks), 1)
        self.assertEqual(second.peaks, ())
        self.assertEqual(second.edges, ())
        self.assertEqual(third.peaks, ())
        self.assertEqual(third.previous_snapshot_sha256, second.sha256)

    def test_home_uses_latest_user_edits_and_is_idempotent(self) -> None:
        first_receipt, _ = self.make_receipt()
        edited_receipt, edited_receipt_ref = self.make_receipt(
            revision=2,
            previous=first_receipt,
            operation="user_edit",
            summary="用户改写后的整理结果。",
        )
        self.actions.receipts[edited_receipt.receipt_id] = (
            edited_receipt,
            edited_receipt_ref,
        )
        first_memory = self.make_memory()
        edited_memory = self.make_memory(
            revision=2,
            previous=first_memory,
            operation="user_edit",
            action_id=ACTION_ID,
        )
        first_relation = self.make_relation(first_memory)
        edited_relation = self.make_relation(
            edited_memory,
            revision=2,
            previous=first_relation,
            operation="user_edit",
            action_id=ACTION_ID,
        )
        self.bundles.memories = (edited_memory,)
        self.bundles.relations = (edited_relation,)
        landscape = self.publish_landscape()
        first, path = self.publisher.publish_home(
            local_date=DATE,
            landscape=landscape,
            schedule=self.schedule,
            now=NOW,
            profile=self.profile,
        )
        before = path.read_bytes()
        second, _ = self.publisher.publish_home(
            local_date=DATE,
            landscape=landscape,
            schedule=self.schedule,
            now=LATER,
            profile=self.profile,
        )
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(first.generated_at, second.generated_at)
        self.assertEqual(first.records[0]["summary"], "用户改写后的整理结果。")
        self.assertEqual(first.records[0]["memory_refs"][0]["revision"], 2)
        self.assertNotIn(QUOTE, path.read_text(encoding="utf-8"))

    def test_merged_record_derived_only_from_exact_manifest_refs(self) -> None:
        receipt, receipt_ref = self.make_receipt()
        self.actions.receipts[receipt.receipt_id] = (receipt, receipt_ref)
        self.install_formal_graph()
        self.bundles.bundle_ref = ObjectRef(
            "daily_bundle", "db_20260818", 1, "a" * 64
        )
        self.bundles.manifest = {
            "source_refs": [self.source_ref.to_dict()],
            "receipt_refs": [receipt_ref.to_dict()],
            "warnings": ["long_term_failed"],
        }
        landscape = self.publish_landscape()
        home, _ = self.publisher.publish_home(
            local_date=DATE,
            landscape=landscape,
            schedule=self.schedule,
            warnings=("long_term_failed",),
            now=NOW,
            profile=self.profile,
        )
        self.assertEqual(home.records[0]["status"], "merged")
        self.assertEqual(home.today_status["merged"], 1)
        self.assertEqual(
            home.today_status["daily_run_status"], "committed_with_warnings"
        )

    def test_runtime_status_allowlist_and_receipt_conflict_fail_closed(self) -> None:
        landscape = self.publish_landscape()
        home, _ = self.publisher.publish_home(
            local_date=DATE,
            landscape=landscape,
            schedule=self.schedule,
            record_runtime_statuses={
                RECORD_ID: {"status": "failed", "error_kind": "provider_error"}
            },
            now=NOW,
            profile=self.profile,
        )
        self.assertEqual(home.records[0]["status"], "failed")
        self.assertIsNone(home.records[0]["receipt_ref"])
        no_candidate, _ = self.publisher.publish_home(
            local_date=DATE,
            landscape=landscape,
            schedule={**self.schedule, "last_run_status": "no_candidate"},
            record_runtime_statuses={
                RECORD_ID: {"status": "no_candidate", "error_kind": None}
            },
            now=NOW,
            profile=self.profile,
        )
        self.assertEqual(no_candidate.records[0]["status"], "no_candidate")
        self.assertIsNone(no_candidate.records[0]["receipt_ref"])
        self.assertIsNone(no_candidate.records[0]["summary"])
        self.assertEqual(no_candidate.records[0]["memory_refs"], [])
        self.assertEqual(no_candidate.records[0]["understanding_refs"], [])
        self.assertEqual(no_candidate.today_status["interpreted"], 1)
        self.assertEqual(
            no_candidate.today_status["daily_run_status"], "no_candidate"
        )
        with self.assertRaises(ContractError):
            self.publisher.build_home(
                local_date=DATE,
                landscape=landscape,
                schedule=self.schedule,
                record_runtime_statuses={
                    RECORD_ID: {"status": "failed", "error_kind": "secret_error"}
                },
                now=NOW,
                profile=self.profile,
            )
        with self.assertRaises(ContractError):
            self.publisher.build_home(
                local_date=DATE,
                landscape=landscape,
                schedule=self.schedule,
                record_runtime_statuses={
                    RECORD_ID: {
                        "status": "no_candidate",
                        "error_kind": "invalid_response",
                    }
                },
                now=NOW,
                profile=self.profile,
            )
        receipt, receipt_ref = self.make_receipt()
        self.actions.receipts[receipt.receipt_id] = (receipt, receipt_ref)
        with self.assertRaises(ContractError) as raised:
            self.publisher.build_home(
                local_date=DATE,
                landscape=landscape,
                schedule=self.schedule,
                record_runtime_statuses={
                    RECORD_ID: {"status": "processing", "error_kind": None}
                },
                now=NOW,
                profile=self.profile,
            )
        self.assertEqual(raised.exception.kind, "stale")

    def test_no_receipts_keeps_old_landscape_and_projects_late_failed_record(
        self,
    ) -> None:
        late_id = "rec_777777777777777777777777"
        late_source, late_ref = self.make_source(
            late_id,
            captured_at="2026-08-18T22:15:00+08:00",
        )
        self.records.records.append((late_source, late_ref))
        receipt, receipt_ref = self.make_receipt()
        self.actions.receipts[receipt.receipt_id] = (receipt, receipt_ref)
        self.install_formal_graph()
        self.bundles.bundle_ref = ObjectRef(
            "daily_bundle", "db_20260818", 1, "a" * 64
        )
        self.bundles.manifest = {
            "source_refs": [self.source_ref.to_dict()],
            "receipt_refs": [receipt_ref.to_dict()],
            "warnings": [],
        }
        landscape = self.publish_landscape("old-bundle-late-record")

        home, _ = self.publisher.publish_home(
            local_date=DATE,
            landscape=landscape,
            schedule={**self.schedule, "last_run_status": "no_receipts"},
            record_runtime_statuses={
                late_id: {"status": "failed", "error_kind": "invalid_response"}
            },
            now=NOW,
            profile=self.profile,
        )

        self.assertEqual(home.today_status["daily_run_status"], "no_receipts")
        self.assertEqual(
            [row["status"] for row in home.records], ["merged", "failed"]
        )
        self.assertEqual(home.landscape_ref["snapshot_id"], landscape.snapshot_id)

    def test_ready_needs_review_and_processing_are_derived_per_record(self) -> None:
        third_record_id = "rec_777777777777777777777777"
        second_source, second_ref = self.make_source(
            SECOND_RECORD_ID, captured_at="2026-08-18T11:00:00+08:00"
        )
        third_source, third_ref = self.make_source(
            third_record_id, captured_at="2026-08-18T12:00:00+08:00"
        )
        self.records.records.extend(
            [(second_source, second_ref), (third_source, third_ref)]
        )
        ready, ready_ref = self.make_receipt()
        review, review_ref = self.make_receipt(
            source_ref=second_ref,
            status="needs_review",
            summary="这条整理需要用户校准。",
        )
        self.actions.receipts[ready.receipt_id] = (ready, ready_ref)
        self.actions.receipts[review.receipt_id] = (review, review_ref)
        landscape = self.publish_landscape()
        home, _ = self.publisher.publish_home(
            local_date=DATE,
            landscape=landscape,
            schedule=self.schedule,
            record_runtime_statuses={
                third_record_id: {"status": "processing", "error_kind": None}
            },
            now=NOW,
            profile=self.profile,
        )
        self.assertEqual(
            [row["status"] for row in home.records],
            ["ready", "needs_review", "processing"],
        )
        self.assertEqual(home.today_status["saved"], 3)
        self.assertEqual(home.today_status["interpreted"], 2)
        self.assertEqual(home.today_status["needs_review"], 1)

    def test_tombstoned_reusable_memory_does_not_reappear(self) -> None:
        active = self.make_memory()
        self.bundles.memories = (active,)
        first = self.publish_landscape("memory-active")
        tombstone = ReusableMemoryRevision.from_dict(
            {
                **active.to_dict(),
                "revision": 2,
                "status": "tombstone",
                "operation": "tombstone",
                "created_at": LATER.isoformat(timespec="seconds"),
                "provenance": {
                    **dict(active.provenance),
                    "origin": "user",
                    "user_action_id": ACTION_ID,
                },
                "previous_revision_sha256": active.sha256,
            }
        )
        self.bundles.memories = (tombstone,)
        self.bundles.relations = ()
        second = self.publish_landscape("memory-deleted")
        third = self.publish_landscape("memory-still-deleted")
        self.assertEqual(len(first.nodes), 1)
        self.assertEqual(second.nodes, ())
        self.assertEqual(third.nodes, ())

    def test_source_edit_updates_home_ref_and_excludes_stale_derivatives(self) -> None:
        receipt, receipt_ref = self.make_receipt()
        self.actions.receipts[receipt.receipt_id] = (receipt, receipt_ref)
        self.install_formal_graph()
        first = self.publish_landscape("source-r1")
        edited_source, edited_ref = self.make_source(
            RECORD_ID, revision=2, previous_sha=self.source.sha256
        )
        self.records.records = [(edited_source, edited_ref)]
        second = self.publish_landscape("source-r2")
        home, _ = self.publisher.publish_home(
            local_date=DATE,
            landscape=second,
            schedule=self.schedule,
            now=LATER,
            profile=self.profile,
        )
        self.assertEqual(len(first.nodes), 1)
        self.assertEqual(second.nodes, ())
        self.assertEqual(second.edges, ())
        self.assertEqual(home.records[0]["record_ref"]["revision"], 2)
        self.assertEqual(home.records[0]["status"], "raw_saved")
        self.assertIsNone(home.records[0]["receipt_ref"])

    def test_original_only_terminal_receipt_is_projected_without_ai_text(self) -> None:
        ready, _ = self.make_receipt()
        original, original_ref = self.make_receipt(
            revision=2,
            previous=ready,
            status="original_only",
            operation="original_only",
        )
        self.actions.receipts[original.receipt_id] = (original, original_ref)
        self.install_formal_graph()
        edited_source, edited_ref = self.make_source(
            RECORD_ID,
            revision=2,
            previous_sha=self.source.sha256,
        )
        self.records.records = [(edited_source, edited_ref)]
        landscape = self.publish_landscape()
        home, _ = self.publisher.publish_home(
            local_date=DATE,
            landscape=landscape,
            schedule=self.schedule,
            now=NOW,
            profile=self.profile,
        )
        row = home.records[0]
        self.assertEqual(row["record_ref"]["revision"], 2)
        self.assertEqual(row["status"], "original_only")
        self.assertEqual(row["receipt_ref"], original_ref.to_dict())
        self.assertIsNone(row["summary"])
        self.assertEqual(row["content_types"], [])
        self.assertEqual(row["topics"], [])
        self.assertEqual(row["purposes"], [])
        self.assertEqual(row["memory_refs"], [])
        self.assertEqual(row["understanding_refs"], [])
        self.assertEqual(landscape.nodes, ())
        self.assertEqual(landscape.edges, ())

    def test_symlink_hardlink_and_non_private_paths_fail_closed(self) -> None:
        landscape = self.publish_landscape()
        _, home_path = self.publisher.publish_home(
            local_date=DATE,
            landscape=landscape,
            schedule=self.schedule,
            now=NOW,
            profile=self.profile,
        )
        original = home_path.read_bytes()
        home_path.unlink()
        target = self.vault / "target.json"
        target.write_bytes(original)
        target.chmod(0o600)
        os.symlink(target, home_path)
        with self.assertRaises(ContractError) as symlink_error:
            self.publisher.publish_home(
                local_date=DATE,
                landscape=landscape,
                schedule=self.schedule,
                now=LATER,
                profile=self.profile,
            )
        self.assertEqual(symlink_error.exception.kind, "evidence")
        home_path.unlink()
        os.link(target, home_path)
        with self.assertRaises(ContractError) as hardlink_error:
            self.publisher.publish_home(
                local_date=DATE,
                landscape=landscape,
                schedule=self.schedule,
                now=LATER,
                profile=self.profile,
            )
        self.assertEqual(hardlink_error.exception.kind, "evidence")
        home_path.unlink()
        self.publisher.projections_dir.chmod(0o755)
        with self.assertRaises(ContractError) as mode_error:
            self.publisher.publish_home(
                local_date=DATE,
                landscape=landscape,
                schedule=self.schedule,
                now=LATER,
                profile=self.profile,
            )
        self.assertEqual(mode_error.exception.kind, "evidence")


if __name__ == "__main__":
    unittest.main()
