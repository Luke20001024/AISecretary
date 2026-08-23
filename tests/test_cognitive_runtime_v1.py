#!/usr/bin/env python3
"""Durability and fail-closed tests for Cognitive Secretary runtimes."""

from __future__ import annotations

import datetime as dt
import copy
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_AGENT = ROOT / "context-agent"
if str(CONTEXT_AGENT) not in sys.path:
    sys.path.insert(0, str(CONTEXT_AGENT))

from cognitive_runtime_v1 import (  # noqa: E402
    CognitiveRuntime,
    make_evidence_ref_id,
    make_object_ref_id,
)
from agent_v1 import build_agent_profile  # noqa: E402
from cognitive_actions_v1 import CognitiveActionStore  # noqa: E402
from cognitive_store_v1 import RecordStore  # noqa: E402
from cognitive_v1 import (  # noqa: E402
    COGNITIVE_SCHEMA_VERSION,
    CognitiveUserAction,
    ObjectRef,
    SourceSpan,
    make_cognitive_action_id,
    persisted_sha256,
)
from core import ContractError  # noqa: E402
from deepseek_provider import CompletionResult  # noqa: E402


NOW = dt.datetime(2026, 8, 18, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
DAY = "2026-08-18.md"
USAGE = {
    "prompt_tokens": 10,
    "completion_tokens": 5,
    "total_tokens": 15,
    "prompt_cache_hit_tokens": 0,
    "prompt_cache_miss_tokens": 10,
    "completion_tokens_details": {"reasoning_tokens": 0},
}


class FakeProvider:
    def __init__(self, replies: Sequence[Any]) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.messages: list[list[Mapping[str, str]]] = []

    def complete(self, messages: Sequence[Mapping[str, str]]) -> CompletionResult:
        self.calls += 1
        self.messages.append(list(messages))
        if not self.replies:
            raise AssertionError("unexpected provider call")
        item = self.replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item = item(messages)
        if isinstance(item, CompletionResult):
            return item
        return CompletionResult(
            content=json.dumps(item, ensure_ascii=False),
            usage=USAGE,
            request_id=f"fake-{self.calls}",
            model="deepseek-v4-pro",
        )


def completion(action: Mapping[str, Any], *, usage: Mapping[str, Any] = USAGE) -> CompletionResult:
    return CompletionResult(
        content=json.dumps(action, ensure_ascii=False),
        usage=usage,
        request_id="fake-request",
        model="deepseek-v4-pro",
    )


def record_proposal(eref: str) -> dict[str, Any]:
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
            "memory_candidates": [
                {
                    "statement": "评审前先定义最早可验证部分。",
                    "memory_kind": "observation",
                    "topics": ["产品设计"],
                    "purposes": ["future_decision"],
                    "uncertainty": "medium",
                    "source_ref_ids": [eref],
                }
            ],
            "relation_candidates": [],
            "source_ref_ids": [eref],
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


def finish_daily_insufficient() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "finish",
        "reason_code": "insufficient_evidence",
        "arguments": {"reason": "insufficient_evidence"},
    }


def inspect_action(oref: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "inspect_memory",
        "reason_code": "need_target_context",
        "arguments": {"memory_ref_id": oref},
    }


def search_action() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "search_history",
        "reason_code": "need_counterexample",
        "arguments": {
            "query": "方案评审",
            "date_from": None,
            "date_to": None,
            "limit": 1,
        },
    }


def daily_proposal(eref: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "propose_daily_bundle",
        "reason_code": "bundle_ready",
        "arguments": {
            "overview": "今天反复回到最早验证。",
            "themes": ["最早验证"],
            "changes": ["开始缩短反馈链路。"],
            "unresolved_questions": [],
            "action_clues": ["下次先发可验证草稿。"],
            "memory_operations": [
                {
                    "operation": "new",
                    "target_memory_ref_id": None,
                    "statement": "评审前先定义最早可验证部分。",
                    "memory_kind": "observation",
                    "topics": ["产品设计"],
                    "purposes": ["future_decision"],
                    "uncertainty": "medium",
                    "source_ref_ids": [eref],
                }
            ],
            "relation_operations": [],
            "material_change": True,
        },
    }


class RuntimeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="memento-cognitive-runtime-")
        self.vault = Path(self.temporary.name) / "vault"
        self.vault.mkdir(mode=0o700)
        (self.vault / "assets").mkdir(mode=0o700)
        self.day = self.vault / DAY
        self.day.write_text(
            "---\ndate: 2026-08-18\ntype: memento-daily\n---\n\n"
            "## 10:50 · 周二 · Chrome\n\n"
            "我每次想把方案想完整再发。\n"
            "真实反馈反而来得太晚。\n\n---\n",
            encoding="utf-8",
        )
        self.day.chmod(0o600)
        self.store = RecordStore(self.vault)
        result = self.store.reconcile_day(DAY, now=NOW)
        self.record_id = result.created_record_ids[0]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def runtime(self, provider: FakeProvider, **kwargs: Any) -> CognitiveRuntime:
        return CognitiveRuntime(
            self.vault,
            provider,
            clock=lambda: NOW,
            **kwargs,
        )

    def evidence(self, runtime: CognitiveRuntime) -> list[dict[str, Any]]:
        return runtime.materialize_record_evidence(self.record_id)

    def ready_daily_material(
        self, runtime: CognitiveRuntime
    ) -> tuple[tuple[SourceSpan, ...], ObjectRef, str, str]:
        evidence = self.evidence(runtime)
        runtime.provider.replies.append(
            completion(record_proposal(evidence[0]["ref_id"]))
        )
        request = runtime.create_interpretation_request(self.record_id)
        interpreted = runtime.run_interpretation(request["id"])
        self.assertEqual(interpreted["status"], "completed")
        spans = tuple(
            SourceSpan.from_dict(raw)
            for raw in interpreted["receipt"]["source_spans"]
        )
        receipt_ref = ObjectRef.from_dict(interpreted["run"]["receipt_ref"])
        profile_sha = build_agent_profile(self.vault)["profile_sha256"]
        action_sha = CognitiveActionStore(
            self.vault, state_root=runtime.files.root
        ).action_watermark()[1]
        return spans, receipt_ref, profile_sha, action_sha

    def test_record_success_receipt_exact_evidence_and_zero_call_cache(self) -> None:
        provider = FakeProvider([])
        runtime = self.runtime(provider)
        eref = self.evidence(runtime)[0]["ref_id"]
        provider.replies.append(completion(record_proposal(eref)))
        request = runtime.create_interpretation_request(self.record_id)
        result = runtime.run_interpretation(request["id"])

        self.assertEqual(result["status"], "completed")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result["receipt"]["source_spans"][0]["quote"], "我每次想把方案想完整再发。")
        self.assertTrue(result["run"]["receipt_ref"]["revision_sha256"])
        completion_files = list(runtime.files.interpretation_runs.glob("*.completion.json"))
        self.assertEqual(len(completion_files), 1)

        second_request = runtime.create_interpretation_request(
            self.record_id, trigger="reconcile"
        )
        cached = runtime.run_interpretation(second_request["id"])
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["status"], "completed")
        self.assertEqual(provider.calls, 1)

    def test_record_finish_writes_no_receipt(self) -> None:
        provider = FakeProvider([completion(finish_record())])
        runtime = self.runtime(provider)
        request = runtime.create_interpretation_request(self.record_id)
        result = runtime.run_interpretation(request["id"])
        self.assertEqual(result["status"], "no_candidate")
        self.assertIsNone(result["receipt"])
        self.assertEqual(list(runtime.files.receipts.glob("*.json")), [])

    def test_invalid_json_and_forged_ref_fail_closed(self) -> None:
        provider = FakeProvider(
            [
                CompletionResult("not-json", USAGE, "bad-json", "deepseek-v4-pro"),
            ]
        )
        runtime = self.runtime(provider)
        first = runtime.create_interpretation_request(self.record_id)
        invalid = runtime.run_interpretation(first["id"])
        self.assertEqual(invalid["status"], "error")
        self.assertEqual(list(runtime.files.receipts.glob("*.json")), [])

        second = runtime.create_interpretation_request(
            self.record_id, trigger="retry", request_nonce="forged"
        )
        forged = runtime.run_interpretation(second["id"])
        self.assertEqual(forged["status"], "error")
        self.assertEqual(
            forged["run"]["error_kind"], "material_attempt_blocked"
        )
        self.assertTrue(forged["cached"])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(list(runtime.files.receipts.glob("*.json")), [])

    def test_known_schema_retry_is_persisted_deterministic_and_bounded(self) -> None:
        provider = FakeProvider(
            [
                CompletionResult(
                    "not-json", USAGE, "invalid-original", "deepseek-v4-pro"
                ),
                CompletionResult(
                    "still-not-json", USAGE, "invalid-retry", "deepseek-v4-pro"
                ),
            ]
        )
        runtime = self.runtime(provider)
        request = runtime.create_interpretation_request(
            self.record_id, trigger="reconcile"
        )
        first = runtime.run_interpretation(request["id"])

        self.assertFalse(first["cached"])
        self.assertIsNone(runtime.create_known_invalid_retry_request(first))
        self.assertEqual(provider.calls, 1)

        surfaced = runtime.run_interpretation(request["id"])
        self.assertTrue(surfaced["cached"])
        self.assertTrue(
            runtime.is_interpretation_schema_retry_eligible(
                request["id"], observed_cached=True
            )
        )
        retry = runtime.create_known_invalid_retry_request(surfaced)
        repeated = runtime.create_known_invalid_retry_request(surfaced)
        self.assertIsNotNone(retry)
        self.assertEqual(retry, repeated)
        self.assertEqual(retry["trigger"], "retry")
        self.assertEqual(retry["record_ref"], request["record_ref"])

        retry_result = runtime.run_interpretation(retry["id"])
        self.assertEqual(retry_result["run"]["error_kind"], "schema")
        retry_cached = runtime.run_interpretation(retry["id"])
        self.assertTrue(retry_cached["cached"])
        self.assertIsNone(runtime.create_known_invalid_retry_request(retry_cached))

        # Replaying the original authorization resolves to the same terminal
        # retry request and cannot create another Provider attempt.
        same_retry = runtime.create_known_invalid_retry_request(surfaced)
        self.assertEqual(same_retry["id"], retry["id"])
        runtime.run_interpretation(same_retry["id"])
        self.assertEqual(provider.calls, 2)
        requests = list(runtime.files.interpretation_requests.glob("ireq_*.json"))
        self.assertEqual(len(requests), 2)

    def test_arbitrary_request_nonces_cannot_consume_or_extend_schema_retry(self) -> None:
        provider = FakeProvider(
            [
                CompletionResult(
                    "invalid-original", USAGE, "invalid-original", "deepseek-v4-pro"
                ),
                CompletionResult(
                    "invalid-bounded-retry",
                    USAGE,
                    "invalid-bounded-retry",
                    "deepseek-v4-pro",
                ),
            ]
        )
        runtime = self.runtime(provider)
        original = runtime.create_interpretation_request(
            self.record_id, trigger="reconcile"
        )
        failed = runtime.run_interpretation(original["id"])
        self.assertEqual(failed["run"]["error_kind"], "schema")
        self.assertEqual(provider.calls, 1)

        for nonce in ("arbitrary-a", "arbitrary-b"):
            request = runtime.create_interpretation_request(
                self.record_id, trigger="retry", request_nonce=nonce
            )
            blocked = runtime.run_interpretation(request["id"])
            self.assertEqual(blocked["status"], "error")
            self.assertEqual(
                blocked["run"]["error_kind"], "material_attempt_blocked"
            )
            self.assertTrue(blocked["cached"])
        self.assertEqual(provider.calls, 1)

        surfaced = runtime.run_interpretation(original["id"])
        bounded = runtime.create_known_invalid_retry_request(surfaced)
        self.assertIsNotNone(bounded)
        bounded_result = runtime.run_interpretation(bounded["id"])
        self.assertEqual(bounded_result["run"]["error_kind"], "schema")
        self.assertEqual(provider.calls, 2)

        third = runtime.create_interpretation_request(
            self.record_id, trigger="retry", request_nonce="arbitrary-c"
        )
        third_result = runtime.run_interpretation(third["id"])
        self.assertEqual(
            third_result["run"]["error_kind"], "material_attempt_blocked"
        )
        self.assertEqual(provider.calls, 2)

    def test_unknown_material_attempt_blocks_different_request_nonce(self) -> None:
        provider = FakeProvider([RuntimeError("connection outcome unknown")])
        runtime = self.runtime(provider)
        original = runtime.create_interpretation_request(
            self.record_id, trigger="reconcile"
        )
        with self.assertRaisesRegex(RuntimeError, "outcome unknown"):
            runtime.run_interpretation(original["id"])
        self.assertEqual(provider.calls, 1)

        different = runtime.create_interpretation_request(
            self.record_id, trigger="retry", request_nonce="different-after-unknown"
        )
        blocked = runtime.run_interpretation(different["id"])
        self.assertEqual(blocked["status"], "error")
        self.assertEqual(
            blocked["run"]["error_kind"], "material_attempt_blocked"
        )
        self.assertEqual(provider.calls, 1)

        recovered = runtime.run_interpretation(original["id"])
        self.assertEqual(recovered["run"]["error_kind"], "unknown_attempt")
        self.assertEqual(provider.calls, 1)

    def test_concurrent_unknown_attempt_blocks_second_material_request(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def fail_after_enter(_: Sequence[Mapping[str, str]]) -> CompletionResult:
            entered.set()
            if not release.wait(timeout=2):
                raise AssertionError("test did not release provider")
            raise RuntimeError("connection outcome unknown")

        provider = FakeProvider([fail_after_enter])
        runtime = self.runtime(provider)
        first = runtime.create_interpretation_request(
            self.record_id, trigger="reconcile"
        )
        second = runtime.create_interpretation_request(
            self.record_id, trigger="retry", request_nonce="parallel-unknown"
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(runtime.run_interpretation, first["id"])
            self.assertTrue(entered.wait(timeout=2))
            second_future = executor.submit(runtime.run_interpretation, second["id"])
            release.set()
            with self.assertRaisesRegex(RuntimeError, "outcome unknown"):
                first_future.result(timeout=5)
            blocked = second_future.result(timeout=5)

        self.assertEqual(blocked["status"], "error")
        self.assertEqual(
            blocked["run"]["error_kind"], "material_attempt_blocked"
        )
        self.assertEqual(provider.calls, 1)

    def test_schema_retry_rejects_noncanonical_persisted_attempt_shapes(self) -> None:
        provider = FakeProvider(
            [CompletionResult("not-json", USAGE, "invalid", "deepseek-v4-pro")]
        )
        runtime = self.runtime(provider)
        request = runtime.create_interpretation_request(self.record_id)
        terminal = runtime.run_interpretation(request["id"])
        runtime.run_interpretation(request["id"])
        run_path = runtime.files.interpretation_runs / (
            terminal["run"]["run_id"] + ".json"
        )
        original = runtime.files.read_json(run_path, name="test schema run")

        mutations = {
            "two model calls": lambda value: value["usage"].__setitem__(
                "model_calls", 2
            ),
            "attempt result count": lambda value: value["steps"][0].__setitem__(
                "result_count", 0
            ),
            "invalid result count": lambda value: value["steps"][1].__setitem__(
                "result_count", 1
            ),
            "different turns": lambda value: value["steps"][1].__setitem__(
                "turn", 2
            ),
            "extra failure step": lambda value: value["steps"].append(
                {
                    **value["steps"][1],
                    "action": "finish",
                    "reason_code": "extra_failure",
                }
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                changed = copy.deepcopy(original)
                mutate(changed)
                runtime.files.write_mutable(run_path, changed)
                self.assertFalse(
                    runtime.is_interpretation_schema_retry_eligible(
                        request["id"], observed_cached=True
                    )
                )
                runtime.files.write_mutable(run_path, original)

    def test_schema_retry_verifies_completion_sidecar_and_usage_binding(self) -> None:
        provider = FakeProvider(
            [CompletionResult("not-json", USAGE, "invalid", "deepseek-v4-pro")]
        )
        runtime = self.runtime(provider)
        request = runtime.create_interpretation_request(self.record_id)
        terminal = runtime.run_interpretation(request["id"])
        runtime.run_interpretation(request["id"])
        run_path = runtime.files.interpretation_runs / (
            terminal["run"]["run_id"] + ".json"
        )
        original = runtime.files.read_json(run_path, name="test schema run")

        mismatched_usage = copy.deepcopy(original)
        mismatched_usage["usage"]["prompt_tokens"] += 1
        runtime.files.write_mutable(run_path, mismatched_usage)
        with self.assertRaises(ContractError) as usage_error:
            runtime.is_interpretation_schema_retry_eligible(
                request["id"], observed_cached=True
            )
        self.assertEqual(usage_error.exception.kind, "evidence")

        mismatched_action = copy.deepcopy(original)
        mismatched_action["steps"][1]["arguments_sha256"] = "f" * 64
        runtime.files.write_mutable(run_path, mismatched_action)
        with self.assertRaises(ContractError) as action_error:
            runtime.is_interpretation_schema_retry_eligible(
                request["id"], observed_cached=True
            )
        self.assertEqual(action_error.exception.kind, "evidence")

        runtime.files.write_mutable(run_path, original)
        sidecar = runtime._completion_path(run_path, terminal["run"]["run_id"], 1)
        sidecar.unlink()
        with self.assertRaises(ContractError) as missing_error:
            runtime.is_interpretation_schema_retry_eligible(
                request["id"], observed_cached=True
            )
        self.assertEqual(missing_error.exception.kind, "evidence")

    def test_schema_retry_fails_closed_when_source_changes_around_creation(self) -> None:
        provider = FakeProvider(
            [CompletionResult("not-json", USAGE, "invalid", "deepseek-v4-pro")]
        )
        runtime = self.runtime(provider)
        request = runtime.create_interpretation_request(self.record_id)
        runtime.run_interpretation(request["id"])
        surfaced = runtime.run_interpretation(request["id"])
        self.assertTrue(
            runtime.is_interpretation_schema_retry_eligible(
                request["id"], observed_cached=True
            )
        )
        retry = runtime.create_known_invalid_retry_request(surfaced)
        self.assertIsNotNone(retry)

        self.day.write_text(
            self.day.read_text(encoding="utf-8").replace(
                "真实反馈反而来得太晚。", "真实反馈已经改变。"
            ),
            encoding="utf-8",
        )
        self.store.reconcile_day(DAY, now=NOW + dt.timedelta(minutes=1))

        self.assertIsNone(runtime.create_known_invalid_retry_request(surfaced))
        stale = runtime.run_interpretation(retry["id"])
        self.assertEqual((stale["status"], stale["run"]["error_kind"]), ("stale", "stale"))
        self.assertEqual(provider.calls, 1)

    def test_retry_request_stales_before_call_when_action_watermark_changes(self) -> None:
        provider = FakeProvider(
            [CompletionResult("not-json", USAGE, "invalid", "deepseek-v4-pro")]
        )
        runtime = self.runtime(provider)
        request = runtime.create_interpretation_request(self.record_id)
        runtime.run_interpretation(request["id"])
        surfaced = runtime.run_interpretation(request["id"])
        retry = runtime.create_known_invalid_retry_request(surfaced)
        self.assertIsNotNone(retry)

        action_store = CognitiveActionStore(
            self.vault, state_root=runtime.files.root
        )
        action_store.submit_action(
            CognitiveUserAction(
                COGNITIVE_SCHEMA_VERSION,
                "memento_cognitive_user_action",
                make_cognitive_action_id("runtime-retry-watermark-race"),
                NOW.isoformat(timespec="seconds"),
                "confirm_receipt",
                ObjectRef(
                    kind="interpretation_receipt",
                    id="rcp_" + "a" * 24,
                    revision=1,
                    revision_sha256="b" * 64,
                ),
                None,
            )
        )

        self.assertIsNone(runtime.create_known_invalid_retry_request(surfaced))
        stale = runtime.run_interpretation(retry["id"])
        self.assertEqual((stale["status"], stale["run"]["error_kind"]), ("stale", "stale"))
        self.assertEqual(provider.calls, 1)

    def test_every_trigger_rejects_stale_action_watermark_before_paid_call(self) -> None:
        provider = FakeProvider([])
        runtime = self.runtime(provider)

        for index, trigger in enumerate(("capture", "reconcile", "source_changed"), start=1):
            request = runtime.create_interpretation_request(
                self.record_id,
                trigger=trigger,
                feedback_watermark_sha256=f"{index}" * 64,
                request_nonce=f"stale-{trigger}",
            )
            stale = runtime.run_interpretation(request["id"])
            self.assertEqual(
                (stale["status"], stale["run"]["error_kind"]),
                ("stale", "stale"),
            )
        self.assertEqual(provider.calls, 0)

        current_request = runtime.create_interpretation_request(
            self.record_id,
            trigger="capture",
            request_nonce="action-race",
        )
        action_store = CognitiveActionStore(
            self.vault, state_root=runtime.files.root
        )
        action_store.submit_action(
            CognitiveUserAction(
                COGNITIVE_SCHEMA_VERSION,
                "memento_cognitive_user_action",
                make_cognitive_action_id("runtime-all-trigger-watermark-race"),
                NOW.isoformat(timespec="seconds"),
                "confirm_receipt",
                ObjectRef(
                    kind="interpretation_receipt",
                    id="rcp_" + "c" * 24,
                    revision=1,
                    revision_sha256="d" * 64,
                ),
                None,
            )
        )
        raced = runtime.run_interpretation(current_request["id"])
        self.assertEqual(
            (raced["status"], raced["run"]["error_kind"]),
            ("stale", "stale"),
        )
        self.assertEqual(provider.calls, 0)

    def test_current_no_candidate_requires_bound_request_run_and_sidecar(self) -> None:
        provider = FakeProvider([completion(finish_record(), usage={})])
        runtime = self.runtime(provider)
        request = runtime.create_interpretation_request(self.record_id)
        terminal = runtime.run_interpretation(request["id"])
        watermark = runtime._current_action_watermark()

        current = runtime.get_current_interpretation_terminal(
            self.record_id, feedback_watermark_sha256=watermark
        )
        self.assertEqual(current["status"], "no_candidate")
        self.assertEqual(current["run_id"], terminal["run"]["run_id"])
        self.assertEqual(provider.calls, 1)

        sidecar = next(runtime.files.interpretation_runs.glob("*.completion.json"))
        persisted_sidecar = runtime.files.read_json(
            sidecar, name="test no-candidate sidecar"
        )
        sidecar.unlink()
        with self.assertRaises(ContractError) as missing_sidecar:
            runtime.get_current_interpretation_terminal(
                self.record_id, feedback_watermark_sha256=watermark
            )
        self.assertEqual(missing_sidecar.exception.kind, "evidence")
        runtime.files.write_immutable(sidecar, persisted_sidecar)

        self.day.write_text(
            self.day.read_text(encoding="utf-8").replace(
                "真实反馈反而来得太晚。", "真实反馈已经改变。"
            ),
            encoding="utf-8",
        )
        self.store.reconcile_day(DAY, now=NOW + dt.timedelta(minutes=1))
        self.assertIsNone(
            runtime.get_current_interpretation_terminal(
                self.record_id, feedback_watermark_sha256=watermark
            )
        )
        self.assertEqual(provider.calls, 1)

    def test_usage_missing_fails_closed_after_completion_persisted(self) -> None:
        provider = FakeProvider([])
        runtime = self.runtime(provider)
        eref = self.evidence(runtime)[0]["ref_id"]
        provider.replies.append(completion(record_proposal(eref), usage={}))
        request = runtime.create_interpretation_request(self.record_id)
        result = runtime.run_interpretation(request["id"])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["run"]["error_kind"], "usage_missing")
        self.assertEqual(
            result["run"]["usage"],
            {
                "model_calls": 1,
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "reasoning_tokens": None,
                "prompt_cache_hit_tokens": None,
                "prompt_cache_miss_tokens": None,
                "usage_missing": True,
                "cost_usd": None,
                "cost_complete": False,
            },
        )
        self.assertEqual(list(runtime.files.receipts.glob("*.json")), [])
        sidecar = next(runtime.files.interpretation_runs.glob("*.completion.json"))
        self.assertTrue(json.loads(sidecar.read_text())["usage_missing"])
        replay = runtime.run_interpretation(request["id"])
        self.assertIsNone(runtime.create_known_invalid_retry_request(replay))
        self.assertEqual(provider.calls, 1)

    def test_unknown_crash_recovery_never_retries_same_request(self) -> None:
        provider = FakeProvider([RuntimeError("connection outcome unknown")])
        runtime = self.runtime(provider)
        request = runtime.create_interpretation_request(self.record_id)
        with self.assertRaises(RuntimeError):
            runtime.run_interpretation(request["id"])
        self.assertEqual(provider.calls, 1)

        recovered = runtime.run_interpretation(request["id"])
        self.assertEqual(recovered["status"], "error")
        self.assertEqual(recovered["run"]["error_kind"], "unknown_attempt")
        self.assertIsNone(runtime.create_known_invalid_retry_request(recovered))
        self.assertEqual(provider.calls, 1)

    def test_persisted_completion_repairs_without_second_call(self) -> None:
        provider = FakeProvider([])
        runtime = self.runtime(provider)
        eref = self.evidence(runtime)[0]["ref_id"]
        provider.replies.append(completion(record_proposal(eref)))
        request = runtime.create_interpretation_request(self.record_id)
        original = runtime._apply_completion

        def crash_after_sidecar(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("crash after durable completion")

        runtime._apply_completion = crash_after_sidecar  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError):
            runtime.run_interpretation(request["id"])
        self.assertEqual(provider.calls, 1)
        runtime._apply_completion = original  # type: ignore[method-assign]

        repaired = runtime.run_interpretation(request["id"])
        self.assertEqual(repaired["status"], "completed")
        self.assertEqual(provider.calls, 1)

    def test_invalid_completion_recovery_keeps_only_hash_and_never_retries(self) -> None:
        secret = "API_KEY=SENSITIVE_PLACEHOLDER_DO_NOT_PERSIST\n私人地址 123"
        provider = FakeProvider(
            [CompletionResult(secret, USAGE, "bad-action", "deepseek-v4-pro")]
        )
        runtime = self.runtime(provider)
        request = runtime.create_interpretation_request(self.record_id)
        original = runtime._apply_completion

        def crash_after_safe_sidecar(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("crash after safe rejection sidecar")

        runtime._apply_completion = crash_after_safe_sidecar  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError):
            runtime.run_interpretation(request["id"])
        self.assertEqual(provider.calls, 1)
        sidecar = next(runtime.files.interpretation_runs.glob("*.completion.json"))
        persisted = sidecar.read_text(encoding="utf-8")
        self.assertNotIn("SENSITIVE_PLACEHOLDER_DO_NOT_PERSIST", persisted)
        self.assertNotIn("私人地址", persisted)
        safe = json.loads(persisted)
        self.assertIsNone(safe["content"])
        self.assertTrue(safe["content_sha256"])
        self.assertTrue(safe["validation_error_kind"])

        runtime._apply_completion = original  # type: ignore[method-assign]
        recovered = runtime.run_interpretation(request["id"])
        self.assertEqual(recovered["status"], "error")
        self.assertEqual(provider.calls, 1)

    def test_lock_factory_failure_is_resolved_not_unknown(self) -> None:
        provider = FakeProvider([])

        def broken_lock(_: Path) -> Any:
            raise RuntimeError("cannot acquire lock")

        runtime = self.runtime(provider, lock_factory=broken_lock)
        request = runtime.create_interpretation_request(self.record_id)
        with self.assertRaises(ContractError):
            runtime.run_interpretation(request["id"])
        self.assertEqual(provider.calls, 0)

        recovered = runtime.run_interpretation(request["id"])
        self.assertEqual(recovered["status"], "error")
        self.assertEqual(recovered["run"]["error_kind"], "provider_lock")
        self.assertIsNone(runtime.create_known_invalid_retry_request(recovered))
        self.assertEqual(provider.calls, 0)

    def test_source_cas_stales_result_before_receipt(self) -> None:
        runtime_holder: dict[str, CognitiveRuntime] = {}

        def mutate(_: Sequence[Mapping[str, str]]) -> CompletionResult:
            runtime = runtime_holder["runtime"]
            eref = runtime.materialize_record_evidence(self.record_id)[0]["ref_id"]
            self.day.write_text(self.day.read_text().replace("真实反馈反而来得太晚。", "真实反馈已经变了。"))
            return completion(record_proposal(eref))

        provider = FakeProvider([mutate])
        runtime = self.runtime(provider)
        runtime_holder["runtime"] = runtime
        request = runtime.create_interpretation_request(self.record_id)
        result = runtime.run_interpretation(request["id"])
        self.assertEqual(result["status"], "stale")
        self.assertEqual(list(runtime.files.receipts.glob("*.json")), [])
        replay = runtime.run_interpretation(request["id"])
        self.assertIsNone(runtime.create_known_invalid_retry_request(replay))
        self.assertEqual(provider.calls, 1)

    def test_completed_request_remains_terminal_after_later_source_edit(self) -> None:
        provider = FakeProvider([])
        runtime = self.runtime(provider)
        eref = self.evidence(runtime)[0]["ref_id"]
        provider.replies.append(completion(record_proposal(eref)))
        request = runtime.create_interpretation_request(self.record_id)
        completed = runtime.run_interpretation(request["id"])
        self.assertEqual(completed["status"], "completed")

        self.day.write_text(
            self.day.read_text().replace(
                "真实反馈反而来得太晚。", "真实反馈已经发生变化。"
            ),
            encoding="utf-8",
        )
        self.store.reconcile_day(DAY, now=NOW + dt.timedelta(minutes=1))
        replayed = runtime.run_interpretation(request["id"])
        self.assertEqual(replayed["status"], "completed")
        self.assertTrue(replayed["cached"])
        self.assertEqual(provider.calls, 1)

    def test_material_cache_key_binds_authorized_objects_and_daily_context(self) -> None:
        payloads = {
            "rmem_" + "3" * 24: {"statement": "先验证", "revision": 1},
            "rmem_" + "4" * 24: {"statement": "先完整", "revision": 1},
        }
        refs = [
            ObjectRef(
                kind="reusable_memory", id=object_id, revision=1,
                revision_sha256=persisted_sha256(payload),
            )
            for object_id, payload in payloads.items()
        ]
        provider = FakeProvider([])
        runtime = self.runtime(
            provider, object_resolver=lambda ref: payloads[ref.id]
        )
        eref = self.evidence(runtime)[0]["ref_id"]
        provider.replies.extend(
            [completion(record_proposal(eref)), completion(record_proposal(eref))]
        )
        first = runtime.create_interpretation_request(
            self.record_id, trigger="retry", request_nonce="object-a"
        )
        second = runtime.create_interpretation_request(
            self.record_id, trigger="retry", request_nonce="object-b"
        )
        runtime.run_interpretation(first["id"], target_objects=[refs[0]])
        runtime.run_interpretation(second["id"], target_objects=[refs[1]])
        self.assertEqual(provider.calls, 2)

        span = SourceSpan.from_dict(self.evidence(runtime)[0]["span"])
        daily_provider = FakeProvider(
            [completion(finish_daily()), completion(finish_daily())]
        )
        daily_runtime = self.runtime(daily_provider)
        daily_request = daily_runtime.create_daily_request("2026-08-18")
        daily_runtime.run_daily(
            daily_request["id"], source_spans=[span], daily_context={"focus": "A"}
        )
        daily_runtime.run_daily(
            daily_request["id"], source_spans=[span], daily_context={"focus": "B"}
        )
        self.assertEqual(daily_provider.calls, 2)

    def test_concurrent_requests_share_material_run_key_at_most_once(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        provider = FakeProvider([])
        runtime = self.runtime(provider)
        eref = self.evidence(runtime)[0]["ref_id"]

        def delayed(_: Sequence[Mapping[str, str]]) -> CompletionResult:
            entered.set()
            if not release.wait(timeout=2):
                raise AssertionError("test did not release provider")
            return completion(record_proposal(eref))

        provider.replies.append(delayed)
        first = runtime.create_interpretation_request(
            self.record_id, trigger="retry", request_nonce="parallel-a"
        )
        second = runtime.create_interpretation_request(
            self.record_id, trigger="retry", request_nonce="parallel-b"
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(runtime.run_interpretation, first["id"])
            self.assertTrue(entered.wait(timeout=2))
            second_future = executor.submit(runtime.run_interpretation, second["id"])
            release.set()
            first_result = first_future.result(timeout=5)
            second_result = second_future.result(timeout=5)
        self.assertEqual(first_result["status"], "completed")
        self.assertEqual(second_result["status"], "completed")
        self.assertTrue(second_result["cached"])
        self.assertEqual(provider.calls, 1)

    def test_current_daily_no_change_requires_exact_material_and_zero_calls(self) -> None:
        payloads = {
            "rmem_" + "3" * 24: {"statement": "先验证", "revision": 1},
            "rmem_" + "4" * 24: {"statement": "再完整", "revision": 1},
        }
        base_ref = ObjectRef(
            "reusable_memory",
            "rmem_" + "3" * 24,
            1,
            persisted_sha256(payloads["rmem_" + "3" * 24]),
        )
        added_ref = ObjectRef(
            "reusable_memory",
            "rmem_" + "4" * 24,
            1,
            persisted_sha256(payloads["rmem_" + "4" * 24]),
        )
        provider = FakeProvider([])
        runtime = self.runtime(
            provider, object_resolver=lambda ref: payloads[ref.id]
        )
        spans, receipt_ref, profile_sha, action_sha = self.ready_daily_material(
            runtime
        )
        context = {"record_count": 1, "receipt_count": 1}
        provider.replies.append(completion(finish_daily()))
        request = runtime.create_daily_request("2026-08-18")
        terminal = runtime.run_daily(
            request["id"],
            source_spans=spans,
            object_refs=[base_ref],
            receipt_refs=[receipt_ref],
            daily_context=context,
            profile_sha256=profile_sha,
            user_action_watermark_sha256=action_sha,
        )
        self.assertEqual(terminal["status"], "no_change")
        calls_before_reads = provider.calls

        current = runtime.get_current_daily_terminal(
            "2026-08-18",
            source_spans=spans,
            object_refs=[base_ref],
            receipt_refs=[receipt_ref],
            daily_context=context,
            profile_sha256=profile_sha,
            user_action_watermark_sha256=action_sha,
        )
        self.assertEqual(
            current,
            {"run_id": terminal["run"]["run_id"], "status": "no_change"},
        )

        extra_span = SourceSpan.from_dict(self.evidence(runtime)[1]["span"])
        variants = (
            {
                "source_spans": [*spans, extra_span],
                "object_refs": [base_ref],
                "receipt_refs": [receipt_ref],
                "daily_context": context,
                "profile_sha256": profile_sha,
                "user_action_watermark_sha256": action_sha,
            },
            {
                "source_spans": spans,
                "object_refs": [base_ref],
                "receipt_refs": [],
                "daily_context": context,
                "profile_sha256": profile_sha,
                "user_action_watermark_sha256": action_sha,
            },
            {
                "source_spans": spans,
                "object_refs": [base_ref, added_ref],
                "receipt_refs": [receipt_ref],
                "daily_context": context,
                "profile_sha256": profile_sha,
                "user_action_watermark_sha256": action_sha,
            },
            {
                "source_spans": spans,
                "object_refs": [base_ref],
                "receipt_refs": [receipt_ref],
                "daily_context": {**context, "focus": "changed"},
                "profile_sha256": profile_sha,
                "user_action_watermark_sha256": action_sha,
            },
            {
                "source_spans": spans,
                "object_refs": [base_ref],
                "receipt_refs": [receipt_ref],
                "daily_context": context,
                "profile_sha256": "f" * 64,
                "user_action_watermark_sha256": action_sha,
            },
            {
                "source_spans": spans,
                "object_refs": [base_ref],
                "receipt_refs": [receipt_ref],
                "daily_context": context,
                "profile_sha256": profile_sha,
                "user_action_watermark_sha256": "e" * 64,
            },
        )
        for index, variant in enumerate(variants):
            with self.subTest(material_variant=index):
                self.assertIsNone(
                    runtime.get_current_daily_terminal(
                        "2026-08-18", **variant
                    )
                )

        changed_policy_provider = FakeProvider([])
        changed_policy_runtime = self.runtime(
            changed_policy_provider,
            object_resolver=lambda ref: payloads[ref.id],
            daily_max_tokens=3601,
        )
        self.assertIsNone(
            changed_policy_runtime.get_current_daily_terminal(
                "2026-08-18",
                source_spans=spans,
                object_refs=[base_ref],
                receipt_refs=[receipt_ref],
                daily_context=context,
                profile_sha256=profile_sha,
                user_action_watermark_sha256=action_sha,
            )
        )
        self.assertEqual(provider.calls, calls_before_reads)
        self.assertEqual(changed_policy_provider.calls, 0)

    def test_current_daily_no_change_accepts_complete_multiturn_tool_chain(self) -> None:
        payload = {
            "schema_version": "1.0",
            "kind": "memento_reusable_memory_revision",
            "memory_id": "rmem_" + "8" * 24,
            "revision": 1,
            "statement": "先检查现有记忆。",
        }
        object_ref = ObjectRef(
            kind="reusable_memory",
            id=payload["memory_id"],
            revision=1,
            revision_sha256=persisted_sha256(payload),
        )
        provider = FakeProvider([])
        runtime = self.runtime(
            provider, object_resolver=lambda _: payload
        )
        spans, receipt_ref, profile_sha, action_sha = self.ready_daily_material(
            runtime
        )
        context = {"record_count": 1, "receipt_count": 1}
        provider.replies.extend(
            [
                completion(inspect_action(make_object_ref_id(object_ref))),
                completion(search_action()),
                completion(finish_daily()),
            ]
        )
        request = runtime.create_daily_request("2026-08-18")
        terminal = runtime.run_daily(
            request["id"],
            source_spans=spans,
            object_refs=[object_ref],
            receipt_refs=[receipt_ref],
            daily_context=context,
            profile_sha256=profile_sha,
            user_action_watermark_sha256=action_sha,
            inspect_memory=lambda _: payload,
            search_history=lambda query, date_from, date_to, limit: (),
        )
        self.assertEqual(terminal["status"], "no_change")
        self.assertEqual(terminal["run"]["usage"]["model_calls"], 3)
        calls = provider.calls

        self.assertEqual(
            runtime.get_current_daily_terminal(
                "2026-08-18",
                source_spans=spans,
                object_refs=[object_ref],
                receipt_refs=[receipt_ref],
                daily_context=context,
                profile_sha256=profile_sha,
                user_action_watermark_sha256=action_sha,
            ),
            {"run_id": terminal["run"]["run_id"], "status": "no_change"},
        )
        self.assertEqual(provider.calls, calls)

    def test_current_daily_no_change_rejects_run_and_sidecar_tampering(self) -> None:
        provider = FakeProvider([])
        runtime = self.runtime(provider)
        spans, receipt_ref, profile_sha, action_sha = self.ready_daily_material(
            runtime
        )
        context = {"record_count": 1, "receipt_count": 1}
        provider.replies.append(completion(finish_daily()))
        request = runtime.create_daily_request("2026-08-18")
        terminal = runtime.run_daily(
            request["id"],
            source_spans=spans,
            receipt_refs=[receipt_ref],
            daily_context=context,
            profile_sha256=profile_sha,
            user_action_watermark_sha256=action_sha,
        )
        arguments = {
            "source_spans": spans,
            "receipt_refs": [receipt_ref],
            "daily_context": context,
            "profile_sha256": profile_sha,
            "user_action_watermark_sha256": action_sha,
        }
        run_path = runtime.files.daily_runs / (
            terminal["run"]["run_id"] + ".json"
        )
        original_run = runtime.files.read_json(run_path, name="daily run test")
        changed_run = copy.deepcopy(original_run)
        changed_run["steps"][1]["arguments_sha256"] = "f" * 64
        runtime.files.write_mutable(run_path, changed_run)
        with self.assertRaises(ContractError) as run_error:
            runtime.get_current_daily_terminal("2026-08-18", **arguments)
        self.assertEqual(run_error.exception.kind, "evidence")

        runtime.files.write_mutable(run_path, original_run)
        sidecar_path = runtime._completion_path(
            run_path, terminal["run"]["run_id"], 1
        )
        original_sidecar = runtime.files.read_json(
            sidecar_path, name="daily completion test"
        )
        changed_sidecar = copy.deepcopy(original_sidecar)
        changed_sidecar["content_sha256"] = "e" * 64
        runtime.files.write_mutable(sidecar_path, changed_sidecar)
        with self.assertRaises(ContractError) as sidecar_error:
            runtime.get_current_daily_terminal("2026-08-18", **arguments)
        self.assertEqual(sidecar_error.exception.kind, "evidence")
        self.assertEqual(provider.calls, 2)

    def test_current_daily_no_change_closes_source_race_inside_lock(self) -> None:
        payload = {"statement": "先验证", "revision": 1}
        object_ref = ObjectRef(
            "reusable_memory",
            "rmem_" + "5" * 24,
            1,
            persisted_sha256(payload),
        )
        resolver_calls = 0

        def resolver(_: ObjectRef) -> Mapping[str, Any]:
            nonlocal resolver_calls
            resolver_calls += 1
            if resolver_calls == 3:
                self.day.write_text(
                    self.day.read_text(encoding="utf-8").replace(
                        "真实反馈反而来得太晚。",
                        "真实反馈已经发生变化。",
                    ),
                    encoding="utf-8",
                )
                self.store.reconcile_day(
                    DAY, now=NOW + dt.timedelta(minutes=1)
                )
            return payload

        provider = FakeProvider([])
        runtime = self.runtime(provider, object_resolver=resolver)
        spans, receipt_ref, profile_sha, action_sha = self.ready_daily_material(
            runtime
        )
        provider.replies.append(completion(finish_daily()))
        request = runtime.create_daily_request("2026-08-18")
        runtime.run_daily(
            request["id"],
            source_spans=spans,
            object_refs=[object_ref],
            receipt_refs=[receipt_ref],
            profile_sha256=profile_sha,
            user_action_watermark_sha256=action_sha,
        )
        calls = provider.calls
        self.assertIsNone(
            runtime.get_current_daily_terminal(
                "2026-08-18",
                source_spans=spans,
                object_refs=[object_ref],
                receipt_refs=[receipt_ref],
                profile_sha256=profile_sha,
                user_action_watermark_sha256=action_sha,
            )
        )
        self.assertEqual(provider.calls, calls)

    def test_current_daily_no_change_closes_action_race_inside_lock(self) -> None:
        payload = {"statement": "先验证", "revision": 1}
        object_ref = ObjectRef(
            "reusable_memory",
            "rmem_" + "6" * 24,
            1,
            persisted_sha256(payload),
        )
        resolver_calls = 0
        target: dict[str, ObjectRef] = {}
        runtime_holder: dict[str, CognitiveRuntime] = {}

        def resolver(_: ObjectRef) -> Mapping[str, Any]:
            nonlocal resolver_calls
            resolver_calls += 1
            if resolver_calls == 3:
                runtime = runtime_holder["runtime"]
                CognitiveActionStore(
                    self.vault, state_root=runtime.files.root
                ).submit_action(
                    CognitiveUserAction(
                        COGNITIVE_SCHEMA_VERSION,
                        "memento_cognitive_user_action",
                        make_cognitive_action_id("daily-action-race"),
                        NOW.isoformat(timespec="seconds"),
                        "confirm_receipt",
                        target["receipt"],
                        None,
                    )
                )
            return payload

        provider = FakeProvider([])
        runtime = self.runtime(provider, object_resolver=resolver)
        runtime_holder["runtime"] = runtime
        spans, receipt_ref, profile_sha, action_sha = self.ready_daily_material(
            runtime
        )
        target["receipt"] = receipt_ref
        provider.replies.append(completion(finish_daily()))
        request = runtime.create_daily_request("2026-08-18")
        runtime.run_daily(
            request["id"],
            source_spans=spans,
            object_refs=[object_ref],
            receipt_refs=[receipt_ref],
            profile_sha256=profile_sha,
            user_action_watermark_sha256=action_sha,
        )
        calls = provider.calls
        self.assertIsNone(
            runtime.get_current_daily_terminal(
                "2026-08-18",
                source_spans=spans,
                object_refs=[object_ref],
                receipt_refs=[receipt_ref],
                profile_sha256=profile_sha,
                user_action_watermark_sha256=action_sha,
            )
        )
        self.assertEqual(provider.calls, calls)

    def test_current_daily_no_change_closes_formal_head_race_inside_lock(self) -> None:
        payload = {"statement": "先验证", "revision": 1}
        object_ref = ObjectRef(
            "reusable_memory",
            "rmem_" + "7" * 24,
            1,
            persisted_sha256(payload),
        )
        resolver_calls = 0

        def resolver(_: ObjectRef) -> Mapping[str, Any]:
            nonlocal resolver_calls
            resolver_calls += 1
            if resolver_calls == 3:
                raise ContractError("formal head changed", kind="stale")
            return payload

        provider = FakeProvider([])
        runtime = self.runtime(provider, object_resolver=resolver)
        spans, receipt_ref, profile_sha, action_sha = self.ready_daily_material(
            runtime
        )
        provider.replies.append(completion(finish_daily()))
        request = runtime.create_daily_request("2026-08-18")
        runtime.run_daily(
            request["id"],
            source_spans=spans,
            object_refs=[object_ref],
            receipt_refs=[receipt_ref],
            profile_sha256=profile_sha,
            user_action_watermark_sha256=action_sha,
        )
        calls = provider.calls
        self.assertIsNone(
            runtime.get_current_daily_terminal(
                "2026-08-18",
                source_spans=spans,
                object_refs=[object_ref],
                receipt_refs=[receipt_ref],
                profile_sha256=profile_sha,
                user_action_watermark_sha256=action_sha,
            )
        )
        self.assertEqual(provider.calls, calls)

    def test_current_daily_failure_terminals_never_call_provider(self) -> None:
        provider = FakeProvider([])
        runtime = self.runtime(provider)
        spans, receipt_ref, profile_sha, action_sha = self.ready_daily_material(
            runtime
        )
        base = {
            "source_spans": spans,
            "receipt_refs": [receipt_ref],
            "profile_sha256": profile_sha,
            "user_action_watermark_sha256": action_sha,
        }

        provider.replies.append(completion(finish_daily_insufficient()))
        insufficient_request = runtime.create_daily_request(
            "2026-08-18", request_nonce="insufficient-evidence"
        )
        insufficient_context = {"case": "insufficient-evidence"}
        insufficient_result = runtime.run_daily(
            insufficient_request["id"],
            daily_context=insufficient_context,
            **base,
        )
        self.assertEqual(insufficient_result["status"], "no_change")
        calls = provider.calls
        self.assertEqual(
            runtime.get_current_daily_terminal(
                "2026-08-18",
                daily_context=insufficient_context,
                **base,
            ),
            {
                "run_id": insufficient_result["run"]["run_id"],
                "status": "no_change",
            },
        )
        self.assertEqual(provider.calls, calls)

        provider.replies.append(completion(finish_daily(), usage={}))
        usage_request = runtime.create_daily_request(
            "2026-08-18", request_nonce="usage-missing"
        )
        usage_context = {"case": "usage-missing"}
        usage_result = runtime.run_daily(
            usage_request["id"], daily_context=usage_context, **base
        )
        self.assertEqual(usage_result["run"]["error_kind"], "usage_missing")
        calls = provider.calls
        self.assertIsNone(
            runtime.get_current_daily_terminal(
                "2026-08-18", daily_context=usage_context, **base
            )
        )
        self.assertEqual(provider.calls, calls)

        provider.replies.append(
            CompletionResult("not-json", USAGE, "bad-daily", "deepseek-v4-pro")
        )
        schema_request = runtime.create_daily_request(
            "2026-08-18", request_nonce="schema"
        )
        schema_context = {"case": "schema"}
        schema_result = runtime.run_daily(
            schema_request["id"], daily_context=schema_context, **base
        )
        self.assertEqual(schema_result["run"]["error_kind"], "schema")
        calls = provider.calls
        self.assertIsNone(
            runtime.get_current_daily_terminal(
                "2026-08-18", daily_context=schema_context, **base
            )
        )
        self.assertEqual(provider.calls, calls)

        provider.replies.append(RuntimeError("provider interrupted"))
        unknown_request = runtime.create_daily_request(
            "2026-08-18", request_nonce="unknown"
        )
        unknown_context = {"case": "unknown"}
        with self.assertRaises(RuntimeError):
            runtime.run_daily(
                unknown_request["id"], daily_context=unknown_context, **base
            )
        calls = provider.calls
        recovered = runtime.run_daily(
            unknown_request["id"], daily_context=unknown_context, **base
        )
        self.assertEqual(recovered["run"]["error_kind"], "unknown_attempt")
        self.assertIsNone(
            runtime.get_current_daily_terminal(
                "2026-08-18", daily_context=unknown_context, **base
            )
        )
        self.assertEqual(provider.calls, calls)

    def test_daily_inspect_search_propose_stages_only(self) -> None:
        payload = {
            "schema_version": "1.0",
            "kind": "memento_reusable_memory_revision",
            "memory_id": "rmem_" + "1" * 24,
            "revision": 1,
            "statement": "先验证。",
        }
        object_ref = ObjectRef(
            kind="reusable_memory",
            id="rmem_" + "1" * 24,
            revision=1,
            revision_sha256=persisted_sha256(payload),
        )
        provider = FakeProvider([])
        runtime = self.runtime(provider, object_resolver=lambda _: payload)
        evidence = self.evidence(runtime)
        initial_span = SourceSpan.from_dict(evidence[0]["span"])
        searched_span = SourceSpan.from_dict(evidence[1]["span"])
        oref = make_object_ref_id(object_ref)
        searched_eref = make_evidence_ref_id(searched_span)
        provider.replies.extend(
            [
                completion(inspect_action(oref)),
                completion(search_action()),
                completion(daily_proposal(searched_eref)),
            ]
        )
        request = runtime.create_daily_request("2026-08-18")
        result = runtime.run_daily(
            request["id"],
            source_spans=[initial_span],
            object_refs=[object_ref],
            inspect_memory=lambda _: {"statement": "先验证。"},
            search_history=lambda query, date_from, date_to, limit: [searched_span],
        )
        self.assertEqual(result["status"], "staged")
        self.assertEqual(result["run"]["stage"], "validating")
        self.assertEqual(result["run"]["usage"]["model_calls"], 3)
        self.assertEqual(result["run"]["usage"]["prompt_tokens"], 30)
        self.assertEqual(result["run"]["usage"]["completion_tokens"], 15)
        self.assertEqual(result["run"]["usage"]["total_tokens"], 45)
        self.assertFalse(result["run"]["usage"]["usage_missing"])
        self.assertTrue(result["run"]["usage"]["cost_complete"])
        self.assertFalse(result["candidate_bundle"]["formal_objects_committed"])
        self.assertEqual(provider.calls, 3)
        root = runtime.files.root
        self.assertFalse((root / "memory-revisions").exists())
        self.assertFalse((root / "relation-revisions").exists())

        cached = runtime.run_daily(
            request["id"],
            source_spans=[initial_span],
            object_refs=[object_ref],
        )
        self.assertTrue(cached["cached"])
        self.assertEqual(provider.calls, 3)

    def test_daily_finish_budget_and_forged_ref(self) -> None:
        seed_runtime = self.runtime(FakeProvider([]))
        span = SourceSpan.from_dict(self.evidence(seed_runtime)[0]["span"])

        finish_provider = FakeProvider([completion(finish_daily())])
        finish_runtime = self.runtime(finish_provider)
        finish_request = finish_runtime.create_daily_request("2026-08-18")
        finished = finish_runtime.run_daily(finish_request["id"], source_spans=[span])
        self.assertEqual(finished["status"], "no_change")

        payload = {"kind": "memory", "id": "rmem_" + "2" * 24, "revision": 1}
        ref = ObjectRef(
            kind="reusable_memory", id="rmem_" + "2" * 24, revision=1,
            revision_sha256=persisted_sha256(payload),
        )
        oref = make_object_ref_id(ref)
        budget_provider = FakeProvider(
            [completion(inspect_action(oref)), completion(inspect_action(oref)), completion(inspect_action(oref))]
        )
        budget_runtime = self.runtime(budget_provider, object_resolver=lambda _: payload)
        budget_request = budget_runtime.create_daily_request(
            "2026-08-18", trigger="manual", request_nonce="budget"
        )
        exhausted = budget_runtime.run_daily(
            budget_request["id"], source_spans=[span], object_refs=[ref],
            profile_sha256="1" * 64,
        )
        self.assertEqual(exhausted["status"], "budget_exhausted")
        self.assertEqual(exhausted["run"]["error_kind"], "tool_call_budget")

        forged_provider = FakeProvider([completion(daily_proposal("eref_" + "f" * 16))])
        forged_runtime = self.runtime(forged_provider)
        forged_request = forged_runtime.create_daily_request(
            "2026-08-18", trigger="manual", request_nonce="forged"
        )
        forged = forged_runtime.run_daily(
            forged_request["id"], source_spans=[span], profile_sha256="2" * 64
        )
        self.assertEqual(forged["status"], "error")
        self.assertEqual(forged["run"]["error_kind"], "evidence")


if __name__ == "__main__":
    unittest.main()
