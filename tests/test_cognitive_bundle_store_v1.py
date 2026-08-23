from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_AGENT = ROOT / "context-agent"
if str(CONTEXT_AGENT) not in sys.path:
    sys.path.insert(0, str(CONTEXT_AGENT))

from core import ContractError, canonical_json, sha256_bytes  # noqa: E402
from agent_v1 import (  # noqa: E402
    _profile_lock as agent_profile_lock,
    build_agent_profile,
)
from cognitive_actions_v1 import CognitiveActionStore  # noqa: E402
from cognitive_bundle_store_v1 import BundleCommitResult, CognitiveBundleStore  # noqa: E402
from cognitive_store_v1 import RecordStore  # noqa: E402
from cognitive_v1 import (  # noqa: E402
    COGNITIVE_SCHEMA_VERSION,
    CognitiveUserAction,
    DailySummaryRevision,
    InterpretationReceiptRevision,
    ObjectRef,
    RelationRevision,
    ReusableMemoryRevision,
    SourceRecordRevision,
    SourceSpan,
    make_daily_summary_id,
    make_cognitive_action_id,
    make_receipt_id,
    make_relation_id,
    make_reusable_memory_id,
    persisted_json_bytes,
)


NOW = dt.datetime(2026, 8, 18, 21, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
LOCAL_DATE = "2026-08-18"
DAY_FILE = f"{LOCAL_DATE}.md"
QUOTE = "先暴露可验证的部分，再补齐完整方案。"
DAY_BYTES = f"## 10:00 · 周二 · Chrome\n\n{QUOTE}\n\n---\n".encode("utf-8")
SOURCE_ID = "rec_111111111111111111111111"
REQUEST_ID = "dreq_111111111111111111111111"
RUN_ID = "drun_111111111111111111111111"
ACTION_ID = "cact_111111111111111111111111"
UNDERSTANDING_ID = "mem_111111111111111111111111"
CANDIDATE_MEMORY_ID = "cmem_111111111111111111111111"
CANDIDATE_RELATION_ID = "crel_111111111111111111111111"


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: dict) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = persisted_json_bytes(value)
    path.write_bytes(payload)
    path.chmod(0o600)
    return digest_bytes(payload)


class BundleStoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="memento-bundle-store-")
        self.vault = Path(self.temporary.name) / "vault"
        self.vault.mkdir(mode=0o700)
        (self.vault / DAY_FILE).write_bytes(DAY_BYTES)
        (self.vault / DAY_FILE).chmod(0o600)
        self.store = CognitiveBundleStore(self.vault)
        self.store._ensure_layout()
        self.source, self.source_ref, self.span = self.make_source()
        self.receipt, self.receipt_ref = self.make_receipt()
        self.understanding_ref = self.make_understanding()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_source(self) -> tuple[SourceRecordRevision, ObjectRef, SourceSpan]:
        record_store = RecordStore(self.vault, state_root=self.store.root)
        result = record_store.reconcile_day(
            DAY_FILE,
            preallocated_record_id=SOURCE_ID,
            now=NOW,
            timezone=NOW.tzinfo,
        )
        self.assertEqual(result.created_record_ids, (SOURCE_ID,))
        path = self.store.records_dir / f"{SOURCE_ID}.r000001.json"
        source = SourceRecordRevision.from_dict(json.loads(path.read_text(encoding="utf-8")))
        source_ref = ObjectRef("source_record", SOURCE_ID, 1, digest_bytes(path.read_bytes()))
        span = SourceSpan(
            record_id=SOURCE_ID,
            record_revision=1,
            record_revision_sha256=source_ref.revision_sha256,
            source_file=DAY_FILE,
            line_start=3,
            line_end=3,
            quote=QUOTE,
            quote_sha256=sha256_bytes(QUOTE.encode("utf-8")),
        )
        return source, source_ref, span

    def make_receipt(
        self,
        *,
        revision: int = 1,
        previous: InterpretationReceiptRevision | None = None,
        operation: str = "interpret",
    ) -> tuple[InterpretationReceiptRevision, ObjectRef]:
        receipt_id = make_receipt_id(SOURCE_ID)
        receipt = InterpretationReceiptRevision(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_interpretation_receipt_revision",
            receipt_id=receipt_id,
            revision=revision,
            status="ready",
            operation=operation,
            created_at=NOW.isoformat(timespec="seconds"),
            request_id="ireq_111111111111111111111111",
            run_id="irun_111111111111111111111111",
            record_ref=self.source_ref,
            user_action_id=ACTION_ID if operation == "user_edit" else None,
            summary="这条记录强调先验证再完善。",
            facets={
                "content_types": ["observation"],
                "topics": ["产品设计"],
                "objects": ["方案评审"],
                "stance": "self_observation",
                "cognitive_state": "first_seen",
                "purposes": ["future_decision"],
            },
            memory_candidates=(),
            relation_candidates=(),
            source_spans=(self.span,),
            contract_version="1.0",
            feedback_watermark_sha256="2" * 64,
            previous_revision_sha256=None if previous is None else previous.sha256,
        )
        path = self.store.receipt_dir / f"{receipt_id}.r{revision:06d}.json"
        revision_sha = write_json(path, receipt.to_dict())
        return receipt, ObjectRef("interpretation_receipt", receipt_id, revision, revision_sha)

    def make_understanding(self) -> ObjectRef:
        value = {
            "memory_id": UNDERSTANDING_ID,
            "revision": 1,
            "status": "active",
            "statement": "你在方案评审中优先寻找可验证部分。",
        }
        path = self.vault / ".context-agent" / "agent-v1" / "memories" / f"{UNDERSTANDING_ID}.r000001.json"
        revision_sha = write_json(path, value)
        return ObjectRef("understanding", UNDERSTANDING_ID, 1, revision_sha)

    def summary(
        self,
        *,
        revision: int = 1,
        previous: DailySummaryRevision | None = None,
        source_ref: ObjectRef | None = None,
        receipt_ref: ObjectRef | None = None,
    ) -> DailySummaryRevision:
        return DailySummaryRevision(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_daily_summary_revision",
            summary_id=make_daily_summary_id(LOCAL_DATE),
            revision=revision,
            status="active",
            operation="generate" if revision == 1 else "regenerate",
            created_at=NOW.isoformat(timespec="seconds"),
            local_date=LOCAL_DATE,
            overview="今天重复关注了方案的最早验证方式。",
            themes=("早期验证",),
            changes=(),
            unresolved_questions=("什么时候已经足够验证？",),
            action_clues=("下次评审先发可验证部分。",),
            source_refs=(source_ref or self.source_ref,),
            receipt_refs=(receipt_ref or self.receipt_ref,),
            review_file=f"Reviews/Daily/{LOCAL_DATE}.md",
            review_sha256=None,
            user_supplement_sha256=None,
            previous_revision_sha256=None if previous is None else previous.sha256,
        )

    def memory(
        self,
        key: str = "early-validation",
        *,
        revision: int = 1,
        previous: ReusableMemoryRevision | None = None,
        operation: str | None = None,
        status: str = "active",
        run_id: str = RUN_ID,
        bundle_revision: int = 1,
        action_id: str | None = None,
    ) -> ReusableMemoryRevision:
        return ReusableMemoryRevision(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_reusable_memory_revision",
            memory_id=make_reusable_memory_id(key),
            revision=revision,
            status=status,
            operation=operation or ("new" if revision == 1 else "revise"),
            created_at=NOW.isoformat(timespec="seconds"),
            statement="评审前先定义最早可验证部分。",
            memory_kind="decision",
            topics=("产品设计",),
            purposes=("future_decision",),
            uncertainty="low",
            source_spans=(self.span,),
            origin_receipt_refs=(self.receipt_ref,),
            provenance={
                "origin": "user" if action_id else "daily_integrator",
                "run_id": run_id,
                "bundle_id": "db_20260818",
                "bundle_revision": bundle_revision,
                "user_action_id": action_id,
            },
            previous_revision_sha256=None if previous is None else previous.sha256,
        )

    def relation(
        self,
        memory: ReusableMemoryRevision,
        key: str = "supports-understanding",
        *,
        revision: int = 1,
        previous: RelationRevision | None = None,
        run_id: str = RUN_ID,
        bundle_revision: int = 1,
    ) -> RelationRevision:
        return RelationRevision(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_relation_revision",
            relation_id=make_relation_id(key),
            revision=revision,
            status="active",
            operation="new" if revision == 1 else "revise",
            created_at=NOW.isoformat(timespec="seconds"),
            type="supports",
            from_ref=ObjectRef("reusable_memory", memory.memory_id, memory.revision, memory.sha256),
            to_ref=self.understanding_ref,
            direction="directed",
            statement="这条记录为方案评审偏好提供一次支持。",
            uncertainty="low",
            source_spans=(self.span,),
            valid_from=LOCAL_DATE,
            provenance={
                "origin": "daily_integrator",
                "run_id": run_id,
                "bundle_id": "db_20260818",
                "bundle_revision": bundle_revision,
                "user_action_id": None,
            },
            previous_revision_sha256=None if previous is None else previous.sha256,
        )

    def input_hashes(
        self,
        nonce: str = "one",
        *,
        source_refs: tuple[ObjectRef, ...] | None = None,
        receipt_refs: tuple[ObjectRef, ...] | None = None,
    ) -> dict[str, str]:
        sources = sorted(
            source_refs or (self.source_ref,),
            key=lambda ref: (ref.id, ref.revision, ref.revision_sha256),
        )
        receipts = sorted(
            receipt_refs or (self.receipt_ref,),
            key=lambda ref: (ref.id, ref.revision, ref.revision_sha256),
        )
        return {
            "source_manifest_sha256": sha256_bytes(
                canonical_json([ref.to_dict() for ref in sources]).encode("utf-8")
            ),
            "receipt_manifest_sha256": sha256_bytes(
                canonical_json([ref.to_dict() for ref in receipts]).encode("utf-8")
            ),
            "profile_sha256": build_agent_profile(self.vault)["profile_sha256"],
            "user_action_watermark_sha256": CognitiveActionStore(
                self.vault,
                state_root=self.store.root,
            ).action_watermark()[1],
            "policy_sha256": sha256_bytes(f"{nonce}:policy".encode("utf-8")),
        }

    def stage_candidates(self, run_id: str = RUN_ID) -> None:
        self.store.stage_candidates(
            run_id=run_id,
            local_date=LOCAL_DATE,
            memory_candidates=(
                {"candidate_id": CANDIDATE_MEMORY_ID, "statement": "候选记忆"},
            ),
            relation_candidates=(
                {"candidate_id": CANDIDATE_RELATION_ID, "statement": "候选关系"},
            ),
            now=NOW,
        )

    def write_review(self, payload: bytes = b"# Daily Review\n\nBound output.\n") -> tuple[str, str]:
        review_file = f"Reviews/Daily/{LOCAL_DATE}.md"
        path = self.vault / review_file
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(payload)
        path.chmod(0o600)
        return review_file, digest_bytes(payload)

    def commit(
        self,
        *,
        summary: DailySummaryRevision,
        memories: tuple[ReusableMemoryRevision, ...],
        relations: tuple[RelationRevision, ...],
        run_id: str = RUN_ID,
        hashes_nonce: str = "one",
        expected: ObjectRef | None = None,
        materials: tuple[dict, ...] = (),
        source_refs: tuple[ObjectRef, ...] | None = None,
        receipt_refs: tuple[ObjectRef, ...] | None = None,
    ):
        selected_sources = source_refs or (self.source_ref,)
        selected_receipts = receipt_refs or (self.receipt_ref,)
        return self.store.commit_day_bundle(
            request_id=REQUEST_ID,
            run_id=run_id,
            input_hashes=self.input_hashes(
                hashes_nonce,
                source_refs=selected_sources,
                receipt_refs=selected_receipts,
            ),
            source_refs=selected_sources,
            receipt_refs=selected_receipts,
            summary=summary,
            memories=memories,
            relations=relations,
            candidate_materializations=materials,
            expected_bundle_ref=expected,
            now=NOW,
        )


