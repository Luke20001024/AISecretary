#!/usr/bin/env python3
"""Bounded, offline tests for the Cognitive Secretary day orchestrator."""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_AGENT = ROOT / "context-agent"
if str(CONTEXT_AGENT) not in sys.path:
    sys.path.insert(0, str(CONTEXT_AGENT))

from cognitive_day_orchestrator_v1 import CognitiveDayOrchestrator  # noqa: E402
from cognitive_v1 import (  # noqa: E402
    COGNITIVE_SCHEMA_VERSION,
    DailySummaryRevision,
    InterpretationReceiptRevision,
    ObjectRef,
    SourceRecordRevision,
    SourceSpan,
    make_daily_summary_id,
    make_receipt_id,
    persisted_sha256,
)
from core import sha256_bytes  # noqa: E402


TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 8, 18, 21, 0, tzinfo=TZ)
DATE = "2026-08-18"
RECORD_ID = "rec_111111111111111111111111"
RECEIPT_ID = make_receipt_id(RECORD_ID)
MEMORY_ID = "rmem_222222222222222222222222"
QUOTE = "评审前先定义最早可验证的部分。"
SHA_A = "a" * 64
SHA_B = "b" * 64
REVIEW_BYTES = b"# Daily Review\n\nBound to the current summary.\n"
SHA_C = sha256_bytes(REVIEW_BYTES)
SHA_D = "d" * 64


class FakeRecordStore:
    def __init__(self, source: SourceRecordRevision) -> None:
        self.source = source

    def load_head(self, record_id: str) -> dict[str, Any]:
        if record_id != self.source.record_id:
            raise AssertionError("unexpected record")
        return self.source.to_dict()


class FakeActionStore:
    def __init__(self, receipt: InterpretationReceiptRevision) -> None:
        self.receipt = receipt

    def load_receipt_head(self, receipt_id: str) -> InterpretationReceiptRevision:
        if receipt_id != self.receipt.receipt_id:
            raise AssertionError("unexpected receipt")
        return self.receipt

    def action_watermark(self):
        return (), SHA_B


