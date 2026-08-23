#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "context-agent"
EVAL_DIR = AGENT_DIR / "eval" / "agent-v1"
for path in (AGENT_DIR, EVAL_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import agent_v1  # noqa: E402
import run_live_preflight as preflight  # noqa: E402
import run_live_workflow_mvp as workflow  # noqa: E402


def finish() -> dict:
    return {
        "schema_version": "1.0",
        "action": "finish",
        "reason_code": "no_material_change",
        "arguments": {"reason": "no_change"},
    }


def investigate(kind: str, target: str | None, queries: list[dict]) -> dict:
    return {
        "schema_version": "1.0",
        "action": "investigate",
        "reason_code": "plan_evidence",
        "arguments": {
            "candidate_kind": kind,
            "target_memory_id": target,
            "queries": queries,
        },
    }


def search_action(query: dict) -> dict:
    return {
        "schema_version": "1.0",
        "action": "search_history",
        "reason_code": "need_history_evidence",
        "arguments": query,
    }


class FakeProvider:
    def __init__(self, model: str, steps: list[dict]) -> None:
        self.model = model
        self.steps = list(steps)
        self.index = 0

    def complete(self, messages):
        del messages
        if self.index >= len(self.steps):
            raise AssertionError("fake provider steps exhausted")
        step = self.steps[self.index]
        self.index += 1
        return SimpleNamespace(
            content=json.dumps(step, ensure_ascii=False),
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 100,
            },
            request_id=f"fake-workflow-{self.index}",
            model=self.model,
        )


class WorkflowMvpLiveRunnerTest(unittest.TestCase):
    @staticmethod
    def _workflow_finalize(action: dict) -> dict:
        converted = json.loads(json.dumps(action, ensure_ascii=False))
        arguments = converted["arguments"]

        def ref_id(item: dict) -> str:
            return "eref_" + workflow._sha(item)[:16]

        arguments["evidence_refs"] = [
            ref_id(item) for item in arguments.pop("evidence")
        ]
        arguments["counterevidence_refs"] = [
            ref_id(item) for item in arguments.pop("counterevidence")
        ]
        return converted

    @staticmethod
    def _query(
        text: str,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict:
        return {
            "query": text,
            "date_from": date_from,
            "date_to": date_to,
            "limit": 5,
        }

    def provider_factory(self, case_id: str, config) -> FakeProvider:
        spec = workflow.CASE_SPECS[case_id]
        root = preflight._case_source_root(spec)
        if case_id == "noise_stop":
            steps = [finish()]
        elif case_id == "repeated_new":
            steps = [
                investigate(
                    "new",
                    None,
                    [self._query("做产品决策前", date_to="2026-07-31")],
                ),
                self._workflow_finalize(preflight.expected_action(spec, root)),
            ]
        elif case_id == "history_revise":
            target = preflight._seed_memory(root, "activation")["memory_id"]
            terminal = preflight.expected_action(spec, root)
            terminal["arguments"]["statement"] = preflight._find_evidence(
                root,
                "2026-07-26.md",
                "我们决定当前阶段以 30 日留存为核心结果指标",
            )["quote"]
            terminal["arguments"]["evidence"] = [
                preflight._find_evidence(
                    root,
                    "2026-07-26.md",
                    "我们决定当前阶段以 30 日留存为核心结果指标",
                ),
                preflight._find_evidence(
                    root,
                    "2026-07-17.md",
                    "从激活转向长期价值的优先级复核",
                ),
                preflight._find_evidence(
                    root,
                    "2026-07-17.md",
                    "优先级刚发生变化，旧决定不应继续作为当前约束",
                ),
            ]
            steps = [
                investigate(
                    "revise",
                    target,
                    [],
                ),
                search_action(
                    self._query(
                        "优先",
                        date_from="2026-07-17",
                        date_to="2026-07-17",
                    )
                ),
                self._workflow_finalize(terminal),
            ]
        else:
            evidence = [
                preflight._find_evidence(
                    root, "2026-07-20.md", preflight.METRIC_STATEMENT
                ),
                preflight._find_evidence(
                    root, "2026-07-24.md", preflight.METRIC_STATEMENT
                ),
            ]
            patch = {
                "schema_version": "1.0",
                "action": "finalize_patch",
                "reason_code": "evidence_sufficient",
                "arguments": {
                    "operation": "new",
                    "target_memory_id": None,
                    "expected_revision": 0,
                    "title": "先定义指标再讨论方案",
                    "statement": preflight.METRIC_STATEMENT,
                    "scope": preflight.METRIC_SCOPE,
                    "uncertainty": "medium",
                    "evidence": evidence,
                    "counterevidence": [],
                },
            }
            steps = [
                investigate("new", None, [self._query("做产品决策前")]),
                self._workflow_finalize(patch),
            ]
        return FakeProvider(config.model, steps)

    def test_plan_only_is_stable_and_zero_call(self) -> None:
        config = workflow.WorkflowLiveConfig()
        first = workflow.build_plan(config)
        second = workflow.build_plan(config)
        self.assertEqual(first, second)
        self.assertFalse(first["executed"])
        self.assertEqual(first["batch"]["calls"], 0)
        self.assertEqual(first["plan_sha256"], workflow.plan_sha256(config))

    def test_fake_four_case_matrix_passes(self) -> None:
        config = workflow.WorkflowLiveConfig()
        report = workflow.run_live(
            config,
            expected_plan_sha256=workflow.plan_sha256(config),
            provider_factory=self.provider_factory,
        )
        self.assertTrue(report["all_passed"])
        self.assertEqual(report["status"], "completed")
        self.assertEqual(len(report["runs"]), 4)
        self.assertEqual(report["batch"]["calls"], 8)
        self.assertTrue(all(item["passed"] for item in report["runs"]))
        self.assertEqual(
            report["runs"][2]["trajectory"],
            ["investigate", "search_history", "finalize_patch"],
        )
        self.assertFalse(report["runs"][2]["checks"]["statement_expected"])
        self.assertTrue(
            report["runs"][2]["checks"]["statement_grounded_current"]
        )
        self.assertFalse(
            report["runs"][2]["checks"]["required_evidence_present"]
        )
        self.assertTrue(
            report["runs"][2]["checks"]["explicit_change_signal_present"]
        )
        self.assertTrue(report["runs"][2]["checks"]["evidence_order_valid"])
        self.assertEqual(report["runs"][3]["error_kind"], "tombstone")

    def test_live_rejects_unreviewed_plan_before_provider(self) -> None:
        config = workflow.WorkflowLiveConfig()
        calls = 0

        def forbidden(case_id, selected):
            nonlocal calls
            del case_id, selected
            calls += 1
            raise AssertionError("provider must not be constructed")

        with self.assertRaisesRegex(ValueError, "plan_mismatch"):
            workflow.run_live(
                config,
                expected_plan_sha256="0" * 64,
                provider_factory=forbidden,
            )
        self.assertEqual(calls, 0)

    def test_workflow_policy_provider_is_not_legacy(self) -> None:
        config = workflow.WorkflowLiveConfig()
        workflow_hash = agent_v1.make_agent_policy_sha256(
            provider=workflow.PROVIDER_NAME,
            model=config.model,
            budget=config.budget,
        )
        legacy_hash = agent_v1.make_agent_policy_sha256(
            provider="deepseek",
            model=config.model,
            budget=config.budget,
        )
        self.assertNotEqual(workflow_hash, legacy_hash)


if __name__ == "__main__":
    unittest.main()
