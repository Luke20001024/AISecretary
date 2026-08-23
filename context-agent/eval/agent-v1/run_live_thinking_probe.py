#!/usr/bin/env python3
"""Paired, single-fixture DeepSeek thinking diagnostic for Remember Agent V1.

The default mode is plan-only and never constructs a provider.  A live run is
limited to two isolated clones of the checked-in ``history_search_revise``
fixture: first thinking disabled, then thinking enabled at high effort.  This
is a diagnostic probe, not the manual release gate.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib
import inspect
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


REPORT_SCHEMA_VERSION = "remember_agent_live_thinking_probe.v1"
MATRIX_VERSION = "remember-agent-thinking-probe-v1"
LIVE_CONFIRMATION = "LIVE_SYNTHETIC_THINKING_PROBE_ONLY"
COST_CONFIRMATION = "ACCEPT_THINKING_PROBE_PROVIDER_COST"
CASE_ID = "history_search_revise"
SPEC = preflight.CASE_BY_ID[CASE_ID]
ARMS = ("disabled", "thinking_high")
ARM_CONFIGS = {
    "disabled": {"thinking": "disabled", "reasoning_effort": None},
    "thinking_high": {"thinking": "enabled", "reasoning_effort": "high"},
}
EXPECTED_TRAJECTORY = tuple(SPEC.a1_trajectory)
PUBLIC_ERROR_CODES = preflight.PUBLIC_ERROR_CODES
QUALITY_CHECKS = tuple(sorted((*preflight.QUALITY_FIELDS, "bounded_finish_absent")))
USAGE_FIELDS = frozenset(
    {
        "model_calls",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "reasoning_tokens",
        "usage_missing",
        "cost_usd",
    }
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    payload = value if isinstance(value, bytes) else _canonical(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


class ThinkingProbeAbort(RuntimeError):
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
class ThinkingProbeConfig:
    model: str = "deepseek-v4-pro"
    timeout: float = 60.0
    max_tokens_per_call: int = 2000
    max_batch_calls: int = 8
    max_batch_tokens: int = 30_000
    max_batch_cost_usd: float = 0.03
    budget: Any = dataclasses.field(
        default_factory=lambda: agent_v1.AgentBudget(max_turns=4)
    )

    def validate(self) -> "ThinkingProbeConfig":
        if self.model != "deepseek-v4-pro":
            raise ThinkingProbeAbort("contract")
        if type(self.timeout) not in {int, float} or not 1 <= self.timeout <= 300:
            raise ThinkingProbeAbort("contract")
        if (
            self.max_tokens_per_call != 2000
            or self.max_batch_calls != 8
            or self.max_batch_tokens != 30_000
            or type(self.max_batch_cost_usd) not in {int, float}
            or not math.isclose(float(self.max_batch_cost_usd), 0.03)
        ):
            raise ThinkingProbeAbort("contract")
        self.budget.validate()
        if self.budget != agent_v1.AgentBudget(max_turns=4):
            raise ThinkingProbeAbort("contract")
        return self


@dataclasses.dataclass(frozen=True)
class FrozenContract:
    matrix_sha256: str
    targeted_fixture_sha256: str
    runner_source_sha256: str
    runner_runtime_sha256: str
    provider_source_sha256: str
    provider_runtime_sha256: str
    preflight_contract_sha256: str
    preflight_runner_source_sha256: str
    preflight_runner_runtime_sha256: str
    preflight_dependency_manifest_sha256: str
    prompt_version: str
    policy_sha256: str


def _base_config(config: ThinkingProbeConfig) -> Any:
    # Preserve the reviewed three-turn six-case dependency contract.  The
    # focused probe itself keeps the production four-turn bounded controller.
    dependency_budget = agent_v1.AgentBudget(
        max_turns=3,
        max_tool_calls=config.budget.max_tool_calls,
        max_total_tokens=config.budget.max_total_tokens,
        max_prompt_chars=config.budget.max_prompt_chars,
    )
    return preflight.PreflightConfig(
        model=config.model,
        timeout=config.timeout,
        max_tokens_per_call=config.max_tokens_per_call,
        max_batch_calls=30,
        max_batch_tokens=config.max_batch_tokens,
        max_batch_cost_usd=config.max_batch_cost_usd,
        budget=dependency_budget,
    )


def _targeted_fixture_manifest() -> dict[str, Any]:
    source_root = preflight._case_source_root(SPEC)
    seed = preflight._seed_memory(source_root, SPEC.seed_key)
    return {
        "case": dataclasses.asdict(SPEC),
        "source_hashes": preflight._source_hashes(source_root, SPEC),
        "seed_sha256": _sha(seed),
    }


def _pricing_manifest(config: ThinkingProbeConfig) -> dict[str, Any]:
    return dataclasses.asdict(core.pricing_for_model(config.model))


def _matrix_manifest(config: ThinkingProbeConfig) -> dict[str, Any]:
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "version": MATRIX_VERSION,
        "case_id": CASE_ID,
        "arms": list(ARMS),
        "arm_configs": {arm: dict(ARM_CONFIGS[arm]) for arm in ARMS},
        "execution_order": list(ARMS),
        "expected_trajectory": list(EXPECTED_TRAJECTORY),
        "bounded_finish_refusal_allowed": False,
        "diagnostic_only": True,
        "release_gate": False,
        "failure_policy": "continue_on_quality_stop_on_integrity",
        "quality_checks": list(QUALITY_CHECKS),
        "pricing": _pricing_manifest(config),
        "live_confirmation_sha256": _sha(LIVE_CONFIRMATION.encode("utf-8")),
        "cost_confirmation_sha256": _sha(COST_CONFIRMATION.encode("utf-8")),
    }


def _runtime_sha256() -> str:
    module = sys.modules.get(__name__)
    if module is None:
        raise ThinkingProbeAbort("security")
    return pairing._module_namespace_sha256(module)


def freeze_contract(config: ThinkingProbeConfig) -> FrozenContract:
    config.validate()
    base = preflight.freeze_contract(_base_config(config))
    provider_module = preflight.deepseek_provider
    return FrozenContract(
        matrix_sha256=_sha(_matrix_manifest(config)),
        targeted_fixture_sha256=_sha(_targeted_fixture_manifest()),
        runner_source_sha256=pairing._secure_source_file_sha256(Path(__file__)),
        runner_runtime_sha256=_runtime_sha256(),
        provider_source_sha256=pairing._secure_source_file_sha256(
            Path(provider_module.__file__)
        ),
        provider_runtime_sha256=pairing._module_namespace_sha256(provider_module),
        preflight_contract_sha256=base.contract_sha256,
        preflight_runner_source_sha256=base.runner_source_sha256,
        preflight_runner_runtime_sha256=base.runner_runtime_sha256,
        preflight_dependency_manifest_sha256=base.dependency_manifest_sha256,
        prompt_version=agent_v1.AGENT_PROMPT_VERSION,
        policy_sha256=agent_v1.make_agent_policy_sha256(
            provider="deepseek", model=config.model, budget=config.budget
        ),
    )


def _assert_frozen(config: ThinkingProbeConfig, frozen: FrozenContract) -> None:
    if freeze_contract(config) != frozen:
        raise ThinkingProbeAbort("security")


def _frozen_public(
    config: ThinkingProbeConfig, frozen: FrozenContract
) -> dict[str, Any]:
    return {
        "matrix_version": MATRIX_VERSION,
        "case_id": CASE_ID,
        "arms": list(ARMS),
        "arm_configs": {arm: dict(ARM_CONFIGS[arm]) for arm in ARMS},
        "execution_order": list(ARMS),
        "diagnostic_only": True,
        "release_gate": False,
        "strict_expected_trajectory": list(EXPECTED_TRAJECTORY),
        "bounded_finish_refusal_allowed": False,
        "failure_policy": "continue_on_quality_stop_on_integrity",
        "provider": "deepseek",
        "model": config.model,
        "endpoint": preflight.deepseek_provider.DEFAULT_ENDPOINT,
        "timeout_seconds": config.timeout,
        "max_tokens_per_call": config.max_tokens_per_call,
        "budget": config.budget.as_dict(),
        "pricing": _pricing_manifest(config),
        "quality_checks": list(QUALITY_CHECKS),
        "matrix_sha256": frozen.matrix_sha256,
        "targeted_fixture_sha256": frozen.targeted_fixture_sha256,
        "runner_source_sha256": frozen.runner_source_sha256,
        "runner_runtime_sha256": frozen.runner_runtime_sha256,
        "provider_source_sha256": frozen.provider_source_sha256,
        "provider_runtime_sha256": frozen.provider_runtime_sha256,
        "preflight_contract_sha256": frozen.preflight_contract_sha256,
        "preflight_runner_source_sha256": frozen.preflight_runner_source_sha256,
        "preflight_runner_runtime_sha256": frozen.preflight_runner_runtime_sha256,
        "preflight_dependency_manifest_sha256": (
            frozen.preflight_dependency_manifest_sha256
        ),
        "prompt_version": frozen.prompt_version,
        "policy_sha256": frozen.policy_sha256,
    }


def plan_sha256(config: ThinkingProbeConfig, frozen: FrozenContract) -> str:
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


class ProbeMeter(pairing.BatchMeter):
    """Global paired meter keyed by treatment rather than release-gate arm."""

    def __init__(self, config: ThinkingProbeConfig, pricing: Any) -> None:
        super().__init__(config, pricing)
        self.by_arm = {
            arm: {"calls": 0, "tokens": 0, "cost_usd": 0.0} for arm in ARMS
        }

    def ensure_arm_capacity(self, arm: str) -> None:
        if arm not in ARMS:
            self._abort("security")
        if self.calls + self.config.budget.max_turns > self.config.max_batch_calls:
            self._abort("call_limit")


class _A1MeterView:
    """Present one treatment bucket as A1 to the reviewed preflight runner."""

    def __init__(self, meter: ProbeMeter, treatment: str) -> None:
        if treatment not in ARMS:
            raise ThinkingProbeAbort("security")
        self._meter = meter
        self._treatment = treatment

    @property
    def config(self) -> ThinkingProbeConfig:
        return self._meter.config

    @property
    def by_arm(self) -> dict[str, dict[str, Any]]:
        return {"A1": self._meter.by_arm[self._treatment]}

    @property
    def usage_complete(self) -> bool:
        return self._meter.usage_complete

    @property
    def halted_code(self) -> str | None:
        return self._meter.halted_code

    @halted_code.setter
    def halted_code(self, value: str | None) -> None:
        self._meter.halted_code = value

    def before_call(self, arm: str, messages: Sequence[Mapping[str, str]]) -> None:
        if arm != "A1":
            self._meter._abort("security")
        self._meter.before_call(self._treatment, messages)

    def observe(self, arm: str, usage: Mapping[str, Any] | None) -> None:
        if arm != "A1":
            self._meter._abort("security")
        self._meter.observe(self._treatment, usage)

    def observe_unpriced(self, arm: str, usage: Mapping[str, Any] | None) -> None:
        if arm != "A1":
            self._meter._abort("security")
        self._meter.observe_unpriced(self._treatment, usage)


ProviderFactory = Callable[[str, ThinkingProbeConfig], Any]


def default_provider_factory(arm: str, config: ThinkingProbeConfig) -> Any:
    if arm not in ARMS:
        raise ThinkingProbeAbort("security")
    treatment = ARM_CONFIGS[arm]
    # Credential lookup remains inside complete(); construction and plan-only
    # mode do not read the environment or macOS Keychain.
    return preflight.deepseek_provider.DeepSeekProvider(
        model=config.model,
        timeout=config.timeout,
        thinking=treatment["thinking"],
        reasoning_effort=treatment["reasoning_effort"],
        max_tokens=config.max_tokens_per_call,
    )


def _resolve_default_provider_factory() -> ProviderFactory:
    module = sys.modules.get(__name__)
    if module is None:
        raise ThinkingProbeAbort("security")
    factory = inspect.getattr_static(module, "default_provider_factory", None)
    if not callable(factory):
        raise ThinkingProbeAbort("security")
    return factory


def _strict_quality(full_quality: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    checks = dict(full_quality["checks"])
    checks["bounded_finish_absent"] = raw.get("bounded_finish_refusal") is False
    if set(checks) != set(QUALITY_CHECKS):
        raise ThinkingProbeAbort("security")
    return {
        "passed": all(checks.values()),
        "score": sum(checks.values()) / len(checks),
        "checks": {name: checks[name] is True for name in QUALITY_CHECKS},
    }


def _empty_batch() -> dict[str, Any]:
    return {
        "calls": 0,
        "tokens": 0,
        "cost_usd": 0.0,
        "cost_complete": True,
        "by_arm": {
            arm: {"calls": 0, "tokens": 0, "cost_usd": 0.0} for arm in ARMS
        },
    }


def _public_batch(meter: ProbeMeter) -> dict[str, Any]:
    value = meter.public()
    return {
        "calls": value["calls"],
        "tokens": value["tokens"],
        "cost_usd": value["cost_usd"],
        "cost_complete": value["cost_complete"],
        "by_arm": {arm: dict(value["by_arm"][arm]) for arm in ARMS},
    }


def _contrast(runs: Sequence[Mapping[str, Any]]) -> str:
    if len(runs) != len(ARMS):
        return "incomplete"
    passed = {run["arm"]: run["quality"]["passed"] is True for run in runs}
    if passed["disabled"] and passed["thinking_high"]:
        return "both_pass"
    if passed["thinking_high"]:
        return "thinking_only_pass"
    if passed["disabled"]:
        return "disabled_only_pass"
    return "neither_pass"


def build_plan(config: ThinkingProbeConfig) -> dict[str, Any]:
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
            "arms_requested": len(ARMS),
            "arms_completed": 0,
            "paired_complete": False,
            "thinking_autonomy_observed": None,
            "contrast": "not_run",
            "batch": _empty_batch(),
        },
    }
    validate_public_report(report)
    return report


def _safe_error_code(exc: BaseException) -> str:
    if isinstance(exc, ThinkingProbeAbort):
        return exc.code
    return preflight._safe_error_code(exc)


def run_live_thinking_probe(
    config: ThinkingProbeConfig,
    *,
    expected_plan_sha256: str,
    provider_factory: ProviderFactory | None = None,
) -> dict[str, Any]:
    config.validate()
    frozen = freeze_contract(config)
    actual_plan = plan_sha256(config, frozen)
    if not _is_sha256(expected_plan_sha256) or expected_plan_sha256 != actual_plan:
        raise ThinkingProbeAbort("plan_mismatch")

    meter = ProbeMeter(config, core.pricing_for_model(config.model))
    runs: list[dict[str, Any]] = []
    completed = 0
    stop_code = "none"
    try:
        with pairing.secure_batch_scratch() as scratch:
            for arm in ARMS:
                _assert_frozen(config, frozen)
                meter.ensure_arm_capacity(arm)
                with preflight.isolated_case_vault(scratch, SPEC) as vault:
                    # Bind the exact runner, provider, fixture and policy once
                    # more after clone materialization and before any provider
                    # can be constructed.
                    _assert_frozen(config, frozen)
                    seed = preflight._seed_memory(vault, SPEC.seed_key)
                    seed_sha = core.sha256_file(
                        agent_v1._memory_path(vault, seed["memory_id"], 1)
                    )
                    factory = provider_factory or _resolve_default_provider_factory()
                    delegate = factory(arm, config)
                    if not hasattr(delegate, "complete"):
                        raise ThinkingProbeAbort("security")
                    meter_view = _A1MeterView(meter, arm)
                    provider = pairing.MeteredProvider(delegate, meter_view, "A1")
                    raw = preflight._agent_run(vault, provider, config, SPEC, "A1")
                    full_quality = preflight._quality(
                        vault, SPEC, "A1", raw, seed_sha
                    )
                    quality = _strict_quality(full_quality, raw)
                    treatment = ARM_CONFIGS[arm]
                    runs.append(
                        {
                            "arm": arm,
                            "thinking": treatment["thinking"],
                            "reasoning_effort": treatment["reasoning_effort"],
                            "status": raw["status"],
                            "error_code": raw["error_code"],
                            "trajectory": list(raw["trajectory"]),
                            "expected_trajectory": list(EXPECTED_TRAJECTORY),
                            "bounded_finish_refusal": raw[
                                "bounded_finish_refusal"
                            ],
                            "quality": quality,
                            "usage": dict(raw["usage"]),
                        }
                    )
                    if raw["error_code"] != "none":
                        stop_code = raw["error_code"]
                    else:
                        completed += 1
                _assert_frozen(config, frozen)
                if stop_code != "none" or meter.halted_code is not None:
                    stop_code = meter.halted_code or stop_code
                    break
    except Exception as exc:
        code = _safe_error_code(exc)
        if meter.calls > 0:
            raise ThinkingProbeAbort(
                code, executed=True, batch=_public_batch(meter)
            ) from exc
        raise ThinkingProbeAbort(code) from exc

    paired_complete = bool(stop_code == "none" and len(runs) == len(ARMS))
    thinking_run = next(
        (run for run in runs if run["arm"] == "thinking_high"), None
    )
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "live_synthetic_thinking_probe",
        "executed": True,
        "status": "completed" if paired_complete else "stopped",
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
            "arms_requested": len(ARMS),
            "arms_completed": completed,
            "paired_complete": paired_complete,
            "thinking_autonomy_observed": (
                thinking_run["quality"]["passed"]
                if paired_complete and thinking_run is not None
                else None
            ),
            "contrast": _contrast(runs),
            "batch": _public_batch(meter),
        },
    }
    try:
        validate_public_report(report)
    except Exception as exc:
        raise ThinkingProbeAbort(
            "security", executed=True, batch=_public_batch(meter)
        ) from exc
    return report


def _validate_usage(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != USAGE_FIELDS:
        raise ThinkingProbeAbort("security")
    for name in USAGE_FIELDS - {"usage_missing", "cost_usd"}:
        if type(value[name]) is not int or value[name] < 0:
            raise ThinkingProbeAbort("security")
    if type(value["usage_missing"]) is not bool:
        raise ThinkingProbeAbort("security")
    if value["cost_usd"] is not None and (
        type(value["cost_usd"]) not in {int, float}
        or not math.isfinite(float(value["cost_usd"]))
        or value["cost_usd"] < 0
    ):
        raise ThinkingProbeAbort("security")


def _validate_batch(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "calls",
        "tokens",
        "cost_usd",
        "cost_complete",
        "by_arm",
    }:
        raise ThinkingProbeAbort("security")
    if (
        type(value["calls"]) is not int
        or not 0 <= value["calls"] <= 8
        or type(value["tokens"]) is not int
        or not 0 <= value["tokens"] <= 30_000
        or type(value["cost_usd"]) not in {int, float}
        or not 0 <= float(value["cost_usd"]) <= 0.03
        or type(value["cost_complete"]) is not bool
        or not isinstance(value["by_arm"], Mapping)
        or set(value["by_arm"]) != set(ARMS)
    ):
        raise ThinkingProbeAbort("security")
    for arm in ARMS:
        bucket = value["by_arm"][arm]
        if not isinstance(bucket, Mapping) or set(bucket) != {
            "calls",
            "tokens",
            "cost_usd",
        }:
            raise ThinkingProbeAbort("security")
        if (
            type(bucket["calls"]) is not int
            or not 0 <= bucket["calls"] <= 4
            or type(bucket["tokens"]) is not int
            or bucket["tokens"] < 0
            or type(bucket["cost_usd"]) not in {int, float}
            or bucket["cost_usd"] < 0
        ):
            raise ThinkingProbeAbort("security")


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
        raise ThinkingProbeAbort("security")
    if (
        report["schema_version"] != REPORT_SCHEMA_VERSION
        or report["mode"]
        not in {"plan_only", "live_synthetic_thinking_probe"}
        or report["stop_code"] not in PUBLIC_ERROR_CODES
        or not _is_sha256(report["plan_sha256"])
    ):
        raise ThinkingProbeAbort("security")
    frozen = report["frozen"]
    frozen_fields = {
        "matrix_version",
        "case_id",
        "arms",
        "arm_configs",
        "execution_order",
        "diagnostic_only",
        "release_gate",
        "strict_expected_trajectory",
        "bounded_finish_refusal_allowed",
        "failure_policy",
        "provider",
        "model",
        "endpoint",
        "timeout_seconds",
        "max_tokens_per_call",
        "budget",
        "pricing",
        "quality_checks",
        "matrix_sha256",
        "targeted_fixture_sha256",
        "runner_source_sha256",
        "runner_runtime_sha256",
        "provider_source_sha256",
        "provider_runtime_sha256",
        "preflight_contract_sha256",
        "preflight_runner_source_sha256",
        "preflight_runner_runtime_sha256",
        "preflight_dependency_manifest_sha256",
        "prompt_version",
        "policy_sha256",
    }
    if not isinstance(frozen, Mapping) or set(frozen) != frozen_fields:
        raise ThinkingProbeAbort("security")
    if (
        frozen["matrix_version"] != MATRIX_VERSION
        or frozen["case_id"] != CASE_ID
        or frozen["arms"] != list(ARMS)
        or frozen["execution_order"] != list(ARMS)
        or frozen["arm_configs"]
        != {arm: dict(ARM_CONFIGS[arm]) for arm in ARMS}
        or frozen["diagnostic_only"] is not True
        or frozen["release_gate"] is not False
        or frozen["strict_expected_trajectory"] != list(EXPECTED_TRAJECTORY)
        or frozen["bounded_finish_refusal_allowed"] is not False
        or frozen["quality_checks"] != list(QUALITY_CHECKS)
        or frozen["provider"] != "deepseek"
        or frozen["model"] != "deepseek-v4-pro"
        or frozen["max_tokens_per_call"] != 2000
    ):
        raise ThinkingProbeAbort("security")
    sha_fields = {name for name in frozen_fields if name.endswith("_sha256")}
    if any(not _is_sha256(frozen[name]) for name in sha_fields):
        raise ThinkingProbeAbort("security")
    if report["limits"] != {
        "max_batch_calls": 8,
        "max_batch_tokens": 30_000,
        "max_batch_cost_usd": 0.03,
        "fail_closed": True,
    }:
        raise ThinkingProbeAbort("security")
    if report["credential"] != {
        "lookup_deferred_until_provider_call": True,
        "environment_or_macos_keychain": True,
        "persisted_in_report": False,
    }:
        raise ThinkingProbeAbort("security")
    current_config = ThinkingProbeConfig()
    current_frozen = freeze_contract(current_config)
    if (
        frozen != _frozen_public(current_config, current_frozen)
        or report["plan_sha256"] != plan_sha256(current_config, current_frozen)
    ):
        raise ThinkingProbeAbort("security")

    runs = report["runs"]
    summary = report["summary"]
    if not isinstance(runs, list) or not isinstance(summary, Mapping):
        raise ThinkingProbeAbort("security")
    if [run.get("arm") for run in runs] != list(ARMS[: len(runs)]):
        raise ThinkingProbeAbort("security")
    for run in runs:
        if set(run) != {
            "arm",
            "thinking",
            "reasoning_effort",
            "status",
            "error_code",
            "trajectory",
            "expected_trajectory",
            "bounded_finish_refusal",
            "quality",
            "usage",
        }:
            raise ThinkingProbeAbort("security")
        arm = run["arm"]
        treatment = ARM_CONFIGS[arm]
        quality = run["quality"]
        if (
            run["thinking"] != treatment["thinking"]
            or run["reasoning_effort"] != treatment["reasoning_effort"]
            or run["error_code"] not in PUBLIC_ERROR_CODES
            or not isinstance(run["trajectory"], list)
            or len(run["trajectory"]) > 4
            or run["expected_trajectory"] != list(EXPECTED_TRAJECTORY)
            or type(run["bounded_finish_refusal"]) is not bool
            or not isinstance(quality, Mapping)
            or set(quality) != {"passed", "score", "checks"}
            or not isinstance(quality["checks"], Mapping)
            or set(quality["checks"]) != set(QUALITY_CHECKS)
            or any(type(value) is not bool for value in quality["checks"].values())
            or type(quality["passed"]) is not bool
            or quality["passed"] != all(quality["checks"].values())
            or type(quality["score"]) not in {int, float}
            or not math.isclose(
                float(quality["score"]),
                sum(quality["checks"].values()) / len(QUALITY_CHECKS),
            )
        ):
            raise ThinkingProbeAbort("security")
        _validate_usage(run["usage"])

    if set(summary) != {
        "arms_requested",
        "arms_completed",
        "paired_complete",
        "thinking_autonomy_observed",
        "contrast",
        "batch",
    }:
        raise ThinkingProbeAbort("security")
    _validate_batch(summary["batch"])
    if (
        summary["arms_requested"] != len(ARMS)
        or type(summary["arms_completed"]) is not int
        or not 0 <= summary["arms_completed"] <= len(ARMS)
        or type(summary["paired_complete"]) is not bool
        or summary["thinking_autonomy_observed"] not in {None, True, False}
        or summary["contrast"]
        not in {
            "not_run",
            "incomplete",
            "both_pass",
            "thinking_only_pass",
            "disabled_only_pass",
            "neither_pass",
        }
    ):
        raise ThinkingProbeAbort("security")

    if report["mode"] == "plan_only":
        if (
            report["executed"] is not False
            or report["status"] != "planned"
            or report["stop_code"] != "none"
            or runs
            or summary
            != {
                "arms_requested": len(ARMS),
                "arms_completed": 0,
                "paired_complete": False,
                "thinking_autonomy_observed": None,
                "contrast": "not_run",
                "batch": _empty_batch(),
            }
        ):
            raise ThinkingProbeAbort("security")
        return

    paired_complete = bool(report["stop_code"] == "none" and len(runs) == len(ARMS))
    thinking_run = next((run for run in runs if run["arm"] == "thinking_high"), None)
    expected_observed = (
        thinking_run["quality"]["passed"]
        if paired_complete and thinking_run is not None
        else None
    )
    if (
        report["executed"] is not True
        or report["status"] != ("completed" if paired_complete else "stopped")
        or summary["paired_complete"] is not paired_complete
        or summary["thinking_autonomy_observed"] is not expected_observed
        or summary["contrast"] != _contrast(runs)
    ):
        raise ThinkingProbeAbort("security")


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ThinkingProbeAbort("contract")


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        description="Paired thinking diagnostic for one synthetic Agent fixture"
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live")
    parser.add_argument("--confirm-cost")
    parser.add_argument("--expect-plan-sha256")
    return parser


def _emergency_report(
    *, live: bool, code: str, executed: bool, batch: Mapping[str, Any] | None
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "live_synthetic_thinking_probe" if live else "plan_only",
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
        config = ThinkingProbeConfig()
        confirmed = (
            args.confirm_live == LIVE_CONFIRMATION
            and args.confirm_cost == COST_CONFIRMATION
        )
        if args.live != confirmed:
            raise ThinkingProbeAbort("confirmation_required")
        report = (
            run_live_thinking_probe(
                config, expected_plan_sha256=args.expect_plan_sha256 or ""
            )
            if args.live
            else build_plan(config)
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if report["status"] in {"planned", "completed"} else 1
    except ThinkingProbeAbort as exc:
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