class FakeBundleStore:
    def __init__(
        self,
        summary: DailySummaryRevision,
        source_ref: ObjectRef,
        receipt_ref: ObjectRef,
    ) -> None:
        self.summary = summary
        self.summary_ref = ObjectRef(
            "daily_summary", summary.summary_id, summary.revision, summary.sha256
        )
        self.source_ref = source_ref
        self.receipt_ref = receipt_ref
        self.bundle_ref: ObjectRef | None = None
        self.manifest: dict[str, Any] | None = None
        self.memory = SimpleNamespace(
            memory_id=MEMORY_ID, revision=1, sha256="e" * 64
        )
        self.append_calls = 0
        self.review_bind_calls = 0

    def commit_initial(self) -> ObjectRef:
        if self.bundle_ref is not None:
            return self.bundle_ref
        manifest = {
            "schema_version": "1.0",
            "kind": "memento_daily_bundle_revision",
            "bundle_id": "db_20260818",
            "revision": 1,
            "status": "committed",
            "operation": "initial_commit",
            "created_at": NOW.isoformat(timespec="seconds"),
            "committed_at": NOW.isoformat(timespec="seconds"),
            "local_date": DATE,
            "request_id": "dreq_333333333333333333333333",
            "run_id": "drun_444444444444444444444444",
            "input_hashes": {
                "source_manifest_sha256": "1" * 64,
                "receipt_manifest_sha256": "2" * 64,
                "profile_sha256": SHA_A,
                "user_action_watermark_sha256": SHA_B,
                "policy_sha256": "3" * 64,
            },
            "source_refs": [self.source_ref.to_dict()],
            "receipt_refs": [self.receipt_ref.to_dict()],
            "memory_refs": [
                ObjectRef(
                    "reusable_memory",
                    self.memory.memory_id,
                    self.memory.revision,
                    self.memory.sha256,
                ).to_dict()
            ],
            "relation_refs": [],
            "summary_ref": self.summary_ref.to_dict(),
            "candidate_materializations": [],
            "long_term_result_ref": None,
            "warnings": [],
            "previous_revision_sha256": None,
        }
        self.manifest = manifest
        self.bundle_ref = ObjectRef(
            "daily_bundle", "db_20260818", 1, persisted_sha256(manifest)
        )
        return self.bundle_ref

    def load_day_bundle_ref(self, local_date: str) -> ObjectRef | None:
        return self.bundle_ref if local_date == DATE else None

    def load_day_manifest(self, local_date: str) -> dict[str, Any] | None:
        return None if local_date != DATE or self.manifest is None else dict(self.manifest)

    def load_daily_summary_head(self, local_date: str):
        if local_date != DATE or self.bundle_ref is None:
            return None
        return self.summary, self.summary_ref

    def list_active_memories(self):
        return (self.memory,) if self.bundle_ref is not None else ()

    def list_active_relations(self):
        return ()

    def append_review_result(self, **kwargs: Any):
        self.review_bind_calls += 1
        assert self.bundle_ref is not None and self.manifest is not None
        if kwargs["expected_bundle_ref"] != self.bundle_ref:
            raise AssertionError("stale review bundle")
        if kwargs["expected_summary_ref"] != self.summary_ref:
            raise AssertionError("stale review summary")
        previous_bundle = self.bundle_ref
        previous_summary = self.summary
        summary = DailySummaryRevision.from_dict(
            {
                **previous_summary.to_dict(),
                "revision": previous_summary.revision + 1,
                "operation": "regenerate",
                "created_at": kwargs["now"].isoformat(timespec="seconds"),
                "review_file": kwargs["review_file"],
                "review_sha256": kwargs["review_sha256"],
                "user_supplement_sha256": kwargs[
                    "user_supplement_sha256"
                ],
                "previous_revision_sha256": previous_summary.sha256,
            }
        )
        summary_ref = ObjectRef(
            "daily_summary", summary.summary_id, summary.revision, summary.sha256
        )
        manifest = {
            **self.manifest,
            "revision": previous_bundle.revision + 1,
            "operation": "append_review_result",
            "summary_ref": summary_ref.to_dict(),
            "previous_revision_sha256": previous_bundle.revision_sha256,
        }
        self.summary = summary
        self.summary_ref = summary_ref
        self.manifest = manifest
        self.bundle_ref = ObjectRef(
            "daily_bundle",
            "db_20260818",
            manifest["revision"],
            persisted_sha256(manifest),
        )
        return SimpleNamespace(
            status="committed",
            bundle_ref=self.bundle_ref,
            summary_ref=summary_ref,
        )

    def commit_day_bundle(self, **kwargs: Any):
        self.append_calls += 1
        assert self.bundle_ref is not None and self.manifest is not None
        if kwargs["expected_bundle_ref"] != self.bundle_ref:
            raise AssertionError("stale fake bundle")
        if kwargs["operation"] != "append_long_term_result":
            raise AssertionError("unexpected fake operation")
        previous = self.bundle_ref
        manifest = {
            **self.manifest,
            "revision": previous.revision + 1,
            "operation": "append_long_term_result",
            "long_term_result_ref": dict(kwargs["long_term_result_ref"]),
            "warnings": list(kwargs["warnings"]),
            "previous_revision_sha256": previous.revision_sha256,
        }
        self.manifest = manifest
        self.bundle_ref = ObjectRef(
            "daily_bundle",
            "db_20260818",
            manifest["revision"],
            persisted_sha256(manifest),
        )
        return SimpleNamespace(
            status="committed",
            committed=True,
            bundle_ref=self.bundle_ref,
            summary_ref=self.summary_ref,
            memory_refs=tuple(
                ObjectRef.from_dict(row) for row in manifest["memory_refs"]
            ),
            relation_refs=(),
        )


class FakePipeline:
    def __init__(
        self,
        records: FakeRecordStore,
        actions: FakeActionStore,
        bundles: FakeBundleStore,
    ) -> None:
        self.records = records
        self.actions = actions
        self.bundles = bundles
        self.run_calls = 0
        self.provider_calls = 0

    def run_day(self, local_date: str, **kwargs: Any):
        self.run_calls += 1
        if local_date != DATE or kwargs["profile_sha256"] != SHA_A:
            raise AssertionError("pipeline binding invalid")
        if self.bundles.bundle_ref is None:
            self.provider_calls += 1
            ref = self.bundles.commit_initial()
            status = "committed"
            requires = True
        else:
            ref = self.bundles.bundle_ref
            status = "no_change"
            requires = False
        return SimpleNamespace(
            status=status,
            record_ids=(RECORD_ID,),
            receipt_refs=(self.bundles.receipt_ref,),
            interpretation_results=(),
            daily_result={"status": status},
            commit_result=SimpleNamespace(bundle_ref=ref),
            material_brief=SimpleNamespace(
                requires_long_term_review=requires,
                material_sha256=SHA_D,
            ),
        )


