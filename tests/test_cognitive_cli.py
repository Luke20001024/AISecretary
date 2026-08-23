#!/usr/bin/env python3
"""CLI contract tests for the Cognitive Secretary composition root."""

from __future__ import annotations

import io
import datetime as dt
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_AGENT = ROOT / "context-agent"
if str(CONTEXT_AGENT) not in sys.path:
    sys.path.insert(0, str(CONTEXT_AGENT))

import context_agent as cli  # noqa: E402
from agent_v1 import enable_agent_schedule, enable_agent_v1  # noqa: E402
from cognitive_actions_v1 import CognitiveActionStore  # noqa: E402
from cognitive_manual_request_v1 import ManualDayRequest, ManualDayRequestStore  # noqa: E402
from cognitive_store_v1 import RecordStore  # noqa: E402
from cognitive_v1 import (  # noqa: E402
    COGNITIVE_SCHEMA_VERSION,
    CognitiveUserAction,
    make_cognitive_action_id,
    make_receipt_id,
)
from deepseek_provider import CompletionResult  # noqa: E402


LOCAL_DATE = "2026-08-18"
RAW_SECRET = "RAW_SENTINEL_do_not_print"
MODEL_SECRET = "MODEL_SENTINEL_do_not_print"
USAGE = {
    "prompt_tokens": 10,
    "completion_tokens": 5,
    "total_tokens": 15,
    "prompt_cache_hit_tokens": 0,
    "prompt_cache_miss_tokens": 10,
    "completion_tokens_details": {"reasoning_tokens": 0},
}


class FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self, messages: Sequence[Mapping[str, str]]
    ) -> CompletionResult:
        self.calls += 1
        payload = json.loads(messages[-1]["content"])
        evidence_ref = payload["untrusted_data"]["source_catalog"][0]["ref_id"]
        action = {
            "schema_version": "1.0",
            "action": "propose_receipt",
            "reason_code": "interpretation_ready",
            "arguments": {
                "summary": MODEL_SECRET,
                "facets": {
                    "content_types": ["observation"],
                    "topics": ["产品设计"],
                    "objects": ["方案评审"],
                    "stance": "self_observation",
                    "cognitive_state": "repeated",
                    "purposes": ["future_decision"],
                },
                "memory_candidates": [],
                "relation_candidates": [],
                "source_ref_ids": [evidence_ref],
            },
        }
        return CompletionResult(
            content=json.dumps(action, ensure_ascii=False),
            usage=USAGE,
            request_id=f"fake-{self.calls}",
            model="deepseek-v4-pro",
        )


class FinishProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self, messages: Sequence[Mapping[str, str]]
    ) -> CompletionResult:
        del messages
        self.calls += 1
        return CompletionResult(
            content=json.dumps(
                {
                    "schema_version": "1.0",
                    "action": "finish",
                    "reason_code": "insufficient_signal",
                    "arguments": {"reason": "insufficient_signal"},
                },
                ensure_ascii=False,
            ),
            usage=USAGE,
            request_id=f"finish-{self.calls}",
            model="deepseek-v4-pro",
        )


