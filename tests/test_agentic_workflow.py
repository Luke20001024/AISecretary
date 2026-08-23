#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "context-agent"
sys.path.insert(0, str(AGENT_DIR))

import agent_v1  # noqa: E402
from agent_v1 import (  # noqa: E402
    AgentBudget,
    MockPlanner,
    build_agent_profile,
    create_agent_request,
    enable_agent_v1,
    process_agent_request,
    run_path,
)
from core import ContractError, Pricing  # noqa: E402


def finish(reason: str = "no_change") -> dict:
    return {
        "schema_version": "1.0",
        "action": "finish",
        "reason_code": (
            "no_material_change" if reason == "no_change" else "insufficient_evidence"
        ),
        "arguments": {"reason": reason},
    }


def investigate(
    candidate_kind: str,
    *,
    target_memory_id: str | None = None,
    queries: list[dict] | None = None,
) -> dict:
    return {
        "schema_version": "1.0",
        "action": "investigate",
        "reason_code": "plan_evidence",
        "arguments": {
            "candidate_kind": candidate_kind,
            "target_memory_id": target_memory_id,
            "queries": queries or [],
        },
    }


def search_query(query: str, *, date_to: str | None = None) -> dict:
    return {
        "query": query,
        "date_from": None,
        "date_to": date_to,
        "limit": 5,
    }


def search_action(query: str, *, date_to: str | None = None) -> dict:
    return {
        "schema_version": "1.0",
        "action": "search_history",
        "reason_code": "need_history_evidence",
        "arguments": search_query(query, date_to=date_to),
    }


def finalize(patch: dict) -> dict:
    return {
        "schema_version": "1.0",
        "action": "finalize_patch",
        "reason_code": "evidence_sufficient",
        "arguments": patch,
    }


def workflow_finalize(patch: dict) -> dict:
    arguments = {
        key: value
        for key, value in patch.items()
        if key not in {"evidence", "counterevidence"}
    }

    def ref_id(item: dict) -> str:
        return "eref_" + agent_v1.sha256_bytes(
            agent_v1.canonical_json(item).encode("utf-8")
        )[:16]

    arguments["evidence_refs"] = [ref_id(item) for item in patch["evidence"]]
    arguments["counterevidence_refs"] = [
        ref_id(item) for item in patch["counterevidence"]
    ]
    return finalize(arguments)


def legacy_read(memory_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "action": "read_memory",
        "reason_code": "inspect_existing",
        "arguments": {"memory_id": memory_id},
    }


class RecordingFake:
    """Deterministic provider that retains only model input snapshots for tests."""

    def __init__(self, steps: list[dict]) -> None:
        self.steps = list(steps)
        self.index = 0
        self.messages: list[list[dict[str, str]]] = []

    def complete(self, messages) -> SimpleNamespace:
        self.messages.append(
            json.loads(json.dumps(messages, ensure_ascii=False))
        )
        if self.index >= len(self.steps):
            raise AssertionError("recording fake steps exhausted")
        step = self.steps[self.index]
        self.index += 1
        return SimpleNamespace(
            content=json.dumps(step, ensure_ascii=False),
            usage={
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 0,
            },
            request_id=f"recording-fake-{self.index}",
            model="fixture",
        )


class AgenticWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="agentic-workflow-")
        self.vault = Path(self.temporary.name)
        enable_agent_v1(self.vault)
        self.write_day("2026-08-01", "评审方案前，先写清成功标准和失败标准。")
        self.write_day("2026-08-02", "这次仍然先定义验证标准，再排实现顺序。")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_day(self, date: str, line: str) -> None:
        (self.vault / f"{date}.md").write_text(
            f"# {date}\n{line}\n", encoding="utf-8"
        )

    def request(self, digit: str, *, as_of: str = "2026-08-11") -> dict:
        request, _ = create_agent_request(
            self.vault,
            as_of=as_of,
            request_id="arq_" + digit * 24,
            created_at=f"{as_of}T10:00:00+08:00",
        )
        return request

    def run_workflow(
        self,
        request_id: str,
        steps: list[dict],
        *,
        workflow: bool = True,
        budget: AgentBudget = AgentBudget(),
    ) -> dict:
        return process_agent_request(
            self.vault,
            request_id,
            provider_client=MockPlanner(steps),
            provider_name=("mock-agentic-workflow" if workflow else "mock"),
            model="fixture",
            pricing=Pricing(),
            budget=budget,
        )[0]

    @staticmethod
    def run_new_identity_repair_case(
        bad_field: str,
    ) -> tuple[dict, dict, RecordingFake, dict]:
        statement = (
            "评审方案前，我习惯先写清目标指标、护栏指标和验证周期，"
            "再讨论功能方案。"
        )
        with tempfile.TemporaryDirectory(
            prefix=f"agentic-workflow-{bad_field}-"
        ) as directory:
            vault = Path(directory)
            enable_agent_v1(vault)
            for date in ("2026-08-01", "2026-08-02"):
                (vault / f"{date}.md").write_text(
                    f"# {date}\n{statement}\n", encoding="utf-8"
                )
            request, _ = create_agent_request(
                vault,
                as_of="2026-08-11",
                request_id=(
                    "arq_" + ("7" if bad_field == "statement" else "8") * 24
                ),
                created_at="2026-08-11T10:00:00+08:00",
            )
            patch = {
                "operation": "new",
                "target_memory_id": None,
                "expected_revision": 0,
                "title": "先定义指标再讨论方案",
                "statement": statement,
                "scope": "产品方案评审",
                "uncertainty": "medium",
                "evidence": [
                    {"file": date + ".md", "line": 2, "quote": statement}
                    for date in ("2026-08-01", "2026-08-02")
                ],
                "counterevidence": [],
            }
            correct_action = workflow_finalize(patch)
            bad_action = json.loads(
                json.dumps(correct_action, ensure_ascii=False)
            )
            bad_action["arguments"][bad_field] = (
                "评审方案前，先明确指标再讨论方案。"
                if bad_field == "statement"
                else "评审方案"
            )
            provider = RecordingFake(
                [investigate("new"), bad_action, correct_action]
            )
            response, _ = process_agent_request(
                vault,
                request["id"],
                provider_client=provider,
                provider_name="mock-agentic-workflow",
                model="fixture",
                pricing=Pricing(),
                budget=AgentBudget(),
            )
            run = json.loads(
                run_path(vault, response["run_id"]).read_text(encoding="utf-8")
            )
            return response, run, provider, bad_action

    @staticmethod
    def new_patch() -> dict:
        return {
            "operation": "new",
            "target_memory_id": None,
            "expected_revision": 0,
            "title": "先定义验证标准",
            "statement": "在产品方案中，多次先定义验证标准再进入实现。",
            "scope": "产品方案评审",
            "uncertainty": "medium",
            "evidence": [
                {
                    "file": "2026-08-01.md",
                    "line": 2,
                    "quote": "评审方案前，先写清成功标准和失败标准。",
                },
                {
                    "file": "2026-08-02.md",
                    "line": 2,
                    "quote": "这次仍然先定义验证标准，再排实现顺序。",
                },
            ],
            "counterevidence": [],
        }

    def create_legacy_memory(self) -> dict:
        request = self.request("1")
        response = self.run_workflow(
            request["id"], [finalize(self.new_patch())], workflow=False
        )
        self.assertEqual(response["status"], "updated")
        return response["memory"]

    def test_workflow_policy_and_prompt_are_distinct_from_legacy(self) -> None:
        request = self.request("1")
        preparation = agent_v1.prepare_agent_run(
            self.vault,
            request,
            agent_v1.sha256_file(agent_v1.request_path(self.vault, request["id"])),
            maximum_chars=120_000,
        )
        messages = agent_v1.build_agent_messages(preparation, workflow_mode=True)
        system = messages[0]["content"]
        self.assertIn("agentic-workflow-investigation-v1.13", system)
        self.assertIn("person-profile-candidate-v1.0", system)
        self.assertIn("稳定偏好、判断方式、工作方式", system)
        self.assertIn("不得把系统事实推导成人物侧写", system)
        self.assertIn("用户长期坚持的偏好", system)
        self.assertEqual(system.count('"action":"investigate"'), 1)
        self.assertNotIn('"action":"read_memory"', system)
        self.assertNotIn('"action":"search_history"', system)
        workflow_policy = agent_v1.make_agent_policy_sha256(
            provider="mock-agentic-workflow", model="fixture", budget=AgentBudget()
        )
        legacy_policy = agent_v1.make_agent_policy_sha256(
            provider="mock", model="fixture", budget=AgentBudget()
        )
        self.assertNotEqual(workflow_policy, legacy_policy)

        captured = []
        canonical_json = agent_v1.canonical_json

        def capture(value):
            captured.append(value)
            return canonical_json(value)

        with mock.patch.object(agent_v1, "canonical_json", side_effect=capture):
            agent_v1.make_agent_policy_sha256(
                provider="mock-agentic-workflow",
                model="fixture",
                budget=AgentBudget(),
            )
        workflow_contract = captured[-1]["tool_contract"]["agentic_workflow"]
        profile_scope = workflow_contract["candidate_profile_scope"]
        self.assertEqual(
            profile_scope,
            {
                "version": "person-profile-candidate-v1.0",
                "instruction_sha256": agent_v1.sha256_bytes(
                    agent_v1.PERSON_PROFILE_CANDIDATE_INSTRUCTION.encode(
                        "utf-8"
                    )
                ),
                "eligible_meaning": [
                    "stable_user_preference",
                    "user_judgment_method",
                    "user_working_method",
                    "user_change_or_tension",
                ],
                "system_content_requires_explicit_user_preference": True,
                "enforcement": "candidate_scout_semantic_judgment",
            },
        )
        self.assertEqual(
            workflow_contract["stable_new_identity_bundle_fields"],
            [
                "status",
                "required_statement",
                "required_scope",
                "eligible_evidence_refs",
            ],
        )
        self.assertEqual(
            workflow_contract["terminal_new_identity_contract"],
            "exact_required_statement_scope_and_finalize_when_stable",
        )
        terminal_gate = workflow_contract["stable_new_terminal_gate"]
        self.assertEqual(terminal_gate["version"], "stable-new-terminal-gate-v1.0")
        self.assertEqual(terminal_gate["required_action"], "finalize_patch")
        self.assertEqual(terminal_gate["required_uncertainty"], "medium")
        self.assertEqual(terminal_gate["minimum_distinct_dates"], 2)

    def test_terminal_judge_receives_complete_four_key_templates(self) -> None:
        provider = RecordingFake(
            [investigate("new"), workflow_finalize(self.new_patch())]
        )
        request = self.request("2")
        response, _ = process_agent_request(
            self.vault,
            request["id"],
            provider_client=provider,
            provider_name="mock-agentic-workflow",
            model="fixture",
            pricing=Pricing(),
            budget=AgentBudget(),
        )

        self.assertEqual(response["status"], "updated")
        self.assertEqual(len(provider.messages), 2)
        terminal_messages = provider.messages[1]
        self.assertIn("complete_envelope_templates", terminal_messages[0]["content"])
        self.assertIn("禁止照抄", terminal_messages[0]["content"])
        payload = json.loads(terminal_messages[1]["content"])
        templates = payload["output_contract"]["complete_envelope_templates"]
        self.assertEqual(
            set(templates),
            {
                "finalize_patch_shape_only",
                "finish_no_change",
                "finish_insufficient_evidence",
            },
        )
        for name in ("finish_no_change", "finish_insufficient_evidence"):
            parsed = agent_v1._parse_action(
                json.dumps(templates[name], ensure_ascii=False),
                workflow_mode=True,
            )
            self.assertEqual(parsed, templates[name])

        finalize_template = templates["finalize_patch_shape_only"]
        self.assertEqual(
            set(finalize_template),
            {"schema_version", "action", "reason_code", "arguments"},
        )
        self.assertEqual(finalize_template["arguments"]["operation"], "new")
        self.assertIsNone(finalize_template["arguments"]["target_memory_id"])
        self.assertEqual(finalize_template["arguments"]["expected_revision"], 0)
        self.assertTrue(
            finalize_template["arguments"]["evidence_refs"][0].startswith(
                "SELECT_"
            )
        )
        with self.assertRaises(ContractError):
            agent_v1._parse_action(
                json.dumps(finalize_template, ensure_ascii=False),
                workflow_mode=True,
            )

    def test_clean_stable_new_gate_rejects_finish_then_commits_exact_identity(self) -> None:
        statement = "我在方案评审前会先检查反例和失败条件，再判断方案。"
        for date in ("2026-08-01", "2026-08-02"):
            self.write_day(date, statement)
        patch = {
            "operation": "new",
            "target_memory_id": None,
            "expected_revision": 0,
            "title": "评审前检查失败条件",
            "statement": statement,
            "scope": "产品方案评审",
            "uncertainty": "medium",
            "evidence": [
                {"file": date + ".md", "line": 2, "quote": statement}
                for date in ("2026-08-01", "2026-08-02")
            ],
            "counterevidence": [],
        }
        provider = RecordingFake(
            [
                investigate("new"),
                finish("insufficient_evidence"),
                workflow_finalize(patch),
            ]
        )
        request = self.request("c")
        response, _ = process_agent_request(
            self.vault,
            request["id"],
            provider_client=provider,
            provider_name="mock-agentic-workflow",
            model="fixture",
            pricing=Pricing(),
            budget=AgentBudget(),
        )
        self.assertEqual(response["status"], "updated")
        self.assertEqual(response["memory"]["statement"], statement)
        self.assertEqual(response["memory"]["scope"], "产品方案评审")
        run = json.loads(
            run_path(self.vault, response["run_id"]).read_text(encoding="utf-8")
        )
        self.assertEqual(
            [step["result_kind"] for step in run["steps"]],
            ["investigation_materialized", "rejected", "memory_updated"],
        )
        terminal_payload = json.loads(provider.messages[1][1]["content"])
        gate = terminal_payload["output_contract"]["stable_new_decision_gate"]
        self.assertTrue(gate["applies"])
        self.assertFalse(gate["requires_finish"])
        self.assertEqual(gate["eligible_ref_distinct_dates"], 2)
        self.assertEqual(
            terminal_payload["output_contract"]["allowed_actions"],
            ["finalize_patch"],
        )
        self.assertIn(
            "stable-new-terminal-gate-v1.0",
            provider.messages[1][0]["content"],
        )

    def test_ambiguous_stable_identity_allows_only_finish(self) -> None:
        first = "我在方案评审前会先检查失败条件。"
        second = "我做产品决策时通常先写出验证标准。"
        for date in ("2026-08-01", "2026-08-02"):
            (self.vault / f"{date}.md").write_text(
                f"# {date}\n{first}\n{second}\n", encoding="utf-8"
            )
        request = self.request("d")
        preparation = agent_v1.prepare_agent_run(
            self.vault,
            request,
            agent_v1.sha256_file(agent_v1.request_path(self.vault, request["id"])),
            maximum_chars=120_000,
        )
        bundle, *_ = agent_v1._materialize_investigation(
            preparation,
            {"candidate_kind": "new", "target_memory_id": None, "queries": []},
        )
        self.assertEqual(
            bundle["stable_new_identity"]["status"], "ambiguous_statement"
        )
        self.assertFalse(bundle["evidence_ready"])
        self.assertEqual(
            bundle["missing_requirements"],
            ["stable_new_identity_ambiguous_statement"],
        )
        self.assertEqual(bundle["allowed_next_actions"], ["finish"])
        payload = json.loads(
            agent_v1.build_workflow_decision_messages(bundle)[1]["content"]
        )
        self.assertEqual(payload["output_contract"]["allowed_actions"], ["finish"])
        self.assertTrue(
            payload["output_contract"]["stable_new_decision_gate"][
                "requires_finish"
            ]
        )

    def test_workflow_can_stop_without_materializing_evidence(self) -> None:
        request = self.request("1")
        response = self.run_workflow(request["id"], [finish()])
        self.assertEqual(response["status"], "no_change")
        self.assertEqual(response["trace"]["actions"], ["finish"])
        self.assertEqual(response["trace"]["tool_calls"], 0)

    def test_candidate_scout_fake_finishes_system_only_material(self) -> None:
        self.write_day(
            "2026-08-01",
            "当前阶段决定将 Agent 调度改为每天 21:00 创建请求。",
        )
        self.write_day(
            "2026-08-02",
            "Workflow 的存储实现改为本地 JSON，并增加迁移测试。",
        )
        request = self.request("1")
        provider = RecordingFake([finish()])
        response, _ = process_agent_request(
            self.vault,
            request["id"],
            provider_client=provider,
            provider_name="mock-agentic-workflow",
            model="fixture",
            pricing=Pricing(),
        )
        self.assertEqual(response["status"], "no_change")
        self.assertEqual(response["trace"]["actions"], ["finish"])
        candidate_input = json.loads(provider.messages[0][1]["content"])
        quotes = {
            item["quote"]
            for item in candidate_input["recent_decision_candidates"]
        }
        self.assertIn(
            "当前阶段决定将 Agent 调度改为每天 21:00 创建请求。",
            quotes,
        )
        self.assertEqual(build_agent_profile(self.vault)["memories"], [])

    def test_explicit_person_preference_remains_a_valid_candidate(self) -> None:
        statement = (
            "做产品决策前，我习惯先写清目标指标、护栏指标和验证周期，"
            "再讨论功能方案。"
        )
        for date in ("2026-08-01", "2026-08-02"):
            self.write_day(date, statement)
        request = self.request("1")
        patch = {
            "operation": "new",
            "target_memory_id": None,
            "expected_revision": 0,
            "title": "先定义指标再讨论方案",
            "statement": statement,
            "scope": "产品决策",
            "uncertainty": "medium",
            "evidence": [
                {"file": f"{date}.md", "line": 2, "quote": statement}
                for date in ("2026-08-01", "2026-08-02")
            ],
            "counterevidence": [],
        }
        provider = RecordingFake(
            [investigate("new"), workflow_finalize(patch)]
        )
        response, _ = process_agent_request(
            self.vault,
            request["id"],
            provider_client=provider,
            provider_name="mock-agentic-workflow",
            model="fixture",
            pricing=Pricing(),
        )
        self.assertEqual(response["status"], "updated")
        self.assertEqual(response["memory"]["statement"], statement)
        self.assertEqual(
            response["trace"]["actions"], ["investigate", "finalize_patch"]
        )

    def test_workflow_materializes_new_candidate_then_agent_commits(self) -> None:
        request = self.request("1")
        response = self.run_workflow(
            request["id"],
            [investigate("new"), workflow_finalize(self.new_patch())],
        )
        self.assertEqual(response["status"], "updated")
        self.assertEqual(response["memory"]["revision"], 1)
        self.assertEqual(
            response["trace"]["actions"], ["investigate", "finalize_patch"]
        )
        self.assertEqual(response["trace"]["tool_calls"], 2)
        self.assertEqual(response["trace"]["history_matches"], 0)
        run = json.loads(
            run_path(self.vault, response["run_id"]).read_text(encoding="utf-8")
        )
        self.assertEqual(run["steps"][0]["result_kind"], "investigation_materialized")
        self.assertEqual(run["steps"][0]["result_count"], 0)

    def test_workflow_normalizes_only_exact_flattened_finalize_shape(
        self,
    ) -> None:
        terminal = workflow_finalize(self.new_patch())
        flattened = {
            "action": terminal["action"],
            **terminal["arguments"],
        }
        self.assertEqual(
            set(flattened),
            {"action"} | set(agent_v1.WORKFLOW_FINALIZE_FIELDS),
        )
        self.assertNotIn("schema_version", flattened)
        self.assertNotIn("reason_code", flattened)
        self.assertNotIn("arguments", flattened)
        optional_envelopes = (
            {},
            {"schema_version": "1.0"},
            {"reason_code": "evidence_sufficient"},
            {
                "schema_version": "1.0",
                "reason_code": "evidence_sufficient",
            },
        )
        for envelope in optional_envelopes:
            with self.subTest(envelope=envelope):
                self.assertEqual(
                    agent_v1._parse_action(
                        json.dumps(
                            {**flattened, **envelope},
                            ensure_ascii=False,
                        ),
                        workflow_mode=True,
                    ),
                    terminal,
                )

        request = self.request("3")
        flattened_with_envelope = {
            **flattened,
            "schema_version": "1.0",
            "reason_code": "evidence_sufficient",
        }
        response = self.run_workflow(
            request["id"],
            [investigate("new"), flattened_with_envelope],
        )
        self.assertEqual(response["status"], "updated")
        self.assertEqual(response["memory"]["revision"], 1)
        self.assertEqual(
            response["trace"]["actions"],
            ["investigate", "finalize_patch"],
        )

        invalid_variants = {
            "wrong_schema": {
                **flattened,
                "schema_version": "2.0",
            },
            "wrong_reason": {
                **flattened,
                "reason_code": "no_material_change",
            },
            "unknown_field": {
                **flattened_with_envelope,
                "unexpected": True,
            },
        }
        for label, invalid in invalid_variants.items():
            with self.subTest(invalid=label), self.assertRaises(ContractError):
                agent_v1._parse_action(
                    json.dumps(invalid, ensure_ascii=False),
                    workflow_mode=True,
                )

    def test_decision_invalid_action_retries_with_fresh_judge_context(
        self,
    ) -> None:
        terminal = workflow_finalize(self.new_patch())
        invalid_flattened = {
            "action": terminal["action"],
            **terminal["arguments"],
        }
        invalid_flattened.pop("counterevidence_refs")
        invalid_flattened["title"] = "BAD_DECISION_SHOULD_NOT_RETURN"
        provider = RecordingFake(
            [investigate("new"), invalid_flattened, terminal]
        )
        request = self.request("4")
        response, _ = process_agent_request(
            self.vault,
            request["id"],
            provider_client=provider,
            provider_name="mock-agentic-workflow",
            model="fixture",
            pricing=Pricing(),
            budget=AgentBudget(),
        )

        self.assertEqual(response["status"], "updated")
        self.assertEqual(
            response["trace"]["actions"],
            ["investigate", "invalid_action", "finalize_patch"],
        )
        self.assertEqual(len(provider.messages), 3)
        initial_judge_messages = provider.messages[1]
        retry_messages = provider.messages[2]
        self.assertEqual(
            [message["role"] for message in retry_messages],
            ["system", "user"],
        )
        self.assertEqual(retry_messages[0], initial_judge_messages[0])
        self.assertIn("最终记忆 Judge", retry_messages[0]["content"])
        retry_payload = json.loads(retry_messages[1]["content"])
        self.assertIn("evidence_bundle", retry_payload)
        self.assertIn("output_contract", retry_payload)
        self.assertNotIn("previous_decision", retry_payload)
        self.assertEqual(
            retry_payload["output_contract"]["allowed_actions"],
            ["finalize_patch", "finish"],
        )
        serialized = json.dumps(retry_messages, ensure_ascii=False)
        self.assertNotIn('"role": "assistant"', serialized)
        self.assertNotIn("invalid_action", serialized)
        self.assertNotIn("BAD_DECISION_SHOULD_NOT_RETURN", serialized)

    def test_workflow_investigate_defaults_only_missing_new_target_to_null(
        self,
    ) -> None:
        new_without_target = investigate("new")
        del new_without_target["arguments"]["target_memory_id"]
        parsed = agent_v1._parse_action(
            json.dumps(new_without_target, ensure_ascii=False),
            workflow_mode=True,
        )
        self.assertIsNone(parsed["arguments"]["target_memory_id"])

        request = self.request("5")
        response = self.run_workflow(
            request["id"],
            [new_without_target, workflow_finalize(self.new_patch())],
        )
        self.assertEqual(response["status"], "updated")
        self.assertEqual(
            response["trace"]["actions"],
            ["investigate", "finalize_patch"],
        )

        non_new_without_target = investigate("revise")
        del non_new_without_target["arguments"]["target_memory_id"]
        with self.assertRaises(ContractError):
            agent_v1._parse_action(
                json.dumps(non_new_without_target, ensure_ascii=False),
                workflow_mode=True,
            )

        new_with_extra_field = investigate("new")
        new_with_extra_field["arguments"]["unexpected"] = True
        with self.assertRaises(ContractError):
            agent_v1._parse_action(
                json.dumps(new_with_extra_field, ensure_ascii=False),
                workflow_mode=True,
            )

    def test_workflow_reads_target_searches_history_then_revises(self) -> None:
        memory = self.create_legacy_memory()
        self.write_day("2026-08-10", "本次改为先检查失败条件，再决定是否进入实现。")
        request = self.request("2")
        patch = {
            "operation": "revise",
            "target_memory_id": memory["memory_id"],
            "expected_revision": 1,
            "title": "先检查失败条件",
            "statement": "本次改为先检查失败条件，再决定是否进入实现。",
            "scope": memory["scope"],
            "uncertainty": "medium",
            "evidence": [
                {
                    "file": "2026-08-10.md",
                    "line": 2,
                    "quote": "本次改为先检查失败条件，再决定是否进入实现。",
                }
            ],
            "counterevidence": [
                {
                    "file": "2026-08-02.md",
                    "line": 2,
                    "quote": "这次仍然先定义验证标准，再排实现顺序。",
                }
            ],
        }
        response = self.run_workflow(
            request["id"],
            [
                investigate(
                    "revise",
                    target_memory_id=memory["memory_id"],
                    queries=[search_query("本次改为")],
                ),
                workflow_finalize(patch),
            ],
        )
        self.assertEqual(response["status"], "updated")
        self.assertEqual(response["memory"]["revision"], 2)
        self.assertEqual(response["trace"]["history_matches"], 1)
        self.assertEqual(build_agent_profile(self.vault)["latest_run"]["history_matches"], 1)

    def test_workflow_asks_agent_for_missing_change_signal_then_revises(self) -> None:
        memory = self.create_legacy_memory()
        self.write_day(
            "2026-08-05",
            "此前先定义验证标准的做法被当前决定替代。",
        )
        self.write_day(
            "2026-08-24",
            "当前阶段先检查失败条件，再决定是否进入实现。",
        )
        request = self.request("2", as_of="2026-08-25")
        patch = {
            "operation": "revise",
            "target_memory_id": memory["memory_id"],
            "expected_revision": 1,
            "title": "先检查失败条件",
            "statement": "当前阶段先检查失败条件，再决定是否进入实现。",
            "scope": memory["scope"],
            "uncertainty": "medium",
            "evidence": [
                {
                    "file": "2026-08-05.md",
                    "line": 2,
                    "quote": "此前先定义验证标准的做法被当前决定替代。",
                },
                {
                    "file": "2026-08-24.md",
                    "line": 2,
                    "quote": "当前阶段先检查失败条件，再决定是否进入实现。",
                },
            ],
            "counterevidence": [
                {
                    "file": "2026-08-02.md",
                    "line": 2,
                    "quote": "这次仍然先定义验证标准，再排实现顺序。",
                }
            ],
        }
        response = self.run_workflow(
            request["id"],
            [
                investigate(
                    "revise",
                    target_memory_id=memory["memory_id"],
                ),
                search_action("替代", date_to="2026-08-11"),
                workflow_finalize(patch),
            ],
        )
        self.assertEqual(response["status"], "updated")
        self.assertEqual(
            response["trace"]["actions"],
            ["investigate", "search_history", "finalize_patch"],
        )
        self.assertEqual(response["trace"]["tool_calls"], 3)
        run = json.loads(
            run_path(self.vault, response["run_id"]).read_text(encoding="utf-8")
        )
        self.assertEqual(run["steps"][0]["result_kind"], "investigation_materialized")
        self.assertEqual(run["steps"][1]["result_kind"], "history_matches")
        self.assertEqual(run["steps"][2]["result_kind"], "memory_updated")

    def test_dedicated_candidate_scout_can_finish_without_controller_veto(self) -> None:
        self.create_legacy_memory()
        self.write_day("2026-08-10", "新一轮材料需要重新核对当前方向。")
        request = self.request("2")
        response = self.run_workflow(request["id"], [finish()])
        self.assertEqual(response["status"], "no_change")
        self.assertEqual(response["trace"]["actions"], ["finish"])
        self.assertEqual(response["trace"]["tool_calls"], 0)
        run = json.loads(
            run_path(self.vault, response["run_id"]).read_text(encoding="utf-8")
        )
        self.assertEqual(run["steps"][0]["result_kind"], "no_change")

    def test_investigation_bundle_labels_validator_change_signals(self) -> None:
        memory = self.create_legacy_memory()
        self.write_day("2026-08-10", "本次改为先检查失败条件，再决定是否进入实现。")
        request = self.request("2")
        preparation = agent_v1.prepare_agent_run(
            self.vault,
            request,
            agent_v1.sha256_file(agent_v1.request_path(self.vault, request["id"])),
            maximum_chars=120_000,
        )
        bundle, _, match_count, target, _ = agent_v1._materialize_investigation(
            preparation,
            investigate(
                "revise",
                target_memory_id=memory["memory_id"],
                queries=[search_query("本次改为")],
            )["arguments"],
        )
        self.assertEqual(target, memory["memory_id"])
        self.assertEqual(match_count, 1)
        matching_ref = next(
            item["ref_id"]
            for item in bundle["evidence_catalog"]
            if item["file"] == "2026-08-10.md" and item["line"] == 2
        )
        self.assertEqual(bundle["change_signal_refs"], [matching_ref])

    def test_workflow_multiterm_search_matches_any_term_but_legacy_requires_all_terms(
        self,
    ) -> None:
        memory = self.create_legacy_memory()
        change_line = "此前先定义验证标准的做法被当前决定替代。"
        self.write_day("2026-07-20", change_line)
        request = self.request("4")
        preparation = agent_v1.prepare_agent_run(
            self.vault,
            request,
            agent_v1.sha256_file(agent_v1.request_path(self.vault, request["id"])),
            maximum_chars=120_000,
        )
        query = search_query("替代 绝不出现", date_to="2026-07-31")
        bundle, _, match_count, _, _ = agent_v1._materialize_investigation(
            preparation,
            investigate(
                "revise",
                target_memory_id=memory["memory_id"],
                queries=[query],
            )["arguments"],
        )
        matching = [
            item
            for item in bundle["evidence_catalog"]
            if item["file"] == "2026-07-20.md" and item["quote"] == change_line
        ]
        self.assertEqual(match_count, 1)
        self.assertEqual(len(matching), 1)
        self.assertIn(matching[0]["ref_id"], bundle["change_signal_refs"])
        self.assertIn("history_search", matching[0]["origins"])

        legacy_matches = agent_v1._literal_history_search(preparation, query)
        self.assertEqual(legacy_matches, [])

    def test_new_identity_evidence_errors_send_finite_repair_codes_and_recover(
        self,
    ) -> None:
        expected_codes = {
            "statement": "identity_statement_mismatch",
            "scope": "identity_scope_mismatch",
        }
        for field, expected_code in expected_codes.items():
            with self.subTest(field=field):
                (
                    response,
                    run,
                    provider,
                    bad_action,
                ) = self.run_new_identity_repair_case(field)
                self.assertEqual(response["status"], "updated")
                self.assertEqual(response["memory"]["revision"], 1)
                self.assertEqual(
                    [step["result_kind"] for step in run["steps"]],
                    ["investigation_materialized", "rejected", "memory_updated"],
                )
                repair_payload = json.loads(provider.messages[2][1]["content"])
                self.assertEqual(len(provider.messages[1]), 2)
                terminal_payload = json.loads(
                    provider.messages[1][1]["content"]
                )
                identity = terminal_payload["evidence_bundle"][
                    "stable_new_identity"
                ]
                self.assertEqual(
                    set(identity),
                    {
                        "status",
                        "required_statement",
                        "required_scope",
                        "eligible_evidence_refs",
                    },
                )
                self.assertEqual(identity["status"], "stable")
                self.assertEqual(
                    identity["required_statement"],
                    response["memory"]["statement"],
                )
                self.assertEqual(
                    identity["required_scope"], "产品方案评审"
                )
                self.assertEqual(
                    identity["eligible_evidence_refs"],
                    bad_action["arguments"]["evidence_refs"],
                )
                self.assertEqual(
                    terminal_payload["output_contract"]["finalize_patch"][
                        "required_identity"
                    ],
                    {
                        "source": "evidence_bundle.stable_new_identity",
                        "when_status": "stable",
                        "statement": "must_equal_required_statement",
                        "scope": "must_equal_required_scope",
                        "evidence_refs": "agent_selects_eligible_cross_date_refs",
                    },
                )
                validation_error = repair_payload["previous_validation_error"]
                self.assertEqual(validation_error["error_kind"], "evidence")
                self.assertEqual(
                    validation_error["patch_error_code"], expected_code
                )
                self.assertEqual(
                    validation_error["required_next_action"], "finalize_patch"
                )
                self.assertEqual(
                    validation_error["repair_source"],
                    "materialized_evidence_bundle",
                )
                self.assertEqual(
                    repair_payload["evidence_bundle"]["stable_new_identity"],
                    identity,
                )

    def test_revise_statement_must_copy_latest_support_quote_then_repairs(
        self,
    ) -> None:
        memory = self.create_legacy_memory()
        change_quote = "此前先定义验证标准的做法被当前决定替代。"
        latest_quote = "当前阶段先检查失败条件，再决定是否进入实现。"
        self.write_day("2026-08-20", change_quote)
        self.write_day("2026-08-24", latest_quote)
        request = self.request("6", as_of="2026-08-25")
        correct_patch = {
            "operation": "revise",
            "target_memory_id": memory["memory_id"],
            "expected_revision": 1,
            "title": "先检查失败条件",
            "statement": latest_quote,
            "scope": memory["scope"],
            "uncertainty": "medium",
            "evidence": [
                {"file": "2026-08-20.md", "line": 2, "quote": change_quote},
                {"file": "2026-08-24.md", "line": 2, "quote": latest_quote},
            ],
            "counterevidence": [
                {
                    "file": "2026-08-02.md",
                    "line": 2,
                    "quote": "这次仍然先定义验证标准，再排实现顺序。",
                }
            ],
        }
        correct_action = workflow_finalize(correct_patch)
        bad_action = json.loads(
            json.dumps(correct_action, ensure_ascii=False)
        )
        bad_action["arguments"]["statement"] = (
            "当前阶段改为先检查失败条件再进入实现。"
        )
        provider = RecordingFake(
            [
                investigate("revise", target_memory_id=memory["memory_id"]),
                bad_action,
                correct_action,
            ]
        )
        response, _ = process_agent_request(
            self.vault,
            request["id"],
            provider_client=provider,
            provider_name="mock-agentic-workflow",
            model="fixture",
            pricing=Pricing(),
            budget=AgentBudget(),
        )
        self.assertEqual(response["status"], "updated")
        self.assertEqual(response["memory"]["statement"], latest_quote)
        self.assertEqual(
            response["trace"]["actions"],
            ["investigate", "finalize_patch", "finalize_patch"],
        )
        run = json.loads(
            run_path(self.vault, response["run_id"]).read_text(encoding="utf-8")
        )
        self.assertEqual(
            [step["result_kind"] for step in run["steps"]],
            ["investigation_materialized", "rejected", "memory_updated"],
        )
        repair_messages = provider.messages[2]
        self.assertEqual(
            [message["role"] for message in repair_messages],
            ["system", "user"],
        )
        repair_payload = json.loads(repair_messages[1]["content"])
        validation_error = repair_payload["previous_validation_error"]
        self.assertEqual(validation_error["error_kind"], "evidence")
        self.assertEqual(
            validation_error["patch_error_code"],
            "statement_not_latest_evidence",
        )
        self.assertEqual(
            validation_error["required_next_action"], "finalize_patch"
        )
        self.assertEqual(
            repair_payload["previous_decision"], bad_action["arguments"]
        )
        self.assertEqual(
            repair_payload["evidence_bundle"]["max_patch_repairs_remaining"],
            0,
        )

    def test_workflow_repair_uses_fresh_context_with_previous_decision_only(
        self,
    ) -> None:
        response, _, provider, bad_action = self.run_new_identity_repair_case(
            "statement"
        )
        self.assertEqual(response["status"], "updated")
        self.assertEqual(len(provider.messages), 3)
        repair_messages = provider.messages[2]
        self.assertEqual(
            [message["role"] for message in repair_messages],
            ["system", "user"],
        )
        repair_payload = json.loads(repair_messages[1]["content"])
        self.assertEqual(
            set(repair_payload),
            {
                "evidence_bundle",
                "output_contract",
                "previous_decision",
                "previous_validation_error",
            },
        )
        self.assertEqual(
            repair_payload["previous_decision"], bad_action["arguments"]
        )
        self.assertNotIn("schema_version", repair_payload["previous_decision"])
        self.assertNotIn("action", repair_payload["previous_decision"])
        self.assertNotIn("reason_code", repair_payload["previous_decision"])
        self.assertNotIn("messages", repair_payload)
        self.assertNotIn("completion", repair_payload)
        serialized = json.dumps(repair_messages, ensure_ascii=False)
        self.assertNotIn('"role": "assistant"', serialized)
        self.assertNotIn("<mission", serialized)
        self.assertNotIn("<recent_records>", serialized)

    def test_wrong_phase_action_is_rejected_without_counting_a_tool(self) -> None:
        request = self.request("1")
        response = self.run_workflow(
            request["id"],
            [legacy_read("mem_" + "0" * 24), investigate("new"), finish()],
        )
        self.assertEqual(response["status"], "no_change")
        self.assertEqual(
            response["trace"]["actions"], ["read_memory", "investigate", "finish"]
        )
        self.assertEqual(response["trace"]["tool_calls"], 1)
        run = json.loads(
            run_path(self.vault, response["run_id"]).read_text(encoding="utf-8")
        )
        self.assertEqual(run["steps"][0]["error_kind"], "workflow_phase")

    def test_terminal_patch_must_bind_materialized_target_and_can_repair_once(self) -> None:
        memory = self.create_legacy_memory()
        self.write_day("2026-08-10", "本次改为先检查失败条件，再决定是否进入实现。")
        request = self.request("2")
        correct = {
            "operation": "revise",
            "target_memory_id": memory["memory_id"],
            "expected_revision": 1,
            "title": "先检查失败条件",
            "statement": "本次改为先检查失败条件，再决定是否进入实现。",
            "scope": memory["scope"],
            "uncertainty": "medium",
            "evidence": [
                {
                    "file": "2026-08-10.md",
                    "line": 2,
                    "quote": "本次改为先检查失败条件，再决定是否进入实现。",
                }
            ],
            "counterevidence": [
                {
                    "file": "2026-08-02.md",
                    "line": 2,
                    "quote": "这次仍然先定义验证标准，再排实现顺序。",
                }
            ],
        }
        response = self.run_workflow(
            request["id"],
            [
                investigate("revise", target_memory_id=memory["memory_id"]),
                workflow_finalize(self.new_patch()),
                workflow_finalize(correct),
            ],
        )
        self.assertEqual(response["status"], "updated")
        self.assertEqual(
            response["trace"]["actions"],
            ["investigate", "finalize_patch", "finalize_patch"],
        )
        run = json.loads(
            run_path(self.vault, response["run_id"]).read_text(encoding="utf-8")
        )
        self.assertEqual(run["steps"][1]["error_kind"], "action")
        self.assertEqual(run["steps"][2]["result_kind"], "memory_updated")


if __name__ == "__main__":
    unittest.main()
