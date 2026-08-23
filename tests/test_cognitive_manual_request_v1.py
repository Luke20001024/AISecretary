#!/usr/bin/env python3

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_AGENT = ROOT / "context-agent"
if str(CONTEXT_AGENT) not in sys.path:
    sys.path.insert(0, str(CONTEXT_AGENT))

from agent_v1 import enable_agent_v1  # noqa: E402
from cognitive_manual_request_v1 import (  # noqa: E402
    ManualDayRequest,
    ManualDayRequestStore,
)
from core import ContractError  # noqa: E402


NOW = dt.datetime(2026, 8, 18, 12, 30, tzinfo=dt.timezone(dt.timedelta(hours=8)))


class ManualRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="memento-manual-day-")
        self.vault = Path(self.temporary.name) / "vault"
        self.vault.mkdir(mode=0o700)
        self.store = ManualDayRequestStore(self.vault)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(self, nonce: str = "1", local_date: str = "2026-08-18") -> ManualDayRequest:
        return ManualDayRequest(
            "cman_" + nonce * 24,
            f"{local_date}T12:29:00+08:00",
            local_date,
        )

    def test_valid_request_runs_once_and_exact_replay_is_zero_calls(self) -> None:
        enable_agent_v1(self.vault)
        request, request_path = self.store.create_request(self.request())
        calls: list[tuple[str, str]] = []

        def runner(local_date: str, trigger: str):
            calls.append((local_date, trigger))
            return {"status": "committed"}

        first = self.store.consume(day_runner=runner, now=NOW)
        second = self.store.consume(day_runner=runner, now=NOW)
        self.assertEqual((first.processed, first.completed), (1, 1))
        self.assertEqual((second.processed, second.already_resolved), (0, 1))
        self.assertEqual(calls, [("2026-08-18", "manual")])
        result_files = list(self.store.results_dir.glob("*.json"))
        self.assertEqual(len(result_files), 1)
        self.assertEqual(request_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(result_files[0].stat().st_mode & 0o777, 0o600)
        result = json.loads(result_files[0].read_text(encoding="utf-8"))
        self.assertEqual(result["request_id"], request.id)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(set(result), {
            "schema_version", "kind", "id", "request_id", "request_sha256",
            "completed_at", "local_date", "status", "runner_status", "error_kind",
        })
        self.assertNotIn(str(self.vault), json.dumps(result))

    def test_past_future_and_disabled_gate_never_enter_day_runner(self) -> None:
        self.store.create_request(self.request("2", "2026-08-17"))
        self.store.create_request(self.request("3", "2026-08-19"))
        self.store.create_request(self.request("4"))
        calls = 0

        def runner(local_date: str, trigger: str):
            nonlocal calls
            calls += 1
            return {"status": "committed"}

        report = self.store.consume(day_runner=runner, now=NOW)
        self.assertEqual((calls, report.processed, report.rejected), (0, 3, 3))
        statuses = {
            json.loads(path.read_text(encoding="utf-8"))["status"]
            for path in self.store.results_dir.glob("*.json")
        }
        self.assertEqual(statuses, {"rejected_date", "master_gate_disabled"})

    def test_immutable_conflict_and_file_safety_fail_closed(self) -> None:
        _, path = self.store.create_request(self.request("5"))
        with self.assertRaises(ContractError) as conflict:
            self.store.create_request(
                ManualDayRequest(
                    "cman_" + "5" * 24,
                    "2026-08-18T12:28:00+08:00",
                    "2026-08-18",
                )
            )
        self.assertEqual(conflict.exception.kind, "conflict")

        path.chmod(0o644)
        with self.assertRaises(ContractError) as permissions:
            self.store.consume(day_runner=lambda *_: {"status": "committed"}, now=NOW)
        self.assertEqual(permissions.exception.kind, "evidence")

        path.chmod(0o600)
        hardlink = self.store.requests_dir / ("cman_" + "6" * 24 + ".json")
        os.link(path, hardlink)
        with self.assertRaises(ContractError) as linked:
            self.store.consume(day_runner=lambda *_: {"status": "committed"}, now=NOW)
        self.assertEqual(linked.exception.kind, "evidence")

    def test_symlink_and_tamper_fail_closed_without_calls(self) -> None:
        self.store._ensure_layout()
        target = Path(self.temporary.name) / "outside.json"
        target.write_text("{}", encoding="utf-8")
        target.chmod(0o600)
        link = self.store.requests_dir / ("cman_" + "7" * 24 + ".json")
        link.symlink_to(target)
        calls = 0

        def runner(*_):
            nonlocal calls
            calls += 1
            return {"status": "committed"}

        with self.assertRaises(ContractError):
            self.store.consume(day_runner=runner, now=NOW)
        self.assertEqual(calls, 0)

    def test_tampered_request_bytes_fail_closed_without_calls(self) -> None:
        self.store._ensure_layout()
        path = self.store.requests_dir / ("cman_" + "b" * 24 + ".json")
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "kind": "memento_cognitive_manual_day_request",
                    "id": "cman_" + "b" * 24,
                    "created_at": "2026-08-18T12:29:00+08:00",
                    "local_date": "2026-08-18",
                    "status": "completed",
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
        calls = 0

        def runner(*_):
            nonlocal calls
            calls += 1
            return {"status": "committed"}

        with self.assertRaises(ContractError) as raised:
            self.store.consume(day_runner=runner, now=NOW)
        self.assertEqual((raised.exception.kind, calls), ("evidence", 0))

    def test_process_crash_before_result_can_replay_same_request(self) -> None:
        enable_agent_v1(self.vault)
        request = self.request("8")
        self.store.create_request(request)
        runner_attempts = 0
        provider_calls = 0
        committed_material = self.vault / ".fake-day-material"

        def crash(local_date: str, trigger: str):
            nonlocal runner_attempts, provider_calls
            runner_attempts += 1
            provider_calls += 1
            committed_material.write_text("committed", encoding="utf-8")
            raise SystemExit(9)

        with self.assertRaises(SystemExit):
            self.store.consume(day_runner=crash, now=NOW)
        self.assertEqual(list(self.store.results_dir.glob("*.json")), [])

        def recovered(local_date: str, trigger: str):
            nonlocal runner_attempts, provider_calls
            runner_attempts += 1
            if not committed_material.exists():
                provider_calls += 1
            return {"status": "no_change"}

        report = self.store.consume(day_runner=recovered, now=NOW)
        self.assertEqual((report.completed, runner_attempts, provider_calls), (1, 2, 1))

    def test_concurrent_consumers_serialize_one_request(self) -> None:
        enable_agent_v1(self.vault)
        self.store.create_request(self.request("9"))
        calls = 0
        calls_lock = threading.Lock()

        def runner(local_date: str, trigger: str):
            nonlocal calls
            with calls_lock:
                calls += 1
            return {"status": "committed"}

        with ThreadPoolExecutor(max_workers=2) as pool:
            reports = list(pool.map(lambda _: self.store.consume(day_runner=runner, now=NOW), range(2)))
        self.assertEqual(calls, 1)
        self.assertEqual(sum(report.processed for report in reports), 1)
        self.assertEqual(sum(report.already_resolved for report in reports), 1)


if __name__ == "__main__":
    unittest.main()