class ReceiptThenFinishProvider:
    """Return one current receipt, then a valid no-change Daily terminal."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self, messages: Sequence[Mapping[str, str]]
    ) -> CompletionResult:
        self.calls += 1
        if self.calls == 1:
            payload = json.loads(messages[-1]["content"])
            evidence_ref = payload["untrusted_data"]["source_catalog"][0]["ref_id"]
            action = {
                "schema_version": "1.0",
                "action": "propose_receipt",
                "reason_code": "interpretation_ready",
                "arguments": {
                    "summary": MODEL_SECRET,
                    "facets": {
                        "content_types": ["observation"],
                        "topics": ["产品设计"],
                        "objects": ["方案评审"],
                        "stance": "self_observation",
                        "cognitive_state": "repeated",
                        "purposes": ["future_decision"],
                    },
                    "memory_candidates": [],
                    "relation_candidates": [],
                    "source_ref_ids": [evidence_ref],
                },
            }
        elif self.calls == 2:
            action = {
                "schema_version": "1.0",
                "action": "finish",
                "reason_code": "no_change",
                "arguments": {"reason": "no_change"},
            }
        else:
            raise AssertionError("unexpected Provider call")
        return CompletionResult(
            content=json.dumps(action, ensure_ascii=False),
            usage=USAGE,
            request_id=f"receipt-finish-{self.calls}",
            model="deepseek-v4-pro",
        )


class CognitiveCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="memento-cognitive-cli-")
        self.vault = Path(self.temporary.name) / "vault"
        self.vault.mkdir(mode=0o700)
        # This suite models a fixed historical day.  Commands that omit
        # ``--date`` must remain deterministic when the calendar advances.
        original_local_date = cli._local_date
        self._local_date_patch = mock.patch.object(
            cli,
            "_local_date",
            side_effect=lambda value=None: (
                LOCAL_DATE if value is None else original_local_date(value)
            ),
        )
        self._local_date_patch.start()
        self.addCleanup(self._local_date_patch.stop)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_day(self) -> None:
        (self.vault / f"{LOCAL_DATE}.md").write_text(
            "---\ndate: 2026-08-18\ntype: memento-daily\n---\n\n"
            "## 09:10 · 周二 · Chrome\n\n"
            f"{RAW_SECRET}\n\n---\n",
            encoding="utf-8",
        )

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_runtime_reuses_provider_factory_with_frozen_token_policies(self) -> None:
        calls: list[int] = []

        def factory(args: Any, model: str | None = None, *, max_tokens: int = 1200):
            calls.append(max_tokens)
            return FakeProvider()

        args = cli.build_parser().parse_args(
            [
                "record-worker",
                "--once",
                "--vault",
                str(self.vault),
                "--date",
                LOCAL_DATE,
            ]
        )
        with mock.patch.object(cli, "_provider", side_effect=factory):
            runtime = cli._cognitive_runtime(args, self.vault)
        self.assertEqual(calls, [2400, 3600])
        self.assertEqual(runtime.record_max_tokens, 2400)
        self.assertEqual(runtime.daily_max_tokens, 3600)

    def test_record_ingest_and_worker_emit_only_bounded_aggregates(self) -> None:
        self.write_day()
        code, stdout, stderr = self.invoke(
            "record-ingest",
            "--vault",
            str(self.vault),
            "--source",
            f"{LOCAL_DATE}.md",
        )
        self.assertEqual((code, stderr), (0, ""))
        ingest = json.loads(stdout)
        self.assertEqual(ingest["status"], "changed")
        self.assertEqual(ingest["created_count"], 1)
        self.assertNotIn(RAW_SECRET, stdout)

        enable_agent_v1(self.vault)
        provider = FakeProvider()
        with mock.patch.object(cli, "_provider", return_value=provider):
            code, stdout, stderr = self.invoke(
                "record-worker",
                "--once",
                "--vault",
                str(self.vault),
                "--date",
                LOCAL_DATE,
            )
        self.assertEqual((code, stderr), (0, ""))
        report = json.loads(stdout)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["selected_count"], 1)
        self.assertEqual(report["outcomes"], {"ready": 1})
        self.assertEqual(provider.calls, 1)
        self.assertNotIn(RAW_SECRET, stdout)
        self.assertNotIn(MODEL_SECRET, stdout)
        self.assertNotIn("record_id", stdout)

    def test_daily_run_is_unified_and_sanitized_for_empty_day(self) -> None:
        enable_agent_v1(self.vault)
        provider = FakeProvider()
        with mock.patch.object(cli, "_provider", return_value=provider):
            code, stdout, stderr = self.invoke(
                "daily-run",
                "--once",
                "--vault",
                str(self.vault),
                "--date",
                LOCAL_DATE,
                "--trigger",
                "manual",
            )
        self.assertEqual((code, stderr), (0, ""))
        report = json.loads(stdout)
        self.assertEqual(report["kind"], "memento_cognitive_day_result")
        self.assertEqual(report["local_date"], LOCAL_DATE)
        self.assertEqual(report["record_count"], 0)
        self.assertEqual(provider.calls, 0)
        self.assertNotIn("bundle_ref", report)
        self.assertNotIn("sha256", stdout)

    def test_schedule_alias_is_path_free_and_tick_is_gate_first(self) -> None:
        code, stdout, stderr = self.invoke(
            "agent-schedule-enable",
            "--vault",
            str(self.vault),
            "--confirm",
            "enable-remember-agent-daily-21",
        )
        self.assertEqual((code, stderr), (0, ""))
        enabled = json.loads(stdout)
        self.assertTrue(enabled["enabled"])
        self.assertNotIn("path", enabled)
        with mock.patch.object(cli, "_provider", side_effect=AssertionError("provider called")):
            code, stdout, stderr = self.invoke(
                "agent-schedule-tick",
                "--once",
                "--vault",
                str(self.vault),
            )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["status"], "master_gate_disabled")
        requests = self.vault / ".context-agent" / "agent-v1" / "requests"
        self.assertFalse(requests.exists())

    def test_projection_and_migration_commands_are_model_free(self) -> None:
        self.write_day()
        with mock.patch.object(cli, "_provider", side_effect=AssertionError("provider called")):
            code, stdout, stderr = self.invoke(
                "cognitive-migration-backfill",
                "--vault",
                str(self.vault),
            )
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(json.loads(stdout)["created_count"], 1)

            code, stdout, stderr = self.invoke(
                "projection-rebuild",
                "--vault",
                str(self.vault),
                "--date",
                LOCAL_DATE,
            )
        self.assertEqual((code, stderr), (0, ""))
        projection = json.loads(stdout)
        self.assertEqual(projection["status"], "completed")
        self.assertNotIn(RAW_SECRET, stdout)
        self.assertNotIn("records", projection)

    def test_no_candidate_survives_rebuild_action_and_08_inspection(self) -> None:
        self.write_day()
        enable_agent_v1(self.vault)
        provider = FinishProvider()
        with mock.patch.object(cli, "_provider", return_value=provider):
            code, stdout, stderr = self.invoke(
                "daily-run",
                "--once",
                "--vault",
                str(self.vault),
                "--date",
                LOCAL_DATE,
                "--trigger",
                "manual",
            )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["status"], "no_candidate")
        self.assertEqual(provider.calls, 1)

        records = RecordStore(self.vault)

        def assert_no_candidate_home() -> None:
            home = json.loads(
                (records.root / "projections" / "home_projection.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(home["records"][0]["status"], "no_candidate")
            self.assertIsNone(home["records"][0]["receipt_ref"])
            self.assertIsNone(home["records"][0]["summary"])
            self.assertEqual(home["today_status"]["daily_run_status"], "no_candidate")
            self.assertEqual(home["schedule"]["last_run_status"], "no_candidate")

        assert_no_candidate_home()
        enable_agent_schedule(
            self.vault,
            updated_at="2026-08-18T12:00:00+08:00",
        )
        recovery_now = dt.datetime(
            2026,
            8,
            19,
            8,
            tzinfo=dt.timezone(dt.timedelta(hours=8)),
        )
        with mock.patch.object(
            cli,
            "_provider",
            side_effect=AssertionError("provider called"),
        ), mock.patch(
            "cognitive_schedule_v1._local_now",
            return_value=recovery_now,
        ):
            code, stdout, stderr = self.invoke(
                "daily-schedule-tick",
                "--once",
                "--vault",
                str(self.vault),
            )
            self.assertEqual((code, stderr), (0, ""))
            schedule = json.loads(stdout)
            self.assertEqual(schedule["status"], "not_due")
            self.assertEqual(schedule["trigger"], "recovery")
            self.assertEqual(schedule["local_date"], LOCAL_DATE)

            code, _, stderr = self.invoke(
                "projection-rebuild",
                "--vault",
                str(self.vault),
                "--date",
                LOCAL_DATE,
            )
            self.assertEqual((code, stderr), (0, ""))
            assert_no_candidate_home()

            code, _, stderr = self.invoke(
                "cognitive-action-worker",
                "--once",
                "--vault",
                str(self.vault),
            )
            self.assertEqual((code, stderr), (0, ""))
            assert_no_candidate_home()
        self.assertEqual(provider.calls, 1)

    def test_daily_no_change_is_terminal_for_production_08_inspection(self) -> None:
        self.write_day()
        enable_agent_v1(self.vault)
        provider = ReceiptThenFinishProvider()
        with mock.patch.object(cli, "_provider", return_value=provider):
            code, stdout, stderr = self.invoke(
                "daily-run",
                "--once",
                "--vault",
                str(self.vault),
                "--date",
                LOCAL_DATE,
                "--trigger",
                "manual",
            )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["status"], "no_change")
        self.assertEqual(provider.calls, 2)

        records = RecordStore(self.vault)
        self.assertFalse(
            (records.root / "day-manifests" / f"{LOCAL_DATE}.json").exists()
        )
        enable_agent_schedule(
            self.vault,
            updated_at="2026-08-18T12:00:00+08:00",
        )
        recovery_now = dt.datetime(
            2026,
            8,
            19,
            8,
            tzinfo=dt.timezone(dt.timedelta(hours=8)),
        )
        with mock.patch.object(
            cli,
            "_provider",
            side_effect=AssertionError("provider called"),
        ), mock.patch(
            "cognitive_schedule_v1._local_now",
            return_value=recovery_now,
        ):
            code, stdout, stderr = self.invoke(
                "daily-schedule-tick",
                "--once",
                "--vault",
                str(self.vault),
            )
        self.assertEqual((code, stderr), (0, ""))
        schedule = json.loads(stdout)
        self.assertEqual(schedule["status"], "not_due")
        self.assertEqual(schedule["trigger"], "recovery")
        self.assertEqual(schedule["local_date"], LOCAL_DATE)
        self.assertEqual(provider.calls, 2)

    def test_cognitive_action_worker_materializes_and_reprojects_without_provider(self) -> None:
        self.write_day()
        enable_agent_v1(self.vault)
        provider = FakeProvider()
        with mock.patch.object(cli, "_provider", return_value=provider):
            self.invoke(
                "record-ingest", "--vault", str(self.vault), "--source", f"{LOCAL_DATE}.md"
            )
            code, _, stderr = self.invoke(
                "record-worker", "--once", "--vault", str(self.vault), "--date", LOCAL_DATE
            )
        self.assertEqual((code, stderr, provider.calls), (0, "", 1))

        records = RecordStore(self.vault)
        record_id = records.list_heads(local_date=LOCAL_DATE)[0]["record_id"]
        actions = CognitiveActionStore(self.vault, state_root=records.root)
        receipt_id = make_receipt_id(record_id)
        target = actions.load_receipt_head_ref(receipt_id)
        edited_summary = "我会先交付一个可以验证的部分。"
        edit = CognitiveUserAction(
            COGNITIVE_SCHEMA_VERSION,
            "memento_cognitive_user_action",
            make_cognitive_action_id("cli-edit"),
            "2026-08-18T12:20:00+08:00",
            "edit_receipt",
            target,
            {
                "summary": edited_summary,
                "facets": {
                    "content_types": ["observation"],
                    "topics": ["产品设计"],
                    "objects": ["方案评审"],
                    "stance": "self_observation",
                    "cognitive_state": "revises_existing",
                    "purposes": ["future_decision"],
                },
            },
        )
        actions.submit_action(edit)

        with mock.patch.object(
            cli, "_provider", side_effect=AssertionError("provider called")
        ), mock.patch.object(
            cli.CognitiveRecordWorker,
            "run",
            side_effect=AssertionError("record worker called"),
        ):
            code, stdout, stderr = self.invoke(
                "cognitive-action-worker", "--once", "--vault", str(self.vault)
            )
        self.assertEqual((code, stderr), (0, ""))
        report = json.loads(stdout)
        self.assertEqual((report["applied"], report["projected_records"]), (1, 1))
        home = json.loads(
            (records.root / "projections" / "home_projection.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(home["records"][0]["summary"], edited_summary)

        target = actions.load_receipt_head_ref(receipt_id)
        action = CognitiveUserAction(
            COGNITIVE_SCHEMA_VERSION,
            "memento_cognitive_user_action",
            make_cognitive_action_id("cli-original-only"),
            "2026-08-18T12:30:00+08:00",
            "original_only",
            target,
            None,
        )
        actions.submit_action(action)

        with mock.patch.object(
            cli, "_provider", side_effect=AssertionError("provider called")
        ), mock.patch.object(
            cli.CognitiveRecordWorker,
            "run",
            side_effect=AssertionError("record worker called"),
        ):
            code, stdout, stderr = self.invoke(
                "cognitive-action-worker", "--once", "--vault", str(self.vault)
            )
        self.assertEqual((code, stderr), (0, ""))
        report = json.loads(stdout)
        self.assertEqual((report["applied"], report["projected_records"]), (1, 1))
        self.assertNotIn(RAW_SECRET, stdout)
        self.assertNotIn(MODEL_SECRET, stdout)
        home = json.loads(
            (records.root / "projections" / "home_projection.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(home["records"][0]["status"], "original_only")
        self.assertIsNone(home["records"][0]["summary"])

    def test_daily_manual_worker_consumes_browser_request_once_with_bounded_output(self) -> None:
        enable_agent_v1(self.vault)
        ManualDayRequestStore(self.vault).create_request(
            ManualDayRequest(
                "cman_" + "a" * 24,
                "2026-08-18T12:29:00+08:00",
                LOCAL_DATE,
            )
        )
        calls: list[tuple[str, str]] = []

        def factory(args: Any, vault: Path):
            self.assertEqual(vault.resolve(), self.vault.resolve())

            def runner(local_date: str, trigger: str):
                calls.append((local_date, trigger))
                return {"status": "no_change"}

            return runner

        fixed_now = dt.datetime(
            2026, 8, 18, 12, 30, tzinfo=dt.timezone(dt.timedelta(hours=8))
        )
        with mock.patch.object(cli, "_cognitive_day_runner", side_effect=factory), mock.patch(
            "cognitive_manual_request_v1._aware_time", return_value=fixed_now
        ):
            first = self.invoke(
                "daily-manual-worker", "--once", "--vault", str(self.vault)
            )
            second = self.invoke(
                "daily-manual-worker", "--once", "--vault", str(self.vault)
            )
        self.assertEqual((first[0], first[2], second[0], second[2]), (0, "", 0, ""))
        first_report = json.loads(first[1])
        second_report = json.loads(second[1])
        self.assertEqual((first_report["completed"], second_report["already_resolved"]), (1, 1))
        self.assertEqual(calls, [(LOCAL_DATE, "manual")])
        self.assertNotIn("request_id", first[1])
        self.assertNotIn("path", first[1])


if __name__ == "__main__":
    unittest.main()
