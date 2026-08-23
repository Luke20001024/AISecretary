#!/usr/bin/env python3

from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
RUNNER_PATH = (
    REPO / "context-agent" / "eval" / "agent-v1" / "run_live_preflight.py"
)


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "remember_agent_v1_live_preflight", RUNNER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 live preflight runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ForwardingModuleProxy:
    def __init__(self, target) -> None:
        self.__name__ = target.__name__
        self._target = target

    def __getattr__(self, name):
        return getattr(self._target, name)


class FakeCompletion:
    def __init__(
        self,
        action: dict,
        *,
        usage: dict | None = None,
        model: str = "deepseek-v4-pro",
    ) -> None:
        self.content = json.dumps(action, ensure_ascii=False)
        self.usage = (
            {
                "prompt_tokens": 120,
                "completion_tokens": 60,
                "total_tokens": 180,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 120,
            }
            if usage is None
            else usage
        )
        self.request_id = "provider-request-not-public"
        self.model = model


class QueueProvider:
    def __init__(
        self,
        actions: list[dict],
        *,
        usage: dict | None = None,
        model: str = "deepseek-v4-pro",
    ) -> None:
        self.actions = list(actions)
        self.usage = usage
        self.model = model
        self.calls = 0

    def complete(self, messages):
        del messages
        self.calls += 1
        if not self.actions:
            raise AssertionError("fake provider action 用尽")
        return FakeCompletion(
            self.actions.pop(0), usage=self.usage, model=self.model
        )


