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
    REPO / "context-agent" / "eval" / "agent-v1" / "run_live_thinking_probe.py"
)


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "remember_agent_v1_live_thinking_probe", RUNNER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load thinking probe runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeCompletion:
    def __init__(self, action: dict, *, reasoning_tokens: int = 0) -> None:
        self.content = json.dumps(action, ensure_ascii=False)
        self.usage = {
            "prompt_tokens": 120,
            "completion_tokens": 60,
            "total_tokens": 180,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 120,
            "completion_tokens_details": {
                "reasoning_tokens": reasoning_tokens,
            },
        }
        self.request_id = "not-public"
        self.model = "deepseek-v4-pro"


class QueueProvider:
    def __init__(self, actions: list[dict], *, reasoning_tokens: int = 0) -> None:
        self.actions = list(actions)
        self.reasoning_tokens = reasoning_tokens
        self.calls = 0

    def complete(self, messages):
        del messages
        self.calls += 1
        if not self.actions:
            raise AssertionError("fake provider action exhausted")
        return FakeCompletion(
            self.actions.pop(0), reasoning_tokens=self.reasoning_tokens
        )


class RememberAgentV1LiveThinkingProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()

    def config(self):
        return self.runner.ThinkingProbeConfig()

    def memory_id(self) -> str:
        spec = self.runner.SPEC
        root = self.runner.preflight._case_source_root(spec)
        return self.runner.preflight._seed_memory(root, spec.seed_key)["memory_id"]

    def finish(self) -> dict:
        return {
            "schema_version": "1.0",
            "action": "finish",
            "reason_code": "no_material_change",
            "arguments": {"reason": "no_change"},
        }

    def ideal_actions(self) -> list[dict]:
        spec = self.runner.SPEC
        source_root = self.runner.preflight._case_source_root(spec)
        return [
            {
                "schema_version": "1.0",
                "action": "read_memory",
                "reason_code": "inspect_existing",
                "arguments": {"memory_id": self.memory_id()},
            },
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
            },
            self.runner.preflight.expected_action(spec, source_root),
        ]

    def plan(self):
        config = self.config()
        frozen = self.runner.freeze_contract(config)
        return config, self.runner.plan_sha256(config, frozen)

    def test_default_is_reproducible_zero_call_plan_without_key_or_vault(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            vault = home / "AISecretary"
            vault.mkdir()
            sentinel = vault / "do-not-touch.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            environment = dict(os.environ)
            environment["HOME"] = str(home)
            environment["DEEPSEEK_API_KEY"] = "must-not-be-read"
            commands = []
            for _ in range(2):
                result = subprocess.run(
                    [sys.executable, str(RUNNER_PATH)],
                    cwd=REPO,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                commands.append(result.stdout)
            self.assertEqual(commands[0], commands[1])
            report = json.loads(commands[0])
            self.assertEqual("plan_only", report["mode"])
            self.assertFalse(report["executed"])
            self.assertEqual(0, report["summary"]["batch"]["calls"])
            self.assertEqual(
                ["disabled", "thinking_high"], report["frozen"]["execution_order"]
            )
            self.assertEqual(
                {
                    "disabled": {
                        "thinking": "disabled",
                        "reasoning_effort": None,
                    },
                    "thinking_high": {
                        "thinking": "enabled",
                        "reasoning_effort": "high",
                    },
                },
                report["frozen"]["arm_configs"],
            )
            self.assertEqual(8, report["limits"]["max_batch_calls"])
            self.assertEqual(30_000, report["limits"]["max_batch_tokens"])
            self.assertEqual(0.03, report["limits"]["max_batch_cost_usd"])
            self.assertEqual("unchanged", sentinel.read_text(encoding="utf-8"))
            self.assertEqual([sentinel], list(vault.iterdir()))

    def test_live_requires_both_exact_confirmations(self) -> None:
        attempts = (
            ["--live"],
            ["--live", "--confirm-live", self.runner.LIVE_CONFIRMATION],
            ["--live", "--confirm-cost", self.runner.COST_CONFIRMATION],
        )
        for argv in attempts:
            with self.subTest(argv=argv):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(2, self.runner.main(argv))
                self.assertEqual(
                    "confirmation_required", json.loads(stderr.getvalue())["stop_code"]
                )

    def test_plan_mismatch_precedes_provider_construction(self) -> None:
        calls = []

        def factory(arm, config):
            calls.append((arm, config.model))
            raise AssertionError("provider must not be constructed")

        with self.assertRaises(self.runner.ThinkingProbeAbort) as caught:
            self.runner.run_live_thinking_probe(
                self.config(),
                expected_plan_sha256="0" * 64,
                provider_factory=factory,
            )
        self.assertEqual("plan_mismatch", caught.exception.code)
        self.assertEqual([], calls)

    def test_default_provider_factory_switches_only_thinking_treatment(self) -> None:
        config = self.config()
        with mock.patch.object(
            self.runner.preflight.deepseek_provider,
            "read_deepseek_api_key",
            side_effect=AssertionError("construction must not read credentials"),
        ):
            disabled = self.runner.default_provider_factory("disabled", config)
            thinking = self.runner.default_provider_factory("thinking_high", config)
        self.assertEqual("disabled", disabled.thinking)
        self.assertIsNone(disabled.reasoning_effort)
        self.assertEqual("enabled", thinking.thinking)
        self.assertEqual("high", thinking.reasoning_effort)
        for provider in (disabled, thinking):
            self.assertEqual("deepseek-v4-pro", provider.model)
            self.assertEqual(60.0, provider.timeout)
            self.assertEqual(2000, provider.max_tokens)

    def test_paired_probe_detects_thinking_only_strict_autonomy(self) -> None:
        providers = {
            "disabled": QueueProvider(
                [
                    self.finish(),
                    self.ideal_actions()[0],
                    self.finish(),
                ]
            ),
            "thinking_high": QueueProvider(
                self.ideal_actions(), reasoning_tokens=24
            ),
        }

        def factory(arm, config):
            self.assertEqual("deepseek-v4-pro", config.model)
            return providers[arm]

        config, plan = self.plan()
        source_before = self.runner.preflight._source_hashes(
            self.runner.preflight._case_source_root(self.runner.SPEC),
            self.runner.SPEC,
        )
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "must-not-be-read"}):
            report = self.runner.run_live_thinking_probe(
                config,
                expected_plan_sha256=plan,
                provider_factory=factory,
            )
        source_after = self.runner.preflight._source_hashes(
            self.runner.preflight._case_source_root(self.runner.SPEC),
            self.runner.SPEC,
        )
        self.assertEqual(source_before, source_after)
        self.assertEqual("completed", report["status"])
        self.assertEqual("none", report["stop_code"])
        self.assertTrue(report["summary"]["paired_complete"])
        self.assertTrue(report["summary"]["thinking_autonomy_observed"])
        self.assertEqual("thinking_only_pass", report["summary"]["contrast"])
        self.assertEqual(6, report["summary"]["batch"]["calls"])
        by_arm = {run["arm"]: run for run in report["runs"]}
        disabled = by_arm["disabled"]
        self.assertEqual(
            ["finish", "read_memory", "finish"], disabled["trajectory"]
        )
        self.assertTrue(disabled["bounded_finish_refusal"])
        self.assertFalse(disabled["quality"]["passed"])
        self.assertFalse(
            disabled["quality"]["checks"]["bounded_finish_absent"]
        )
        thinking = by_arm["thinking_high"]
        self.assertEqual(list(self.runner.EXPECTED_TRAJECTORY), thinking["trajectory"])
        self.assertFalse(thinking["bounded_finish_refusal"])
        self.assertTrue(thinking["quality"]["passed"])
        self.assertEqual(72, thinking["usage"]["reasoning_tokens"])

    def test_each_arm_gets_an_independent_fixture_clone(self) -> None:
        providers = {
            arm: QueueProvider(self.ideal_actions()) for arm in self.runner.ARMS
        }

        def factory(arm, config):
            del config
            return providers[arm]

        config, plan = self.plan()
        report = self.runner.run_live_thinking_probe(
            config,
            expected_plan_sha256=plan,
            provider_factory=factory,
        )
        self.assertEqual("both_pass", report["summary"]["contrast"])
        self.assertTrue(report["summary"]["thinking_autonomy_observed"])
        self.assertTrue(all(run["quality"]["passed"] for run in report["runs"]))
        self.assertEqual(3, providers["disabled"].calls)
        self.assertEqual(3, providers["thinking_high"].calls)

    def test_bounded_finish_prefix_never_counts_as_autonomous_success(self) -> None:
        providers = {
            "disabled": QueueProvider(self.ideal_actions()),
            "thinking_high": QueueProvider([self.finish(), *self.ideal_actions()]),
        }

        def factory(arm, config):
            del config
            return providers[arm]

        config, plan = self.plan()
        report = self.runner.run_live_thinking_probe(
            config,
            expected_plan_sha256=plan,
            provider_factory=factory,
        )
        self.assertEqual("disabled_only_pass", report["summary"]["contrast"])
        self.assertFalse(report["summary"]["thinking_autonomy_observed"])
        thinking = report["runs"][1]
        self.assertEqual(
            ["finish", *self.runner.EXPECTED_TRAJECTORY], thinking["trajectory"]
        )
        self.assertTrue(thinking["bounded_finish_refusal"])
        self.assertFalse(thinking["quality"]["passed"])
        self.assertFalse(thinking["quality"]["checks"]["trajectory_expected"])
        self.assertFalse(
            thinking["quality"]["checks"]["bounded_finish_absent"]
        )

    def test_report_allowlist_rejects_extra_or_forged_fields(self) -> None:
        report = self.runner.build_plan(self.config())
        report["secret"] = "not allowed"
        with self.assertRaises(self.runner.ThinkingProbeAbort) as caught:
            self.runner.validate_public_report(report)
        self.assertEqual("security", caught.exception.code)

        report = self.runner.build_plan(self.config())
        report["frozen"]["thinking"] = "enabled"
        with self.assertRaises(self.runner.ThinkingProbeAbort) as caught:
            self.runner.validate_public_report(report)
        self.assertEqual("security", caught.exception.code)

        report = self.runner.build_plan(self.config())
        report["plan_sha256"] = "0" * 64
        with self.assertRaises(self.runner.ThinkingProbeAbort) as caught:
            self.runner.validate_public_report(report)
        self.assertEqual("security", caught.exception.code)

    def test_vault_and_output_options_are_rejected_without_echo(self) -> None:
        for option in ("--vault", "--output"):
            marker = "/private/secret-target"
            result = subprocess.run(
                [sys.executable, str(RUNNER_PATH), option, marker],
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
