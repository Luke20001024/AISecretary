#!/usr/bin/env python3

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "context-agent"
sys.path.insert(0, str(AGENT_DIR))

from agent_v1 import (  # noqa: E402
    agent_schedule_path,
    disable_agent_schedule,
    enable_agent_schedule,
    enable_agent_v1,
)
from cognitive_schedule_v1 import (  # noqa: E402
    CognitiveScheduleCore,
    DayCompletionState,
    inspect_day_completion,
)
from cognitive_pipeline_v1 import CognitivePipeline  # noqa: E402
from cognitive_runtime_v1 import CognitiveRuntime, make_object_ref_id  # noqa: E402
from core import ContractError, sha256_bytes  # noqa: E402
from cognitive_v1 import (  # noqa: E402
    ObjectRef,
    make_cognitive_action_id,
    make_receipt_id,
)
from deepseek_provider import CompletionResult  # noqa: E402


LOCAL_TZ = dt.timezone(dt.timedelta(hours=8))
USAGE = {
    "prompt_tokens": 100,
    "completion_tokens": 50,
    "total_tokens": 150,
    "prompt_tokens_details": {"cached_tokens": 0},
    "prompt_cache_hit_tokens": 0,
    "prompt_cache_miss_tokens": 30,
    "completion_tokens_details": {"reasoning_tokens": 0},
}


class QueueProvider:
    def __init__(self, replies: Sequence[Mapping[str, Any]]) -> None:
        self.replies = list(replies)
        self.calls = 0

    def complete(self, messages: Sequence[Mapping[str, str]]) -> CompletionResult:
        del messages
        self.calls += 1
        if not self.replies:
            raise AssertionError("unexpected Provider call")
        value = self.replies.pop(0)
        return CompletionResult(
            content=json.dumps(value, ensure_ascii=False),
            usage=USAGE,
            request_id=f"schedule-{self.calls}",
            model="deepseek-v4-pro",
        )


def record_proposal(evidence_ref: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "propose_receipt",
        "reason_code": "interpretation_ready",
        "arguments": {
            "summary": "先保留一个可验证的小版本。",
            "facets": {
                "content_types": ["observation"],
                "topics": ["产品设计"],
                "objects": ["方案"],
                "stance": "self_observation",
                "cognitive_state": "first_seen",
                "purposes": ["future_decision"],
            },
            "memory_candidates": [],
            "relation_candidates": [],
            "source_ref_ids": [evidence_ref],
        },
    }


def daily_proposal(evidence_ref: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "propose_daily_bundle",
        "reason_code": "bundle_ready",
        "arguments": {
            "overview": "今天形成了一条可核对记录。",
            "themes": ["产品设计"],
            "changes": [],
            "unresolved_questions": [],
            "action_clues": [],
            "memory_operations": [
                {
                    "operation": "new",
                    "target_memory_ref_id": None,
                    "statement": "先保留一个可验证的小版本。",
                    "memory_kind": "observation",
                    "topics": ["产品设计"],
                    "purposes": ["future_decision"],
                    "uncertainty": "medium",
                    "source_ref_ids": [evidence_ref],
                }
            ],
            "relation_operations": [],
            "material_change": True,
        },
    }


def finish_record() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "finish",
        "reason_code": "insufficient_signal",
        "arguments": {"reason": "insufficient_signal"},
    }


def finish_daily() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "finish",
        "reason_code": "no_change",
        "arguments": {"reason": "no_change"},
    }


def inspect_action(object_ref_id: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "inspect_memory",
        "reason_code": "need_target_context",
        "arguments": {"memory_ref_id": object_ref_id},
    }


class RecordingRunner:
    def __init__(self, *, status: str = "no_change") -> None:
        self.status = status
        self.calls: list[tuple[str, str]] = []

    def __call__(self, local_date: str, trigger: str) -> dict[str, str]:
        self.calls.append((local_date, trigger))
        return {"status": self.status, "ignored_private_field": "not exposed"}