class CommitTests(BundleStoreCase):
    def test_public_commit_defaults_cannot_disable_action_or_profile_cas(self) -> None:
        store = CognitiveBundleStore(self.vault, state_root=self.store.root)
        # ``None`` is a reset to the authoritative local readers, not a
        # production bypass retained for model-free callers.
        store.set_action_watermark_reader(None)
        store.set_profile_sha256_reader(None)

        stale_action_hashes = self.input_hashes()
        stale_action_hashes["user_action_watermark_sha256"] = "f" * 64
        with self.assertRaises(ContractError) as action_raised:
            store.commit_day_bundle(
                request_id=REQUEST_ID,
                run_id=RUN_ID,
                input_hashes=stale_action_hashes,
                source_refs=(self.source_ref,),
                receipt_refs=(self.receipt_ref,),
                summary=self.summary(),
                memories=(),
                relations=(),
                now=NOW,
            )
        self.assertEqual(action_raised.exception.kind, "stale")
        self.assertFalse(store.catalog_path.exists())

        stale_profile_hashes = self.input_hashes()
        current_profile_sha = build_agent_profile(self.vault)["profile_sha256"]
        stale_profile_hashes["profile_sha256"] = (
            "e" * 64 if current_profile_sha != "e" * 64 else "d" * 64
        )
        with self.assertRaises(ContractError) as profile_raised:
            store.commit_day_bundle(
                request_id=REQUEST_ID,
                run_id=RUN_ID,
                input_hashes=stale_profile_hashes,
                source_refs=(self.source_ref,),
                receipt_refs=(self.receipt_ref,),
                summary=self.summary(),
                memories=(),
                relations=(),
                now=NOW,
            )
        self.assertEqual(profile_raised.exception.kind, "stale")
        self.assertFalse(store.catalog_path.exists())
        self.assertFalse(any(store.committed_dir.iterdir()))
        self.assertFalse(any(store.summary_dir.iterdir()))

    def test_daily_summary_reader_returns_none_before_first_summary(self) -> None:
        summary_id = make_daily_summary_id(LOCAL_DATE)
        self.assertIsNone(self.store.load_daily_summary_head(LOCAL_DATE))
        self.assertIsNone(self.store.load_daily_summary_head(summary_id=summary_id))

    def test_daily_summary_reader_returns_exact_current_head_and_ref(self) -> None:
        summary1 = self.summary()
        first = self.commit(summary=summary1, memories=(), relations=())
        summary2 = self.summary(revision=2, previous=summary1)
        second = self.commit(
            summary=summary2,
            memories=(),
            relations=(),
            run_id="drun_222222222222222222222222",
            hashes_nonce="two",
            expected=first.bundle_ref,
        )

        expected = (summary2, second.summary_ref)
        self.assertEqual(self.store.load_daily_summary_head(LOCAL_DATE), expected)
        self.assertEqual(
            self.store.load_daily_summary_head(summary_id=summary2.summary_id),
            expected,
        )

    def test_daily_summary_reader_fails_closed_on_catalog_or_chain_damage(self) -> None:
        summary1 = self.summary()
        first = self.commit(summary=summary1, memories=(), relations=())
        summary2 = self.summary(revision=2, previous=summary1)
        self.commit(
            summary=summary2,
            memories=(),
            relations=(),
            run_id="drun_222222222222222222222222",
            hashes_nonce="two",
            expected=first.bundle_ref,
        )

        catalog_bytes = self.store.catalog_path.read_bytes()
        catalog = json.loads(catalog_bytes.decode("utf-8"))
        catalog["daily_summaries"][0]["revision_sha256"] = "f" * 64
        self.store.catalog_path.write_bytes(persisted_json_bytes(catalog))
        with self.assertRaises(ContractError):
            self.store.load_daily_summary_head(LOCAL_DATE)

        self.store.catalog_path.write_bytes(catalog_bytes)
        first_revision_path = self.store.summary_dir / f"{summary1.summary_id}.r000001.json"
        first_revision_bytes = first_revision_path.read_bytes()
        damaged = json.loads(first_revision_bytes.decode("utf-8"))
        damaged["overview"] = "被篡改的日级总结。"
        first_revision_path.write_bytes(persisted_json_bytes(damaged))
        with self.assertRaises(ContractError):
            self.store.load_daily_summary_head(summary_id=summary1.summary_id)

    def test_atomic_success_multiple_objects_and_candidate_isolation(self) -> None:
        self.stage_candidates()
        summary = self.summary()
        first = self.memory()
        second = self.memory("system-boundaries")
        first_relation = self.relation(first)
        second_relation = self.relation(second, "second-support")
        materials = (
            {
                "candidate_kind": "memory",
                "candidate_id": CANDIDATE_MEMORY_ID,
                "formal_ref": ObjectRef("reusable_memory", first.memory_id, 1, first.sha256).to_dict(),
            },
            {
                "candidate_kind": "relation",
                "candidate_id": CANDIDATE_RELATION_ID,
                "formal_ref": ObjectRef("relation", first_relation.relation_id, 1, first_relation.sha256).to_dict(),
            },
        )
        result = self.commit(
            summary=summary,
            memories=(first, second),
            relations=(first_relation, second_relation),
            materials=materials,
        )

        self.assertTrue(result.committed)
        catalog = self.store.load_catalog()
        self.assertEqual(catalog["revision"], 1)
        self.assertEqual(len(catalog["reusable_memories"]), 2)
        self.assertEqual(len(catalog["relations"]), 2)
        self.assertEqual(self.store.load_memory_head(first.memory_id), first)
        self.assertEqual(self.store.load_relation_head(first_relation.relation_id), first_relation)
        candidate_path = self.store.candidate_staging_dir / f"{RUN_ID}.json"
        self.assertTrue(candidate_path.is_file())
        self.assertIn(CANDIDATE_MEMORY_ID, candidate_path.read_text(encoding="utf-8"))
        formal_bytes = (self.store.memory_dir / f"{first.memory_id}.r000001.json").read_bytes()
        self.assertNotIn(CANDIDATE_MEMORY_ID.encode("utf-8"), formal_bytes)
        self.assertFalse(any(path.name.startswith("cmem_") for path in self.store.memory_dir.iterdir()))
        self.assertFalse(any(path.name.startswith("crel_") for path in self.store.relation_dir.iterdir()))

    def test_repeat_same_material_is_noop_without_new_revision(self) -> None:
        summary = self.summary()
        memory = self.memory()
        relation = self.relation(memory)
        first = self.commit(summary=summary, memories=(memory,), relations=(relation,))
        second_summary = self.summary(revision=2, previous=summary)
        second = self.commit(summary=second_summary, memories=(), relations=())
        self.assertEqual(second.status, "no_change")
        self.assertEqual(second.bundle_ref, first.bundle_ref)
        self.assertEqual(list(self.store.committed_dir.glob("day_*.r*")), [self.store._bundle_directory("db_20260818", 1)])
        self.assertFalse((self.store.summary_dir / "dsum_20260818.r000002.json").exists())

    def test_persisted_byte_hashes_bind_every_visible_ref(self) -> None:
        summary = self.summary()
        memory = self.memory()
        relation = self.relation(memory)
        result = self.commit(summary=summary, memories=(memory,), relations=(relation,))
        targets = (
            (result.summary_ref, self.store.summary_dir / "dsum_20260818.r000001.json"),
            (result.memory_refs[0], self.store.memory_dir / f"{memory.memory_id}.r000001.json"),
            (result.relation_refs[0], self.store.relation_dir / f"{relation.relation_id}.r000001.json"),
            (result.bundle_ref, self.store._bundle_directory("db_20260818", 1) / "manifest.json"),
        )
        for ref, path in targets:
            self.assertEqual(ref.revision_sha256, digest_bytes(path.read_bytes()))
        self.assertEqual((self.store.memory_dir / f"{memory.memory_id}.r000001.json").read_bytes(), persisted_json_bytes(memory))

    def test_bundle_cas_and_second_revision(self) -> None:
        summary1 = self.summary()
        first = self.commit(summary=summary1, memories=(), relations=())
        summary2 = self.summary(revision=2, previous=summary1)
        with self.assertRaises(ContractError) as raised:
            self.commit(
                summary=summary2,
                memories=(),
                relations=(),
                run_id="drun_222222222222222222222222",
                hashes_nonce="two",
                expected=None,
            )
        self.assertEqual(raised.exception.kind, "stale")
        second = self.commit(
            summary=summary2,
            memories=(),
            relations=(),
            run_id="drun_222222222222222222222222",
            hashes_nonce="two",
            expected=first.bundle_ref,
        )
        self.assertEqual(second.bundle_ref.revision, 2)
        self.assertEqual(self.store.load_day_bundle_ref(LOCAL_DATE), second.bundle_ref)

    def test_two_concurrent_same_day_commits_serialize_to_one_revision(self) -> None:
        summary = self.summary()
        stores = (
            CognitiveBundleStore(self.vault, state_root=self.store.root),
            CognitiveBundleStore(self.vault, state_root=self.store.root),
        )

        def run(store: CognitiveBundleStore):
            return store.commit_day_bundle(
                request_id=REQUEST_ID,
                run_id=RUN_ID,
                input_hashes=self.input_hashes(),
                source_refs=(self.source_ref,),
                receipt_refs=(self.receipt_ref,),
                summary=summary,
                memories=(),
                relations=(),
                expected_bundle_ref=None,
                now=NOW,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, stores))
        self.assertEqual(sorted(row.status for row in results), ["committed", "no_change"])
        self.assertEqual({row.bundle_ref for row in results}, {results[0].bundle_ref})
        self.assertEqual(len(list(self.store.committed_dir.glob("day_*.r*"))), 1)

    def test_append_long_term_result_reuses_summary_and_retry_is_noop(self) -> None:
        summary = self.summary()
        first = self.commit(summary=summary, memories=(), relations=())
        agent_result = {
            "request_id": "req_111111111111111111111111",
            "run_id": "run_111111111111111111111111",
            "response_sha256": "8" * 64,
            "status": "no_change",
            "memory_ref": None,
        }

        def append():
            return self.store.commit_day_bundle(
                request_id=REQUEST_ID,
                run_id="drun_222222222222222222222222",
                input_hashes=self.input_hashes(),
                source_refs=(self.source_ref,),
                receipt_refs=(self.receipt_ref,),
                summary=summary,
                memories=(),
                relations=(),
                long_term_result_ref=agent_result,
                expected_bundle_ref=first.bundle_ref,
                operation="append_long_term_result",
                now=NOW,
            )

        second = append()
        retry = append()
        self.assertEqual(second.bundle_ref.revision, 2)
        self.assertEqual(second.summary_ref, first.summary_ref)
        self.assertEqual(retry.status, "no_change")
        self.assertEqual(retry.bundle_ref, second.bundle_ref)

    def test_append_long_term_result_inherits_nonempty_formal_objects(self) -> None:
        self.stage_candidates()
        summary = self.summary()
        memory = self.memory()
        relation = self.relation(memory)
        materials = (
            {
                "candidate_kind": "memory",
                "candidate_id": CANDIDATE_MEMORY_ID,
                "formal_ref": ObjectRef(
                    "reusable_memory", memory.memory_id, 1, memory.sha256
                ).to_dict(),
            },
            {
                "candidate_kind": "relation",
                "candidate_id": CANDIDATE_RELATION_ID,
                "formal_ref": ObjectRef(
                    "relation", relation.relation_id, 1, relation.sha256
                ).to_dict(),
            },
        )
        first = self.commit(
            summary=summary,
            memories=(memory,),
            relations=(relation,),
            materials=materials,
        )
        memory_bytes = (
            self.store.memory_dir / f"{memory.memory_id}.r000001.json"
        ).read_bytes()
        relation_bytes = (
            self.store.relation_dir / f"{relation.relation_id}.r000001.json"
        ).read_bytes()
        agent_result = {
            "request_id": "req_111111111111111111111111",
            "run_id": "run_111111111111111111111111",
            "response_sha256": "8" * 64,
            "status": "no_change",
            "memory_ref": None,
        }

        def append():
            return self.store.commit_day_bundle(
                request_id=REQUEST_ID,
                run_id="drun_222222222222222222222222",
                input_hashes=self.input_hashes(),
                source_refs=(self.source_ref,),
                receipt_refs=(self.receipt_ref,),
                summary=summary,
                memories=(),
                relations=(),
                long_term_result_ref=agent_result,
                expected_bundle_ref=first.bundle_ref,
                operation="append_long_term_result",
                now=NOW,
            )

        second = append()
        retry = append()
        self.assertEqual(second.bundle_ref.revision, 2)
        self.assertEqual(second.memory_refs, first.memory_refs)
        self.assertEqual(second.relation_refs, first.relation_refs)
        self.assertEqual(retry.status, "no_change")
        self.assertEqual(retry.bundle_ref, second.bundle_ref)
        self.assertEqual(
            (
                self.store.memory_dir
                / f"{memory.memory_id}.r000001.json"
            ).read_bytes(),
            memory_bytes,
        )
        self.assertEqual(
            (
                self.store.relation_dir
                / f"{relation.relation_id}.r000001.json"
            ).read_bytes(),
            relation_bytes,
        )
        self.assertFalse(
            (self.store.memory_dir / f"{memory.memory_id}.r000002.json").exists()
        )
        self.assertFalse(
            (
                self.store.relation_dir
                / f"{relation.relation_id}.r000002.json"
            ).exists()
        )


