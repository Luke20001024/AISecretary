#!/usr/bin/env python3
"""Focused tests for the bounded per-record Cognitive Secretary fast path."""

from __future__ import annotations

import datetime as dt
import dataclasses
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_AGENT = ROOT / "context-agent"
if str(CONTEXT_AGENT) not in sys.path:
    sys.path.insert(0, str(CONTEXT_AGENT))

from cognitive_record_worker_v1 import CognitiveRecordWorker  # noqa: E402
from cognitive_runtime_v1 import CognitiveRuntime  # noqa: E402
from cognitive_v1 import (  # noqa: E402
    COGNITIVE_SCHEMA_VERSION,
    CognitiveUserAction,
    make_cognitive_action_id,
    make_receipt_id,
)
from core import ContractError  # noqa: E402
from deepseek_provider import CompletionResult  # noqa: E402


NOW = dt.datetime(2026, 8, 18, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
LOCAL_DATE = "2026-08-18"
DAY = f"{LOCAL_DATE}.md"
FRONTMATTER = b"---\ndate: 2026-08-18\ntype: memento-daily\n---\n"
USAGE = {
    "prompt_tokens": 10,
    "completion_tokens": 5,
    "total_tokens": 15,
    "prompt_cache_hit_tokens": 0,
    "prompt_cache_miss_tokens": 10,
    "completion_tokens_details": {"reasoning_tokens": 0},
}


def block(body: str, *, time: str, source: str = "Chrome") -> bytes:
    return (
        f"\n## {time} · 周二 · {source}\n\n{body}\n\n---\n"
    ).encode("utf-8")


def edit_payload() -> dict[str, Any]:
    return {
        "summary": "我修改了这条即时整理。",
        "facets": {
            "content_types": ["observation"],
            "topics": ["产品设计"],
            "objects": ["方案评审"],
            "stance": "self_observation",
            "cognitive_state": "revises_existing",
            "purposes": ["future_decision"],
        },
    }


def proposal(evidence_ref: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "propose_receipt",
        "reason_code": "interpretation_ready",
        "arguments": {
            "summary": "先暴露可验证部分，再补齐完整方案。",
            "facets": {
                "content_types": ["observation"],
                "topics": ["产品设计"],
                "objects": ["方案评审"],
                "stance": "self_observation",
                "cognitive_state": "repeated",
                "purposes": ["future_decision"],
            },
            "memory_candidates": [],
            "relation_candidates": [],
            "source_ref_ids": [evidence_ref],
        },
    }


def finish_record() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "finish",
        "reason_code": "insufficient_signal",
        "arguments": {"reason": "insufficient_signal"},
    }


class AutoProvider:
    def __init__(self, replies: Sequence[Any] | None = None) -> None:
        self.replies = list(replies or [])
        self.calls = 0
        self.messages: list[list[Mapping[str, str]]] = []
        self._lock = threading.Lock()

    def complete(self, messages: Sequence[Mapping[str, str]]) -> CompletionResult:
        with self._lock:
            self.calls += 1
            call = self.calls
            self.messages.append(list(messages))
            item = self.replies.pop(0) if self.replies else None
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item = item(messages)
        if isinstance(item, CompletionResult):
            return item
        if item is None:
            user = json.loads(messages[-1]["content"])
            evidence_ref = user["untrusted_data"]["source_catalog"][0]["ref_id"]
            item = proposal(evidence_ref)
        if isinstance(item, str):
            content = item
        else:
            content = json.dumps(item, ensure_ascii=False)
        return CompletionResult(
            content=content,
            usage=USAGE,
            request_id=f"fake-{call}",
            model="deepseek-v4-pro",
        )


class BlockingProvider(AutoProvider):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, messages: Sequence[Mapping[str, str]]) -> CompletionResult:
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise AssertionError("blocking provider timed out")
        return super().complete(messages)


class WorkerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="memento-record-worker-")
        self.vault = Path(self.temporary.name) / "vault"
        self.vault.mkdir(mode=0o700)
        self.day = self.vault / DAY

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, *rows: tuple[str, str]) -> None:
        content = FRONTMATTER + b"".join(block(body, time=time) for time, body in rows)
        self.day.write_bytes(content)
        self.day.chmod(0o600)

    def worker(
        self,
        provider: AutoProvider,
        *,
        hook=None,
    ) -> CognitiveRecordWorker:
        runtime = CognitiveRuntime(
            self.vault,
            provider,
            lock_factory=None,
            clock=lambda: NOW,
        )
        return CognitiveRecordWorker(
            self.vault,
            runtime=runtime,
            home_projection_hook=hook,
            clock=lambda: NOW,
        )

    def run_worker(self, worker: CognitiveRecordWorker, *, limit: int | None = None):
        return worker.run(local_date=LOCAL_DATE, source_file=DAY, limit=limit)


