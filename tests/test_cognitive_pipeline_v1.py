#!/usr/bin/env python3
"""End-to-end fake-provider tests for the Cognitive Secretary controller."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_AGENT = ROOT / "context-agent"
if str(CONTEXT_AGENT) not in sys.path:
    sys.path.insert(0, str(CONTEXT_AGENT))

from agent_v1 import build_agent_profile  # noqa: E402
from cognitive_pipeline_v1 import CognitivePipeline  # noqa: E402
from cognitive_v1 import make_cognitive_action_id, make_receipt_id  # noqa: E402
from core import ContractError  # noqa: E402
from deepseek_provider import CompletionResult  # noqa: E402


NOW = dt.datetime(2026, 8, 18, 21, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
LOCAL_DATE = "2026-08-18"
DAY = f"{LOCAL_DATE}.md"
USAGE = {
    "prompt_tokens": 30,
    "completion_tokens": 20,
    "total_tokens": 50,
    "prompt_cache_hit_tokens": 0,
    "prompt_cache_miss_tokens": 30,
    "completion_tokens_details": {"reasoning_tokens": 0},
}


class FakeProvider:
    def __init__(self, replies: Sequence[Any] = ()) -> None:
        self.replies = list(replies)
        self.calls = 0

    def complete(self, messages: Sequence[Mapping[str, str]]) -> CompletionResult:
        self.calls += 1
        if not self.replies:
            raise AssertionError("unexpected provider call")
        value = self.replies.pop(0)
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            value = value(messages)
        return CompletionResult(
            content=json.dumps(value, ensure_ascii=False),
            usage=USAGE,
            request_id=f"fake-{self.calls}",
            model="deepseek-v4-pro",
        )


def record_proposal(evidence_ref: str, statement: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "propose_receipt",
        "reason_code": "interpretation_ready",
        "arguments": {
            "summary": statement,
            "facets": {
                "content_types": ["observation"],
                "topics": ["产品设计"],
                "objects": ["方案评审"],
                "stance": "self_observation",
                "cognitive_state": "repeated",
                "purposes": ["future_decision"],
            },
            "memory_candidates": [
                {
                    "statement": statement,
                    "memory_kind": "observation",
                    "topics": ["产品设计"],
                    "purposes": ["future_decision"],
                    "uncertainty": "medium",
                    "source_ref_ids": [evidence_ref],
                }
            ],
            "relation_candidates": [],
            "source_ref_ids": [evidence_ref],
        },
    }


def daily_proposal(
    evidence_ref: str,
    statement: str,
    *,
    overview: str = "今天反复回到更早验证。",
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "propose_daily_bundle",
        "reason_code": "bundle_ready",
        "arguments": {
            "overview": overview,
            "themes": ["更早验证"],
            "changes": ["开始缩短反馈链路。"],
            "unresolved_questions": ["哪个部分可以最早验证？"],
            "action_clues": ["下次先发可验证草稿。"],
            "memory_operations": [
                {
                    "operation": "new",
                    "target_memory_ref_id": None,
                    "statement": statement,
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


def finish_daily() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "finish",
        "reason_code": "no_change",
        "arguments": {"reason": "no_change"},
    }


def finish_record() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "finish",
        "reason_code": "insufficient_signal",
        "arguments": {"reason": "insufficient_signal"},
    }


class PipelineCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="memento-pipeline-")
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

    def pipeline(self, provider: FakeProvider) -> CognitivePipeline:
        return CognitivePipeline(
            self.vault,
            provider,
            clock=lambda: NOW,
        )

    def prepare_record(self, pipeline: CognitivePipeline, *, newest: bool = False) -> tuple[str, str]:
        pipeline.records.reconcile_day(DAY, now=NOW, timezone=NOW.tzinfo)
        heads = pipeline.records.list_heads(local_date=LOCAL_DATE)
        head = heads[-1] if newest else heads[0]
        evidence = pipeline.runtime.materialize_record_evidence(head["record_id"])
        return head["record_id"], evidence[0]["ref_id"]

    def write_records(self, count: int) -> None:
        blocks = [
            "---\ndate: 2026-08-18\ntype: memento-daily\n---\n\n"
        ]
        for index in range(count):
            blocks.append(
                f"## {10 + index:02d}:00 · 周二 · Chrome\n\n"
                f"第 {index + 1} 条记录用于验证每日完整性闸门。\n\n---\n"
            )
        self.day.write_text("".join(blocks), encoding="utf-8")
        self.day.chmod(0o600)

    def terminalize_receipt(
        self,
        pipeline: CognitivePipeline,
        head: Mapping[str, Any],
        status: str,
        *,
        minute: int,
    ) -> None:
        receipt_id = make_receipt_id(head["record_id"])
        current = pipeline.actions.load_receipt_head(receipt_id)
        current_ref = pipeline.actions.load_receipt_head_ref(receipt_id)
        terminal = dataclasses.replace(
            current,
            revision=current.revision + 1,
            status=status,
            operation=status,
            created_at=(NOW + dt.timedelta(minutes=minute)).isoformat(
                timespec="seconds"
            ),
            user_action_id=make_cognitive_action_id(
                f"pipeline-terminal-{status}-{head['record_id']}"
            ),
            summary=None,
            facets={},
            memory_candidates=(),
            relation_candidates=(),
            source_spans=(),
            previous_revision_sha256=current_ref.revision_sha256,
        )
        pipeline.actions.commit_user_receipt_revision(
            terminal,
            expected_ref=current_ref,
        )

    def test_full_chain_commits_formal_objects_and_replay_is_zero_call(self) -> None:
        provider = FakeProvider()
        pipeline = self.pipeline(provider)
        record_id, evidence_ref = self.prepare_record(pipeline)
        provider.replies.extend(
            [
                record_proposal(evidence_ref, "评审前先定义最早可验证部分。"),
                daily_proposal(evidence_ref, "评审前先定义最早可验证部分。"),
            ]
        )

        result = pipeline.run_day(LOCAL_DATE)

        self.assertEqual(result.status, "committed")
        self.assertEqual(result.record_ids, (record_id,))
        self.assertEqual(provider.calls, 2)
        self.assertIsNotNone(result.material_brief)
        self.assertTrue(result.material_brief.requires_long_term_review)  # type: ignore[union-attr]
        self.assertEqual(len(pipeline.bundles.list_active_memories()), 1)
        self.assertEqual(len(pipeline.bundles.list_active_relations()), 0)
        formal_file = next(pipeline.bundles.memory_dir.glob("rmem_*.json"))
        self.assertNotIn("cmem_", formal_file.read_text(encoding="utf-8"))
        candidate_file = next(pipeline.bundles.candidate_staging_dir.glob("drun_*.json"))
        self.assertIn("cmem_", candidate_file.read_text(encoding="utf-8"))

        # Re-open every store/runtime object to prove that replay is decided
        # from the committed manifest rather than an in-memory/provider cache.
        replay_provider = FakeProvider()
        replay = self.pipeline(replay_provider).run_day(LOCAL_DATE)
        self.assertEqual(replay.status, "no_change")
        self.assertEqual(provider.calls, 2)
        self.assertEqual(replay_provider.calls, 0)
        self.assertFalse(replay.material_brief.requires_long_term_review)  # type: ignore[union-attr]
        self.assertEqual(
            pipeline.bundles.load_day_bundle_ref(LOCAL_DATE).revision, 1  # type: ignore[union-attr]
        )

    def test_self_produced_profile_change_is_cache_only_not_new_daily_input(self) -> None:
        provider = FakeProvider()
        pipeline = self.pipeline(provider)
        _, evidence_ref = self.prepare_record(pipeline)
        input_profile_sha = build_agent_profile(self.vault)["profile_sha256"]
        output_profile_sha = "b" * 64
        provider.replies.extend(
            [
                record_proposal(evidence_ref, "先定义最早验证。"),
                daily_proposal(evidence_ref, "先定义最早验证。"),
            ]
        )
        first = pipeline.run_day(
            LOCAL_DATE,
            profile_sha256=input_profile_sha,
        )
        self.assertEqual(first.status, "committed")

        replay_provider = FakeProvider()
        replay = self.pipeline(replay_provider).run_day(
            LOCAL_DATE,
            profile_sha256=output_profile_sha,
            replay_profile_sha256=input_profile_sha,
        )

        self.assertEqual(replay.status, "no_change")
        self.assertEqual(replay_provider.calls, 0)

    def test_same_day_new_record_appends_summary_and_bundle_revision(self) -> None:
        provider = FakeProvider()
        pipeline = self.pipeline(provider)
        _, first_evidence = self.prepare_record(pipeline)
        provider.replies.extend(
            [
                record_proposal(first_evidence, "先定义最早验证。"),
                daily_proposal(first_evidence, "先定义最早验证。"),
            ]
        )
        first = pipeline.run_day(LOCAL_DATE)
        self.assertEqual(first.status, "committed")
        first_summary, first_summary_ref = pipeline.bundles.load_daily_summary_head(  # type: ignore[misc]
            LOCAL_DATE
        )

        with self.day.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n## 16:40 · 周二 · 语音\n\n"
                "我可以先发问题、假设和验证方式。\n\n---\n"
            )
        _, second_evidence = self.prepare_record(pipeline, newest=True)
        provider.replies.extend(
            [
                record_proposal(second_evidence, "草稿先保留问题、假设和验证方式。"),
                daily_proposal(
                    second_evidence,
                    "草稿先保留问题、假设和验证方式。",
                    overview="今天开始把最早验证写成草稿结构。",
                ),
            ]
        )
        second = pipeline.run_day(LOCAL_DATE)

        self.assertEqual(second.status, "committed")
        self.assertEqual(provider.calls, 4)
        bundle_ref = pipeline.bundles.load_day_bundle_ref(LOCAL_DATE)
        self.assertEqual(bundle_ref.revision, 2)  # type: ignore[union-attr]
        second_summary, _ = pipeline.bundles.load_daily_summary_head(LOCAL_DATE)  # type: ignore[misc]
        self.assertEqual(second_summary.revision, 2)
        self.assertEqual(
            second_summary.previous_revision_sha256,
            first_summary_ref.revision_sha256,
        )
        self.assertEqual(first_summary.sha256, first_summary_ref.revision_sha256)
        self.assertEqual(len(pipeline.bundles.list_active_memories()), 2)

    def test_daily_finish_leaves_candidates_and_formal_store_empty(self) -> None:
        provider = FakeProvider()
        pipeline = self.pipeline(provider)
        _, evidence_ref = self.prepare_record(pipeline)
        provider.replies.extend(
            [record_proposal(evidence_ref, "只保留当天观察。"), finish_daily()]
        )

        result = pipeline.run_day(LOCAL_DATE)

        self.assertEqual(result.status, "no_change")
        self.assertIsNone(result.commit_result)
        self.assertIsNone(pipeline.bundles.load_day_bundle_ref(LOCAL_DATE))
        self.assertEqual(pipeline.bundles.list_active_memories(), ())
        self.assertEqual(list(pipeline.bundles.candidate_staging_dir.glob("*.json")), [])

    def test_staged_daily_candidate_recovers_after_commit_interruption(self) -> None:
        provider = FakeProvider()
        pipeline = self.pipeline(provider)
        _, evidence_ref = self.prepare_record(pipeline)
        provider.replies.extend(
            [
                record_proposal(evidence_ref, "先把最早验证写清楚。"),
                daily_proposal(evidence_ref, "先把最早验证写清楚。"),
            ]
        )
        original_commit = pipeline.bundles.commit_day_bundle

        def interrupt_commit(**_kwargs: Any) -> Any:
            raise RuntimeError("simulated process interruption before commit")

        pipeline.bundles.commit_day_bundle = interrupt_commit  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "simulated process interruption"):
            pipeline.run_day(LOCAL_DATE)
        pipeline.bundles.commit_day_bundle = original_commit  # type: ignore[method-assign]
        self.assertEqual(provider.calls, 2)
        self.assertIsNone(pipeline.bundles.load_day_bundle_ref(LOCAL_DATE))
        self.assertEqual(len(list(pipeline.bundles.candidate_staging_dir.glob("*.json"))), 1)

        recovery_provider = FakeProvider()
        recovered = self.pipeline(recovery_provider).run_day(
            LOCAL_DATE, trigger="recovery"
        )

        self.assertEqual(recovered.status, "committed")
        self.assertEqual(recovery_provider.calls, 0)
        self.assertEqual(len(recovered.commit_result.memory_refs), 1)  # type: ignore[union-attr]
        self.assertEqual(len(pipeline.bundles.list_active_memories()), 1)

    def test_unknown_provider_outcome_is_not_retried_by_pipeline(self) -> None:
        provider = FakeProvider([RuntimeError("outcome unknown")])
        pipeline = self.pipeline(provider)
        self.prepare_record(pipeline)

        with self.assertRaises(RuntimeError):
            pipeline.run_day(LOCAL_DATE)
        self.assertEqual(provider.calls, 1)

        recovered = pipeline.run_day(LOCAL_DATE)
        self.assertEqual(recovered.status, "no_receipts")
        self.assertEqual(provider.calls, 1)
        self.assertIsNone(pipeline.bundles.load_day_bundle_ref(LOCAL_DATE))

    def test_daily_waits_for_every_eligible_receipt_then_source_edit_repairs(self) -> None:
        self.write_records(6)
        provider = FakeProvider()
        pipeline = self.pipeline(provider)
        pipeline.records.reconcile_day(DAY, now=NOW, timezone=NOW.tzinfo)
        heads = pipeline.records.list_heads(local_date=LOCAL_DATE)
        self.assertEqual(len(heads), 6)
        evidence_refs = [
            pipeline.runtime.materialize_record_evidence(head["record_id"])[0][
                "ref_id"
            ]
            for head in heads
        ]
        _, watermark = pipeline.actions.action_watermark()

        # Seed five current receipts and one fully known schema rejection.
        for index, (head, evidence_ref) in enumerate(zip(heads, evidence_refs)):
            provider.replies.append(
                record_proposal(evidence_ref, f"第 {index + 1} 条已整理。")
                if index < 5
                else {"invalid": "record-action-schema"}
            )
            request = pipeline.runtime.create_interpretation_request(
                head["record_id"],
                trigger="reconcile",
                feedback_watermark_sha256=watermark,
            )
            interpreted = pipeline.runtime.run_interpretation(request["id"])
            self.assertEqual(
                interpreted["status"], "completed" if index < 5 else "error"
            )
        self.assertEqual(provider.calls, 6)

        # The cached known-invalid result receives its one bounded retry.  A
        # second schema failure remains visible and still blocks the day.
        provider.replies.append({"invalid": "retry-record-action-schema"})
        blocked = pipeline.run_day(LOCAL_DATE)

        self.assertEqual(blocked.status, "no_receipts")
        self.assertEqual(len(blocked.record_ids), 6)
        self.assertEqual(blocked.receipt_refs, ())
        self.assertIsNone(blocked.daily_result)
        self.assertIsNone(blocked.commit_result)
        self.assertIsNone(blocked.material_brief)
        self.assertEqual(provider.calls, 7)
        self.assertEqual(
            len(list(pipeline.runtime.files.daily_requests.glob("*.json"))), 0
        )
        self.assertEqual(len(list(pipeline.runtime.files.daily_runs.glob("*.json"))), 0)
        self.assertIsNone(pipeline.bundles.load_day_bundle_ref(LOCAL_DATE))
        self.assertEqual(
            len(list(pipeline.bundles.summary_dir.glob("*.json"))), 0
        )
        self.assertEqual(pipeline.bundles.list_active_memories(), ())
        self.assertEqual(pipeline.bundles.list_active_relations(), ())

        blocked_replay = pipeline.run_day(LOCAL_DATE)
        self.assertEqual(blocked_replay.status, "no_receipts")
        self.assertEqual(provider.calls, 7)
        self.assertIsNone(pipeline.bundles.load_day_bundle_ref(LOCAL_DATE))

        # Editing the source creates new material identity and permits a fresh
        # interpretation after the sole retry for revision 1 is exhausted.
        updated = self.day.read_text(encoding="utf-8").replace(
            "第 6 条记录用于验证每日完整性闸门。",
            "第 6 条记录已补充新信息，可以重新整理。",
        )
        self.day.write_text(updated, encoding="utf-8")
        self.day.chmod(0o600)
        pipeline.records.reconcile_day(DAY, now=NOW, timezone=NOW.tzinfo)
        revised_head = pipeline.records.load_head(heads[-1]["record_id"])
        self.assertEqual(revised_head["revision"], 2)
        revised_evidence_ref = pipeline.runtime.materialize_record_evidence(
            heads[-1]["record_id"]
        )[0]["ref_id"]

        provider.replies.extend(
            [
                record_proposal(revised_evidence_ref, "第 6 条已重新整理。"),
                daily_proposal(evidence_refs[0], "六条记录齐备后才形成日级沉淀。"),
            ]
        )
        committed = pipeline.run_day(LOCAL_DATE)

        self.assertEqual(committed.status, "committed")
        self.assertEqual(len(committed.receipt_refs), 6)
        self.assertEqual(provider.calls, 9)
        bundle_ref = pipeline.bundles.load_day_bundle_ref(LOCAL_DATE)
        self.assertIsNotNone(bundle_ref)
        self.assertEqual(bundle_ref.revision, 1)  # type: ignore[union-attr]
        self.assertEqual(len(pipeline.bundles.list_active_memories()), 1)

        replay_provider = FakeProvider()
        replay = self.pipeline(replay_provider).run_day(LOCAL_DATE)
        self.assertEqual(replay.status, "no_change")
        self.assertEqual(replay_provider.calls, 0)
        self.assertEqual(
            pipeline.bundles.load_day_bundle_ref(LOCAL_DATE).revision, 1  # type: ignore[union-attr]
        )

    def test_cached_schema_retry_is_shared_by_every_day_trigger(self) -> None:
        for trigger in ("manual", "scheduled", "recovery"):
            with self.subTest(trigger=trigger), tempfile.TemporaryDirectory(
                prefix=f"memento-pipeline-{trigger}-"
            ) as temporary:
                vault = Path(temporary) / "vault"
                vault.mkdir(mode=0o700)
                day = vault / DAY
                day.write_text(self.day.read_text(encoding="utf-8"), encoding="utf-8")
                day.chmod(0o600)
                provider = FakeProvider([{"invalid": "record-action-schema"}])
                pipeline = CognitivePipeline(vault, provider, clock=lambda: NOW)

                first = pipeline.run_day(LOCAL_DATE, trigger=trigger)
                self.assertEqual(first.status, "no_receipts")
                self.assertEqual(provider.calls, 1)
                record_id = first.record_ids[0]
                evidence_ref = pipeline.runtime.materialize_record_evidence(record_id)[0][
                    "ref_id"
                ]
                provider.replies.extend(
                    [
                        record_proposal(evidence_ref, f"{trigger} 重试成功。"),
                        daily_proposal(evidence_ref, f"{trigger} 日级归并成功。"),
                    ]
                )

                second = pipeline.run_day(LOCAL_DATE, trigger=trigger)
                self.assertEqual(second.status, "committed")
                self.assertEqual(provider.calls, 3)
                self.assertEqual(
                    [row["status"] for row in second.interpretation_results],
                    ["error", "completed"],
                )

                replay = pipeline.run_day(LOCAL_DATE, trigger=trigger)
                self.assertEqual(replay.status, "no_change")
                self.assertEqual(provider.calls, 3)

    def test_failed_known_invalid_retry_is_zero_call_on_later_tick(self) -> None:
        provider = FakeProvider([{"invalid": "first-schema"}])
        pipeline = self.pipeline(provider)

        first = pipeline.run_day(LOCAL_DATE, trigger="recovery")
        self.assertEqual(first.status, "no_receipts")
        self.assertEqual(provider.calls, 1)

        provider.replies.append({"invalid": "retry-schema"})
        second = pipeline.run_day(LOCAL_DATE, trigger="recovery")
        self.assertEqual(second.status, "no_receipts")
        self.assertEqual(provider.calls, 2)
        self.assertEqual(
            [row["status"] for row in second.interpretation_results],
            ["error", "error"],
        )

        third = pipeline.run_day(LOCAL_DATE, trigger="recovery")
        self.assertEqual(third.status, "no_receipts")
        self.assertEqual(provider.calls, 2)
        self.assertEqual(
            [row["status"] for row in third.interpretation_results],
            ["error", "error"],
        )

    def test_known_invalid_retry_can_finish_no_candidate_and_then_is_zero_call(
        self,
    ) -> None:
        provider = FakeProvider([{"invalid": "first-schema"}])
        pipeline = self.pipeline(provider)

        first = pipeline.run_day(LOCAL_DATE, trigger="scheduled")
        self.assertEqual(first.status, "no_receipts")
        self.assertEqual(provider.calls, 1)

        provider.replies.append(finish_record())
        second = pipeline.run_day(LOCAL_DATE, trigger="scheduled")
        self.assertEqual(second.status, "no_candidate")
        self.assertEqual(provider.calls, 2)
        self.assertEqual(
            [row["status"] for row in second.interpretation_results],
            ["error", "no_candidate"],
        )

        third = pipeline.run_day(LOCAL_DATE, trigger="scheduled")
        self.assertEqual(third.status, "no_candidate")
        self.assertEqual(provider.calls, 2)
        self.assertEqual(
            [row["status"] for row in third.interpretation_results],
            ["no_candidate"],
        )
        self.assertIs(third.interpretation_results[0]["cached"], True)

    def test_original_only_and_tombstone_do_not_block_remaining_receipts(self) -> None:
        self.write_records(3)
        provider = FakeProvider()
        pipeline = self.pipeline(provider)
        pipeline.records.reconcile_day(DAY, now=NOW, timezone=NOW.tzinfo)
        heads = pipeline.records.list_heads(local_date=LOCAL_DATE)
        evidence_refs = [
            pipeline.runtime.materialize_record_evidence(head["record_id"])[0][
                "ref_id"
            ]
            for head in heads
        ]
        _, watermark = pipeline.actions.action_watermark()
        for index, (head, evidence_ref) in enumerate(zip(heads, evidence_refs)):
            provider.replies.append(
                record_proposal(evidence_ref, f"终态测试记录 {index + 1}。")
            )
            request = pipeline.runtime.create_interpretation_request(
                head["record_id"],
                trigger="reconcile",
                feedback_watermark_sha256=watermark,
            )
            self.assertEqual(
                pipeline.runtime.run_interpretation(request["id"])["status"],
                "completed",
            )

        for index, status in enumerate(("original_only", "tombstone")):
            self.terminalize_receipt(
                pipeline,
                heads[index],
                status,
                minute=index + 1,
            )

        provider.replies.append(
            daily_proposal(
                evidence_refs[-1],
                "显式终态不阻塞其余可归并记录。",
            )
        )
        result = pipeline.run_day(LOCAL_DATE)

        self.assertEqual(result.status, "committed")
        self.assertEqual(len(result.receipt_refs), 1)
        self.assertEqual(provider.calls, 4)
        self.assertEqual(len(pipeline.bundles.list_active_memories()), 1)

    def test_all_user_terminal_records_return_no_change(self) -> None:
        self.write_records(2)
        provider = FakeProvider()
        pipeline = self.pipeline(provider)
        pipeline.records.reconcile_day(DAY, now=NOW, timezone=NOW.tzinfo)
        heads = pipeline.records.list_heads(local_date=LOCAL_DATE)
        _, watermark = pipeline.actions.action_watermark()
        for index, head in enumerate(heads):
            evidence_ref = pipeline.runtime.materialize_record_evidence(
                head["record_id"]
            )[0]["ref_id"]
            provider.replies.append(
                record_proposal(evidence_ref, f"终态记录 {index + 1}。")
            )
            request = pipeline.runtime.create_interpretation_request(
                head["record_id"],
                trigger="reconcile",
                feedback_watermark_sha256=watermark,
            )
            self.assertEqual(
                pipeline.runtime.run_interpretation(request["id"])["status"],
                "completed",
            )
        for index, status in enumerate(("original_only", "tombstone")):
            self.terminalize_receipt(
                pipeline,
                heads[index],
                status,
                minute=index + 1,
            )

        result = pipeline.run_day(LOCAL_DATE)

        self.assertEqual(result.status, "no_change")
        self.assertEqual(result.receipt_refs, ())
        self.assertEqual(provider.calls, 2)
        self.assertEqual(
            len(list(pipeline.runtime.files.daily_requests.glob("*.json"))), 0
        )

    def test_user_terminal_plus_no_candidate_returns_no_candidate(self) -> None:
        self.write_records(2)
        provider = FakeProvider()
        pipeline = self.pipeline(provider)
        pipeline.records.reconcile_day(DAY, now=NOW, timezone=NOW.tzinfo)
        heads = pipeline.records.list_heads(local_date=LOCAL_DATE)
        evidence_ref = pipeline.runtime.materialize_record_evidence(
            heads[0]["record_id"]
        )[0]["ref_id"]
        _, watermark = pipeline.actions.action_watermark()
        provider.replies.extend(
            [record_proposal(evidence_ref, "只保留原文。"), finish_record()]
        )
        for head in heads:
            request = pipeline.runtime.create_interpretation_request(
                head["record_id"],
                trigger="reconcile",
                feedback_watermark_sha256=watermark,
            )
            pipeline.runtime.run_interpretation(request["id"])
        self.terminalize_receipt(
            pipeline,
            heads[0],
            "original_only",
            minute=1,
        )

        result = pipeline.run_day(LOCAL_DATE)

        self.assertEqual(result.status, "no_candidate")
        self.assertEqual(result.receipt_refs, ())
        self.assertEqual(provider.calls, 2)

    def test_no_candidate_is_excluded_while_ready_receipts_commit(self) -> None:
        self.write_records(2)
        provider = FakeProvider()
        pipeline = self.pipeline(provider)
        pipeline.records.reconcile_day(DAY, now=NOW, timezone=NOW.tzinfo)
        heads = pipeline.records.list_heads(local_date=LOCAL_DATE)
        evidence_ref = pipeline.runtime.materialize_record_evidence(
            heads[0]["record_id"]
        )[0]["ref_id"]
        _, watermark = pipeline.actions.action_watermark()
        provider.replies.extend(
            [record_proposal(evidence_ref, "第一条已整理。"), finish_record()]
        )
        for head in heads:
            request = pipeline.runtime.create_interpretation_request(
                head["record_id"],
                trigger="reconcile",
                feedback_watermark_sha256=watermark,
            )
            pipeline.runtime.run_interpretation(request["id"])
        self.assertEqual(provider.calls, 2)
        provider.replies.append(
            daily_proposal(evidence_ref, "只归并已形成合法回执的记录。")
        )

        result = pipeline.run_day(LOCAL_DATE)

        self.assertEqual(result.status, "committed")
        self.assertEqual(len(result.receipt_refs), 1)
        self.assertEqual(
            result.receipt_refs[0].id,
            make_receipt_id(heads[0]["record_id"]),
        )
        self.assertEqual(len(result.interpretation_results), 1)
        self.assertEqual(result.interpretation_results[0]["status"], "no_candidate")
        self.assertIs(result.interpretation_results[0]["cached"], True)
        self.assertEqual(provider.calls, 3)
        self.assertEqual(
            len(list(pipeline.runtime.files.daily_requests.glob("*.json"))), 1
        )
        manifest = pipeline.bundles.load_day_manifest(LOCAL_DATE)
        self.assertIsNotNone(manifest)
        assert manifest is not None
        self.assertEqual(len(manifest["receipt_refs"]), 1)
        self.assertEqual(len(pipeline.bundles.list_active_memories()), 1)

    def test_all_current_no_candidate_records_are_terminal_and_cached(self) -> None:
        self.write_records(2)
        provider = FakeProvider([finish_record(), finish_record()])
        pipeline = self.pipeline(provider)

        first = pipeline.run_day(LOCAL_DATE)
        second = pipeline.run_day(LOCAL_DATE)

        self.assertEqual(first.status, "no_candidate")
        self.assertEqual(second.status, "no_candidate")
        self.assertEqual(first.receipt_refs, ())
        self.assertEqual(second.receipt_refs, ())
        self.assertEqual(provider.calls, 2)
        self.assertEqual(
            [row["status"] for row in second.interpretation_results],
            ["no_candidate", "no_candidate"],
        )
        self.assertTrue(
            all(row["cached"] is True for row in second.interpretation_results)
        )
        self.assertEqual(
            len(list(pipeline.runtime.files.daily_requests.glob("*.json"))), 0
        )
        self.assertIsNone(pipeline.bundles.load_day_bundle_ref(LOCAL_DATE))

    def test_source_edit_invalidates_an_older_no_candidate_terminal(self) -> None:
        self.write_records(1)
        provider = FakeProvider([finish_record()])
        pipeline = self.pipeline(provider)

        first = pipeline.run_day(LOCAL_DATE)
        self.assertEqual(first.status, "no_candidate")
        first_head = pipeline.records.list_heads(local_date=LOCAL_DATE)[0]
        self.assertEqual(first_head["revision"], 1)

        updated = self.day.read_text(encoding="utf-8").replace(
            "第 1 条记录用于验证每日完整性闸门。",
            "第 1 条记录已修改，必须重新整理。",
        )
        self.day.write_text(updated, encoding="utf-8")
        self.day.chmod(0o600)
        provider.replies.append({"invalid": "record-action-schema"})

        second = pipeline.run_day(LOCAL_DATE)

        second_head = pipeline.records.list_heads(local_date=LOCAL_DATE)[0]
        self.assertEqual(second_head["record_id"], first_head["record_id"])
        self.assertEqual(second_head["revision"], 2)
        self.assertEqual(second.status, "no_receipts")
        self.assertEqual(provider.calls, 2)
        self.assertEqual(len(second.interpretation_results), 1)
        self.assertEqual(second.interpretation_results[0]["status"], "error")
        self.assertEqual(
            second.interpretation_results[0]["request"]["record_ref"]["revision"],
            2,
        )
        self.assertIsNone(pipeline.bundles.load_day_bundle_ref(LOCAL_DATE))

    def test_default_missing_daily_file_is_a_normal_empty_day(self) -> None:
        self.day.unlink()
        provider = FakeProvider()
        pipeline = self.pipeline(provider)

        result = pipeline.run_day(LOCAL_DATE, trigger="recovery")

        self.assertEqual(result.status, "no_records")
        self.assertEqual(result.record_ids, ())
        self.assertEqual(provider.calls, 0)

    def test_explicit_missing_daily_file_still_fails_closed(self) -> None:
        self.day.unlink()
        pipeline = self.pipeline(FakeProvider())

        with self.assertRaisesRegex(ContractError, "\u4e0d\u5b58\u5728"):
            pipeline.run_day(
                LOCAL_DATE,
                trigger="manual",
                source_files=(DAY,),
            )

    def test_missing_source_with_indexed_records_still_fails_closed(self) -> None:
        pipeline = self.pipeline(FakeProvider())
        pipeline.records.reconcile_day(DAY, now=NOW, timezone=NOW.tzinfo)
        self.day.unlink()

        with self.assertRaisesRegex(ContractError, "\u4e0d\u5b58\u5728"):
            pipeline.run_day(LOCAL_DATE, trigger="recovery")

    def test_dangling_daily_symlink_is_not_treated_as_an_empty_day(self) -> None:
        self.day.unlink()
        self.day.symlink_to(self.vault / "missing-daily-source.md")
        pipeline = self.pipeline(FakeProvider())

        with self.assertRaises(ContractError) as captured:
            pipeline.run_day(LOCAL_DATE, trigger="recovery")
        self.assertEqual(captured.exception.kind, "evidence")


if __name__ == "__main__":
    unittest.main()
