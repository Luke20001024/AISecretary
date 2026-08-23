#!/usr/bin/env python3
"""Fail-closed acceptance harness for Cognitive Secretary V1.

The default command is plan-only.  ``--run-fake`` exercises the production
stores/orchestrators with deterministic local providers.  Real DeepSeek is
reachable only through the three-part live gate: ``--execute-live``, an exact
confirmation string, and the current plan SHA-256.

No caller-supplied Vault is accepted.  Every case owns a mode-0700 temporary
Vault containing synthetic Markdown only, and every case is attempted at most
once.  The public report is a content-free allow-list.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import hashlib
import importlib
import io
import json
import os
import re
import stat
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
CONTEXT_AGENT_ROOT = HERE.parents[1]
if str(CONTEXT_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(CONTEXT_AGENT_ROOT))

agent_v1 = importlib.import_module("agent_v1")
context_agent = importlib.import_module("context_agent")
core = importlib.import_module("core")
deepseek_provider = importlib.import_module("deepseek_provider")
cognitive_actions_v1 = importlib.import_module("cognitive_actions_v1")
cognitive_agent_adapter_v1 = importlib.import_module("cognitive_agent_adapter_v1")
cognitive_bundle_store_v1 = importlib.import_module("cognitive_bundle_store_v1")
cognitive_daily_review_v1 = importlib.import_module("cognitive_daily_review_v1")
cognitive_day_orchestrator_v1 = importlib.import_module(
    "cognitive_day_orchestrator_v1"
)
cognitive_pipeline_v1 = importlib.import_module("cognitive_pipeline_v1")
cognitive_prompts_v1 = importlib.import_module("cognitive_prompts_v1")
cognitive_projection_v1 = importlib.import_module("cognitive_projection_v1")
cognitive_record_worker_v1 = importlib.import_module("cognitive_record_worker_v1")
cognitive_runtime_v1 = importlib.import_module("cognitive_runtime_v1")
cognitive_store_v1 = importlib.import_module("cognitive_store_v1")
cognitive_v1 = importlib.import_module("cognitive_v1")


REPORT_SCHEMA_VERSION = "memento_cognitive_v1_live_acceptance.v1"
PLAN_VERSION = "cognitive-secretary-v1-isolated-2case-v4"
LIVE_CONFIRMATION = "execute-cognitive-v1-live-on-isolated-synthetic-vault"
MODEL = "deepseek-v4-pro"
PROVIDER_NAME = "deepseek"
AGENT_PROVIDER_NAME = "deepseek-agentic-workflow"
CASE_IDS = ("two_day_positive_with_negative", "original_only_retraction")
POSITIVE_QUOTE = "我在方案评审前会先检查反例和失败条件，再判断方案。"
NEGATIVE_QUOTE = "该系统规格文档要求缓存容量为二百五十六兆字节。"
TZ = dt.timezone(dt.timedelta(hours=8))
USAGE_FIELDS = frozenset(
    {
        "calls",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cache_hit_tokens",
        "cache_miss_tokens",
        "reasoning_tokens",
        "cost_usd",
        "cost_complete",
    }
)
PUBLIC_CASE_FIELDS = frozenset(
    {
        "case",
        "status",
        "calls",
        "tokens",
        "cost_usd",
        "source_hash_before",
        "source_hash_after",
        "error_kind",
        "failure_stage",
        "failed_checks",
        "agent_diagnostics",
    }
)
PUBLIC_AGENT_DIAGNOSTIC_FIELDS = frozenset(
    {
        "local_date",
        "response_status",
        "error_kind",
        "model_turns",
        "tool_calls",
        "history_matches",
        "action_counts",
        "stable_identity_status",
        "eligible_evidence_ref_count",
        "candidate_date_count",
        "structure_ready",
        "missing_requirement_codes",
    }
)
PUBLIC_STABLE_IDENTITY_STATUSES = frozenset(
    {
        "unavailable",
        "not_applicable",
        "stable",
        "ambiguous_statement",
        "unsafe_repeated_statement",
        "scope_missing",
        "scope_ambiguous",
    }
)
PUBLIC_MISSING_REQUIREMENT_CODES = frozenset(
    {
        "two_distinct_evidence_dates",
        "target_memory",
        "new_support_for_existing_memory",
        "explicit_change_signal",
        "older_direction_evidence",
        "explicit_tension_signal",
        "existing_direction_evidence",
        "stable_new_identity_ambiguous_statement",
        "stable_new_identity_unsafe_repeated_statement",
        "stable_new_identity_scope_missing",
        "stable_new_identity_scope_ambiguous",
    }
)
PUBLIC_AGENT_ACTIONS = (
    "investigate",
    "read_memory",
    "search_history",
    "finalize_patch",
    "finish",
    "invalid_action",
    "unknown",
)
PUBLIC_FAILURE_STAGES = frozenset(
    {
        "none",
        "setup",
        "record_worker",
        "day_orchestrator",
        "replay",
        "action_worker",
        "postconditions",
        "runtime",
    }
)
PUBLIC_CHECK_NAMES = frozenset(
    {
        "private_vault",
        "source_sha_invariant",
        "stable_records",
        "receipts_complete",
        "bundle_complete",
        "review_complete",
        "long_term_terminal",
        "projection_complete",
        "warnings_empty",
        "negative_excluded",
        "long_term_evidence_exact",
        "projection_refs_valid",
        "replay_zero_calls",
        "original_only_zero_calls",
        "derivatives_retracted",
        "original_preserved",
    }
)
PUBLIC_ERROR_KINDS = frozenset(
    {
        "none",
        "authorization",
        "budget",
        "conflict",
        "evidence",
        "not_found",
        "provider",
        "quality_gate",
        "runtime",
        "schema",
        "stale",
        "usage_missing",
    }
)
RAW_REFERENCE_RE = re.compile(
    r"\b(?:eref|mem|rcp|rec|arq|arun|ark|ltg|bnd|dsm|act|uact)_[0-9a-f]{8,}\b"
)
ABSOLUTE_PATH_RE = re.compile(
    r"(?:/Users/|/home/|/private/var/|/var/folders/|/tmp/)"
)

EXPECTED_AGENT_CONTRACT = {
    "prompt_version": "remember-agent-v1.22",
    "workflow_policy_version": "agentic-workflow-investigation-v1.13",
    "stable_new_identity_policy_version": "stable-new-identity-v1.1",
    "stable_new_terminal_gate_policy_version": "stable-new-terminal-gate-v1.0",
}
AS_OF_SOURCE_NAMES = (
    "agent_v1.py",
    "cognitive_agent_adapter_v1.py",
    "cognitive_day_orchestrator_v1.py",
    "cognitive_store_v1.py",
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    payload = value if isinstance(value, bytes) else _canonical(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _prefix(value: str) -> str:
    return value[:12]


def _assert_content_free(value: Any) -> Any:
    encoded = _canonical(value)
    for forbidden in (POSITIVE_QUOTE, NEGATIVE_QUOTE, "sk-", str(Path.home())):
        if forbidden in encoded:
            raise core.ContractError(
                "public report contains forbidden content", kind="runtime"
            )
    if RAW_REFERENCE_RE.search(encoded) or ABSOLUTE_PATH_RE.search(encoded):
        raise core.ContractError(
            "public report contains a raw reference or path", kind="runtime"
        )
    return value


def _source_digest(vault: Path) -> str:
    rows = [
        {"file": path.name, "sha256": core.sha256_file(path)}
        for path in sorted(vault.glob("20??-??-??.md"))
    ]
    return _sha(rows)


def _private_mode(path: Path, *, directory: bool) -> bool:
    details = path.lstat()
    expected = stat.S_ISDIR(details.st_mode) if directory else stat.S_ISREG(details.st_mode)
    return bool(
        expected
        and details.st_uid == os.getuid()
        and not path.is_symlink()
        and stat.S_IMODE(details.st_mode) & 0o077 == 0
    )


def _current_local_date() -> str:
    return dt.datetime.now().astimezone().date().isoformat()


@dataclasses.dataclass(frozen=True)
class AcceptanceConfig:
    model: str = MODEL
    timeout_seconds: float = 120.0
    max_calls: int = 20
    max_total_tokens: int = 160_000
    max_cost_usd: float = 0.25

    def validate(self) -> "AcceptanceConfig":
        if self.model != MODEL:
            raise ValueError("unsupported model")
        if not 1 <= self.timeout_seconds <= 300:
            raise ValueError("invalid timeout")
        if self.max_calls != 20 or self.max_total_tokens != 160_000:
            raise ValueError("invalid live budget")
        if self.max_cost_usd != 0.25:
            raise ValueError("invalid cost budget")
        return self


def _source_manifest() -> list[dict[str, str]]:
    files = [
        Path(__file__),
        Path(agent_v1.__file__),
        Path(context_agent.__file__),
        Path(cognitive_actions_v1.__file__),
        Path(cognitive_agent_adapter_v1.__file__),
        Path(cognitive_bundle_store_v1.__file__),
        Path(cognitive_daily_review_v1.__file__),
        Path(cognitive_day_orchestrator_v1.__file__),
        Path(cognitive_pipeline_v1.__file__),
        Path(cognitive_prompts_v1.__file__),
        Path(cognitive_projection_v1.__file__),
        Path(cognitive_record_worker_v1.__file__),
        Path(cognitive_runtime_v1.__file__),
        Path(cognitive_store_v1.__file__),
        Path(cognitive_v1.__file__),
        Path(deepseek_provider.__file__),
        CONTEXT_AGENT_ROOT / "schemas" / "record_interpreter_action_v1.json",
        CONTEXT_AGENT_ROOT / "schemas" / "daily_integrator_action_v1.json",
    ]
    return [
        {"name": path.name, "sha256": core.sha256_file(path)}
        for path in files
    ]


def _agent_contract() -> dict[str, Any]:
    actual = {
        "prompt_version": agent_v1.AGENT_PROMPT_VERSION,
        "workflow_policy_version": agent_v1.AGENTIC_WORKFLOW_POLICY_VERSION,
        "stable_new_identity_policy_version": (
            agent_v1.STABLE_NEW_IDENTITY_POLICY_VERSION
        ),
        "stable_new_terminal_gate_policy_version": (
            agent_v1.STABLE_NEW_TERMINAL_GATE_POLICY_VERSION
        ),
    }
    if actual != EXPECTED_AGENT_CONTRACT:
        raise core.ContractError(
            "live acceptance Agent contract is stale", kind="conflict"
        )
    return {
        **actual,
        "workflow_instruction_sha256": core.sha256_bytes(
            agent_v1.AGENTIC_WORKFLOW_INSTRUCTION.encode("utf-8")
        ),
        "stable_new_identity_instruction_sha256": core.sha256_bytes(
            agent_v1.STABLE_NEW_IDENTITY_INSTRUCTION.encode("utf-8")
        ),
        "stable_new_terminal_gate_instruction_sha256": core.sha256_bytes(
            agent_v1.STABLE_NEW_TERMINAL_GATE_INSTRUCTION.encode("utf-8")
        ),
    }


def _as_of_contract(sources: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    by_name = {item["name"]: item["sha256"] for item in sources}
    missing = [name for name in AS_OF_SOURCE_NAMES if name not in by_name]
    if missing:
        raise core.ContractError(
            "live acceptance as_of source manifest is incomplete", kind="conflict"
        )
    return {
        "request_as_of_bounds_daily_history": True,
        "request_as_of_bounds_record_authorization": True,
        "receipt_head_revalidated_at_authorization": True,
        "source_sha256": {
            name: by_name[name] for name in AS_OF_SOURCE_NAMES
        },
    }


def _plan_payload(config: AcceptanceConfig) -> dict[str, Any]:
    config.validate()
    sources = _source_manifest()
    return {
        "version": PLAN_VERSION,
        "schema_version": REPORT_SCHEMA_VERSION,
        "cases": list(CASE_IDS),
        "model": config.model,
        "thinking": "disabled",
        "timeout_seconds": config.timeout_seconds,
        "limits": {
            "calls": config.max_calls,
            "tokens": config.max_total_tokens,
            "cost_usd": config.max_cost_usd,
        },
        "live_confirmation_sha256": _sha(LIVE_CONFIRMATION.encode("utf-8")),
        "sources": sources,
        "agent_contract": _agent_contract(),
        "as_of_contract": _as_of_contract(sources),
        "fixtures": {
            "two_day_dates": ["2026-08-17", "2026-08-18"],
            "original_only_date": _current_local_date(),
            "positive_sha256": _sha(POSITIVE_QUOTE.encode("utf-8")),
            "negative_sha256": _sha(NEGATIVE_QUOTE.encode("utf-8")),
        },
        "quality_contract": {
            "current_user_keychain_only": True,
            "temporary_vault_only": True,
            "source_markdown_immutable": True,
            "production_orchestration_only": True,
            "negative_not_long_term": True,
            "same_input_replay_zero_calls": True,
            "original_only_zero_additional_calls": True,
            "public_report_content_free": True,
            "stop_after_first_failed_case": True,
        },
    }


def plan_sha256(config: AcceptanceConfig) -> str:
    return _sha(_plan_payload(config))


def build_plan(config: AcceptanceConfig) -> dict[str, Any]:
    return _assert_content_free({
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "plan_only",
        "executed": False,
        "status": "planned",
        "error_kind": "none",
        "plan_sha256": plan_sha256(config),
        "plan": _plan_payload(config),
        "cases": [],
        "usage": _empty_usage(),
        "temporary_directory": "not_created",
    })


def _empty_usage() -> dict[str, Any]:
    return {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "reasoning_tokens": 0,
        "cost_usd": 0.0,
        "cost_complete": True,
    }


class UsageMeter:
    """Aggregate only billing metadata and stop before exceeding a budget."""

    def __init__(self, config: AcceptanceConfig) -> None:
        self.config = config
        self.value = _empty_usage()

    def before_call(self) -> None:
        if self.value["calls"] >= self.config.max_calls:
            raise core.ContractError("live call budget exhausted", kind="budget")

    def after_call(self, completion: Any) -> None:
        self.value["calls"] += 1
        usage = getattr(completion, "usage", None)
        normalized = core.normalize_usage(usage if isinstance(usage, Mapping) else None)
        missing = core.usage_is_missing(usage if isinstance(usage, Mapping) else None)
        for public, source in (
            ("prompt_tokens", "prompt_tokens"),
            ("completion_tokens", "completion_tokens"),
            ("total_tokens", "total_tokens"),
            ("cache_hit_tokens", "prompt_cache_hit_tokens"),
            ("cache_miss_tokens", "prompt_cache_miss_tokens"),
            ("reasoning_tokens", "reasoning_tokens"),
        ):
            self.value[public] += normalized[source]
        if missing:
            self.value["cost_complete"] = False
        else:
            self.value["cost_usd"] = round(
                self.value["cost_usd"]
                + core.calculate_cost(normalized, core.pricing_for_model(self.config.model)),
                10,
            )
        if self.value["total_tokens"] > self.config.max_total_tokens:
            raise core.ContractError("live token budget exhausted", kind="budget")
        if self.value["cost_usd"] > self.config.max_cost_usd:
            raise core.ContractError("live cost budget exhausted", kind="budget")

    def public(self) -> dict[str, Any]:
        result = dict(self.value)
        if set(result) != USAGE_FIELDS:
            raise core.ContractError("usage report allow-list mismatch", kind="runtime")
        return result


class MeteredProvider:
    def __init__(
        self,
        delegate: Any,
        meter: UsageMeter,
        *,
        request_observer: Callable[[Sequence[Mapping[str, str]]], None] | None = None,
    ) -> None:
        self.delegate = delegate
        self.meter = meter
        self.request_observer = request_observer

    def complete(self, messages: Sequence[Mapping[str, str]]) -> Any:
        self.meter.before_call()
        if self.request_observer is not None:
            # Diagnostics must never influence the production Agent outcome.
            # The observer keeps only finite metadata from an already-built
            # Workflow bundle and discards all text-bearing input.
            with contextlib.suppress(Exception):
                self.request_observer(messages)
        try:
            completion = self.delegate.complete(messages)
        except Exception as exc:
            usage = getattr(exc, "usage", None)
            if isinstance(usage, Mapping):
                stub = type("UsageOnly", (), {"usage": usage})()
                self.meter.after_call(stub)
            else:
                self.meter.value["calls"] += 1
                self.meter.value["cost_complete"] = False
            raise
        self.meter.after_call(completion)
        return completion


class FakeCognitiveProvider:
    """Deterministic provider; it never opens a socket."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.calls = 0

    @staticmethod
    def _record_action(payload: Mapping[str, Any]) -> dict[str, Any]:
        catalog = payload["untrusted_data"]["source_catalog"]
        refs = [row["ref_id"] for row in catalog]
        text = "\n".join(row["span"]["quote"] for row in catalog)
        positive = POSITIVE_QUOTE in text
        return {
            "schema_version": "1.0",
            "action": "propose_receipt",
            "reason_code": "interpretation_ready",
            "arguments": {
                "summary": (
                    "评审前先核对反例和失败条件。"
                    if positive
                    else "一条待查找的系统规格。"
                ),
                "facets": {
                    "content_types": ["observation" if positive else "fact"],
                    "topics": ["产品评审" if positive else "系统规格"],
                    "objects": ["方案" if positive else "缓存容量"],
                    "stance": "self_observation" if positive else "unknown",
                    "cognitive_state": "repeated" if positive else "first_seen",
                    "purposes": ["future_decision" if positive else "find_later"],
                },
                "memory_candidates": (
                    [
                        {
                            "statement": POSITIVE_QUOTE,
                            "memory_kind": "observation",
                            "topics": ["产品评审"],
                            "purposes": ["future_decision"],
                            "uncertainty": "medium",
                            "source_ref_ids": refs,
                        }
                    ]
                    if positive
                    else []
                ),
                "relation_candidates": [],
                "source_ref_ids": refs,
            },
        }

    @staticmethod
    def _daily_action(payload: Mapping[str, Any]) -> dict[str, Any]:
        catalog = payload["untrusted_data"]["source_catalog"]
        positive_refs = [
            row["ref_id"]
            for row in catalog
            if row["span"]["quote"] == POSITIVE_QUOTE
        ]
        if not positive_refs:
            return {
                "schema_version": "1.0",
                "action": "finish",
                "reason_code": "no_change",
                "arguments": {"reason": "no_change"},
            }
        return {
            "schema_version": "1.0",
            "action": "propose_daily_bundle",
            "reason_code": "bundle_ready",
            "arguments": {
                "overview": "今天继续在评审前检查反例与失败条件。",
                "themes": ["产品评审"],
                "changes": [],
                "unresolved_questions": [],
                "action_clues": ["判断方案前先写反例和失败条件。"],
                "memory_operations": [
                    {
                        "operation": "new",
                        "target_memory_ref_id": None,
                        "statement": POSITIVE_QUOTE,
                        "memory_kind": "observation",
                        "topics": ["产品评审"],
                        "purposes": ["future_decision"],
                        "uncertainty": "medium",
                        "source_ref_ids": positive_refs,
                    }
                ],
                "relation_operations": [],
                "material_change": True,
            },
        }

    def complete(self, messages: Sequence[Mapping[str, str]]) -> Any:
        self.calls += 1
        system = messages[0]["content"]
        payload = json.loads(messages[-1]["content"])
        if system.startswith("你是 Memento Record Interpreter"):
            action = self._record_action(payload)
        elif system.startswith("你是 Memento Daily Integrator"):
            action = self._daily_action(payload)
        else:
            raise AssertionError("unexpected cognitive prompt")
        usage = {
            "prompt_tokens": 120,
            "completion_tokens": 60,
            "total_tokens": 180,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 120,
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
        return deepseek_provider.CompletionResult(
            content=_canonical(action),
            usage=usage,
            request_id=f"fake-cognitive-{self.calls}",
            model=self.model,
        )


class QueueAgentProvider:
    def __init__(self, actions: Sequence[Mapping[str, Any]], model: str) -> None:
        self.actions = [dict(row) for row in actions]
        self.model = model
        self.calls = 0

    def complete(self, messages: Sequence[Mapping[str, str]]) -> Any:
        del messages
        self.calls += 1
        if not self.actions:
            raise AssertionError("unexpected Agent provider call")
        usage = {
            "prompt_tokens": 180,
            "completion_tokens": 80,
            "total_tokens": 260,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 180,
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
        return deepseek_provider.CompletionResult(
            content=_canonical(self.actions.pop(0)),
            usage=usage,
            request_id=f"fake-agent-{self.calls}",
            model=self.model,
        )


ProviderBuilder = Callable[[str], Any]


def _real_builder(config: AcceptanceConfig) -> ProviderBuilder:
    def build(kind: str) -> Any:
        max_tokens = 3600 if kind == "cognitive" else 2400
        return deepseek_provider.DeepSeekProvider(
            model=config.model,
            timeout=config.timeout_seconds,
            thinking="disabled",
            reasoning_effort=None,
            max_tokens=max_tokens,
        )

    return build


def _write_day(vault: Path, local_date: str, rows: Sequence[tuple[str, str]]) -> Path:
    body = [f"---\ndate: {local_date}\ntype: memento-daily\n---\n"]
    for time_text, quote in rows:
        body.append(f"\n## {time_text} · 周一 · Live Acceptance\n\n{quote}\n\n---\n")
    path = vault / f"{local_date}.md"
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(body))
        handle.flush()
        os.fsync(handle.fileno())
    return path


def _exact_evidence(vault: Path, local_date: str, quote: str) -> dict[str, Any]:
    path = vault / f"{local_date}.md"
    lines = path.read_text(encoding="utf-8").splitlines()
    return {"file": path.name, "line": lines.index(quote) + 1, "quote": quote}


def _evidence_ref(item: Mapping[str, Any]) -> str:
    return "eref_" + core.sha256_bytes(core.canonical_json(dict(item)).encode("utf-8"))[:16]


def _finish_agent() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "finish",
        "reason_code": "insufficient_evidence",
        "arguments": {"reason": "insufficient_evidence"},
    }