class FastPathTests(WorkerCase):
    def test_empty_day_does_not_call_provider(self) -> None:
        self.write()
        provider = AutoProvider()
        result = self.run_worker(self.worker(provider))

        self.assertEqual(result.status, "no_records")
        self.assertEqual(result.selected_count, 0)
        self.assertEqual(provider.calls, 0)

    def test_one_record_creates_receipt_and_hook_exposes_only_finite_status(self) -> None:
        self.write(("10:50", "评审前先找到最早可验证的部分。"))
        provider = AutoProvider()
        publications: list[tuple[str, dict[str, dict[str, Any]]]] = []

        def hook(local_date, statuses):
            publications.append((local_date, {key: dict(value) for key, value in statuses.items()}))

        worker = self.worker(provider, hook=hook)
        result = self.run_worker(worker)

        self.assertEqual((result.status, result.selected_count, provider.calls), ("completed", 1, 1))
        self.assertEqual(result.items[0].outcome, "ready")
        self.assertIsNotNone(result.items[0].receipt_ref)
        self.assertEqual(publications[0][0], LOCAL_DATE)
        self.assertEqual(list(publications[0][1].values()), [{"status": "processing", "error_kind": None}])
        self.assertEqual(publications[-1][1], {})
        self.assertNotIn("评审", json.dumps(publications, ensure_ascii=False))
        public_result = json.dumps(result.to_dict(), ensure_ascii=False)
        self.assertNotIn("评审", public_result)
        self.assertNotIn("summary", public_result)

    def test_limit_preserves_capture_order_and_defers_without_provider_call(self) -> None:
        self.write(
            ("09:00", "第一条。"),
            ("10:00", "第二条。"),
            ("11:00", "第三条。"),
        )
        provider = AutoProvider()
        worker = self.worker(provider)

        first = self.run_worker(worker, limit=2)
        self.assertEqual((first.status, first.selected_count, first.deferred_count), ("partial", 2, 1))
        self.assertEqual(provider.calls, 2)
        interpreted = [item.record_id for item in first.items if item.outcome == "ready"]
        heads = worker.records.list_heads(local_date=LOCAL_DATE)
        self.assertEqual(interpreted, [row["record_id"] for row in heads[:2]])

        second = self.run_worker(worker, limit=2)
        self.assertEqual((second.status, second.selected_count, second.deferred_count), ("completed", 1, 0))
        self.assertEqual(provider.calls, 3)

    def test_same_material_replay_uses_zero_provider_calls(self) -> None:
        self.write(("10:50", "同一条记录。"))
        provider = AutoProvider()
        worker = self.worker(provider)
        first = self.run_worker(worker)
        self.assertEqual(first.items[0].outcome, "ready")

        second = self.run_worker(worker)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(second.selected_count, 0)
        self.assertEqual(second.items[0].outcome, "current")

    def test_applied_user_edit_republishes_once_with_zero_selected(self) -> None:
        self.write(("10:50", "先得到一条 receipt。"))
        provider = AutoProvider()
        publications: list[tuple[str, dict[str, dict[str, Any]]]] = []

        def hook(local_date, statuses):
            publications.append(
                (local_date, {key: dict(value) for key, value in statuses.items()})
            )

        worker = self.worker(provider, hook=hook)
        first = self.run_worker(worker)
        receipt_ref = worker.actions.load_receipt_head_ref(
            make_receipt_id(first.items[0].record_id)
        )
        publications.clear()
        worker.actions.submit_action(
            CognitiveUserAction(
                COGNITIVE_SCHEMA_VERSION,
                "memento_cognitive_user_action",
                make_cognitive_action_id("worker-publish-user-edit"),
                NOW.isoformat(timespec="seconds"),
                "edit_receipt",
                receipt_ref,
                edit_payload(),
            )
        )

        edited = self.run_worker(worker)

        self.assertEqual((edited.selected_count, provider.calls), (0, 1))
        self.assertEqual(edited.actions.applied, 1)
        self.assertEqual(publications, [(LOCAL_DATE, {})])

        replay = self.run_worker(worker)
        self.assertEqual((replay.selected_count, provider.calls), (0, 1))
        self.assertEqual(replay.actions.applied, 0)
        self.assertEqual(publications, [(LOCAL_DATE, {})])

    def test_terminal_derivative_retraction_republishes_with_zero_selected(self) -> None:
        self.write(("10:50", "原文优先会撤回正式衍生物。"))
        provider = AutoProvider()
        publications: list[tuple[str, dict[str, dict[str, Any]]]] = []

        def hook(local_date, statuses):
            publications.append(
                (local_date, {key: dict(value) for key, value in statuses.items()})
            )

        worker = self.worker(provider, hook=hook)
        first = self.run_worker(worker)
        receipt_ref = worker.actions.load_receipt_head_ref(
            make_receipt_id(first.items[0].record_id)
        )
        worker.actions.submit_action(
            CognitiveUserAction(
                COGNITIVE_SCHEMA_VERSION,
                "memento_cognitive_user_action",
                make_cognitive_action_id("worker-publish-original-only"),
                NOW.isoformat(timespec="seconds"),
                "original_only",
                receipt_ref,
                None,
            )
        )
        applied = self.run_worker(worker)
        self.assertEqual((applied.selected_count, provider.calls), (0, 1))
        publications.clear()

        terminal_retraction = types.SimpleNamespace(status="applied")
        with mock.patch.object(
            worker.formal,
            "retract_terminal_receipt_derivatives",
            return_value=terminal_retraction,
        ):
            replay = self.run_worker(worker)

        self.assertEqual((replay.selected_count, provider.calls), (0, 1))
        self.assertEqual(replay.actions.applied, 0)
        self.assertEqual(publications, [(LOCAL_DATE, {})])

    def test_source_edit_appends_same_receipt_chain_and_replay_is_zero_call(self) -> None:
        self.write(("10:50", "旧内容。"))
        provider = AutoProvider()
        worker = self.worker(provider)
        first = self.run_worker(worker)
        record_id = first.items[0].record_id
        receipt_id = make_receipt_id(record_id)
        before = worker.actions.load_receipt_head(receipt_id)
        self.assertEqual(before.revision, 1)

        self.write(("10:50", "编辑后的新内容。"))
        second = self.run_worker(worker)

        self.assertEqual(provider.calls, 2)
        revised = next(item for item in second.items if item.record_id == record_id)
        self.assertEqual((revised.outcome, revised.error_kind), ("ready", None))
        after = worker.actions.load_receipt_head(receipt_id)
        self.assertEqual(after.revision, 2)
        self.assertEqual(after.record_ref.revision, 2)
        self.assertEqual(after.previous_revision_sha256, before.sha256)

        replay = self.run_worker(worker)
        self.assertEqual(provider.calls, 2)
        self.assertEqual((replay.selected_count, replay.items[0].outcome), (0, "current"))

    def test_source_edit_conflict_is_finite_failure_and_keeps_old_receipt(self) -> None:
        self.write(("10:50", "旧内容。"))
        provider = AutoProvider()
        worker = self.worker(provider)
        first = self.run_worker(worker)
        record_id = first.items[0].record_id
        receipt_id = make_receipt_id(record_id)
        before = worker.actions.load_receipt_head(receipt_id)

        self.write(("10:50", "编辑后的新内容。"))
        original_commit = worker.runtime._commit_receipt

        def conflict(**kwargs):
            raise ContractError("controlled source revision conflict", kind="conflict")

        worker.runtime._commit_receipt = conflict
        try:
            second = self.run_worker(worker)
        finally:
            worker.runtime._commit_receipt = original_commit

        self.assertEqual(provider.calls, 2)
        failed = next(item for item in second.items if item.record_id == record_id)
        self.assertEqual((failed.outcome, failed.error_kind), ("failed", "runtime"))
        self.assertEqual(
            second.record_runtime_statuses[record_id],
            {"status": "failed", "error_kind": "runtime"},
        )
        after = worker.actions.load_receipt_head(receipt_id)
        self.assertEqual(after.revision, 1)
        self.assertEqual(after.record_ref, before.record_ref)

    def test_invalid_provider_response_becomes_finite_failure(self) -> None:
        self.write(("10:50", "保留的内容。"))
        provider = AutoProvider(["not-json"])
        worker = self.worker(provider)
        result = self.run_worker(worker)

        self.assertEqual(result.status, "completed_with_failures")
        self.assertEqual(result.items[0].outcome, "failed")
        self.assertEqual(result.items[0].error_kind, "invalid_response")
        self.assertEqual(provider.calls, 1)

        # A new process/runtime instance must derive the same bounded retry.
        retried = self.run_worker(self.worker(provider))
        self.assertEqual(
            (retried.status, retried.items[0].outcome), ("completed", "ready")
        )
        self.assertEqual(provider.calls, 2)

        replay = self.run_worker(worker)
        self.assertEqual((replay.selected_count, replay.items[0].outcome), (0, "current"))
        self.assertEqual(provider.calls, 2)

    def test_schema_retry_is_stable_and_bounded_to_one_new_request(self) -> None:
        self.write(("10:50", "两次都返回无效合同。"))
        provider = AutoProvider(["not-json", "still-not-json"])
        worker = self.worker(provider)

        first = self.run_worker(worker)
        second = self.run_worker(worker)
        third = self.run_worker(worker)

        self.assertEqual(first.items[0].error_kind, "invalid_response")
        self.assertEqual(second.items[0].error_kind, "invalid_response")
        self.assertEqual(third.items[0].error_kind, "invalid_response")
        self.assertEqual(provider.calls, 2)
        requests = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(
                worker.runtime.files.interpretation_requests.glob("ireq_*.json")
            )
        ]
        self.assertEqual(len(requests), 2)
        self.assertEqual(
            sorted(item["trigger"] for item in requests), ["reconcile", "retry"]
        )

    def test_retry_unknown_attempt_never_creates_a_third_call(self) -> None:
        self.write(("10:50", "重试时供应商结果未知。"))
        provider = AutoProvider(
            ["not-json", RuntimeError("retry outcome unknown after send")]
        )

        first = self.run_worker(self.worker(provider))
        second = self.run_worker(self.worker(provider))
        third = self.run_worker(self.worker(provider))

        self.assertEqual(first.items[0].error_kind, "invalid_response")
        self.assertEqual(second.items[0].error_kind, "unknown_attempt")
        self.assertEqual(third.items[0].error_kind, "unknown_attempt")
        self.assertEqual(provider.calls, 2)

    def test_retry_usage_missing_never_creates_a_third_call(self) -> None:
        self.write(("10:50", "重试响应缺少 usage。"))
        missing_usage = CompletionResult(
            content="still-not-json",
            usage={},
            request_id="missing-usage",
            model="deepseek-v4-pro",
        )
        provider = AutoProvider(["not-json", missing_usage])

        first = self.run_worker(self.worker(provider))
        second = self.run_worker(self.worker(provider))
        third = self.run_worker(self.worker(provider))

        self.assertEqual(first.items[0].error_kind, "invalid_response")
        self.assertEqual(second.items[0].error_kind, "invalid_response")
        self.assertEqual(third.items[0].error_kind, "invalid_response")
        self.assertEqual(provider.calls, 2)

    def test_concurrent_retry_replay_shares_one_paid_retry(self) -> None:
        self.write(("10:50", "并发重放只允许一个重试调用。"))
        entered = threading.Event()
        release = threading.Event()

        def delayed(messages: Sequence[Mapping[str, str]]) -> dict[str, Any]:
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError("retry provider timed out")
            user = json.loads(messages[-1]["content"])
            evidence_ref = user["untrusted_data"]["source_catalog"][0]["ref_id"]
            return proposal(evidence_ref)

        provider = AutoProvider(["not-json", delayed])
        first_worker = self.worker(provider)
        self.run_worker(first_worker)
        second_worker = self.worker(provider)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(self.run_worker, first_worker)
            self.assertTrue(entered.wait(timeout=3))
            second_future = pool.submit(self.run_worker, second_worker)
            release.set()
            results = [
                first_future.result(timeout=5),
                second_future.result(timeout=5),
            ]

        self.assertEqual(provider.calls, 2)
        self.assertEqual(sorted(item.selected_count for item in results), [0, 1])
        self.assertEqual(
            sorted(result.items[0].outcome for result in results),
            ["current", "ready"],
        )

    def test_current_no_candidates_do_not_starve_the_next_record(self) -> None:
        initial_rows = tuple(
            (f"{9 + index // 6:02d}:{(index % 6) * 10:02d}", f"低信号记录 {index + 1}。")
            for index in range(16)
        )
        self.write(*initial_rows)
        provider = AutoProvider([finish_record() for _ in range(16)])
        worker = self.worker(provider)

        first = self.run_worker(worker)
        second = self.run_worker(worker)
        self.assertEqual((first.selected_count, second.selected_count), (8, 8))
        self.assertEqual(provider.calls, 16)

        all_rows = initial_rows + (("12:00", "第十七条需要真正整理。"),)
        self.write(*all_rows)
        third = self.run_worker(worker)

        self.assertEqual((third.selected_count, third.deferred_count), (1, 0))
        self.assertEqual(provider.calls, 17)
        self.assertEqual(
            [item.outcome for item in third.items].count("no_candidate"), 16
        )
        self.assertEqual(
            [item.outcome for item in third.items].count("ready"), 1
        )

    def test_unknown_provider_attempt_is_not_retried(self) -> None:
        self.write(("10:50", "供应商中断。"))
        provider = AutoProvider([RuntimeError("network ended after send")])
        worker = self.worker(provider)
        result = self.run_worker(worker)

        self.assertEqual(provider.calls, 1)
        self.assertEqual((result.items[0].outcome, result.items[0].error_kind), ("failed", "unknown_attempt"))

        replay = self.run_worker(worker)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(
            (replay.items[0].outcome, replay.items[0].error_kind),
            ("failed", "unknown_attempt"),
        )

    def test_user_edit_and_original_only_are_not_overwritten(self) -> None:
        self.write(("10:50", "先得到一条 receipt。"))
        provider = AutoProvider()
        worker = self.worker(provider)
        first = self.run_worker(worker)
        receipt_ref = worker.actions.load_receipt_head_ref(make_receipt_id(first.items[0].record_id))
        edit = CognitiveUserAction(
            COGNITIVE_SCHEMA_VERSION,
            "memento_cognitive_user_action",
            make_cognitive_action_id("worker-user-edit"),
            NOW.isoformat(timespec="seconds"),
            "edit_receipt",
            receipt_ref,
            edit_payload(),
        )
        worker.actions.submit_action(edit)

        edited = self.run_worker(worker)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(edited.items[0].outcome, "current")
        edited_ref = worker.actions.load_receipt_head_ref(receipt_ref.id)
        self.assertEqual(edited_ref.revision, 2)

        original = CognitiveUserAction(
            COGNITIVE_SCHEMA_VERSION,
            "memento_cognitive_user_action",
            make_cognitive_action_id("worker-original-only"),
            (NOW + dt.timedelta(minutes=1)).isoformat(timespec="seconds"),
            "original_only",
            edited_ref,
            None,
        )
        worker.actions.submit_action(original)
        terminal = self.run_worker(worker)

        self.assertEqual(provider.calls, 1)
        self.assertEqual(terminal.items[0].outcome, "original_only")
        head = worker.actions.load_receipt_head(receipt_ref.id)
        self.assertEqual((head.status, head.operation, head.revision), ("original_only", "original_only", 3))

        self.write(("10:50", "即使原文修改，original_only 仍为终态。"))
        replay = self.run_worker(worker)
        self.assertEqual(provider.calls, 1)
        self.assertEqual((replay.selected_count, replay.items[0].outcome), (0, "original_only"))

    def test_tombstone_receipt_is_permanently_skipped(self) -> None:
        self.write(("10:50", "形成后被终止的 receipt。"))
        provider = AutoProvider()
        worker = self.worker(provider)
        first = self.run_worker(worker)
        receipt_id = make_receipt_id(first.items[0].record_id)
        head = worker.actions.load_receipt_head(receipt_id)
        head_ref = worker.actions.load_receipt_head_ref(receipt_id)
        tombstone = dataclasses.replace(
            head,
            revision=2,
            status="tombstone",
            operation="tombstone",
            created_at=(NOW + dt.timedelta(minutes=1)).isoformat(timespec="seconds"),
            user_action_id=make_cognitive_action_id("worker-receipt-tombstone"),
            summary=None,
            facets={},
            memory_candidates=(),
            relation_candidates=(),
            source_spans=(),
            previous_revision_sha256=head.sha256,
        )
        worker.actions.commit_user_receipt_revision(tombstone, expected_ref=head_ref)

        self.write(("10:50", "即使原文修改，tombstone 仍为终态。"))
        result = self.run_worker(worker)

        self.assertEqual(provider.calls, 1)
        self.assertEqual((result.selected_count, result.items[0].outcome), (0, "tombstone"))
        terminal = worker.actions.load_receipt_head(receipt_id)
        self.assertEqual((terminal.status, terminal.revision), ("tombstone", 2))

    def test_source_delete_does_not_reinterpret_or_revive_receipt(self) -> None:
        self.write(("10:50", "随后删除的记录。"))
        provider = AutoProvider()
        worker = self.worker(provider)
        first = self.run_worker(worker)
        receipt_id = make_receipt_id(first.items[0].record_id)

        self.write()
        second = self.run_worker(worker)

        self.assertEqual((second.status, second.selected_count, provider.calls), ("no_records", 0, 1))
        self.assertEqual(worker.actions.load_receipt_head(receipt_id).revision, 1)

    def test_concurrent_runs_share_one_provider_call(self) -> None:
        self.write(("10:50", "并发只处理一次。"))
        provider = BlockingProvider()
        first_worker = self.worker(provider)
        second_runtime = CognitiveRuntime(
            self.vault,
            provider,
            lock_factory=None,
            clock=lambda: NOW,
        )
        second_worker = CognitiveRecordWorker(
            self.vault,
            runtime=second_runtime,
            clock=lambda: NOW,
        )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(self.run_worker, first_worker)
            self.assertTrue(provider.entered.wait(timeout=3))
            second_future = pool.submit(self.run_worker, second_worker)
            provider.release.set()
            results = [first_future.result(timeout=5), second_future.result(timeout=5)]

        self.assertEqual(provider.calls, 1)
        self.assertEqual(sorted(item.selected_count for item in results), [0, 1])
        self.assertEqual(
            sorted(result.items[0].outcome for result in results),
            ["current", "ready"],
        )


