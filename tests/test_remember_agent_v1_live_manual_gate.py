#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
RUNNER_PATH = (
    REPO / "context-agent" / "eval" / "agent-v1" / "run_live_manual_gate.py"
)


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "remember_agent_v1_live_manual_gate", RUNNER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load manual gate runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeCompletion:
    def __init__(self, action: dict, *, usage: dict | None = None) -> None:
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
        self.request_id = "not-public"
        self.model = "deepseek-v4-pro"


class QueueProvider:
    def __init__(self, actions: list[dict], *, usage: dict | None = None) -> None:
        self.actions = list(actions)
        self.usage = usage
        self.calls = 0

    def complete(self, messages):
        del messages
        self.calls += 1
        if not self.actions:
            raise AssertionError("fake provider action exhausted")
        return FakeCompletion(self.actions.pop(0), usage=self.usage)


class RememberAgentV1LiveManualGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()

    def config(self):
        return self.runner.ManualGateConfig()

    def actions(
        self,
        case_id: str,
        *,
        first_finish: bool = False,
        post_read_finish: bool = False,
    ) -> list[dict]:
        preflight = self.runner.preflight
        spec = preflight.CASE_BY_ID[case_id]
        source_root = preflight._case_source_root(spec)
        memory_id = preflight._seed_memory(source_root, spec.seed_key)["memory_id"]
        actions = ([
            {
                "schema_version": "1.0",
                "action": "finish",
                "reason_code": "no_material_change",
                "arguments": {"reason": "no_change"},
            }
        ] if first_finish else []) + [
            {
                "schema_version": "1.0",
                "action": "read_memory",
                "reason_code": "inspect_existing",
                "arguments": {"memory_id": memory_id},
            }
        ]
        if post_read_finish:
            actions.append(
                {
                    "schema_version": "1.0",
                    "action": "finish",
                    "reason_code": "no_material_change",
                    "arguments": {"reason": "no_change"},
                }
            )
        if "search_history" in spec.a1_trajectory:
            actions.append(
                {
                    "schema_version": "1.0",
                    "action": "search_history",
                    "reason_code": "check_counterevidence",
                    "arguments": {
                        "query": spec.search_query,
                        "date_from": spec.search_date_from,
                        "date_to": spec.search_date_to,
                        "limit": 5,
                    },
                }
            )
        actions.append(preflight.expected_action(spec, source_root))
        return actions

    def test_default_is_plan_only_zero_call_without_key_or_vault(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            vault = home / "AISecretary"
            vault.mkdir()
            sentinel = vault / "do-not-touch.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            environment = dict(os.environ)
            environment["HOME"] = str(home)
            environment["DEEPSEEK_API_KEY"] = "must-not-be-read"
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
            self.assertEqual("plan_only", report["mode"])
            self.assertFalse(report["executed"])
            self.assertEqual(0, report["summary"]["batch"]["calls"])
            self.assertEqual(
                ["history_search_revise", "revision_conflict"],
                report["frozen"]["cases"],
            )
            self.assertEqual(5, report["frozen"]["ideal_calls"])
            self.assertEqual(9, report["frozen"]["legal_max_calls"])
            self.assertEqual(
                {
                    "history_search_revise": 5,
                    "revision_conflict": 4,
                },
                report["frozen"]["case_call_reservations"],
            )
            self.assertEqual(5, report["frozen"]["budget"]["max_turns"])
            self.assertEqual(
                20_000,
                report["frozen"]["budget"]["max_total_tokens"],
            )
            self.assertEqual(9, report["limits"]["max_batch_calls"])
            self.assertEqual(100_000, report["limits"]["max_batch_tokens"])
            self.assertEqual("unchanged", sentinel.read_text(encoding="utf-8"))
            self.assertEqual([sentinel], list(vault.iterdir()))

    def test_five_turn_budget_is_frozen_without_changing_preflight_budget(self) -> None:
        config = self.config()
        report = self.runner.build_plan(config)

        self.assertEqual(
            {
                "max_turns": 5,
                "max_tool_calls": 3,
                "max_total_tokens": 20_000,
                "max_prompt_chars": 180_000,
            },
            report["frozen"]["budget"],
        )
        self.assertEqual(100_000, report["limits"]["max_batch_tokens"])
        preflight_budget = self.runner._base_config(config).budget
        self.assertEqual(
            self.runner.agent_v1.AgentBudget().as_dict(),
            preflight_budget.as_dict(),
        )
        self.assertEqual(3, preflight_budget.max_turns)
        self.assertEqual(12_000, preflight_budget.max_total_tokens)

        legacy_manual_config = self.runner.ManualGateConfig(
            budget=self.runner.agent_v1.AgentBudget(
                max_turns=5,
                max_total_tokens=12_000,
            )
        )
        legacy_report = self.runner.build_plan(legacy_manual_config)
        self.assertNotEqual(report["plan_sha256"], legacy_report["plan_sha256"])
        self.assertEqual(
            12_000,
            legacy_report["frozen"]["budget"]["max_total_tokens"],
        )

    def test_v2_public_report_is_rejected_after_v3_contract_change(self) -> None:
        report = self.runner.build_plan(self.config())
        report["schema_version"] = "remember_agent_live_manual_gate.v2"
        with self.assertRaises(self.runner.ManualGateAbort) as caught:
            self.runner.validate_public_report(report)
        self.assertEqual("security", caught.exception.code)

    def test_live_requires_both_exact_confirmations(self) -> None:
        attempts = (
            ["--live"],
            [
                "--live",
                "--confirm-live",
                self.runner.LIVE_CONFIRMATION,
            ],
            [
                "--live",
                "--confirm-cost",
                self.runner.COST_CONFIRMATION,
            ],
        )
        for argv in attempts:
            with self.subTest(argv=argv):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(2, self.runner.main(argv))
                self.assertEqual(
                    "confirmation_required", json.loads(stderr.getvalue())["stop_code"]
                )

    def test_plan_sha_mismatch_precedes_provider_construction(self) -> None:
        calls = []

        def factory(case_id, arm, config):
            calls.append((case_id, arm, config.model))
            raise AssertionError("provider must not be constructed")

        with self.assertRaises(self.runner.ManualGateAbort) as caught:
            self.runner.run_live_manual_gate(
                self.config(),
                expected_plan_sha256="0" * 64,
                provider_factory=factory,
            )
        self.assertEqual("plan_mismatch", caught.exception.code)
        self.assertEqual([], calls)

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(
                2,
                self.runner.main(
                    [
                        "--live",
                        "--confirm-live",
                        self.runner.LIVE_CONFIRMATION,
                        "--confirm-cost",
                        self.runner.COST_CONFIRMATION,
                        "--expect-plan-sha256",
                        "0" * 64,
                    ]
                ),
            )
        self.assertEqual("plan_mismatch", json.loads(stderr.getvalue())["stop_code"])

    def test_case_capacity_reserves_five_then_four_under_nine_call_cap(self) -> None:
        config = self.config()
        meter = self.runner.preflight.PreflightMeter(
            config, self.runner.core.pricing_for_model(config.model)
        )
        history, conflict = self.runner.CASES
        self.runner._ensure_case_capacity(meter, history)
        meter.calls = 5
        self.runner._ensure_case_capacity(meter, conflict)
        meter.calls = 6
        with self.assertRaises(self.runner.preflight.PreflightAbort) as caught:
            self.runner._ensure_case_capacity(meter, conflict)
        self.assertEqual("call_limit", caught.exception.code)

    def test_fake_two_case_gate_checks_revise_and_conflict(self) -> None:
        providers = {
            case_id: QueueProvider(self.actions(case_id))
            for case_id in self.runner.CASE_IDS
        }

        def factory(case_id, arm, config):
            self.assertEqual("A1", arm)
            self.assertEqual("deepseek-v4-pro", config.model)
            return providers[case_id]

        config = self.config()
        frozen = self.runner.freeze_contract(config)
        source_before = {
            spec.case_id: self.runner.preflight._source_hashes(
                self.runner.preflight._case_source_root(spec), spec
            )
            for spec in self.runner.CASES
        }
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            vault = home / "AISecretary"
            vault.mkdir()
            sentinel = vault / "do-not-touch.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            # Do not redirect HOME for a live synthetic run: the reviewed
            # scratch guard intentionally rejects any system temp directory
            # related to a configured/home Vault.  The unaccepted temporary
            # directory below still proves no caller-supplied Vault is used.
            with mock.patch.dict(
                os.environ, {"DEEPSEEK_API_KEY": "must-not-be-read"}
            ):
                report = self.runner.run_live_manual_gate(
                    config,
                    expected_plan_sha256=self.runner.plan_sha256(config, frozen),
                    provider_factory=factory,
                )
            self.assertEqual("unchanged", sentinel.read_text(encoding="utf-8"))
            self.assertEqual([sentinel], list(vault.iterdir()))

        self.assertEqual("completed", report["status"])
        self.assertTrue(report["summary"]["gate_passed"])
        self.assertEqual(5, report["summary"]["batch"]["calls"])
        self.assertEqual(5, report["summary"]["batch"]["a1"]["calls"])
        by_case = {run["case"]: run for run in report["runs"]}
        revise = by_case["history_search_revise"]
        self.assertEqual("updated", revise["status"])
        self.assertEqual(
            ["read_memory", "search_history", "finalize_patch"],
            revise["trajectory"],
        )
        self.assertTrue(revise["quality"]["passed"])
        self.assertTrue(revise["task_passed"])
        self.assertEqual("unassisted", revise["autonomy_classification"])
        self.assertEqual(
            {"pre_read": False, "post_read": False, "count": 0},
            revise["finish_reviews"],
        )
        for check in (
            "operation_expected",
            "revision_expected",
            "evidence_exact",
            "counterevidence_exact",
            "source_hashes_exact",
            "usage_complete",
            "source_clone_unchanged",
        ):
            self.assertTrue(revise["quality"]["checks"][check])

        conflict = by_case["revision_conflict"]
        self.assertEqual("stale", conflict["status"])
        self.assertEqual(["read_memory", "finalize_patch"], conflict["trajectory"])
        self.assertTrue(conflict["quality"]["passed"])
        self.assertTrue(conflict["task_passed"])
        self.assertEqual("unassisted", conflict["autonomy_classification"])
        for check in (
            "cas_preserved",
            "stale_no_agent_write",
            "old_revision_preserved",
            "user_action_wins",
            "usage_complete",
            "source_clone_unchanged",
        ):
            self.assertTrue(conflict["quality"]["checks"][check])

        source_after = {
            spec.case_id: self.runner.preflight._source_hashes(
                self.runner.preflight._case_source_root(spec), spec
            )
            for spec in self.runner.CASES
        }
        self.assertEqual(source_before, source_after)
        self.assertEqual(3, providers["history_search_revise"].calls)
        self.assertEqual(2, providers["revision_conflict"].calls)

    def test_bounded_finish_prefix_is_audited_and_accepted_only_for_exact_paths(self) -> None:
        providers = {
            case_id: QueueProvider(self.actions(case_id, first_finish=True))
            for case_id in self.runner.CASE_IDS
        }

        def factory(case_id, arm, config):
            del arm, config
            return providers[case_id]

        config = self.config()
        frozen = self.runner.freeze_contract(config)
        report = self.runner.run_live_manual_gate(
            config,
            expected_plan_sha256=self.runner.plan_sha256(config, frozen),
            provider_factory=factory,
        )
        self.assertTrue(report["summary"]["gate_passed"])
        self.assertEqual(7, report["summary"]["batch"]["calls"])
        for run in report["runs"]:
            self.assertEqual(
                run["trajectory"], ["finish", *run["expected_trajectory"]]
            )
            self.assertTrue(run["quality"]["checks"]["trajectory_expected"])
            self.assertTrue(run["quality"]["checks"]["audit_clean"])
            self.assertTrue(run["task_passed"])
            self.assertEqual("guarded", run["autonomy_classification"])
            self.assertEqual(
                {"pre_read": True, "post_read": False, "count": 1},
                run["finish_reviews"],
            )

        full_quality = {
            "checks": {
                name: True
                for name in self.runner.REQUIRED_CHECKS[
                    "history_search_revise"
                ]
            }
        }
        full_quality["checks"]["trajectory_expected"] = False
        raw = {
            "bounded_finish_refusal_count": 0,
            "pre_read_finish_refusal": False,
            "post_read_finish_refusal": False,
            "trajectory": [
                "finish",
                *self.runner.preflight._expected_trajectory(
                    self.runner.CASES[0], self.runner.ARM
                ),
            ],
        }
        selected = self.runner._selected_quality(
            self.runner.CASES[0], full_quality, raw
        )
        self.assertFalse(selected["checks"]["trajectory_expected"])

    def test_dual_finish_reviews_are_reported_as_scaffolded_not_release_pass(self) -> None:
        providers = {
            case_id: QueueProvider(
                self.actions(
                    case_id,
                    first_finish=True,
                    post_read_finish=True,
                )
            )
            for case_id in self.runner.CASE_IDS
        }

        def factory(case_id, arm, config):
            del arm, config
            return providers[case_id]

        config = self.config()
        frozen = self.runner.freeze_contract(config)
        report = self.runner.run_live_manual_gate(
            config,
            expected_plan_sha256=self.runner.plan_sha256(config, frozen),
            provider_factory=factory,
        )
        self.assertEqual("stopped", report["status"])
        self.assertFalse(report["summary"]["gate_passed"])
        self.assertEqual("quality_gate", report["stop_code"])
        self.assertEqual(1, len(report["runs"]))
        self.assertEqual(5, report["summary"]["batch"]["calls"])
        run = report["runs"][0]
        self.assertEqual("scaffolded", run["autonomy_classification"])
        self.assertEqual(
            {"pre_read": True, "post_read": True, "count": 2},
            run["finish_reviews"],
        )
        self.assertTrue(run["task_passed"])
        self.assertFalse(run["quality"]["passed"])
        self.assertFalse(run["quality"]["checks"]["trajectory_expected"])

    def test_agent_token_budget_projects_budget_and_stops_before_second_case(self) -> None:
        over_budget_usage = {
            "prompt_tokens": 20_000,
            "completion_tokens": 1,
            "total_tokens": 20_001,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 20_000,
        }
        providers = {
            "history_search_revise": QueueProvider(
                self.actions("history_search_revise"),
                usage=over_budget_usage,
            ),
            "revision_conflict": QueueProvider(self.actions("revision_conflict")),
        }

        def factory(case_id, arm, config):
            del arm, config
            return providers[case_id]

        config = self.config()
        frozen = self.runner.freeze_contract(config)
        report = self.runner.run_live_manual_gate(
            config,
            expected_plan_sha256=self.runner.plan_sha256(config, frozen),
            provider_factory=factory,
        )

        self.assertEqual("stopped", report["status"])
        self.assertEqual("budget", report["stop_code"])
        self.assertFalse(report["summary"]["gate_passed"])
        self.assertEqual(1, report["summary"]["batch"]["calls"])
        self.assertEqual(20_001, report["summary"]["batch"]["tokens"])
        self.assertTrue(report["summary"]["batch"]["cost_complete"])
        self.assertEqual(1, len(report["runs"]))
        self.assertEqual("budget_exhausted", report["runs"][0]["status"])
        self.assertEqual("budget", report["runs"][0]["error_code"])
        self.assertEqual(1, report["runs"][0]["usage"]["model_calls"])
        self.assertEqual(1, providers["history_search_revise"].calls)
        self.assertEqual(0, providers["revision_conflict"].calls)

    def test_public_report_recomputes_review_classification_and_task_passed(self) -> None:
        providers = {
            case_id: QueueProvider(self.actions(case_id))
            for case_id in self.runner.CASE_IDS
        }

        def factory(case_id, arm, config):
            del arm, config
            return providers[case_id]

        config = self.config()
        frozen = self.runner.freeze_contract(config)
        report = self.runner.run_live_manual_gate(
            config,
            expected_plan_sha256=self.runner.plan_sha256(config, frozen),
            provider_factory=factory,
        )

        forged_classification = json.loads(json.dumps(report))
        forged_classification["runs"][0]["autonomy_classification"] = "guarded"

        forged_task = json.loads(json.dumps(report))
        forged_task["runs"][0]["task_passed"] = False

        forged_reviews = json.loads(json.dumps(report))
        forged_reviews["runs"][0]["finish_reviews"] = {
            "pre_read": True,
            "post_read": False,
            "count": 1,
        }
        forged_reviews["runs"][0]["autonomy_classification"] = "guarded"

        for forged in (forged_classification, forged_task, forged_reviews):
            with self.subTest(forged=forged["runs"][0]):
                with self.assertRaises(self.runner.ManualGateAbort) as caught:
                    self.runner.validate_public_report(forged)
                self.assertEqual("security", caught.exception.code)

    def test_late_report_failure_keeps_executed_meter(self) -> None:
        providers = {
            case_id: QueueProvider(self.actions(case_id))
            for case_id in self.runner.CASE_IDS
        }

        def factory(case_id, arm, config):
            del arm, config
            return providers[case_id]

        config = self.config()
        frozen = self.runner.freeze_contract(config)
        with mock.patch.object(
            self.runner,
            "validate_public_report",
            side_effect=self.runner.ManualGateAbort("security"),
        ):
            with self.assertRaises(self.runner.ManualGateAbort) as caught:
                self.runner.run_live_manual_gate(
                    config,
                    expected_plan_sha256=self.runner.plan_sha256(config, frozen),
                    provider_factory=factory,
                )
        self.assertEqual("security", caught.exception.code)
        self.assertTrue(caught.exception.executed)
        self.assertEqual(5, caught.exception.batch["calls"])
        self.assertEqual(5, caught.exception.batch["a1"]["calls"])

    def test_vault_and_output_options_are_rejected_without_echo(self) -> None:
        marker = "/private/secret-vault"
        result = subprocess.run(
            [sys.executable, str(RUNNER_PATH), "--vault", marker],
            cwd=REPO,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, result.returncode)
        self.assertNotIn(marker, result.stderr)
        self.assertEqual("contract", json.loads(result.stderr)["stop_code"])


if __name__ == "__main__":
    unittest.main()
