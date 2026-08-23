#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO / "context-agent" / "eval" / "agent-v1" / "run_live_pairing.py"


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "remember_agent_v1_live_pairing", RUNNER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 live pairing runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeCompletion:
    def __init__(
        self,
        content: dict,
        *,
        usage: dict | None = None,
        model: str = "deepseek-v4-pro",
    ) -> None:
        self.content = json.dumps(content, ensure_ascii=False)
        self.usage = (
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 100,
            }
            if usage is None
            else usage
        )
        self.request_id = "redacted-by-runner"
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
        self.calls += 1
        if not self.actions:
            raise AssertionError("fake provider action 用尽")
        return FakeCompletion(
            self.actions.pop(0), usage=self.usage, model=self.model
        )


class RememberAgentV1LivePairingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()

    def config(self, **changes):
        values = {
            "model": "deepseek-v4-pro",
            "repeats": 1,
            "max_batch_calls": 4,
            "max_batch_tokens": 500_000,
            "max_batch_cost_usd": 1.0,
        }
        values.update(changes)
        return self.runner.PairingConfig(**values)

    def test_default_contract_is_one_pair_four_calls_and_two_source_days(self) -> None:
        config = self.runner.PairingConfig()
        plan = self.runner.build_plan(config)
        self.assertEqual(1, config.repeats)
        self.assertEqual(4, config.max_batch_calls)
        self.assertEqual(80_000, config.max_batch_tokens)
        self.assertEqual(0.10, config.max_batch_cost_usd)
        self.assertEqual(
            ("2026-07-14.md", "2026-07-17.md"), self.runner.CASE_SOURCES
        )
        self.assertEqual("2026-07-17", plan["frozen"]["as_of"])
        self.assertEqual(2, plan["frozen"]["source_files"])
        self.assertEqual(
            "same_revision_quality_cost_only", plan["frozen"]["focused_gate"]
        )
        self.assertFalse(plan["frozen"]["agent_gain_claimed"])
        self.assertEqual(
            "oracle_assisted_fixed_workflow",
            plan["frozen"]["w1_baseline_kind"],
        )
        self.assertRegex(plan["plan_sha256"], r"^[0-9a-f]{64}$")

    def fake_factory(self, providers: dict[tuple[str, int], QueueProvider]):
        def factory(arm, repetition, config):
            self.assertEqual("deepseek-v4-pro", config.model)
            return providers[(arm, repetition)]

        return factory

    def run_pairing(self, config, providers):
        frozen = self.runner.freeze_contract(config)
        return self.runner.run_live_pairing(
            config,
            expected_plan_sha256=self.runner.plan_sha256(config, frozen),
            provider_factory=self.fake_factory(providers),
        )

    def test_default_cli_is_plan_only_and_never_reads_key_or_constructs_provider(self) -> None:
        sentinel = "token-secret-sentinel"
        environment = dict(os.environ)
        environment["DEEPSEEK_API_KEY"] = sentinel
        result = subprocess.run(
            [
                sys.executable,
                str(RUNNER_PATH),
                "--repeats",
                "1",
                "--max-batch-calls",
                "4",
            ],
            cwd=REPO,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("plan_only", report["mode"])
        self.assertEqual("planned", report["status"])
        self.assertFalse(report["executed"])
        self.assertEqual(0, report["summary"]["batch"]["calls"])
        self.assertNotIn(sentinel, result.stdout)
        self.assertNotIn("/Users/", result.stdout)
        self.assertEqual([], report["runs"])

    def test_live_requires_both_switches_before_provider_construction(self) -> None:
        called = False

        def forbidden_factory(*args):
            nonlocal called
            called = True
            raise AssertionError("provider 不应被构造")

        with mock.patch.object(
            self.runner, "default_provider_factory", forbidden_factory
        ):
            code = self.runner.main(
                ["--live", "--repeats", "1", "--max-batch-calls", "4"]
            )
        self.assertEqual(2, code)
        self.assertFalse(called)

    def test_live_plan_hash_must_match_before_provider_construction(self) -> None:
        config = self.config()
        called = False

        def forbidden_factory(*args):
            nonlocal called
            called = True
            raise AssertionError("plan mismatch 前不得构造 provider")

        with self.assertRaises(self.runner.PairingAbort) as raised:
            self.runner.run_live_pairing(
                config,
                expected_plan_sha256="0" * 64,
                provider_factory=forbidden_factory,
            )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(called)

        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        original_equal = self.runner.FrozenContract.__eq__

        def wrapped_equal(left, right):
            return original_equal(left, right)

        with mock.patch.object(
            self.runner.FrozenContract, "__eq__", wrapped_equal
        ):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(
                original.runner_runtime_sha256, changed.runner_runtime_sha256
            )
            with self.assertRaises(self.runner.PairingAbort) as raised:
                self.runner.run_live_pairing(
                    config,
                    expected_plan_sha256=expected,
                    provider_factory=forbidden_factory,
                )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(called)

    def test_plan_binds_runtime_meter_monkeypatch_before_provider_construction(self) -> None:
        config = self.config()
        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        called = False

        def forbidden_factory(*args):
            nonlocal called
            called = True
            raise AssertionError("runtime mismatch 前不得构造 provider")

        def bypass_meter(self, arm, messages):
            del self, arm, messages

        with mock.patch.object(
            self.runner.BatchMeter, "before_call", bypass_meter
        ):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(
                original.runner_runtime_sha256, changed.runner_runtime_sha256
            )
            self.assertNotEqual(expected, self.runner.plan_sha256(config, changed))
            with self.assertRaises(self.runner.PairingAbort) as raised:
                self.runner.run_live_pairing(
                    config,
                    expected_plan_sha256=expected,
                    provider_factory=forbidden_factory,
                )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(called)

    def test_default_provider_is_resolved_only_after_frozen_plan_check(self) -> None:
        config = self.config()
        live = self.runner.run_live_pairing
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
            with self.assertRaises(self.runner.PairingAbort) as raised:
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
            with self.assertRaises(self.runner.PairingAbort) as raised:
                live(config, expected_plan_sha256=expected)
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(constructed)

    def test_runner_namespace_constants_and_class_drift_reject_old_plan(self) -> None:
        config = self.config()
        constructed = False

        def forbidden_factory(*args):
            nonlocal constructed
            constructed = True
            raise AssertionError("provider must not be constructed")

        original_abort = self.runner.PairingAbort

        class ReplacementPairingAbort(original_abort):
            pass

        mutations = (
            (
                "W1_TERMINAL_INSTRUCTION",
                self.runner.W1_TERMINAL_INSTRUCTION + "<drift />",
            ),
            (
                "PUBLIC_ERROR_CODES",
                self.runner.PUBLIC_ERROR_CODES | {"runner_drift_test"},
            ),
            ("CASE_ID", self.runner.CASE_ID + "_drift"),
            (
                "CONTEXT_AGENT_ROOT",
                self.runner.CONTEXT_AGENT_ROOT.parent / "runner-drift-root",
            ),
            ("PairingAbort", ReplacementPairingAbort),
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
                    self.assertNotEqual(
                        expected, self.runner.plan_sha256(config, changed)
                    )
                    with self.assertRaises(original_abort) as raised:
                        self.runner.run_live_pairing(
                            config,
                            expected_plan_sha256=expected,
                            provider_factory=forbidden_factory,
                        )
                self.assertEqual("plan_mismatch", raised.exception.code)
                self.assertFalse(constructed)

    def test_runner_source_hash_covers_non_execution_source_changes(self) -> None:
        original = RUNNER_PATH.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "runner.py"
            copied.write_bytes(original)
            baseline = self.runner._runner_source_sha256(copied)
            copied.write_bytes(original + b"\n# safety-surface-change\n")
            changed = self.runner._runner_source_sha256(copied)
        self.assertNotEqual(baseline, changed)
        self.assertEqual(
            self.runner._runner_source_sha256(),
            self.runner.freeze_contract(self.config()).runner_source_sha256,
        )

    def test_plan_binds_dependency_files_and_runtime_symbols(self) -> None:
        config = self.config()
        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        manifest = self.runner._dependency_contract()
        self.assertEqual(
            {"agent_v1", "core", "deepseek_provider", "reflection"},
            set(manifest["source_files"]),
        )
        self.assertIn(
            "_contains_forbidden_text",
            manifest["runtime_surface"]["module_external_aliases"]["agent_v1"],
        )
        self.assertTrue(
            set(self.runner.AGENT_EXTERNAL_ALIAS_SURFACE).issubset(
                manifest["runtime_surface"]["module_external_aliases"]["agent_v1"]
            )
        )
        self.assertEqual(
            set(self.runner.REFLECTION_EXTERNAL_ALIAS_SURFACE),
            set(
                manifest["runtime_surface"]["module_external_aliases"][
                    "reflection"
                ]
            ),
        )
        alias_sources = manifest["runtime_surface"][
            "module_external_alias_sources"
        ]
        self.assertEqual("core._source_path", alias_sources["agent_v1"]["_source_path"])
        self.assertEqual(
            "reflection._contains_forbidden_text",
            alias_sources["agent_v1"]["_contains_forbidden_text"],
        )
        self.assertEqual(
            "core.ContractError",
            alias_sources["reflection"]["ContractError"],
        )
        self.assertEqual(
            {"agent_v1", "core", "deepseek_provider", "reflection"},
            {
                item["alias"]
                for item in manifest["project_module_aliases"]["aliases"]
            },
        )
        closure = manifest["runtime_surface"]["project_namespace_closure"]
        self.assertEqual(
            {"agent_v1", "core", "deepseek_provider", "reflection"},
            set(closure),
        )
        self.assertIn(
            "_validate_patch_shape",
            closure["agent_v1"]["module_owned_functions_and_classes"],
        )
        self.assertIn(
            "collect_reflection_sources",
            closure["reflection"]["module_owned_functions_and_classes"],
        )
        self.assertIn(
            "MODEL_PRICING", closure["core"]["uppercase_constants"]
        )
        self.assertIn(
            "IDENTITY_LABEL_PATTERNS",
            closure["agent_v1"]["uppercase_constants"],
        )
        for digest in manifest["source_files"].values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(
            self.runner._sha(manifest), original.dependency_manifest_sha256
        )
        called = False

        def forbidden_factory(*args):
            nonlocal called
            called = True
            raise AssertionError("dependency mismatch 前不得构造 provider")

        def fake_cost(*args, **kwargs):
            del args, kwargs
            return 0.0

        with mock.patch.object(self.runner.core, "calculate_cost", fake_cost):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(
                original.dependency_manifest_sha256,
                changed.dependency_manifest_sha256,
            )
            self.assertNotEqual(expected, self.runner.plan_sha256(config, changed))
            with self.assertRaises(self.runner.PairingAbort) as raised:
                self.runner.run_live_pairing(
                    config,
                    expected_plan_sha256=expected,
                    provider_factory=forbidden_factory,
                )
        self.assertEqual("plan_mismatch", raised.exception.code)
        self.assertFalse(called)

    def test_agent_external_alias_drift_rejects_old_plan_before_provider(self) -> None:
        config = self.config()

        def forbidden_factory(*args):
            raise AssertionError("alias drift 前不得构造 provider")

        original_source_path = self.runner.agent_v1._source_path
        original_validate_patch_shape = self.runner.agent_v1._validate_patch_shape
        original_has_explicit_signal = self.runner.agent_v1._has_explicit_signal

        def wrapped_source_path(*args, **kwargs):
            return original_source_path(*args, **kwargs)

        def wrapped_validate_patch_shape(*args, **kwargs):
            return original_validate_patch_shape(*args, **kwargs)

        def wrapped_has_explicit_signal(*args, **kwargs):
            return original_has_explicit_signal(*args, **kwargs)

        identity_mutations = (
            ("_contains_forbidden_text", lambda value: False),
            ("_source_path", wrapped_source_path),
            (
                "IDENTITY_LABEL_PATTERNS",
                self.runner.agent_v1.IDENTITY_LABEL_PATTERNS
                + (re.compile(r"policy-binding-regression-only"),),
            ),
        )
        for symbol_name, replacement in identity_mutations:
            with self.subTest(symbol=symbol_name):
                original = self.runner.freeze_contract(config)
                expected = self.runner.plan_sha256(config, original)
                with mock.patch.object(
                    self.runner.agent_v1, symbol_name, replacement
                ):
                    with self.assertRaises(self.runner.PairingAbort) as raised:
                        self.runner.freeze_contract(config)
                    self.assertEqual("security", raised.exception.code)
                    with self.assertRaises(self.runner.PairingAbort) as raised:
                        self.runner.run_live_pairing(
                            config,
                            expected_plan_sha256=expected,
                            provider_factory=forbidden_factory,
                        )
                self.assertEqual("security", raised.exception.code)

        behavior_mutations = (
            ("_validate_patch_shape", wrapped_validate_patch_shape),
            ("_has_explicit_signal", wrapped_has_explicit_signal),
        )
        for symbol_name, replacement in behavior_mutations:
            with self.subTest(module_owned_symbol=symbol_name):
                original = self.runner.freeze_contract(config)
                expected = self.runner.plan_sha256(config, original)
                with mock.patch.object(
                    self.runner.agent_v1, symbol_name, replacement
                ):
                    changed = self.runner.freeze_contract(config)
                    self.assertNotEqual(
                        original.dependency_manifest_sha256,
                        changed.dependency_manifest_sha256,
                    )
                    with self.assertRaises(self.runner.PairingAbort) as raised:
                        self.runner.run_live_pairing(
                            config,
                            expected_plan_sha256=expected,
                            provider_factory=forbidden_factory,
                        )
                self.assertEqual("plan_mismatch", raised.exception.code)

        reflection_source_path = self.runner.reflection._source_path
        reflection_ensure_text = self.runner.reflection._ensure_text
        reflection_contract_error = self.runner.reflection.ContractError

        def wrapped_reflection_source_path(*args, **kwargs):
            return reflection_source_path(*args, **kwargs)

        def wrapped_reflection_ensure_text(*args, **kwargs):
            return reflection_ensure_text(*args, **kwargs)

        class ReplacementContractError(reflection_contract_error):
            pass

        reflection_mutations = (
            ("_source_path", wrapped_reflection_source_path),
            ("_ensure_text", wrapped_reflection_ensure_text),
            ("ContractError", ReplacementContractError),
        )
        for symbol_name, replacement in reflection_mutations:
            with self.subTest(reflection_alias=symbol_name):
                original = self.runner.freeze_contract(config)
                expected = self.runner.plan_sha256(config, original)
                with mock.patch.object(
                    self.runner.reflection, symbol_name, replacement
                ):
                    with self.assertRaises(self.runner.PairingAbort) as raised:
                        self.runner.freeze_contract(config)
                    self.assertEqual("security", raised.exception.code)
                    with self.assertRaises(self.runner.PairingAbort) as raised:
                        self.runner.run_live_pairing(
                            config,
                            expected_plan_sha256=expected,
                            provider_factory=forbidden_factory,
                        )
                self.assertEqual("security", raised.exception.code)

    def test_duplicate_project_modules_cannot_replace_external_alias_identity(self) -> None:
        config = self.config()
        frozen = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, frozen)
        constructed = False

        def forbidden_factory(*args):
            nonlocal constructed
            constructed = True
            raise AssertionError("duplicate alias must precede provider")

        def load_duplicate(module, duplicate_name):
            spec = importlib.util.spec_from_file_location(duplicate_name, module.__file__)
            if spec is None or spec.loader is None:
                raise AssertionError("duplicate module spec unavailable")
            duplicate = importlib.util.module_from_spec(spec)
            sys.modules[duplicate_name] = duplicate
            spec.loader.exec_module(duplicate)
            return duplicate

        duplicate_names = (
            "remember_agent_v1_duplicate_core",
            "remember_agent_v1_duplicate_reflection",
        )
        duplicate_core = load_duplicate(self.runner.core, duplicate_names[0])
        duplicate_reflection = load_duplicate(
            self.runner.reflection, duplicate_names[1]
        )
        try:
            mutations = (
                (self.runner.agent_v1, "ContractError", duplicate_core.ContractError),
                (self.runner.agent_v1, "_source_path", duplicate_core._source_path),
                (self.runner.reflection, "ContractError", duplicate_core.ContractError),
                (self.runner.reflection, "_source_path", duplicate_core._source_path),
                (
                    self.runner.agent_v1,
                    "_contains_forbidden_text",
                    duplicate_reflection._contains_forbidden_text,
                ),
            )
            for module, symbol, replacement in mutations:
                with self.subTest(module=module.__name__, symbol=symbol):
                    with mock.patch.object(module, symbol, replacement):
                        with self.assertRaises(self.runner.PairingAbort) as raised:
                            self.runner.freeze_contract(config)
                        self.assertEqual("security", raised.exception.code)
                        with self.assertRaises(self.runner.PairingAbort) as raised:
                            self.runner.run_live_pairing(
                                config,
                                expected_plan_sha256=expected,
                                provider_factory=forbidden_factory,
                            )
                        self.assertEqual("security", raised.exception.code)
        finally:
            for duplicate_name in duplicate_names:
                sys.modules.pop(duplicate_name, None)
        self.assertFalse(constructed)

    def test_class_method_and_pricing_constant_drift_reject_old_plan(self) -> None:
        config = self.config()

        def forbidden_factory(*args):
            raise AssertionError("namespace drift 前不得构造 provider")

        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        original_validate = self.runner.agent_v1.AgentBudget.validate

        def wrapped_validate(instance):
            return original_validate(instance)

        with mock.patch.object(
            self.runner.agent_v1.AgentBudget, "validate", wrapped_validate
        ):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(
                original.dependency_manifest_sha256,
                changed.dependency_manifest_sha256,
            )
            with self.assertRaises(self.runner.PairingAbort) as raised:
                self.runner.run_live_pairing(
                    config,
                    expected_plan_sha256=expected,
                    provider_factory=forbidden_factory,
                )
        self.assertEqual("plan_mismatch", raised.exception.code)

        original = self.runner.freeze_contract(config)
        expected = self.runner.plan_sha256(config, original)
        pricing = self.runner.core.MODEL_PRICING[config.model]
        changed_pricing = dict(self.runner.core.MODEL_PRICING)
        changed_pricing[config.model] = self.runner.core.Pricing(
            cache_hit_input_usd_per_million=(
                pricing.cache_hit_input_usd_per_million + 0.000001
            ),
            cache_miss_input_usd_per_million=pricing.cache_miss_input_usd_per_million,
            output_usd_per_million=pricing.output_usd_per_million,
            effective_date=pricing.effective_date,
        )
        with mock.patch.object(
            self.runner.core, "MODEL_PRICING", changed_pricing
        ):
            changed = self.runner.freeze_contract(config)
            self.assertNotEqual(
                original.dependency_manifest_sha256,
                changed.dependency_manifest_sha256,
            )
            with self.assertRaises(self.runner.PairingAbort) as raised:
                self.runner.run_live_pairing(
                    config,
                    expected_plan_sha256=expected,
                    provider_factory=forbidden_factory,
                )
        self.assertEqual("plan_mismatch", raised.exception.code)

    def test_dependency_constant_serializer_fails_closed_on_cycle_or_unknown(self) -> None:
        cyclic: list[object] = []
        cyclic.append(cyclic)
        with self.assertRaises(self.runner.PairingAbort) as raised:
            self.runner._stable_dependency_value(cyclic)
        self.assertEqual("security", raised.exception.code)
        with self.assertRaises(self.runner.PairingAbort) as raised:
            self.runner._stable_dependency_value(object())
        self.assertEqual("security", raised.exception.code)

    def test_dependency_source_file_hash_changes_for_modified_copy(self) -> None:
        source = Path(self.runner.agent_v1.__file__).resolve(strict=True)
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "agent_v1.py"
            copied.write_bytes(source.read_bytes())
            baseline = self.runner._secure_source_file_sha256(copied)
            copied.write_bytes(source.read_bytes() + b"\n# dependency-change\n")
            changed = self.runner._secure_source_file_sha256(copied)
        self.assertNotEqual(baseline, changed)

    def test_synthetic_clone_is_0700_0600_isolated_and_ephemeral(self) -> None:
        fixture_before = self.runner._source_hashes(self.runner.SCENARIO_ROOT)
        seen: list[Path] = []
        with self.runner.secure_batch_scratch() as scratch:
            self.assertEqual(0o700, stat.S_IMODE(scratch.stat().st_mode))
            with self.runner.isolated_case_vault(scratch) as first:
                seen.append(first)
                self.assertEqual(0o700, stat.S_IMODE(first.stat().st_mode))
                for filename in self.runner.CASE_SOURCES:
                    self.assertEqual(
                        0o600, stat.S_IMODE((first / filename).stat().st_mode)
                    )
                (first / self.runner.CASE_SOURCES[0]).write_text(
                    "isolated mutation", encoding="utf-8"
                )
            with self.runner.isolated_case_vault(scratch) as second:
                seen.append(second)
        self.assertNotEqual(seen[0], seen[1])
        self.assertTrue(all(not path.exists() for path in seen))
        self.assertEqual(fixture_before, self.runner._source_hashes(self.runner.SCENARIO_ROOT))

    def test_dangerous_tmpdir_and_vault_are_rejected_before_provider_and_unchanged(self) -> None:
        config = self.config()
        frozen = self.runner.freeze_contract(config)
        expected_plan = self.runner.plan_sha256(config, frozen)
        called = False

        def forbidden_factory(*args):
            nonlocal called
            called = True
            raise AssertionError("unsafe scratch 不得构造 provider")

        with tempfile.TemporaryDirectory(prefix="pairing-sentinel-parent-") as parent:
            sentinel = Path(parent) / "sentinel-vault"
            sentinel.mkdir(mode=0o700)
            protected = sentinel / "protected.md"
            protected.write_text("do-not-touch", encoding="utf-8")
            protected.chmod(0o600)
            before = {
                "content": protected.read_bytes(),
                "file_mode": stat.S_IMODE(protected.stat().st_mode),
                "dir_mode": stat.S_IMODE(sentinel.stat().st_mode),
                "entries": sorted(path.name for path in sentinel.iterdir()),
            }
            with mock.patch.dict(
                os.environ,
                {"TMPDIR": str(sentinel), "MEMENTO_VAULT": str(sentinel)},
            ):
                with self.assertRaises(self.runner.PairingAbort) as raised:
                    self.runner.run_live_pairing(
                        config,
                        expected_plan_sha256=expected_plan,
                        provider_factory=forbidden_factory,
                    )
            after = {
                "content": protected.read_bytes(),
                "file_mode": stat.S_IMODE(protected.stat().st_mode),
                "dir_mode": stat.S_IMODE(sentinel.stat().st_mode),
                "entries": sorted(path.name for path in sentinel.iterdir()),
            }
        self.assertEqual("security", raised.exception.code)
        self.assertFalse(called)
        self.assertEqual(before, after)

    def test_fake_pair_executes_isolated_w1_and_a1_with_attributed_usage(self) -> None:
        action = self.runner._revision_action(self.runner.SCENARIO_ROOT)
        memory_id = action["arguments"]["target_memory_id"]
        providers = {
            ("W1", 1): QueueProvider([action]),
            ("A1", 1): QueueProvider(
                [
                    {
                        "schema_version": "1.0",
                        "action": "read_memory",
                        "reason_code": "inspect_existing",
                        "arguments": {"memory_id": memory_id},
                    },
                    action,
                ]
            ),
        }
        report = self.run_pairing(self.config(), providers)
        self.assertEqual("completed", report["status"])
        self.assertEqual(1, report["summary"]["pairs_completed"])
        self.assertEqual(3, report["summary"]["batch"]["calls"])
        self.assertEqual(1, report["summary"]["batch"]["by_arm"]["W1"]["calls"])
        self.assertEqual(2, report["summary"]["batch"]["by_arm"]["A1"]["calls"])
        by_arm = {item["arm"]: item for item in report["runs"]}
        self.assertEqual(
            ["read_memory", "search_history", "finalize_patch"],
            by_arm["W1"]["trajectory"],
        )
        self.assertEqual(
            ["read_memory", "finalize_patch"], by_arm["A1"]["trajectory"]
        )
        self.assertTrue(by_arm["W1"]["quality"]["passed"])
        self.assertTrue(by_arm["A1"]["quality"]["passed"])
        for arm in ("W1", "A1"):
            checks = by_arm[arm]["quality"]["checks"]
            self.assertEqual(
                {
                    "terminal_updated",
                    "operation_revise",
                    "target_preserved",
                    "revision_advanced_once",
                    "expected_statement",
                    "expected_scope",
                    "new_evidence_exact_2026_07_17",
                    "counterevidence_exact_2026_07_14",
                    "source_hashes_exact",
                    "cas_chain_exact",
                    "strict_evidence_and_sensitive_contract",
                    "unique_memory_exactly_two_revisions",
                    "usage_complete",
                    "no_rejected_invalid_or_budget_steps",
                    "source_clone_unchanged",
                },
                set(checks),
            )
            self.assertTrue(all(checks.values()))
        self.assertEqual(1, by_arm["W1"]["usage"]["model_calls"])
        self.assertEqual(2, by_arm["A1"]["usage"]["model_calls"])
        self.assertEqual(
            by_arm["W1"]["baseline_sha256"], by_arm["A1"]["baseline_sha256"]
        )
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(self.runner.OLD_MARKER, serialized)
        self.assertNotIn(self.runner.NEW_MARKER, serialized)
        self.assertNotIn("memento-agent-pairing-", serialized)
        self.assertNotIn("redacted-by-runner", serialized)

    def test_w1_terminal_prompt_is_frozen_and_allows_only_finish_or_patch(self) -> None:
        action = self.runner._revision_action(self.runner.SCENARIO_ROOT)

        class InspectingProvider(QueueProvider):
            def complete(provider_self, messages):
                last = messages[-1]
                self.assertEqual("user", last["role"])
                self.assertIn("finalize_patch 或 finish", last["content"])
                self.assertIn("不得再输出 read_memory", last["content"])
                return super(InspectingProvider, provider_self).complete(messages)

        providers = {
            ("W1", 1): InspectingProvider([action]),
            ("A1", 1): QueueProvider(
                [
                    {
                        "schema_version": "1.0",
                        "action": "read_memory",
                        "reason_code": "inspect_existing",
                        "arguments": {
                            "memory_id": action["arguments"]["target_memory_id"]
                        },
                    },
                    action,
                ]
            ),
        }
        report = self.run_pairing(self.config(), providers)
        self.assertEqual("completed", report["status"])
        self.assertEqual(
            self.runner.W1_TERMINAL_POLICY_VERSION,
            report["frozen"]["w1_terminal_policy_version"],
        )
        self.assertEqual(
            report["frozen"]["budget"]["max_prompt_chars"],
            report["frozen"]["input_maximum_chars_both_arms"],
        )
        self.assertEqual(2, len(action["arguments"]["evidence"]))
        self.assertEqual(
            {40, 44}, {item["line"] for item in action["arguments"]["evidence"]}
        )

    def test_usage_missing_stops_batch_before_a1(self) -> None:
        action = self.runner._revision_action(self.runner.SCENARIO_ROOT)
        providers = {
            ("W1", 1): QueueProvider([action], usage={}),
            ("A1", 1): QueueProvider([action]),
        }
        report = self.run_pairing(self.config(), providers)
        self.assertEqual("stopped", report["status"])
        self.assertEqual("usage_missing", report["stop_code"])
        self.assertEqual(1, report["summary"]["batch"]["calls"])
        self.assertEqual(0, providers[("A1", 1)].calls)
        self.assertFalse(report["summary"]["batch"]["cost_complete"])

    def test_partial_usage_is_not_normalized_into_a_complete_cost(self) -> None:
        action = self.runner._revision_action(self.runner.SCENARIO_ROOT)
        partial = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }
        providers = {
            ("W1", 1): QueueProvider([action], usage=partial),
            ("A1", 1): QueueProvider([action]),
        }
        report = self.run_pairing(self.config(), providers)
        self.assertEqual("usage_missing", report["stop_code"])
        self.assertEqual(1, report["summary"]["batch"]["calls"])
        self.assertFalse(report["summary"]["batch"]["cost_complete"])
        self.assertEqual(1, report["runs"][0]["usage"]["model_calls"])
        self.assertTrue(report["runs"][0]["usage"]["usage_missing"])
        self.assertIsNone(report["runs"][0]["usage"]["cost_usd"])
        self.assertEqual(0, providers[("A1", 1)].calls)

    def test_completion_model_mismatch_is_security_stop_before_a1(self) -> None:
        action = self.runner._revision_action(self.runner.SCENARIO_ROOT)
        providers = {
            ("W1", 1): QueueProvider([action], model="unexpected-model"),
            ("A1", 1): QueueProvider([action]),
        }
        report = self.run_pairing(self.config(), providers)
        self.assertEqual("security", report["stop_code"])
        self.assertEqual(1, report["summary"]["batch"]["calls"])
        self.assertEqual(150, report["summary"]["batch"]["tokens"])
        self.assertFalse(report["summary"]["batch"]["cost_complete"])
        self.assertEqual(1, report["runs"][0]["usage"]["model_calls"])
        self.assertIsNone(report["runs"][0]["usage"]["cost_usd"])
        self.assertEqual(0, providers[("A1", 1)].calls)

    def test_provider_error_stops_without_serializing_exception_text(self) -> None:
        class FailingProvider:
            calls = 0

            def complete(self, messages):
                del messages
                self.calls += 1
                raise self_runner.deepseek_provider.ProviderError(
                    "upstream echoed token-secret-sentinel-and-record-text"
                )

        self_runner = self.runner
        failing = FailingProvider()
        never = QueueProvider([self.runner._revision_action(self.runner.SCENARIO_ROOT)])
        providers = {("W1", 1): failing, ("A1", 1): never}
        report = self.run_pairing(self.config(), providers)
        self.assertEqual("provider_error", report["stop_code"])
        self.assertEqual(1, report["summary"]["batch"]["calls"])
        self.assertEqual(0, never.calls)
        self.assertFalse(report["summary"]["batch"]["cost_complete"])
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("token-secret-sentinel", serialized)
        self.assertNotIn("upstream echoed", serialized)

    def test_provider_error_model_and_complete_usage_are_checked(self) -> None:
        complete_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 100,
        }
        self_runner = self.runner

        class ErrorProvider:
            def __init__(self, model):
                self.model = model
                self.calls = 0

            def complete(self, messages):
                del messages
                self.calls += 1
                raise self_runner.deepseek_provider.ProviderError(
                    "finite provider failure",
                    usage=complete_usage,
                    model=self.model,
                )

        never = QueueProvider([self.runner._revision_action(self.runner.SCENARIO_ROOT)])
        matching = ErrorProvider("deepseek-v4-pro")
        report = self.run_pairing(
            self.config(), {("W1", 1): matching, ("A1", 1): never}
        )
        self.assertEqual("provider_error", report["stop_code"])
        self.assertTrue(report["summary"]["batch"]["cost_complete"])
        self.assertEqual(150, report["summary"]["batch"]["tokens"])
        self.assertEqual(1, report["runs"][0]["usage"]["model_calls"])
        self.assertFalse(report["runs"][0]["usage"]["usage_missing"])
        self.assertEqual(0, never.calls)

        mismatching = ErrorProvider("unexpected-model")
        report = self.run_pairing(
            self.config(), {("W1", 1): mismatching, ("A1", 1): never}
        )
        self.assertEqual("security", report["stop_code"])
        self.assertEqual(150, report["summary"]["batch"]["tokens"])
        self.assertFalse(report["summary"]["batch"]["cost_complete"])
        self.assertIsNone(report["runs"][0]["usage"]["cost_usd"])
        self.assertEqual(0, never.calls)

    def test_partial_provider_usage_is_unpriced_but_not_dropped(self) -> None:
        partial_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }
        action = self.runner._revision_action(self.runner.SCENARIO_ROOT)
        never = QueueProvider([action])

        completion_mismatch = QueueProvider(
            [action], usage=partial_usage, model="unexpected-model"
        )
        report = self.run_pairing(
            self.config(),
            {("W1", 1): completion_mismatch, ("A1", 1): never},
        )
        self.assertEqual("security", report["stop_code"])
        self.assertEqual(150, report["summary"]["batch"]["tokens"])
        self.assertEqual(150, report["runs"][0]["usage"]["total_tokens"])
        self.assertFalse(report["summary"]["batch"]["cost_complete"])
        self.assertIsNone(report["runs"][0]["usage"]["cost_usd"])

        self_runner = self.runner

        class PartialErrorProvider:
            def __init__(self, model):
                self.model = model
                self.calls = 0

            def complete(self, messages):
                del messages
                self.calls += 1
                raise self_runner.deepseek_provider.ProviderError(
                    "finite provider failure",
                    usage=partial_usage,
                    model=self.model,
                )

        for model, expected_code in (
            (None, "provider_error"),
            ("deepseek-v4-pro", "provider_error"),
            ("unexpected-model", "security"),
        ):
            with self.subTest(model=model):
                report = self.run_pairing(
                    self.config(),
                    {
                        ("W1", 1): PartialErrorProvider(model),
                        ("A1", 1): never,
                    },
                )
                self.assertEqual(expected_code, report["stop_code"])
                self.assertEqual(150, report["summary"]["batch"]["tokens"])
                self.assertEqual(150, report["runs"][0]["usage"]["total_tokens"])
                self.assertFalse(report["summary"]["batch"]["cost_complete"])
                self.assertIsNone(report["runs"][0]["usage"]["cost_usd"])

    def test_hard_token_limit_stops_before_first_provider_call(self) -> None:
        action = self.runner._revision_action(self.runner.SCENARIO_ROOT)
        provider = QueueProvider([action])
        providers = {("W1", 1): provider, ("A1", 1): provider}
        report = self.run_pairing(self.config(max_batch_tokens=1), providers)
        self.assertEqual("token_limit", report["stop_code"])
        self.assertEqual(0, report["summary"]["batch"]["calls"])
        self.assertEqual(0, provider.calls)

    def test_quality_failure_completes_both_arms_and_marks_batch_false(self) -> None:
        finish = {
            "schema_version": "1.0",
            "action": "finish",
            "reason_code": "no_material_change",
            "arguments": {"reason": "no_change"},
        }
        providers = {
            ("W1", 1): QueueProvider([finish]),
            ("A1", 1): QueueProvider([finish]),
        }
        report = self.run_pairing(self.config(), providers)
        self.assertEqual("completed", report["status"])
        self.assertEqual("none", report["stop_code"])
        self.assertEqual(2, report["summary"]["batch"]["calls"])
        self.assertEqual(1, providers[("A1", 1)].calls)
        self.assertEqual(1, report["summary"]["pairs_completed"])
        self.assertFalse(report["summary"]["batch_quality"])
        self.assertEqual(2, len(report["runs"]))
        self.assertTrue(all(not run["quality"]["passed"] for run in report["runs"]))

    def test_main_unknown_failure_is_redacted_finite_runtime_code(self) -> None:
        with mock.patch.object(
            self.runner,
            "build_plan",
            side_effect=RuntimeError("/tmp/token-private-sentinel"),
        ), mock.patch("sys.stderr") as stderr:
            code = self.runner.main([])
        self.assertEqual(2, code)
        serialized = "".join(str(call) for call in stderr.write.call_args_list)
        self.assertIn("runtime", serialized)
        self.assertNotIn("/tmp", serialized)
        self.assertNotIn("token-private-sentinel", serialized)

    def test_public_report_uses_recursive_allowlist_and_finite_strings(self) -> None:
        report = self.runner.build_plan(self.config())
        mutations = []
        unknown_top = json.loads(json.dumps(report))
        unknown_top["provider_echo"] = "credential-secret-sentinel"
        mutations.append(unknown_top)
        unknown_nested = json.loads(json.dumps(report))
        unknown_nested["summary"]["batch"]["provider_echo"] = "record text"
        mutations.append(unknown_nested)
        fixture_marker = json.loads(json.dumps(report))
        fixture_marker["status"] = self.runner.NEW_MARKER
        mutations.append(fixture_marker)
        provider_echo = json.loads(json.dumps(report))
        provider_echo["frozen"]["prompt_version"] = "upstream echoed record text"
        mutations.append(provider_echo)
        for bad in mutations:
            with self.assertRaises(self.runner.PairingAbort):
                self.runner.validate_public_report(bad)

    def test_output_option_is_rejected_without_writing_or_provider_construction(self) -> None:
        called = False

        def forbidden_factory(*args):
            nonlocal called
            called = True
            raise AssertionError("--output 拒绝前不得构造 provider")

        with tempfile.TemporaryDirectory(prefix="pairing-output-sentinel-") as parent:
            vault = Path(parent) / "real-vault-sentinel"
            vault.mkdir(mode=0o700)
            sentinel = vault / "daily.md"
            sentinel.write_text("unchanged", encoding="utf-8")
            before = sentinel.read_bytes()
            with mock.patch.object(
                self.runner, "default_provider_factory", forbidden_factory
            ), mock.patch("sys.stderr") as stderr:
                code = self.runner.main(["--output", str(vault / "report.json")])
            serialized = "".join(str(call) for call in stderr.write.call_args_list)
            after = sentinel.read_bytes()
        self.assertEqual(2, code)
        self.assertFalse(called)
        self.assertEqual(before, after)
        self.assertIn("contract", serialized)
        self.assertNotIn(str(vault), serialized)
        self.assertNotIn("--output", self.runner.build_parser().format_help())


if __name__ == "__main__":
    unittest.main()
