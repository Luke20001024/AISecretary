#!/usr/bin/env python3
"""Security and serialization tests for the shared paid-provider lock."""

from __future__ import annotations

import contextlib
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "context-agent"
sys.path.insert(0, str(AGENT_DIR))

import agent_v1  # noqa: E402
import core  # noqa: E402
from core import (  # noqa: E402
    ContractError,
    provider_call_lock,
    provider_call_lock_path,
)


class ProviderCallLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="provider-call-lock-")
        self.vault = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @property
    def lock_path(self) -> Path:
        return provider_call_lock_path(self.vault)

    def prepare_lock_parent(self) -> None:
        self.lock_path.parent.mkdir(parents=True, mode=0o700)

    def test_creates_owner_only_lock_at_shared_cognitive_path(self) -> None:
        with provider_call_lock(self.vault):
            self.assertTrue(self.lock_path.is_file())
            details = self.lock_path.stat()
            self.assertEqual(details.st_uid, os.getuid())
            self.assertEqual(details.st_nlink, 1)
            self.assertEqual(stat.S_IMODE(details.st_mode) & 0o077, 0)
        self.assertEqual(
            self.lock_path.relative_to(self.vault.resolve()).as_posix(),
            ".context-agent/cognitive-secretary-v1/locks/provider.lock",
        )

    def test_symlink_lock_fails_closed(self) -> None:
        self.prepare_lock_parent()
        target = self.vault / "outside.lock"
        target.write_text("do not lock me", encoding="utf-8")
        self.lock_path.symlink_to(target)
        with self.assertRaises(ContractError) as caught:
            with provider_call_lock(self.vault):
                self.fail("symlink lock must never be acquired")
        self.assertEqual(caught.exception.kind, "evidence")

    def test_hardlink_lock_fails_closed(self) -> None:
        self.prepare_lock_parent()
        target = self.vault / "outside.lock"
        target.write_text("do not lock me", encoding="utf-8")
        target.chmod(0o600)
        os.link(target, self.lock_path)
        self.assertEqual(self.lock_path.stat().st_nlink, 2)
        with self.assertRaises(ContractError) as caught:
            with provider_call_lock(self.vault):
                self.fail("hard-linked lock must never be acquired")
        self.assertEqual(caught.exception.kind, "evidence")

    def test_group_or_world_accessible_lock_fails_closed(self) -> None:
        self.prepare_lock_parent()
        self.lock_path.write_text("unsafe permissions", encoding="utf-8")
        self.lock_path.chmod(0o666)
        with self.assertRaises(ContractError) as caught:
            with provider_call_lock(self.vault):
                self.fail("world-writable lock must never be acquired")
        self.assertEqual(caught.exception.kind, "evidence")

    def test_non_owner_lock_fails_closed(self) -> None:
        with provider_call_lock(self.vault):
            pass
        real_fstat = os.fstat

        def wrong_owner_for_regular_file(descriptor: int) -> os.stat_result:
            details = real_fstat(descriptor)
            if not stat.S_ISREG(details.st_mode):
                return details
            values = list(details)
            values[4] = details.st_uid + 1
            return os.stat_result(values)

        with mock.patch.object(
            core.os, "fstat", side_effect=wrong_owner_for_regular_file
        ):
            with self.assertRaises(ContractError) as caught:
                with provider_call_lock(self.vault):
                    self.fail("non-owner lock must never be acquired")
        self.assertEqual(caught.exception.kind, "evidence")

    def test_two_threads_are_serialized(self) -> None:
        active = 0
        maximum_active = 0
        state_lock = threading.Lock()
        first_entered = threading.Event()
        order: list[str] = []

        def worker(label: str, hold_seconds: float) -> None:
            nonlocal active, maximum_active
            with provider_call_lock(self.vault):
                with state_lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                    order.append(f"{label}:enter")
                    if label == "A":
                        first_entered.set()
                time.sleep(hold_seconds)
                with state_lock:
                    order.append(f"{label}:exit")
                    active -= 1

        first = threading.Thread(target=worker, args=("A", 0.15), daemon=True)
        second = threading.Thread(target=worker, args=("B", 0.01), daemon=True)
        first.start()
        self.assertTrue(first_entered.wait(timeout=2))
        second.start()
        first.join(timeout=3)
        second.join(timeout=3)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(maximum_active, 1)
        self.assertEqual(order, ["A:enter", "A:exit", "B:enter", "B:exit"])

    def test_two_processes_are_serialized(self) -> None:
        log_path = self.vault / "process-order.log"
        script = r'''
import os
import sys
import time
from pathlib import Path
from core import provider_call_lock

vault, log_path, label, hold = sys.argv[1:]
with provider_call_lock(Path(vault)):
    descriptor = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, f"{label}:enter\n".encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    time.sleep(float(hold))
    descriptor = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, f"{label}:exit\n".encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
'''
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(AGENT_DIR)
        first = subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(self.vault),
                str(log_path),
                "A",
                "0.25",
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if log_path.is_file() and "A:enter" in log_path.read_text(encoding="utf-8"):
                break
            if first.poll() is not None:
                break
            time.sleep(0.01)
        self.assertIsNone(first.poll(), first.stderr.read() if first.stderr else "")
        second = subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(self.vault),
                str(log_path),
                "B",
                "0.01",
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        first_stdout, first_stderr = first.communicate(timeout=5)
        second_stdout, second_stderr = second.communicate(timeout=5)
        self.assertEqual(first.returncode, 0, first_stdout + first_stderr)
        self.assertEqual(second.returncode, 0, second_stdout + second_stderr)
        self.assertEqual(
            log_path.read_text(encoding="utf-8").splitlines(),
            ["A:enter", "A:exit", "B:enter", "B:exit"],
        )

    def test_agent_mission_lock_delegates_to_shared_lock(self) -> None:
        held = {"value": False}

        @contextlib.contextmanager
        def observed(vault: Path):
            self.assertEqual(vault, self.vault)
            held["value"] = True
            try:
                yield
            finally:
                held["value"] = False

        with mock.patch.object(agent_v1, "provider_call_lock", observed):
            with agent_v1._mission_lock(self.vault):
                self.assertTrue(held["value"])
        self.assertFalse(held["value"])


if __name__ == "__main__":
    unittest.main()