def _agent_actions(vault: Path, local_date: str) -> list[dict[str, Any]]:
    if local_date != "2026-08-18":
        return [_finish_agent()]
    evidence = [
        _exact_evidence(vault, "2026-08-17", POSITIVE_QUOTE),
        _exact_evidence(vault, "2026-08-18", POSITIVE_QUOTE),
    ]
    return [
        {
            "schema_version": "1.0",
            "action": "investigate",
            "reason_code": "plan_evidence",
            "arguments": {
                "candidate_kind": "new",
                "target_memory_id": None,
                "queries": [
                    {
                        "query": "反例和失败条件",
                        "date_from": "2026-08-17",
                        "date_to": "2026-08-18",
                        "limit": 5,
                    }
                ],
            },
        },
        {
            "schema_version": "1.0",
            "action": "finalize_patch",
            "reason_code": "evidence_sufficient",
            "arguments": {
                "operation": "new",
                "target_memory_id": None,
                "expected_revision": 0,
                "title": "评审前先检查反例和失败条件",
                "statement": POSITIVE_QUOTE,
                "scope": "产品方案评审",
                "uncertainty": "medium",
                "evidence_refs": [_evidence_ref(row) for row in evidence],
                "counterevidence_refs": [],
            },
        },
    ]


class CaseRuntime:
    def __init__(
        self,
        vault: Path,
        meter: UsageMeter,
        config: AcceptanceConfig,
        provider_builder: ProviderBuilder | None,
    ) -> None:
        self.vault = vault
        self.meter = meter
        self.config = config
        self.provider_builder = provider_builder
        self.fake_cognitive = FakeCognitiveProvider(config.model)
        self.stage = "setup"
        self.agent_diagnostics: list[dict[str, Any]] = []

    def cognitive_provider(self) -> MeteredProvider:
        delegate = (
            self.provider_builder("cognitive")
            if self.provider_builder is not None
            else self.fake_cognitive
        )
        return MeteredProvider(delegate, self.meter)

    def agent_runner(self, selected_vault: Path, request_id: str) -> None:
        if selected_vault.resolve() != self.vault.resolve():
            raise core.ContractError("Agent Vault mismatch", kind="evidence")
        request, _, _ = agent_v1.load_agent_request(self.vault, request_id)
        if self.provider_builder is None:
            delegate = QueueAgentProvider(
                _agent_actions(self.vault, request["as_of"]), self.config.model
            )
        else:
            delegate = self.provider_builder("agent")
        material_diagnostic: dict[str, Any] | None = None

        def observe(messages: Sequence[Mapping[str, str]]) -> None:
            nonlocal material_diagnostic
            observed = _observe_agent_material(messages)
            if observed is not None:
                material_diagnostic = observed

        provider = MeteredProvider(
            delegate,
            self.meter,
            request_observer=observe,
        )
        response, _ = agent_v1.process_agent_request(
            self.vault,
            request_id,
            provider_client=provider,
            provider_name=(
                "mock-agentic-workflow"
                if self.provider_builder is None
                else AGENT_PROVIDER_NAME
            ),
            model=self.config.model,
            pricing=core.pricing_for_model(self.config.model),
            budget=agent_v1.AgentBudget(
                max_turns=5,
                max_tool_calls=5,
                max_total_tokens=40_000,
                max_prompt_chars=180_000,
            ),
            maximum_chars=180_000,
        )
        self.agent_diagnostics.append(
            _agent_diagnostic(
                request["as_of"], response, material_diagnostic=material_diagnostic
            )
        )
        if response["status"] not in {
            "updated",
            "no_change",
            "insufficient_evidence",
        }:
            raise core.ContractError("Agent terminal rejected", kind="quality_gate")

    def components(self, now: dt.datetime) -> tuple[Any, Any, Any, Any, Any]:
        provider_name = "fake-cognitive" if self.provider_builder is None else PROVIDER_NAME
        runtime = cognitive_runtime_v1.CognitiveRuntime(
            self.vault,
            self.cognitive_provider(),
            provider_name=provider_name,
            model=self.config.model,
            thinking="disabled",
            reasoning_effort=None,
            record_max_tokens=2400,
            daily_max_tokens=3600,
            clock=lambda: now,
        )
        actions = cognitive_actions_v1.CognitiveActionStore(
            self.vault, state_root=runtime.files.root
        )
        bundles = cognitive_bundle_store_v1.CognitiveBundleStore(
            self.vault, state_root=runtime.files.root
        )
        pipeline = cognitive_pipeline_v1.CognitivePipeline(
            self.vault,
            runtime=runtime,
            record_store=runtime.store,
            action_store=actions,
            bundle_store=bundles,
            clock=lambda: now,
        )
        publisher = cognitive_projection_v1.CognitiveProjectionPublisher(
            self.vault,
            record_store=runtime.store,
            action_store=actions,
            bundle_store=bundles,
            state_root=runtime.files.root,
        )
        schedule = {
            "enabled": False,
            "hour": 21,
            "minute": 0,
            "next_due_at": (now + dt.timedelta(days=1)).isoformat(timespec="seconds"),
            "last_run_status": "not_started",
        }

        def publish(date: str, statuses: Mapping[str, Mapping[str, Any]]) -> Any:
            return publisher.publish(
                local_date=date,
                schedule=schedule,
                record_runtime_statuses=statuses,
                now=now,
            )

        worker = cognitive_record_worker_v1.CognitiveRecordWorker(
            self.vault,
            runtime=runtime,
            record_store=runtime.store,
            action_store=actions,
            formal_store=bundles,
            home_projection_hook=publish,
            clock=lambda: now,
        )
        orchestrator = cognitive_day_orchestrator_v1.CognitiveDayOrchestrator(
            self.vault,
            pipeline=pipeline,
            bundle_store=bundles,
            agent_runner=self.agent_runner,
            schedule_loader=lambda _date, _now: schedule,
            clock=lambda: now,
        )
        return runtime, actions, bundles, worker, orchestrator


