#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import datetime as dt
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
from agent_v1 import (  # noqa: E402
    MockPlanner,
    agent_schedule_path,
    build_agent_messages,
    create_agent_request,
    disable_agent_schedule,
    enable_agent_schedule,
    enable_agent_v1,
    inspect_agent_schedule,
    load_agent_request,
    prepare_agent_run,
    process_agent_request,
    scheduled_agent_request_id,
    tick_agent_schedule,
    validate_agent_request,
)
from core import ContractError, Pricing  # noqa: E402


class RememberAgentScheduleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="remember-agent-schedule-"
        )
        self.vault = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def local_time(self, hour: int, minute: int = 0) -> dt.datetime:
        return dt.datetime(
            2026,
            8,
            16,
            hour,
            minute,
            tzinfo=dt.timezone(dt.timedelta(hours=8)),
        )

    def enable_all(self) -> None:
        enable_agent_v1(self.vault)
        enable_agent_schedule(
            self.vault, updated_at="2026-08-16T12:00:00+08:00"
        )

    def test_absence_enable_disable_and_exact_contract(self) -> None:
        absent = inspect_agent_schedule(self.vault)
        self.assertEqual(absent["state"], "disabled")
        self.assertEqual(absent["reason"], "missing")
        self.assertFalse(agent_schedule_path(self.vault).exists())

        enabled = enable_agent_schedule(
            self.vault, updated_at="2026-08-16T12:00:00+08:00"
        )
        self.assertTrue(enabled["enabled"])
        self.assertTrue(enabled["changed"])
        schedule = enabled["schedule"]
        self.assertEqual(
            set(schedule),
            {
                "schema_version",
                "kind",
                "enabled",
                "cadence",
                "hour",
                "minute",
                "updated_at",
            },
        )
        self.assertEqual(schedule["cadence"], "daily")
        self.assertEqual((schedule["hour"], schedule["minute"]), (21, 0))
        details = agent_schedule_path(self.vault).lstat()
        self.assertTrue(stat.S_ISREG(details.st_mode))
        self.assertEqual(details.st_uid, os.getuid())
        self.assertEqual(details.st_nlink, 1)
        self.assertFalse(stat.S_IMODE(details.st_mode) & 0o022)
        self.assertFalse(enable_agent_schedule(self.vault)["changed"])

        disabled = disable_agent_schedule(
            self.vault, updated_at="2026-08-16T13:00:00+08:00"
        )
        self.assertEqual(disabled["state"], "disabled")
        self.assertEqual(disabled["reason"], "valid")
        self.assertFalse(disabled["schedule"]["enabled"])
        self.assertFalse(disable_agent_schedule(self.vault)["changed"])

    def test_invalid_schedule_fails_closed(self) -> None:
        enable_agent_schedule(self.vault)
        path = agent_schedule_path(self.vault)
        path.chmod(0o622)
        report = inspect_agent_schedule(self.vault)
        self.assertEqual(report["state"], "invalid")
        self.assertEqual(report["reason"], "group_or_world_writable")
        with self.assertRaises(ContractError):
            enable_agent_schedule(self.vault)

        path.chmod(0o600)
        path.unlink()
        target = self.vault / "schedule-target.json"
        target.write_text("{}\n", encoding="utf-8")
        path.symlink_to(target)
        self.assertEqual(inspect_agent_schedule(self.vault)["reason"], "symlink")

    def test_due_tick_is_deterministic_and_same_day_idempotent(self) -> None:
        self.enable_all()
        early = tick_agent_schedule(self.vault, now=self.local_time(20, 59))
        self.assertEqual(early["status"], "not_due")

        created = tick_agent_schedule(self.vault, now=self.local_time(21))
        self.assertEqual(created["status"], "created")
        request = created["request"]
        self.assertEqual(request["trigger"], "scheduled")
        self.assertEqual(request["as_of"], "2026-08-16")
        self.assertEqual(
            request["id"], scheduled_agent_request_id("2026-08-16")
        )

        repeated = tick_agent_schedule(self.vault, now=self.local_time(23, 30))
        self.assertEqual(repeated["status"], "already_exists")
        self.assertEqual(repeated["request"]["id"], request["id"])
        request_files = list(
            (self.vault / ".context-agent" / "agent-v1" / "requests").glob(
                "*.json"
            )
        )
        self.assertEqual(len(request_files), 1)

    def test_morning_wake_catches_up_the_most_recent_due_slot(self) -> None:
        enable_agent_v1(self.vault)
        enable_agent_schedule(
            self.vault, updated_at="2026-08-15T10:00:00+08:00"
        )
        wake = tick_agent_schedule(self.vault, now=self.local_time(8))
        self.assertEqual(wake["status"], "created")
        self.assertEqual(wake["local_date"], "2026-08-15")
        self.assertEqual(wake["request"]["as_of"], "2026-08-15")
        self.assertEqual(
            wake["request"]["id"], scheduled_agent_request_id("2026-08-15")
        )

    def test_morning_enable_does_not_backfill_a_pre_enable_slot(self) -> None:
        enable_agent_v1(self.vault)
        enable_agent_schedule(
            self.vault, updated_at="2026-08-16T08:00:00+08:00"
        )
        report = tick_agent_schedule(self.vault, now=self.local_time(8, 1))
        self.assertEqual(report["status"], "not_due")
        self.assertEqual(report["local_date"], "2026-08-15")
        self.assertFalse(
            (self.vault / ".context-agent" / "agent-v1" / "requests").exists()
        )

    def test_pending_manual_request_prevents_scheduled_request(self) -> None:
        self.enable_all()
        manual, _ = create_agent_request(
            self.vault,
            as_of="2026-08-16",
            request_id="arq_" + "a" * 24,
            created_at="2026-08-16T20:55:00+08:00",
        )
        report = tick_agent_schedule(self.vault, now=self.local_time(21))
        self.assertEqual(report["status"], "pending_request")
        self.assertEqual(report["pending_request_id"], manual["id"])
        self.assertFalse(
            (
                self.vault
                / ".context-agent"
                / "agent-v1"
                / "requests"
                / f"{scheduled_agent_request_id('2026-08-16')}.json"
            ).exists()
        )

    def test_master_gate_off_and_schedule_off_do_not_create(self) -> None:
        enable_agent_schedule(self.vault)
        master_off = tick_agent_schedule(self.vault, now=self.local_time(21))
        self.assertEqual(master_off["status"], "master_gate_disabled")

        enable_agent_v1(self.vault)
        disable_agent_schedule(self.vault)
        schedule_off = tick_agent_schedule(self.vault, now=self.local_time(21))
        self.assertEqual(schedule_off["status"], "schedule_disabled")
        self.assertFalse(
            (self.vault / ".context-agent" / "agent-v1" / "requests").exists()
        )

    def test_trigger_validation_and_scheduled_id_binding(self) -> None:
        manual, _ = create_agent_request(
            self.vault,
            as_of="2026-08-16",
            request_id="arq_" + "b" * 24,
            created_at="2026-08-16T10:00:00+08:00",
        )
        self.assertEqual(manual["trigger"], "manual")
        invalid = dict(manual)
        invalid["trigger"] = "timer"
        with self.assertRaises(ContractError):
            validate_agent_request(invalid)
        with self.assertRaises(ContractError) as caught:
            create_agent_request(
                self.vault,
                as_of="2026-08-16",
                request_id="arq_" + "c" * 24,
                trigger="scheduled",
            )
        self.assertEqual(caught.exception.kind, "conflict")

    def test_manual_and_scheduled_requests_have_identical_model_context(self) -> None:
        (self.vault / "2026-08-16.md").write_text(
            "# 2026-08-16\n今天复核了当前方向。\n", encoding="utf-8"
        )
        manual, _ = create_agent_request(
            self.vault,
            as_of="2026-08-16",
            request_id="arq_" + "e" * 24,
            created_at="2026-08-16T21:00:00+08:00",
        )
        scheduled, _ = create_agent_request(
            self.vault,
            as_of="2026-08-16",
            created_at="2026-08-16T21:00:00+08:00",
            trigger="scheduled",
        )
        manual_value, _, manual_sha = load_agent_request(
            self.vault, manual["id"]
        )
        scheduled_value, _, scheduled_sha = load_agent_request(
            self.vault, scheduled["id"]
        )
        manual_preparation = prepare_agent_run(
            self.vault, manual_value, manual_sha, maximum_chars=120_000
        )
        scheduled_preparation = prepare_agent_run(
            self.vault, scheduled_value, scheduled_sha, maximum_chars=120_000
        )
        for workflow_mode in (False, True):
            with self.subTest(workflow_mode=workflow_mode):
                manual_messages = build_agent_messages(
                    manual_preparation, workflow_mode=workflow_mode
                )
                scheduled_messages = build_agent_messages(
                    scheduled_preparation, workflow_mode=workflow_mode
                )
                self.assertEqual(manual_messages, scheduled_messages)
                if workflow_mode:
                    mission = json.loads(manual_messages[1]["content"])["mission"]
                    self.assertEqual(mission["trigger"], "user_authorized")
                else:
                    self.assertIn(
                        '<mission trigger="user_authorized"',
                        manual_messages[1]["content"],
                    )

    def test_policy_binds_both_request_triggers_to_one_model_context(self) -> None:
        captured = []
        canonical_json = agent_v1.canonical_json

        def capture(value):
            captured.append(value)
            return canonical_json(value)

        with mock.patch.object(agent_v1, "canonical_json", side_effect=capture):
            agent_v1.make_agent_policy_sha256(
                provider="mock-agentic-workflow",
                model="fixture",
                budget=agent_v1.AgentBudget(),
            )
        policy = captured[-1]
        self.assertEqual(policy["prompt_version"], "remember-agent-v1.22")
        self.assertEqual(
            policy["authorization"],
            {
                "allowed_request_triggers": ["manual", "scheduled"],
                "model_context_trigger": "user_authorized",
                "window_days": 14,
            },
        )

    def test_corrupt_completed_response_fails_schedule_tick_closed(self) -> None:
        self.enable_all()
        request, _ = create_agent_request(
            self.vault,
            as_of="2026-08-16",
            request_id="arq_" + "a" * 24,
            created_at="2026-08-16T20:55:00+08:00",
        )
        output = agent_v1.response_path(self.vault, request["id"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("{\n", encoding="utf-8")
        with self.assertRaises(ContractError) as caught:
            tick_agent_schedule(self.vault, now=self.local_time(21))
        self.assertEqual(caught.exception.kind, "schema")

    def test_mismatched_completed_response_fails_schedule_tick_closed(self) -> None:
        self.enable_all()
        (self.vault / "2026-08-16.md").write_text(
            "# 2026-08-16\n今天只记录了一条普通想法。\n",
            encoding="utf-8",
        )
        pending, _ = create_agent_request(
            self.vault,
            as_of="2026-08-16",
            request_id="arq_" + "a" * 24,
            created_at="2026-08-16T20:54:00+08:00",
        )
        completed_request, _ = create_agent_request(
            self.vault,
            as_of="2026-08-16",
            request_id="arq_" + "b" * 24,
            created_at="2026-08-16T20:55:00+08:00",
        )
        step = {
            "schema_version": "1.0",
            "action": "finish",
            "reason_code": "no_material_change",
            "arguments": {"reason": "no_change"},
        }
        _, completed_path = process_agent_request(
            self.vault,
            completed_request["id"],
            provider_client=MockPlanner([step]),
            provider_name="mock-agentic-workflow",
            model="fixture",
            pricing=Pricing(),
        )
        mismatched_path = agent_v1.response_path(self.vault, pending["id"])
        mismatched_path.write_text(
            completed_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        with self.assertRaises(ContractError) as caught:
            tick_agent_schedule(self.vault, now=self.local_time(21))
        self.assertEqual(caught.exception.kind, "conflict")

    def test_provider_execution_occurs_inside_global_mission_lock(self) -> None:
        enable_agent_v1(self.vault)
        (self.vault / "2026-08-16.md").write_text(
            "# 2026-08-16\n今天只记录了一条普通想法。\n",
            encoding="utf-8",
        )
        request, _ = create_agent_request(
            self.vault,
            as_of="2026-08-16",
            request_id="arq_" + "d" * 24,
            created_at="2026-08-16T21:00:00+08:00",
        )
        held = {"value": False}

        @contextlib.contextmanager
        def observed_lock(_vault: Path):
            self.assertFalse(held["value"])
            held["value"] = True
            try:
                yield
            finally:
                held["value"] = False

        class AssertingPlanner(MockPlanner):
            def complete(inner_self, messages):
                self.assertTrue(held["value"])
                return super().complete(messages)

        step = {
            "schema_version": "1.0",
            "action": "finish",
            "reason_code": "no_material_change",
            "arguments": {"reason": "no_change"},
        }
        with mock.patch.object(agent_v1, "_mission_lock", observed_lock):
            response, _ = process_agent_request(
                self.vault,
                request["id"],
                provider_client=AssertingPlanner([step]),
                provider_name="mock-agentic-workflow",
                model="fixture",
                pricing=Pricing(),
            )
        self.assertEqual(response["status"], "no_change")
        self.assertFalse(held["value"])

    def test_cli_schedule_confirmation_and_tick_do_not_run_provider(self) -> None:
        rejected = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "agent-schedule-enable",
                "--vault",
                str(self.vault),
                "--confirm",
                "yes",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(json.loads(rejected.stderr)["error_kind"], "authorization")

        enabled = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "agent-schedule-enable",
                "--vault",
                str(self.vault),
                "--confirm",
                "enable-remember-agent-daily-21",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(enabled.returncode, 0, enabled.stderr)
        self.assertTrue(json.loads(enabled.stdout)["enabled"])

        tick = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "agent-schedule-tick",
                "--once",
                "--vault",
                str(self.vault),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(tick.returncode, 0, tick.stderr)
        self.assertEqual(json.loads(tick.stdout)["status"], "master_gate_disabled")


if __name__ == "__main__":
    unittest.main()