class BoundaryTests(WorkerCase):
    def test_mismatched_source_file_is_rejected_before_any_provider_call(self) -> None:
        self.write(("10:50", "边界测试。"))
        provider = AutoProvider()
        worker = self.worker(provider)
        with self.assertRaises(ContractError) as captured:
            worker.run(local_date=LOCAL_DATE, source_file="../2026-08-18.md")
        self.assertEqual(captured.exception.kind, "evidence")
        self.assertEqual(provider.calls, 0)

    def test_symlink_day_lock_fails_closed(self) -> None:
        self.write(("10:50", "边界测试。"))
        provider = AutoProvider()
        worker = self.worker(provider)
        target = worker.root / "locks" / "attacker.lock"
        target.write_text("x", encoding="utf-8")
        target.chmod(0o600)
        lock = worker.root / "locks" / f"record-worker-{LOCAL_DATE}.lock"
        lock.symlink_to(target)

        with self.assertRaises(ContractError) as captured:
            self.run_worker(worker)
        self.assertEqual(captured.exception.kind, "evidence")
        self.assertEqual(provider.calls, 0)

    def test_hardlinked_day_lock_fails_closed(self) -> None:
        self.write(("10:50", "边界测试。"))
        provider = AutoProvider()
        worker = self.worker(provider)
        target = worker.root / "locks" / "attacker.lock"
        target.write_text("x", encoding="utf-8")
        target.chmod(0o600)
        lock = worker.root / "locks" / f"record-worker-{LOCAL_DATE}.lock"
        os.link(target, lock)

        with self.assertRaises(ContractError) as captured:
            self.run_worker(worker)
        self.assertEqual(captured.exception.kind, "evidence")
        self.assertEqual(provider.calls, 0)

    def test_foreign_owner_day_lock_fails_closed(self) -> None:
        self.write(("10:50", "边界测试。"))
        provider = AutoProvider()
        worker = self.worker(provider)
        real_fstat = os.fstat

        def foreign_owner(descriptor):
            details = real_fstat(descriptor)
            return types.SimpleNamespace(
                st_mode=details.st_mode,
                st_nlink=details.st_nlink,
                st_uid=os.getuid() + 1,
                st_dev=details.st_dev,
                st_ino=details.st_ino,
            )

        with mock.patch("cognitive_record_worker_v1.os.fstat", side_effect=foreign_owner):
            with self.assertRaises(ContractError) as captured:
                self.run_worker(worker)
        self.assertEqual(captured.exception.kind, "evidence")
        self.assertEqual(provider.calls, 0)


if __name__ == "__main__":
    unittest.main()