class CognitiveScheduleV1Test(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="cognitive-schedule-")
        self.vault = Path(self.temporary.name)
        self.runner = RecordingRunner()
        self.completion = DayCompletionState(True, True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def local_time(self, day: int, hour: int, minute: int = 0) -> dt.datetime:
        return dt.datetime(2026, 8, day, hour, minute, tzinfo=LOCAL_TZ)

    def enable_all(self, *, updated_at: str = "2026-08-17T12:00:00+08:00") -> None:
        enable_agent_v1(self.vault)
        enable_agent_schedule(self.vault, updated_at=updated_at)

    def core(self, *, completion: DayCompletionState | None = None, runner=None) -> CognitiveScheduleCore:
        state = self.completion if completion is None else completion
        return CognitiveScheduleCore(
            self.vault,
            day_runner=runner or self.runner,
            completion_reader=lambda local_date: state,
        )

    def assert_finite_report(self, report: dict) -> None:
        self.assertEqual(
            set(report),
            {
                "schema_version",
                "kind",
                "status",
                "checked_at",
                "local_date",
                "trigger",
                "runner_status",
                "bundle_committed",
                "review_valid",
                "error_kind",
            },
        )
        self.assertNotIn("ignored_private_field", report)

    def test_21_due_and_complete_morning_not_due(self) -> None:
        self.enable_all()
        morning = self.core().tick(now=self.local_time(18, 20, 59))
        self.assertEqual(morning["status"], "not_due")
        self.assertEqual(morning["trigger"], "recovery")
        self.assertEqual(morning["local_date"], "2026-08-17")
        self.assertEqual(self.runner.calls, [])

        due = self.core().tick(now=self.local_time(18, 21))
        self.assertEqual(due["status"], "completed")
        self.assertEqual(due["runner_status"], "no_change")
        self.assertEqual(due["trigger"], "scheduled")
        self.assertEqual(self.runner.calls, [("2026-08-18", "scheduled")])
        self.assert_finite_report(due)

    def test_sleep_wake_before_recovery_uses_most_recent_21_slot(self) -> None:
        self.enable_all()
        wake = self.core().tick(now=self.local_time(18, 7, 30))
        self.assertEqual(wake["status"], "completed")
        self.assertEqual(wake["local_date"], "2026-08-17")
        self.assertEqual(wake["trigger"], "scheduled")
        self.assertEqual(self.runner.calls, [("2026-08-17", "scheduled")])

    def test_08_recovery_requires_bundle_and_valid_review(self) -> None:
        self.enable_all()
        cases = (
            DayCompletionState(False, False),
            DayCompletionState(False, True),
            DayCompletionState(True, False),
            DayCompletionState(True, True, False),
        )
        for state in cases:
            with self.subTest(state=state):
                runner = RecordingRunner()
                report = self.core(completion=state, runner=runner).tick(
                    now=self.local_time(18, 8)
                )
                self.assertEqual(report["status"], "completed")
                self.assertEqual(report["trigger"], "recovery")
                self.assertEqual(report["local_date"], "2026-08-17")
                self.assertEqual(runner.calls, [("2026-08-17", "recovery")])
                self.assertEqual(report["bundle_committed"], state.bundle_committed)
                self.assertEqual(report["review_valid"], state.review_valid)

        retired_terminal = DayCompletionState(True, False, True, True)
        self.assertTrue(retired_terminal.complete)
        retired_runner = RecordingRunner()
        retired = self.core(
            completion=retired_terminal,
            runner=retired_runner,
        ).tick(now=self.local_time(18, 8))
        self.assertEqual(retired["status"], "not_due")
        self.assertEqual(retired_runner.calls, [])

        complete_runner = RecordingRunner()
        complete = self.core(runner=complete_runner).tick(now=self.local_time(18, 8))
        self.assertEqual(complete["status"], "not_due")
        self.assertEqual(complete_runner.calls, [])

    def test_no_bundle_terminal_days_are_complete(self) -> None:
        empty = inspect_day_completion(self.vault, "2026-08-16")
        self.assertFalse(empty.bundle_committed)
        self.assertTrue(empty.inputs_current)
        self.assertTrue(empty.terminal_complete)
        self.assertTrue(empty.complete)

        no_candidate_date = "2026-08-17"
        no_candidate_day = self.vault / f"{no_candidate_date}.md"
        no_candidate_day.write_text(
            "---\ndate: 2026-08-17\ntype: memento-daily\n---\n\n"
            "## 10:00 · 周一 · Chrome\n\n仅保留一条信号不足的记录。\n\n---\n",
            encoding="utf-8",
        )
        no_candidate_day.chmod(0o600)
        production_provider = QueueProvider([finish_record()])
        production_runtime = CognitiveRuntime(
            self.vault,
            production_provider,
            provider_name="deepseek",
            model="deepseek-v4-pro",
        )
        pipeline = CognitivePipeline(
            self.vault,
            runtime=production_runtime,
            record_store=production_runtime.store,
            clock=lambda: self.local_time(17, 21),
        )
        self.assertEqual(
            pipeline.run_day(no_candidate_date, trigger="scheduled").status,
            "no_candidate",
        )
        wrong_policy = inspect_day_completion(self.vault, no_candidate_date)
        self.assertFalse(wrong_policy.complete)
        current_no_candidate = inspect_day_completion(
            self.vault,
            no_candidate_date,
            runtime=production_runtime,
        )
        self.assertTrue(current_no_candidate.terminal_complete)
        self.assertTrue(current_no_candidate.complete)
        self.assertEqual(production_provider.calls, 1)

        terminal_date = "2026-08-18"
        terminal_day = self.vault / f"{terminal_date}.md"
        terminal_day.write_text(
            "---\ndate: 2026-08-18\ntype: memento-daily\n---\n\n"
            "## 10:00 · 周二 · Chrome\n\n第一条用户终态。\n\n---\n"
            "## 11:00 · 周二 · Chrome\n\n第二条用户终态。\n\n---\n",
            encoding="utf-8",
        )
        terminal_day.chmod(0o600)
        terminal_provider = QueueProvider([])
        terminal_pipeline = CognitivePipeline(
            self.vault,
            terminal_provider,
            clock=lambda: self.local_time(18, 21),
        )
        terminal_pipeline.records.reconcile_day(
            terminal_day.name,
            now=self.local_time(18, 21),
            timezone=LOCAL_TZ,
        )
        heads = terminal_pipeline.records.list_heads(local_date=terminal_date)
        _, watermark = terminal_pipeline.actions.action_watermark()
        for head in heads:
            evidence_ref = terminal_pipeline.runtime.materialize_record_evidence(
                head["record_id"]
            )[0]["ref_id"]
            terminal_provider.replies.append(record_proposal(evidence_ref))
            request = terminal_pipeline.runtime.create_interpretation_request(
                head["record_id"],
                trigger="reconcile",
                feedback_watermark_sha256=watermark,
            )
            self.assertEqual(
                terminal_pipeline.runtime.run_interpretation(request["id"])[
                    "status"
                ],
                "completed",
            )

        for index, head in enumerate(heads):
            receipt_id = make_receipt_id(head["record_id"])
            current = terminal_pipeline.actions.load_receipt_head(receipt_id)
            current_ref = terminal_pipeline.actions.load_receipt_head_ref(receipt_id)
            status = ("original_only", "tombstone")[index]
            terminal_pipeline.actions.commit_user_receipt_revision(
                dataclasses.replace(
                    current,
                    revision=current.revision + 1,
                    status=status,
                    operation=status,
                    created_at=self.local_time(18, 21, index + 1).isoformat(
                        timespec="seconds"
                    ),
                    user_action_id=make_cognitive_action_id(
                        f"schedule-terminal-{status}-{head['record_id']}"
                    ),
                    summary=None,
                    facets={},
                    memory_candidates=(),
                    relation_candidates=(),
                    source_spans=(),
                    previous_revision_sha256=current_ref.revision_sha256,
                ),
                expected_ref=current_ref,
            )
            if index == 0:
                mixed = inspect_day_completion(self.vault, terminal_date)
                self.assertTrue(mixed.inputs_current)
                self.assertFalse(mixed.terminal_complete)
                self.assertFalse(mixed.complete)
        user_terminal = inspect_day_completion(self.vault, terminal_date)
        self.assertTrue(user_terminal.terminal_complete)
        self.assertTrue(user_terminal.complete)
        self.assertIsNone(
            terminal_pipeline.bundles.load_day_bundle_ref(terminal_date)
        )

        terminal_day.write_text(
            "---\ndate: 2026-08-18\ntype: memento-daily\n---\n\n"
            "## 10:00 · 周二 · Chrome\n\n第一条用户终态后编辑。\n\n---\n"
            "## 11:00 · 周二 · Chrome\n\n第二条用户终态后编辑。\n\n---\n",
            encoding="utf-8",
        )
        terminal_day.chmod(0o600)
        terminal_pipeline.records.reconcile_day(
            terminal_day.name,
            now=self.local_time(18, 21, 3),
            timezone=LOCAL_TZ,
        )
        edited_heads = terminal_pipeline.records.list_heads(
            local_date=terminal_date
        )
        self.assertTrue(
            all(
                edited["revision"] > original["revision"]
                for edited, original in zip(edited_heads, heads)
            )
        )
        replay = terminal_pipeline.run_day(terminal_date, trigger="recovery")
        self.assertEqual(replay.status, "no_change")
        self.assertEqual(terminal_provider.calls, 2)
        edited_terminal = inspect_day_completion(self.vault, terminal_date)
        self.assertTrue(edited_terminal.inputs_current)
        self.assertTrue(edited_terminal.terminal_complete)
        self.assertTrue(edited_terminal.complete)

        self.enable_all(updated_at="2026-08-18T12:00:00+08:00")
        runner = RecordingRunner(status="no_change")
        report = CognitiveScheduleCore(
            self.vault,
            day_runner=runner,
        ).tick(now=self.local_time(19, 8))
        self.assertEqual(report["status"], "not_due")
        self.assertEqual(report["local_date"], terminal_date)
        self.assertEqual(runner.calls, [])
        self.assertEqual(terminal_provider.calls, 2)

    def test_daily_no_change_without_bundle_stops_08_recovery(self) -> None:
        local_date = "2026-08-17"
        day = self.vault / f"{local_date}.md"
        day.write_text(
            "---\ndate: 2026-08-17\ntype: memento-daily\n---\n\n"
            "## 20:50 · 周一 · Chrome\n\n"
            "这条记录已整理，但今日无需形成新的长期材料。\n\n---\n",
            encoding="utf-8",
        )
        day.chmod(0o600)
        provider = QueueProvider([])
        runtime = CognitiveRuntime(
            self.vault,
            provider,
            provider_name="deepseek",
            model="deepseek-v4-pro",
        )
        pipeline = CognitivePipeline(
            self.vault,
            runtime=runtime,
            record_store=runtime.store,
            clock=lambda: self.local_time(17, 21),
        )
        pipeline.records.reconcile_day(
            day.name,
            now=self.local_time(17, 21),
            timezone=LOCAL_TZ,
        )
        head = pipeline.records.list_heads(local_date=local_date)[0]
        evidence_ref = pipeline.runtime.materialize_record_evidence(
            head["record_id"]
        )[0]["ref_id"]
        provider.replies.extend([record_proposal(evidence_ref), finish_daily()])

        result = pipeline.run_day(local_date, trigger="scheduled")

        self.assertEqual(result.status, "no_change")
        self.assertEqual(provider.calls, 2)
        self.assertIsNone(pipeline.bundles.load_day_bundle_ref(local_date))
        completion = inspect_day_completion(
            self.vault,
            local_date,
            runtime=runtime,
        )
        self.assertTrue(completion.inputs_current)
        self.assertTrue(completion.terminal_complete)
        self.assertTrue(completion.complete)

        self.enable_all()
        runner = RecordingRunner(status="no_change")
        report = CognitiveScheduleCore(
            self.vault,
            day_runner=runner,
            completion_reader=lambda date: inspect_day_completion(
                self.vault,
                date,
                runtime=runtime,
            ),
        ).tick(now=self.local_time(18, 8))
        self.assertEqual(report["status"], "not_due")
        self.assertEqual(report["local_date"], local_date)
        self.assertEqual(runner.calls, [])
        self.assertEqual(provider.calls, 2)

    def test_current_multiturn_no_change_retires_an_old_bundle_for_recovery(self) -> None:
        local_date = "2026-08-17"
        day = self.vault / f"{local_date}.md"
        day.write_text(
            "---\ndate: 2026-08-17\ntype: memento-daily\n---\n\n"
            "## 20:40 · 周一 · Chrome\n\n"
            "先交付一个可验证的小版本。\n\n---\n",
            encoding="utf-8",
        )
        day.chmod(0o600)
        provider = QueueProvider([])
        runtime = CognitiveRuntime(
            self.vault,
            provider,
            provider_name="deepseek",
            model="deepseek-v4-pro",
        )
        pipeline = CognitivePipeline(
            self.vault,
            runtime=runtime,
            record_store=runtime.store,
            clock=lambda: self.local_time(17, 21),
        )
        pipeline.records.reconcile_day(
            day.name,
            now=self.local_time(17, 21),
            timezone=LOCAL_TZ,
        )
        first_head = pipeline.records.list_heads(local_date=local_date)[0]
        first_evidence = runtime.materialize_record_evidence(
            first_head["record_id"]
        )[0]["ref_id"]
        provider.replies.extend(
            [record_proposal(first_evidence), daily_proposal(first_evidence)]
        )
        first = pipeline.run_day(local_date, trigger="scheduled")
        self.assertEqual(first.status, "committed")
        self.assertIsNotNone(first.commit_result)
        old_bundle_ref = first.commit_result.bundle_ref
        old_manifest = pipeline.bundles.load_day_manifest(local_date)
        self.assertIsNotNone(old_manifest)
        review_path = self.vault / "Reviews" / "Daily" / f"{local_date}.md"
        review_path.parent.mkdir(mode=0o700, parents=True)
        review_bytes = b"# Daily Review\n\nBound to the retained bundle.\n"
        review_path.write_bytes(review_bytes)
        review_path.chmod(0o600)
        bound = pipeline.bundles.append_review_result(
            expected_bundle_ref=old_bundle_ref,
            expected_summary_ref=ObjectRef.from_dict(old_manifest["summary_ref"]),
            review_file=f"Reviews/Daily/{local_date}.md",
            review_sha256=sha256_bytes(review_bytes),
            user_supplement_sha256=None,
            now=self.local_time(17, 21, 1),
        )
        self.assertEqual(bound.status, "committed")
        old_bundle_ref = bound.bundle_ref
        old_manifest = pipeline.bundles.load_day_manifest(local_date)
        self.assertIsNotNone(old_manifest)

        day.write_text(
            "---\ndate: 2026-08-17\ntype: memento-daily\n---\n\n"
            "## 20:40 · 周一 · Chrome\n\n"
            "先交付一个可验证的小版本，再补一条边界。\n\n---\n",
            encoding="utf-8",
        )
        day.chmod(0o600)
        pipeline.records.reconcile_day(
            day.name,
            now=self.local_time(17, 21, 1),
            timezone=LOCAL_TZ,
        )
        edited_head = pipeline.records.list_heads(local_date=local_date)[0]
        self.assertEqual(edited_head["record_id"], first_head["record_id"])
        self.assertGreater(edited_head["revision"], first_head["revision"])
        edited_evidence = runtime.materialize_record_evidence(
            edited_head["record_id"]
        )[0]["ref_id"]
        memory = pipeline.bundles.list_active_memories()[0]
        memory_ref = ObjectRef(
            "reusable_memory",
            memory.memory_id,
            memory.revision,
            memory.sha256,
        )
        provider.replies.extend(
            [
                record_proposal(edited_evidence),
                inspect_action(make_object_ref_id(memory_ref)),
                finish_daily(),
            ]
        )
        updated = pipeline.run_day(
            local_date,
            trigger="recovery",
            inspect_memory=lambda ref: pipeline.bundles.load_memory_head(
                ref.id
            ).to_dict(),
        )
        self.assertEqual(updated.status, "no_change")
        self.assertEqual(provider.calls, 5)
        self.assertEqual(
            pipeline.bundles.load_day_bundle_ref(local_date),
            old_bundle_ref,
        )
        self.assertEqual(
            pipeline.bundles.load_day_manifest(local_date),
            old_manifest,
        )

        review_path.unlink()
        completion = inspect_day_completion(
            self.vault,
            local_date,
            runtime=runtime,
        )
        self.assertTrue(completion.inputs_current)
        self.assertFalse(completion.terminal_complete)
        self.assertFalse(completion.review_valid)
        self.assertFalse(completion.complete)

        self.enable_all()
        due_runner = RecordingRunner(status="no_change")
        due = CognitiveScheduleCore(
            self.vault,
            day_runner=due_runner,
            completion_reader=lambda date: inspect_day_completion(
                self.vault,
                date,
                runtime=runtime,
            ),
        ).tick(now=self.local_time(18, 8))
        self.assertEqual(due["status"], "completed")
        self.assertEqual(due_runner.calls, [(local_date, "recovery")])
        self.assertEqual(provider.calls, 5)

        review_path.write_bytes(review_bytes)
        review_path.chmod(0o600)
        reviewed = inspect_day_completion(
            self.vault,
            local_date,
            runtime=runtime,
        )
        self.assertTrue(reviewed.inputs_current)
        self.assertFalse(reviewed.terminal_complete)
        self.assertTrue(reviewed.review_valid)
        self.assertTrue(reviewed.complete)

        runner = RecordingRunner(status="no_change")
        report = CognitiveScheduleCore(
            self.vault,
            day_runner=runner,
            completion_reader=lambda date: inspect_day_completion(
                self.vault,
                date,
                runtime=runtime,
            ),
        ).tick(now=self.local_time(18, 8, 2))
        self.assertEqual(report["status"], "not_due")
        self.assertEqual(runner.calls, [])
        self.assertEqual(provider.calls, 5)

    def test_old_bundle_does_not_hide_late_failed_or_edited_record(self) -> None:
        local_date = "2026-08-17"
        day = self.vault / f"{local_date}.md"
        day.write_text(
            "---\ndate: 2026-08-17\ntype: memento-daily\n---\n\n"
            "## 20:50 · 周一 · Chrome\n\n"
            "先交付一个可验证的小版本。\n\n---\n",
            encoding="utf-8",
        )
        day.chmod(0o600)
        provider = QueueProvider([])
        pipeline = CognitivePipeline(
            self.vault,
            provider,
            clock=lambda: self.local_time(17, 21),
        )
        pipeline.records.reconcile_day(
            day.name,
            now=self.local_time(17, 21),
            timezone=LOCAL_TZ,
        )
        first_head = pipeline.records.list_heads(local_date=local_date)[0]
        first_evidence = pipeline.runtime.materialize_record_evidence(
            first_head["record_id"]
        )[0]["ref_id"]
        provider.replies.extend(
            [record_proposal(first_evidence), daily_proposal(first_evidence)]
        )
        committed = pipeline.run_day(local_date, trigger="scheduled")
        self.assertEqual(committed.status, "committed")
        self.assertIsNotNone(committed.commit_result)
        review_path = self.vault / "Reviews" / "Daily" / f"{local_date}.md"
        review_path.parent.mkdir(mode=0o700, parents=True)
        review_bytes = b"# Daily Review\n\nBound to the committed summary.\n"
        review_path.write_bytes(review_bytes)
        review_path.chmod(0o600)
        bound = pipeline.bundles.append_review_result(
            expected_bundle_ref=committed.commit_result.bundle_ref,
            expected_summary_ref=committed.commit_result.summary_ref,
            review_file=f"Reviews/Daily/{local_date}.md",
            review_sha256=sha256_bytes(review_bytes),
            user_supplement_sha256=None,
            now=self.local_time(17, 21, 5),
        )
        self.assertEqual(bound.status, "committed")
        complete = inspect_day_completion(self.vault, local_date)
        self.assertTrue(complete.complete)
        self.assertTrue(complete.inputs_current)

        # Before a user terminal choice exists, editing the current record
        # invalidates the ready receipt and the immutable old manifest.
        original_day = day.read_text(encoding="utf-8")
        day.write_text(
            original_day.replace(
                "先交付一个可验证的小版本。",
                "先交付一个可验证的小版本，再补充边界。",
            ),
            encoding="utf-8",
        )
        day.chmod(0o600)
        pipeline.records.reconcile_day(
            day.name,
            now=self.local_time(17, 21, 6),
            timezone=LOCAL_TZ,
        )
        stale_ready = inspect_day_completion(self.vault, local_date)
        self.assertTrue(stale_ready.bundle_committed)
        self.assertTrue(stale_ready.review_valid)
        self.assertFalse(stale_ready.inputs_current)
        self.assertFalse(stale_ready.complete)

        day.write_text(original_day, encoding="utf-8")
        day.chmod(0o600)
        pipeline.records.reconcile_day(
            day.name,
            now=self.local_time(17, 21, 7),
            timezone=LOCAL_TZ,
        )

        # A current user terminal retires this record from future daily input.
        # The immutable old manifest must not cause recovery to loop forever.
        receipt_id = make_receipt_id(first_head["record_id"])
        current_receipt = pipeline.actions.load_receipt_head(receipt_id)
        current_receipt_ref = pipeline.actions.load_receipt_head_ref(receipt_id)
        pipeline.actions.commit_user_receipt_revision(
            dataclasses.replace(
                current_receipt,
                revision=current_receipt.revision + 1,
                status="original_only",
                operation="original_only",
                created_at=self.local_time(17, 21, 6).isoformat(timespec="seconds"),
                user_action_id=make_cognitive_action_id(
                    f"schedule-retire-{first_head['record_id']}"
                ),
                summary=None,
                facets={},
                memory_candidates=(),
                relation_candidates=(),
                source_spans=(),
                previous_revision_sha256=current_receipt_ref.revision_sha256,
            ),
            expected_ref=current_receipt_ref,
        )
        retired = inspect_day_completion(self.vault, local_date)
        self.assertTrue(retired.inputs_current)
        self.assertTrue(retired.terminal_complete)
        self.assertTrue(retired.complete)

        # A late record receives a terminal schema failure after the old
        # bundle.  It has no receipt and therefore must make recovery due.
        day.write_text(
            day.read_text(encoding="utf-8")
            + "\n## 22:15 · 周一 · Chrome\n\n"
            + "这条较晚记录的整理失败了。\n\n---\n",
            encoding="utf-8",
        )
        day.chmod(0o600)
        unreconciled = inspect_day_completion(self.vault, local_date)
        self.assertTrue(unreconciled.bundle_committed)
        self.assertTrue(unreconciled.review_valid)
        self.assertFalse(unreconciled.inputs_current)
        self.assertFalse(unreconciled.complete)
        pipeline.records.reconcile_day(
            day.name,
            now=self.local_time(17, 22, 15),
            timezone=LOCAL_TZ,
        )
        heads = pipeline.records.list_heads(local_date=local_date)
        late_head = heads[-1]
        _, watermark = pipeline.actions.action_watermark()
        provider.replies.append({"invalid": "late-record-schema"})
        request = pipeline.runtime.create_interpretation_request(
            late_head["record_id"],
            trigger="reconcile",
            feedback_watermark_sha256=watermark,
        )
        failed = pipeline.runtime.run_interpretation(request["id"])
        self.assertEqual(failed["status"], "error")
        late = inspect_day_completion(self.vault, local_date)
        self.assertTrue(late.bundle_committed)
        self.assertTrue(late.review_valid)
        self.assertFalse(late.inputs_current)
        self.assertFalse(late.complete)

        self.enable_all()
        recovery_runner = RecordingRunner(status="no_receipts")
        recovery = CognitiveScheduleCore(
            self.vault,
            day_runner=recovery_runner,
        ).tick(now=self.local_time(18, 8))
        self.assertEqual(recovery["status"], "completed")
        self.assertEqual(recovery["runner_status"], "no_receipts")
        self.assertEqual(recovery_runner.calls, [(local_date, "recovery")])

        # The earlier user-terminal choice remains final after source edits;
        # it retires the old manifest row by stable record id.
        day.write_text(
            "---\ndate: 2026-08-17\ntype: memento-daily\n---\n\n"
            "## 20:50 · 周一 · Chrome\n\n"
            "先交付两个可验证的小版本。\n\n---\n",
            encoding="utf-8",
        )
        day.chmod(0o600)
        pipeline.records.reconcile_day(
            day.name,
            now=self.local_time(18, 8, 5),
            timezone=LOCAL_TZ,
        )
        edited = inspect_day_completion(self.vault, local_date)
        self.assertTrue(edited.bundle_committed)
        self.assertTrue(edited.review_valid)
        self.assertTrue(edited.inputs_current)
        self.assertTrue(edited.terminal_complete)
        self.assertTrue(edited.complete)

        # A terminal overlay cannot conceal a catalog ref whose immutable
        # manifest disappeared.  The completion reader must fail closed.
        current_bundle = pipeline.bundles.load_day_bundle_ref(local_date)
        self.assertIsNotNone(current_bundle)
        manifest_path = (
            pipeline.bundles.committed_dir
            / f"day_{current_bundle.id[3:]}.r{current_bundle.revision:06d}"  # type: ignore[union-attr]
            / "manifest.json"
        )
        manifest_path.unlink()
        with self.assertRaises(ContractError):
            inspect_day_completion(self.vault, local_date)

    def test_never_bulk_backfills_older_history(self) -> None:
        self.enable_all(updated_at="2026-01-01T00:00:00+08:00")
        missing = DayCompletionState(False, False)
        report = self.core(completion=missing).tick(now=self.local_time(18, 14))
        self.assertEqual(report["local_date"], "2026-08-17")
        self.assertEqual(self.runner.calls, [("2026-08-17", "recovery")])

    def test_schedule_enabled_after_due_slot_does_not_backfill(self) -> None:
        self.enable_all(updated_at="2026-08-18T08:00:00+08:00")
        report = self.core(completion=DayCompletionState(False, False)).tick(
            now=self.local_time(18, 8, 1)
        )
        self.assertEqual(report["status"], "not_due")
        self.assertEqual(report["local_date"], "2026-08-17")
        self.assertEqual(self.runner.calls, [])

    def test_gate_and_schedule_fail_closed(self) -> None:
        enable_agent_schedule(self.vault, updated_at="2026-08-17T12:00:00+08:00")
        gate_off = self.core().tick(now=self.local_time(18, 21))
        self.assertEqual(gate_off["status"], "master_gate_disabled")

        enable_agent_v1(self.vault)
        disable_agent_schedule(self.vault, updated_at="2026-08-17T13:00:00+08:00")
        schedule_off = self.core().tick(now=self.local_time(18, 21))
        self.assertEqual(schedule_off["status"], "schedule_disabled")
        self.assertEqual(self.runner.calls, [])

        path = agent_schedule_path(self.vault)
        value = json.loads(path.read_text(encoding="utf-8"))
        value["hour"] = 20
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ContractError) as caught:
            self.core().tick(now=self.local_time(18, 21))
        self.assertEqual(caught.exception.kind, "evidence")
        self.assertEqual(self.runner.calls, [])

    def test_missing_schedule_is_disabled(self) -> None:
        enable_agent_v1(self.vault)
        report = self.core().tick(now=self.local_time(18, 21))
        self.assertEqual(report["status"], "schedule_disabled")
        self.assertEqual(self.runner.calls, [])

    def test_manual_uses_master_gate_but_not_schedule_switch(self) -> None:
        enable_agent_v1(self.vault)
        report = self.core().run_manual(now=self.local_time(18, 10))
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["trigger"], "manual")
        self.assertEqual(report["local_date"], "2026-08-18")
        self.assertEqual(self.runner.calls, [("2026-08-18", "manual")])

    def test_runner_failure_is_finite_and_retryable(self) -> None:
        self.enable_all()
        attempts = 0

        def failing(local_date: str, trigger: str) -> dict[str, str]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("secret text must not leak")
            return {"status": "no_change"}

        core = self.core(runner=failing)
        failed = core.tick(now=self.local_time(18, 21))
        self.assertEqual(failed["status"], "runner_failed")
        self.assertEqual(failed["error_kind"], "runtime")
        self.assertNotIn("secret", json.dumps(failed))
        retried = core.tick(now=self.local_time(18, 21, 1))
        self.assertEqual(retried["status"], "completed")
        self.assertEqual(attempts, 2)

    def test_concurrent_same_day_ticks_are_serialized(self) -> None:
        self.enable_all()
        guard = threading.Lock()
        active = 0
        max_active = 0
        calls: list[tuple[str, str]] = []

        def slow(local_date: str, trigger: str) -> dict[str, str]:
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
                calls.append((local_date, trigger))
            time.sleep(0.05)
            with guard:
                active -= 1
            return {"status": "no_change"}

        core = self.core(runner=slow)
        barrier = threading.Barrier(3)
        reports: list[dict] = []

        def worker() -> None:
            barrier.wait()
            reports.append(core.tick(now=self.local_time(18, 21)))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(max_active, 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual({call for call in calls}, {("2026-08-18", "scheduled")})
        self.assertEqual([row["status"] for row in reports], ["completed", "completed"])

    def test_invalid_runner_contract_returns_runner_failed(self) -> None:
        self.enable_all()
        report = self.core(runner=lambda local_date, trigger: {"status": "invented"}).tick(
            now=self.local_time(18, 21)
        )
        self.assertEqual(report["status"], "runner_failed")
        self.assertEqual(report["error_kind"], "contract")


if __name__ == "__main__":
    unittest.main()
