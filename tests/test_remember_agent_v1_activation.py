#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "context-agent"
CLI = AGENT_DIR / "context_agent.py"
sys.path.insert(0, str(AGENT_DIR))

import agent_v1  # noqa: E402
import context_agent  # noqa: E402
from agent_v1 import (  # noqa: E402
    AGENT_V1_GATE_CONTENT,
    create_agent_request,
    disable_agent_v1,
    enable_agent_v1,
    inspect_agent_v1_gate,
    require_agent_v1_enabled,
    response_path,
    run_path,
    make_run_id,
)
from core import ContractError  # noqa: E402


ENABLE_CONFIRMATION = "enable-remember-agent-v1"
DISABLE_CONFIRMATION = "disable-remember-agent-v1"


class RememberAgentV1ActivationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="remember-agent-v1-activation-"
        )
        self.vault = Path(self.temporary.name)
        self.gate = self.vault / ".context-agent" / "agent-v1" / "enabled"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_gate_lifecycle_is_exact_secure_and_idempotent(self) -> None:
        status = inspect_agent_v1_gate(self.vault)
        self.assertEqual(status["state"], "disabled")
        self.assertFalse(status["enabled"])
        with self.assertRaises(ContractError) as caught:
            require_agent_v1_enabled(self.vault)
        self.assertEqual(caught.exception.kind, "disabled")

        enabled = enable_agent_v1(self.vault)
        self.assertTrue(enabled["enabled"])
        self.assertTrue(enabled["changed"])
        details = self.gate.lstat()
        self.assertTrue(stat.S_ISREG(details.st_mode))
        self.assertEqual(details.st_uid, os.getuid())
        self.assertEqual(stat.S_IMODE(details.st_mode), 0o600)
        self.assertEqual(details.st_nlink, 1)
        self.assertEqual(self.gate.read_bytes(), AGENT_V1_GATE_CONTENT)
        self.assertFalse(enable_agent_v1(self.vault)["changed"])
        self.assertEqual(require_agent_v1_enabled(self.vault)["state"], "enabled")

        disabled = disable_agent_v1(self.vault)
        self.assertEqual(disabled["state"], "disabled")
        self.assertTrue(disabled["changed"])
        self.assertFalse(self.gate.exists())
        self.assertFalse(disable_agent_v1(self.vault)["changed"])

    def test_invalid_existing_gate_is_never_overwritten_or_removed(self) -> None:
        enable_agent_v1(self.vault)
        self.gate.write_bytes(b"enabled-v1")
        before = self.gate.read_bytes()
        status = inspect_agent_v1_gate(self.vault)
        self.assertEqual(status["state"], "invalid")
        self.assertEqual(status["reason"], "wrong_content")
        with self.assertRaises(ContractError) as enable_error:
            enable_agent_v1(self.vault)
        self.assertEqual(enable_error.exception.kind, "evidence")
        with self.assertRaises(ContractError) as disable_error:
            disable_agent_v1(self.vault)
        self.assertEqual(disable_error.exception.kind, "evidence")
        self.assertEqual(self.gate.read_bytes(), before)

    def test_gate_rejects_mode_owner_hardlink_symlink_and_non_regular(self) -> None:
        enable_agent_v1(self.vault)
        self.gate.chmod(0o644)
        self.assertEqual(inspect_agent_v1_gate(self.vault)["reason"], "wrong_mode")
        self.gate.chmod(0o600)

        with mock.patch.object(agent_v1.os, "getuid", return_value=os.getuid() + 1):
            self.assertEqual(
                inspect_agent_v1_gate(self.vault)["reason"], "wrong_owner"
            )

        self.gate.unlink()
        hardlink_source = self.vault / "gate-hardlink-source"
        hardlink_source.write_bytes(AGENT_V1_GATE_CONTENT)
        hardlink_source.chmod(0o600)
        os.link(hardlink_source, self.gate)
        self.assertEqual(
            inspect_agent_v1_gate(self.vault)["reason"], "wrong_link_count"
        )

        self.gate.unlink()
        hardlink_source.unlink()
        symlink_target = self.vault / "gate-symlink-target"
        symlink_target.write_bytes(AGENT_V1_GATE_CONTENT)
        symlink_target.chmod(0o600)
        self.gate.symlink_to(symlink_target)
        symlink_status = inspect_agent_v1_gate(self.vault)
        self.assertEqual(symlink_status["state"], "invalid")
        self.assertEqual(symlink_status["reason"], "symlink")

        self.gate.unlink()
        self.gate.mkdir()
        directory_status = inspect_agent_v1_gate(self.vault)
        self.assertEqual(directory_status["state"], "invalid")
        self.assertEqual(directory_status["reason"], "not_regular")

    def test_unsafe_parent_is_invalid_and_enable_refuses_it(self) -> None:
        context_root = self.vault / ".context-agent"
        context_root.mkdir(mode=0o700)
        outside = self.vault / "outside-agent-root"
        outside.mkdir()
        (context_root / "agent-v1").symlink_to(outside, target_is_directory=True)
        status = inspect_agent_v1_gate(self.vault)
        self.assertEqual(status["state"], "invalid")
        self.assertEqual(status["reason"], "unsafe_parent")
        with self.assertRaises(ContractError):
            enable_agent_v1(self.vault)
        self.assertEqual(list(outside.iterdir()), [])

    def test_cli_requires_exact_confirmation(self) -> None:
        rejected = self.cli(
            "agent-enable",
            "--vault",
            str(self.vault),
            "--confirm",
            "yes",
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(json.loads(rejected.stderr)["error_kind"], "authorization")
        self.assertFalse(self.gate.exists())

        enabled = self.cli(
            "agent-enable",
            "--vault",
            str(self.vault),
            "--confirm",
            ENABLE_CONFIRMATION,
        )
        self.assertEqual(enabled.returncode, 0, enabled.stderr)
        self.assertTrue(json.loads(enabled.stdout)["enabled"])

        rejected_disable = self.cli(
            "agent-disable",
            "--vault",
            str(self.vault),
            "--confirm",
            "yes",
        )
        self.assertEqual(rejected_disable.returncode, 2)
        self.assertTrue(self.gate.is_file())
        disabled = self.cli(
            "agent-disable",
            "--vault",
            str(self.vault),
            "--confirm",
            DISABLE_CONFIRMATION,
        )
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        self.assertFalse(self.gate.exists())

    def test_cli_request_run_and_worker_fail_closed_but_profile_is_allowed(self) -> None:
        request_id = "arq_" + "a" * 24
        blocked_request = self.cli(
            "agent-request",
            "--vault",
            str(self.vault),
            "--as-of",
            "2026-08-14",
            "--request-id",
            request_id,
        )
        self.assertEqual(blocked_request.returncode, 2)
        self.assertEqual(json.loads(blocked_request.stderr)["error_kind"], "disabled")
        self.assertFalse(
            (self.vault / ".context-agent" / "agent-v1" / "requests").exists()
        )

        # Direct core primitives remain available to offline evaluation.  The
        # public run/worker commands still refuse to start without the gate.
        request, _ = create_agent_request(
            self.vault,
            as_of="2026-08-14",
            request_id=request_id,
            created_at="2026-08-14T10:00:00+08:00",
        )
        steps = self.vault / "steps.json"
        steps.write_text(
            json.dumps(
                [
                    {
                        "schema_version": "1.0",
                        "action": "finish",
                        "reason_code": "no_material_change",
                        "arguments": {"reason": "no_change"},
                    }
                ]
            ),
            encoding="utf-8",
        )
        blocked_run = self.cli(
            "agent-run",
            "--vault",
            str(self.vault),
            "--request",
            request["id"],
            "--mock-steps",
            str(steps),
        )
        self.assertEqual(blocked_run.returncode, 2)
        self.assertEqual(json.loads(blocked_run.stderr)["error_kind"], "disabled")
        self.assertFalse(response_path(self.vault, request["id"]).exists())
        self.assertFalse(run_path(self.vault, make_run_id(request["id"])).exists())

        blocked_worker = self.cli(
            "agent-worker",
            "--vault",
            str(self.vault),
            "--once",
            "--mock-steps",
            str(steps),
        )
        self.assertEqual(blocked_worker.returncode, 2)
        self.assertFalse(response_path(self.vault, request["id"]).exists())

        profile = self.cli("agent-profile", "--vault", str(self.vault))
        self.assertEqual(profile.returncode, 0, profile.stderr)
        self.assertEqual(json.loads(profile.stdout)["kind"], "remember_agent_profile")
        self.assertEqual(inspect_agent_v1_gate(self.vault)["state"], "disabled")

    def test_provider_factory_is_not_reached_while_disabled(self) -> None:
        args = argparse.Namespace(vault=str(self.vault), mock_steps=None)
        with mock.patch.object(
            context_agent,
            "_provider",
            side_effect=AssertionError("provider factory must not run"),
        ) as provider:
            with self.assertRaises(ContractError) as caught:
                context_agent._process_agent_reference(
                    args,
                    "arq_" + "b" * 24,
                    mock_steps=None,
                )
        self.assertEqual(caught.exception.kind, "disabled")
        provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