class EvidenceTests(BundleStoreCase):
    def test_forged_and_stale_refs_fail_before_visibility(self) -> None:
        forged = ObjectRef("source_record", self.source_ref.id, 1, "f" * 64)
        with self.assertRaises(ContractError):
            self.commit(
                summary=self.summary(source_ref=forged),
                memories=(),
                relations=(),
                source_refs=(forged,),
            )
        self.assertFalse(self.store.catalog_path.exists())

        receipt2, _ = self.make_receipt(
            revision=2,
            previous=self.receipt,
            operation="user_edit",
        )
        self.assertEqual(receipt2.revision, 2)
        with self.assertRaises(ContractError) as raised:
            self.commit(summary=self.summary(), memories=(), relations=())
        self.assertEqual(raised.exception.kind, "stale")
        self.assertFalse(self.store.catalog_path.exists())


class ReviewBindingTests(BundleStoreCase):
    def _append_long_term(
        self,
        base: ObjectRef,
        summary: DailySummaryRevision,
    ):
        return self.store.commit_day_bundle(
            request_id=REQUEST_ID,
            run_id="drun_222222222222222222222222",
            input_hashes=self.input_hashes(),
            source_refs=(self.source_ref,),
            receipt_refs=(self.receipt_ref,),
            summary=summary,
            memories=(),
            relations=(),
            long_term_result_ref={
                "request_id": "req_111111111111111111111111",
                "run_id": "run_111111111111111111111111",
                "response_sha256": "8" * 64,
                "status": "no_change",
                "memory_ref": None,
            },
            expected_bundle_ref=base,
            operation="append_long_term_result",
            now=NOW,
        )

    def test_append_review_result_inherits_complete_bundle_and_is_idempotent(self) -> None:
        self.stage_candidates()
        summary = self.summary()
        memory = self.memory()
        relation = self.relation(memory)
        materials = (
            {
                "candidate_kind": "memory",
                "candidate_id": CANDIDATE_MEMORY_ID,
                "formal_ref": ObjectRef(
                    "reusable_memory", memory.memory_id, 1, memory.sha256
                ).to_dict(),
            },
            {
                "candidate_kind": "relation",
                "candidate_id": CANDIDATE_RELATION_ID,
                "formal_ref": ObjectRef(
                    "relation", relation.relation_id, 1, relation.sha256
                ).to_dict(),
            },
        )
        initial = self.commit(
            summary=summary,
            memories=(memory,),
            relations=(relation,),
            materials=materials,
        )
        with_agent = self._append_long_term(initial.bundle_ref, summary)
        inherited = self.store.load_day_manifest(LOCAL_DATE)
        self.assertIsNotNone(inherited)
        review_file, review_sha = self.write_review()
        memory_files = tuple(sorted(self.store.memory_dir.iterdir()))
        relation_files = tuple(sorted(self.store.relation_dir.iterdir()))

        result = self.store.append_review_result(
            expected_bundle_ref=with_agent.bundle_ref,
            expected_summary_ref=with_agent.summary_ref,
            review_file=review_file,
            review_sha256=review_sha,
            user_supplement_sha256="a" * 64,
            now=NOW + dt.timedelta(seconds=5),
        )
        current = self.store.load_day_manifest(LOCAL_DATE)
        self.assertIsNotNone(current)
        assert inherited is not None and current is not None
        self.assertEqual(result.status, "committed")
        self.assertEqual(result.bundle_ref.revision, 3)
        self.assertEqual(result.summary_ref.revision, 2)
        self.assertEqual(current["operation"], "append_review_result")
        for field in (
            "request_id",
            "run_id",
            "input_hashes",
            "source_refs",
            "receipt_refs",
            "memory_refs",
            "relation_refs",
            "candidate_materializations",
            "long_term_result_ref",
            "warnings",
        ):
            self.assertEqual(current[field], inherited[field], field)
        bound = self.store.load_daily_summary_head(LOCAL_DATE)
        self.assertIsNotNone(bound)
        assert bound is not None
        bound_summary, bound_ref = bound
        self.assertEqual(bound_ref, result.summary_ref)
        self.assertEqual(bound_summary.review_file, review_file)
        self.assertEqual(bound_summary.review_sha256, review_sha)
        self.assertEqual(bound_summary.user_supplement_sha256, "a" * 64)
        self.assertEqual(bound_summary.previous_revision_sha256, summary.sha256)
        self.assertEqual(tuple(sorted(self.store.memory_dir.iterdir())), memory_files)
        self.assertEqual(tuple(sorted(self.store.relation_dir.iterdir())), relation_files)

        replay = self.store.append_review_result(
            expected_bundle_ref=with_agent.bundle_ref,
            expected_summary_ref=with_agent.summary_ref,
            review_file=review_file,
            review_sha256=review_sha,
            user_supplement_sha256="a" * 64,
            now=NOW + dt.timedelta(seconds=10),
        )
        self.assertEqual(replay.status, "no_change")
        self.assertEqual(replay.bundle_ref, result.bundle_ref)
        self.assertEqual(replay.summary_ref, result.summary_ref)
        self.assertEqual(len(tuple(self.store.summary_dir.iterdir())), 2)
        self.assertEqual(len(tuple(self.store.committed_dir.iterdir())), 3)

    def test_append_review_result_rejects_tampered_bytes_and_different_hash(self) -> None:
        summary = self.summary()
        initial = self.commit(summary=summary, memories=(), relations=())
        review_file, review_sha = self.write_review(b"first review bytes")
        with self.assertRaises(ContractError) as tampered:
            self.store.append_review_result(
                expected_bundle_ref=initial.bundle_ref,
                expected_summary_ref=initial.summary_ref,
                review_file=review_file,
                review_sha256="f" * 64,
                user_supplement_sha256=None,
                now=NOW,
            )
        self.assertEqual(tampered.exception.kind, "evidence")
        self.assertEqual(self.store.load_day_bundle_ref(LOCAL_DATE), initial.bundle_ref)

        bound = self.store.append_review_result(
            expected_bundle_ref=initial.bundle_ref,
            expected_summary_ref=initial.summary_ref,
            review_file=review_file,
            review_sha256=review_sha,
            user_supplement_sha256=None,
            now=NOW,
        )
        _, different_sha = self.write_review(b"different review bytes")
        with self.assertRaises(ContractError) as conflicting:
            self.store.append_review_result(
                expected_bundle_ref=initial.bundle_ref,
                expected_summary_ref=initial.summary_ref,
                review_file=review_file,
                review_sha256=different_sha,
                user_supplement_sha256=None,
                now=NOW,
            )
        self.assertEqual(conflicting.exception.kind, "conflict")
        self.assertEqual(self.store.load_day_bundle_ref(LOCAL_DATE), bound.bundle_ref)
        self.assertFalse(
            (self.store.summary_dir / f"{summary.summary_id}.r000003.json").exists()
        )

    def test_append_review_result_rejects_stale_bundle_base(self) -> None:
        summary = self.summary()
        initial = self.commit(summary=summary, memories=(), relations=())
        self._append_long_term(initial.bundle_ref, summary)
        review_file, review_sha = self.write_review()
        with self.assertRaises(ContractError) as raised:
            self.store.append_review_result(
                expected_bundle_ref=initial.bundle_ref,
                expected_summary_ref=initial.summary_ref,
                review_file=review_file,
                review_sha256=review_sha,
                user_supplement_sha256=None,
                now=NOW,
            )
        self.assertEqual(raised.exception.kind, "stale")

    def test_append_review_result_recovers_atomic_transaction(self) -> None:
        summary = self.summary()
        initial = self.commit(summary=summary, memories=(), relations=())
        review_file, review_sha = self.write_review()

        def crash(stage: str) -> None:
            if stage == "after_formal_revisions":
                raise RuntimeError("simulated review binding crash")

        crashing = CognitiveBundleStore(
            self.vault,
            state_root=self.store.root,
            fault_hook=crash,
        )
        with self.assertRaises(RuntimeError):
            crashing.append_review_result(
                expected_bundle_ref=initial.bundle_ref,
                expected_summary_ref=initial.summary_ref,
                review_file=review_file,
                review_sha256=review_sha,
                user_supplement_sha256=None,
                now=NOW,
            )
        raw_catalog = json.loads(crashing.catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(raw_catalog["daily_bundles"][0], initial.bundle_ref.to_dict())
        self.assertEqual(raw_catalog["daily_summaries"][0], initial.summary_ref.to_dict())
        self.assertTrue(
            (crashing.summary_dir / f"{summary.summary_id}.r000002.json").is_file()
        )

        recovered = CognitiveBundleStore(self.vault, state_root=crashing.root)
        recovered.recover()
        self.store = recovered
        loaded = recovered.load_daily_summary_head(LOCAL_DATE)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        recovered_summary, recovered_summary_ref = loaded
        self.assertEqual(recovered_summary.review_sha256, review_sha)
        recovered_bundle_ref = recovered.load_day_bundle_ref(LOCAL_DATE)
        self.assertIsNotNone(recovered_bundle_ref)
        assert recovered_bundle_ref is not None
        self.assertEqual(recovered_bundle_ref.revision, 2)
        replay = recovered.append_review_result(
            expected_bundle_ref=initial.bundle_ref,
            expected_summary_ref=initial.summary_ref,
            review_file=review_file,
            review_sha256=review_sha,
            user_supplement_sha256=None,
            now=NOW + dt.timedelta(seconds=5),
        )
        self.assertEqual(replay.status, "no_change")
        self.assertEqual(replay.bundle_ref, recovered_bundle_ref)
        self.assertEqual(replay.summary_ref, recovered_summary_ref)

    def test_append_review_result_fails_closed_on_tampered_summary_chain(self) -> None:
        summary = self.summary()
        initial = self.commit(summary=summary, memories=(), relations=())
        review_file, review_sha = self.write_review()
        path = self.store.summary_dir / f"{summary.summary_id}.r000001.json"
        damaged = json.loads(path.read_text(encoding="utf-8"))
        damaged["overview"] = "tampered summary"
        path.write_bytes(persisted_json_bytes(damaged))
        with self.assertRaises(ContractError):
            self.store.append_review_result(
                expected_bundle_ref=initial.bundle_ref,
                expected_summary_ref=initial.summary_ref,
                review_file=review_file,
                review_sha256=review_sha,
                user_supplement_sha256=None,
                now=NOW,
            )
        raw_catalog = json.loads(self.store.catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(raw_catalog["daily_bundles"][0], initial.bundle_ref.to_dict())
        self.assertFalse(
            (self.store.summary_dir / f"{summary.summary_id}.r000002.json").exists()
        )


class EvidenceContinuationTests(BundleStoreCase):
    def test_quote_must_equal_selected_source_lines_not_just_be_substring(self) -> None:
        short_quote = "先暴露可验证的部分"
        short_span = SourceSpan(
            **{
                **self.span.to_dict(),
                "quote": short_quote,
                "quote_sha256": sha256_bytes(short_quote.encode("utf-8")),
            }
        )
        memory = ReusableMemoryRevision.from_dict(
            {**self.memory().to_dict(), "source_spans": [short_span.to_dict()]}
        )
        with self.assertRaises(ContractError) as raised:
            self.commit(summary=self.summary(), memories=(memory,), relations=())
        self.assertEqual(raised.exception.kind, "stale")

    def test_daily_summary_cannot_be_relation_endpoint_or_long_term_evidence(self) -> None:
        summary = self.summary()
        memory = self.memory()
        relation = self.relation(memory).to_dict()
        relation["to_ref"] = ObjectRef("daily_summary", summary.summary_id, 1, summary.sha256).to_dict()
        with self.assertRaises(ContractError):
            self.commit(summary=summary, memories=(memory,), relations=(relation,))
        self.assertFalse(self.store.catalog_path.exists())


class RecoveryAndPriorityTests(BundleStoreCase):
    def action_store(self) -> CognitiveActionStore:
        actions = CognitiveActionStore(self.vault, state_root=self.store.root)
        self.store.set_action_watermark_reader(
            lambda: actions.action_watermark()[1]
        )
        return actions

    def user_action(self, nonce: str = "bundle-race") -> CognitiveUserAction:
        return CognitiveUserAction(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_cognitive_user_action",
            id=make_cognitive_action_id(nonce),
            created_at=NOW.isoformat(timespec="seconds"),
            action="confirm_receipt",
            target_ref=self.receipt_ref,
            payload=None,
        )

    def commit_at_action_watermark(
        self,
        actions: CognitiveActionStore,
        *,
        summary: DailySummaryRevision,
        memories: tuple[ReusableMemoryRevision, ...] = (),
        relations: tuple[RelationRevision, ...] = (),
    ) -> BundleCommitResult:
        _, watermark = actions.action_watermark()
        hashes = self.input_hashes()
        hashes["user_action_watermark_sha256"] = watermark
        return self.store.commit_day_bundle(
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            input_hashes=hashes,
            source_refs=(self.source_ref,),
            receipt_refs=(self.receipt_ref,),
            summary=summary,
            memories=memories,
            relations=relations,
            now=NOW,
        )

    def commit_at_profile_sha256(
        self,
        profile_sha256: str,
        *,
        summary: DailySummaryRevision,
        memories: tuple[ReusableMemoryRevision, ...] = (),
        relations: tuple[RelationRevision, ...] = (),
    ) -> BundleCommitResult:
        hashes = self.input_hashes()
        hashes["profile_sha256"] = profile_sha256
        return self.store.commit_day_bundle(
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            input_hashes=hashes,
            source_refs=(self.source_ref,),
            receipt_refs=(self.receipt_ref,),
            summary=summary,
            memories=memories,
            relations=relations,
            now=NOW,
        )

    def test_action_submission_waits_through_catalogue_switch(self) -> None:
        actions = self.action_store()
        _, initial_watermark = actions.action_watermark()
        action = self.user_action()
        submission_started = threading.Event()
        submitted = []

        def submit() -> None:
            submission_started.set()
            submitted.append(actions.submit_action(action))

        future_holder = []

        with ThreadPoolExecutor(max_workers=1) as executor:
            def observe(stage: str) -> None:
                if stage == "after_formal_revisions":
                    future = executor.submit(submit)
                    future_holder.append(future)
                    self.assertTrue(submission_started.wait(timeout=1))
                    with self.assertRaises(FutureTimeoutError):
                        future.result(timeout=0.1)
                elif stage == "after_catalog_switch":
                    self.assertEqual(len(future_holder), 1)
                    self.assertFalse(future_holder[0].done())

            self.store._fault_hook = observe
            result = self.commit_at_action_watermark(
                actions,
                summary=self.summary(),
            )
            future_holder[0].result(timeout=2)

        self.assertTrue(result.committed)
        self.assertEqual(len(submitted), 1)
        self.assertEqual(self.store.load_day_bundle_ref(LOCAL_DATE), result.bundle_ref)
        self.assertNotEqual(actions.action_watermark()[1], initial_watermark)

    def test_recovery_rejects_staged_transaction_after_new_action(self) -> None:
        actions = self.action_store()
        summary = self.summary()
        memory = self.memory()

        def crash(stage: str) -> None:
            if stage == "after_formal_revisions":
                raise RuntimeError("simulated process crash")

        self.store._fault_hook = crash
        with self.assertRaises(RuntimeError):
            self.commit_at_action_watermark(
                actions,
                summary=summary,
                memories=(memory,),
            )
        memory_path = self.store.memory_dir / f"{memory.memory_id}.r000001.json"
        self.assertTrue(memory_path.exists())

        actions.submit_action(self.user_action("recovery-race"))
        recovered = CognitiveBundleStore(self.vault, state_root=self.store.root)
        recovered.set_action_watermark_reader(
            lambda: actions.action_watermark()[1]
        )
        recovered.recover()

        self.store = recovered
        self.assertFalse(recovered.catalog_path.exists())
        self.assertFalse(memory_path.exists())
        self.assertFalse(any(recovered.transaction_staging_dir.iterdir()))
        self.assertTrue(any(recovered.quarantine_dir.rglob(memory_path.name)))

    def test_profile_writer_waits_through_catalogue_switch(self) -> None:
        profile = {"sha256": "a" * 64}
        self.store.set_profile_sha256_reader(lambda: profile["sha256"])
        writer_started = threading.Event()
        future_holder = []

        def write_profile() -> None:
            writer_started.set()
            with agent_profile_lock(self.vault):
                profile["sha256"] = "b" * 64

        with ThreadPoolExecutor(max_workers=1) as executor:
            def observe(stage: str) -> None:
                if stage == "after_formal_revisions":
                    future = executor.submit(write_profile)
                    future_holder.append(future)
                    self.assertTrue(writer_started.wait(timeout=1))
                    with self.assertRaises(FutureTimeoutError):
                        future.result(timeout=0.1)
                elif stage == "after_catalog_switch":
                    self.assertEqual(len(future_holder), 1)
                    self.assertFalse(future_holder[0].done())

            self.store._fault_hook = observe
            result = self.commit_at_profile_sha256(
                profile["sha256"],
                summary=self.summary(),
            )
            future_holder[0].result(timeout=2)

        self.assertTrue(result.committed)
        self.assertEqual(profile["sha256"], "b" * 64)
        self.assertEqual(self.store.load_day_bundle_ref(LOCAL_DATE), result.bundle_ref)

    def test_recovery_rejects_staged_transaction_after_profile_change(self) -> None:
        profile = {"sha256": "a" * 64}
        self.store.set_profile_sha256_reader(lambda: profile["sha256"])
        summary = self.summary()
        memory = self.memory()

        def crash(stage: str) -> None:
            if stage == "after_formal_revisions":
                raise RuntimeError("simulated process crash")

        self.store._fault_hook = crash
        with self.assertRaises(RuntimeError):
            self.commit_at_profile_sha256(
                profile["sha256"],
                summary=summary,
                memories=(memory,),
            )
        memory_path = self.store.memory_dir / f"{memory.memory_id}.r000001.json"
        self.assertTrue(memory_path.exists())

        with agent_profile_lock(self.vault):
            profile["sha256"] = "b" * 64
        recovered = CognitiveBundleStore(self.vault, state_root=self.store.root)
        recovered.set_profile_sha256_reader(lambda: profile["sha256"])
        recovered.recover()

        self.store = recovered
        self.assertFalse(recovered.catalog_path.exists())
        self.assertFalse(memory_path.exists())
        self.assertFalse(any(recovered.transaction_staging_dir.iterdir()))
        self.assertTrue(any(recovered.quarantine_dir.rglob(memory_path.name)))

    def test_crash_after_formal_files_recovers_all_or_none(self) -> None:
        summary = self.summary()
        memory = self.memory()
        relation = self.relation(memory)

        def crash(stage: str) -> None:
            if stage == "after_formal_revisions":
                raise RuntimeError("simulated process crash")

        crashing = CognitiveBundleStore(self.vault, state_root=self.store.root, fault_hook=crash)
        self.store = crashing
        with self.assertRaises(RuntimeError):
            self.commit(summary=summary, memories=(memory,), relations=(relation,))
        self.assertFalse(crashing.catalog_path.exists())
        self.assertTrue((crashing.memory_dir / f"{memory.memory_id}.r000001.json").exists())

        recovered = CognitiveBundleStore(self.vault, state_root=crashing.root)
        recovered.recover()
        self.store = recovered
        catalog = recovered.load_catalog()
        self.assertEqual(len(catalog["daily_bundles"]), 1)
        self.assertEqual(len(catalog["daily_summaries"]), 1)
        self.assertEqual(len(catalog["reusable_memories"]), 1)
        self.assertEqual(len(catalog["relations"]), 1)
        self.assertFalse(any(recovered.transaction_staging_dir.iterdir()))

    def test_crash_after_committed_directory_recovers_catalogue(self) -> None:
        summary = self.summary()

        def crash(stage: str) -> None:
            if stage == "after_committed_directory":
                raise RuntimeError("simulated process crash")

        crashing = CognitiveBundleStore(self.vault, state_root=self.store.root, fault_hook=crash)
        self.store = crashing
        with self.assertRaises(RuntimeError):
            self.commit(summary=summary, memories=(), relations=())
        self.assertFalse(crashing.catalog_path.exists())
        self.assertTrue(crashing._bundle_directory("db_20260818", 1).is_dir())
        recovered = CognitiveBundleStore(self.vault, state_root=crashing.root)
        recovered.recover()
        self.store = recovered
        self.assertIsNotNone(recovered.load_day_bundle_ref(LOCAL_DATE))

    def test_stale_crash_transaction_quarantines_unpublished_revision_slots(self) -> None:
        summary = self.summary()
        memory = self.memory()

        def crash(stage: str) -> None:
            if stage == "after_formal_revisions":
                raise RuntimeError("simulated process crash")

        crashing = CognitiveBundleStore(self.vault, state_root=self.store.root, fault_hook=crash)
        self.store = crashing
        with self.assertRaises(RuntimeError):
            self.commit(summary=summary, memories=(memory,), relations=())
        memory_path = crashing.memory_dir / f"{memory.memory_id}.r000001.json"
        self.assertTrue(memory_path.exists())

        changed = DAY_BYTES.replace(QUOTE.encode("utf-8"), "改动后的原始记录。".encode("utf-8"))
        (self.vault / DAY_FILE).write_bytes(changed)
        RecordStore(self.vault, state_root=crashing.root).reconcile_day(
            DAY_FILE,
            now=NOW + dt.timedelta(minutes=1),
            timezone=NOW.tzinfo,
        )
        recovered = CognitiveBundleStore(self.vault, state_root=crashing.root)
        recovered.recover()
        self.store = recovered
        self.assertFalse(recovered.catalog_path.exists())
        self.assertFalse(memory_path.exists())
        self.assertFalse(any(recovered.transaction_staging_dir.iterdir()))
        self.assertTrue(any(recovered.quarantine_dir.rglob(memory_path.name)))

    def test_user_tombstone_is_visible_append_only_and_cannot_be_revived(self) -> None:
        summary1 = self.summary()
        memory1 = self.memory()
        first = self.commit(summary=summary1, memories=(memory1,), relations=())
        memory2 = self.memory(
            revision=2,
            previous=memory1,
            operation="tombstone",
            status="tombstone",
            action_id=ACTION_ID,
        )
        tombstone = self.store.commit_user_memory_revision(
            memory2,
            expected_ref=first.memory_refs[0],
            now=NOW,
        )
        self.assertEqual(tombstone.object_ref.revision, 2)
        self.assertEqual(self.store.list_active_memories(), ())
        self.assertEqual(
            self.store.find_user_action_materialization("reusable_memory", memory1.memory_id, ACTION_ID),
            tombstone.object_ref,
        )
        self.assertTrue((self.store.memory_dir / f"{memory1.memory_id}.r000001.json").exists())
        self.assertTrue((self.store.memory_dir / f"{memory1.memory_id}.r000002.json").exists())

        summary2 = self.summary(revision=2, previous=summary1)
        memory3 = self.memory(
            revision=3,
            previous=memory2,
            run_id="drun_222222222222222222222222",
            bundle_revision=2,
        )
        with self.assertRaises(ContractError):
            self.commit(
                summary=summary2,
                memories=(memory3,),
                relations=(),
                run_id="drun_222222222222222222222222",
                hashes_nonce="two",
                expected=first.bundle_ref,
            )
        self.assertEqual(self.store.load_memory_head(memory1.memory_id).status, "tombstone")


class FilesystemHardeningTests(BundleStoreCase):
    def test_occupied_symlink_hardlink_and_directory_slots_fail_closed(self) -> None:
        memory = self.memory()
        target = self.store.memory_dir / f"{memory.memory_id}.r000001.json"
        sentinel = self.store.root / "sentinel.json"
        sentinel.write_text("{}\n", encoding="utf-8")
        sentinel.chmod(0o600)

        target.symlink_to(sentinel)
        with self.assertRaises(ContractError):
            self.store._safe_write_immutable(target, memory.to_dict())
        target.unlink()

        os.link(sentinel, target)
        with self.assertRaises(ContractError):
            self.store._safe_write_immutable(target, memory.to_dict())
        target.unlink()

        target.mkdir(mode=0o700)
        with self.assertRaises(ContractError):
            self.store._safe_write_immutable(target, memory.to_dict())
        target.rmdir()

        target.write_text("{\"occupied\": true}\n", encoding="utf-8")
        target.chmod(0o600)
        with self.assertRaises(ContractError):
            self.store._safe_write_immutable(target, memory.to_dict())


if __name__ == "__main__":
    unittest.main()