class RememberAgentV1LivePreflightTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()

    def config(self, **changes):
        values = {
            "model": "deepseek-v4-pro",
            "max_batch_calls": 30,
            "max_batch_tokens": 250_000,
            "max_batch_cost_usd": 0.20,
        }
        values.update(changes)
        return self.runner.PreflightConfig(**values)

    def finish(self):
        return {
            "schema_version": "1.0",
            "action": "finish",
            "reason_code": "no_material_change",
            "arguments": {"reason": "no_change"},
        }

    def actions(self, case_id: str, arm: str) -> list[dict]:
        spec = self.runner.CASE_BY_ID[case_id]
        source_root = self.runner._case_source_root(spec)
        terminal = self.runner.expected_action(spec, source_root)
        if arm != "A1":
            return [terminal]
        result: list[dict] = []
        for tool in spec.a1_trajectory[:-1]:
            if tool == "read_memory":
                memory_id = self.runner._seed_memory(
                    source_root, spec.seed_key
                )["memory_id"]
                result.append(
                    {
                        "schema_version": "1.0",
                        "action": "read_memory",
                        "reason_code": "inspect_existing",
                        "arguments": {"memory_id": memory_id},
                    }
                )
            elif tool == "search_history":
                result.append(
                    {
                        "schema_version": "1.0",
                        "action": "search_history",
                        "reason_code": (
                            "check_counterevidence"
                            if case_id
                            in {"current_boundary_stop", "history_search_revise"}
                            else "need_history_evidence"
                        ),
                        "arguments": {
                            "query": spec.search_query,
                            "date_from": spec.search_date_from,
                            "date_to": spec.search_date_to,
                            "limit": 5,
                        },
                    }
                )
        result.append(terminal)
        return result

    def providers(self):
        return {
            (spec.case_id, arm): QueueProvider(self.actions(spec.case_id, arm))
            for spec in self.runner.CASES
            for arm in self.runner.ARMS
        }

    def factory(self, providers):
        def make(case_id, arm, config):
            self.assertEqual("deepseek-v4-pro", config.model)
            return providers[(case_id, arm)]

        return make

    def run_preflight(self, providers, config=None):
        config = config or self.config()
        frozen = self.runner.freeze_contract(config)
        return self.runner.run_live_preflight(
            config,
            expected_plan_sha256=self.runner.plan_sha256(config, frozen),
            provider_factory=self.factory(providers),
        )

    def test_plan_only_is_zero_call_and_matrix_is_exact(self) -> None:
        environment = dict(os.environ)
        environment["DEEPSEEK_API_KEY"] = "token-secret-sentinel"
        result = subprocess.run(
            [sys.executable, str(RUNNER_PATH)],
            cwd=REPO,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("remember_agent_live_preflight.v4", report["schema_version"])
        self.assertEqual("plan_only", report["mode"])
        self.assertEqual(0, report["summary"]["batch"]["calls"])
        self.assertEqual(
            "remember-agent-preflight-6case-v5",
            report["frozen"]["matrix_version"],
        )
        self.assertEqual(
            {
                "policy_version": (
                    "a1-trajectory-semantic-search-authorized-sources-v2"
                ),
                "read_memory": "exact_arguments_result",
                "search_history": "semantic_action_result_authorized_sources",
                "expected_tools": "a1_trajectory_nonterminal",
                "max_search_results": 5,
                "complete_response_and_run_sources_required": True,
            },
            report["frozen"]["a1_tool_acceptance"],
        )
        self.assertEqual(
            [
                "direct_stop",
                "profile_only_reinforce",
                "history_search_new",
                "current_boundary_stop",
                "history_search_revise",
                "revision_conflict",
            ],
            report["frozen"]["cases"],
        )
        self.assertEqual(["W0", "W1", "A1"], report["frozen"]["arms"])
        self.assertEqual(30, report["limits"]["max_batch_calls"])
        self.assertEqual(
            {
                "baseline_calls": 12,
                "ideal_a1_calls": 12,
                "ideal_total_calls": 24,
                "legal_a1_max_calls": 18,
                "legal_total_max_calls": 30,
            },
            report["frozen"]["call_budget"],
        )
        self.assertEqual(250_000, report["limits"]["max_batch_tokens"])
        self.assertEqual(0.20, report["limits"]["max_batch_cost_usd"])
        self.assertNotIn("token-secret-sentinel", result.stdout)
        self.assertNotIn("/Users/", result.stdout)
        with self.assertRaises(self.runner.PreflightAbort) as raised:
            self.runner.build_plan(self.config(max_batch_calls=29))
        self.assertEqual("call_limit", raised.exception.code)
        historical_v2 = json.loads(json.dumps(report))
        historical_v2["schema_version"] = "remember_agent_live_preflight.v2"
        with self.assertRaises(self.runner.PreflightAbort):
            self.runner.validate_public_report(historical_v2)

    def test_live_requires_both_confirmation_and_exact_plan_before_provider(self) -> None:
        called = False

        def forbidden(*args):
            nonlocal called
            called = True
            raise AssertionError("preflight gate 前不得构造 provider")

        with mock.patch.object(self.runner, "default_provider_factory", forbidden):
            code = self.runner.main(["--live"])
        self.assertEqual(2, code)
        self.assertFalse(called)
        with self.assertRaises(self.runner.PreflightAbort) as raised:
            self.runner.run_live_preflight(
                self.config(),
                expected_plan_sha256="0" * 64,
                provider_factory=forbidden,
            )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(called)

    def test_pre_call_cli_failure_remains_unexecuted(self) -> None:
        with mock.patch("sys.stderr") as stderr:
            code = self.runner.main(["--live"])
        self.assertEqual(2, code)
        serialized = "".join(
            str(call.args[0]) for call in stderr.write.call_args_list if call.args
        )
        self.assertIn('"executed":false', serialized)
        self.assertIn('"stop_code":"confirmation_required"', serialized)
        self.assertNotIn('"summary"', serialized)

    def test_timeout_is_frozen_and_plan_mismatch_precedes_provider(self) -> None:
        original = self.config(timeout=60.0)
        changed = self.config(timeout=61.0)
        original_frozen = self.runner.freeze_contract(original)
        changed_frozen = self.runner.freeze_contract(changed)
        original_plan = self.runner.plan_sha256(original, original_frozen)
        changed_plan = self.runner.plan_sha256(changed, changed_frozen)
        self.assertNotEqual(original_plan, changed_plan)
        self.assertEqual(
            60.0,
            self.runner._frozen_public(original, original_frozen)[
                "timeout_seconds"
            ],
        )
        self.assertEqual(
            61.0,
            self.runner._frozen_public(changed, changed_frozen)[
                "timeout_seconds"
            ],
        )
        self.assertEqual(
            61.0,
            self.runner.default_provider_factory(
                "direct_stop", "W0", changed
            ).timeout,
        )
        called = False

        def forbidden(*args):
            nonlocal called
            called = True
            raise AssertionError("plan mismatch 前不得构造 provider")

        with self.assertRaises(self.runner.PreflightAbort) as raised:
            self.runner.run_live_preflight(
                changed,
                expected_plan_sha256=original_plan,
                provider_factory=forbidden,
            )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(called)

    def test_fake_matrix_executes_all_six_cases_and_three_arms(self) -> None:
        providers = self.providers()
        report = self.run_preflight(providers)
        self.assertEqual("completed", report["status"])
        self.assertEqual("none", report["stop_code"])
        self.assertTrue(report["summary"]["batch_quality"])
        self.assertEqual(6, report["summary"]["cases_completed"])
        self.assertEqual(18, len(report["runs"]))
        self.assertEqual(24, report["summary"]["batch"]["calls"])
        self.assertEqual(6, report["summary"]["batch"]["by_arm"]["W0"]["calls"])
        self.assertEqual(6, report["summary"]["batch"]["by_arm"]["W1"]["calls"])
        self.assertEqual(12, report["summary"]["batch"]["by_arm"]["A1"]["calls"])
        self.assertTrue(all(run["quality"]["passed"] for run in report["runs"]))
        by_key = {(run["case"], run["arm"]): run for run in report["runs"]}
        for spec in self.runner.CASES:
            for arm in self.runner.ARMS:
                self.assertEqual(
                    self.runner._expected_trajectory(spec, arm),
                    by_key[(spec.case_id, arm)]["trajectory"],
                )
                self.assertTrue(
                    by_key[(spec.case_id, arm)]["quality"]["checks"][
                        "tool_contract_valid"
                    ]
                )
        for arm in self.runner.ARMS:
            conflict = by_key[("revision_conflict", arm)]
            self.assertEqual("stale", conflict["status"])
            self.assertTrue(conflict["quality"]["checks"]["stale_no_agent_write"])
            self.assertTrue(conflict["quality"]["checks"]["old_revision_preserved"])
            self.assertTrue(conflict["quality"]["checks"]["user_action_wins"])
        boundary = by_key[("current_boundary_stop", "A1")]
        self.assertEqual(
            ["read_memory", "finish"],
            boundary["trajectory"],
        )
        self.assertEqual("no_change", boundary["status"])
        self.assertTrue(boundary["quality"]["checks"]["outcome_expected"])
        for arm in self.runner.ARMS:
            self.assertEqual(
                "no_change",
                by_key[("current_boundary_stop", arm)]["status"],
            )
        revise = by_key[("history_search_revise", "A1")]
        self.assertEqual("updated", revise["status"])
        self.assertEqual(
            ["read_memory", "search_history", "finalize_patch"],
            revise["trajectory"],
        )
        self.assertTrue(revise["quality"]["checks"]["operation_expected"])
        self.assertTrue(revise["quality"]["checks"]["evidence_exact"])
        self.assertTrue(revise["quality"]["checks"]["counterevidence_exact"])
        new_memory = by_key[("history_search_new", "A1")]
        self.assertTrue(new_memory["quality"]["checks"]["revision_expected"])
        self.assertTrue(new_memory["quality"]["checks"]["statement_exact"])
        self.assertTrue(new_memory["quality"]["checks"]["scope_exact"])
        self.assertNotIn(
            "statement_scope_expected", new_memory["quality"]["checks"]
        )
        serialized = json.dumps(report, ensure_ascii=False)
        for marker in (
            self.runner.METRIC_STATEMENT,
            self.runner.ACTIVATION_STATEMENT,
            self.runner.REVISION_STATEMENT,
            self.runner.REVISION_SIGNAL,
            self.runner.RETENTION_SUPPORT,
            "provider-request-not-public",
            "memento-preflight-",
        ):
            self.assertNotIn(marker, serialized)

    def test_quality_failure_finishes_current_three_arms_then_stops(self) -> None:
        providers = self.providers()
        providers[("direct_stop", "W0")] = QueueProvider(
            [
                {
                    "schema_version": "1.0",
                    "action": "finish",
                    "reason_code": "insufficient_evidence",
                    "arguments": {"reason": "insufficient_evidence"},
                }
            ]
        )
        report = self.run_preflight(providers)
        self.assertEqual("stopped", report["status"])
        self.assertEqual("quality_gate", report["stop_code"])
        self.assertEqual(3, len(report["runs"]))
        self.assertEqual(1, providers[("direct_stop", "A1")].calls)
        self.assertEqual(0, providers[("profile_only_reinforce", "W0")].calls)
        self.assertEqual(1, report["summary"]["cases_completed"])
        self.assertFalse(report["summary"]["batch_quality"])

    def test_fourth_case_wrong_stop_reason_is_detailed_quality_gate(self) -> None:
        providers = self.providers()
        provider = providers[("current_boundary_stop", "A1")]
        provider.actions[-1] = {
            "schema_version": "1.0",
            "action": "finish",
            "reason_code": "insufficient_evidence",
            "arguments": {"reason": "insufficient_evidence"},
        }
        report = self.run_preflight(providers)
        self.assertTrue(report["executed"])
        self.assertEqual("stopped", report["status"])
        self.assertEqual("quality_gate", report["stop_code"])
        self.assertEqual(15, report["summary"]["batch"]["calls"])
        self.assertEqual(
            {"W0": 4, "W1": 4, "A1": 7},
            {
                arm: report["summary"]["batch"]["by_arm"][arm]["calls"]
                for arm in self.runner.ARMS
            },
        )
        self.assertEqual(12, len(report["runs"]))
        self.assertEqual(4, report["summary"]["cases_completed"])
        last = report["runs"][-1]
        self.assertEqual("current_boundary_stop", last["case"])
        self.assertEqual("A1", last["arm"])
        self.assertEqual("insufficient_evidence", last["status"])
        self.assertEqual("none", last["error_code"])
        self.assertEqual(
            ["read_memory", "finish"], last["trajectory"]
        )
        self.assertTrue(last["quality"]["checks"]["audit_clean"])
        self.assertFalse(last["quality"]["checks"]["outcome_expected"])
        self.assertFalse(last["quality"]["passed"])

    def test_a1_wrong_search_query_or_date_misses_authorized_source(self) -> None:
        config = self.config()
        spec = self.runner.CASE_BY_ID["history_search_new"]
        for mutation in ("query", "date_to"):
            with self.subTest(mutation=mutation):
                actions = self.actions(spec.case_id, "A1")
                search_action = json.loads(json.dumps(actions[0]))
                if mutation == "query":
                    search_action["arguments"]["query"] = "不存在的搜索短语"
                else:
                    search_action["arguments"]["date_to"] = "2026-07-19"
                actions[0] = search_action
                actions[-1] = self.finish()
                with self.runner.pairing.secure_batch_scratch() as scratch:
                    with self.runner.isolated_case_vault(scratch, spec) as vault:
                        meter = self.runner.PreflightMeter(
                            config,
                            self.runner.core.pricing_for_model(config.model),
                        )
                        provider = self.runner.pairing.MeteredProvider(
                            QueueProvider(actions), meter, "A1"
                        )
                        raw = self.runner._agent_run(
                            vault, provider, config, spec, "A1"
                        )
                        quality = self.runner._quality(
                            vault, spec, "A1", raw, None
                        )
                self.assertEqual("no_change", raw["status"])
                self.assertEqual("none", raw["error_code"])
                self.assertTrue(raw["audit_clean"])
                self.assertEqual(0, raw["tool_contract"][0]["result_count"])
                self.assertNotIn(
                    "2026-07-20.md",
                    {item["file"] for item in raw["response_source_hashes"]},
                )
                self.assertFalse(quality["checks"]["tool_contract_valid"])

    def test_a1_semantic_search_accepts_autonomous_authorized_query(self) -> None:
        providers = self.providers()
        provider = providers[("history_search_new", "A1")]
        search_action = json.loads(json.dumps(provider.actions[0]))
        search_action["arguments"].update(
            {
                "query": "目标指标",
                "date_from": None,
                "date_to": None,
                "limit": 5,
            }
        )
        provider.actions[0] = search_action

        spec = self.runner.CASE_BY_ID["history_search_new"]
        with self.runner.pairing.secure_batch_scratch() as scratch:
            with self.runner.isolated_case_vault(scratch, spec) as vault:
                expected_hash = self.runner._expected_tool_contract(vault, spec)[0][
                    "arguments_sha256"
                ]
        actual_hash = self.runner._sha(
            self.runner._canonical(search_action["arguments"]).encode("utf-8")
        )
        self.assertNotEqual(expected_hash, actual_hash)

        report = self.run_preflight(providers)
        self.assertEqual("completed", report["status"])
        self.assertEqual("none", report["stop_code"])
        by_key = {(run["case"], run["arm"]): run for run in report["runs"]}
        run = by_key[("history_search_new", "A1")]
        self.assertTrue(run["quality"]["checks"]["tool_contract_valid"])
        self.assertTrue(run["quality"]["checks"]["evidence_exact"])
        self.assertTrue(run["quality"]["checks"]["source_hashes_exact"])
        self.assertTrue(run["quality"]["passed"])
        self.assertTrue(
            by_key[("history_search_new", "W0")]["quality"]["checks"][
                "tool_contract_valid"
            ]
        )
        self.assertTrue(
            by_key[("history_search_new", "W1")]["quality"]["checks"][
                "tool_contract_valid"
            ]
        )

    def test_a1_semantic_search_rejects_extra_source(self) -> None:
        config = self.config()
        spec = self.runner.CASE_BY_ID["history_search_new"]
        with self.runner.pairing.secure_batch_scratch() as scratch:
            with self.runner.isolated_case_vault(scratch, spec) as vault:
                extra = vault / "2026-07-21.md"
                self.runner.pairing._secure_write_clone(
                    extra,
                    (vault / "2026-07-20.md").read_bytes(),
                )
                actions = self.actions(spec.case_id, "A1")
                actions[0] = json.loads(json.dumps(actions[0]))
                actions[0]["arguments"].update(
                    {
                        "query": "目标指标",
                        "date_from": None,
                        "date_to": None,
                        "limit": 5,
                    }
                )
                meter = self.runner.PreflightMeter(
                    config, self.runner.core.pricing_for_model(config.model)
                )
                provider = self.runner.pairing.MeteredProvider(
                    QueueProvider(actions), meter, "A1"
                )
                raw = self.runner._agent_run(vault, provider, config, spec, "A1")
                quality = self.runner._quality(vault, spec, "A1", raw, None)
                authorized = self.runner._authorized_source_hashes(vault, spec)

        self.assertEqual("updated", raw["status"])
        self.assertEqual("none", raw["error_code"])
        self.assertTrue(raw["audit_clean"])
        self.assertNotEqual(authorized, raw["response_source_hashes"])
        self.assertNotEqual(authorized, raw["run_source_hashes"])
        self.assertIn(
            "2026-07-21.md",
            {item["file"] for item in raw["response_source_hashes"]},
        )
        self.assertFalse(quality["checks"]["tool_contract_valid"])
        self.assertEqual(
            {"tool_contract_valid"},
            {name for name, passed in quality["checks"].items() if not passed},
        )

    def test_a1_wrong_but_existing_memory_target_is_clean_quality_failure(self) -> None:
        config = self.config()
        spec = self.runner.CASE_BY_ID["current_boundary_stop"]
        with self.runner.pairing.secure_batch_scratch() as scratch:
            with self.runner.isolated_case_vault(scratch, spec) as vault:
                self.runner.pairing._secure_write_clone(
                    vault / "2026-07-20.md",
                    (self.runner.SCENARIO_ROOT / "2026-07-20.md").read_bytes(),
                )
                decoy = self.runner._seed_memory(vault, "metric")
                decoy_path = self.runner.agent_v1._memory_path(
                    vault, decoy["memory_id"], 1
                )
                self.runner.core.atomic_write_json(decoy_path, decoy)
                activation = self.runner._seed_memory(vault, "activation")
                seed_sha = self.runner.core.sha256_file(
                    self.runner.agent_v1._memory_path(
                        vault, activation["memory_id"], 1
                    )
                )

                actions = self.actions(spec.case_id, "A1")
                actions[0] = json.loads(json.dumps(actions[0]))
                actions[0]["arguments"]["memory_id"] = decoy["memory_id"]
                meter = self.runner.PreflightMeter(
                    config, self.runner.core.pricing_for_model(config.model)
                )
                provider = self.runner.pairing.MeteredProvider(
                    QueueProvider(actions), meter, "A1"
                )
                raw = self.runner._agent_run(
                    vault, provider, config, spec, "A1"
                )
                decoy_path.unlink()
                quality = self.runner._quality(
                    vault, spec, "A1", raw, seed_sha
                )

        self.assertEqual("no_change", raw["status"])
        self.assertEqual("none", raw["error_code"])
        self.assertTrue(raw["audit_clean"])
        self.assertEqual(
            ["read_memory", "finish"], raw["trajectory"]
        )
        self.assertFalse(quality["checks"]["tool_contract_valid"])
        self.assertEqual(
            {"tool_contract_valid"},
            {name for name, passed in quality["checks"].items() if not passed},
        )

    def test_usage_model_provider_and_budget_fail_closed(self) -> None:
        cases = [
            (QueueProvider([self.finish()], usage={}), "usage_missing"),
            (QueueProvider([self.finish()], model="unexpected-model"), "security"),
        ]
        for first, code in cases:
            providers = self.providers()
            providers[("direct_stop", "W0")] = first
            report = self.run_preflight(providers)
            self.assertEqual(code, report["stop_code"])
            self.assertEqual(1, report["summary"]["batch"]["calls"])
            self.assertEqual(0, providers[("direct_stop", "W1")].calls)
            self.assertFalse(report["summary"]["batch"]["cost_complete"])

        providers = self.providers()
        report = self.run_preflight(
            providers, self.config(max_batch_tokens=1)
        )
        self.assertEqual("token_limit", report["stop_code"])
        self.assertEqual(0, report["summary"]["batch"]["calls"])

    def test_agent_budget_projects_budget_while_loop_remains_agent_error(self) -> None:
        self.assertIn("budget", self.runner.PUBLIC_ERROR_CODES)

        budget_config = self.config(
            budget=self.runner.agent_v1.AgentBudget(max_total_tokens=100)
        )
        direct_stop = self.runner.CASE_BY_ID["direct_stop"]
        with self.runner.pairing.secure_batch_scratch() as scratch:
            with self.runner.isolated_case_vault(scratch, direct_stop) as vault:
                meter = self.runner.PreflightMeter(
                    budget_config,
                    self.runner.core.pricing_for_model(budget_config.model),
                )
                paid = QueueProvider([self.finish()])
                raw = self.runner._agent_run(
                    vault,
                    self.runner.pairing.MeteredProvider(paid, meter, "A1"),
                    budget_config,
                    direct_stop,
                    "A1",
                )
        self.assertEqual("budget_exhausted", raw["status"])
        self.assertEqual("budget", raw["error_code"])
        # The paid completion is now parsed for public audit, but the
        # overshoot action is never executed and cannot trigger another call.
        self.assertEqual(["finish"], raw["trajectory"])
        self.assertEqual(1, paid.calls)
        self.assertEqual(1, meter.calls)
        self.assertEqual(180, meter.tokens)

        loop_config = self.config()
        history = self.runner.CASE_BY_ID["history_search_new"]
        repeated = self.actions(history.case_id, "A1")[0]
        with self.runner.pairing.secure_batch_scratch() as scratch:
            with self.runner.isolated_case_vault(scratch, history) as vault:
                meter = self.runner.PreflightMeter(
                    loop_config,
                    self.runner.core.pricing_for_model(loop_config.model),
                )
                paid = QueueProvider([repeated, repeated])
                raw = self.runner._agent_run(
                    vault,
                    self.runner.pairing.MeteredProvider(paid, meter, "A1"),
                    loop_config,
                    history,
                    "A1",
                )
        self.assertEqual("budget_exhausted", raw["status"])
        self.assertEqual("agent_error", raw["error_code"])
        self.assertEqual(["search_history", "search_history"], raw["trajectory"])
        self.assertEqual(2, paid.calls)
        self.assertEqual(2, meter.calls)

    def test_partial_usage_and_provider_error_model_mismatch_are_fail_closed(self) -> None:
        partial = {
            "prompt_tokens": 120,
            "completion_tokens": 60,
            "total_tokens": 180,
        }
        providers = self.providers()
        providers[("direct_stop", "W0")] = QueueProvider(
            [self.finish()], usage=partial
        )
        report = self.run_preflight(providers)
        self.assertEqual("usage_missing", report["stop_code"])
        self.assertFalse(report["summary"]["batch"]["cost_complete"])
        self.assertEqual(1, report["runs"][0]["usage"]["model_calls"])
        self.assertTrue(report["runs"][0]["usage"]["usage_missing"])
        self.assertIsNone(report["runs"][0]["usage"]["cost_usd"])

        self_runner = self.runner

        class ErrorProvider:
            calls = 0

            def complete(self, messages):
                del messages
                self.calls += 1
                raise self_runner.deepseek_provider.ProviderError(
                    "finite failure",
                    usage={
                        "prompt_tokens": 120,
                        "completion_tokens": 60,
                        "total_tokens": 180,
                        "prompt_cache_hit_tokens": 0,
                        "prompt_cache_miss_tokens": 120,
                    },
                    model="unexpected-model",
                )

        providers = self.providers()
        providers[("direct_stop", "W0")] = ErrorProvider()
        report = self.run_preflight(providers)
        self.assertEqual("security", report["stop_code"])
        self.assertEqual(180, report["summary"]["batch"]["tokens"])
        self.assertFalse(report["summary"]["batch"]["cost_complete"])
        self.assertIsNone(report["runs"][0]["usage"]["cost_usd"])

    def test_partial_usage_model_and_provider_failures_keep_executed_meter(self) -> None:
        partial = {
            "prompt_tokens": 120,
            "completion_tokens": 60,
            "total_tokens": 180,
        }
        providers = self.providers()
        providers[("direct_stop", "W0")] = QueueProvider(
            [self.finish()], usage=partial, model="unexpected-model"
        )
        report = self.run_preflight(providers)
        self.assertTrue(report["executed"])
        self.assertEqual("security", report["stop_code"])
        self.assertEqual(180, report["summary"]["batch"]["tokens"])
        self.assertEqual(180, report["runs"][0]["usage"]["total_tokens"])
        self.assertFalse(report["summary"]["batch"]["cost_complete"])
        self.assertIsNone(report["runs"][0]["usage"]["cost_usd"])

        providers = self.providers()
        providers[("direct_stop", "A1")] = QueueProvider(
            [self.finish()], usage=partial, model="unexpected-model"
        )
        report = self.run_preflight(providers)
        self.assertTrue(report["executed"])
        self.assertEqual("security", report["stop_code"])
        self.assertEqual(540, report["summary"]["batch"]["tokens"])
        self.assertEqual(180, report["runs"][-1]["usage"]["total_tokens"])
        self.assertFalse(report["summary"]["batch"]["cost_complete"])
        self.assertIsNone(report["runs"][-1]["usage"]["cost_usd"])

        self_runner = self.runner

        class PartialErrorProvider:
            def __init__(self, model):
                self.model = model
                self.calls = 0

            def complete(self, messages):
                del messages
                self.calls += 1
                raise self_runner.deepseek_provider.ProviderError(
                    "finite provider failure", usage=partial, model=self.model
                )

        for arm in ("W0", "A1"):
            for model, expected_code in (
                (None, "provider_error"),
                ("deepseek-v4-pro", "provider_error"),
                ("unexpected-model", "security"),
            ):
                with self.subTest(arm=arm, model=model):
                    providers = self.providers()
                    providers[("direct_stop", arm)] = PartialErrorProvider(model)
                    report = self.run_preflight(providers)
                    self.assertTrue(report["executed"])
                    self.assertEqual(expected_code, report["stop_code"])
                    expected_tokens = 180 if arm == "W0" else 540
                    self.assertEqual(
                        expected_tokens, report["summary"]["batch"]["tokens"]
                    )
                    failed = report["runs"][-1]
                    self.assertEqual(180, failed["usage"]["total_tokens"])
                    self.assertFalse(report["summary"]["batch"]["cost_complete"])
                    self.assertIsNone(failed["usage"]["cost_usd"])

    def test_late_validator_failure_preserves_executed_emergency_meter(self) -> None:
        config = self.config()
        providers = self.providers()
        original_validate = self.runner.validate_public_report

        def reject_live_report(report):
            if report.get("mode") == "live_synthetic_preflight":
                raise self.runner.PreflightAbort("security")
            return original_validate(report)

        self.runner.validate_public_report = reject_live_report
        try:
            frozen = self.runner.freeze_contract(config)
            with self.assertRaises(self.runner.PreflightAbort) as raised:
                self.runner.run_live_preflight(
                    config,
                    expected_plan_sha256=self.runner.plan_sha256(config, frozen),
                    provider_factory=self.factory(providers),
                )
        finally:
            self.runner.validate_public_report = original_validate
        self.assertEqual("security", raised.exception.code)
        self.assertTrue(raised.exception.executed)
        self.assertEqual(24, raised.exception.batch["calls"])
        self.assertEqual(4320, raised.exception.batch["tokens"])
        self.assertNotIn("runs", raised.exception.batch)

        with mock.patch.object(
            self.runner,
            "run_live_preflight",
            side_effect=self.runner.PreflightAbort(
                "security",
                executed=True,
                batch=raised.exception.batch,
            ),
        ), mock.patch("sys.stderr") as stderr:
            code = self.runner.main(
                [
                    "--live",
                    "--confirm-live",
                    self.runner.LIVE_CONFIRMATION,
                    "--expect-plan-sha256",
                    "0" * 64,
                ]
            )
        self.assertEqual(2, code)
        serialized = "".join(
            str(call.args[0]) for call in stderr.write.call_args_list if call.args
        )
        self.assertIn('"executed":true', serialized)
        self.assertIn('"calls":24', serialized)
        self.assertIn('"tokens":4320', serialized)
        self.assertNotIn("provider-request-not-public", serialized)
        self.assertNotIn("/Users/", serialized)

    def test_provider_error_is_redacted_and_stops_immediately(self) -> None:
        self_runner = self.runner

        class FailingProvider:
            calls = 0

            def complete(self, messages):
                del messages
                self.calls += 1
                raise self_runner.deepseek_provider.ProviderError(
                    "upstream token-secret-sentinel and record text"
                )

        providers = self.providers()
        providers[("direct_stop", "W0")] = FailingProvider()
        report = self.run_preflight(providers)
        self.assertEqual("provider_error", report["stop_code"])
        self.assertEqual(1, report["summary"]["batch"]["calls"])
        self.assertFalse(report["summary"]["batch"]["cost_complete"])
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("token-secret-sentinel", serialized)
        self.assertNotIn("upstream", serialized)

    def test_dangerous_tmpdir_vault_is_rejected_before_provider_and_unchanged(self) -> None:
        config = self.config()
        frozen = self.runner.freeze_contract(config)
        called = False

        def forbidden(*args):
            nonlocal called
            called = True
            raise AssertionError("unsafe scratch 不得构造 provider")

        with tempfile.TemporaryDirectory(prefix="preflight-sentinel-parent-") as parent:
            vault = Path(parent) / "sentinel-vault"
            vault.mkdir(mode=0o700)
            sentinel = vault / "daily.md"
            sentinel.write_text("unchanged", encoding="utf-8")
            sentinel.chmod(0o600)
            before = (sentinel.read_bytes(), stat.S_IMODE(sentinel.stat().st_mode), sorted(vault.iterdir()))
            with mock.patch.dict(
                os.environ, {"TMPDIR": str(vault), "MEMENTO_VAULT": str(vault)}
            ):
                with self.assertRaises(self.runner.pairing.PairingAbort) as raised:
                    self.runner.run_live_preflight(
                        config,
                        expected_plan_sha256=self.runner.plan_sha256(config, frozen),
                        provider_factory=forbidden,
                    )
            after = (sentinel.read_bytes(), stat.S_IMODE(sentinel.stat().st_mode), sorted(vault.iterdir()))
        self.assertEqual("security", raised.exception.code)
        self.assertFalse(called)
        self.assertEqual(before, after)

    def test_fixture_drift_during_clone_stops_before_provider(self) -> None:
        config = self.config()
        original_isolated = self.runner.isolated_case_vault
        called = False

        def forbidden(*args):
            nonlocal called
            called = True
            raise AssertionError("clone 后冻结检查前不得构造 provider")

        @self.runner.contextlib.contextmanager
        def drifting_clone(scratch_root, spec):
            with original_isolated(scratch_root, spec) as vault:
                original_version = self.runner.MATRIX_VERSION
                self.runner.MATRIX_VERSION = original_version + "-drift"
                try:
                    yield vault
                finally:
                    self.runner.MATRIX_VERSION = original_version

        with mock.patch.object(
            self.runner, "isolated_case_vault", drifting_clone
        ):
            frozen = self.runner.freeze_contract(config)
            report = self.runner.run_live_preflight(
                config,
                expected_plan_sha256=self.runner.plan_sha256(config, frozen),
                provider_factory=forbidden,
            )
        self.assertFalse(called)
        self.assertEqual("stopped", report["status"])
        self.assertEqual("security", report["stop_code"])
        self.assertEqual(0, report["summary"]["batch"]["calls"])
        self.assertEqual([], report["runs"])

    def test_plan_binds_runtime_pairing_and_dependencies(self) -> None:
        config = self.config()
        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        called = False

        def forbidden(*args):
            nonlocal called
            called = True
            raise AssertionError("frozen mismatch 前不得构造 provider")

        def bypass(self, arm, messages):
            del self, arm, messages

        with mock.patch.object(self.runner.PreflightMeter, "before_call", bypass):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(original.runner_runtime_sha256, changed.runner_runtime_sha256)
            self.assertNotEqual(expected, self.runner.plan_sha256(config, changed))
            with self.assertRaises(self.runner.PreflightAbort) as raised:
                self.runner.run_live_preflight(
                    config,
                    expected_plan_sha256=expected,
                    provider_factory=forbidden,
                )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(called)

    def test_runner_namespace_behavior_constants_reject_old_plan(self) -> None:
        config = self.config()
        called = False

        def forbidden(*args):
            nonlocal called
            called = True
            raise AssertionError("provider must not be constructed")

        mutations = (
            (
                "TERMINAL_INSTRUCTION",
                self.runner.TERMINAL_INSTRUCTION + "<drift />",
            ),
            (
                "PUBLIC_ERROR_CODES",
                self.runner.PUBLIC_ERROR_CODES | {"runner_drift_test"},
            ),
            ("ARMS", tuple(reversed(self.runner.ARMS))),
            ("METRIC_STATEMENT", self.runner.METRIC_STATEMENT + " drift"),
            (
                "CASE_BY_ID",
                {**self.runner.CASE_BY_ID, "runner_drift": self.runner.CASES[0]},
            ),
            (
                "CONTEXT_AGENT_ROOT",
                self.runner.CONTEXT_AGENT_ROOT.parent / "runner-drift-root",
            ),
        )
        for symbol_name, replacement in mutations:
            with self.subTest(symbol=symbol_name):
                original = self.runner.freeze_contract(config)
                expected = self.runner.plan_sha256(config, original)
                with mock.patch.object(self.runner, symbol_name, replacement):
                    changed = self.runner.freeze_contract(config)
                    self.assertNotEqual(
                        original.runner_runtime_sha256,
                        changed.runner_runtime_sha256,
                    )
                    with self.assertRaises(self.runner.PreflightAbort) as raised:
                        self.runner.run_live_preflight(
                            config,
                            expected_plan_sha256=expected,
                            provider_factory=forbidden,
                        )
                self.assertEqual("plan_mismatch", raised.exception.code)
                self.assertFalse(called)

        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        original_complete = self.runner.ConflictDelegate.complete

        def wrapped_complete(instance, messages):
            return original_complete(instance, messages)

        with mock.patch.object(
            self.runner.ConflictDelegate, "complete", wrapped_complete
        ):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(
                original.runner_runtime_sha256, changed.runner_runtime_sha256
            )
            with self.assertRaises(self.runner.PreflightAbort) as raised:
                self.runner.run_live_preflight(
                    config,
                    expected_plan_sha256=expected,
                    provider_factory=forbidden,
                )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(called)

    def test_project_module_alias_identity_fails_closed_before_provider(self) -> None:
        config = self.config()
        frozen = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, frozen)
        manifest = self.runner._preflight_dependency_contract()
        self.assertEqual(
            {"pairing", "agent_v1", "core", "deepseek_provider"},
            {
                item["alias"]
                for item in manifest["runner_project_module_aliases"]["aliases"]
            },
        )
        called = False

        def forbidden(*args):
            nonlocal called
            called = True
            raise AssertionError("module alias drift must precede provider")

        for alias in ("pairing", "agent_v1", "core", "deepseek_provider"):
            with self.subTest(alias=alias):
                proxy = ForwardingModuleProxy(getattr(self.runner, alias))
                with mock.patch.object(self.runner, alias, proxy):
                    with self.assertRaises(self.runner.PreflightAbort) as raised:
                        self.runner.freeze_contract(config)
                    self.assertEqual("security", raised.exception.code)
                    with self.assertRaises(self.runner.PreflightAbort) as raised:
                        self.runner.run_live_preflight(
                            config,
                            expected_plan_sha256=expected,
                            provider_factory=forbidden,
                        )
                    self.assertEqual("security", raised.exception.code)
        self.assertFalse(called)

        proxy = ForwardingModuleProxy(self.runner.agent_v1)
        with mock.patch.object(self.runner.pairing, "agent_v1", proxy):
            with self.assertRaises(self.runner.PreflightAbort) as raised:
                self.runner.freeze_contract(config)
        self.assertEqual("security", raised.exception.code)

        original_append = self.runner.agent_v1._append_tool_result

        def wrapped_append(*args, **kwargs):
            return original_append(*args, **kwargs)

        with mock.patch.object(
            self.runner.agent_v1, "_append_tool_result", wrapped_append
        ):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(
                frozen.dependency_manifest_sha256,
                changed.dependency_manifest_sha256,
            )
            with self.assertRaises(self.runner.PreflightAbort) as raised:
                self.runner.run_live_preflight(
                    config,
                    expected_plan_sha256=expected,
                    provider_factory=forbidden,
                )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(called)

        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        called = False

        def forbidden_default(*args):
            nonlocal called
            called = True
            raise AssertionError("global/default drift 后不得构造 provider")

        with mock.patch.object(
            self.runner, "default_provider_factory", forbidden_default
        ):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(
                original.runner_runtime_sha256, changed.runner_runtime_sha256
            )
            with self.assertRaises(self.runner.PreflightAbort) as raised:
                self.runner.run_live_preflight(
                    config, expected_plan_sha256=expected
                )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(called)

        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        called = False
        with mock.patch.object(
            self.runner.run_live_preflight,
            "__kwdefaults__",
            {"provider_factory": forbidden_default},
        ):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(
                original.runner_runtime_sha256, changed.runner_runtime_sha256
            )
            with self.assertRaises(self.runner.PreflightAbort) as raised:
                self.runner.run_live_preflight(
                    config, expected_plan_sha256=expected
                )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(called)

        original = self.runner.freeze_contract(config)
        with mock.patch.object(self.runner.core, "calculate_cost", lambda *a, **k: 0.0):
            changed = self.runner.freeze_contract(config)
        self.assertNotEqual(
            original.dependency_manifest_sha256,
            changed.dependency_manifest_sha256,
        )

        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        original_tool_contract = self.runner._expected_tool_contract

        def wrapped_tool_contract(*args, **kwargs):
            return original_tool_contract(*args, **kwargs)

        with mock.patch.object(
            self.runner, "_expected_tool_contract", wrapped_tool_contract
        ):
            changed = self.runner.freeze_contract(config)
        self.assertNotEqual(
            original.runner_runtime_sha256, changed.runner_runtime_sha256
        )
        self.assertNotEqual(expected, self.runner.plan_sha256(config, changed))

        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        original_eq = self.runner.FrozenContract.__eq__
        called = False

        def wrapped_eq(instance, other):
            return original_eq(instance, other)

        def forbidden(*args):
            nonlocal called
            called = True
            raise AssertionError("class runtime drift 后不得构造 provider")

        with mock.patch.object(
            self.runner.FrozenContract, "__eq__", wrapped_eq
        ):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(
                original.runner_runtime_sha256, changed.runner_runtime_sha256
            )
            self.assertNotEqual(
                expected, self.runner.plan_sha256(config, changed)
            )
            with self.assertRaises(self.runner.PreflightAbort) as raised:
                self.runner.run_live_preflight(
                    config,
                    expected_plan_sha256=expected,
                    provider_factory=forbidden,
                )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(called)

        for symbol_name in (
            "validate_user_action",
            "user_action_path",
            "_verify_registered_evidence",
            "derive_stable_new_identity",
            "_evidence_patch_guidance",
            "_validate_patch_semantics",
            "_validate_patch_shape",
            "_has_explicit_signal",
        ):
            original = self.runner.freeze_contract(config)
            expected = self.runner.plan_sha256(config, original)
            original_symbol = getattr(self.runner.agent_v1, symbol_name)

            def wrapped(*args, __delegate=original_symbol, **kwargs):
                return __delegate(*args, **kwargs)

            called = False
            with mock.patch.object(self.runner.agent_v1, symbol_name, wrapped):
                changed = self.runner.freeze_contract(config)
                self.assertNotEqual(
                    original.dependency_manifest_sha256,
                    changed.dependency_manifest_sha256,
                    symbol_name,
                )
                self.assertNotEqual(
                    expected,
                    self.runner.plan_sha256(config, changed),
                    symbol_name,
                )
                with self.assertRaises(self.runner.PreflightAbort) as raised:
                    self.runner.run_live_preflight(
                        config,
                        expected_plan_sha256=expected,
                        provider_factory=forbidden,
                    )
            self.assertEqual("plan_mismatch", raised.exception.code)
            self.assertFalse(called)

        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        called = False
        with mock.patch.object(
            self.runner.agent_v1, "_contains_forbidden_text", lambda value: False
        ):
            with self.assertRaises(
                (self.runner.PreflightAbort, self.runner.pairing.PairingAbort)
            ) as raised:
                self.runner.freeze_contract(config)
            self.assertEqual("security", raised.exception.code)
            with self.assertRaises(
                (self.runner.PreflightAbort, self.runner.pairing.PairingAbort)
            ) as raised:
                self.runner.run_live_preflight(
                    config,
                    expected_plan_sha256=expected,
                    provider_factory=forbidden,
                )
        self.assertEqual("security", raised.exception.code)
        self.assertFalse(called)

        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        changed_templates = (
            *self.runner.agent_v1.STABLE_NEW_SCOPE_EXCLUSION_TEMPLATES,
            r"不要把\s*{trigger}\s*当成本条范围",
        )
        called = False
        with mock.patch.object(
            self.runner.agent_v1,
            "STABLE_NEW_SCOPE_EXCLUSION_TEMPLATES",
            changed_templates,
        ):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(original.policy_sha256, changed.policy_sha256)
            self.assertNotEqual(
                expected,
                self.runner.plan_sha256(config, changed),
            )
            with self.assertRaises(self.runner.PreflightAbort) as raised:
                self.runner.run_live_preflight(
                    config,
                    expected_plan_sha256=expected,
                    provider_factory=forbidden,
                )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(called)

    def test_plan_binds_runner_source_fixture_and_pairing_runtime(self) -> None:
        config = self.config()
        frozen = self.runner.freeze_contract(config)
        self.assertEqual(
            self.runner.pairing._secure_source_file_sha256(RUNNER_PATH),
            frozen.runner_source_sha256,
        )
        fixture_key = "current_boundary_stop_v1/2026-07-17.md"
        fixture_manifest = self.runner._fixture_manifest()
        dedicated_root = self.runner.HISTORY_AMBIGUOUS_FIXTURE_ROOT
        self.assertTrue(dedicated_root.is_relative_to(self.runner.CONTEXT_AGENT_ROOT))
        self.assertEqual(
            self.runner.pairing._secure_source_file_sha256(
                dedicated_root / "2026-07-17.md"
            ),
            fixture_manifest[fixture_key],
        )
        mutated_manifest = dict(fixture_manifest)
        mutated_manifest[fixture_key] = "0" * 64
        mutated_fixture_sha = self.runner._sha(mutated_manifest)
        self.assertNotEqual(frozen.fixture_sha256, mutated_fixture_sha)
        changed_fixture = dataclasses.replace(
            frozen, fixture_sha256=mutated_fixture_sha
        )
        self.assertNotEqual(
            self.runner.plan_sha256(config, frozen),
            self.runner.plan_sha256(config, changed_fixture),
        )
        with tempfile.TemporaryDirectory(prefix="preflight-source-copy-") as temporary:
            copied = Path(temporary) / "runner.py"
            copied.write_bytes(RUNNER_PATH.read_bytes())
            before = self.runner.pairing._secure_source_file_sha256(copied)
            copied.write_bytes(RUNNER_PATH.read_bytes() + b"\n# safety-change\n")
            after = self.runner.pairing._secure_source_file_sha256(copied)
        self.assertNotEqual(before, after)

        original = self.runner.freeze_contract(config)
        with mock.patch.object(
            self.runner.pairing,
            "secure_batch_scratch",
            side_effect=AssertionError("must not run"),
        ):
            changed = self.runner.freeze_contract(config)
        self.assertNotEqual(original.pairing_runtime_sha256, changed.pairing_runtime_sha256)

    def test_ambiguous_history_is_outside_initial_window_and_conflict_timing_is_frozen(self) -> None:
        ambiguous = self.runner.CASE_BY_ID["current_boundary_stop"]
        history_new = self.runner.CASE_BY_ID["history_search_new"]
        revise = self.runner.CASE_BY_ID["history_search_revise"]
        self.assertEqual(("2026-07-20.md", "2026-08-01.md"), history_new.source_files)
        self.assertEqual("2026-08-01", history_new.expected_evidence[1][0][:-3])
        self.assertEqual("2026-08-01", ambiguous.as_of)
        self.assertEqual("2026-07-18", ambiguous.search_date_to)
        self.assertEqual("no_change", ambiguous.expected_status)
        self.assertEqual("current_boundary_stop_v1", ambiguous.fixture_set)
        self.assertIn("2026-07-19.md", ambiguous.source_files)
        self.assertEqual(
            ["read_memory", "finish"],
            self.runner._expected_trajectory(ambiguous, "A1"),
        )
        self.assertEqual("2026-07-31", revise.as_of)
        self.assertEqual("2026-07-17", revise.search_date_from)
        self.assertEqual("2026-07-17", revise.search_date_to)
        self.assertEqual(
            ["read_memory", "search_history", "finalize_patch"],
            self.runner._expected_trajectory(revise, "A1"),
        )
        self.assertLess(
            max(file for file, _ in revise.expected_counterevidence),
            min(file for file, _ in revise.expected_evidence),
        )
        plan = self.runner.build_plan(self.config())
        self.assertEqual(
            "after_terminal_completion_before_commit",
            plan["frozen"]["conflict_injection"],
        )

    def test_search_is_materially_necessary_and_returns_non_seed_information(self) -> None:
        config = self.config()
        history = self.runner.CASE_BY_ID["history_search_new"]
        ambiguous = self.runner.CASE_BY_ID["current_boundary_stop"]
        revise = self.runner.CASE_BY_ID["history_search_revise"]
        with self.runner.pairing.secure_batch_scratch() as scratch:
            with self.runner.isolated_case_vault(scratch, history) as vault:
                _, preparation, _ = self.runner._prepare(vault, history, "W1", config)
                self.assertEqual(
                    ["2026-08-01.md"],
                    [path.name for path in preparation.recent_paths],
                )
                recent_support = self.runner._expected_evidence(
                    vault, (("2026-08-01.md", self.runner.METRIC_STATEMENT),)
                )
                self.assertEqual(1, len({item["file"] for item in recent_support}))
                matches = self.runner.agent_v1._literal_history_search(
                    preparation,
                    {
                        "query": history.search_query,
                        "date_from": None,
                        "date_to": history.search_date_to,
                        "limit": 5,
                    },
                )
                self.assertEqual("2026-07-20.md", matches[0]["file"])
                self.assertNotIn("2026-07-20.md", [path.name for path in preparation.recent_paths])

            with self.runner.isolated_case_vault(scratch, ambiguous) as vault:
                _, preparation, _ = self.runner._prepare(
                    vault, ambiguous, "W1", config
                )
                self.assertEqual(
                    ["2026-07-19.md"],
                    [path.name for path in preparation.recent_paths],
                )
                memory_id = self.runner._seed_memory(vault, "activation")["memory_id"]
                read = self.runner.agent_v1._read_memory_tool(preparation, memory_id)
                seed_quotes = {
                    item["quote"] for item in read["memory"]["evidence"]
                }
                matches = self.runner.agent_v1._literal_history_search(
                    preparation,
                    {
                        "query": ambiguous.search_query,
                        "date_from": None,
                        "date_to": ambiguous.search_date_to,
                        "limit": 5,
                    },
                )
                self.assertEqual("2026-07-17.md", matches[0]["file"])
                self.assertEqual(1, len(matches))
                self.assertEqual(
                    "团队重新讨论了激活优先级，但没有形成新决定，指标口径也未确认。",
                    matches[0]["quote"],
                )
                self.assertTrue(all(item["quote"] not in seed_quotes for item in matches))
                fixture_text = "\n".join(
                    (vault / filename).read_text(encoding="utf-8")
                    for filename in ambiguous.source_files
                )
                for permission_oracle in ("用户确认", "最终由用户", "系统不得"):
                    self.assertNotIn(permission_oracle, fixture_text)

            with self.runner.isolated_case_vault(scratch, revise) as vault:
                _, preparation, _ = self.runner._prepare(vault, revise, "W1", config)
                self.assertEqual(
                    ["2026-07-26.md"],
                    [path.name for path in preparation.recent_paths],
                )
                memory_id = self.runner._seed_memory(vault, "activation")["memory_id"]
                read = self.runner.agent_v1._read_memory_tool(preparation, memory_id)
                self.assertEqual(
                    [("2026-07-14.md", 40)],
                    [
                        (item["file"], item["line"])
                        for item in read["memory"]["evidence"]
                    ],
                )
                patch = self.runner.expected_action(revise, vault)["arguments"]
                with self.assertRaises(self.runner.core.ContractError) as raised:
                    self.runner.agent_v1._validate_patch_semantics(preparation, patch)
                self.assertEqual("evidence", raised.exception.kind)

                matches = self.runner.agent_v1._literal_history_search(
                    preparation,
                    {
                        "query": revise.search_query,
                        "date_from": revise.search_date_from,
                        "date_to": revise.search_date_to,
                        "limit": 5,
                    },
                )
                self.assertTrue(matches)
                self.assertNotRegex(revise.search_query, r"\s")
                self.assertEqual({"2026-07-17.md"}, {item["file"] for item in matches})
                self.assertTrue({40, 44}.issubset({item["line"] for item in matches}))
                revised_memory_id, target = (
                    self.runner.agent_v1._validate_patch_semantics(
                        preparation, patch
                    )
                )
                self.assertEqual(memory_id, revised_memory_id)
                self.assertEqual(1, target["revision"])
                self.assertLess(
                    max(item["file"] for item in patch["counterevidence"]),
                    min(item["file"] for item in patch["evidence"]),
                )

    def test_w0_materializes_only_w1_increment_without_replaying_records(self) -> None:
        config = self.config()
        with self.runner.pairing.secure_batch_scratch() as scratch:
            for spec in self.runner.CASES:
                with self.subTest(case=spec.case_id):
                    with self.runner.isolated_case_vault(scratch, spec) as vault:
                        payloads = {}
                        deltas = {}
                        for arm in ("W0", "W1"):
                            _, preparation, messages = self.runner._prepare(
                                vault, spec, arm, config
                            )
                            base_length = len(messages)
                            if arm == "W0":
                                self.runner._append_preloaded_context(
                                    messages, preparation, spec
                                )
                            else:
                                self.runner._run_w1_tools(
                                    messages, preparation, spec
                                )
                            payloads[arm] = "\n".join(
                                item["content"] for item in messages
                            )
                            deltas[arm] = "\n".join(
                                item["content"] for item in messages[base_length:]
                            )

                        if spec.w1_tools:
                            self.assertIn(
                                "<materialized_w1_steps>", deltas["W0"]
                            )
                        else:
                            self.assertEqual(deltas["W1"], deltas["W0"])
                        self.assertNotIn('"records":', deltas["W0"])
                        self.assertNotIn('"active_memory":', deltas["W0"])
                        for filename in spec.source_files:
                            header = f"# {filename[:-3]}"
                            self.assertEqual(0, deltas["W0"].count(header))
                            self.assertEqual(0, deltas["W1"].count(header))
                            lines = (vault / filename).read_text(
                                encoding="utf-8"
                            ).splitlines()
                            for line in {line for line in lines if line}:
                                self.assertEqual(
                                    payloads["W1"].count(line),
                                    payloads["W0"].count(line),
                                    f"{spec.case_id}:{filename}:{line}",
                                )

                        for tool in ("read_memory", "search_history"):
                            expected = spec.w1_tools.count(tool)
                            marker = f'"action":"{tool}"'
                            self.assertEqual(expected, deltas["W0"].count(marker))
                            self.assertEqual(expected, deltas["W1"].count(marker))
                        expected_read = spec.w1_tools.count("read_memory")
                        expected_search = spec.w1_tools.count("search_history")
                        self.assertEqual(
                            expected_read,
                            deltas["W0"].count('"required_patch_binding"'),
                        )
                        self.assertEqual(
                            expected_read,
                            deltas["W1"].count('"required_patch_binding"'),
                        )
                        self.assertEqual(
                            expected_search,
                            deltas["W0"].count('"matches":['),
                        )
                        self.assertEqual(
                            expected_search,
                            deltas["W1"].count('"matches":['),
                        )

        ambiguous = self.runner.CASE_BY_ID["current_boundary_stop"]
        recent_quote = (
            "需要核对周中关于激活优先级的讨论；"
            "当前信息尚不能判断优先级是否发生变化。"
        )
        seed_header = "# 2026-07-14"
        with self.runner.pairing.secure_batch_scratch() as scratch:
            with self.runner.isolated_case_vault(scratch, ambiguous) as vault:
                for arm in ("W0", "W1"):
                    _, preparation, messages = self.runner._prepare(
                        vault, ambiguous, arm, config
                    )
                    if arm == "W0":
                        self.runner._append_preloaded_context(
                            messages, preparation, ambiguous
                        )
                    else:
                        self.runner._run_w1_tools(
                            messages, preparation, ambiguous
                        )
                    payload = "\n".join(item["content"] for item in messages)
                    self.assertEqual(1, payload.count(recent_quote), arm)
                    self.assertEqual(0, payload.count(seed_header), arm)

    def test_each_case_clone_is_private_ephemeral_and_fixture_unchanged(self) -> None:
        spec = self.runner.CASE_BY_ID["current_boundary_stop"]
        source_root = self.runner._case_source_root(spec)
        before = self.runner._source_hashes(source_root, spec)
        seen = None
        with self.runner.pairing.secure_batch_scratch() as scratch:
            with self.runner.isolated_case_vault(scratch, spec) as vault:
                seen = vault
                self.assertEqual(0o700, stat.S_IMODE(vault.stat().st_mode))
                for filename in spec.source_files:
                    self.assertEqual(
                        0o600, stat.S_IMODE((vault / filename).stat().st_mode)
                    )
                (vault / spec.source_files[0]).write_text(
                    "isolated mutation", encoding="utf-8"
                )
            self.assertFalse(seen.exists())
        self.assertEqual(
            before, self.runner._source_hashes(source_root, spec)
        )

    def test_public_report_recursive_allowlist_and_finite_strings(self) -> None:
        report = self.runner.build_plan(self.config())
        mutations = []
        unknown = json.loads(json.dumps(report))
        unknown["provider_echo"] = "secret"
        mutations.append(unknown)
        nested = json.loads(json.dumps(report))
        nested["summary"]["batch"]["prompt"] = "record text"
        mutations.append(nested)
        body = json.loads(json.dumps(report))
        body["status"] = self.runner.METRIC_STATEMENT
        mutations.append(body)
        free_string = json.loads(json.dumps(report))
        free_string["frozen"]["prompt_version"] = "provider echoed body"
        mutations.append(free_string)
        nan = json.loads(json.dumps(report))
        nan["limits"]["max_batch_cost_usd"] = float("nan")
        mutations.append(nan)
        for mutation in mutations:
            with self.assertRaises(self.runner.PreflightAbort):
                self.runner.validate_public_report(mutation)

    def test_live_public_report_recomputes_summary_quality_and_usage(self) -> None:
        report = self.run_preflight(self.providers())

        def clone():
            return json.loads(json.dumps(report))

        mutations = []

        wrong_cases = clone()
        wrong_cases["summary"]["cases_completed"] = 0
        mutations.append(wrong_cases)

        contradictory_status = clone()
        contradictory_status["stop_code"] = "quality_gate"
        mutations.append(contradictory_status)

        wrong_score = clone()
        wrong_score["runs"][0]["quality"]["score"] = 0
        mutations.append(wrong_score)

        wrong_usage = clone()
        wrong_usage["runs"][0]["usage"]["model_calls"] = 0
        mutations.append(wrong_usage)

        wrong_calls = clone()
        wrong_calls["summary"]["batch"]["calls"] += 1
        wrong_calls["summary"]["batch"]["by_arm"]["W0"]["calls"] += 1
        mutations.append(wrong_calls)

        wrong_tokens = clone()
        wrong_tokens["summary"]["batch"]["tokens"] += 1
        wrong_tokens["summary"]["batch"]["by_arm"]["W0"]["tokens"] += 1
        mutations.append(wrong_tokens)

        wrong_cost = clone()
        wrong_cost["summary"]["batch"]["cost_usd"] = round(
            wrong_cost["summary"]["batch"]["cost_usd"] + 0.0001, 10
        )
        wrong_cost["summary"]["batch"]["by_arm"]["W0"]["cost_usd"] = round(
            wrong_cost["summary"]["batch"]["by_arm"]["W0"]["cost_usd"]
            + 0.0001,
            10,
        )
        mutations.append(wrong_cost)

        wrong_completeness = clone()
        wrong_completeness["summary"]["batch"]["cost_complete"] = False
        mutations.append(wrong_completeness)

        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=index):
                with self.assertRaises(self.runner.PreflightAbort) as raised:
                    self.runner.validate_public_report(mutation)
                self.assertEqual("security", raised.exception.code)

    def test_cli_has_no_vault_or_output_path_and_redacts_rejected_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="preflight-output-sentinel-") as parent:
            vault = Path(parent) / "real-vault-sentinel"
            vault.mkdir(mode=0o700)
            sentinel = vault / "daily.md"
            sentinel.write_text("unchanged", encoding="utf-8")
            before = sentinel.read_bytes()
            with mock.patch("sys.stderr") as stderr:
                code = self.runner.main(["--vault", str(vault)])
            serialized = "".join(str(call) for call in stderr.write.call_args_list)
            after = sentinel.read_bytes()
        self.assertEqual(2, code)
        self.assertEqual(before, after)
        self.assertNotIn(str(vault), serialized)
        help_text = self.runner.build_parser().format_help()
        self.assertNotIn("--vault", help_text)
        self.assertNotIn("--output", help_text)


if __name__ == "__main__":
    unittest.main()
