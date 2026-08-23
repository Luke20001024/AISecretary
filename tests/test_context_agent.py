#!/usr/bin/env python3

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "context-agent"
PRODUCT_MANAGER_SCENARIO = (
    AGENT_DIR / "eval" / "scenarios" / "product-manager-20d"
)
sys.path.insert(0, str(AGENT_DIR))

from core import (  # noqa: E402
    ContractError,
    Pricing,
    append_usage_log,
    build_context_pack,
    calculate_cost,
    collect_sources,
    create_pending,
    decide_candidate,
    make_candidate_id,
    pricing_for_model,
    source_hashes,
    validate_model_response,
    validate_pending,
)
import context_agent as context_agent_cli  # noqa: E402
from deepseek_provider import (  # noqa: E402
    CompletionResult,
    DeepSeekProvider,
    ProviderError,
)


def valid_response() -> dict:
    return {
        "schema_version": "1.0",
        "status": "candidate",
        "candidate": {
            "statement": "输出方案时先给结论，再给细节。",
            "scope": "global",
            "why_now": "记录明确表达了可复用的输出偏好。",
            "category": "work_preference",
            "sensitive": False,
            "uncertainty": "low",
            "evidence": [
                {
                    "file": "2026-08-01.md",
                    "line": 5,
                    "quote": "以后给我技术方案时，先写结论，再写实现细节。",
                },
                {
                    "file": "2026-08-02.md",
                    "line": 5,
                    "quote": "产品文档依然先说结论，再展开技术细节。",
                }
            ],
        },
    }


class ContextAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="memento-agent-test-")
        self.vault = Path(self.temporary.name)
        self.source = self.vault / "2026-08-01.md"
        self.source.write_text(
            "# 2026-08-01\n\n## 09:10 · 项目复盘\n\n"
            "以后给我技术方案时，先写结论，再写实现细节。\n",
            encoding="utf-8",
        )
        self.second_source = self.vault / "2026-08-02.md"
        self.second_source.write_text(
            "# 2026-08-02\n\n## 18:30 · 文档反馈\n\n"
            "产品文档依然先说结论，再展开技术细节。\n",
            encoding="utf-8",
        )
        self.sources = [self.source, self.second_source]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_pending(self) -> tuple[dict, Path]:
        hashes = source_hashes(self.sources)
        return create_pending(
            valid_response(),
            self.vault,
            provider="mock",
            model="fixture",
            hashes=hashes,
            created_at="2026-08-10T00:00:00+00:00",
        )

    def test_exact_evidence_is_required(self) -> None:
        validate_model_response(valid_response(), self.vault)
        response = valid_response()
        response["candidate"]["evidence"][0]["quote"] += " "
        with self.assertRaisesRegex(ContractError, "不完全一致") as captured:
            validate_model_response(response, self.vault)
        self.assertEqual(captured.exception.kind, "evidence")

    def test_schema_rejects_extra_field_and_sensitive_inference(self) -> None:
        response = valid_response()
        response["candidate"]["invented"] = "not allowed"
        with self.assertRaisesRegex(ContractError, "未知字段"):
            validate_model_response(response, self.vault)

        response = valid_response()
        response["candidate"]["statement"] = "记录用户的政治立场。"
        with self.assertRaises(ContractError) as captured:
            validate_model_response(response, self.vault)
        self.assertEqual(captured.exception.kind, "sensitive")

        response = valid_response()
        response["candidate"]["uncertainty"] = "high"
        with self.assertRaisesRegex(ContractError, "high 必须返回 no_candidate"):
            validate_model_response(response, self.vault)

    def test_sensitive_emotional_state_false_negative_is_blocked(self) -> None:
        # Regression: the earlier lexical backstop accepted these PRD-forbidden
        # emotional / mental-state inferences when the model set sensitive=false.
        for statement in (
            "用户情绪低落。",
            "用户今天情绪很好。",
            "用户目前处于焦虑状态。",
            "The user is emotionally unstable.",
        ):
            with self.subTest(statement=statement):
                response = valid_response()
                response["candidate"]["statement"] = statement
                response["candidate"]["sensitive"] = False
                with self.assertRaises(ContractError) as captured:
                    validate_model_response(response, self.vault)
                self.assertEqual(captured.exception.kind, "sensitive")

    def test_work_preference_requires_two_distinct_daily_files(self) -> None:
        response = valid_response()
        response["candidate"]["evidence"] = response["candidate"]["evidence"][:1]
        with self.assertRaises(ContractError) as captured:
            validate_model_response(response, self.vault)
        self.assertEqual(captured.exception.kind, "evidence")
        self.assertIn("两个不同日期", str(captured.exception))

    def test_evidence_symlink_cannot_escape_vault(self) -> None:
        outside_dir = Path(self.temporary.name).parent / f"outside-{os.getpid()}"
        outside_dir.mkdir(exist_ok=True)
        outside = outside_dir / "outside.md"
        outside.write_text(self.second_source.read_text(encoding="utf-8"), encoding="utf-8")
        self.second_source.unlink()
        self.second_source.symlink_to(outside)
        try:
            with self.assertRaises(ContractError) as captured:
                validate_model_response(valid_response(), self.vault)
            self.assertEqual(captured.exception.kind, "evidence")
            self.assertIn("vault 边界", str(captured.exception))
        finally:
            self.second_source.unlink(missing_ok=True)
            outside.unlink(missing_ok=True)
            outside_dir.rmdir()

    def test_automatic_latest_collection_rejects_symlink_escape(self) -> None:
        outside_dir = Path(self.temporary.name).parent / f"latest-outside-{os.getpid()}"
        outside_dir.mkdir(exist_ok=True)
        outside = outside_dir / "record.md"
        outside.write_text("# escaped\n", encoding="utf-8")
        self.second_source.unlink()
        self.second_source.symlink_to(outside)
        try:
            with self.assertRaises(ContractError) as captured:
                collect_sources(self.vault)
            self.assertEqual(captured.exception.kind, "evidence")
            self.assertIn("vault 边界", str(captured.exception))
        finally:
            self.second_source.unlink(missing_ok=True)
            outside.unlink(missing_ok=True)
            outside_dir.rmdir()

    def test_candidate_id_is_stable_across_evidence_and_hash_order(self) -> None:
        candidate = valid_response()["candidate"]
        candidate["evidence"] = list(candidate["evidence"])
        hashes = [
            {"file": "2026-08-02.md", "sha256": "b" * 64},
            {"file": "2026-08-01.md", "sha256": "a" * 64},
        ]
        first = make_candidate_id(candidate, hashes)
        reversed_candidate = dict(candidate)
        reversed_candidate["evidence"] = list(reversed(candidate["evidence"]))
        second_id = make_candidate_id(reversed_candidate, list(reversed(hashes)))
        self.assertEqual(first, second_id)

    def test_pending_contract_is_flat_and_hash_bound(self) -> None:
        pending, path = self.create_pending()
        self.assertEqual(pending["id"], pending["candidate_id"])
        self.assertEqual(pending["status"], "candidate")
        self.assertIn("statement", pending)
        self.assertNotIn("candidate", pending)
        self.assertRegex(pending["generation_key"], r"^gen_[0-9a-f]{24}$")
        self.assertEqual(validate_pending(json.loads(path.read_text()), self.vault), pending)

        original = self.source.read_text(encoding="utf-8")
        self.source.write_text(original + "人工补充\n", encoding="utf-8")
        with self.assertRaises(ContractError) as captured:
            validate_pending(json.loads(path.read_text()), self.vault)
        self.assertEqual(captured.exception.kind, "stale")

    def test_source_change_during_generation_blocks_candidate_write(self) -> None:
        hashes = source_hashes(self.sources)
        self.second_source.write_text(
            self.second_source.read_text(encoding="utf-8") + "生成期间人工追加。\n",
            encoding="utf-8",
        )
        with self.assertRaises(ContractError) as captured:
            create_pending(
                valid_response(),
                self.vault,
                provider="mock",
                model="fixture",
                hashes=hashes,
            )
        self.assertEqual(captured.exception.kind, "stale")
        candidates = self.vault / ".context-agent" / "candidates"
        self.assertFalse(candidates.exists())

    def test_confirm_writes_context_and_never_modifies_source(self) -> None:
        pending, _ = self.create_pending()
        original_bytes = self.source.read_bytes()
        decision = decide_candidate(
            self.vault,
            pending["id"],
            "confirm",
            decided_at="2026-08-10T01:00:00+00:00",
        )
        self.assertEqual(decision["action"], "confirm")
        confirmed_path = self.vault / "Context" / "Confirmed" / f"{pending['id']}.json"
        confirmed = json.loads(confirmed_path.read_text(encoding="utf-8"))
        self.assertEqual(confirmed["status"], "active")
        self.assertEqual(confirmed["original_candidate_id"], pending["id"])
        self.assertEqual(confirmed["decision_action"], "confirm")
        self.assertEqual(self.source.read_bytes(), original_bytes)

        markdown, stats = build_context_pack(self.vault)
        self.assertEqual(stats, {"included": 1, "invalid_skipped": 0})
        self.assertIn(pending["statement"], markdown)
        self.assertIn("2026-08-01.md:5", markdown)

    def test_scope_and_edit_are_user_authored_confirmations(self) -> None:
        pending, _ = self.create_pending()
        decision = decide_candidate(
            self.vault, pending["id"], "scope", scope="Memento MVP"
        )
        self.assertEqual(decision["scope"], "Memento MVP")
        confirmed = json.loads(
            (
                self.vault
                / "Context"
                / "Confirmed"
                / f"{pending['id']}.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(confirmed["scope"], "Memento MVP")
        with self.assertRaises(ContractError) as captured:
            decide_candidate(self.vault, pending["id"], "scope", scope="别的项目")
        self.assertEqual(captured.exception.kind, "conflict")

        other_vault = self.vault / "other"
        other_vault.mkdir()
        other_source = other_vault / self.source.name
        other_source.write_bytes(self.source.read_bytes())
        other_second_source = other_vault / self.second_source.name
        other_second_source.write_bytes(self.second_source.read_bytes())
        other_pending, _ = create_pending(
            valid_response(),
            other_vault,
            provider="mock",
            model="fixture",
            hashes=source_hashes([other_source, other_second_source]),
        )
        edit = decide_candidate(
            other_vault,
            other_pending["id"],
            "edit",
            statement="先给结论，再补充必要细节。",
        )
        self.assertEqual(edit["statement"], "先给结论，再补充必要细节。")
        with self.assertRaises(ContractError) as captured:
            decide_candidate(
                other_vault,
                other_pending["id"],
                "edit",
                statement="另一条不同的修改。",
            )
        self.assertEqual(captured.exception.kind, "conflict")

    def test_stranded_confirmed_context_is_recovered_idempotently(self) -> None:
        pending, _ = self.create_pending()
        first = decide_candidate(
            self.vault,
            pending["id"],
            "confirm",
            decided_at="2026-08-10T01:00:00+00:00",
        )
        decision_path = (
            self.vault / ".context-agent" / "decisions" / f"{pending['id']}.json"
        )
        decision_path.unlink()
        recovered = decide_candidate(
            self.vault,
            pending["id"],
            "confirm",
            decided_at="2026-08-10T02:00:00+00:00",
        )
        self.assertEqual(first["decided_at"], recovered["decided_at"])
        self.assertTrue(decision_path.is_file())

        decision_path.unlink()
        confirmed_path = (
            self.vault / "Context" / "Confirmed" / f"{pending['id']}.json"
        )
        conflicting = json.loads(confirmed_path.read_text(encoding="utf-8"))
        conflicting["statement"] = "冲突内容"
        confirmed_path.write_text(json.dumps(conflicting), encoding="utf-8")
        with self.assertRaises(ContractError) as captured:
            decide_candidate(self.vault, pending["id"], "confirm")
        self.assertEqual(captured.exception.kind, "conflict")

    def test_reject_and_just_once_do_not_create_confirmed_context(self) -> None:
        pending, _ = self.create_pending()
        decision = decide_candidate(self.vault, pending["id"], "reject")
        self.assertEqual(decision["action"], "reject")
        self.assertFalse((self.vault / "Context" / "Confirmed").exists())

        other_vault = self.vault / "once"
        other_vault.mkdir()
        other_source = other_vault / self.source.name
        other_source.write_bytes(self.source.read_bytes())
        other_second_source = other_vault / self.second_source.name
        other_second_source.write_bytes(self.second_source.read_bytes())
        other, _ = create_pending(
            valid_response(),
            other_vault,
            provider="mock",
            model="fixture",
            hashes=source_hashes([other_source, other_second_source]),
        )
        once = decide_candidate(other_vault, other["id"], "just_once")
        self.assertIn("one_time_context", once)
        self.assertEqual(
            set(once["one_time_context"]),
            {
                "statement",
                "scope",
                "category",
                "evidence",
                "source_hashes",
                "original_candidate_id",
            },
        )
        retry = decide_candidate(other_vault, other["id"], "just_once")
        self.assertEqual(retry["one_time_context"], once["one_time_context"])
        self.assertFalse((other_vault / "Context" / "Confirmed").exists())

    def test_usage_log_contains_tokens_and_cost_but_no_content_or_key(self) -> None:
        usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "total_tokens": 1200,
            "prompt_cache_hit_tokens": 250,
            "prompt_cache_miss_tokens": 750,
            "completion_tokens_details": {"reasoning_tokens": 20},
        }
        event = append_usage_log(
            self.vault,
            model="deepseek-v4-pro",
            provider="deepseek",
            usage=usage,
            request_id="request_fixture",
        )
        self.assertEqual(event["reasoning_tokens"], 20)
        self.assertGreater(event["cost_usd"], 0)
        log_path = next((self.vault / ".context-agent" / "usage").glob("*.ndjson"))
        raw = log_path.read_text(encoding="utf-8")
        self.assertNotIn("messages", raw)
        self.assertNotIn("content", raw)
        self.assertNotIn("secret_fixture", raw)
        self.assertFalse(event["usage_missing"])
        self.assertAlmostEqual(
            calculate_cost(
                {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
                Pricing(
                    cache_hit_input_usd_per_million=0.1,
                    cache_miss_input_usd_per_million=0.4,
                    output_usd_per_million=0.8,
                ),
            ),
            1.2,
        )

        self.assertAlmostEqual(
            calculate_cost(
                {
                    "prompt_tokens": 1_000_000,
                    "completion_tokens": 0,
                    "prompt_cache_hit_tokens": 250_000,
                },
                Pricing(
                    cache_hit_input_usd_per_million=0.1,
                    cache_miss_input_usd_per_million=0.4,
                    output_usd_per_million=0.8,
                ),
            ),
            0.325,
        )
        pro = pricing_for_model("deepseek-v4-pro")
        flash = pricing_for_model("deepseek-v4-flash")
        self.assertEqual(
            (
                pro.cache_hit_input_usd_per_million,
                pro.cache_miss_input_usd_per_million,
                pro.output_usd_per_million,
            ),
            (0.003625, 0.435, 0.87),
        )
        self.assertEqual(
            (
                flash.cache_hit_input_usd_per_million,
                flash.cache_miss_input_usd_per_million,
                flash.output_usd_per_million,
            ),
            (0.0028, 0.14, 0.28),
        )

        missing_event = append_usage_log(
            self.vault,
            model="deepseek-v4-pro",
            provider="deepseek",
            usage={},
            request_id="request_without_usage",
        )
        self.assertTrue(missing_event["usage_missing"])
        self.assertIsNone(missing_event["cost_usd"])

    def test_usage_log_rejects_symlinked_parent_directory_and_file(self) -> None:
        fixed_time = "2026-08-12T10:00:00+00:00"
        usage = {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}
        for scenario in ("runtime", "usage", "file", "hardlink"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(
                prefix=f"memento-usage-{scenario}-"
            ) as temporary:
                root = Path(temporary)
                vault = root / "vault"
                vault.mkdir()
                external = root / "external"
                external.mkdir()
                runtime = vault / ".context-agent"
                usage_dir = runtime / "usage"
                protected = vault / "2026-08-12.md"
                protected.write_text("# 不可被 usage 日志改写\n", encoding="utf-8")
                before = protected.read_bytes()

                if scenario == "runtime":
                    runtime.symlink_to(external, target_is_directory=True)
                elif scenario == "usage":
                    runtime.mkdir()
                    usage_dir.symlink_to(external, target_is_directory=True)
                else:
                    usage_dir.mkdir(parents=True)
                    log_path = usage_dir / "2026-08.ndjson"
                    if scenario == "file":
                        log_path.symlink_to(protected)
                    else:
                        os.link(protected, log_path)

                with mock.patch("core.utc_now", return_value=fixed_time):
                    with self.assertRaises(ContractError):
                        append_usage_log(
                            vault,
                            model="deepseek-v4-pro",
                            provider="deepseek",
                            usage=usage,
                            request_id="request_attack_fixture",
                        )
                self.assertEqual(protected.read_bytes(), before)
                self.assertEqual(list(external.glob("*.ndjson")), [])

    def test_deepseek_provider_reads_key_from_environment_and_requests_json(self) -> None:
        body = json.dumps(
            {
                "id": "request_fixture",
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"ok":true}'},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }
        ).encode()

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return body

        captured = {}

        def fake_urlopen(request, **kwargs):
            captured["request"] = request
            captured["kwargs"] = kwargs
            return FakeResponse()

        provider = DeepSeekProvider()
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret_fixture"}, clear=False):
            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                result = provider.complete([{"role": "user", "content": "return JSON"}])
        request = captured["request"]
        payload = json.loads(request.data.decode())
        self.assertEqual(payload["model"], "deepseek-v4-pro")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(request.get_header("Authorization"), "Bearer secret_fixture")
        self.assertEqual(result.request_id, "request_fixture")

        truncated_body = json.dumps(
            {
                "id": "request_truncated",
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"schema_version":"1.0"}'},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1200},
            }
        ).encode()

        class TruncatedResponse(FakeResponse):
            def read(self):
                return truncated_body

        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret_fixture"}, clear=False):
            with mock.patch("urllib.request.urlopen", return_value=TruncatedResponse()):
                with self.assertRaisesRegex(ProviderError, "length") as truncated:
                    provider.complete([{"role": "user", "content": "return JSON"}])
        self.assertEqual(
            truncated.exception.usage,
            {"prompt_tokens": 10, "completion_tokens": 1200},
        )
        self.assertEqual(truncated.exception.request_id, "request_truncated")
        self.assertEqual(truncated.exception.model, "deepseek-v4-pro")
        self.assertNotIn("schema_version", str(truncated.exception))

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("deepseek_provider.sys.platform", "linux"):
                with self.assertRaisesRegex(ProviderError, "DEEPSEEK_API_KEY"):
                    provider.complete([])

    def test_deepseek_provider_reads_key_from_macos_keychain(self) -> None:
        body = json.dumps(
            {
                "id": "request_keychain_fixture",
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"ok":true}'},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }
        ).encode()

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return body

        keychain_result = mock.Mock(
            returncode=0,
            stdout="keychain_fixture\n",
            stderr="",
        )
        captured = {}

        def fake_urlopen(request, **kwargs):
            captured["request"] = request
            return FakeResponse()

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("deepseek_provider.sys.platform", "darwin"):
                with mock.patch("deepseek_provider.getpass.getuser", return_value="test-user"):
                    with mock.patch(
                        "deepseek_provider.subprocess.run",
                        return_value=keychain_result,
                    ) as keychain_lookup:
                        with mock.patch(
                            "urllib.request.urlopen", side_effect=fake_urlopen
                        ):
                            result = DeepSeekProvider().complete(
                                [{"role": "user", "content": "return JSON"}]
                            )

        self.assertEqual(result.request_id, "request_keychain_fixture")
        self.assertEqual(
            captured["request"].get_header("Authorization"),
            "Bearer keychain_fixture",
        )
        command = keychain_lookup.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/security")
        self.assertIn("com.memento.context-agent.deepseek-api-key", command)
        self.assertNotIn("keychain_fixture", command)

    def test_generate_preserves_error_usage_and_marks_missing_usage(self) -> None:
        cli_args = [
            "generate",
            "--vault",
            str(self.vault),
            "--source",
            self.source.name,
        ]
        failed_provider = mock.Mock()
        failed_provider.complete.side_effect = ProviderError(
            "DeepSeek 响应未正常结束（length）",
            usage={
                "prompt_tokens": 25,
                "completion_tokens": 1200,
                "total_tokens": 1225,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 25,
            },
            request_id="request_generate_truncated",
            model="deepseek-v4-pro",
        )
        error_output = io.StringIO()
        with mock.patch.object(context_agent_cli, "_provider", return_value=failed_provider):
            with redirect_stderr(error_output):
                exit_code = context_agent_cli.main(cli_args)
        self.assertEqual(exit_code, 2)
        self.assertIn("length", error_output.getvalue())

        log_path = next((self.vault / ".context-agent" / "usage").glob("*.ndjson"))
        first_event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(first_event["request_id"], "request_generate_truncated")
        self.assertFalse(first_event["usage_missing"])
        self.assertGreater(first_event["cost_usd"], 0)

        missing_provider = mock.Mock()
        missing_provider.complete.return_value = CompletionResult(
            content=json.dumps(
                {
                    "schema_version": "1.0",
                    "status": "no_candidate",
                    "candidate": None,
                }
            ),
            usage={},
            request_id="request_generate_missing_usage",
            model="deepseek-v4-pro",
        )
        success_output = io.StringIO()
        with mock.patch.object(context_agent_cli, "_provider", return_value=missing_provider):
            with redirect_stdout(success_output):
                exit_code = context_agent_cli.main(cli_args)
        self.assertEqual(exit_code, 0)
        result = json.loads(success_output.getvalue())
        self.assertTrue(result["usage"]["usage_missing"])
        self.assertIsNone(result["usage"]["cost_usd"])
        self.assertEqual(result["status"], "no_candidate")

    def test_cli_offline_eval_passes_and_reports_cost(self) -> None:
        process = subprocess.run(
            [sys.executable, str(AGENT_DIR / "context_agent.py"), "eval"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertTrue(report["all_passed"])
        self.assertEqual(report["reports"][0]["cases_total"], 9)
        self.assertGreater(report["reports"][0]["cost_usd"], 0)
        self.assertEqual(report["reports"][0]["calls_attempted"], 0)
        self.assertEqual(report["reports"][0]["errors_total"], 0)

    def test_eval_case_sources_cannot_escape_or_overwrite_temp_vault(self) -> None:
        with tempfile.TemporaryDirectory(prefix="memento-eval-source-boundary-") as temporary:
            root = Path(temporary)
            case_vault = root / "case-vault"
            case_vault.mkdir()
            sentinel = root / "sentinel.txt"
            sentinel.write_text("不可覆盖\n", encoding="utf-8")
            before = sentinel.read_bytes()

            for malicious_name in ("../sentinel.txt", str(sentinel)):
                with self.subTest(name=malicious_name):
                    with self.assertRaises(ContractError):
                        context_agent_cli._write_case_sources(
                            case_vault, {malicious_name: "owned\n"}
                        )
                    self.assertEqual(sentinel.read_bytes(), before)

            linked = case_vault / "2026-08-12.md"
            linked.symlink_to(sentinel)
            with self.assertRaises(ContractError):
                context_agent_cli._write_case_sources(
                    case_vault, {linked.name: "owned\n"}
                )
            self.assertEqual(sentinel.read_bytes(), before)
            linked.unlink()

            existing = case_vault / "2026-08-12.md"
            existing.write_text("已有测试记录\n", encoding="utf-8")
            existing_before = existing.read_bytes()
            with self.assertRaises(ContractError):
                context_agent_cli._write_case_sources(
                    case_vault, {existing.name: "owned\n"}
                )
            self.assertEqual(existing.read_bytes(), existing_before)

    def test_live_eval_continues_after_provider_and_json_errors_across_models(self) -> None:
        cases = []
        for index in range(4):
            cases.append(
                {
                    "name": f"resilience case {index + 1}",
                    "live_eval": True,
                    "sources": {
                        f"2026-08-{index + 1:02d}.md": (
                            f"# 2026-08-{index + 1:02d}\n\n"
                            "## 随手记\n\n今天喝了一杯水。\n"
                        )
                    },
                    "mock_response": {
                        "schema_version": "1.0",
                        "status": "no_candidate",
                        "candidate": None,
                    },
                    "expected": {
                        "contract_valid": True,
                        "evidence_valid": True,
                        "status": "no_candidate",
                    },
                }
            )

        counts = {"deepseek-v4-pro": 0, "deepseek-v4-flash": 0}

        class FlakyProvider:
            def __init__(self, model: str) -> None:
                self.model = model

            def complete(self, messages):
                index = counts[self.model]
                counts[self.model] += 1
                if self.model == "deepseek-v4-pro" and index == 0:
                    raise ProviderError(
                        "synthetic provider failure",
                        usage={
                            "prompt_tokens": 10,
                            "completion_tokens": 1,
                            "total_tokens": 11,
                            "prompt_cache_hit_tokens": 0,
                            "prompt_cache_miss_tokens": 10,
                        },
                        request_id=f"{self.model}-{index}",
                        model=self.model,
                    )
                if self.model == "deepseek-v4-pro" and index == 1:
                    content = "not-json"
                elif self.model == "deepseek-v4-pro" and index == 2:
                    content = json.dumps({"unexpected": "object"})
                else:
                    content = json.dumps(
                        {
                            "schema_version": "1.0",
                            "status": "no_candidate",
                            "candidate": None,
                        }
                    )
                return CompletionResult(
                    content=content,
                    usage={
                        "prompt_tokens": 10,
                        "completion_tokens": 1,
                        "total_tokens": 11,
                        "prompt_cache_hit_tokens": 0,
                        "prompt_cache_miss_tokens": 10,
                    },
                    request_id=f"{self.model}-{index}",
                    model=self.model,
                )

        output = io.StringIO()
        with mock.patch.object(context_agent_cli, "_load_eval_cases", return_value=cases):
            with mock.patch.object(
                context_agent_cli,
                "_provider",
                side_effect=lambda args, model=None: FlakyProvider(model or args.model),
            ):
                with redirect_stdout(output):
                    exit_code = context_agent_cli.main(
                        [
                            "eval",
                            "--live",
                            "--vault",
                            str(self.vault),
                            "--model",
                            "deepseek-v4-pro",
                            "--model",
                            "deepseek-v4-flash",
                        ]
                    )

        self.assertEqual(exit_code, 1)
        report = json.loads(output.getvalue())
        self.assertEqual(len(report["reports"]), 2)
        pro, flash = report["reports"]
        self.assertEqual(pro["model"], "deepseek-v4-pro")
        self.assertEqual(pro["cases_total"], 4)
        self.assertEqual(pro["calls_attempted"], 4)
        self.assertEqual(pro["calls_completed"], 3)
        self.assertEqual(pro["errors_total"], 3)
        self.assertEqual(pro["provider_errors"], 1)
        self.assertEqual(pro["invalid_json_errors"], 1)
        self.assertEqual(pro["contract_errors"], 1)
        self.assertEqual(pro["usage_missing"], 0)
        self.assertTrue(pro["cost_complete"])
        self.assertEqual(pro["usage"]["prompt_tokens"], 40)
        self.assertEqual(pro["results"][0]["error_kind"], "provider_error")
        self.assertEqual(pro["results"][1]["error_kind"], "invalid_json")
        self.assertEqual(pro["results"][2]["error_kind"], "schema")
        self.assertTrue(pro["results"][3]["passed"])

        self.assertEqual(flash["model"], "deepseek-v4-flash")
        self.assertEqual(flash["calls_attempted"], 4)
        self.assertEqual(flash["calls_completed"], 4)
        self.assertEqual(flash["errors_total"], 0)
        self.assertEqual(flash["cases_passed"], 4)
        usage_lines = []
        for path in (self.vault / ".context-agent" / "usage").glob("*.ndjson"):
            usage_lines.extend(path.read_text(encoding="utf-8").splitlines())
        self.assertEqual(len(usage_lines), 8)
        provider_error_usage = next(
            json.loads(line)
            for line in usage_lines
            if json.loads(line).get("request_id") == "deepseek-v4-pro-0"
        )
        self.assertFalse(provider_error_usage["usage_missing"])
        self.assertGreater(provider_error_usage["cost_usd"], 0)

    def test_cli_generate_validate_decide_and_pack_flow(self) -> None:
        mock_response = self.vault / "mock-response.json"
        mock_response.write_text(
            json.dumps(valid_response(), ensure_ascii=False), encoding="utf-8"
        )
        cli = [sys.executable, str(AGENT_DIR / "context_agent.py")]
        generated = subprocess.run(
            cli
            + [
                "generate",
                "--vault",
                str(self.vault),
                "--source",
                self.source.name,
                "--source",
                self.second_source.name,
                "--mock-response",
                str(mock_response),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        generation = json.loads(generated.stdout)
        candidate_id = generation["candidate"]["id"]
        candidate_path = generation["candidate_path"]

        validated = subprocess.run(
            cli
            + [
                "validate",
                "--vault",
                str(self.vault),
                "--input",
                candidate_path,
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertEqual(json.loads(validated.stdout)["kind"], "candidate")

        decided = subprocess.run(
            cli
            + [
                "decide",
                "--vault",
                str(self.vault),
                "--candidate",
                candidate_id,
                "--action",
                "confirm",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(decided.returncode, 0, decided.stderr)

        packed = subprocess.run(
            cli + ["pack", "--vault", str(self.vault)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(packed.returncode, 0, packed.stderr)
        self.assertIn(valid_response()["candidate"]["statement"], packed.stdout)

        source_before = self.source.read_bytes()
        blocked = subprocess.run(
            cli
            + [
                "pack",
                "--vault",
                str(self.vault),
                "--output",
                str(self.source),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("不得覆盖", blocked.stderr)
        self.assertEqual(self.source.read_bytes(), source_before)

    def test_product_manager_20_day_scenario_is_reproducible(self) -> None:
        daily_files = sorted(PRODUCT_MANAGER_SCENARIO.glob("20??-??-??.md"))
        self.assertEqual(len(daily_files), 20)
        self.assertEqual(daily_files[0].name, "2026-07-14.md")
        self.assertEqual(daily_files[-1].name, "2026-08-02.md")
        for path in daily_files:
            text = path.read_text(encoding="utf-8")
            self.assertTrue(
                text.startswith("<!-- MEMENTO_SYNTHETIC_CONTEXT_TEST_V2_RICH -->\n")
            )
            self.assertEqual(text.count("\n## "), 10, path.name)

        ground_truth = json.loads(
            (PRODUCT_MANAGER_SCENARIO / "ground-truth.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(ground_truth["synthetic"])
        review_window = ground_truth["review_window"]
        self.assertEqual(review_window["days"], 14)
        review_names = [path.name for path in daily_files[-14:]]
        self.assertEqual(review_names[0], "2026-07-20.md")
        self.assertEqual(review_names[-1], "2026-08-02.md")
        self.assertEqual(
            [path.name for path in collect_sources(PRODUCT_MANAGER_SCENARIO, review_names)],
            review_names,
        )

        expected = ground_truth["primary_expected_candidate"]
        self.assertGreaterEqual(len(expected["evidence_days"]), 2)
        for evidence_day in expected["evidence_days"]:
            lines = (
                PRODUCT_MANAGER_SCENARIO / evidence_day
            ).read_text(encoding="utf-8").splitlines()
            self.assertIn(expected["exact_quote"], lines)


if __name__ == "__main__":
    unittest.main()
