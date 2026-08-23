#!/usr/bin/env python3
"""Release-blocking cross-module regressions for Cognitive Secretary V1.

These tests intentionally exercise contracts that span more than one store.
They use temporary Vaults and fake providers/runners only.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_AGENT = ROOT / "context-agent"
TESTS = ROOT / "tests"
for path in (CONTEXT_AGENT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import test_cognitive_agent_adapter_v1 as adapter_cases  # noqa: E402
import test_cognitive_bundle_store_v1 as bundle_cases  # noqa: E402
import test_cognitive_pipeline_v1 as pipeline_cases  # noqa: E402
import test_cognitive_projection_v1 as projection_cases  # noqa: E402
import test_cognitive_runtime_v1 as runtime_cases  # noqa: E402
import agent_v1 as agent_v1_module  # noqa: E402
import cognitive_bundle_store_v1 as bundle_store_module  # noqa: E402
import cognitive_runtime_v1 as runtime_module  # noqa: E402
import cognitive_store_v1 as record_store_module  # noqa: E402
from agent_v1 import (  # noqa: E402
    AgentBudget,
    AgentPreparation,
    MockPlanner,
    build_agent_messages,
    build_agent_profile,
    create_agent_request,
    enable_agent_v1,
    load_agent_request,
    make_agent_run_key,
    prepare_agent_run,
    process_agent_request,
)
from cognitive_agent_adapter_v1 import CognitiveAgentAdapter  # noqa: E402
from cognitive_actions_v1 import CognitiveActionStore  # noqa: E402
from cognitive_day_orchestrator_v1 import CognitiveDayOrchestrator  # noqa: E402
from cognitive_pipeline_v1 import CognitivePipeline  # noqa: E402
from cognitive_record_worker_v1 import CognitiveRecordWorker  # noqa: E402
from cognitive_v1 import (  # noqa: E402
    COGNITIVE_SCHEMA_VERSION,
    CognitiveUserAction,
    InterpretationReceiptRevision,
    ObjectRef,
    RelationRevision,
    ReusableMemoryRevision,
    SourceRecordRevision,
    SourceSpan,
    make_cognitive_action_id,
    make_receipt_id,
    persisted_sha256,
)
from core import ContractError, Pricing, canonical_json, sha256_bytes  # noqa: E402


NOW = pipeline_cases.NOW
LOCAL_DATE = pipeline_cases.LOCAL_DATE
DAY = pipeline_cases.DAY


class CognitiveCrossModuleP1Regressions(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="memento-cognitive-p1-regression-"
        )
        self.vault = Path(self.temporary.name) / "vault"
        self.vault.mkdir(mode=0o700)
        self.day = self.vault / DAY
        self.day.write_text(
            "---\ndate: 2026-08-18\ntype: memento-daily\n---\n\n"
            "## 10:50 · 周二 · Chrome\n\n"
            "我每次想把方案想完整再发。\n"
            "真实反馈反而来得太晚。\n\n---\n",
            encoding="utf-8",
        )
        self.day.chmod(0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def pipeline(
        self, provider: pipeline_cases.FakeProvider
    ) -> CognitivePipeline:
        pipeline = CognitivePipeline(self.vault, provider, clock=lambda: NOW)
        # Direct Pipeline calls now freeze the canonical Agent profile when no
        # explicit profile hash is supplied.  Bind the commit-time reader to
        # that same authoritative projection so these cross-module fixtures
        # exercise the public API contract rather than a synthetic ZERO head.
        pipeline.bundles.set_profile_sha256_reader(
            lambda: build_agent_profile(self.vault)["profile_sha256"]
        )
        return pipeline

    def test_agent_request_lock_rejects_symlink_before_at_most_once_boundary(
        self,
    ) -> None:
        """The per-request at-most-once lock cannot follow a symlink.

        Exact expectation: an occupied symlink at the canonical request-lock
        path raises ``ContractError(kind="evidence")`` before the protected
        request body runs.  The symlink target bytes and mode stay unchanged.
        """

        locks = self.vault / ".context-agent" / "agent-v1" / "locks"
        locks.mkdir(mode=0o700, parents=True)
        target = self.vault / "request-lock-target"
        target.write_text("do-not-touch\n", encoding="utf-8")
        target.chmod(0o644)
        request_id = "arq_" + "a" * 24
        (locks / f"{request_id}.lock").symlink_to(target)

        with self.assertRaises(ContractError) as raised:
            with agent_v1_module._request_lock(self.vault, request_id):
                self.fail("unsafe request lock entered")

        self.assertEqual(raised.exception.kind, "evidence")
        self.assertEqual(target.read_bytes(), b"do-not-touch\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)

    def test_agent_profile_lock_rejects_symlink_before_commit_cas_boundary(
        self,
    ) -> None:
        """The Agent writer and Daily committer must lock the same safe inode.

        Exact expectation: an occupied symlink at ``profile.lock`` raises
        ``ContractError(kind="evidence")`` before any profile write can enter
        its critical section.  The external target is not chmod'ed or changed.
        """

        locks = self.vault / ".context-agent" / "agent-v1" / "locks"
        locks.mkdir(mode=0o700, parents=True)
        target = self.vault / "profile-lock-target"
        target.write_text("do-not-touch\n", encoding="utf-8")
        target.chmod(0o644)
        (locks / "profile.lock").symlink_to(target)

        with self.assertRaises(ContractError) as raised:
            with agent_v1_module._profile_lock(self.vault):
                self.fail("unsafe profile lock entered")

        self.assertEqual(raised.exception.kind, "evidence")
        self.assertEqual(target.read_bytes(), b"do-not-touch\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)

    def test_agent_profile_lock_rejects_path_replacement_while_waiting(
        self,
    ) -> None:
        """A waiter must re-check the canonical path after acquiring flock.

        Exact expectation: replacing ``profile.lock`` after a second writer
        opened the old inode but before it acquired that inode's lock makes
        the waiter fail closed.  It never enters a critical section guarded by
        an inode different from the current canonical lock path.
        """

        locks = self.vault / ".context-agent" / "agent-v1" / "locks"
        locks.mkdir(mode=0o700, parents=True)
        lock_path = locks / "profile.lock"
        lock_path.touch(mode=0o600)
        lock_path.chmod(0o600)

        holder_entered = threading.Event()
        release_holder = threading.Event()
        waiter_at_flock = threading.Event()
        waiter_entered = threading.Event()
        waiter_errors: list[BaseException] = []

        def holder() -> None:
            with agent_v1_module._profile_lock(self.vault):
                holder_entered.set()
                release_holder.wait(timeout=5)

        holder_thread = threading.Thread(target=holder, daemon=True)
        holder_thread.start()
        self.assertTrue(holder_entered.wait(timeout=2))

        waiter_thread: threading.Thread

        def waiter() -> None:
            try:
                with agent_v1_module._profile_lock(self.vault):
                    waiter_entered.set()
            except BaseException as exc:  # captured for deterministic assertion
                waiter_errors.append(exc)

        waiter_thread = threading.Thread(target=waiter, daemon=True)
        real_flock = agent_v1_module.fcntl.flock

        def observed_flock(descriptor: int, operation: int):
            if (
                threading.current_thread() is waiter_thread
                and operation == agent_v1_module.fcntl.LOCK_EX
            ):
                waiter_at_flock.set()
            return real_flock(descriptor, operation)

        try:
            with mock.patch.object(
                agent_v1_module.fcntl,
                "flock",
                side_effect=observed_flock,
            ):
                waiter_thread.start()
                self.assertTrue(waiter_at_flock.wait(timeout=2))
                lock_path.rename(locks / "profile.lock.replaced")
                lock_path.touch(mode=0o600)
                lock_path.chmod(0o600)
                release_holder.set()
                waiter_thread.join(timeout=2)
                holder_thread.join(timeout=2)
        finally:
            release_holder.set()
            waiter_thread.join(timeout=2)
            holder_thread.join(timeout=2)

        self.assertFalse(waiter_thread.is_alive())
        self.assertFalse(holder_thread.is_alive())
        self.assertFalse(waiter_entered.is_set())
        self.assertEqual(len(waiter_errors), 1)
        self.assertIsInstance(waiter_errors[0], ContractError)
        self.assertEqual(waiter_errors[0].kind, "evidence")

    def _assert_waiting_lock_rejects_path_replacement(
        self,
        *,
        lock_factory,
        lock_path: Path,
        fcntl_module,
    ) -> None:
        """Exercise one lock's post-wait canonical-inode invariant."""

        holder_entered = threading.Event()
        release_holder = threading.Event()
        waiter_at_flock = threading.Event()
        waiter_entered = threading.Event()
        waiter_errors: list[BaseException] = []

        def holder() -> None:
            with lock_factory():
                holder_entered.set()
                release_holder.wait(timeout=5)

        holder_thread = threading.Thread(target=holder, daemon=True)
        holder_thread.start()
        self.assertTrue(holder_entered.wait(timeout=2))
        self.assertTrue(lock_path.is_file())

        waiter_thread: threading.Thread

        def waiter() -> None:
            try:
                with lock_factory():
                    waiter_entered.set()
            except BaseException as exc:  # captured for deterministic assertion
                waiter_errors.append(exc)

        waiter_thread = threading.Thread(target=waiter, daemon=True)
        real_flock = fcntl_module.flock

        def observed_flock(descriptor: int, operation: int):
            if (
                threading.current_thread() is waiter_thread
                and operation == fcntl_module.LOCK_EX
            ):
                waiter_at_flock.set()
            return real_flock(descriptor, operation)

        replaced_path = lock_path.with_name(lock_path.name + ".replaced")
        try:
            with mock.patch.object(
                fcntl_module,
                "flock",
                side_effect=observed_flock,
            ):
                waiter_thread.start()
                self.assertTrue(waiter_at_flock.wait(timeout=2))
                lock_path.rename(replaced_path)
                lock_path.touch(mode=0o600)
                lock_path.chmod(0o600)
                release_holder.set()
                waiter_thread.join(timeout=2)
                holder_thread.join(timeout=2)
        finally:
            release_holder.set()
            waiter_thread.join(timeout=2)
            holder_thread.join(timeout=2)

        self.assertFalse(waiter_thread.is_alive())
        self.assertFalse(holder_thread.is_alive())
        self.assertFalse(waiter_entered.is_set())
        self.assertEqual(len(waiter_errors), 1)
        self.assertIsInstance(waiter_errors[0], ContractError)
        self.assertEqual(waiter_errors[0].kind, "evidence")

    def test_runtime_run_lock_rejects_path_replacement_while_waiting(
        self,
    ) -> None:
        """Runtime at-most-once locking must remain bound to one path inode.

        Exact expectation: replacing the canonical run lock after a waiter
        opened the old inode but before it acquired ``flock`` makes that
        waiter fail closed.  It cannot enter a second critical section that
        could issue another Provider call for the same durable run key.
        """

        pipeline = self.pipeline(pipeline_cases.FakeProvider())
        lock_name = "p1-runtime-path-replacement"
        lock_path = pipeline.runtime.files.locks / f"{lock_name}.lock"
        self._assert_waiting_lock_rejects_path_replacement(
            lock_factory=lambda: pipeline.runtime.files.lock(lock_name),
            lock_path=lock_path,
            fcntl_module=runtime_module.fcntl,
        )

    def test_bundle_lock_rejects_path_replacement_while_waiting(
        self,
    ) -> None:
        """Formal catalogue CAS must remain bound to one lock-path inode.

        Exact expectation: replacing ``daily-bundle.lock`` after a waiter
        opened the old inode but before it acquired ``flock`` makes that
        waiter fail closed.  Two formal writers can never enter through
        different inodes and publish competing catalogue heads.
        """

        pipeline = self.pipeline(pipeline_cases.FakeProvider())
        lock_path = pipeline.bundles.locks_dir / "daily-bundle.lock"
        self._assert_waiting_lock_rejects_path_replacement(
            lock_factory=lambda: bundle_store_module._BundleLock(
                pipeline.bundles
            ),
            lock_path=lock_path,
            fcntl_module=bundle_store_module.fcntl,
        )

    def test_bundle_profile_guard_rejects_lock_replacement_while_waiting(
        self,
    ) -> None:
        """Daily profile CAS and Agent writers must share the same inode.

        Exact expectation: replacing Agent V1's canonical ``profile.lock``
        after the daily committer opened the old inode but before it acquired
        ``flock`` makes the committer fail closed.  It cannot validate a
        profile on one lock inode while writers continue through another.
        """

        pipeline = self.pipeline(pipeline_cases.FakeProvider())
        lock_path = (
            self.vault
            / ".context-agent"
            / "agent-v1"
            / "locks"
            / "profile.lock"
        )
        self._assert_waiting_lock_rejects_path_replacement(
            lock_factory=lambda: bundle_store_module._AgentProfileLock(
                pipeline.bundles
            ),
            lock_path=lock_path,
            fcntl_module=bundle_store_module.fcntl,
        )

    def test_record_store_lock_rejects_path_replacement_while_waiting(
        self,
    ) -> None:
        """Source-record reconciliation must serialize on one visible inode.

        Exact expectation: replacing ``records.lock`` after a waiter opened
        the old inode but before it acquired ``flock`` makes that waiter fail
        closed.  Concurrent reconcilers cannot publish record-index revisions
        while believing that two different inodes are the same store lock.
        """

        pipeline = self.pipeline(pipeline_cases.FakeProvider())
        lock_path = pipeline.records.locks_dir / "records.lock"
        self._assert_waiting_lock_rejects_path_replacement(
            lock_factory=lambda: record_store_module._StoreLock(
                pipeline.records
            ),
            lock_path=lock_path,
            fcntl_module=record_store_module.fcntl,
        )

    @staticmethod
    def prepare_record(pipeline: CognitivePipeline) -> tuple[str, str]:
        pipeline.records.reconcile_day(DAY, now=NOW, timezone=NOW.tzinfo)
        head = pipeline.records.list_heads(local_date=LOCAL_DATE)[0]
        evidence = pipeline.runtime.materialize_record_evidence(head["record_id"])
        return head["record_id"], evidence[0]["ref_id"]

    def commit_one_memory(
        self,
    ) -> tuple[CognitivePipeline, pipeline_cases.DayPipelineResult]:
        provider = pipeline_cases.FakeProvider()
        pipeline = self.pipeline(provider)
        _, evidence_ref = self.prepare_record(pipeline)
        provider.replies.extend(
            [
                pipeline_cases.record_proposal(
                    evidence_ref, "评审前先定义最早可验证部分。"
                ),
                pipeline_cases.daily_proposal(
                    evidence_ref, "评审前先定义最早可验证部分。"
                ),
            ]
        )
        result = pipeline.run_day(LOCAL_DATE)
        self.assertEqual(result.status, "committed")
        self.assertEqual(provider.calls, 2)
        self.assertEqual(len(pipeline.bundles.list_active_memories()), 1)
        return pipeline, result

    def test_commit_rejects_user_action_watermark_changed_after_model_result(self) -> None:
        """A pre-commit user action must make the frozen daily input stale.

        Exact expectation: commit raises ``ContractError(kind="stale")`` and
        publishes no bundle or formal object.  The immutable action itself is
        retained for the next reconciliation.
        """

        provider = pipeline_cases.FakeProvider()
        pipeline = self.pipeline(provider)
        _, evidence_ref = self.prepare_record(pipeline)
        provider.replies.extend(
            [
                pipeline_cases.record_proposal(
                    evidence_ref, "评审前先定义最早可验证部分。"
                ),
                pipeline_cases.daily_proposal(
                    evidence_ref, "评审前先定义最早可验证部分。"
                ),
            ]
        )

        original_commit = pipeline.bundles.commit_day_bundle

        def submit_terminal_action_before_commit(**kwargs):
            receipt_ref = kwargs["receipt_refs"][0]
            action = CognitiveUserAction(
                COGNITIVE_SCHEMA_VERSION,
                "memento_cognitive_user_action",
                make_cognitive_action_id("p1-watermark-race"),
                "2026-08-18T21:00:05+08:00",
                "original_only",
                receipt_ref,
                None,
            )
            pipeline.actions.submit_action(action)
            return original_commit(**kwargs)

        pipeline.bundles.commit_day_bundle = submit_terminal_action_before_commit

        with self.assertRaises(ContractError) as raised:
            pipeline.run_day(LOCAL_DATE)
        self.assertEqual(raised.exception.kind, "stale")
        self.assertIsNone(pipeline.bundles.load_day_bundle_ref(LOCAL_DATE))
        self.assertEqual(pipeline.bundles.list_active_memories(), ())
        self.assertEqual(pipeline.bundles.list_active_relations(), ())

    def test_day_commit_holds_action_watermark_guard_through_catalogue_switch(
        self,
    ) -> None:
        """The final watermark read and catalogue publication are atomic.

        Exact expectation: a user action submitted concurrently with the
        catalogue switch cannot become visible until the old-watermark bundle
        has been atomically published.  The commit succeeds from its frozen
        input and the immutable action remains available for the next
        reconciliation; it never slips into the middle of publication.
        """

        provider = pipeline_cases.FakeProvider()
        pipeline = self.pipeline(provider)
        _, evidence_ref = self.prepare_record(pipeline)
        provider.replies.extend(
            [
                pipeline_cases.record_proposal(
                    evidence_ref, "评审前先定义最早可验证部分。"
                ),
                pipeline_cases.daily_proposal(
                    evidence_ref, "评审前先定义最早可验证部分。"
                ),
            ]
        )
        _, frozen_watermark = pipeline.actions.action_watermark()
        original_replace = pipeline.bundles._safe_write_replace
        submit_started = threading.Event()
        submit_finished = threading.Event()
        submit_errors: list[BaseException] = []
        submitted_action_id = make_cognitive_action_id(
            "p1-catalogue-switch-action-serialization"
        )
        intercepted = False
        submitter: threading.Thread | None = None

        def replace_while_submitter_waits(path: Path, value):
            nonlocal intercepted, submitter
            if path == pipeline.bundles.catalog_path and not intercepted:
                intercepted = True
                receipt_path = next(
                    pipeline.runtime.files.receipts.glob("*.r000001.json")
                )
                receipt_value = json.loads(
                    receipt_path.read_text(encoding="utf-8")
                )
                receipt_ref = ObjectRef(
                    "interpretation_receipt",
                    receipt_value["receipt_id"],
                    1,
                    persisted_sha256(receipt_value),
                )
                action = CognitiveUserAction(
                    COGNITIVE_SCHEMA_VERSION,
                    "memento_cognitive_user_action",
                    submitted_action_id,
                    "2026-08-18T21:00:05+08:00",
                    "original_only",
                    receipt_ref,
                    None,
                )

                def submit() -> None:
                    submit_started.set()
                    try:
                        pipeline.actions.submit_action(action)
                    except BaseException as exc:  # pragma: no cover - asserted below
                        submit_errors.append(exc)
                    finally:
                        submit_finished.set()

                submitter = threading.Thread(target=submit, daemon=True)
                submitter.start()
                self.assertTrue(submit_started.wait(timeout=1))
                # The commit holds the canonical action lock at this exact
                # publication boundary, so the submitter must still wait.
                self.assertFalse(submit_finished.wait(timeout=0.05))
            return original_replace(path, value)

        pipeline.bundles._safe_write_replace = replace_while_submitter_waits
        result = pipeline.run_day(LOCAL_DATE)

        self.assertEqual(result.status, "committed")
        self.assertTrue(intercepted)
        self.assertIsNotNone(submitter)
        submitter.join(timeout=1)
        self.assertFalse(submitter.is_alive())
        self.assertEqual(submit_errors, [])
        self.assertTrue(submit_finished.is_set())
        bundle_ref = pipeline.bundles.load_day_bundle_ref(LOCAL_DATE)
        self.assertIsNotNone(bundle_ref)
        manifest = pipeline.bundles.load_day_manifest(LOCAL_DATE)
        self.assertEqual(
            manifest["input_hashes"]["user_action_watermark_sha256"],
            frozen_watermark,
        )
        action_refs, current_watermark = pipeline.actions.action_watermark()
        self.assertIn(submitted_action_id, {row.action_id for row in action_refs})
        self.assertNotEqual(current_watermark, frozen_watermark)

    def test_day_commit_rejects_profile_changed_after_model_result(self) -> None:
        """Daily commit must CAS the frozen long-term profile at visibility.

        Exact expectation: if the current Agent profile changes after the
        Daily Integrator result but before the catalogue switch, the workflow
        reports a finite error and publishes no daily bundle or formal object.
        """

        provider = pipeline_cases.FakeProvider()
        pipeline = self.pipeline(provider)
        _, evidence_ref = self.prepare_record(pipeline)
        provider.replies.extend(
            [
                pipeline_cases.record_proposal(
                    evidence_ref, "评审前先定义最早可验证部分。"
                ),
                pipeline_cases.daily_proposal(
                    evidence_ref, "评审前先定义最早可验证部分。"
                ),
            ]
        )
        profile = {"sha": "a" * 64}

        def profile_loader(_: Path):
            return {"profile_sha256": profile["sha"], "memories": []}

        original_commit = pipeline.bundles.commit_day_bundle

        def change_profile_before_commit(**kwargs):
            profile["sha"] = "b" * 64
            return original_commit(**kwargs)

        pipeline.bundles.commit_day_bundle = change_profile_before_commit

        class NoMaterialAdapter:
            def process(self, **kwargs):
                return SimpleNamespace(
                    status="no_material",
                    material_sha256="c" * 64,
                    agent_result_ref=None,
                    warning=None,
                )

        class AnyProfileProjector:
            def publish(self, **kwargs):
                return SimpleNamespace(
                    landscape=SimpleNamespace(
                        snapshot_id="lnd_" + "d" * 24,
                        sha256="e" * 64,
                    ),
                    home=SimpleNamespace(sha256="f" * 64),
                )

        orchestrator = CognitiveDayOrchestrator(
            self.vault,
            pipeline=pipeline,
            renderer=__import__(
                "test_cognitive_day_orchestrator_v1"
            ).FakeRenderer(),
            long_term_adapter=NoMaterialAdapter(),
            projector=AnyProfileProjector(),
            bundle_store=pipeline.bundles,
            profile_loader=profile_loader,
            clock=lambda: NOW,
        )
        result = orchestrator.run_day(LOCAL_DATE)

        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_kind, "pipeline_failed")
        self.assertIsNone(pipeline.bundles.load_day_bundle_ref(LOCAL_DATE))
        self.assertEqual(pipeline.bundles.list_active_memories(), ())
        self.assertEqual(pipeline.bundles.list_active_relations(), ())

    def test_day_commit_holds_profile_guard_through_catalogue_switch(self) -> None:
        """The final profile read and formal publication are atomic.

        Exact expectation: if the Agent profile advances after the commit-time
        reader has obtained the frozen SHA but before the daily catalogue is
        switched, the workflow becomes stale and publishes no daily bundle or
        formal head.
        """

        provider = pipeline_cases.FakeProvider()
        pipeline = self.pipeline(provider)
        _, evidence_ref = self.prepare_record(pipeline)
        provider.replies.extend(
            [
                pipeline_cases.record_proposal(
                    evidence_ref, "评审前先定义最早可验证部分。"
                ),
                pipeline_cases.daily_proposal(
                    evidence_ref, "评审前先定义最早可验证部分。"
                ),
            ]
        )
        profile = {"sha": "a" * 64, "calls": 0}

        def profile_loader(_: Path):
            old_sha = profile["sha"]
            profile["calls"] += 1
            if profile["calls"] == 2:
                # Simulate Agent V1 committing a new profile immediately
                # after this reader obtained the prior SHA.
                profile["sha"] = "b" * 64
            return {"profile_sha256": old_sha, "memories": []}

        class NoMaterialAdapter:
            def process(self, **kwargs):
                return SimpleNamespace(
                    status="no_material",
                    material_sha256="c" * 64,
                    agent_result_ref=None,
                    warning=None,
                )

        class AnyProfileProjector:
            def publish(self, **kwargs):
                return SimpleNamespace(
                    landscape=SimpleNamespace(
                        snapshot_id="lnd_" + "d" * 24,
                        sha256="e" * 64,
                    ),
                    home=SimpleNamespace(sha256="f" * 64),
                )

        orchestrator = CognitiveDayOrchestrator(
            self.vault,
            pipeline=pipeline,
            renderer=__import__(
                "test_cognitive_day_orchestrator_v1"
            ).FakeRenderer(),
            long_term_adapter=NoMaterialAdapter(),
            projector=AnyProfileProjector(),
            bundle_store=pipeline.bundles,
            profile_loader=profile_loader,
            clock=lambda: NOW,
        )

        result = orchestrator.run_day(LOCAL_DATE)

        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_kind, "pipeline_failed")
        self.assertEqual(profile["sha"], "b" * 64)
        self.assertIsNone(pipeline.bundles.load_day_bundle_ref(LOCAL_DATE))
        self.assertEqual(pipeline.bundles.list_active_memories(), ())
        self.assertEqual(pipeline.bundles.list_active_relations(), ())

    def test_receipt_commit_rejects_user_action_submitted_after_model_result(
        self,
    ) -> None:
        """Per-record commit must CAS its frozen feedback watermark.

        Exact expectation: when the user submits ``original_only`` against the
        current receipt after the Provider result but before a source-change
        interpretation appends its next receipt revision, the interpretation
        becomes stale and appends no AI revision.  Reconciling the already
        durable user action then applies it to the unchanged base revision.
        """

        case = runtime_cases.RuntimeCase(
            methodName="test_source_cas_stales_result_before_receipt"
        )
        case.setUp()
        try:
            provider = runtime_cases.FakeProvider([])
            runtime = case.runtime(provider)
            first_evidence_ref = case.evidence(runtime)[0]["ref_id"]
            provider.replies.append(
                runtime_cases.completion(
                    runtime_cases.record_proposal(first_evidence_ref)
                )
            )
            first_request = runtime.create_interpretation_request(case.record_id)
            first = runtime.run_interpretation(first_request["id"])
            self.assertEqual(first["status"], "completed")
            first_receipt_ref = ObjectRef.from_dict(first["run"]["receipt_ref"])

            case.day.write_text(
                case.day.read_text(encoding="utf-8").replace(
                    "真实反馈反而来得太晚。",
                    "真实反馈反而来得太晚，下次先发可验证草稿。",
                ),
                encoding="utf-8",
            )
            case.day.chmod(0o600)
            case.store.reconcile_day(runtime_cases.DAY, now=runtime_cases.NOW)
            second_evidence_ref = case.evidence(runtime)[0]["ref_id"]

            actions = CognitiveActionStore(
                case.vault, state_root=runtime.files.root
            )
            _, frozen_watermark = actions.action_watermark()
            provider.replies.append(
                runtime_cases.completion(
                    runtime_cases.record_proposal(second_evidence_ref)
                )
            )
            second_request = runtime.create_interpretation_request(
                case.record_id,
                trigger="source_changed",
                feedback_watermark_sha256=frozen_watermark,
            )
            original_commit = runtime._commit_receipt

            def submit_terminal_action_before_receipt(**kwargs):
                actions.submit_action(
                    CognitiveUserAction(
                        COGNITIVE_SCHEMA_VERSION,
                        "memento_cognitive_user_action",
                        make_cognitive_action_id("p1-receipt-watermark-race"),
                        "2026-08-18T12:00:01+08:00",
                        "original_only",
                        first_receipt_ref,
                        None,
                    )
                )
                return original_commit(**kwargs)

            runtime._commit_receipt = submit_terminal_action_before_receipt
            second = runtime.run_interpretation(second_request["id"])

            self.assertEqual(second["status"], "stale")
            receipt_files = sorted(
                runtime.files.receipts.glob(
                    f"{first_receipt_ref.id}.r*.json"
                )
            )
            self.assertEqual(len(receipt_files), 1)
            report = actions.reconcile(
                receipt_store=actions,
                now=runtime_cases.NOW,
            )
            self.assertEqual((report.applied, report.conflict), (1, 0))
            self.assertEqual(
                actions.load_receipt_head(first_receipt_ref.id).status,
                "original_only",
            )
            self.assertEqual(provider.calls, 2)
        finally:
            case.tearDown()

    def test_public_runtime_default_request_cannot_bypass_user_action_watermark(
        self,
    ) -> None:
        """The exported Runtime API must freeze the current action watermark.

        Exact expectation: a request created before a user edits the current
        receipt becomes stale when its Provider result later tries to commit.
        Omitting the optional watermark argument must not turn off the guard;
        the user-edited receipt stays the current head and no AI r3 is added.
        """

        case = runtime_cases.RuntimeCase(
            methodName="test_source_cas_stales_result_before_receipt"
        )
        case.setUp()
        try:
            provider = runtime_cases.FakeProvider([])
            runtime = case.runtime(provider)
            evidence_ref = case.evidence(runtime)[0]["ref_id"]
            provider.replies.append(
                runtime_cases.completion(
                    runtime_cases.record_proposal(evidence_ref)
                )
            )
            first_request = runtime.create_interpretation_request(case.record_id)
            first = runtime.run_interpretation(first_request["id"])
            self.assertEqual(first["status"], "completed")
            first_receipt_ref = ObjectRef.from_dict(first["run"]["receipt_ref"])

            # Freeze the second request before the user's edit, using the
            # exported method exactly as its default signature permits.
            second_request = runtime.create_interpretation_request(
                case.record_id,
                trigger="retry",
                request_nonce="public-default-watermark-race",
            )

            actions = CognitiveActionStore(
                case.vault, state_root=runtime.files.root
            )
            user_summary = "我更在意尽早看到真实反馈。"
            action = CognitiveUserAction(
                COGNITIVE_SCHEMA_VERSION,
                "memento_cognitive_user_action",
                make_cognitive_action_id("p1-public-runtime-zero-watermark"),
                "2026-08-18T12:00:01+08:00",
                "edit_receipt",
                first_receipt_ref,
                {
                    "summary": user_summary,
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
            actions.submit_action(action)
            report = actions.reconcile(receipt_store=actions, now=runtime_cases.NOW)
            self.assertEqual((report.applied, report.conflict), (1, 0))
            edited = actions.load_receipt_head(first_receipt_ref.id)
            self.assertEqual((edited.revision, edited.operation), (2, "user_edit"))
            self.assertEqual(edited.summary, user_summary)

            provider.replies.append(
                runtime_cases.completion(
                    runtime_cases.record_proposal(evidence_ref)
                )
            )
            second = runtime.run_interpretation(second_request["id"])

            self.assertEqual(second["status"], "stale")
            head = actions.load_receipt_head(first_receipt_ref.id)
            self.assertEqual((head.revision, head.operation), (2, "user_edit"))
            self.assertEqual(head.summary, user_summary)
            self.assertEqual(
                len(
                    tuple(
                        runtime.files.receipts.glob(
                            f"{first_receipt_ref.id}.r*.json"
                        )
                    )
                ),
                2,
            )
            self.assertEqual(provider.calls, 1)
        finally:
            case.tearDown()

    def test_original_only_retracts_single_source_formal_memory_but_keeps_history(
        self,
    ) -> None:
        """A terminal receipt removes solely-derived material from active heads.

        Exact expectation: the prior immutable formal revision remains on
        disk for audit, while it disappears from active memory/relation reads
        without another provider call.
        """

        pipeline, first = self.commit_one_memory()
        memory_ref = first.commit_result.memory_refs[0]  # type: ignore[union-attr]
        revision_one = (
            pipeline.bundles.memory_dir
            / f"{memory_ref.id}.r{memory_ref.revision:06d}.json"
        )
        self.assertTrue(revision_one.exists())

        receipt_ref = first.receipt_refs[0]
        action = CognitiveUserAction(
            COGNITIVE_SCHEMA_VERSION,
            "memento_cognitive_user_action",
            make_cognitive_action_id("p1-original-only-retract"),
            "2026-08-18T21:01:00+08:00",
            "original_only",
            receipt_ref,
            None,
        )
        pipeline.actions.submit_action(action)

        replay_provider = pipeline_cases.FakeProvider()
        self.pipeline(replay_provider).run_day(LOCAL_DATE)

        head = pipeline.actions.load_receipt_head(receipt_ref.id)
        self.assertEqual(head.status, "original_only")
        self.assertEqual(replay_provider.calls, 0)
        self.assertEqual(pipeline.bundles.list_active_memories(), ())
        self.assertEqual(pipeline.bundles.list_active_relations(), ())
        self.assertTrue(revision_one.exists())

    def test_tombstoned_receipt_retracts_single_source_formal_memory_but_keeps_history(
        self,
    ) -> None:
        """Receipt tombstone has the same active-set boundary as original_only."""

        pipeline, first = self.commit_one_memory()
        memory_ref = first.commit_result.memory_refs[0]  # type: ignore[union-attr]
        revision_one = (
            pipeline.bundles.memory_dir
            / f"{memory_ref.id}.r{memory_ref.revision:06d}.json"
        )
        receipt_ref = first.receipt_refs[0]
        receipt = pipeline.actions.load_receipt_head(receipt_ref.id)
        tombstone = dataclasses.replace(
            receipt,
            revision=receipt.revision + 1,
            status="tombstone",
            operation="tombstone",
            created_at="2026-08-18T21:01:00+08:00",
            user_action_id=make_cognitive_action_id("p1-receipt-tombstone"),
            summary=None,
            facets={},
            memory_candidates=(),
            relation_candidates=(),
            source_spans=(),
            previous_revision_sha256=receipt.sha256,
        )
        pipeline.actions.commit_user_receipt_revision(
            tombstone, expected_ref=receipt_ref
        )

        replay_provider = pipeline_cases.FakeProvider()
        self.pipeline(replay_provider).run_day(LOCAL_DATE)

        self.assertEqual(replay_provider.calls, 0)
        self.assertEqual(pipeline.bundles.list_active_memories(), ())
        self.assertEqual(pipeline.bundles.list_active_relations(), ())
        self.assertTrue(revision_one.exists())

    def test_record_worker_immediately_retracts_terminal_receipt_derivatives(
        self,
    ) -> None:
        """The per-record public workflow must honor terminal priority fully.

        Exact expectation: when RecordWorker reconciles an ``original_only``
        action, every active formal memory/relation depending on that receipt
        is removed from the catalogue in the same bounded run.  Immutable
        prior revisions remain for audit and no provider call is needed.
        """

        pipeline, first = self.commit_one_memory()
        memory_ref = first.commit_result.memory_refs[0]  # type: ignore[union-attr]
        revision_one = (
            pipeline.bundles.memory_dir
            / f"{memory_ref.id}.r{memory_ref.revision:06d}.json"
        )
        receipt_ref = first.receipt_refs[0]
        pipeline.actions.submit_action(
            CognitiveUserAction(
                COGNITIVE_SCHEMA_VERSION,
                "memento_cognitive_user_action",
                make_cognitive_action_id("p1-worker-terminal-cascade"),
                "2026-08-18T21:01:00+08:00",
                "original_only",
                receipt_ref,
                None,
            )
        )
        provider_calls = pipeline.runtime.provider.calls
        worker = CognitiveRecordWorker(
            self.vault,
            runtime=pipeline.runtime,
            record_store=pipeline.records,
            action_store=pipeline.actions,
            formal_store=pipeline.bundles,
            clock=lambda: NOW,
        )

        result = worker.run(local_date=LOCAL_DATE, source_file=DAY)

        self.assertEqual(result.items[0].outcome, "original_only")
        self.assertEqual(pipeline.runtime.provider.calls, provider_calls)
        self.assertEqual(pipeline.bundles.list_active_memories(), ())
        self.assertEqual(pipeline.bundles.list_active_relations(), ())
        self.assertTrue(revision_one.is_file())

    def test_invalid_provider_content_is_never_persisted_verbatim(self) -> None:
        """Untrusted Provider prose must not become a durable secret sidecar.

        Exact expectation: content that fails the strict action contract may
        terminate the request as a finite schema error, but neither the raw
        completion nor a copy of its sensitive text is written anywhere under
        the Cognitive state root.  Crash recovery may retain a canonical,
        already-validated action or a content hash; it cannot retain arbitrary
        model prose, hidden reasoning, secrets, or source-text echoes.
        """

        case = runtime_cases.RuntimeCase(
            methodName="test_invalid_json_and_forged_ref_fail_closed"
        )
        case.setUp()
        try:
            sensitive = (
                "API_KEY=SENSITIVE_PLACEHOLDER_DO_NOT_PERSIST\n"
                "原始记录：私人地址 123"
            )
            provider = runtime_cases.FakeProvider(
                [
                    runtime_cases.CompletionResult(
                        sensitive,
                        runtime_cases.USAGE,
                        "sensitive-invalid-json",
                        "deepseek-v4-pro",
                    )
                ]
            )
            runtime = case.runtime(provider)
            request = runtime.create_interpretation_request(case.record_id)

            result = runtime.run_interpretation(request["id"])

            self.assertEqual(result["status"], "error")
            self.assertEqual(provider.calls, 1)
            persisted = b"\n".join(
                path.read_bytes()
                for path in sorted(runtime.files.root.rglob("*"))
                if path.is_file()
            )
            for marker in (
                b"SENSITIVE_PLACEHOLDER_DO_NOT_PERSIST",
                "私人地址 123".encode("utf-8"),
            ):
                self.assertNotIn(
                    marker,
                    persisted,
                    "unvalidated Provider content was persisted verbatim",
                )
        finally:
            case.tearDown()

    def test_provider_metadata_is_allowlisted_before_any_persistence(self) -> None:
        """Provider metadata cannot carry arbitrary text into durable state.

        Exact expectation: the recovery sidecar and usage audit may retain
        normalized token counts, the locally configured model, and a bounded
        opaque request identifier or its hash. Unknown usage fields and
        arbitrary Provider-returned model/request strings are never persisted
        verbatim, even when the action body itself is valid.
        """

        case = runtime_cases.RuntimeCase(
            methodName="test_record_finish_writes_no_receipt"
        )
        case.setUp()
        try:
            usage_marker = "SENSITIVE_PROVIDER_USAGE_METADATA"
            request_marker = "SENSITIVE_PROVIDER_REQUEST_METADATA"
            model_marker = "SENSITIVE_PROVIDER_MODEL_METADATA"
            raw_usage = dict(runtime_cases.USAGE)
            raw_usage["provider_debug"] = usage_marker
            raw_usage["completion_tokens_details"] = {
                "reasoning_tokens": 0,
                "provider_trace": usage_marker,
            }
            provider = runtime_cases.FakeProvider(
                [
                    runtime_cases.CompletionResult(
                        json.dumps(
                            runtime_cases.finish_record(), ensure_ascii=False
                        ),
                        raw_usage,
                        request_marker,
                        model_marker,
                    )
                ]
            )
            runtime = case.runtime(provider)
            request = runtime.create_interpretation_request(case.record_id)

            result = runtime.run_interpretation(request["id"])

            self.assertEqual(result["status"], "no_candidate")
            self.assertEqual(provider.calls, 1)
            persisted = b"\n".join(
                path.read_bytes()
                for path in sorted((case.vault / ".context-agent").rglob("*"))
                if path.is_file()
            )
            for marker in (usage_marker, request_marker, model_marker):
                self.assertNotIn(
                    marker.encode("utf-8"),
                    persisted,
                    "unvalidated Provider metadata was persisted verbatim",
                )
        finally:
            case.tearDown()

    def test_partial_usage_remains_unknown_in_runtime_recovery_sidecar(self) -> None:
        """A partial Provider usage object cannot masquerade as complete.

        Exact expectation: when any required token count is absent, the
        durable completion sidecar records ``usage_missing=true`` and keeps
        usage unknown instead of synthesizing zero completion/reasoning/total
        counts that could understate cost.
        """

        case = runtime_cases.RuntimeCase(
            methodName="test_record_finish_writes_no_receipt"
        )
        case.setUp()
        try:
            provider = runtime_cases.FakeProvider(
                [
                    runtime_cases.CompletionResult(
                        json.dumps(
                            runtime_cases.finish_record(), ensure_ascii=False
                        ),
                        {"prompt_tokens": 10},
                        "partial-usage-request",
                        "deepseek-v4-pro",
                    )
                ]
            )
            runtime = case.runtime(provider)
            request = runtime.create_interpretation_request(case.record_id)

            result = runtime.run_interpretation(request["id"])

            self.assertEqual(result["status"], "no_candidate")
            self.assertEqual(provider.calls, 1)
            completion_path = next(
                runtime.files.interpretation_runs.glob("*.completion.json")
            )
            completion = json.loads(
                completion_path.read_text(encoding="utf-8")
            )
            self.assertTrue(completion["usage_missing"])
            self.assertIsNone(completion["usage"])
        finally:
            case.tearDown()

    def test_partial_usage_cannot_authorize_second_agent_provider_turn(
        self,
    ) -> None:
        """Incomplete billing data must stop the Agent after one paid call.

        Exact expectation: a first-turn ``search_history`` action accompanied
        only by ``prompt_tokens`` is audited as missing usage.  Agent V1
        returns ``budget_exhausted`` after that turn and never makes the
        second Provider call.
        """

        enable_agent_v1(self.vault)
        request, _ = create_agent_request(
            self.vault,
            as_of=LOCAL_DATE,
            request_id="arq_" + "b" * 24,
            created_at="2026-08-18T10:00:00+08:00",
        )

        class PartialUsagePlanner(MockPlanner):
            def __init__(self):
                super().__init__(
                    [
                        {
                            "schema_version": "1.0",
                            "action": "search_history",
                            "reason_code": "need_history_evidence",
                            "arguments": {
                                "query": "方案",
                                "date_from": None,
                                "date_to": None,
                                "limit": 5,
                            },
                        },
                        {
                            "schema_version": "1.0",
                            "action": "finish",
                            "reason_code": "insufficient_evidence",
                            "arguments": {"reason": "insufficient_evidence"},
                        },
                    ]
                )
                self.calls = 0

            def complete(self, messages):
                self.calls += 1
                result = super().complete(messages)
                result.usage = {"prompt_tokens": 10}
                result.request_id = f"partial-usage-{self.calls}"
                result.model = "deepseek-v4-pro"
                return result

        provider = PartialUsagePlanner()
        response, _ = process_agent_request(
            self.vault,
            request["id"],
            provider_client=provider,
            provider_name="deepseek",
            model="deepseek-v4-pro",
            pricing=Pricing(),
            budget=AgentBudget(),
        )

        self.assertEqual(provider.calls, 1)
        self.assertEqual(response["status"], "budget_exhausted")
        self.assertEqual(response["usage"]["model_calls"], 1)
        self.assertTrue(response["usage"]["usage_missing"])
        self.assertIsNone(response["usage"]["cost_usd"])
        self.assertEqual(response["trace"]["actions"], ["search_history"])

    def test_legacy_agent_provider_metadata_is_allowlisted_before_persistence(
        self,
    ) -> None:
        """Agent V1 must not persist arbitrary Provider-returned metadata.

        Exact expectation: the shared usage audit may retain normalized token
        counts and the locally configured provider/model.  It may retain only
        a bounded hash (or no value) for the external request identifier.
        Unknown usage fields and arbitrary Provider-returned model/request
        strings are never persisted verbatim.
        """

        enable_agent_v1(self.vault)
        request, _ = create_agent_request(
            self.vault,
            as_of=LOCAL_DATE,
            request_id="arq_" + "a" * 24,
            created_at="2026-08-18T10:00:00+08:00",
        )
        usage_marker = "SENSITIVE_AGENT_PROVIDER_USAGE_METADATA"
        request_marker = "SENSITIVE_AGENT_PROVIDER_REQUEST_METADATA"
        model_marker = "SENSITIVE_AGENT_PROVIDER_MODEL_METADATA"

        class MarkerPlanner(MockPlanner):
            def complete(self, messages):
                result = super().complete(messages)
                result.model = model_marker
                result.request_id = request_marker
                result.usage = {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                    "provider_debug": usage_marker,
                }
                return result

        process_agent_request(
            self.vault,
            request["id"],
            provider_client=MarkerPlanner(
                [
                    {
                        "schema_version": "1.0",
                        "action": "finish",
                        "reason_code": "insufficient_evidence",
                        "arguments": {"reason": "insufficient_evidence"},
                    }
                ]
            ),
            provider_name="deepseek",
            model="deepseek-v4-pro",
            pricing=Pricing(),
            budget=AgentBudget(),
        )

        persisted = b"\n".join(
            path.read_bytes()
            for path in sorted((self.vault / ".context-agent").rglob("*"))
            if path.is_file()
        )
        for marker in (usage_marker, request_marker, model_marker):
            self.assertNotIn(
                marker.encode("utf-8"),
                persisted,
                "Agent V1 persisted untrusted Provider metadata verbatim",
            )

    def test_recovery_rejects_changed_canonical_action_sidecar(self) -> None:
        """Crash recovery must authenticate the canonical persisted action.

        Exact expectation: changing a valid, durable ``propose_receipt``
        completion into a different valid ``finish`` action cannot alter the
        recovered outcome. The same request is rejected as damaged evidence,
        no receipt is created, and the Provider is not called again.
        """

        case = runtime_cases.RuntimeCase(
            methodName="test_persisted_completion_repairs_without_second_call"
        )
        case.setUp()
        try:
            provider = runtime_cases.FakeProvider([])
            runtime = case.runtime(provider)
            evidence_ref = case.evidence(runtime)[0]["ref_id"]
            provider.replies.append(
                runtime_cases.completion(
                    runtime_cases.record_proposal(evidence_ref)
                )
            )
            request = runtime.create_interpretation_request(case.record_id)
            original_apply = runtime._apply_completion

            def crash_after_sidecar(*args, **kwargs):
                raise RuntimeError("crash after durable completion")

            runtime._apply_completion = crash_after_sidecar
            with self.assertRaises(RuntimeError):
                runtime.run_interpretation(request["id"])
            self.assertEqual(provider.calls, 1)

            sidecar = next(
                runtime.files.interpretation_runs.glob("*.completion.json")
            )
            changed = json.loads(sidecar.read_text(encoding="utf-8"))
            changed["content"] = canonical_json(runtime_cases.finish_record())
            runtime.files.write_mutable(sidecar, changed)
            runtime._apply_completion = original_apply

            with self.assertRaises(ContractError) as raised:
                runtime.run_interpretation(request["id"])
            self.assertEqual(raised.exception.kind, "evidence")
            self.assertEqual(provider.calls, 1)
            self.assertEqual(
                list(runtime.files.receipts.glob("*.json")),
                [],
            )
        finally:
            case.tearDown()

    def test_terminal_receipt_cascade_is_conservative_across_multi_source_graph(
        self,
    ) -> None:
        """A terminal source cannot remain inside any active formal object.

        Exact expectation: objects using the terminal receipt are tombstoned,
        including a memory that also has another source and every relation
        that points to a retracted memory. Objects supported only by the other
        receipt stay active. All revision-one files remain for audit.
        """

        case = bundle_cases.CommitTests(
            methodName="test_atomic_success_multiple_objects_and_candidate_isolation"
        )
        case.setUp()
        try:
            second_source_id = "rec_" + "2" * 24
            second_quote = "同时保留一条独立的支持记录。"
            second_file = bundle_cases.DAY_FILE
            second_path = case.vault / second_file
            second_path.write_bytes(
                second_path.read_bytes()
                + f"\n## 11:00 · 周二 · Chrome\n\n{second_quote}\n\n---\n".encode(
                    "utf-8"
                )
            )
            parsed_day = bundle_cases.RecordStore(
                case.vault, state_root=case.store.root
            ).parse_day(second_file)
            self.assertEqual(len(parsed_day.records), 2)
            parsed_second = parsed_day.records[1]
            second_source = SourceRecordRevision(
                schema_version=COGNITIVE_SCHEMA_VERSION,
                kind="memento_source_record_revision",
                record_id=second_source_id,
                revision=1,
                status="active",
                operation="ingest",
                created_at=NOW.isoformat(timespec="seconds"),
                captured_at="2026-08-18T11:00:00+08:00",
                local_date=LOCAL_DATE,
                source_type=parsed_second.source_type,
                source_app=parsed_second.source_app,
                source_file=second_file,
                line_start=parsed_second.line_start,
                line_end=parsed_second.line_end,
                entry_sha256=parsed_second.entry_sha256,
                source_snapshot_sha256=parsed_day.source_snapshot_sha256,
                attachments=(),
                ingest_origin="reconciler",
                previous_revision_sha256=None,
            )
            second_source_path = (
                case.store.records_dir / f"{second_source_id}.r000001.json"
            )
            second_source_sha = bundle_cases.write_json(
                second_source_path, second_source.to_dict()
            )
            second_source_ref = ObjectRef(
                "source_record", second_source_id, 1, second_source_sha
            )
            second_span = SourceSpan(
                record_id=second_source_id,
                record_revision=1,
                record_revision_sha256=second_source_sha,
                source_file=second_file,
                line_start=parsed_second.line_start + 2,
                line_end=parsed_second.line_start + 2,
                quote=second_quote,
                quote_sha256=sha256_bytes(second_quote.encode("utf-8")),
            )
            second_receipt = InterpretationReceiptRevision(
                schema_version=COGNITIVE_SCHEMA_VERSION,
                kind="memento_interpretation_receipt_revision",
                receipt_id=make_receipt_id(second_source_id),
                revision=1,
                status="ready",
                operation="interpret",
                created_at=NOW.isoformat(timespec="seconds"),
                request_id="ireq_" + "2" * 24,
                run_id="irun_" + "2" * 24,
                record_ref=second_source_ref,
                user_action_id=None,
                summary="独立支持记录。",
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
                source_spans=(second_span,),
                contract_version="record-interpreter-v1",
                feedback_watermark_sha256="2" * 64,
                previous_revision_sha256=None,
            )
            second_receipt_path = (
                case.store.receipt_dir
                / f"{second_receipt.receipt_id}.r000001.json"
            )
            second_receipt_sha = bundle_cases.write_json(
                second_receipt_path, second_receipt.to_dict()
            )
            second_receipt_ref = ObjectRef(
                "interpretation_receipt",
                second_receipt.receipt_id,
                1,
                second_receipt_sha,
            )

            first_only = case.memory("terminal-only")
            second_only = ReusableMemoryRevision.from_dict(
                {
                    **case.memory("independent-only").to_dict(),
                    "source_spans": [second_span.to_dict()],
                    "origin_receipt_refs": [second_receipt_ref.to_dict()],
                }
            )
            mixed = ReusableMemoryRevision.from_dict(
                {
                    **case.memory("mixed-sources").to_dict(),
                    "source_spans": [case.span.to_dict(), second_span.to_dict()],
                    "origin_receipt_refs": [
                        case.receipt_ref.to_dict(),
                        second_receipt_ref.to_dict(),
                    ],
                }
            )
            first_relation = case.relation(first_only, "terminal-relation")
            second_relation = RelationRevision.from_dict(
                {
                    **case.relation(second_only, "independent-relation").to_dict(),
                    "source_spans": [second_span.to_dict()],
                }
            )
            mixed_relation = RelationRevision.from_dict(
                {
                    **case.relation(mixed, "mixed-relation").to_dict(),
                    "source_spans": [
                        case.span.to_dict(),
                        second_span.to_dict(),
                    ],
                }
            )
            contaminated_relation = RelationRevision.from_dict(
                {
                    **case.relation(
                        second_only, "terminal-span-independent-endpoint"
                    ).to_dict(),
                    "source_spans": [
                        case.span.to_dict(),
                        second_span.to_dict(),
                    ],
                }
            )
            summary = case.summary()
            summary = dataclasses.replace(
                summary,
                source_refs=tuple(
                    sorted(
                        (case.source_ref, second_source_ref),
                        key=lambda ref: (ref.id, ref.revision, ref.revision_sha256),
                    )
                ),
                receipt_refs=tuple(
                    sorted(
                        (case.receipt_ref, second_receipt_ref),
                        key=lambda ref: (ref.id, ref.revision, ref.revision_sha256),
                    )
                ),
            )
            case.commit(
                summary=summary,
                memories=(first_only, second_only, mixed),
                relations=(
                    first_relation,
                    second_relation,
                    mixed_relation,
                    contaminated_relation,
                ),
                source_refs=(case.source_ref, second_source_ref),
                receipt_refs=(case.receipt_ref, second_receipt_ref),
            )

            terminal = InterpretationReceiptRevision.from_dict(
                {
                    **case.receipt.to_dict(),
                    "revision": 2,
                    "status": "original_only",
                    "operation": "original_only",
                    "created_at": "2026-08-18T21:01:00+08:00",
                    "user_action_id": make_cognitive_action_id(
                        "p1-multi-source-terminal"
                    ),
                    "summary": None,
                    "facets": {},
                    "memory_candidates": [],
                    "relation_candidates": [],
                    "source_spans": [],
                    "previous_revision_sha256": case.receipt.sha256,
                }
            )
            bundle_cases.write_json(
                case.store.receipt_dir
                / f"{terminal.receipt_id}.r{terminal.revision:06d}.json",
                terminal.to_dict(),
            )
            result = case.store.retract_terminal_receipt_derivatives((terminal,))

            self.assertEqual(result.status, "applied")
            active_memory_ids = {
                row.memory_id for row in case.store.list_active_memories()
            }
            active_relation_ids = {
                row.relation_id for row in case.store.list_active_relations()
            }
            self.assertEqual(active_memory_ids, {second_only.memory_id})
            self.assertEqual(active_relation_ids, {second_relation.relation_id})
            for row in (first_only, second_only, mixed):
                self.assertTrue(
                    (
                        case.store.memory_dir
                        / f"{row.memory_id}.r000001.json"
                    ).is_file()
                )
            for row in (
                first_relation,
                second_relation,
                mixed_relation,
                contaminated_relation,
            ):
                self.assertTrue(
                    (
                        case.store.relation_dir
                        / f"{row.relation_id}.r000001.json"
                    ).is_file()
                )
        finally:
            case.tearDown()

    def test_original_only_immediately_hides_dependent_understanding_peak(
        self,
    ) -> None:
        """A terminal record cannot remain visible as a long-term peak.

        Exact expectation: the user-facing landscape overlays the current
        ``original_only`` receipt immediately and therefore omits every active
        Agent V1 understanding, formal reusable memory, and formal relation
        containing evidence from that record.  This applies even when an
        understanding also contains another source. Immutable histories may
        remain until dependency rebuild writes tombstones or clean replacement
        revisions supported only by still-authorized sources.
        """

        case = projection_cases.ProjectionCase(
            methodName="test_original_only_terminal_receipt_is_projected_without_ai_text"
        )
        case.setUp()
        try:
            ready, _ = case.make_receipt()
            case.install_formal_graph()
            terminal, terminal_ref = case.make_receipt(
                revision=2,
                previous=ready,
                status="original_only",
                operation="original_only",
            )
            case.actions.receipts[terminal.receipt_id] = (
                terminal,
                terminal_ref,
            )
            understanding = dict(case.profile["memories"][0])
            understanding["evidence"] = [
                *understanding["evidence"],
                {
                    "file": "2026-08-17.md",
                    "line": 2,
                    "quote": "另一日仍有一条独立支持。",
                },
            ]
            case.profile = case.make_profile(memories=[understanding])

            landscape = case.publish_landscape("terminal-source-overlay")

            self.assertEqual(
                landscape.peaks,
                (),
                "understanding derived from original_only source remained visible",
            )
            self.assertEqual(
                landscape.nodes,
                (),
                "formal memory derived from original_only source remained visible",
            )
            self.assertEqual(
                landscape.edges,
                (),
                "formal relation derived from original_only source remained visible",
            )
            home = case.publisher.build_home(
                local_date=projection_cases.DATE,
                landscape=landscape,
                schedule=case.schedule,
                now=projection_cases.NOW,
                profile=case.profile,
            )
            self.assertEqual(len(home.records), 1)
            self.assertEqual(home.records[0]["status"], "original_only")
            self.assertEqual(home.records[0]["memory_refs"], [])
            self.assertEqual(home.records[0]["understanding_refs"], [])
        finally:
            case.tearDown()

    def test_review_append_operation_is_declared_by_normative_data_contract(
        self,
    ) -> None:
        """Every emitted bundle operation must be in the normative enum."""

        case = bundle_cases.ReviewBindingTests(
            methodName="test_append_review_result_inherits_complete_bundle_and_is_idempotent"
        )
        case.setUp()
        try:
            summary = case.summary()
            initial = case.commit(summary=summary, memories=(), relations=())
            review_file, review_sha = case.write_review()
            case.store.append_review_result(
                expected_bundle_ref=initial.bundle_ref,
                expected_summary_ref=initial.summary_ref,
                review_file=review_file,
                review_sha256=review_sha,
                user_supplement_sha256=None,
                now=NOW,
            )
            manifest = case.store.load_day_manifest(bundle_cases.LOCAL_DATE)
            self.assertIsNotNone(manifest)
            assert manifest is not None
            emitted_operation = manifest["operation"]

            contract = (
                ROOT / "docs" / "cognitive-secretary-mvp" / "DATA_CONTRACT.md"
            ).read_text(encoding="utf-8")
            enum_line = next(
                line
                for line in contract.splitlines()
                if line.startswith("- `operation`:")
                and "initial_commit" in line
            )
            self.assertIn(
                emitted_operation,
                enum_line,
                "runtime-emitted DailyBundle operation is absent from DATA_CONTRACT",
            )
        finally:
            case.tearDown()

    def test_agent_material_gate_excludes_terminal_record_from_source_view(
        self,
    ) -> None:
        """A valid material gate must not re-authorize terminal raw text.

        Exact expectation: another valid record may create the Agent request,
        but the request preparation and candidate prompt must omit the text of
        a same-day record whose current receipt is ``original_only``.  The
        still-authorized record remains visible, proving this is a span-level
        authorization boundary rather than a whole-day suppression.
        """

        terminal_quote = "这条记录只允许保留原文，不得进入长期判断。"
        active_quote = "另一条有效记录可以继续形成长期材料。"
        self.day.write_text(
            "---\ndate: 2026-08-18\ntype: memento-daily\n---\n\n"
            "## 10:50 · 周二 · Chrome\n\n"
            f"{terminal_quote}\n\n---\n\n"
            "## 11:10 · 周二 · Chrome\n\n"
            f"{active_quote}\n\n---\n",
            encoding="utf-8",
        )

        provider = pipeline_cases.FakeProvider()
        pipeline = self.pipeline(provider)
        pipeline.records.reconcile_day(DAY, now=NOW, timezone=NOW.tzinfo)
        heads = pipeline.records.list_heads(local_date=LOCAL_DATE)
        self.assertEqual(len(heads), 2)
        evidence_refs = {
            head["record_id"]: pipeline.runtime.materialize_record_evidence(
                head["record_id"]
            )[0]["ref_id"]
            for head in heads
        }
        terminal_head, active_head = heads
        provider.replies.extend(
            [
                pipeline_cases.record_proposal(
                    evidence_refs[terminal_head["record_id"]], terminal_quote
                ),
                pipeline_cases.record_proposal(
                    evidence_refs[active_head["record_id"]], active_quote
                ),
                pipeline_cases.daily_proposal(
                    evidence_refs[active_head["record_id"]], active_quote
                ),
            ]
        )
        first = pipeline.run_day(LOCAL_DATE)
        self.assertEqual(first.status, "committed")

        terminal_receipt_ref = next(
            ref
            for ref in first.receipt_refs
            if ref.id == make_receipt_id(terminal_head["record_id"])
        )
        action = CognitiveUserAction(
            COGNITIVE_SCHEMA_VERSION,
            "memento_cognitive_user_action",
            make_cognitive_action_id("p1-agent-terminal-source-boundary"),
            "2026-08-18T21:01:00+08:00",
            "original_only",
            terminal_receipt_ref,
            None,
        )
        pipeline.actions.submit_action(action)
        provider.replies.append(
            pipeline_cases.daily_proposal(
                evidence_refs[active_head["record_id"]], active_quote
            )
        )
        second = pipeline.run_day(LOCAL_DATE)
        self.assertEqual(second.status, "committed")
        self.assertEqual(
            pipeline.actions.load_receipt_head(terminal_receipt_ref.id).status,
            "original_only",
        )

        profile = build_agent_profile(self.vault)
        _, action_watermark = pipeline.actions.action_watermark()
        bundle_ref = second.commit_result.bundle_ref  # type: ignore[union-attr]
        manifest = pipeline.bundles.load_day_manifest(LOCAL_DATE)
        self.assertIsNotNone(manifest)
        assert manifest is not None
        adapter = CognitiveAgentAdapter(
            self.vault,
            bundle_store=pipeline.bundles,
            agent_runner=None,
            profile_loader=build_agent_profile,
            gate_checker=lambda vault: {"enabled": True, "vault": str(vault)},
            clock=lambda: NOW,
        )
        adapter_result = adapter.process(
            bundle_ref=bundle_ref,
            manifest=manifest,
            reusable_memory_heads=tuple(
                ObjectRef(
                    "reusable_memory", row.memory_id, row.revision, row.sha256
                )
                for row in pipeline.bundles.list_active_memories()
            ),
            relation_heads=tuple(
                ObjectRef(
                    "relation", row.relation_id, row.revision, row.sha256
                )
                for row in pipeline.bundles.list_active_relations()
            ),
            profile_sha256=profile["profile_sha256"],
            user_action_watermark_sha256=action_watermark,
            trigger="scheduled",
        )
        self.assertIsNotNone(adapter_result.request_id)
        assert adapter_result.request_id is not None
        request, _, request_sha = load_agent_request(
            self.vault, adapter_result.request_id
        )
        preparation = prepare_agent_run(
            self.vault, request, request_sha, maximum_chars=100_000
        )
        prompt = "\n".join(
            message["content"]
            for message in build_agent_messages(
                preparation, workflow_mode=True
            )
        )

        self.assertIn(active_quote, prompt)
        self.assertNotIn(
            terminal_quote,
            prompt,
            "original_only record leaked into Agent V1 source candidates",
        )

    def test_scheduled_request_identity_changes_with_same_day_material_gate(self) -> None:
        """Scheduled identity is stable per material gate, not merely per date.

        Exact expectation: an exact replay reuses its request, while a changed
        same-day bundle/material gate creates a distinct request and invokes
        the Agent runner once for that new gate.
        """

        case = adapter_cases.AdapterTests(
            methodName="test_no_formal_material_creates_zero_agent_requests"
        )
        case.setUp()
        try:
            runner = adapter_cases.TerminalRunner()
            adapter = case.adapter(runner)
            first = case.process(adapter, trigger="scheduled")

            second_memory = ObjectRef(
                "reusable_memory", "rmem_" + "9" * 24, 1, "8" * 64
            )
            manifest_two = case.make_manifest(
                memory_refs=(case.memory_ref, second_memory)
            )
            manifest_two.update(
                revision=2,
                operation="feedback_recompute",
                previous_revision_sha256=case.bundle_ref.revision_sha256,
            )
            ref_two = ObjectRef(
                "daily_bundle",
                "db_20260818",
                2,
                persisted_sha256(manifest_two),
            )
            case.store.manifest = manifest_two
            case.store.bundle_ref = ref_two
            case.store.memories = (case.memory_ref, second_memory)

            second = case.process(adapter, trigger="scheduled")

            self.assertEqual(first.status, "completed")
            self.assertEqual(second.status, "completed")
            self.assertNotEqual(first.material_sha256, second.material_sha256)
            self.assertNotEqual(first.request_id, second.request_id)
            self.assertTrue(second.request_created)
            self.assertTrue(second.runner_called)
            self.assertEqual(len(runner.calls), 2)
        finally:
            case.tearDown()

    def test_agent_result_is_not_accepted_after_cognitive_action_gate_changes(
        self,
    ) -> None:
        """Agent completion must still match the current Cognitive gate.

        Exact expectation: if a user action advances the committed daily
        bundle/action watermark while the Agent runner is in flight, the old
        gate is reported as a finite warning and receives no adapter result
        sidecar.  The Agent terminal audit files may remain immutable, but
        they cannot be accepted as the result of the superseded gate.
        """

        case = adapter_cases.AdapterTests(
            methodName="test_no_formal_material_creates_zero_agent_requests"
        )
        case.setUp()
        try:
            terminal = adapter_cases.TerminalRunner()

            def advance_cognitive_gate(vault: Path, request_id: str) -> None:
                terminal(vault, request_id)
                next_manifest = case.make_manifest(
                    memory_refs=(case.memory_ref,)
                )
                next_manifest.update(
                    revision=2,
                    operation="feedback_recompute",
                    previous_revision_sha256=case.bundle_ref.revision_sha256,
                    input_hashes={
                        **next_manifest["input_hashes"],
                        "user_action_watermark_sha256": "6" * 64,
                    },
                )
                case.store.manifest = next_manifest
                case.store.bundle_ref = ObjectRef(
                    "daily_bundle",
                    "db_20260818",
                    2,
                    persisted_sha256(next_manifest),
                )

            result = case.process(
                case.adapter(advance_cognitive_gate),
                action_sha="5" * 64,
            )

            self.assertEqual(result.status, "warning")
            self.assertEqual(result.warning, "long_term_failed")
            self.assertTrue(result.runner_called)
            result_dir = (
                case.state_root / "long-term-agent-adapter" / "results"
            )
            self.assertEqual(list(result_dir.glob("*.json")), [])
        finally:
            case.tearDown()

    def test_agent_run_key_binds_cognitive_authorization_scope(self) -> None:
        """Different Cognitive authorization scopes cannot share Agent work.

        Exact expectation: when the same immutable daily file and Agent
        profile are reprocessed under a new Cognitive material gate that
        authorizes an additional current receipt/record span, the Agent run
        key changes even if every legacy Agent watermark is identical.  This
        prevents an earlier terminal result from being reused without reading
        the newly authorized record.
        """

        first_ref = ObjectRef(
            "interpretation_receipt", "rcp_" + "1" * 24, 1, "1" * 64
        ).to_dict()
        second_ref = ObjectRef(
            "interpretation_receipt", "rcp_" + "2" * 24, 1, "2" * 64
        ).to_dict()
        common = dict(
            vault=self.vault,
            request={"id": "arq_" + "a" * 24},
            request_sha256="a" * 64,
            recent_paths=(self.day,),
            source_registry={DAY: "b" * 64},
            history_sha256="c" * 64,
            profile={},
            profile_sha256="d" * 64,
            feedback_items=(),
            feedback_refs=(),
            feedback_sha256="e" * 64,
            user_action_refs=(),
            user_action_sha256="f" * 64,
            cognitive_action_sha256="f" * 64,
        )
        first = AgentPreparation(
            **common,
            cognitive_authorization={
                "schema_version": 1,
                "kind": "remember_agent_cognitive_authorization",
                "request_id": "arq_" + "a" * 24,
                "material_gate_key": "ltg_" + "1" * 24,
                "material_sha256": "3" * 64,
                "user_action_watermark_sha256": "f" * 64,
                "receipt_refs": [first_ref],
            },
            cognitive_allowed_source_lines={DAY: frozenset({1, 2})},
            cognitive_receipt_refs=(first_ref,),
        )
        second = AgentPreparation(
            **common,
            cognitive_authorization={
                "schema_version": 1,
                "kind": "remember_agent_cognitive_authorization",
                "request_id": "arq_" + "b" * 24,
                "material_gate_key": "ltg_" + "2" * 24,
                "material_sha256": "4" * 64,
                "user_action_watermark_sha256": "f" * 64,
                "receipt_refs": [first_ref, second_ref],
            },
            cognitive_allowed_source_lines={DAY: frozenset({1, 2, 10, 11})},
            cognitive_receipt_refs=(first_ref, second_ref),
        )
        budget = AgentBudget()

        self.assertNotEqual(
            make_agent_run_key(
                first, provider="mock", model="mock-model", budget=budget
            ),
            make_agent_run_key(
                second, provider="mock", model="mock-model", budget=budget
            ),
            "Agent run key ignored the Cognitive authorization/material gate",
        )

    def test_future_stale_receipt_cannot_change_old_authorization_or_run_key(
        self,
    ) -> None:
        """Future receipt churn is outside an older Cognitive run snapshot.

        Exact expectation: after an 08-18 authorization is frozen, adding an
        08-19 daily file and an 08-19 receipt whose SourceRecord has already
        advanced to a newer revision leaves the Adapter refs, Agent refs,
        history watermark, selected source hashes, and run key unchanged.
        The stale future binding must not block replay of the older day.
        """

        past_record_ref = ObjectRef(
            "source_record", "rec_" + "1" * 24, 1, "2" * 64
        )
        future_receipt_record_ref = ObjectRef(
            "source_record", "rec_" + "3" * 24, 1, "4" * 64
        )
        future_current_record_ref = ObjectRef(
            "source_record", future_receipt_record_ref.id, 2, "5" * 64
        )
        past_receipt_ref = ObjectRef(
            "interpretation_receipt", "rcp_" + "6" * 24, 1, "7" * 64
        )
        future_receipt_ref = ObjectRef(
            "interpretation_receipt", "rcp_" + "8" * 24, 1, "9" * 64
        )
        past_receipt = SimpleNamespace(
            record_ref=past_record_ref,
            sha256=past_receipt_ref.revision_sha256,
        )
        future_receipt = SimpleNamespace(
            record_ref=future_receipt_record_ref,
            sha256=future_receipt_ref.revision_sha256,
        )
        action_sha = "a" * 64
        actions = adapter_cases.FakeActionStore(
            ((past_receipt, past_receipt_ref),),
            watermark_sha256=action_sha,
        )
        records = adapter_cases.FakeRecordStore(
            {
                past_record_ref.id: (
                    past_record_ref,
                    {
                        "status": "active",
                        "local_date": LOCAL_DATE,
                        "source_file": DAY,
                        "line_start": 6,
                        "line_end": 11,
                    },
                ),
                future_receipt_record_ref.id: (
                    future_current_record_ref,
                    {
                        "status": "active",
                        "local_date": "2026-08-19",
                        "source_file": "2026-08-19.md",
                        "line_start": 6,
                        "line_end": 9,
                    },
                ),
            }
        )
        case = adapter_cases.AdapterTests(
            methodName="test_empty_receipt_refs_keep_standalone_authorization_behavior"
        )
        case.setUp()
        try:
            manifest = case.make_manifest(memory_refs=(case.memory_ref,))
            manifest["receipt_refs"] = [past_receipt_ref.to_dict()]
            manifest["input_hashes"] = {
                **manifest["input_hashes"],
                "user_action_watermark_sha256": action_sha,
            }
            adapter = case.adapter(
                None,
                action_store=actions,
                record_store=records,
            )
            before_adapter_refs = adapter._cognitive_authorization_refs(
                manifest,
                expected_action_sha256=action_sha,
            )

            request_id = "arq_" + "b" * 24
            request, request_file = create_agent_request(
                self.vault,
                as_of=LOCAL_DATE,
                request_id=request_id,
                created_at=NOW.isoformat(timespec="seconds"),
            )
            agent_v1_module.persist_cognitive_authorization(
                self.vault,
                request_id=request_id,
                material_gate_key="ltg_" + "c" * 24,
                material_sha256="d" * 64,
                user_action_watermark_sha256=action_sha,
                receipt_refs=before_adapter_refs or (),
            )
            request_sha = agent_v1_module.sha256_file(request_file)
            with (
                mock.patch(
                    "cognitive_actions_v1.CognitiveActionStore",
                    return_value=actions,
                ),
                mock.patch(
                    "cognitive_store_v1.RecordStore",
                    return_value=records,
                ),
            ):
                before = prepare_agent_run(
                    self.vault,
                    request,
                    request_sha,
                    maximum_chars=100_000,
                )
                before_key = make_agent_run_key(
                    before,
                    provider="mock-agentic-workflow",
                    model="mock-model",
                    budget=AgentBudget(),
                )

                future_day = self.vault / "2026-08-19.md"
                future_day.write_text(
                    "---\ndate: 2026-08-19\ntype: memento-daily\n---\n\n"
                    "## 09:00 · 周三 · Chrome\n\n未来记录。\n\n---\n",
                    encoding="utf-8",
                )
                future_day.chmod(0o600)
                actions.heads = (
                    (past_receipt, past_receipt_ref),
                    (future_receipt, future_receipt_ref),
                )

                after_adapter_refs = adapter._cognitive_authorization_refs(
                    manifest,
                    expected_action_sha256=action_sha,
                )
                after = prepare_agent_run(
                    self.vault,
                    request,
                    request_sha,
                    maximum_chars=100_000,
                )
                after_key = make_agent_run_key(
                    after,
                    provider="mock-agentic-workflow",
                    model="mock-model",
                    budget=AgentBudget(),
                )

            expected_refs = (past_receipt_ref.to_dict(),)
            self.assertEqual(before_adapter_refs, expected_refs)
            self.assertEqual(after_adapter_refs, expected_refs)
            self.assertEqual(tuple(before.cognitive_receipt_refs), expected_refs)
            self.assertEqual(tuple(after.cognitive_receipt_refs), expected_refs)
            self.assertEqual(before.history_sha256, after.history_sha256)
            self.assertEqual(before.source_registry, after.source_registry)
            self.assertEqual(before_key, after_key)
            self.assertNotIn(
                "2026-08-19.md", after.cognitive_allowed_source_lines or {}
            )
        finally:
            case.tearDown()

    def test_adapter_recovery_rejects_changed_cognitive_authorization_sidecar(
        self,
    ) -> None:
        """Crash recovery must re-verify the immutable authorization sidecar.

        Exact expectation: after an Agent terminal is written but before the
        adapter result is committed, replacing that request's Cognitive
        authorization with a different material binding makes replay fail
        closed.  The old terminal may remain for audit, but the adapter must
        not publish it as ``recovered`` for the current Cognitive gate.
        """

        pipeline, result = self.commit_one_memory()
        manifest = pipeline.bundles.load_day_manifest(LOCAL_DATE)
        self.assertIsNotNone(manifest)
        assert manifest is not None
        bundle_ref = result.commit_result.bundle_ref  # type: ignore[union-attr]
        profile = build_agent_profile(self.vault)
        _, action_sha = pipeline.actions.action_watermark()
        runner = adapter_cases.TerminalRunner(fail_after_write=True)
        adapter = CognitiveAgentAdapter(
            self.vault,
            bundle_store=pipeline.bundles,
            agent_runner=runner,
            profile_loader=build_agent_profile,
            gate_checker=lambda vault: {"enabled": True, "vault": str(vault)},
            clock=lambda: NOW,
        )

        def process():
            return adapter.process(
                bundle_ref=bundle_ref,
                manifest=manifest,
                reusable_memory_heads=tuple(
                    ObjectRef(
                        "reusable_memory", row.memory_id, row.revision, row.sha256
                    )
                    for row in pipeline.bundles.list_active_memories()
                ),
                relation_heads=tuple(
                    ObjectRef(
                        "relation", row.relation_id, row.revision, row.sha256
                    )
                    for row in pipeline.bundles.list_active_relations()
                ),
                profile_sha256=profile["profile_sha256"],
                user_action_watermark_sha256=action_sha,
                trigger="scheduled",
            )

        first = process()
        self.assertEqual(first.status, "warning")
        self.assertIsNotNone(first.request_id)
        assert first.request_id is not None
        authorization_path = (
            self.vault
            / ".context-agent"
            / "agent-v1"
            / "cognitive-authorizations"
            / f"{first.request_id}.json"
        )
        authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
        authorization["material_sha256"] = "0" * 64
        authorization_path.write_text(
            json.dumps(authorization, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )
        authorization_path.chmod(0o600)

        second = process()

        self.assertEqual(second.status, "warning")
        self.assertNotEqual(
            second.status,
            "recovered",
            "adapter recovered a terminal after its authorization changed",
        )
        result_dir = (
            self.vault
            / ".context-agent"
            / "cognitive-secretary-v1"
            / "long-term-agent-adapter"
            / "results"
        )
        self.assertEqual(list(result_dir.glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