def _projection_checks(vault: Path, local_date: str) -> tuple[bool, dict[str, Any]]:
    root = vault / ".context-agent" / "cognitive-secretary-v1"
    home_path = root / "projections" / "home_projection.json"
    head_path = root / "projections" / "landscape-head.json"
    home = cognitive_v1.validate_home_projection(core.read_json(home_path))
    head = core.read_json(head_path)
    snapshot_path = root / "landscape-snapshots" / f"{head['snapshot_id']}.json"
    snapshot = cognitive_v1.validate_landscape_snapshot(core.read_json(snapshot_path))
    profile_refs = {
        (row["memory_id"], row["revision"], row["revision_sha256"])
        for row in agent_v1.build_agent_profile(vault)["memories"]
    }
    formal_store = cognitive_bundle_store_v1.CognitiveBundleStore(vault)
    memory_refs = {
        (row.memory_id, row.revision, row.sha256)
        for row in formal_store.list_active_memories()
    }
    projected_understandings = {
        (ref["id"], ref["revision"], ref["revision_sha256"])
        for row in home["records"]
        for ref in row["understanding_refs"]
    }
    projected_memories = {
        (ref["id"], ref["revision"], ref["revision_sha256"])
        for row in home["records"]
        for ref in row["memory_refs"]
    }
    peak_refs = {
        (
            row["understanding_ref"]["id"],
            row["understanding_ref"]["revision"],
            row["understanding_ref"]["revision_sha256"],
        )
        for row in snapshot["peaks"]
    }
    node_refs = {
        (
            row["memory_ref"]["id"],
            row["memory_ref"]["revision"],
            row["memory_ref"]["revision_sha256"],
        )
        for row in snapshot["nodes"]
    }
    valid = bool(
        home["local_date"] == local_date
        and home["landscape_ref"]["snapshot_id"] == snapshot["snapshot_id"]
        and home["landscape_ref"]["snapshot_sha256"]
        == cognitive_v1.persisted_sha256(snapshot)
        and projected_understandings.issubset(profile_refs)
        and projected_memories.issubset(memory_refs)
        and peak_refs == profile_refs
        and node_refs == memory_refs
    )
    return valid, {"home": home, "landscape": snapshot}


