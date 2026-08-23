#!/usr/bin/env python3
"""Focused real-DeepSeek acceptance for the Agentic Workflow MVP.

Default execution is plan-only and performs zero provider calls.  Live mode
uses four checked-in synthetic cases in private temporary Vaults.  It accepts
neither a user Vault nor an output path.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
CONTEXT_AGENT_ROOT = HERE.parents[1]
if str(CONTEXT_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(CONTEXT_AGENT_ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

agent_v1 = importlib.import_module("agent_v1")
core = importlib.import_module("core")
deepseek_provider = importlib.import_module("deepseek_provider")
pairing = importlib.import_module("run_live_pairing")
preflight = importlib.import_module("run_live_preflight")


REPORT_SCHEMA_VERSION = "remember_agent_workflow_mvp_live.v1"
PLAN_VERSION = "agentic-workflow-mvp-4case-v7"
PROVIDER_NAME = "deepseek-agentic-workflow"
ARM = "A1"
CASE_IDS = (
    "noise_stop",
    "repeated_new",
    "history_revise",
    "tombstone_protection",
)
CASE_SPECS = {
    "noise_stop": preflight.CASE_BY_ID["direct_stop"],
    "repeated_new": preflight.CASE_BY_ID["history_search_new"],
    "history_revise": preflight.CASE_BY_ID["history_search_revise"],
    "tombstone_protection": preflight.CASE_BY_ID["profile_only_reinforce"],
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    payload = value if isinstance(value, bytes) else _canonical(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclasses.dataclass(frozen=True)
class WorkflowLiveConfig:
    model: str = "deepseek-v4-pro"
    timeout: float = 120.0
    max_tokens_per_call: int = 3000
    max_batch_calls: int = 20
    max_batch_tokens: int = 200_000
    max_batch_cost_usd: float = 0.25
    budget: Any = dataclasses.field(
        default_factory=lambda: agent_v1.AgentBudget(
            max_turns=5,
            max_tool_calls=5,
            max_total_tokens=40_000,
            max_prompt_chars=180_000,
        )
    )

    def validate(self) -> "WorkflowLiveConfig":
        if self.model not in preflight.SUPPORTED_MODELS:
            raise ValueError("unsupported model")
        if not 1 <= self.timeout <= 300:
            raise ValueError("invalid timeout")
        if self.max_tokens_per_call != 3000:
            raise ValueError("invalid completion limit")
        if self.max_batch_calls != len(CASE_IDS) * self.budget.max_turns:
            raise ValueError("invalid call limit")
        if self.max_batch_tokens != 200_000:
            raise ValueError("invalid token limit")
        if self.max_batch_cost_usd != 0.25:
            raise ValueError("invalid cost limit")
        self.budget.validate()
        if self.budget.as_dict() != {
            "max_turns": 5,
            "max_tool_calls": 5,
            "max_total_tokens": 40_000,
            "max_prompt_chars": 180_000,
        }:
            raise ValueError("invalid Agent budget")
        return self


def _fixture_manifest() -> list[dict[str, str]]:
    rows: dict[str, str] = {}
    for case_id in CASE_IDS:
        spec = CASE_SPECS[case_id]
        root = preflight._case_source_root(spec)
        for filename in spec.source_files:
            key = f"{spec.fixture_set}/{filename}"
            rows[key] = core.sha256_file(root / filename)
    return [
        {"file": filename, "sha256": digest}
        for filename, digest in sorted(rows.items())
    ]


def _plan_payload(config: WorkflowLiveConfig) -> dict[str, Any]:
    config.validate()
    files = (
        Path(__file__),
        Path(agent_v1.__file__),
        Path(core.__file__),
        Path(deepseek_provider.__file__),
        Path(preflight.__file__),
        Path(pairing.__file__),
    )
    return {
        "version": PLAN_VERSION,
        "schema_version": REPORT_SCHEMA_VERSION,
        "cases": list(CASE_IDS),
        "provider": PROVIDER_NAME,
        "model": config.model,
        "thinking": "disabled",
        "timeout_seconds": config.timeout,
        "max_tokens_per_call": config.max_tokens_per_call,
        "budget": config.budget.as_dict(),
        "limits": {
            "calls": config.max_batch_calls,
            "tokens": config.max_batch_tokens,
            "cost_usd": config.max_batch_cost_usd,
        },
        "policy_sha256": agent_v1.make_agent_policy_sha256(
            provider=PROVIDER_NAME,
            model=config.model,
            budget=config.budget,
        ),
        "source_files": [
            {"name": path.name, "sha256": pairing._secure_source_file_sha256(path)}
            for path in files
        ],
        "fixtures": _fixture_manifest(),
        "quality_contract": {
            "noise_stop": "finish_no_change_no_write",
            "repeated_new": "investigate_then_exact_new_revision",
            "history_revise": "investigate_history_then_structural_grounded_revise_revision",
            "tombstone_protection": "no_resurrection_or_revision_after_tombstone",
            "source_files_unchanged": True,
            "usage_complete": True,
        },
    }


def plan_sha256(config: WorkflowLiveConfig) -> str:
    return _sha(_plan_payload(config))


def build_plan(config: WorkflowLiveConfig) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "plan_only",
        "executed": False,
        "status": "planned",
        "plan_sha256": plan_sha256(config),
        "plan": _plan_payload(config),
        "runs": [],
        "batch": {
            "calls": 0,
            "tokens": 0,
            "cost_usd": 0.0,
            "cost_complete": True,
        },
    }


class _MeterConfig:
    def __init__(self, config: WorkflowLiveConfig) -> None:
        self.model = config.model
        self.budget = config.budget
        self.max_tokens_per_call = config.max_tokens_per_call
        self.max_batch_calls = config.max_batch_calls
        self.max_batch_tokens = config.max_batch_tokens
        self.max_batch_cost_usd = config.max_batch_cost_usd


ProviderFactory = Callable[[str, WorkflowLiveConfig], Any]


def default_provider_factory(case_id: str, config: WorkflowLiveConfig) -> Any:
    del case_id
    return deepseek_provider.DeepSeekProvider(
        model=config.model,
        timeout=config.timeout,
        thinking="disabled",
        reasoning_effort=None,
        max_tokens=config.max_tokens_per_call,
    )


def _tombstone_seed(vault: Path, spec: Any) -> tuple[str, str]:
    seed = preflight._seed_memory(vault, spec.seed_key)
    memory_id = seed["memory_id"]
    revision_one = core.sha256_file(agent_v1._memory_path(vault, memory_id, 1))
    agent_v1.tombstone_memory(
        vault,
        memory_id,
        expected_revision=1,
        user_action_id="uact_" + "d" * 24,
        created_at="2026-07-24T20:00:00+08:00",
    )
    return memory_id, revision_one


def _stored_memory(vault: Path, response: Mapping[str, Any]) -> Mapping[str, Any] | None:
    memory = response.get("memory")
    if not isinstance(memory, Mapping):
        return None
    return agent_v1.validate_memory_revision(
        core.read_json(
            agent_v1._memory_path(vault, memory["memory_id"], memory["revision"])
        ),
        vault,
        verify_sources=True,
    )


def _quality_checks(
    vault: Path,
    case_id: str,
    spec: Any,
    response: Mapping[str, Any],
    run: Mapping[str, Any],
    baseline_sources: Mapping[str, str],
    tombstone_memory_id: str | None,
) -> dict[str, bool]:
    diagnostics: dict[str, bool] = {}
    sources_preserved = preflight._source_hashes(vault, spec) == baseline_sources
    usage_complete = bool(
        response["usage"]["usage_missing"] is False
        and response["usage"]["model_calls"] >= 1
        and response["usage"]["cost_usd"] is not None
    )
    audit_bound = bool(
        run["provider"] == PROVIDER_NAME
        and run["policy_sha256"]
        == agent_v1.make_agent_policy_sha256(
            provider=PROVIDER_NAME,
            model=run["model"],
            budget=agent_v1.AgentBudget(**run["budget"]),
        )
        and [step["action"] for step in agent_v1._public_run_steps(run["steps"])]
        == response["trace"]["actions"]
    )
    stored = _stored_memory(vault, response)
    if case_id == "noise_stop":
        task = bool(
            response["status"] == "no_change"
            and response["memory"] is None
            and response["trace"]["actions"] == ["finish"]
        )
    elif case_id in {"repeated_new", "history_revise"}:
        expected_operation = "new" if case_id == "repeated_new" else "revise"
        expected_revision = 1 if case_id == "repeated_new" else 2
        expected_evidence = preflight._expected_evidence(vault, spec.expected_evidence)
        expected_counter = preflight._expected_evidence(
            vault, spec.expected_counterevidence
        )
        public_steps = agent_v1._public_run_steps(run["steps"])
        actions = response["trace"]["actions"]
        latest_evidence_file = (
            max(item["file"] for item in stored["evidence"])
            if stored is not None and stored["evidence"]
            else None
        )
        statement_grounded_current = bool(
            stored is not None
            and latest_evidence_file is not None
            and any(
                item["file"] == latest_evidence_file
                and item["quote"] == stored["statement"]
                for item in stored["evidence"]
            )
        )
        explicit_change_signal_present = bool(
            stored is not None
            and agent_v1._has_explicit_signal(
                list(stored["evidence"]) + list(stored["counterevidence"]),
                agent_v1.EXPLICIT_CHANGE_EVIDENCE_PATTERNS,
            )
        )
        evidence_order_valid = bool(
            stored is not None
            and stored["evidence"]
            and stored["counterevidence"]
            and min(item["file"] for item in stored["evidence"])
            > max(item["file"] for item in stored["counterevidence"])
        )
        trajectory_ok = bool(
            len(actions) >= 2
            and actions[0] == "investigate"
            and actions[-1] == "finalize_patch"
            and public_steps[0]["result_kind"] == "investigation_materialized"
            and public_steps[-1]["result_kind"] == "memory_updated"
        )
        repair_count = 0
        for index, action_name in enumerate(actions[1:-1], start=1):
            step = public_steps[index]
            if action_name == "search_history":
                trajectory_ok = trajectory_ok and bool(
                    step["result_kind"] == "history_matches"
                    and step["error_kind"] is None
                )
            elif action_name == "finalize_patch":
                repair_count += 1
                trajectory_ok = trajectory_ok and bool(
                    repair_count == 1
                    and step["result_kind"] == "rejected"
                    and step["error_kind"] is not None
                )
            else:
                trajectory_ok = False
        task = bool(
            response["status"] == "updated"
            and response["trace"]["history_matches"] >= 1
            and stored is not None
            and stored["operation"] == expected_operation
            and stored["revision"] == expected_revision
            and (
                stored["statement"] == spec.expected_statement
                if case_id == "repeated_new"
                else statement_grounded_current
            )
            and stored["scope"] == spec.expected_scope
            and (
                all(item in stored["evidence"] for item in expected_evidence)
                if case_id == "repeated_new"
                else explicit_change_signal_present
                and evidence_order_valid
            )
            and all(
                item in stored["counterevidence"] for item in expected_counter
            )
        )
        diagnostics = {
            "strict_trajectory": trajectory_ok,
            "memory_updated": response["status"] == "updated" and stored is not None,
            "operation_expected": bool(
                stored is not None and stored["operation"] == expected_operation
            ),
            "revision_expected": bool(
                stored is not None and stored["revision"] == expected_revision
            ),
            "statement_expected": bool(
                stored is not None and stored["statement"] == spec.expected_statement
            ),
            "statement_grounded_current": statement_grounded_current,
            "explicit_change_signal_present": explicit_change_signal_present,
            "evidence_order_valid": evidence_order_valid,
            "scope_expected": bool(
                stored is not None and stored["scope"] == spec.expected_scope
            ),
            "required_evidence_present": bool(
                stored is not None
                and all(item in stored["evidence"] for item in expected_evidence)
            ),
            "required_counterevidence_present": bool(
                stored is not None
                and all(
                    item in stored["counterevidence"]
                    for item in expected_counter
                )
            ),
        }
    else:
        histories, _, _ = agent_v1._load_memory_histories(vault)
        history = histories.get(tombstone_memory_id or "", [])
        task = bool(
            tombstone_memory_id is not None
            and len(history) == 2
            and history[-1]["status"] == "tombstone"
            and response["memory"] is None
            and response["status"]
            in {"no_change", "insufficient_evidence", "error"}
            and (response["status"] != "error" or response["error_kind"] == "tombstone")
            and not build_active_ids(vault)
        )
    return {
        "task_expected": task,
        "sources_preserved": sources_preserved,
        "usage_complete": usage_complete,
        "audit_bound": audit_bound,
        **diagnostics,
    }


def build_active_ids(vault: Path) -> list[str]:
    return [
        item["memory_id"] for item in agent_v1.build_agent_profile(vault)["memories"]
    ]


def run_live(
    config: WorkflowLiveConfig,
    *,
    expected_plan_sha256: str,
    provider_factory: ProviderFactory | None = None,
) -> dict[str, Any]:
    config.validate()
    actual_plan = plan_sha256(config)
    if expected_plan_sha256 != actual_plan:
        raise ValueError("plan_mismatch")
    pricing = core.pricing_for_model(config.model)
    meter = pairing.BatchMeter(_MeterConfig(config), pricing)
    factory = provider_factory or default_provider_factory
    results: list[dict[str, Any]] = []
    stop_code = "none"
    with pairing.secure_batch_scratch() as scratch:
        for case_id in CASE_IDS:
            spec = CASE_SPECS[case_id]
            try:
                meter.ensure_arm_capacity(ARM)
                with preflight.isolated_case_vault(scratch, spec) as vault:
                    agent_v1.enable_agent_v1(vault)
                    baseline = preflight._source_hashes(vault, spec)
                    tombstone_memory_id = None
                    if case_id == "tombstone_protection":
                        tombstone_memory_id, _ = _tombstone_seed(vault, spec)
                    delegate = factory(case_id, config)
                    provider = pairing.MeteredProvider(delegate, meter, ARM)
                    request = preflight._create_request(vault, spec, "A1")
                    response, _ = agent_v1.process_agent_request(
                        vault,
                        request["id"],
                        provider_client=provider,
                        provider_name=PROVIDER_NAME,
                        model=config.model,
                        pricing=pricing,
                        budget=config.budget,
                        maximum_chars=config.budget.max_prompt_chars,
                    )
                    run = agent_v1.validate_agent_run(
                        core.read_json(
                            agent_v1.run_path(
                                vault, agent_v1.make_run_id(request["id"])
                            )
                        )
                    )
                    checks = _quality_checks(
                        vault,
                        case_id,
                        spec,
                        response,
                        run,
                        baseline,
                        tombstone_memory_id,
                    )
                    results.append(
                        {
                            "case": case_id,
                            "status": response["status"],
                            "error_kind": response["error_kind"],
                            "trajectory": list(response["trace"]["actions"]),
                            "checks": checks,
                            "passed": all(
                                checks[field]
                                for field in (
                                    "task_expected",
                                    "sources_preserved",
                                    "usage_complete",
                                    "audit_bound",
                                )
                            ),
                            "usage": dict(response["usage"]),
                        }
                    )
                if meter.halted_code is not None:
                    stop_code = meter.halted_code
                    break
            except Exception as exc:
                stop_code = getattr(exc, "code", None) or getattr(
                    exc, "kind", None
                ) or "runtime"
                break
    all_passed = bool(
        len(results) == len(CASE_IDS)
        and all(item["passed"] for item in results)
        and stop_code == "none"
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "live_synthetic",
        "executed": meter.calls > 0,
        "status": "completed" if all_passed else "stopped",
        "stop_code": stop_code if stop_code != "none" else (
            "none" if all_passed else "quality_gate"
        ),
        "plan_sha256": actual_plan,
        "runs": results,
        "batch": meter.public(),
        "all_passed": all_passed,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--expect-plan-sha256")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = WorkflowLiveConfig()
    if not args.live:
        print(json.dumps(build_plan(config), ensure_ascii=False, sort_keys=True))
        return 0
    if not args.expect_plan_sha256:
        raise SystemExit("live mode requires --expect-plan-sha256")
    report = run_live(config, expected_plan_sha256=args.expect_plan_sha256)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
