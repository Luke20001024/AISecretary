#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import contextlib
import dataclasses
import io
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
RUNNER_PATH = REPO / "context-agent" / "eval" / "agent-v1" / "run_live_e2.py"


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "remember_agent_v1_live_e2", RUNNER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load live E2 runner")
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
    def __init__(self, action: dict, model: str) -> None:
        self.content = json.dumps(action, ensure_ascii=False)
        self.model = model
        self.request_id = "synthetic-provider-id"
        self.usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 100,
        }


class QueueProvider:
    def __init__(self, actions: list[dict], model: str) -> None:
        self.actions = list(actions)
        self.model = model
        self.calls = 0

    def complete(self, messages):
        del messages
        self.calls += 1
        if not self.actions:
            raise AssertionError("fake action queue exhausted")
        return FakeCompletion(self.actions.pop(0), self.model)


class RememberAgentV1LiveE2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()

    def config(self, **changes):
        values = {
            "model": "deepseek-v4-pro",
            "max_batch_calls": 100,
            "max_batch_tokens": 1_200_000,
            "max_batch_cost_usd": 1.0,
        }
        values.update(changes)
        return self.runner.E2Config(**values)

    def fake_factory(self, arm, date, vault, config):
        return QueueProvider(
            self.runner.oracle_fake_actions(vault, arm, date), config.model
        )

    def test_default_plan_is_frozen_twenty_day_zero_call_contract(self) -> None:
        config = self.runner.E2Config()
        plan = self.runner.build_plan(config)
        self.runner.validate_public_report(plan)
        self.assertEqual("plan_only", plan["mode"])
        self.assertFalse(plan["executed"])
        self.assertEqual(20, plan["frozen"]["daily_files"])
        self.assertEqual(
            sorted(self.runner.offline.DAILY_TARGETS),
            plan["frozen"]["date_order"],
        )
        self.assertEqual(
            {"new": 4, "reinforce": 3, "no_change": 13},
            plan["frozen"]["daily_operation_counts"],
        )
        self.assertEqual(100, plan["limits"]["max_batch_calls"])
        self.assertEqual(1_200_000, plan["limits"]["max_batch_tokens"])
        self.assertEqual(1.0, plan["limits"]["max_batch_cost_usd"])
        self.assertEqual(0, plan["summary"]["batch"]["calls"])
        self.assertEqual([], plan["days"])
        self.assertFalse(
            plan["summary"]["operation_coverage"][
                "complete_for_agent_patch_space"
            ]
        )
        self.assertEqual(
            ["revise", "tension"],
            plan["summary"]["operation_coverage"]["not_covered"],
        )
        self.assertFalse(plan["frozen"]["agent_gain_claimed"])
        self.assertEqual(
            self.runner.agent_v1.make_agent_policy_sha256(
                provider="deepseek",
                model=config.model,
                budget=config.budget,
            ),
            plan["frozen"]["policy_sha256"],
        )
        self.assertRegex(plan["plan_sha256"], r"^[0-9a-f]{64}$")

    def test_cli_plan_only_never_constructs_provider_or_echoes_secret_path(self) -> None:
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
        self.assertEqual("planned", report["status"])
        self.assertEqual(0, report["summary"]["batch"]["calls"])
        self.assertNotIn("token-secret-sentinel", result.stdout)
        self.assertNotIn("/Users/", result.stdout)
        self.assertNotIn("MEMENTO_SYNTHETIC_CONTEXT_TEST", result.stdout)

    def test_live_requires_confirmation_and_plan_sha_before_provider(self) -> None:
        called = False

        def forbidden(*args):
            nonlocal called
            called = True
            raise AssertionError("provider must not be constructed")

        with mock.patch.object(self.runner, "default_provider_factory", forbidden):
            code = self.runner.main(["--live"])
        self.assertEqual(2, code)
        self.assertFalse(called)

        config = self.config()
        with self.assertRaises(self.runner.pairing.PairingAbort) as raised:
            self.runner.run_live_e2(
                config,
                expected_plan_sha256="0" * 64,
                provider_factory=forbidden,
            )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(called)

    def test_default_provider_is_resolved_only_after_frozen_plan_check(self) -> None:
        config = self.config()
        live = self.runner.run_live_e2
        self.assertIsNone(live.__kwdefaults__["provider_factory"])
        constructed = False

        def replacement_factory(*args):
            nonlocal constructed
            constructed = True
            raise AssertionError("replacement provider must not be constructed")

        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        original_kwdefaults = dict(live.__kwdefaults__)
        try:
            live.__kwdefaults__ = {
                **original_kwdefaults,
                "provider_factory": replacement_factory,
            }
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(
                original.runner_runtime_sha256, changed.runner_runtime_sha256
            )
            with self.assertRaises(self.runner.pairing.PairingAbort) as raised:
                live(config, expected_plan_sha256=expected)
        finally:
            live.__kwdefaults__ = original_kwdefaults
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(constructed)

        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        with mock.patch.object(
            self.runner, "default_provider_factory", replacement_factory
        ):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(
                original.runner_runtime_sha256, changed.runner_runtime_sha256
            )
            with self.assertRaises(self.runner.pairing.PairingAbort) as raised:
                live(config, expected_plan_sha256=expected)
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(constructed)

    def test_plan_binds_source_runtime_and_reused_pairing_safety(self) -> None:
        config = self.config()
        frozen = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, frozen)
        self.assertEqual(
            self.runner.pairing._runner_source_sha256(RUNNER_PATH),
            frozen.runner_source_sha256,
        )
        manifest = self.runner._dependency_contract()
        self.assertIn("pairing_runner_sha256", manifest)
        self.assertIn("offline_runner_sha256", manifest)
        self.assertIn("MeteredProvider.complete", manifest["reused_pairing_symbols"])

        def bypass(self, arm, messages):
            del self, arm, messages

        with mock.patch.object(
            self.runner.pairing.BatchMeter, "before_call", bypass
        ):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(
                frozen.dependency_manifest_sha256,
                changed.dependency_manifest_sha256,
            )
            self.assertNotEqual(expected, self.runner.plan_sha256(config, changed))

        original_equal = self.runner.FrozenContract.__eq__

        def wrapped_equal(left, right):
            return original_equal(left, right)

        constructed = False

        def forbidden(*args):
            nonlocal constructed
            constructed = True
            raise AssertionError("provider must not be constructed")

        with mock.patch.object(
            self.runner.FrozenContract, "__eq__", wrapped_equal
        ):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(
                frozen.runner_runtime_sha256, changed.runner_runtime_sha256
            )
            with self.assertRaises(self.runner.pairing.PairingAbort) as raised:
                self.runner.run_live_e2(
                    config,
                    expected_plan_sha256=expected,
                    provider_factory=forbidden,
                )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(constructed)

    def test_runner_namespace_constants_reject_old_plan_before_provider(self) -> None:
        config = self.config()
        constructed = False

        def forbidden(*args):
            nonlocal constructed
            constructed = True
            raise AssertionError("provider must not be constructed")

        mutations = (
            (
                "W0_TERMINAL_INSTRUCTION",
                self.runner.W0_TERMINAL_INSTRUCTION + "<drift />",
            ),
            (
                "FATAL_STOP_CODES",
                self.runner.FATAL_STOP_CODES | {"runner_drift_test"},
            ),
            ("ARMS", tuple(reversed(self.runner.ARMS))),
            (
                "NEW_EVIDENCE",
                {
                    **self.runner.NEW_EVIDENCE,
                    "2099-01-01": ("metric_first", ()),
                },
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
                    with self.assertRaises(self.runner.pairing.PairingAbort) as raised:
                        self.runner.run_live_e2(
                            config,
                            expected_plan_sha256=expected,
                            provider_factory=forbidden,
                        )
                self.assertEqual("plan_mismatch", raised.exception.code)
                self.assertFalse(constructed)

        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        original_validate = self.runner.E2Config.validate

        def wrapped_validate(instance):
            return original_validate(instance)

        with mock.patch.object(self.runner.E2Config, "validate", wrapped_validate):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(
                original.runner_runtime_sha256, changed.runner_runtime_sha256
            )
            with self.assertRaises(self.runner.pairing.PairingAbort) as raised:
                self.runner.run_live_e2(
                    config,
                    expected_plan_sha256=expected,
                    provider_factory=forbidden,
                )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(constructed)

    def test_project_module_aliases_and_pairing_namespace_are_frozen(self) -> None:
        config = self.config()
        frozen = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, frozen)
        manifest = self.runner._dependency_contract()
        self.assertIn("pairing_runtime_namespace_sha256", manifest)
        self.assertIn("PairingAbort", manifest["pairing_runtime_surface"][
            "module_owned_functions_and_classes"
        ])
        self.assertIn(
            "SUPPORTED_MODELS",
            manifest["pairing_runtime_surface"]["uppercase_constants"],
        )
        self.assertEqual(
            {"pairing", "offline", "agent_v1", "core", "deepseek_provider"},
            {
                item["alias"]
                for item in manifest["runner_project_module_aliases"]["aliases"]
            },
        )
        constructed = False

        def forbidden(*args):
            nonlocal constructed
            constructed = True
            raise AssertionError("module drift must precede provider")

        original_abort = self.runner.pairing.PairingAbort
        for alias in (
            "pairing",
            "offline",
            "agent_v1",
            "core",
            "deepseek_provider",
        ):
            with self.subTest(alias=alias):
                proxy = ForwardingModuleProxy(getattr(self.runner, alias))
                with mock.patch.object(self.runner, alias, proxy):
                    with self.assertRaises(original_abort) as raised:
                        self.runner.freeze_contract(config)
                    self.assertEqual("security", raised.exception.code)
                    with self.assertRaises(original_abort) as raised:
                        self.runner.run_live_e2(
                            config,
                            expected_plan_sha256=expected,
                            provider_factory=forbidden,
                        )
                    self.assertEqual("security", raised.exception.code)
        self.assertFalse(constructed)

        offline_agent_proxy = ForwardingModuleProxy(self.runner.agent_v1)
        with mock.patch.object(
            self.runner.offline, "agent_v1", offline_agent_proxy
        ):
            with self.assertRaises(original_abort) as raised:
                self.runner.freeze_contract(config)
        self.assertEqual("security", raised.exception.code)

        class ReplacementPairingAbort(original_abort):
            pass

        for symbol, replacement in (
            ("PairingAbort", ReplacementPairingAbort),
            (
                "SUPPORTED_MODELS",
                tuple(reversed(self.runner.pairing.SUPPORTED_MODELS)),
            ),
        ):
            with self.subTest(pairing_symbol=symbol):
                with mock.patch.object(self.runner.pairing, symbol, replacement):
                    changed = self.runner.freeze_contract(config)
                    self.assertNotEqual(
                        frozen.dependency_manifest_sha256,
                        changed.dependency_manifest_sha256,
                    )
                    with self.assertRaises(original_abort) as raised:
                        self.runner.run_live_e2(
                            config,
                            expected_plan_sha256=expected,
                            provider_factory=forbidden,
                        )
                self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(constructed)

    def test_offline_namespace_and_chronological_order_are_frozen(self) -> None:
        config = self.config()
        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        constructed = False

        def forbidden(*args):
            nonlocal constructed
            constructed = True
            raise AssertionError("provider must not be constructed")

        topics = dict(self.runner.offline.TOPICS)
        topic = topics["metric_first"]
        topics["metric_first"] = dataclasses.replace(
            topic, statement=topic.statement + " drift"
        )
        with mock.patch.object(self.runner.offline, "TOPICS", topics):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(
                original.dependency_manifest_sha256,
                changed.dependency_manifest_sha256,
            )
            with self.assertRaises(self.runner.pairing.PairingAbort) as raised:
                self.runner.run_live_e2(
                    config,
                    expected_plan_sha256=expected,
                    provider_factory=forbidden,
                )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(constructed)

        reversed_targets = dict(
            reversed(tuple(self.runner.offline.DAILY_TARGETS.items()))
        )
        seen_dates: list[str] = []

        def stopping_factory(arm, date, vault, config):
            del arm, vault, config
            seen_dates.append(date)
            raise RuntimeError("offline-order-probe")

        with mock.patch.object(
            self.runner.offline, "DAILY_TARGETS", reversed_targets
        ):
            changed = self.runner.freeze_contract(config)
            changed_plan = self.runner.plan_sha256(config, changed)
            self.assertEqual(expected, changed_plan)
            self.assertEqual(
                sorted(reversed_targets),
                self.runner._frozen_public(config, changed)["date_order"],
            )
            report = self.runner.run_live_e2(
                config,
                expected_plan_sha256=changed_plan,
                provider_factory=stopping_factory,
            )
        self.assertEqual([sorted(reversed_targets)[0]], seen_dates)
        self.assertEqual("runtime", report["stop_code"])

    def test_timeout_is_frozen_and_old_sha_rejects_before_provider(self) -> None:
        original = self.config(timeout=60.0)
        changed = self.config(timeout=61.0)
        original_frozen = self.runner.freeze_contract(original)
        changed_frozen = self.runner.freeze_contract(changed)
        original_sha = self.runner.plan_sha256(original, original_frozen)
        changed_sha = self.runner.plan_sha256(changed, changed_frozen)
        self.assertEqual(60.0, self.runner._frozen_public(original, original_frozen)["timeout_seconds"])
        self.assertEqual(61.0, self.runner._frozen_public(changed, changed_frozen)["timeout_seconds"])
        self.assertNotEqual(original_sha, changed_sha)
        self.assertEqual(61.0, self.runner.default_provider_factory("W0", "2026-07-14", REPO, changed).timeout)

        constructed = False

        def forbidden(*args):
            nonlocal constructed
            constructed = True
            raise AssertionError("provider must not be constructed")

        with self.assertRaises(self.runner.pairing.PairingAbort) as raised:
            self.runner.run_live_e2(
                changed,
                expected_plan_sha256=original_sha,
                provider_factory=forbidden,
            )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(constructed)

    def test_agent_policy_change_invalidates_old_plan_before_provider(self) -> None:
        config = self.config()
        original_frozen = self.runner.freeze_contract(config)
        original_sha = self.runner.plan_sha256(config, original_frozen)
        original_rules = self.runner.agent_v1.STABLE_NEW_SCOPE_RULES
        changed_rules = original_rules + (
            ("policy-binding-regression-only", ("policy-binding-regression-only",)),
        )
        constructed = False

        def forbidden(*args):
            nonlocal constructed
            constructed = True
            raise AssertionError("provider must not be constructed")

        with mock.patch.object(
            self.runner.agent_v1, "STABLE_NEW_SCOPE_RULES", changed_rules
        ):
            changed_frozen = self.runner.freeze_contract(config)
            changed_sha = self.runner.plan_sha256(config, changed_frozen)
            self.assertNotEqual(
                original_frozen.policy_sha256, changed_frozen.policy_sha256
            )
            self.assertNotEqual(original_sha, changed_sha)
            self.assertEqual(
                changed_frozen.policy_sha256,
                self.runner._frozen_public(config, changed_frozen)["policy_sha256"],
            )
            with self.assertRaises(self.runner.pairing.PairingAbort) as raised:
                self.runner.run_live_e2(
                    config,
                    expected_plan_sha256=original_sha,
                    provider_factory=forbidden,
                )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(constructed)

    def test_reflection_filter_change_invalidates_old_plan_before_provider(self) -> None:
        config = self.config()
        original_frozen = self.runner.freeze_contract(config)
        original_sha = self.runner.plan_sha256(config, original_frozen)
        constructed = False

        def forbidden(*args):
            nonlocal constructed
            constructed = True
            raise AssertionError("provider must not be constructed")

        with mock.patch.object(
            self.runner.agent_v1, "_contains_forbidden_text", lambda value: False
        ):
            with self.assertRaises(self.runner.pairing.PairingAbort) as raised:
                self.runner.freeze_contract(config)
            self.assertEqual("security", raised.exception.code)
            with self.assertRaises(self.runner.pairing.PairingAbort) as raised:
                self.runner.run_live_e2(
                    config,
                    expected_plan_sha256=original_sha,
                    provider_factory=forbidden,
                )
        self.assertEqual("security", raised.exception.code)
        self.assertFalse(constructed)

    def test_three_arm_vaults_are_private_isolated_and_ephemeral(self) -> None:
        seen: list[Path] = []
        fixture_before = self.runner._fixture_hashes()
        with self.runner.pairing.secure_batch_scratch() as scratch:
            for arm in self.runner.ARMS:
                with self.runner.isolated_e2_arm_vault(scratch, arm) as vault:
                    seen.append(vault)
                    self.assertEqual(0o700, stat.S_IMODE(vault.stat().st_mode))
                    self.runner._clone_day(vault, "2026-07-14")
                    self.assertEqual(
                        0o600,
                        stat.S_IMODE((vault / "2026-07-14.md").stat().st_mode),
                    )
        self.assertEqual(3, len(set(seen)))
        self.assertTrue(all(not path.exists() for path in seen))
        self.assertEqual(fixture_before, self.runner._fixture_hashes())

    def test_material_gate_exact_same_state_probe_is_zero_call(self) -> None:
        config = self.config()
        with self.runner.pairing.secure_batch_scratch() as scratch:
            with self.runner.isolated_e2_arm_vault(scratch, "A1") as vault:
                state = self.runner.ArmState("A1", vault)
                self.runner._clone_day(vault, "2026-07-14")
                decision, key = self.runner._material_gate_decision(state, config)
                self.assertEqual("run", decision)
                state.last_material_key = key
                decision, repeated = self.runner._material_gate_decision(state, config)
                self.assertEqual("skip", decision)
                self.assertEqual(key, repeated)
                self.runner._clone_day(vault, "2026-07-15")
                decision, changed = self.runner._material_gate_decision(state, config)
                self.assertEqual("run", decision)
                self.assertNotEqual(key, changed)

    def test_full_fake_provider_replay_advances_independent_state_and_scores(self) -> None:
        config = self.config()
        frozen = self.runner.freeze_contract(config)
        report = self.runner.run_live_e2(
            config,
            expected_plan_sha256=self.runner.plan_sha256(config, frozen),
            provider_factory=self.fake_factory,
        )
        self.assertEqual("completed", report["status"])
        self.assertEqual("none", report["stop_code"])
        self.assertEqual(20, report["summary"]["days_completed"])
        self.assertEqual(60, len(report["days"]))
        self.assertTrue(report["summary"]["batch_quality"])
        self.assertEqual(4, report["summary"]["operation_coverage"]["target_counts"]["new"])
        for arm in self.runner.ARMS:
            aggregate = report["summary"]["by_arm"][arm]
            self.assertEqual(20, aggregate["days_evaluated"])
            self.assertEqual(20, aggregate["quality_days_passed"])
            self.assertEqual(1.0, aggregate["macro_f1"])
            self.assertEqual(1.0, aggregate["route_accuracy"])
            self.assertIsNone(aggregate["first_error_day"])
            self.assertEqual(4, aggregate["active_memories_end"])
        no_change = [
            item
            for item in report["days"]
            if item["expected_operation"] == "no_change"
        ]
        self.assertEqual(39, len(no_change))
        self.assertTrue(
            all(
                item["material_gate_probe"]
                == {
                    "required": True,
                    "decision": "skip",
                    "provider_calls": 0,
                    "passed": True,
                }
                for item in no_change
            )
        )
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("做产品决策前，我习惯", serialized)
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn("synthetic-provider-id", serialized)

        duplicated = json.loads(json.dumps(report))
        duplicated["days"][1] = duplicated["days"][0]
        with self.assertRaises(self.runner.pairing.PairingAbort):
            self.runner.validate_public_report(duplicated)
        wrong_completed = json.loads(json.dumps(report))
        wrong_completed["summary"]["days_completed"] = 19
        with self.assertRaises(self.runner.pairing.PairingAbort):
            self.runner.validate_public_report(wrong_completed)
        wrong_usage = json.loads(json.dumps(report))
        wrong_usage["summary"]["by_arm"]["W0"]["usage"]["calls"] += 1
        with self.assertRaises(self.runner.pairing.PairingAbort):
            self.runner.validate_public_report(wrong_usage)

    def test_a1_fatal_categories_stop_before_later_arm_construction(self) -> None:
        config = self.config()
        frozen = self.runner.freeze_contract(config)
        constructed: list[tuple[str, str]] = []

        def fatal_factory(arm, date, vault, config):
            constructed.append((date, arm))
            if date == "2026-07-15" and arm == "A1":
                return QueueProvider([{} for _ in range(config.budget.max_turns)], config.model)
            return QueueProvider(
                self.runner.oracle_fake_actions(vault, arm, date), config.model
            )

        report = self.runner.run_live_e2(
            config,
            expected_plan_sha256=self.runner.plan_sha256(config, frozen),
            provider_factory=fatal_factory,
        )
        self.assertTrue(report["executed"])
        self.assertEqual("stopped", report["status"])
        self.assertEqual("budget", report["stop_code"])
        self.assertEqual(1, report["summary"]["days_completed"])
        self.assertIn(("2026-07-15", "W1"), constructed)
        self.assertIn(("2026-07-15", "A1"), constructed)
        self.assertNotIn(("2026-07-15", "W0"), constructed)
        fatal_day = [
            item
            for item in report["days"]
            if item["date"] == "2026-07-15" and item["arm"] == "A1"
        ]
        self.assertEqual(1, len(fatal_day))
        self.assertEqual("budget", fatal_day[0]["error_code"])

        for kind in ("budget", "runtime", "tombstone", "feedback", "identity_label"):
            self.assertEqual(
                kind,
                self.runner._response_error_code(
                    {"status": "error", "error_kind": kind}
                ),
            )
            self.assertIn(kind, self.runner.FATAL_STOP_CODES)
        for kind in ("stale", "cas", "sensitive", "evidence", "conflict"):
            self.assertEqual(
                "security",
                self.runner._response_error_code(
                    {"status": "error", "error_kind": kind}
                ),
            )
        self.assertEqual(
            "agent_error",
            self.runner._response_error_code(
                {"status": "budget_exhausted", "error_kind": "loop"}
            ),
        )
        self.assertEqual("runtime", self.runner._safe_error_code(ValueError("local")))

    def test_first_paid_arm_then_fixture_drift_returns_metered_executed_report(self) -> None:
        config = self.config()
        original_hashes = self.runner._fixture_hashes
        hash_calls = 0
        constructed: list[tuple[str, str]] = []

        def drifting_hashes(*args, **kwargs):
            nonlocal hash_calls
            hash_calls += 1
            result = dict(original_hashes(*args, **kwargs))
            if hash_calls >= 5:
                result[sorted(result)[0]] = "0" * 64
            return result

        def recording_factory(arm, date, vault, config):
            constructed.append((date, arm))
            return QueueProvider(
                self.runner.oracle_fake_actions(vault, arm, date), config.model
            )

        with mock.patch.object(
            self.runner, "_fixture_hashes", side_effect=drifting_hashes
        ):
            frozen = self.runner.freeze_contract(config)
            report = self.runner.run_live_e2(
                config,
                expected_plan_sha256=self.runner.plan_sha256(config, frozen),
                provider_factory=recording_factory,
            )
        self.assertTrue(report["executed"])
        self.assertEqual("stopped", report["status"])
        self.assertEqual("security", report["stop_code"])
        self.assertEqual([("2026-07-14", "W0")], constructed)
        self.assertEqual(1, report["summary"]["batch"]["calls"])
        self.assertEqual(150, report["summary"]["batch"]["tokens"])
        self.assertGreater(report["summary"]["batch"]["cost_usd"], 0)
        self.assertEqual(1, len(report["days"]))
        self.assertEqual(0, report["summary"]["days_completed"])

    def test_unknown_live_exception_reports_execution_as_unknown(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(
            self.runner, "run_live_e2", side_effect=ValueError("unknown")
        ), contextlib.redirect_stderr(stderr):
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
        failure = json.loads(stderr.getvalue())
        self.assertIsNone(failure["executed"])
        self.assertEqual("runtime", failure["stop_code"])

    def test_quality_failure_finishes_same_day_three_arms_then_stops(self) -> None:
        config = self.config()
        frozen = self.runner.freeze_contract(config)

        def wrong_factory(arm, date, vault, config):
            actions = self.runner.oracle_fake_actions(vault, arm, date)
            if date == "2026-07-24":
                actions = [self.runner._finish_action()]
            return QueueProvider(actions, config.model)

        report = self.runner.run_live_e2(
            config,
            expected_plan_sha256=self.runner.plan_sha256(config, frozen),
            provider_factory=wrong_factory,
        )
        self.assertEqual("stopped", report["status"])
        self.assertEqual("quality_failure", report["stop_code"])
        self.assertEqual("2026-07-24", report["summary"]["stopped_after_day"])
        self.assertEqual(11, report["summary"]["days_completed"])
        self.assertEqual(33, len(report["days"]))
        self.assertEqual(
            {"W0", "W1", "A1"},
            {
                item["arm"]
                for item in report["days"]
                if item["date"] == "2026-07-24"
            },
        )
        self.assertFalse(report["summary"]["batch_quality"])

    def test_strict_report_allowlist_rejects_unknown_and_source_echo(self) -> None:
        plan = self.runner.build_plan(self.config())
        polluted = dict(plan)
        polluted["prompt"] = "secret"
        with self.assertRaises(self.runner.pairing.PairingAbort):
            self.runner.validate_public_report(polluted)
        leaked = json.loads(json.dumps(plan))
        leaked["limitations"][0] = "MEMENTO_SYNTHETIC_CONTEXT_TEST"
        with self.assertRaises(self.runner.pairing.PairingAbort):
            self.runner.validate_public_report(leaked)
        wrong_policy = json.loads(json.dumps(plan))
        wrong_policy["frozen"]["policy_sha256"] = "0" * 64
        with self.assertRaises(self.runner.pairing.PairingAbort) as raised:
            self.runner.validate_public_report(wrong_policy)
        self.assertEqual("security", raised.exception.code)
        forged_plan = json.loads(json.dumps(plan))
        forged_plan["plan_sha256"] = "0" * 64
        with self.assertRaises(self.runner.pairing.PairingAbort) as raised:
            self.runner.validate_public_report(forged_plan)
        self.assertEqual("security", raised.exception.code)

    def test_arbitrary_output_option_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "report.json"
            result = subprocess.run(
                [sys.executable, str(RUNNER_PATH), "--output", str(target)],
                cwd=REPO,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, result.returncode)
            self.assertFalse(target.exists())
            self.assertNotIn(str(target), result.stderr)


if __name__ == "__main__":
    unittest.main()