def _active_understanding_evidence(vault: Path) -> list[dict[str, Any]]:
    profile = agent_v1.build_agent_profile(vault)
    result: list[dict[str, Any]] = []
    for row in profile["memories"]:
        memory = agent_v1.validate_memory_revision(
            core.read_json(agent_v1._memory_path(vault, row["memory_id"], row["revision"])),
            vault,
            verify_sources=True,
        )
        result.extend(memory["evidence"])
        result.extend(memory["counterevidence"])
    return result


def _assert_public_case(item: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(item)
    if set(result) != PUBLIC_CASE_FIELDS:
        raise core.ContractError("case report allow-list mismatch", kind="runtime")
    if result["error_kind"] not in PUBLIC_ERROR_KINDS:
        raise core.ContractError("public error kind invalid", kind="runtime")
    diagnostics = result["agent_diagnostics"]
    if not isinstance(diagnostics, list) or len(diagnostics) > 4:
        raise core.ContractError("agent diagnostics invalid", kind="runtime")
    for diagnostic in diagnostics:
        if (
            not isinstance(diagnostic, dict)
            or set(diagnostic) != PUBLIC_AGENT_DIAGNOSTIC_FIELDS
        ):
            raise core.ContractError(
                "agent diagnostic allow-list mismatch", kind="runtime"
            )
        if diagnostic["response_status"] not in agent_v1.RESPONSE_STATUSES:
            raise core.ContractError("agent diagnostic status invalid", kind="runtime")
        if diagnostic["error_kind"] not in PUBLIC_ERROR_KINDS:
            raise core.ContractError(
                "agent diagnostic error kind invalid", kind="runtime"
            )
        for field in ("model_turns", "tool_calls", "history_matches"):
            if type(diagnostic[field]) is not int or diagnostic[field] < 0:
                raise core.ContractError(
                    "agent diagnostic counter invalid", kind="runtime"
                )
        for field in ("eligible_evidence_ref_count", "candidate_date_count"):
            if diagnostic[field] is not None and (
                type(diagnostic[field]) is not int or diagnostic[field] < 0
            ):
                raise core.ContractError(
                    "agent material diagnostic counter invalid", kind="runtime"
                )
        if (
            diagnostic["stable_identity_status"]
            not in PUBLIC_STABLE_IDENTITY_STATUSES
        ):
            raise core.ContractError(
                "agent stable identity diagnostic invalid", kind="runtime"
            )
        if diagnostic["structure_ready"] not in {True, False, None}:
            raise core.ContractError(
                "agent structure diagnostic invalid", kind="runtime"
            )
        requirement_codes = diagnostic["missing_requirement_codes"]
        if requirement_codes is not None and (
            not isinstance(requirement_codes, list)
            or requirement_codes != sorted(set(requirement_codes))
            or any(
                code not in PUBLIC_MISSING_REQUIREMENT_CODES
                for code in requirement_codes
            )
        ):
            raise core.ContractError(
                "agent missing requirement diagnostics invalid", kind="runtime"
            )
        counts = diagnostic["action_counts"]
        if not isinstance(counts, dict) or tuple(counts) != PUBLIC_AGENT_ACTIONS:
            raise core.ContractError(
                "agent diagnostic action counts invalid", kind="runtime"
            )
        if any(type(value) is not int or value < 0 for value in counts.values()):
            raise core.ContractError(
                "agent diagnostic action count invalid", kind="runtime"
            )
    return _assert_content_free(result)


def _observe_agent_material(
    messages: Sequence[Mapping[str, str]],
) -> dict[str, Any] | None:
    """Reduce an already-built production Workflow bundle to finite fields."""

    if not messages or not isinstance(messages[-1], Mapping):
        return None
    content = messages[-1].get("content")
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    candidate = payload.get("evidence_bundle", payload)
    if (
        not isinstance(candidate, Mapping)
        or candidate.get("workflow_phase") != "evidence_materialized"
    ):
        return None
    identity = candidate.get("stable_new_identity")
    identity = identity if isinstance(identity, Mapping) else {}
    status = identity.get("status", "not_applicable")
    if status not in PUBLIC_STABLE_IDENTITY_STATUSES - {"unavailable"}:
        return None
    eligible_refs = identity.get("eligible_evidence_refs", [])
    if not isinstance(eligible_refs, list) or any(
        not isinstance(item, str) for item in eligible_refs
    ):
        return None
    evidence_catalog = candidate.get("evidence_catalog")
    if not isinstance(evidence_catalog, list):
        return None
    candidate_dates: set[str] = set()
    for item in evidence_catalog:
        if not isinstance(item, Mapping):
            continue
        origins = item.get("origins")
        file = item.get("file")
        if (
            isinstance(origins, list)
            and any(
                origin in {"recent_candidate", "history_search"}
                for origin in origins
            )
            and isinstance(file, str)
            and agent_v1.DAILY_NAME_RE.fullmatch(file)
        ):
            candidate_dates.add(file)
    structure_ready = candidate.get("evidence_ready")
    if type(structure_ready) is not bool:
        return None
    missing = candidate.get("missing_requirements")
    if (
        not isinstance(missing, list)
        or any(code not in PUBLIC_MISSING_REQUIREMENT_CODES for code in missing)
    ):
        return None
    return {
        "stable_identity_status": status,
        "eligible_evidence_ref_count": len(eligible_refs),
        "candidate_date_count": len(candidate_dates),
        "structure_ready": structure_ready,
        "missing_requirement_codes": sorted(set(missing)),
    }


def _formal_agent_material_diagnostic(
    response: Mapping[str, Any],
) -> dict[str, Any]:
    """Reduce only an already committed formal memory to finite metadata.

    The formal response does not expose the transient evidence bundle when an
    Agent finishes without a commit.  In that case the harness reports
    ``unavailable`` instead of rematerializing evidence or reproducing the
    production Workflow's missing-requirement judgment.
    """

    unavailable = {
        "stable_identity_status": "unavailable",
        "eligible_evidence_ref_count": None,
        "candidate_date_count": None,
        "structure_ready": None,
        "missing_requirement_codes": None,
    }
    if response.get("status") != "updated":
        return unavailable
    memory = response.get("memory")
    if not isinstance(memory, Mapping):
        return unavailable
    provenance = memory.get("provenance")
    evidence = memory.get("evidence")
    if not isinstance(provenance, Mapping) or not isinstance(evidence, list):
        return unavailable
    safe_evidence = [item for item in evidence if isinstance(item, Mapping)]
    candidate_dates = {
        item.get("file")
        for item in safe_evidence
        if isinstance(item.get("file"), str)
        and agent_v1.DAILY_NAME_RE.fullmatch(item["file"])
    }
    operation = provenance.get("operation")
    if operation != "new":
        return {
            "stable_identity_status": "not_applicable",
            "eligible_evidence_ref_count": 0,
            "candidate_date_count": len(candidate_dates),
            "structure_ready": True,
            "missing_requirement_codes": [],
        }
    identity = agent_v1.derive_stable_new_identity(safe_evidence)
    status = identity.get("status")
    if status not in PUBLIC_STABLE_IDENTITY_STATUSES - {"unavailable"}:
        return unavailable
    eligible_count = (
        sum(
            1
            for item in safe_evidence
            if item.get("quote") == identity.get("statement")
        )
        if status == "stable"
        else 0
    )
    return {
        "stable_identity_status": status,
        "eligible_evidence_ref_count": eligible_count,
        "candidate_date_count": len(candidate_dates),
        # A committed formal memory has already passed the production
        # structural and evidence validators.  No transient bundle is rebuilt.
        "structure_ready": True,
        "missing_requirement_codes": [],
    }


def _agent_diagnostic(
    local_date: str,
    response: Mapping[str, Any],
    *,
    material_diagnostic: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    trace = response.get("trace") if isinstance(response, Mapping) else None
    trace = trace if isinstance(trace, Mapping) else {}
    action_counts = {name: 0 for name in PUBLIC_AGENT_ACTIONS}
    actions = trace.get("actions")
    if isinstance(actions, list):
        for action in actions:
            key = (
                action
                if action in action_counts and action != "unknown"
                else "unknown"
            )
            action_counts[key] += 1
    status = response.get("status")
    if status not in agent_v1.RESPONSE_STATUSES:
        status = "error"
    error_kind = response.get("error_kind")
    if error_kind is None:
        error_kind = "none"
    if error_kind not in PUBLIC_ERROR_KINDS:
        error_kind = "runtime"
    return {
        "local_date": local_date,
        "response_status": status,
        "error_kind": error_kind,
        "model_turns": trace.get("model_turns", 0),
        "tool_calls": trace.get("tool_calls", 0),
        "history_matches": trace.get("history_matches", 0),
        "action_counts": action_counts,
        **(
            dict(material_diagnostic)
            if material_diagnostic is not None
            else _formal_agent_material_diagnostic(response)
        ),
    }


def _case_result(
    case_id: str,
    *,
    meter_before: Mapping[str, Any],
    meter_after: Mapping[str, Any],
    source_before: str,
    source_after: str,
    checks: Mapping[str, bool],
    agent_diagnostics: Sequence[Mapping[str, Any]] = (),
    error_kind: str = "none",
    failure_stage: str = "postconditions",
) -> dict[str, Any]:
    calls = meter_after["calls"] - meter_before["calls"]
    tokens = meter_after["total_tokens"] - meter_before["total_tokens"]
    cost = round(meter_after["cost_usd"] - meter_before["cost_usd"], 10)
    if not set(checks).issubset(PUBLIC_CHECK_NAMES) or any(
        type(value) is not bool for value in checks.values()
    ):
        raise core.ContractError("case checks contain invalid fields", kind="runtime")
    failed_checks = sorted(name for name, value in checks.items() if not value)
    passed = bool(checks and not failed_checks and error_kind == "none")
    normalized_stage = "none" if passed else failure_stage
    if normalized_stage not in PUBLIC_FAILURE_STAGES:
        raise core.ContractError("case failure stage invalid", kind="runtime")
    return _assert_public_case(
        {
            "case": case_id,
            "status": "passed" if passed else "failed",
            "calls": calls,
            "tokens": tokens,
            "cost_usd": cost,
            "source_hash_before": _prefix(source_before),
            "source_hash_after": _prefix(source_after),
            "error_kind": error_kind if error_kind != "none" else (
                "none" if passed else "quality_gate"
            ),
            "failure_stage": normalized_stage,
            "failed_checks": failed_checks,
            "agent_diagnostics": [dict(row) for row in agent_diagnostics],
        }
    )


def _run_two_day_case(
    vault: Path,
    runtime: CaseRuntime,
) -> dict[str, Any]:
    _write_day(vault, "2026-08-17", [("09:10", POSITIVE_QUOTE)])
    _write_day(
        vault,
        "2026-08-18",
        [("09:20", POSITIVE_QUOTE), ("15:40", NEGATIVE_QUOTE)],
    )
    source_before = _source_digest(vault)
    usage_before = runtime.meter.public()
    stable: dict[str, tuple[str, ...]] = {}
    stable_receipts: dict[str, tuple[tuple[str, int, str], ...]] = {}
    receipts_complete = True
    bundle_complete = True
    review_complete = True
    long_term_terminal = True
    projection_complete = True
    warnings_empty = True
    final_orchestrator = None
    long_term_required = False
    for local_date in ("2026-08-17", "2026-08-18"):
        now = dt.datetime.fromisoformat(f"{local_date}T21:00:00+08:00")
        component_runtime, actions, _bundles, worker, orchestrator = runtime.components(now)
        runtime.stage = "record_worker"
        worker_result = worker.run(
            local_date=local_date, source_file=f"{local_date}.md", limit=8
        )
        if worker_result.status not in {"completed", "partial"}:
            raise core.ContractError("record worker failed", kind="quality_gate")
        heads = component_runtime.store.list_heads(local_date=local_date)
        stable[local_date] = tuple(row["record_id"] for row in heads)
        for row in heads:
            receipt_id = cognitive_v1.make_receipt_id(row["record_id"])
            try:
                receipt = actions.load_receipt_head(receipt_id)
            except core.ContractError:
                receipts_complete = False
                continue
            receipts_complete = receipts_complete and receipt.record_ref.id == row["record_id"]
        stable_receipts[local_date] = tuple(
            sorted(
                (
                    receipt.receipt_id,
                    receipt.revision,
                    receipt.sha256,
                )
                for row in heads
                for receipt in (
                    actions.load_receipt_head(cognitive_v1.make_receipt_id(row["record_id"])),
                )
            )
        )
        runtime.stage = "day_orchestrator"
        day_result = orchestrator.run_day(local_date, trigger="manual")
        if day_result.status not in {"committed", "committed_with_warnings", "no_change"}:
            raise core.ContractError("manual day workflow failed", kind="quality_gate")
        bundle_complete = bundle_complete and bool(day_result.bundle_ref)
        review_complete = review_complete and day_result.review_status in {
            "completed",
            "no_change",
            "recovered",
        }
        long_term_terminal = long_term_terminal and (
            day_result.long_term_status
            in {"completed", "already_linked", "recovered"}
            if day_result.long_term_required
            else day_result.long_term_status in {"no_material", "skipped"}
        )
        projection_complete = (
            projection_complete and day_result.projection_status == "completed"
        )
        warnings_empty = warnings_empty and not day_result.warnings
        final_orchestrator = orchestrator
        long_term_required = long_term_required or day_result.long_term_required
    calls_before_replay = runtime.meter.public()["calls"]
    assert final_orchestrator is not None
    runtime.stage = "replay"
    final_orchestrator.run_day("2026-08-18", trigger="manual")
    replay_zero = runtime.meter.public()["calls"] == calls_before_replay

    records = cognitive_store_v1.RecordStore(vault)
    stable_records = all(
        tuple(row["record_id"] for row in records.list_heads(local_date=date)) == ids
        for date, ids in stable.items()
    )
    final_actions = cognitive_actions_v1.CognitiveActionStore(vault)
    receipts_complete = receipts_complete and all(
        tuple(
            sorted(
                (
                    receipt.receipt_id,
                    receipt.revision,
                    receipt.sha256,
                )
                for row in records.list_heads(local_date=date)
                for receipt in (
                    final_actions.load_receipt_head(
                        cognitive_v1.make_receipt_id(row["record_id"])
                    ),
                )
            )
        )
        == expected
        for date, expected in stable_receipts.items()
    )
    evidence = _active_understanding_evidence(vault)
    active = agent_v1.build_agent_profile(vault)["memories"]
    exact_pairs = {(row["file"], row["quote"]) for row in evidence}
    positive_pairs = {
        ("2026-08-17.md", POSITIVE_QUOTE),
        ("2026-08-18.md", POSITIVE_QUOTE),
    }
    negative_excluded = all(row["quote"] != NEGATIVE_QUOTE for row in evidence)
    long_term_exact = (
        bool(active) and positive_pairs.issubset(exact_pairs)
        if long_term_required
        else not active
    )
    projection_valid, _ = _projection_checks(vault, "2026-08-18")
    source_after = _source_digest(vault)
    runtime.stage = "postconditions"
    checks = {
        "private_vault": _private_mode(vault, directory=True)
        and all(_private_mode(path, directory=False) for path in vault.glob("20??-??-??.md")),
        "source_sha_invariant": source_before == source_after,
        "stable_records": stable_records,
        "receipts_complete": receipts_complete,
        "bundle_complete": bundle_complete,
        "review_complete": review_complete,
        "long_term_terminal": long_term_terminal,
        "projection_complete": projection_complete,
        "warnings_empty": warnings_empty,
        "negative_excluded": negative_excluded,
        "long_term_evidence_exact": long_term_exact,
        "projection_refs_valid": projection_valid,
        "replay_zero_calls": replay_zero,
    }
    return _case_result(
        CASE_IDS[0],
        meter_before=usage_before,
        meter_after=runtime.meter.public(),
        source_before=source_before,
        source_after=source_after,
        checks=checks,
        agent_diagnostics=runtime.agent_diagnostics,
    )


def _run_original_only_case(vault: Path, runtime: CaseRuntime) -> dict[str, Any]:
    local_date = _current_local_date()
    now = dt.datetime.combine(
        dt.date.fromisoformat(local_date), dt.time(21, 0), tzinfo=TZ
    )
    _write_day(vault, local_date, [("10:10", POSITIVE_QUOTE)])
    source_before = _source_digest(vault)
    usage_before = runtime.meter.public()
    component_runtime, actions, bundles, worker, orchestrator = runtime.components(now)
    runtime.stage = "record_worker"
    worker.run(local_date=local_date, source_file=f"{local_date}.md", limit=8)
    runtime.stage = "day_orchestrator"
    orchestrator.run_day(local_date, trigger="manual")
    head = component_runtime.store.list_heads(local_date=local_date)[0]
    receipt = actions.load_receipt_head(cognitive_v1.make_receipt_id(head["record_id"]))
    receipt_ref = cognitive_v1.ObjectRef(
        "interpretation_receipt", receipt.receipt_id, receipt.revision, receipt.sha256
    )
    action = cognitive_v1.CognitiveUserAction(
        cognitive_v1.COGNITIVE_SCHEMA_VERSION,
        "memento_cognitive_user_action",
        cognitive_v1.make_cognitive_action_id("live-original-only"),
        (now + dt.timedelta(minutes=1)).isoformat(timespec="seconds"),
        "original_only",
        receipt_ref,
        None,
    )
    actions.submit_action(action)
    calls_before = runtime.meter.public()["calls"]
    runtime.stage = "action_worker"
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        action_worker_code = context_agent.main(
            ["cognitive-action-worker", "--once", "--vault", str(vault)]
        )
    calls_after = runtime.meter.public()["calls"]
    if action_worker_code != 0 or stderr.getvalue():
        raise core.ContractError("cognitive action worker failed", kind="quality_gate")
    action_worker_result = json.loads(stdout.getvalue())
    if action_worker_result.get("applied") != 1:
        raise core.ContractError("cognitive action was not applied", kind="quality_gate")
    projection_valid, projection = _projection_checks(vault, local_date)
    row = next(
        item
        for item in projection["home"]["records"]
        if item["record_ref"]["id"] == head["record_id"]
    )
    source_after = _source_digest(vault)
    derivatives_retracted = bool(
        row["status"] == "original_only"
        and not row["memory_refs"]
        and not row["understanding_refs"]
        and bundles.list_active_memories() == ()
        and bundles.list_active_relations() == ()
    )
    checks = {
        "private_vault": _private_mode(vault, directory=True),
        "source_sha_invariant": source_before == source_after,
        "projection_refs_valid": projection_valid,
        "original_only_zero_calls": calls_before == calls_after,
        "derivatives_retracted": derivatives_retracted,
        "original_preserved": (vault / f"{local_date}.md").is_file()
        and actions.load_receipt_head(receipt.receipt_id).status == "original_only",
    }
    runtime.stage = "postconditions"
    return _case_result(
        CASE_IDS[1],
        meter_before=usage_before,
        meter_after=runtime.meter.public(),
        source_before=source_before,
        source_after=source_after,
        checks=checks,
        agent_diagnostics=runtime.agent_diagnostics,
    )


def _error_kind(exc: BaseException) -> str:
    raw = getattr(exc, "kind", None) or getattr(exc, "code", None)
    if raw in PUBLIC_ERROR_KINDS:
        return str(raw)
    if isinstance(exc, deepseek_provider.ProviderError):
        return "provider"
    return "runtime"


def run_acceptance(
    config: AcceptanceConfig,
    *,
    live: bool,
    expected_plan_sha256: str,
    confirmation: str | None,
    provider_builder: ProviderBuilder | None = None,
) -> dict[str, Any]:
    config.validate()
    actual_plan = plan_sha256(config)
    if expected_plan_sha256 != actual_plan:
        raise ValueError("plan_mismatch")
    if live and confirmation != LIVE_CONFIRMATION:
        raise PermissionError("live_confirmation_mismatch")
    if live and "DEEPSEEK_API_KEY" in os.environ:
        raise PermissionError("live_key_source_mismatch")
    if not live and provider_builder is not None:
        raise ValueError("fake mode does not accept external provider")
    builder = (provider_builder or _real_builder(config)) if live else None
    meter = UsageMeter(config)
    results: list[dict[str, Any]] = []
    temporary_state = "cleanup_pending"
    temporary = tempfile.TemporaryDirectory(prefix="memento-cognitive-live-")
    scratch = Path(temporary.name)
    try:
        scratch.chmod(0o700)
        if not _private_mode(scratch, directory=True):
            raise core.ContractError("temporary directory is not private", kind="evidence")
        for index, case_id in enumerate(CASE_IDS):
            vault = scratch / f"case-{index + 1}"
            vault.mkdir(mode=0o700)
            agent_v1.enable_agent_v1(vault)
            runtime = CaseRuntime(vault, meter, config, builder)
            usage_before = meter.public()
            source_before = _source_digest(vault)
            try:
                if case_id == CASE_IDS[0]:
                    item = _run_two_day_case(vault, runtime)
                else:
                    item = _run_original_only_case(vault, runtime)
            except Exception as exc:
                source_after = _source_digest(vault)
                item = _case_result(
                    case_id,
                    meter_before=usage_before,
                    meter_after=meter.public(),
                    source_before=source_before,
                    source_after=source_after,
                    checks={},
                    agent_diagnostics=runtime.agent_diagnostics,
                    error_kind=_error_kind(exc),
                    failure_stage=(
                        runtime.stage
                        if runtime.stage in PUBLIC_FAILURE_STAGES
                        else "runtime"
                    ),
                )
            results.append(item)
            if item["status"] != "passed":
                break
    finally:
        temporary.cleanup()
        temporary_state = "cleaned" if not scratch.exists() else "cleanup_failed"
    all_passed = bool(
        len(results) == len(CASE_IDS)
        and all(row["status"] == "passed" for row in results)
        and temporary_state == "cleaned"
    )
    return _assert_content_free({
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "live_synthetic" if live else "fake_synthetic",
        "executed": bool(live and meter.public()["calls"] > 0),
        "status": "completed" if all_passed else "stopped",
        "error_kind": (
            "none"
            if all_passed
            else (results[-1]["error_kind"] if results else "runtime")
        ),
        "plan_sha256": actual_plan,
        "cases": results,
        "usage": meter.public(),
        "temporary_directory": temporary_state,
        "all_passed": all_passed,
    })


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--run-fake", action="store_true")
    modes.add_argument("--execute-live", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--expect-plan-sha256")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AcceptanceConfig()
    if not args.run_fake and not args.execute_live:
        print(_canonical(build_plan(config)))
        return 0
    if not args.expect_plan_sha256:
        raise SystemExit("execution requires --expect-plan-sha256")
    try:
        report = run_acceptance(
            config,
            live=bool(args.execute_live),
            expected_plan_sha256=args.expect_plan_sha256,
            confirmation=args.confirm,
        )
    except (PermissionError, ValueError) as exc:
        report = _assert_content_free(
            {
                "schema_version": REPORT_SCHEMA_VERSION,
                "mode": "live_synthetic" if args.execute_live else "fake_synthetic",
                "executed": False,
                "status": "stopped",
                "error_kind": (
                    "authorization" if isinstance(exc, PermissionError) else "conflict"
                ),
                "plan_sha256": plan_sha256(config),
                "cases": [],
                "usage": _empty_usage(),
                "temporary_directory": "not_created",
                "all_passed": False,
            }
        )
    print(_canonical(report))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
