#!/usr/bin/env python3
"""Minimal A1-only live gate before Remember Agent V1 manual enablement.

The default mode only prints a frozen plan and performs no provider call.  A
live run is limited to two checked-in synthetic cases, each cloned into the
reviewed preflight runner's private scratch area.  This runner accepts neither
an arbitrary Vault nor an output path.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib
import json
import math
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
pairing = importlib.import_module("run_live_pairing")
preflight = importlib.import_module("run_live_preflight")


REPORT_SCHEMA_VERSION = "remember_agent_live_manual_gate.v3"
MATRIX_VERSION = "remember-agent-manual-enable-gate-v3"
LIVE_CONFIRMATION = "LIVE_SYNTHETIC_MANUAL_GATE_ONLY"
COST_CONFIRMATION = "ACCEPT_MANUAL_GATE_PROVIDER_COST"
CASE_IDS = ("history_search_revise", "revision_conflict")
CASES = tuple(preflight.CASE_BY_ID[case_id] for case_id in CASE_IDS)
ARM = "A1"
PUBLIC_ERROR_CODES = preflight.PUBLIC_ERROR_CODES
REQUIRED_CHECKS = {
    "history_search_revise": (
        "outcome_expected",
        "trajectory_expected",
        "operation_expected",
        "revision_expected",
        "statement_exact",
        "scope_exact",
        "tool_contract_valid",
        "evidence_exact",
        "counterevidence_exact",
        "source_hashes_exact",
        "cas_preserved",
        "usage_complete",
        "audit_clean",
        "source_clone_unchanged",
    ),
    "revision_conflict": (
        "outcome_expected",
        "trajectory_expected",
        "tool_contract_valid",
        "cas_preserved",
        "stale_no_agent_write",
        "old_revision_preserved",
        "user_action_wins",
        "usage_complete",
        "audit_clean",
        "source_clone_unchanged",
    ),
}
AUTONOMY_CLASSIFICATION_BY_REFUSAL_COUNT = {
    0: "unassisted",
    1: "guarded",
    2: "scaffolded",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    payload = value if isinstance(value, bytes) else _canonical(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ManualGateAbort(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        executed: bool = False,
        batch: Mapping[str, Any] | None = None,
    ) -> None:
        if code not in PUBLIC_ERROR_CODES:
            code = "contract"
        super().__init__(code)
        self.code = code
        self.executed = executed is True
        self.batch = dict(batch) if isinstance(batch, Mapping) else None


@dataclasses.dataclass(frozen=True)
class ManualGateConfig:
    model: str = "deepseek-v4-pro"
    timeout: float = 60.0
    max_tokens_per_call: int = 2000
    max_batch_calls: int = 9
    max_batch_tokens: int = 100_000
    max_batch_cost_usd: float = 0.10
    budget: Any = dataclasses.field(
        # v1.9 reached 12,096 cumulative tokens on its fourth call.  Because
        # the runtime stops only above the configured limit, 12,096 is the
        # smallest value that admits that observed completion; the fifth call
        # remains unknown.  20,000 is an engineering threshold for this
        # five-turn gate [猜测], not a mathematical guarantee for every path.
        default_factory=lambda: agent_v1.AgentBudget(
            max_turns=5, max_total_tokens=20_000
        )
    )

    def validate(self) -> "ManualGateConfig":
        if self.model not in preflight.SUPPORTED_MODELS:
            raise ManualGateAbort("contract")
        if type(self.timeout) not in {int, float} or not 1 <= self.timeout <= 300:
            raise ManualGateAbort("contract")
        for value, minimum, maximum in (
            (self.max_tokens_per_call, 1, 20_000),
            (self.max_batch_calls, 9, 9),
            (self.max_batch_tokens, 1, 100_000),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ManualGateAbort("contract")
        if (
            type(self.max_batch_cost_usd) not in {int, float}
            or not 0 < self.max_batch_cost_usd <= 0.10
            or not math.isfinite(float(self.max_batch_cost_usd))
        ):
            raise ManualGateAbort("contract")
        self.budget.validate()
        if self.budget.max_turns != 5:
            raise ManualGateAbort("contract")
        return self


@dataclasses.dataclass(frozen=True)
class FrozenContract:
    matrix_sha256: str
    runner_source_sha256: str
    preflight_contract_sha256: str
    preflight_runner_source_sha256: str
    preflight_runner_runtime_sha256: str
    preflight_dependency_manifest_sha256: str
    policy_sha256: str


def _base_config(config: ManualGateConfig) -> Any:
    # The canonical six-case config remains a dependency only.  Keep its
    # reviewed three-turn contract fixed while the manual gate's production
    # A1 controller gets independent pre-read and post-read review turns.
    preflight_budget = agent_v1.AgentBudget()
    return preflight.PreflightConfig(
        model=config.model,
        timeout=config.timeout,
        max_tokens_per_call=config.max_tokens_per_call,
        max_batch_calls=30,
        max_batch_tokens=config.max_batch_tokens,
        max_batch_cost_usd=config.max_batch_cost_usd,
        budget=preflight_budget,
    )


def _case_call_reservation(spec: Any) -> int:
    """Reserve the reviewed path plus both bounded finish-review calls."""

    return len(spec.a1_trajectory) + 2


def _ensure_case_capacity(meter: Any, spec: Any) -> None:
    required = _case_call_reservation(spec)
    if meter.calls + required > meter.config.max_batch_calls:
        meter._abort("call_limit")


def _matrix_manifest() -> dict[str, Any]:
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "version": MATRIX_VERSION,
        "arm": ARM,
        "cases": [dataclasses.asdict(spec) for spec in CASES],
        "execution_order": list(CASE_IDS),
        "failure_policy": "fail_closed_after_each_case",
        "ideal_calls": sum(len(spec.a1_trajectory) for spec in CASES),
        "legal_max_calls": sum(_case_call_reservation(spec) for spec in CASES),
        "case_call_reservations": {
            spec.case_id: _case_call_reservation(spec) for spec in CASES
        },
        "required_checks": {
            case_id: list(checks) for case_id, checks in REQUIRED_CHECKS.items()
        },
        "a1_tool_acceptance": preflight._a1_tool_acceptance_manifest(),
        "conflict_injection": "after_terminal_completion_before_commit",
        "live_confirmation_sha256": _sha(LIVE_CONFIRMATION.encode("utf-8")),
        "cost_confirmation_sha256": _sha(COST_CONFIRMATION.encode("utf-8")),
    }


def freeze_contract(config: ManualGateConfig) -> FrozenContract:
    config.validate()
    base = preflight.freeze_contract(_base_config(config))
    return FrozenContract(
        matrix_sha256=_sha(_matrix_manifest()),
        runner_source_sha256=pairing._secure_source_file_sha256(Path(__file__)),
        preflight_contract_sha256=base.contract_sha256,
        preflight_runner_source_sha256=base.runner_source_sha256,
        preflight_runner_runtime_sha256=base.runner_runtime_sha256,
        preflight_dependency_manifest_sha256=base.dependency_manifest_sha256,
        policy_sha256=agent_v1.make_agent_policy_sha256(
            provider="deepseek", model=config.model, budget=config.budget
        ),
    )


def _assert_frozen(config: ManualGateConfig, frozen: FrozenContract) -> None:
    if freeze_contract(config) != frozen:
        raise ManualGateAbort("security")


def _frozen_public(
    config: ManualGateConfig, frozen: FrozenContract
) -> dict[str, Any]:
    return {
        "matrix_version": MATRIX_VERSION,
        "cases": list(CASE_IDS),
        "arm": ARM,
        "execution_order": list(CASE_IDS),
        "failure_policy": "fail_closed_after_each_case",
        "ideal_calls": sum(len(spec.a1_trajectory) for spec in CASES),
        "legal_max_calls": sum(_case_call_reservation(spec) for spec in CASES),
        "case_call_reservations": {
            spec.case_id: _case_call_reservation(spec) for spec in CASES
        },
        "required_checks": {
            case_id: list(checks) for case_id, checks in REQUIRED_CHECKS.items()
        },
        "a1_tool_acceptance": preflight._a1_tool_acceptance_manifest(),
        "provider": "deepseek",
        "model": config.model,
        "thinking": "disabled",
        "timeout_seconds": config.timeout,
        "max_tokens_per_call": config.max_tokens_per_call,
        "budget": config.budget.as_dict(),
        "matrix_sha256": frozen.matrix_sha256,
        "runner_source_sha256": frozen.runner_source_sha256,
        "preflight_contract_sha256": frozen.preflight_contract_sha256,
        "preflight_runner_source_sha256": frozen.preflight_runner_source_sha256,
        "preflight_runner_runtime_sha256": frozen.preflight_runner_runtime_sha256,
        "preflight_dependency_manifest_sha256": (
            frozen.preflight_dependency_manifest_sha256
        ),
        "policy_sha256": frozen.policy_sha256,
    }


def plan_sha256(config: ManualGateConfig, frozen: FrozenContract) -> str:
    return _sha(
        {
            "frozen": _frozen_public(config, frozen),
            "limits": {
                "max_batch_calls": config.max_batch_calls,
                "max_batch_tokens": config.max_batch_tokens,
                "max_batch_cost_usd": config.max_batch_cost_usd,
                "fail_closed": True,
            },
        }
    )


def _empty_batch() -> dict[str, Any]:
    return {
        "calls": 0,
        "tokens": 0,
        "cost_usd": 0.0,
        "cost_complete": True,
        "a1": {"calls": 0, "tokens": 0, "cost_usd": 0.0},
    }


def _public_batch(meter: Any) -> dict[str, Any]:
    value = meter.public()
    return {
        "calls": value["calls"],
        "tokens": value["tokens"],
        "cost_usd": value["cost_usd"],
        "cost_complete": value["cost_complete"],
        "a1": dict(value["by_arm"][ARM]),
    }


def build_plan(config: ManualGateConfig) -> dict[str, Any]:
    frozen = freeze_contract(config)
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "plan_only",
        "executed": False,
        "status": "planned",
        "stop_code": "none",
        "plan_sha256": plan_sha256(config, frozen),
        "frozen": _frozen_public(config, frozen),
        "limits": {
            "max_batch_calls": config.max_batch_calls,
            "max_batch_tokens": config.max_batch_tokens,
            "max_batch_cost_usd": config.max_batch_cost_usd,
            "fail_closed": True,
        },
        "credential": {
            "lookup_deferred_until_provider_call": True,
            "environment_or_macos_keychain": True,
            "persisted_in_report": False,
        },
        "runs": [],
        "summary": {
            "cases_requested": len(CASES),
            "cases_completed": 0,
            "gate_passed": None,
            "batch": _empty_batch(),
        },
    }
    validate_public_report(report)
    return report


ProviderFactory = Callable[[str, str, ManualGateConfig], Any]


def _selected_quality(
    spec: Any,
    full_quality: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    full_checks = full_quality["checks"]
    checks = {
        name: full_checks[name] is True
        for name in REQUIRED_CHECKS[spec.case_id]
    }
    expected = preflight._expected_trajectory(spec, ARM)
    trajectory = raw.get("trajectory")
    refusal_count = raw.get("bounded_finish_refusal_count", 0)
    if (
        isinstance(trajectory, list)
        and trajectory == ["finish", *expected]
        and refusal_count == 1
        and raw.get("pre_read_finish_refusal") is True
        and raw.get("post_read_finish_refusal") is False
    ):
        # Preserve the already reviewed v2 pre-read allowance.  Post-read and
        # dual-review paths are reported as bounded task outcomes below but do
        # not silently become release-gate passes before an explicit review.
        checks["trajectory_expected"] = True
    return {
        "passed": all(checks.values()),
        "score": sum(checks.values()) / len(checks),
        "checks": checks,
    }


def _reviewed_trajectory(
    expected: Sequence[str], *, pre_read: bool, post_read: bool
) -> list[str] | None:
    trajectory = list(expected)
    if post_read:
        if not trajectory or trajectory[0] != "read_memory":
            return None
        trajectory.insert(1, "finish")
    if pre_read:
        trajectory.insert(0, "finish")
    return trajectory


def _task_passed(
    spec: Any,
    full_quality: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> bool:
    """Verify task success separately from release-gate eligibility."""

    expected = preflight._expected_trajectory(spec, ARM)
    trajectory = raw.get("trajectory")
    refusal_count = raw.get("bounded_finish_refusal_count", 0)
    pre_read = raw.get("pre_read_finish_refusal") is True
    post_read = raw.get("post_read_finish_refusal") is True
    reviewed = _reviewed_trajectory(
        expected, pre_read=pre_read, post_read=post_read
    )
    checks = full_quality["checks"]
    return bool(
        isinstance(trajectory, list)
        and reviewed is not None
        and trajectory == reviewed
        and refusal_count == int(pre_read) + int(post_read)
        and all(
            checks[name] is True
            for name in REQUIRED_CHECKS[spec.case_id]
            if name != "trajectory_expected"
        )
    )


def _safe_error_code(exc: BaseException) -> str:
    if isinstance(exc, ManualGateAbort):
        return exc.code
    return preflight._safe_error_code(exc)


def run_live_manual_gate(
    config: ManualGateConfig,
    *,
    expected_plan_sha256: str,
    provider_factory: ProviderFactory | None = None,
) -> dict[str, Any]:
    config.validate()
    frozen = freeze_contract(config)
    actual_plan = plan_sha256(config, frozen)
    if (
        not isinstance(expected_plan_sha256, str)
        or len(expected_plan_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_plan_sha256)
        or expected_plan_sha256 != actual_plan
    ):
        raise ManualGateAbort("plan_mismatch")
    meter = preflight.PreflightMeter(config, core.pricing_for_model(config.model))
    runs: list[dict[str, Any]] = []
    cases_completed = 0
    stop_code = "none"
    try:
        with pairing.secure_batch_scratch() as scratch:
            for spec in CASES:
                _assert_frozen(config, frozen)
                _ensure_case_capacity(meter, spec)
                with preflight.isolated_case_vault(scratch, spec) as vault:
                    _assert_frozen(config, frozen)
                    seed = preflight._seed_memory(vault, spec.seed_key)
                    seed_path = agent_v1._memory_path(vault, seed["memory_id"], 1)
                    seed_sha = core.sha256_file(seed_path)
                    factory = provider_factory
                    if factory is None:
                        # Resolve the canonical factory from the preflight
                        # module whose runtime identity is already frozen.
                        factory = preflight._resolve_local_symbol(
                            "default_provider_factory"
                        )
                    if not callable(factory):
                        raise ManualGateAbort("security")
                    delegate = factory(spec.case_id, ARM, config)
                    if spec.conflict:
                        delegate = preflight.ConflictDelegate(
                            delegate, vault, seed["memory_id"], ARM
                        )
                    provider = pairing.MeteredProvider(delegate, meter, ARM)
                    raw = preflight._agent_run(vault, provider, config, spec, ARM)
                    full_quality = preflight._quality(
                        vault, spec, ARM, raw, seed_sha
                    )
                    quality = _selected_quality(spec, full_quality, raw)
                    task_passed = _task_passed(
                        spec, full_quality, raw
                    )
                    runs.append(
                        {
                            "case": spec.case_id,
                            "arm": ARM,
                            "status": raw["status"],
                            "error_code": raw["error_code"],
                            "trajectory": list(raw["trajectory"]),
                            "expected_trajectory": preflight._expected_trajectory(
                                spec, ARM
                            ),
                            "autonomy_classification": raw[
                                "autonomy_classification"
                            ],
                            "finish_reviews": {
                                "pre_read": raw["pre_read_finish_refusal"],
                                "post_read": raw["post_read_finish_refusal"],
                                "count": raw["bounded_finish_refusal_count"],
                            },
                            "task_passed": task_passed,
                            "quality": quality,
                            "usage": dict(raw["usage"]),
                        }
                    )
                    if raw["error_code"] != "none":
                        stop_code = raw["error_code"]
                    elif not quality["passed"]:
                        stop_code = "quality_gate"
                if stop_code != "none" or meter.halted_code is not None:
                    stop_code = meter.halted_code or stop_code
                    break
                cases_completed += 1
    except Exception as exc:
        code = _safe_error_code(exc)
        if meter.calls > 0:
            raise ManualGateAbort(
                code, executed=True, batch=_public_batch(meter)
            ) from exc
        raise ManualGateAbort(code) from exc
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "live_synthetic_manual_gate",
        "executed": True,
        "status": "completed" if stop_code == "none" else "stopped",
        "stop_code": stop_code,
        "plan_sha256": actual_plan,
        "frozen": _frozen_public(config, frozen),
        "limits": {
            "max_batch_calls": config.max_batch_calls,
            "max_batch_tokens": config.max_batch_tokens,
            "max_batch_cost_usd": config.max_batch_cost_usd,
            "fail_closed": True,
        },
        "credential": {
            "lookup_deferred_until_provider_call": True,
            "environment_or_macos_keychain": True,
            "persisted_in_report": False,
        },
        "runs": runs,
        "summary": {
            "cases_requested": len(CASES),
            "cases_completed": cases_completed,
            "gate_passed": bool(
                stop_code == "none"
                and len(runs) == len(CASES)
                and all(run["quality"]["passed"] for run in runs)
            ),
            "batch": _public_batch(meter),
        },
    }
    try:
        validate_public_report(report)
    except Exception as exc:
        raise ManualGateAbort(
            "security", executed=True, batch=_public_batch(meter)
        ) from exc
    return report


def validate_public_report(report: Mapping[str, Any]) -> None:
    required_top = {
        "schema_version",
        "mode",
        "executed",
        "status",
        "stop_code",
        "plan_sha256",
        "frozen",
        "limits",
        "credential",
        "runs",
        "summary",
    }
    if not isinstance(report, Mapping) or set(report) != required_top:
        raise ManualGateAbort("security")
    if report["schema_version"] != REPORT_SCHEMA_VERSION:
        raise ManualGateAbort("security")
    if report["mode"] not in {"plan_only", "live_synthetic_manual_gate"}:
        raise ManualGateAbort("security")
    if report["stop_code"] not in PUBLIC_ERROR_CODES:
        raise ManualGateAbort("security")
    runs = report["runs"]
    summary = report["summary"]
    if not isinstance(runs, list) or not isinstance(summary, Mapping):
        raise ManualGateAbort("security")
    if [run.get("case") for run in runs] != list(CASE_IDS[: len(runs)]):
        raise ManualGateAbort("security")
    for run in runs:
        if set(run) != {
            "case",
            "arm",
            "status",
            "error_code",
            "trajectory",
            "expected_trajectory",
            "autonomy_classification",
            "finish_reviews",
            "task_passed",
            "quality",
            "usage",
        }:
            raise ManualGateAbort("security")
        if (
            run["arm"] != ARM
            or run["error_code"] not in PUBLIC_ERROR_CODES
            or run["autonomy_classification"]
            not in {"unassisted", "guarded", "scaffolded"}
            or type(run["task_passed"]) is not bool
            or not isinstance(run["trajectory"], list)
            or not isinstance(run["expected_trajectory"], list)
            or any(
                action not in agent_v1.AGENT_ACTIONS
                for action in [*run["trajectory"], *run["expected_trajectory"]]
            )
        ):
            raise ManualGateAbort("security")
        finish_reviews = run["finish_reviews"]
        if (
            not isinstance(finish_reviews, Mapping)
            or set(finish_reviews) != {"pre_read", "post_read", "count"}
            or type(finish_reviews["pre_read"]) is not bool
            or type(finish_reviews["post_read"]) is not bool
            or type(finish_reviews["count"]) is not int
            or finish_reviews["count"]
            != int(finish_reviews["pre_read"]) + int(finish_reviews["post_read"])
            or not 0 <= finish_reviews["count"] <= 2
        ):
            raise ManualGateAbort("security")
        quality = run["quality"]
        expected_names = set(REQUIRED_CHECKS[run["case"]])
        if (
            not isinstance(quality, Mapping)
            or set(quality) != {"passed", "score", "checks"}
            or not isinstance(quality["checks"], Mapping)
            or set(quality["checks"]) != expected_names
            or any(type(value) is not bool for value in quality["checks"].values())
            or quality["passed"] is not all(quality["checks"].values())
        ):
            raise ManualGateAbort("security")
        expected_classification = AUTONOMY_CLASSIFICATION_BY_REFUSAL_COUNT[
            finish_reviews["count"]
        ]
        reviewed_trajectory = _reviewed_trajectory(
            run["expected_trajectory"],
            pre_read=finish_reviews["pre_read"],
            post_read=finish_reviews["post_read"],
        )
        expected_task_passed = bool(
            reviewed_trajectory is not None
            and run["trajectory"] == reviewed_trajectory
            and all(
                value is True
                for name, value in quality["checks"].items()
                if name != "trajectory_expected"
            )
        )
        if (
            run["autonomy_classification"] != expected_classification
            or run["task_passed"] is not expected_task_passed
        ):
            raise ManualGateAbort("security")
    expected_gate = (
        len(runs) == len(CASES)
        and all(run["quality"]["passed"] for run in runs)
        and report["stop_code"] == "none"
    )
    if report["mode"] == "plan_only":
        if (
            report["executed"] is not False
            or report["status"] != "planned"
            or runs
            or summary.get("gate_passed") is not None
            or summary.get("batch") != _empty_batch()
        ):
            raise ManualGateAbort("security")
    elif (
        report["executed"] is not True
        or summary.get("gate_passed") is not expected_gate
        or report["status"] != ("completed" if expected_gate else "stopped")
    ):
        raise ManualGateAbort("security")


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ManualGateAbort("contract")


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        description="A1-only synthetic gate before manual Agent V1 enablement"
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live")
    parser.add_argument("--confirm-cost")
    parser.add_argument("--expect-plan-sha256")
    parser.add_argument(
        "--model", choices=preflight.SUPPORTED_MODELS, default="deepseek-v4-pro"
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-tokens-per-call", type=int, default=2000)
    parser.add_argument("--max-batch-calls", type=int, default=9)
    parser.add_argument("--max-batch-tokens", type=int, default=100_000)
    parser.add_argument("--max-batch-cost-usd", type=float, default=0.10)
    return parser


def _emergency_report(
    *, live: bool, code: str, executed: bool, batch: Mapping[str, Any] | None
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "live_synthetic_manual_gate" if live else "plan_only",
        "executed": executed is True,
        "status": "stopped",
        "stop_code": code if code in PUBLIC_ERROR_CODES else "runtime",
    }
    if executed and isinstance(batch, Mapping):
        report["summary"] = {"batch": dict(batch)}
    return report


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if any(
        item in {"--output", "--vault"}
        or item.startswith("--output=")
        or item.startswith("--vault=")
        for item in raw_argv
    ):
        print(
            _canonical(
                _emergency_report(
                    live="--live" in raw_argv,
                    code="contract",
                    executed=False,
                    batch=None,
                )
            ),
            file=sys.stderr,
        )
        return 2
    try:
        args = build_parser().parse_args(raw_argv)
        config = ManualGateConfig(
            model=args.model,
            timeout=args.timeout,
            max_tokens_per_call=args.max_tokens_per_call,
            max_batch_calls=args.max_batch_calls,
            max_batch_tokens=args.max_batch_tokens,
            max_batch_cost_usd=args.max_batch_cost_usd,
        )
        confirmed = (
            args.confirm_live == LIVE_CONFIRMATION
            and args.confirm_cost == COST_CONFIRMATION
        )
        if args.live != confirmed:
            raise ManualGateAbort("confirmation_required")
        report = (
            run_live_manual_gate(
                config, expected_plan_sha256=args.expect_plan_sha256 or ""
            )
            if args.live
            else build_plan(config)
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if report["status"] in {"planned", "completed"} else 1
    except ManualGateAbort as exc:
        print(
            _canonical(
                _emergency_report(
                    live="--live" in raw_argv,
                    code=exc.code,
                    executed=exc.executed,
                    batch=exc.batch,
                )
            ),
            file=sys.stderr,
        )
        return 2
    except Exception:
        print(
            _canonical(
                _emergency_report(
                    live="--live" in raw_argv,
                    code="runtime",
                    executed=False,
                    batch=None,
                )
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
