#!/usr/bin/env python3
"""Offline safety and contract tests for the cognitive live harness."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
RUNNER_PATH = (
    REPO
    / "context-agent"
    / "eval"
    / "cognitive-v1"
    / "run_live_acceptance.py"
)


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "memento_cognitive_live_acceptance", RUNNER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load cognitive live acceptance harness")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FailingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages):
        del messages
        self.calls += 1
        raise RuntimeError("synthetic provider failure")


class CognitiveLiveAcceptanceHarnessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()

    def config(self):
        return self.runner.AcceptanceConfig()

    def test_default_is_plan_only_and_never_constructs_provider(self) -> None:
        output = io.StringIO()
        with mock.patch.object(
            self.runner.deepseek_provider,
            "DeepSeekProvider",
            side_effect=AssertionError("network provider constructed"),
        ), contextlib.redirect_stdout(output):
            code = self.runner.main([])

        report = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("plan_only", report["mode"])
        self.assertFalse(report["executed"])
        self.assertEqual([], report["cases"])
        self.assertEqual(0, report["usage"]["calls"])
        self.assertEqual("not_created", report["temporary_directory"])

    def test_v4_plan_binds_agent_policies_and_as_of_sources(self) -> None:
        payload = self.runner._plan_payload(self.config())
        self.assertEqual(
            "cognitive-secretary-v1-isolated-2case-v4", payload["version"]
        )
        contract = payload["agent_contract"]
        self.assertEqual(
            self.runner.EXPECTED_AGENT_CONTRACT,
            {
                key: contract[key]
                for key in self.runner.EXPECTED_AGENT_CONTRACT
            },
        )
        self.assertEqual(
            self.runner.core.sha256_bytes(
                self.runner.agent_v1.AGENTIC_WORKFLOW_INSTRUCTION.encode("utf-8")
            ),
            contract["workflow_instruction_sha256"],
        )
        self.assertEqual(
            self.runner.core.sha256_bytes(
                self.runner.agent_v1.STABLE_NEW_IDENTITY_INSTRUCTION.encode("utf-8")
            ),
            contract["stable_new_identity_instruction_sha256"],
        )
        self.assertEqual(
            self.runner.core.sha256_bytes(
                self.runner.agent_v1.STABLE_NEW_TERMINAL_GATE_INSTRUCTION.encode(
                    "utf-8"
                )
            ),
            contract["stable_new_terminal_gate_instruction_sha256"],
        )
        source_hashes = payload["as_of_contract"]["source_sha256"]
        self.assertEqual(
            set(self.runner.AS_OF_SOURCE_NAMES), set(source_hashes)
        )
        self.assertTrue(
            payload["as_of_contract"]["request_as_of_bounds_daily_history"]
        )
        self.assertTrue(
            payload["as_of_contract"]["request_as_of_bounds_record_authorization"]
        )
        self.assertTrue(
            payload["as_of_contract"]["receipt_head_revalidated_at_authorization"]
        )

    def test_agent_contract_fails_closed_when_policy_version_drifts(self) -> None:
        with mock.patch.object(
            self.runner.agent_v1,
            "AGENT_PROMPT_VERSION",
            "remember-agent-v1.23",
        ), self.assertRaisesRegex(
            self.runner.core.ContractError, "Agent contract is stale"
        ):
            self.runner._plan_payload(self.config())

    def test_live_gate_requires_current_plan_and_exact_confirmation(self) -> None:
        config = self.config()
        plan = self.runner.plan_sha256(config)
        provider_builder = mock.Mock(side_effect=AssertionError("provider built"))

        with self.assertRaisesRegex(ValueError, "plan_mismatch"):
            self.runner.run_acceptance(
                config,
                live=True,
                expected_plan_sha256="0" * 64,
                confirmation=self.runner.LIVE_CONFIRMATION,
                provider_builder=provider_builder,
            )
        with self.assertRaisesRegex(PermissionError, "live_confirmation_mismatch"):
            self.runner.run_acceptance(
                config,
                live=True,
                expected_plan_sha256=plan,
                confirmation="yes",
                provider_builder=provider_builder,
            )
        provider_builder.assert_not_called()

    def test_live_rejects_environment_key_before_provider_construction(self) -> None:
        config = self.config()
        provider_builder = mock.Mock(side_effect=AssertionError("provider built"))
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-not-used"}):
            with self.assertRaisesRegex(PermissionError, "live_key_source_mismatch"):
                self.runner.run_acceptance(
                    config,
                    live=True,
                    expected_plan_sha256=self.runner.plan_sha256(config),
                    confirmation=self.runner.LIVE_CONFIRMATION,
                    provider_builder=provider_builder,
                )
        provider_builder.assert_not_called()

    def test_cli_gate_failure_is_content_free_json_without_temp_directory(self) -> None:
        config = self.config()
        output = io.StringIO()
        with mock.patch.object(
            self.runner.deepseek_provider,
            "DeepSeekProvider",
            side_effect=AssertionError("network provider constructed"),
        ), contextlib.redirect_stdout(output):
            code = self.runner.main(
                [
                    "--execute-live",
                    "--confirm",
                    "wrong",
                    "--expect-plan-sha256",
                    self.runner.plan_sha256(config),
                ]
            )
        report = json.loads(output.getvalue())
        self.assertEqual(1, code)
        self.assertEqual("authorization", report["error_kind"])
        self.assertFalse(report["executed"])
        self.assertEqual([], report["cases"])
        self.assertEqual("not_created", report["temporary_directory"])
        self.assertNotIn(str(Path.home()), output.getvalue())

    def test_parser_has_no_caller_supplied_vault_option(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            self.runner.build_parser().parse_args(["--vault", str(Path.home())])

    def test_private_fixture_and_source_sha_are_stable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="memento-harness-permission-") as raw:
            vault = Path(raw) / "vault"
            vault.mkdir(mode=0o700)
            path = self.runner._write_day(
                vault,
                "2026-08-18",
                [("09:20", self.runner.POSITIVE_QUOTE)],
            )
            before = self.runner._source_digest(vault)
            (vault / ".unrelated-sidecar").write_text("derived", encoding="utf-8")
            after = self.runner._source_digest(vault)

            self.assertEqual(0o700, stat.S_IMODE(vault.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertTrue(self.runner._private_mode(vault, directory=True))
            self.assertTrue(self.runner._private_mode(path, directory=False))
            self.assertEqual(before, after)

    def test_public_case_report_rejects_raw_key_and_full_home_path(self) -> None:
        base = {
            "case": self.runner.CASE_IDS[0],
            "status": "failed",
            "calls": 0,
            "tokens": 0,
            "cost_usd": 0.0,
            "source_hash_before": "a" * 12,
            "source_hash_after": "a" * 12,
            "error_kind": "quality_gate",
            "failure_stage": "postconditions",
            "failed_checks": ["projection_refs_valid"],
            "agent_diagnostics": [],
        }
        for secret in (
            self.runner.POSITIVE_QUOTE,
            "sk-live-secret",
            str(Path.home()),
            "eref_0123456789abcdef",
            "/tmp/memento-private-output.json",
        ):
            unsafe = dict(base)
            unsafe["source_hash_after"] = secret
            with self.assertRaises(self.runner.core.ContractError):
                self.runner._assert_public_case(unsafe)

    def test_first_provider_failure_stops_without_case_retry(self) -> None:
        config = self.config()
        providers: list[FailingProvider] = []

        def build(kind: str):
            self.assertEqual("cognitive", kind)
            provider = FailingProvider()
            providers.append(provider)
            return provider

        with mock.patch.dict(os.environ, {}, clear=True):
            report = self.runner.run_acceptance(
                config,
                live=True,
                expected_plan_sha256=self.runner.plan_sha256(config),
                confirmation=self.runner.LIVE_CONFIRMATION,
                provider_builder=build,
            )

        self.assertEqual("stopped", report["status"])
        self.assertEqual(1, len(report["cases"]))
        self.assertEqual(self.runner.CASE_IDS[0], report["cases"][0]["case"])
        self.assertEqual("failed", report["cases"][0]["status"])
        self.assertEqual("record_worker", report["cases"][0]["failure_stage"])
        self.assertEqual([], report["cases"][0]["failed_checks"])
        self.assertEqual(1, report["cases"][0]["calls"])
        self.assertEqual(1, sum(provider.calls for provider in providers))
        self.assertEqual("cleaned", report["temporary_directory"])

    def test_fake_full_chain_covers_replay_original_only_and_redaction(self) -> None:
        config = self.config()
        seen_vaults: list[Path] = []
        original = self.runner.CaseRuntime

        def construct(vault, meter, accepted_config, provider_builder):
            seen_vaults.append(vault)
            self.assertFalse(vault.is_symlink())
            self.assertEqual(0o700, stat.S_IMODE(vault.stat().st_mode))
            self.assertNotEqual(Path.home(), vault)
            return original(vault, meter, accepted_config, provider_builder)

        with mock.patch.object(
            self.runner.deepseek_provider,
            "DeepSeekProvider",
            side_effect=AssertionError("network provider constructed"),
        ), mock.patch.object(
            self.runner.context_agent,
            "_provider",
            side_effect=AssertionError("action worker provider constructed"),
        ), mock.patch.object(self.runner, "CaseRuntime", side_effect=construct):
            report = self.runner.run_acceptance(
                config,
                live=False,
                expected_plan_sha256=self.runner.plan_sha256(config),
                confirmation=None,
            )

        self.assertTrue(report["all_passed"])
        self.assertEqual(list(self.runner.CASE_IDS), [row["case"] for row in report["cases"]])
        first, second = report["cases"]
        self.assertEqual("passed", first["status"])
        self.assertEqual("passed", second["status"])
        self.assertEqual("none", first["failure_stage"])
        self.assertEqual([], first["failed_checks"])
        self.assertEqual(
            ["2026-08-17", "2026-08-18"],
            [row["local_date"] for row in first["agent_diagnostics"]],
        )
        self.assertEqual(
            ["insufficient_evidence", "updated"],
            [row["response_status"] for row in first["agent_diagnostics"]],
        )
        first_day, second_day = first["agent_diagnostics"]
        self.assertEqual("unavailable", first_day["stable_identity_status"])
        self.assertIsNone(first_day["eligible_evidence_ref_count"])
        self.assertIsNone(first_day["candidate_date_count"])
        self.assertIsNone(first_day["structure_ready"])
        self.assertIsNone(first_day["missing_requirement_codes"])
        self.assertEqual("stable", second_day["stable_identity_status"])
        self.assertEqual(2, second_day["eligible_evidence_ref_count"])
        self.assertEqual(2, second_day["candidate_date_count"])
        self.assertIs(second_day["structure_ready"], True)
        self.assertEqual([], second_day["missing_requirement_codes"])
        self.assertEqual(
            1,
            first["agent_diagnostics"][1]["action_counts"]["investigate"],
        )
        self.assertEqual(
            1,
            first["agent_diagnostics"][1]["action_counts"]["finalize_patch"],
        )
        self.assertEqual(first["source_hash_before"], first["source_hash_after"])
        self.assertEqual(second["source_hash_before"], second["source_hash_after"])
        self.assertEqual("cleaned", report["temporary_directory"])
        self.assertTrue(seen_vaults)
        self.assertTrue(all(not path.exists() for path in seen_vaults))

        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(self.runner.POSITIVE_QUOTE, encoded)
        self.assertNotIn(self.runner.NEGATIVE_QUOTE, encoded)
        self.assertNotIn("sk-", encoded)
        self.assertNotIn(str(Path.home()), encoded)
        self.assertIsNone(self.runner.RAW_REFERENCE_RE.search(encoded))
        self.assertIsNone(self.runner.ABSOLUTE_PATH_RE.search(encoded))
        self.assertEqual(self.runner.USAGE_FIELDS, frozenset(report["usage"]))

    def test_uncommitted_diagnostic_does_not_rederive_material(self) -> None:
        response = {
            "status": "insufficient_evidence",
            "trace": {
                "model_turns": 1,
                "tool_calls": 1,
                "history_matches": 0,
                "actions": ["finish"],
            },
            "error_kind": None,
            "memory": None,
        }
        with mock.patch.object(
            self.runner.agent_v1,
            "derive_stable_new_identity",
            side_effect=AssertionError("must not rematerialize"),
        ):
            diagnostic = self.runner._agent_diagnostic("2026-08-18", response)
        self.assertEqual("unavailable", diagnostic["stable_identity_status"])
        self.assertIsNone(diagnostic["structure_ready"])
        self.assertIsNone(diagnostic["missing_requirement_codes"])

    def test_bundle_observer_emits_only_finite_metadata(self) -> None:
        raw_ref = "eref_0123456789abcdef"
        bundle = {
            "workflow_phase": "evidence_materialized",
            "stable_new_identity": {
                "status": "stable",
                "required_statement": self.runner.POSITIVE_QUOTE,
                "required_scope": "产品方案评审",
                "eligible_evidence_refs": [raw_ref, "eref_fedcba9876543210"],
            },
            "evidence_catalog": [
                {
                    "ref_id": raw_ref,
                    "file": "2026-08-17.md",
                    "line": 9,
                    "quote": self.runner.POSITIVE_QUOTE,
                    "origins": ["history_search"],
                },
                {
                    "ref_id": "eref_fedcba9876543210",
                    "file": "2026-08-18.md",
                    "line": 9,
                    "quote": self.runner.POSITIVE_QUOTE,
                    "origins": ["recent_candidate"],
                },
            ],
            "evidence_ready": True,
            "missing_requirements": [],
        }
        observed = self.runner._observe_agent_material(
            [{"role": "user", "content": json.dumps(bundle, ensure_ascii=False)}]
        )
        self.assertEqual(
            {
                "stable_identity_status": "stable",
                "eligible_evidence_ref_count": 2,
                "candidate_date_count": 2,
                "structure_ready": True,
                "missing_requirement_codes": [],
            },
            observed,
        )
        encoded = json.dumps(observed, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(self.runner.POSITIVE_QUOTE, encoded)
        self.assertNotIn(raw_ref, encoded)
        self.assertNotIn("2026-08-17.md", encoded)


if __name__ == "__main__":
    unittest.main()