class FakeReviewResult:
    def __init__(self, base_summary_ref: ObjectRef, status: str) -> None:
        self.status = status
        self.base_summary_ref = base_summary_ref
        self.review_file = f"Reviews/Daily/{DATE}.md"
        self.review_sha256 = SHA_C
        self.user_supplement_sha256 = None

    def summary_binding(self) -> dict[str, str | None]:
        return {
            "review_file": self.review_file,
            "review_sha256": self.review_sha256,
            "user_supplement_sha256": self.user_supplement_sha256,
        }


class FakeRenderer:
    def __init__(
        self,
        vault: Path | None = None,
        error: bool = False,
        delay: float = 0.0,
    ) -> None:
        self.vault = vault
        self.error = error
        self.delay = delay
        self.calls = 0
        self.write_calls = 0
        self.written = False

    def render(self, **kwargs: Any):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise RuntimeError("redacted renderer failure")
        if kwargs["summary_ref"].kind != "daily_summary":
            raise AssertionError("summary not bound")
        review_path = None
        if self.vault is not None:
            review_path = self.vault / "Reviews" / "Daily" / f"{DATE}.md"
        current = (
            None
            if review_path is None or not review_path.exists()
            else review_path.read_bytes()
        )
        if current == REVIEW_BYTES:
            status = "unchanged"
        else:
            if review_path is not None:
                review_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                review_path.write_bytes(REVIEW_BYTES)
                review_path.chmod(0o600)
            status = "recovered" if self.written else "created"
            self.written = True
            self.write_calls += 1
        return FakeReviewResult(kwargs["summary_ref"], status)


class StoreWithoutReviewAppend:
    """Expose every existing fake-store API except the required Review CAS."""

    def __init__(self, delegate: FakeBundleStore) -> None:
        self.delegate = delegate

    def __getattr__(self, name: str) -> Any:
        if name == "append_review_result":
            raise AttributeError(name)
        return getattr(self.delegate, name)


class FakeAdapter:
    def __init__(self, error: bool = False) -> None:
        self.error = error
        self.calls = 0
        self.result = {
            "request_id": "arq_555555555555555555555555",
            "run_id": "arun_666666666666666666666666",
            "response_sha256": "7" * 64,
            "status": "no_change",
            "memory_ref": None,
        }

    def process(self, **kwargs: Any):
        self.calls += 1
        if self.error:
            raise RuntimeError("redacted Agent failure")
        if kwargs["profile_sha256"] != SHA_A:
            raise AssertionError("profile not bound")
        return SimpleNamespace(
            status="completed",
            material_sha256=SHA_D,
            agent_result_ref=dict(self.result),
            warning=None,
        )


class FakeProjector:
    def __init__(self, error: bool = False) -> None:
        self.error = error
        self.calls = 0
        self.kwargs: list[dict[str, Any]] = []

    def publish(self, **kwargs: Any):
        self.calls += 1
        self.kwargs.append(dict(kwargs))
        if self.error:
            raise RuntimeError("redacted projection failure")
        if kwargs["profile"]["profile_sha256"] != SHA_A:
            raise AssertionError("projection profile not bound")
        return SimpleNamespace(
            landscape=SimpleNamespace(
                snapshot_id="lnd_888888888888888888888888", sha256="8" * 64
            ),
            home=SimpleNamespace(sha256="9" * 64),
        )


class SimulatedCrash(BaseException):
    pass


class DayOrchestratorCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="memento-day-orchestrator-")
        self.vault = Path(self.temporary.name) / "vault"
        self.vault.mkdir(mode=0o700)
        self.vault.chmod(0o700)
        source = SourceRecordRevision(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_source_record_revision",
            record_id=RECORD_ID,
            revision=1,
            status="active",
            operation="ingest",
            created_at=NOW.isoformat(timespec="seconds"),
            captured_at="2026-08-18T10:50:00+08:00",
            local_date=DATE,
            source_type="text",
            source_app="Chrome",
            source_file=f"{DATE}.md",
            line_start=1,
            line_end=1,
            entry_sha256="1" * 64,
            source_snapshot_sha256="2" * 64,
            attachments=(),
            ingest_origin="reconciler",
            previous_revision_sha256=None,
        )
        source_ref = ObjectRef("source_record", RECORD_ID, 1, source.sha256)
        span = SourceSpan(
            record_id=RECORD_ID,
            record_revision=1,
            record_revision_sha256=source.sha256,
            source_file=f"{DATE}.md",
            line_start=1,
            line_end=1,
            quote=QUOTE,
            quote_sha256=sha256_bytes(QUOTE.encode("utf-8")),
        )
        receipt = InterpretationReceiptRevision(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_interpretation_receipt_revision",
            receipt_id=RECEIPT_ID,
            revision=1,
            status="ready",
            operation="interpret",
            created_at=NOW.isoformat(timespec="seconds"),
            request_id="ireq_aaaaaaaaaaaaaaaaaaaaaaaa",
            run_id="irun_bbbbbbbbbbbbbbbbbbbbbbbb",
            record_ref=source_ref,
            user_action_id=None,
            summary="先定义最早验证部分。",
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
            source_spans=(span,),
            contract_version="record-interpreter-v1",
            feedback_watermark_sha256=SHA_B,
            previous_revision_sha256=None,
        )
        receipt_ref = ObjectRef(
            "interpretation_receipt", RECEIPT_ID, 1, receipt.sha256
        )
        summary = DailySummaryRevision(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_daily_summary_revision",
            summary_id=make_daily_summary_id(DATE),
            revision=1,
            status="active",
            operation="generate",
            created_at=NOW.isoformat(timespec="seconds"),
            local_date=DATE,
            overview="今天回到更早验证。",
            themes=("更早验证",),
            changes=(),
            unresolved_questions=("什么时候足够验证？",),
            action_clues=("下次先发可验证版。",),
            source_refs=(source_ref,),
            receipt_refs=(receipt_ref,),
            review_file=f"Reviews/Daily/{DATE}.md",
            review_sha256=None,
            user_supplement_sha256=None,
            previous_revision_sha256=None,
        )
        self.records = FakeRecordStore(source)
        self.actions = FakeActionStore(receipt)
        self.bundles = FakeBundleStore(summary, source_ref, receipt_ref)
        self.pipeline = FakePipeline(self.records, self.actions, self.bundles)
        self.renderer = FakeRenderer(self.vault)
        self.adapter = FakeAdapter()
        self.projector = FakeProjector()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def profile_loader(vault: Path) -> Mapping[str, Any]:
        del vault
        return {"profile_sha256": SHA_A, "memories": []}

    def orchestrator(self, **overrides: Any) -> CognitiveDayOrchestrator:
        bundle_store = overrides.pop("bundle_store", self.bundles)
        return CognitiveDayOrchestrator(
            self.vault,
            pipeline=overrides.pop("pipeline", self.pipeline),
            renderer=overrides.pop("renderer", self.renderer),
            long_term_adapter=overrides.pop("long_term_adapter", self.adapter),
            projector=overrides.pop("projector", self.projector),
            bundle_store=bundle_store,
            profile_loader=self.profile_loader,
            clock=lambda: NOW,
            **overrides,
        )

    def test_success_links_agent_result_and_publishes(self) -> None:
        result = self.orchestrator().run_day(DATE, trigger="manual")

        self.assertEqual(result.status, "committed")
        self.assertEqual(result.review_status, "completed")
        self.assertEqual(result.review_sha256, SHA_C)
        self.assertEqual(result.long_term_status, "completed")
        self.assertEqual(result.projection_status, "completed")
        self.assertEqual(result.warnings, ())
        self.assertEqual(self.bundles.bundle_ref.revision, 3)  # type: ignore[union-attr]
        self.assertEqual(result.bundle_ref, self.bundles.bundle_ref.to_dict())  # type: ignore[union-attr]
        self.assertEqual(self.bundles.review_bind_calls, 1)
        self.assertEqual(self.bundles.append_calls, 1)
        self.assertEqual(self.renderer.calls, 1)
        self.assertEqual(self.adapter.calls, 1)
        self.assertEqual(self.projector.calls, 1)

    def test_default_renderer_uses_an_independent_private_state_root(self) -> None:
        orchestrator = self.orchestrator(renderer=None)

        self.assertEqual(
            orchestrator.renderer.root,
            self.vault.resolve()
            / ".context-agent"
            / "cognitive-secretary-v1"
            / "daily-review-projection",
        )
        self.assertNotEqual(orchestrator.renderer.root, orchestrator.root)

    def test_no_change_replay_has_zero_downstream_or_provider_calls(self) -> None:
        orchestrator = self.orchestrator()
        first = orchestrator.run_day(DATE)
        self.assertEqual(first.status, "committed")
        second = orchestrator.run_day(DATE)

        self.assertEqual(second.status, "no_change")
        self.assertTrue(second.cached)
        self.assertEqual(self.pipeline.provider_calls, 1)
        self.assertEqual(self.renderer.calls, 1)
        self.assertEqual(self.adapter.calls, 1)
        self.assertEqual(self.projector.calls, 1)
        self.assertEqual(self.bundles.append_calls, 1)

    def test_no_candidate_is_projected_and_returned_without_bundle_downstream(self) -> None:
        def no_candidate(local_date: str, **kwargs: Any):
            del kwargs
            self.assertEqual(local_date, DATE)
            return SimpleNamespace(
                status="no_candidate",
                record_ids=(RECORD_ID,),
                receipt_refs=(),
                interpretation_results=(
                    {
                        "status": "no_candidate",
                        "request": {"record_ref": self.bundles.source_ref.to_dict()},
                        "run": {"error_kind": None},
                        "receipt": None,
                    },
                ),
                commit_result=None,
                material_brief=None,
            )

        self.pipeline.run_day = no_candidate  # type: ignore[method-assign]

        result = self.orchestrator().run_day(DATE)

        self.assertEqual(result.status, "no_candidate")
        self.assertEqual(result.review_status, "skipped")
        self.assertEqual(result.long_term_status, "skipped")
        self.assertEqual(result.projection_status, "completed")
        self.assertEqual(self.renderer.calls, 0)
        self.assertEqual(self.adapter.calls, 0)
        self.assertEqual(self.bundles.append_calls, 0)
        self.assertEqual(
            self.projector.kwargs[-1]["schedule"]["last_run_status"],
            "no_candidate",
        )
        self.assertEqual(
            self.projector.kwargs[-1]["record_runtime_statuses"],
            {RECORD_ID: {"status": "no_candidate", "error_kind": None}},
        )

    def test_old_bundle_no_receipts_skips_downstream_and_forces_record_projection(
        self,
    ) -> None:
        orchestrator = self.orchestrator()
        first = orchestrator.run_day(DATE)
        self.assertEqual(first.status, "committed")
        original_bundle = self.bundles.bundle_ref
        review_calls = self.renderer.calls
        adapter_calls = self.adapter.calls
        append_calls = self.bundles.append_calls
        projection_calls = self.projector.calls

        def no_receipts(local_date: str, **kwargs: Any):
            del kwargs
            self.assertEqual(local_date, DATE)
            return SimpleNamespace(
                status="no_receipts",
                record_ids=(RECORD_ID,),
                receipt_refs=(),
                interpretation_results=(
                    {
                        "status": "error",
                        "request": {"record_ref": self.bundles.source_ref.to_dict()},
                        "run": {"error_kind": "schema"},
                        "receipt": None,
                    },
                ),
                commit_result=None,
                material_brief=None,
            )

        self.pipeline.run_day = no_receipts  # type: ignore[method-assign]

        second = orchestrator.run_day(DATE, trigger="recovery")

        self.assertEqual(second.status, "no_receipts")
        self.assertEqual(second.bundle_ref, original_bundle.to_dict())  # type: ignore[union-attr]
        self.assertEqual(second.review_status, "skipped")
        self.assertEqual(second.long_term_status, "skipped")
        self.assertEqual(second.projection_status, "completed")
        self.assertEqual(self.renderer.calls, review_calls)
        self.assertEqual(self.adapter.calls, adapter_calls)
        self.assertEqual(self.bundles.append_calls, append_calls)
        self.assertEqual(self.projector.calls, projection_calls + 1)
        self.assertEqual(
            self.projector.kwargs[-1]["schedule"]["last_run_status"],
            "no_receipts",
        )
        self.assertEqual(
            self.projector.kwargs[-1]["record_runtime_statuses"],
            {RECORD_ID: {"status": "failed", "error_kind": "invalid_response"}},
        )

    def test_old_bundle_current_daily_no_change_reprojects_without_repeating_downstream(
        self,
    ) -> None:
        orchestrator = self.orchestrator()
        first = orchestrator.run_day(DATE)
        self.assertEqual(first.status, "committed")
        original_bundle = self.bundles.bundle_ref
        review_calls = self.renderer.calls
        adapter_calls = self.adapter.calls
        append_calls = self.bundles.append_calls
        projection_calls = self.projector.calls

        def current_no_change(local_date: str, **kwargs: Any):
            del kwargs
            self.assertEqual(local_date, DATE)
            return SimpleNamespace(
                status="no_change",
                record_ids=(RECORD_ID,),
                receipt_refs=(self.bundles.receipt_ref,),
                interpretation_results=(),
                daily_result={"status": "no_change", "cached": False},
                commit_result=None,
                material_brief=None,
            )

        self.pipeline.run_day = current_no_change  # type: ignore[method-assign]

        second = orchestrator.run_day(DATE, trigger="recovery")

        self.assertEqual(second.status, "no_change")
        self.assertEqual(second.bundle_ref, original_bundle.to_dict())  # type: ignore[union-attr]
        self.assertEqual(second.review_status, "no_change")
        self.assertEqual(second.long_term_status, "already_linked")
        self.assertEqual(second.projection_status, "completed")
        self.assertEqual(self.renderer.calls, review_calls + 1)
        self.assertEqual(self.adapter.calls, adapter_calls)
        self.assertEqual(self.bundles.append_calls, append_calls)
        self.assertEqual(self.bundles.review_bind_calls, 1)
        self.assertEqual(self.projector.calls, projection_calls + 1)
        self.assertEqual(
            self.projector.kwargs[-1]["schedule"]["last_run_status"],
            "no_change",
        )

    def test_old_bundle_current_daily_no_change_repairs_missing_review(self) -> None:
        initial_bundle = self.bundles.commit_initial()

        def current_no_change(local_date: str, **kwargs: Any):
            del kwargs
            self.assertEqual(local_date, DATE)
            return SimpleNamespace(
                status="no_change",
                record_ids=(RECORD_ID,),
                receipt_refs=(self.bundles.receipt_ref,),
                interpretation_results=(),
                daily_result={"status": "no_change", "cached": False},
                commit_result=None,
                material_brief=None,
            )

        self.pipeline.run_day = current_no_change  # type: ignore[method-assign]

        result = self.orchestrator().run_day(DATE, trigger="recovery")

        self.assertEqual(result.status, "no_change")
        self.assertEqual(result.review_status, "completed")
        self.assertEqual(result.long_term_status, "no_material")
        self.assertEqual(result.projection_status, "completed")
        self.assertNotEqual(result.bundle_ref, initial_bundle.to_dict())
        self.assertEqual(self.renderer.calls, 1)
        self.assertEqual(self.bundles.review_bind_calls, 1)
        self.assertEqual(self.adapter.calls, 0)
        self.assertEqual(self.bundles.append_calls, 0)
        self.assertEqual(self.projector.calls, 1)

    def test_record_terminal_no_change_retires_old_bundle_without_review(self) -> None:
        initial_bundle = self.bundles.commit_initial()

        def record_terminal(local_date: str, **kwargs: Any):
            del kwargs
            self.assertEqual(local_date, DATE)
            return SimpleNamespace(
                status="no_change",
                record_ids=(RECORD_ID,),
                receipt_refs=(),
                interpretation_results=(),
                daily_result=None,
                commit_result=None,
                material_brief=None,
            )

        self.pipeline.run_day = record_terminal  # type: ignore[method-assign]

        result = self.orchestrator().run_day(DATE, trigger="recovery")

        self.assertEqual(result.status, "no_change")
        self.assertEqual(result.bundle_ref, initial_bundle.to_dict())
        self.assertEqual(result.review_status, "skipped")
        self.assertEqual(result.long_term_status, "skipped")
        self.assertEqual(result.projection_status, "completed")
        self.assertEqual(self.renderer.calls, 0)
        self.assertEqual(self.adapter.calls, 0)
        self.assertEqual(self.bundles.review_bind_calls, 0)
        self.assertEqual(self.projector.calls, 1)

    def test_bound_review_is_restored_without_appending_another_bundle(self) -> None:
        orchestrator = self.orchestrator()
        first = orchestrator.run_day(DATE)
        self.assertEqual(first.status, "committed")
        original_bundle = self.bundles.bundle_ref
        original_revision = original_bundle.revision  # type: ignore[union-attr]
        review_path = self.vault / "Reviews" / "Daily" / f"{DATE}.md"
        review_path.write_bytes(b"tampered")
        review_path.chmod(0o600)

        def current_no_change(local_date: str, **kwargs: Any):
            del kwargs
            self.assertEqual(local_date, DATE)
            return SimpleNamespace(
                status="no_change",
                record_ids=(RECORD_ID,),
                receipt_refs=(self.bundles.receipt_ref,),
                interpretation_results=(),
                daily_result={"status": "no_change", "cached": True},
                commit_result=None,
                material_brief=None,
            )

        self.pipeline.run_day = current_no_change  # type: ignore[method-assign]
        review_calls = self.renderer.calls
        write_calls = self.renderer.write_calls
        bind_calls = self.bundles.review_bind_calls
        append_calls = self.bundles.append_calls
        adapter_calls = self.adapter.calls

        recovered = orchestrator.run_day(DATE, trigger="recovery")

        self.assertEqual(recovered.status, "no_change")
        self.assertEqual(recovered.review_status, "recovered")
        self.assertEqual(recovered.bundle_ref, original_bundle.to_dict())  # type: ignore[union-attr]
        self.assertEqual(self.bundles.bundle_ref.revision, original_revision)  # type: ignore[union-attr]
        self.assertEqual(review_path.read_bytes(), REVIEW_BYTES)
        self.assertEqual(self.renderer.calls, review_calls + 1)
        self.assertEqual(self.renderer.write_calls, write_calls + 1)
        self.assertEqual(self.bundles.review_bind_calls, bind_calls)
        self.assertEqual(self.bundles.append_calls, append_calls)
        self.assertEqual(self.adapter.calls, adapter_calls)
        self.assertNotIn("review_failed", recovered.warnings)

    def test_replay_carries_prior_pipeline_profile_only_when_output_is_current(self) -> None:
        calls: list[dict[str, Any]] = []
        original = self.pipeline.run_day

        def observed(local_date: str, **kwargs: Any):
            calls.append(dict(kwargs))
            return original(local_date, **kwargs)

        self.pipeline.run_day = observed  # type: ignore[method-assign]
        orchestrator = self.orchestrator()
        first = orchestrator.run_day(DATE)
        second = orchestrator.run_day(DATE)

        self.assertEqual(first.status, "committed")
        self.assertEqual(second.status, "no_change")
        self.assertEqual(calls[0]["replay_profile_sha256"], None)
        self.assertEqual(calls[1]["replay_profile_sha256"], SHA_A)

    def test_renderer_failure_is_warning_and_does_not_block_later_stages(self) -> None:
        renderer = FakeRenderer(error=True)
        result = self.orchestrator(renderer=renderer).run_day(DATE)

        self.assertEqual(result.status, "committed_with_warnings")
        self.assertEqual(result.review_status, "failed")
        self.assertIn("review_failed", result.warnings)
        self.assertEqual(self.adapter.calls, 1)
        self.assertEqual(self.projector.calls, 1)
        self.assertEqual(self.bundles.bundle_ref.revision, 2)  # type: ignore[union-attr]

    def test_long_term_failure_preserves_daily_bundle_and_projects(self) -> None:
        adapter = FakeAdapter(error=True)
        result = self.orchestrator(long_term_adapter=adapter).run_day(DATE)

        self.assertEqual(result.status, "committed_with_warnings")
        self.assertEqual(result.long_term_status, "failed")
        self.assertIn("long_term_failed", result.warnings)
        self.assertEqual(self.bundles.bundle_ref.revision, 2)  # type: ignore[union-attr]
        self.assertEqual(self.projector.calls, 1)

    def test_long_term_retry_refreshes_projection_after_warning_clears(self) -> None:
        failed_adapter = FakeAdapter(error=True)
        orchestrator = self.orchestrator(long_term_adapter=failed_adapter)
        first = orchestrator.run_day(DATE)
        self.assertEqual(first.status, "committed_with_warnings")
        self.assertEqual(self.projector.calls, 1)

        recovered_adapter = FakeAdapter()
        orchestrator.long_term_adapter = recovered_adapter
        recovered = orchestrator.run_day(DATE, trigger="recovery")

        self.assertEqual(recovered.status, "no_change")
        self.assertEqual(recovered.warnings, ())
        self.assertEqual(recovered_adapter.calls, 1)
        self.assertEqual(self.projector.calls, 2)
        self.assertEqual(self.bundles.bundle_ref.revision, 3)  # type: ignore[union-attr]

    def test_projection_failure_preserves_committed_bundle(self) -> None:
        projector = FakeProjector(error=True)
        result = self.orchestrator(projector=projector).run_day(DATE)

        self.assertEqual(result.status, "committed_with_warnings")
        self.assertEqual(result.projection_status, "failed")
        self.assertIn("landscape_failed", result.warnings)
        self.assertEqual(self.bundles.bundle_ref.revision, 3)  # type: ignore[union-attr]

    def test_missing_review_append_api_never_reports_review_complete(self) -> None:
        result = self.orchestrator(
            bundle_store=StoreWithoutReviewAppend(self.bundles)
        ).run_day(DATE)

        self.assertEqual(result.status, "committed_with_warnings")
        self.assertEqual(result.review_status, "failed")
        self.assertIsNone(result.review_sha256)
        self.assertIn("review_failed", result.warnings)
        self.assertIsNone(self.bundles.summary.review_sha256)
        self.assertEqual(self.renderer.write_calls, 1)
        self.assertEqual(self.bundles.review_bind_calls, 0)

    def test_crash_after_pipeline_checkpoint_recovers_without_paid_replay(self) -> None:
        crashed = False

        def fault(stage: str) -> None:
            nonlocal crashed
            if stage == "after_pipeline_checkpoint" and not crashed:
                crashed = True
                raise SimulatedCrash()

        orchestrator = self.orchestrator(fault_hook=fault)
        with self.assertRaises(SimulatedCrash):
            orchestrator.run_day(DATE)
        running = orchestrator.status(DATE)
        self.assertEqual(running.status, "running")
        self.assertEqual(running.stage, "pipeline_completed")

        recovered = orchestrator.run_day(DATE, trigger="recovery")
        self.assertEqual(recovered.status, "no_change")
        self.assertEqual(self.pipeline.provider_calls, 1)
        self.assertEqual(self.renderer.calls, 1)
        self.assertEqual(self.adapter.calls, 1)
        self.assertEqual(self.projector.calls, 1)

    def test_crash_after_review_file_reuses_file_then_appends_binding(self) -> None:
        crashed = False

        def fault(stage: str) -> None:
            nonlocal crashed
            if stage == "after_review_file" and not crashed:
                crashed = True
                raise SimulatedCrash()

        orchestrator = self.orchestrator(fault_hook=fault)
        with self.assertRaises(SimulatedCrash):
            orchestrator.run_day(DATE)
        self.assertEqual(self.renderer.write_calls, 1)
        self.assertEqual(self.bundles.review_bind_calls, 0)
        self.assertIsNone(self.bundles.summary.review_sha256)

        recovered = orchestrator.run_day(DATE, trigger="recovery")

        self.assertEqual(recovered.status, "no_change")
        self.assertEqual(recovered.review_status, "no_change")
        self.assertEqual(self.pipeline.provider_calls, 1)
        self.assertEqual(self.renderer.calls, 2)
        self.assertEqual(self.renderer.write_calls, 1)
        self.assertEqual(self.bundles.review_bind_calls, 1)
        self.assertEqual(self.bundles.summary.review_sha256, SHA_C)

    def test_crash_after_review_binding_resumes_at_long_term_gate(self) -> None:
        crashed = False

        def fault(stage: str) -> None:
            nonlocal crashed
            if stage == "after_review" and not crashed:
                crashed = True
                raise SimulatedCrash()

        orchestrator = self.orchestrator(fault_hook=fault)
        with self.assertRaises(SimulatedCrash):
            orchestrator.run_day(DATE)
        bound = orchestrator.status(DATE)
        self.assertEqual(bound.review_status, "completed")
        self.assertEqual(bound.review_sha256, SHA_C)
        self.assertTrue(bound.long_term_required)
        self.assertEqual(self.bundles.bundle_ref.revision, 2)  # type: ignore[union-attr]
        self.assertEqual(bound.bundle_ref, self.bundles.bundle_ref.to_dict())  # type: ignore[union-attr]
        self.assertEqual(self.adapter.calls, 0)

        recovered = orchestrator.run_day(DATE, trigger="recovery")

        self.assertEqual(recovered.status, "no_change")
        self.assertEqual(recovered.review_status, "completed")
        self.assertEqual(self.pipeline.provider_calls, 1)
        self.assertEqual(self.renderer.calls, 1)
        self.assertEqual(self.bundles.review_bind_calls, 1)
        self.assertEqual(self.adapter.calls, 1)
        self.assertEqual(self.bundles.bundle_ref.revision, 3)  # type: ignore[union-attr]

    def test_concurrent_same_day_serializes_and_runs_downstream_once(self) -> None:
        renderer = FakeRenderer(delay=0.05)
        orchestrator = self.orchestrator(renderer=renderer)
        barrier = threading.Barrier(2)

        def run() -> Any:
            barrier.wait(timeout=2)
            return orchestrator.run_day(DATE)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run), pool.submit(run)]
            results = [future.result(timeout=5) for future in futures]

        self.assertEqual(sorted(row.status for row in results), ["committed", "no_change"])
        self.assertEqual(self.pipeline.provider_calls, 1)
        self.assertEqual(renderer.calls, 1)
        self.assertEqual(self.adapter.calls, 1)
        self.assertEqual(self.projector.calls, 1)
        self.assertEqual(self.bundles.append_calls, 1)
        self.assertEqual(self.bundles.review_bind_calls, 1)


if __name__ == "__main__":
    unittest.main()
