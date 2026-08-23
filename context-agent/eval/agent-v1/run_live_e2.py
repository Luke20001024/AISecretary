#!/usr/bin/env python3
"""Frozen 20-day W0/W1/A1 synthetic live-evaluation runner.

The default command is plan-only.  It cannot construct a provider, read an API
key, or open a user Vault.  Live mode is deliberately gated by two explicit
confirmations plus the SHA-256 of the reviewed plan.  Even then, every arm runs
in an independent private clone of the checked-in synthetic 20-day fixture.

This file reuses the trusted scratch, provider metering, strict usage/model
checks, dependency hashing, and safe finite errors from ``run_live_pairing``.
It adds only the stateful 20-day experiment: W0, W1, and A1 each carry their own
prior outputs forward; an error is never repaired with oracle state.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import hashlib
import hmac
import importlib
import inspect
import json
import math
import os
import stat
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
CONTEXT_AGENT_ROOT = HERE.parents[1]
SCENARIO_ROOT = CONTEXT_AGENT_ROOT / "eval" / "scenarios" / "product-manager-20d"
GROUND_TRUTH_PATH = SCENARIO_ROOT / "ground-truth.json"

for entry in (str(HERE), str(CONTEXT_AGENT_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

pairing = importlib.import_module("run_live_pairing")
offline = importlib.import_module("run_offline_eval")
agent_v1 = pairing.agent_v1
core = pairing.core
deepseek_provider = pairing.deepseek_provider


PROJECT_MODULE_ALIASES = (
    ("pairing", "run_live_pairing"),
    ("offline", "run_offline_eval"),
    ("agent_v1", "agent_v1"),
    ("core", "core"),
    ("deepseek_provider", "deepseek_provider"),
)
PROJECT_MODULE_RELATIONSHIPS = (
    ("agent_v1", "pairing", "agent_v1"),
    ("core", "pairing", "core"),
    ("deepseek_provider", "pairing", "deepseek_provider"),
    ("agent_v1", "offline", "agent_v1"),
)


REPORT_SCHEMA_VERSION = "remember_agent_live_e2.v1"
LIVE_CONFIRMATION = "LIVE_20D_SYNTHETIC_ONLY"
ARMS = ("W0", "W1", "A1")
OPERATION_LABELS = ("new", "reinforce", "no_change")
PREDICTED_OPERATIONS = frozenset(
    {"new", "reinforce", "revise", "tension", "no_change", "error"}
)
PUBLIC_ERROR_CODES = frozenset(
    set(pairing.PUBLIC_ERROR_CODES)
    | {
        "budget",
        "feedback",
        "identity_label",
        "quality_failure",
        "tombstone",
    }
)
FATAL_STOP_CODES = frozenset(
    {
        "budget",
        "security",
        "provider_error",
        "usage_missing",
        "call_limit",
        "token_limit",
        "cost_limit",
        "runtime",
        "tombstone",
        "feedback",
        "identity_label",
    }
)
ARM_ORDER_CYCLE = (
    ("W0", "W1", "A1"),
    ("W1", "A1", "W0"),
    ("A1", "W0", "W1"),
)
W0_POLICY_VERSION = "oracle-target-materialized-single-call-v1"
W1_POLICY_VERSION = "oracle-read-literal-search-terminal-v1"
MATERIAL_GATE_VERSION = "exact-material-input-v1"
W0_TERMINAL_INSTRUCTION = (
    "<workflow_constraint version=\"oracle-target-materialized-single-call-v1\">"
    "本轮是固定单次强基线。若 supplied_target 非 null，它是确定性预处理器"
    "提供的完整当前 revision 与 required_patch_binding。只能直接输出 finalize_patch "
    "或 finish 的顶层四键 JSON；不得输出 read_memory 或 search_history。"
    "</workflow_constraint>"
)
W1_TERMINAL_INSTRUCTION = (
    "<workflow_constraint version=\"oracle-read-literal-search-terminal-v1\">"
    "固定 Workflow 已完成本日允许的 read_memory（若存在 oracle target）和一次"
    " literal search。只能直接输出 finalize_patch 或 finish 的顶层四键 JSON；"
    "不得再输出 read_memory 或 search_history。</workflow_constraint>"
)
LIMITATIONS = (
    "逐日 operation oracle 直接复用离线 DAILY_TARGETS。",
    "20 日逐日目标只覆盖 new、reinforce、no_change；不覆盖 revise 或 tension。",
    "W0/W1 使用 oracle target 路由，是有意设置的强基线；本实验不能把较少调用直接解释为 Agent 增益。",
    "质量失败会在完成同一日三臂后停止，因此报告会记录 first_error_day，但可能没有后续 cascade 观察期。",
    "material gate probe 只证明完全相同的处理后状态可 0 调用重放；不证明当日首次 no_change 判断可以免调用。",
    "计划中的成本上限只是安全熔断值，不是实际成本预测。",
)


@dataclasses.dataclass(frozen=True)
class E2Config:
    model: str = "deepseek-v4-pro"
    timeout: float = 60.0
    max_tokens_per_call: int = 2_000
    max_batch_calls: int = 100
    max_batch_tokens: int = 1_200_000
    max_batch_cost_usd: float = 1.0
    budget: Any = dataclasses.field(default_factory=agent_v1.AgentBudget)

    def validate(self) -> "E2Config":
        if self.model not in pairing.SUPPORTED_MODELS:
            raise pairing.PairingAbort("contract")
        if type(self.timeout) not in {int, float} or not 1 <= self.timeout <= 300:
            raise pairing.PairingAbort("contract")
        for value, maximum in (
            (self.max_tokens_per_call, 20_000),
            (self.max_batch_calls, 100),
            (self.max_batch_tokens, 1_200_000),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise pairing.PairingAbort("contract")
        if (
            type(self.max_batch_cost_usd) not in {int, float}
            or not 0 < self.max_batch_cost_usd <= 1.0
        ):
            raise pairing.PairingAbort("contract")
        self.budget.validate()
        return self


@dataclasses.dataclass(frozen=True)
class FrozenContract:
    fixture_manifest_sha256: str
    ground_truth_sha256: str
    daily_oracle_sha256: str
    policy_sha256: str
    prompt_version: str
    prompt_builder_sha256: str
    dependency_manifest_sha256: str
    runner_source_sha256: str
    runner_runtime_sha256: str
    runner_contract_sha256: str


@dataclasses.dataclass
class ArmState:
    arm: str
    vault: Path
    last_material_key: str | None = None
    first_error_day: str | None = None
    post_first_error_failures: int = 0


class E2BatchMeter(pairing.BatchMeter):
    """The pairing fail-closed meter, extended only with W0 capacity."""

    def __init__(self, config: E2Config, pricing: Any) -> None:
        super().__init__(config, pricing)
        self.by_arm = {
            arm: {"calls": 0, "tokens": 0, "cost_usd": 0.0} for arm in ARMS
        }

    def ensure_arm_capacity(self, arm: str) -> None:
        if arm not in ARMS:
            self._abort("security")
        required = self.config.budget.max_turns if arm == "A1" else 1
        if self.calls + required > self.config.max_batch_calls:
            self._abort("call_limit")


ProviderFactory = Callable[[str, str, Path, E2Config], Any]


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    raw = value if isinstance(value, bytes) else _canonical(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _raise_alias_security() -> None:
    canonical_pairing = sys.modules.get("run_live_pairing")
    if inspect.ismodule(canonical_pairing):
        try:
            abort_type = inspect.getattr_static(canonical_pairing, "PairingAbort")
        except AttributeError:
            abort_type = None
        if inspect.isclass(abort_type) and issubclass(abort_type, RuntimeError):
            raise abort_type("security")
    raise RuntimeError("security")


def _assert_project_module_aliases() -> dict[str, Any]:
    """Verify runner-local project modules are the reviewed canonical objects."""

    aliases: list[dict[str, Any]] = []
    for alias, expected_name in PROJECT_MODULE_ALIASES:
        value = globals().get(alias)
        if (
            not inspect.ismodule(value)
            or getattr(value, "__name__", None) != expected_name
            or sys.modules.get(expected_name) is not value
        ):
            _raise_alias_security()
        aliases.append(
            {
                "alias": alias,
                "expected_module": expected_name,
                "sys_modules_identity": True,
            }
        )
    relationships: list[dict[str, Any]] = []
    for local_alias, owner_alias, attribute in PROJECT_MODULE_RELATIONSHIPS:
        try:
            paired_value = inspect.getattr_static(globals()[owner_alias], attribute)
        except AttributeError:
            _raise_alias_security()
        if globals()[local_alias] is not paired_value:
            _raise_alias_security()
        relationships.append(
            {
                "local_alias": local_alias,
                "module_reference": f"{owner_alias}.{attribute}",
                "identity": True,
            }
        )
    return {"aliases": aliases, "relationships": relationships}


def _date_order() -> tuple[str, ...]:
    targets = offline.DAILY_TARGETS
    if not isinstance(targets, Mapping) or len(targets) != 20:
        raise pairing.PairingAbort("security")
    parsed: list[tuple[dt.date, str]] = []
    for value in targets:
        if not isinstance(value, str):
            raise pairing.PairingAbort("security")
        try:
            date = dt.date.fromisoformat(value)
        except ValueError as exc:
            raise pairing.PairingAbort("security") from exc
        if date.isoformat() != value:
            raise pairing.PairingAbort("security")
        parsed.append((date, value))
    parsed.sort()
    for (previous, _), (current, _) in zip(parsed, parsed[1:]):
        if current != previous + dt.timedelta(days=1):
            raise pairing.PairingAbort("security")
    return tuple(value for _, value in parsed)


def _daily_oracle() -> dict[str, dict[str, str | None]]:
    result: dict[str, dict[str, str | None]] = {}
    for date in _date_order():
        target = offline.DAILY_TARGETS[date]
        if (
            not isinstance(target, tuple)
            or len(target) != 2
            or target[0] not in OPERATION_LABELS
            or (target[1] is not None and not isinstance(target[1], str))
        ):
            raise pairing.PairingAbort("security")
        result[date] = {"operation": target[0], "topic": target[1]}
    return result


def _fixture_hashes(root: Path = SCENARIO_ROOT) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for date in _date_order():
        path = root / f"{date}.md"
        if path.is_symlink() or not path.is_file():
            raise pairing.PairingAbort("security")
        resolved = path.resolve(strict=True)
        if resolved.parent != root.resolve(strict=True):
            raise pairing.PairingAbort("security")
        hashes[path.name] = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if len(hashes) != 20:
        raise pairing.PairingAbort("security")
    return hashes


RUNNER_SECURITY_SURFACE = (
    "E2Config",
    "E2Config.validate",
    "FrozenContract",
    "E2BatchMeter",
    "E2BatchMeter.__init__",
    "E2BatchMeter.ensure_arm_capacity",
    "_canonical",
    "_sha",
    "_date_order",
    "_daily_oracle",
    "_fixture_hashes",
    "_dependency_contract",
    "freeze_contract",
    "_assert_frozen",
    "isolated_e2_arm_vault",
    "_clone_day",
    "_visible_source_hashes",
    "_stored_revision_count",
    "_material_key",
    "_material_gate_decision",
    "_request_id",
    "_create_request",
    "_topic",
    "_oracle_target",
    "_materialized_target",
    "_search_query",
    "_search_action",
    "_finish_action",
    "_find_evidence",
    "_oracle_patch",
    "oracle_fake_actions",
    "default_provider_factory",
    "_usage_after_error",
    "_terminal_result",
    "_w0_run",
    "_w1_run",
    "_a1_run",
    "_response_error_code",
    "_safe_error_code",
    "_predicted_operation",
    "_required_evidence",
    "_expected_path",
    "_quality",
    "stateful_budget_max_turns",
    "_public_day",
    "_macro_f1",
    "_active_memories_end",
    "_usage_detail_complete",
    "_summarize",
    "_emergency_summary",
    "_frozen_public",
    "_limits",
    "_plan_payload",
    "plan_sha256",
    "_empty_batch",
    "build_plan",
    "_live_report",
    "run_live_e2",
    "validate_public_report",
    "SafeArgumentParser",
    "build_parser",
    "main",
)


def _resolve_symbol(name: str) -> Any:
    value: Any = globals().get(name.split(".")[0])
    if value is None:
        raise pairing.PairingAbort("security")
    for part in name.split(".")[1:]:
        try:
            value = inspect.getattr_static(value, part)
        except AttributeError as exc:
            raise pairing.PairingAbort("security") from exc
    return value


def _runtime_safety_sha256() -> str:
    _assert_project_module_aliases()
    module = sys.modules.get(__name__)
    if module is None:
        raise pairing.PairingAbort("security")
    return pairing._module_namespace_sha256(module)


def _dependency_contract() -> dict[str, Any]:
    project_module_aliases = _assert_project_module_aliases()
    reused_symbols: dict[str, Any] = {}
    for name in (
        "BatchMeter",
        "BatchMeter.__init__",
        "BatchMeter.before_call",
        "BatchMeter.observe",
        "BatchMeter.observe_unpriced",
        "BatchMeter.public",
        "BatchMeter._abort",
        "MeteredProvider",
        "MeteredProvider.__init__",
        "MeteredProvider.complete",
        "secure_batch_scratch",
        "_trusted_system_temp_parent",
        "_dangerous_roots",
        "_paths_related",
        "_resolved_safety_path",
        "_validate_private_directory",
        "_secure_write_clone",
        "_runner_source_sha256",
        "_runtime_symbol_fingerprint",
        "_secure_source_file_sha256",
        "_dependency_contract",
        "_strict_usage_valid",
        "_empty_usage",
        "_single_usage",
    ):
        value: Any = pairing
        for part in name.split("."):
            value = inspect.getattr_static(value, part)
        reused_symbols[name] = pairing._dependency_symbol_fingerprint(value)
    pairing_namespace = pairing._module_namespace_manifest(pairing)
    offline_namespace = pairing._module_namespace_manifest(offline)
    return {
        "runner_project_module_aliases": project_module_aliases,
        "pairing_runner_sha256": pairing._secure_source_file_sha256(
            Path(pairing.__file__).resolve(strict=True)
        ),
        "offline_runner_sha256": pairing._secure_source_file_sha256(
            Path(offline.__file__).resolve(strict=True)
        ),
        "pairing_runtime_namespace_sha256": _sha(pairing_namespace),
        "pairing_runtime_surface": pairing_namespace["surface"],
        "offline_runtime_namespace_sha256": _sha(offline_namespace),
        "offline_runtime_surface": offline_namespace["surface"],
        "agent_dependencies": pairing._dependency_contract(),
        "reused_pairing_symbols": reused_symbols,
    }


def freeze_contract(config: E2Config) -> FrozenContract:
    _assert_project_module_aliases()
    config.validate()
    fixture = _fixture_hashes()
    if not GROUND_TRUTH_PATH.is_file() or GROUND_TRUTH_PATH.is_symlink():
        raise pairing.PairingAbort("security")
    runner_source = pairing._runner_source_sha256(Path(__file__))
    runtime = _runtime_safety_sha256()
    return FrozenContract(
        fixture_manifest_sha256=_sha(fixture),
        ground_truth_sha256=hashlib.sha256(GROUND_TRUTH_PATH.read_bytes()).hexdigest(),
        daily_oracle_sha256=_sha(_daily_oracle()),
        policy_sha256=agent_v1.make_agent_policy_sha256(
            provider="deepseek", model=config.model, budget=config.budget
        ),
        prompt_version=agent_v1.AGENT_PROMPT_VERSION,
        prompt_builder_sha256=_sha(
            inspect.getsource(agent_v1.build_agent_messages).encode("utf-8")
        ),
        dependency_manifest_sha256=_sha(_dependency_contract()),
        runner_source_sha256=runner_source,
        runner_runtime_sha256=runtime,
        runner_contract_sha256=_sha(
            {
                "source_sha256": runner_source,
                "runtime_sha256": runtime,
                "security_surface": list(RUNNER_SECURITY_SURFACE),
            }
        ),
    )


def _assert_frozen(config: E2Config, frozen: FrozenContract) -> None:
    _assert_project_module_aliases()
    if freeze_contract(config) != frozen:
        raise pairing.PairingAbort("security")


@contextlib.contextmanager
def isolated_e2_arm_vault(scratch_root: Path, arm: str) -> Iterator[Path]:
    if arm not in ARMS:
        raise pairing.PairingAbort("security")
    trusted = pairing._validate_private_directory(
        Path(scratch_root), expected_parent=pairing._trusted_system_temp_parent()
    )
    with __import__("tempfile").TemporaryDirectory(
        prefix=f"memento-agent-e2-{arm.lower()}-", dir=trusted
    ) as temporary:
        vault = Path(temporary)
        vault.chmod(0o700)
        resolved = pairing._validate_private_directory(vault, expected_parent=trusted)
        yield resolved


def _clone_day(vault: Path, date: str) -> None:
    if date not in _date_order():
        raise pairing.PairingAbort("security")
    filename = f"{date}.md"
    source = SCENARIO_ROOT / filename
    target = vault / filename
    if target.exists() or target.is_symlink():
        raise pairing.PairingAbort("security")
    pairing._secure_write_clone(target, source.read_bytes())
    if stat.S_IMODE(target.stat().st_mode) != 0o600:
        raise pairing.PairingAbort("security")
    if core.sha256_file(target) != core.sha256_file(source):
        raise pairing.PairingAbort("security")


def _visible_source_hashes(vault: Path) -> dict[str, str]:
    return {
        path.name: core.sha256_file(path)
        for path in sorted(vault.glob("2026-??-??.md"))
        if path.is_file() and not path.is_symlink()
    }


def _stored_revision_count(vault: Path) -> int:
    directory = agent_v1._agent_directory(vault, "memories")
    return (
        sum(
            1
            for path in directory.glob("*.json")
            if path.is_file() and not path.is_symlink()
        )
        if directory.is_dir()
        else 0
    )


def _material_key(state: ArmState, config: E2Config) -> str:
    profile = agent_v1.build_agent_profile(state.vault)
    return _sha(
        {
            "gate_version": MATERIAL_GATE_VERSION,
            "arm": state.arm,
            "source_hashes": _visible_source_hashes(state.vault),
            "profile_sha256": profile["profile_sha256"],
            "policy_sha256": agent_v1.make_agent_policy_sha256(
                provider="deepseek", model=config.model, budget=config.budget
            ),
        }
    )


def _material_gate_decision(state: ArmState, config: E2Config) -> tuple[str, str]:
    key = _material_key(state, config)
    return ("skip" if key == state.last_material_key else "run", key)


def _request_id(plan_sha: str, arm: str, date: str) -> str:
    return "arq_" + hashlib.sha256(f"{plan_sha}:{arm}:{date}".encode()).hexdigest()[:24]


def _create_request(
    vault: Path, arm: str, date: str, plan_sha: str
) -> tuple[dict[str, Any], str]:
    request, path = agent_v1.create_agent_request(
        vault,
        as_of=date,
        request_id=_request_id(plan_sha, arm, date),
        created_at=f"{date}T22:00:00+08:00",
    )
    return request, core.sha256_file(path)


def _topic(topic_key: str | None) -> Any | None:
    if topic_key is None:
        return None
    value = offline.TOPICS.get(topic_key)
    if value is None:
        raise pairing.PairingAbort("contract")
    return value


def _oracle_target(vault: Path, topic_key: str | None) -> Mapping[str, Any] | None:
    topic = _topic(topic_key)
    if topic is None:
        return None
    expected_id = topic.memory_id
    matches = [
        item
        for item in agent_v1.build_agent_profile(vault)["memories"]
        if item["memory_id"] == expected_id
    ]
    if len(matches) > 1:
        raise pairing.PairingAbort("security")
    return matches[0] if matches else None


def _materialized_target(vault: Path, target: Mapping[str, Any] | None) -> Any:
    if target is None:
        return None
    revision = target["revision"]
    if type(revision) is not int or revision < 1:
        raise pairing.PairingAbort("security")
    record = agent_v1.validate_memory_revision(
        core.read_json(agent_v1._memory_path(vault, target["memory_id"], revision)),
        vault,
        verify_sources=True,
    )
    return {
        "current_revision": dict(record),
        "required_patch_binding": {
            "target_memory_id": target["memory_id"],
            "expected_revision": revision,
        },
    }


def _search_query(topic_key: str | None) -> str:
    return {
        "metric_first": "目标指标、护栏指标和验证周期",
        "failure_first": "先看反例和失败条件",
        "local_memory": "长期记忆只保存在本地 JSON 文件中",
        "confirmation_gate": "用户没有确认的候选不得进入长期 Context",
        None: "长期理解",
    }[topic_key]


def _search_action(topic_key: str | None, date: str) -> dict[str, Any]:
    return {
        "schema_version": agent_v1.AGENT_SCHEMA_VERSION,
        "action": "search_history",
        "reason_code": "need_history_evidence",
        "arguments": {
            "query": _search_query(topic_key),
            "date_from": None,
            "date_to": date,
            "limit": 8,
        },
    }


def _finish_action() -> dict[str, Any]:
    return {
        "schema_version": agent_v1.AGENT_SCHEMA_VERSION,
        "action": "finish",
        "reason_code": "no_material_change",
        "arguments": {"reason": "no_change"},
    }


def _find_evidence(vault: Path, filename: str, marker: str) -> dict[str, Any]:
    matches = [
        {"file": filename, "line": index, "quote": line}
        for index, line in enumerate(
            (vault / filename).read_text(encoding="utf-8").splitlines(), start=1
        )
        if marker in line
    ]
    if len(matches) != 1:
        raise pairing.PairingAbort("security")
    return matches[0]


NEW_EVIDENCE = {
    "2026-07-24": (
        "metric_first",
        (
            ("2026-07-20.md", "做产品决策前，我习惯先写清目标指标"),
            ("2026-07-24.md", "做产品决策前，我习惯先写清目标指标"),
        ),
    ),
    "2026-07-27": (
        "failure_first",
        (
            ("2026-07-21.md", "评审方案时，我希望先看反例"),
            ("2026-07-27.md", "评审方案时，我希望先看反例"),
        ),
    ),
    "2026-07-29": (
        "local_memory",
        (
            ("2026-07-22.md", "长期记忆只保存在本地 JSON 文件中"),
            ("2026-07-29.md", "长期记忆只保存在本地 JSON 文件中"),
        ),
    ),
    "2026-07-30": (
        "confirmation_gate",
        (
            ("2026-07-23.md", "用户没有确认的候选不得进入长期 Context"),
            ("2026-07-30.md", "用户没有确认的候选不得进入长期 Context"),
        ),
    ),
}
REINFORCE_EVIDENCE = {
    "2026-07-28": ("2026-07-28.md", "做产品决策前，我习惯先写清目标指标"),
    "2026-08-01": ("2026-08-01.md", "做产品决策前，我习惯先写清目标指标"),
    "2026-08-02": ("2026-08-02.md", "过去两周，指标、护栏和验证周期"),
}


def _oracle_patch(vault: Path, date: str) -> dict[str, Any]:
    operation, topic_key = offline.DAILY_TARGETS[date]
    topic = _topic(topic_key)
    if operation == "new":
        frozen_topic, markers = NEW_EVIDENCE[date]
        if frozen_topic != topic_key or topic is None:
            raise pairing.PairingAbort("contract")
        evidence = [_find_evidence(vault, file, marker) for file, marker in markers]
        target_id = None
        expected_revision = 0
        statement, scope = topic.statement, topic.scope
    elif operation == "reinforce":
        target = _oracle_target(vault, topic_key)
        if target is None:
            raise pairing.PairingAbort("contract")
        file, marker = REINFORCE_EVIDENCE[date]
        evidence = [_find_evidence(vault, file, marker)]
        target_id = target["memory_id"]
        expected_revision = target["revision"]
        statement, scope = target["statement"], target["scope"]
    else:
        raise pairing.PairingAbort("contract")
    return {
        "schema_version": agent_v1.AGENT_SCHEMA_VERSION,
        "action": "finalize_patch",
        "reason_code": "evidence_sufficient",
        "arguments": {
            "operation": operation,
            "target_memory_id": target_id,
            "expected_revision": expected_revision,
            "title": topic.title,
            "statement": statement,
            "scope": scope,
            "uncertainty": "medium",
            "evidence": evidence,
            "counterevidence": [],
        },
    }


def oracle_fake_actions(vault: Path, arm: str, date: str) -> list[dict[str, Any]]:
    """Deterministic actions for offline fake-provider contract tests only."""

    operation, topic_key = offline.DAILY_TARGETS[date]
    terminal = _finish_action() if operation == "no_change" else _oracle_patch(vault, date)
    if arm in {"W0", "W1"}:
        return [terminal]
    if arm != "A1":
        raise pairing.PairingAbort("contract")
    if operation == "reinforce":
        target = _oracle_target(vault, topic_key)
        if target is None:
            return [_finish_action()]
        return [
            {
                "schema_version": agent_v1.AGENT_SCHEMA_VERSION,
                "action": "read_memory",
                "reason_code": "inspect_existing",
                "arguments": {"memory_id": target["memory_id"]},
            },
            terminal,
        ]
    if operation == "new" or topic_key is not None:
        return [_search_action(topic_key, date), terminal]
    return [terminal]


def default_provider_factory(arm: str, date: str, vault: Path, config: E2Config) -> Any:
    _assert_project_module_aliases()
    del arm, date, vault
    return deepseek_provider.DeepSeekProvider(
        model=config.model,
        timeout=config.timeout,
        thinking="disabled",
        reasoning_effort=None,
        max_tokens=config.max_tokens_per_call,
    )


def _usage_after_error(
    exc: BaseException, meter: E2BatchMeter, arm: str, calls_before: int, config: E2Config
) -> dict[str, Any]:
    if meter.by_arm[arm]["calls"] <= calls_before:
        return pairing._empty_usage()
    return pairing._single_usage(
        getattr(exc, "usage", None),
        core.pricing_for_model(config.model),
        cost_known=not (
            isinstance(exc, pairing.PairingAbort) and exc.code == "security"
        ),
    )


def _terminal_result(
    preparation: Any,
    action: Mapping[str, Any],
    request_id: str,
) -> tuple[str, Mapping[str, Any] | None]:
    if action["action"] == "finalize_patch":
        memory = agent_v1._finalize_patch(
            preparation,
            action["arguments"],
            run_id=agent_v1.make_run_id(request_id),
        )
        return "updated", memory
    if action["action"] == "finish":
        return action["arguments"]["reason"], None
    raise pairing.PairingAbort("invalid_terminal_action")


def _w0_run(
    state: ArmState,
    provider: Any,
    config: E2Config,
    date: str,
    plan_sha: str,
) -> dict[str, Any]:
    operation, topic_key = offline.DAILY_TARGETS[date]
    request, request_sha = _create_request(state.vault, "W0", date, plan_sha)
    preparation = agent_v1.prepare_agent_run(
        state.vault, request, request_sha, maximum_chars=config.budget.max_prompt_chars
    )
    target = _oracle_target(state.vault, topic_key) if operation == "reinforce" else None
    messages = agent_v1.build_agent_messages(preparation)
    messages.append(
        {
            "role": "user",
            "content": W0_TERMINAL_INSTRUCTION
            + "\n<supplied_target>"
            + _canonical(_materialized_target(state.vault, target))
            + "</supplied_target>",
        }
    )
    calls_before = provider.meter.by_arm["W0"]["calls"]
    usage = pairing._empty_usage()
    status, memory, error_code = "error", None, "none"
    try:
        completion = provider.complete(messages)
        usage = pairing._single_usage(
            completion.usage, core.pricing_for_model(config.model)
        )
        action = agent_v1._parse_action(completion.content)
        status, memory = _terminal_result(preparation, action, request["id"])
    except Exception as exc:
        error_code = _safe_error_code(exc)
        usage = _usage_after_error(exc, provider.meter, "W0", calls_before, config)
    return {
        "status": status,
        "memory": memory,
        "error_code": error_code,
        "path": ["terminal_model_action"],
        "usage": usage,
    }


def _w1_run(
    state: ArmState,
    provider: Any,
    config: E2Config,
    date: str,
    plan_sha: str,
) -> dict[str, Any]:
    operation, topic_key = offline.DAILY_TARGETS[date]
    request, request_sha = _create_request(state.vault, "W1", date, plan_sha)
    preparation = agent_v1.prepare_agent_run(
        state.vault, request, request_sha, maximum_chars=config.budget.max_prompt_chars
    )
    messages = agent_v1.build_agent_messages(preparation)
    path: list[str] = []
    target = _oracle_target(state.vault, topic_key) if operation == "reinforce" else None
    if target is not None:
        read_action = {
            "schema_version": agent_v1.AGENT_SCHEMA_VERSION,
            "action": "read_memory",
            "reason_code": "inspect_existing",
            "arguments": {"memory_id": target["memory_id"]},
        }
        read_result = agent_v1._read_memory_tool(preparation, target["memory_id"])
        agent_v1._append_tool_result(messages, read_action, {"ok": True, **read_result})
        path.append("read_memory")
    search_action = _search_action(topic_key, date)
    matches = agent_v1._literal_history_search(preparation, search_action["arguments"])
    agent_v1._append_tool_result(
        messages,
        search_action,
        {"ok": True, "matches": matches, "match_count": len(matches)},
    )
    path.extend(("search_history", "terminal_model_action"))
    messages.append({"role": "user", "content": W1_TERMINAL_INSTRUCTION})
    calls_before = provider.meter.by_arm["W1"]["calls"]
    usage = pairing._empty_usage()
    status, memory, error_code = "error", None, "none"
    try:
        completion = provider.complete(messages)
        usage = pairing._single_usage(
            completion.usage, core.pricing_for_model(config.model)
        )
        action = agent_v1._parse_action(completion.content)
        status, memory = _terminal_result(preparation, action, request["id"])
    except Exception as exc:
        error_code = _safe_error_code(exc)
        usage = _usage_after_error(exc, provider.meter, "W1", calls_before, config)
    return {
        "status": status,
        "memory": memory,
        "error_code": error_code,
        "path": path,
        "usage": usage,
    }


def _a1_run(
    state: ArmState,
    provider: Any,
    config: E2Config,
    date: str,
    plan_sha: str,
) -> dict[str, Any]:
    request, _ = _create_request(state.vault, "A1", date, plan_sha)
    response, _ = agent_v1.process_agent_request(
        state.vault,
        request["id"],
        provider_client=provider,
        provider_name="deepseek",
        model=config.model,
        pricing=core.pricing_for_model(config.model),
        budget=config.budget,
        maximum_chars=config.budget.max_prompt_chars,
    )
    memory = response["memory"]
    error_code = _response_error_code(response)
    return {
        "status": response["status"],
        "memory": memory,
        "error_code": error_code,
        "path": list(response["trace"]["actions"]),
        "usage": dict(response["usage"]),
    }


def _response_error_code(response: Mapping[str, Any]) -> str:
    """Project an Agent response onto a finite public stop category.

    Safety and user-authority violations are never downgraded to an ordinary
    quality miss.  Planner/schema/loop failures remain ``agent_error`` so the
    other arms on the same day can still be observed before the quality stop.
    """

    if response.get("status") in {"updated", "no_change", "insufficient_evidence"}:
        return "none"
    error_kind = response.get("error_kind")
    if error_kind in {"stale", "cas", "sensitive", "evidence", "conflict"}:
        return "security"
    if error_kind in {
        "budget",
        "runtime",
        "tombstone",
        "feedback",
        "identity_label",
    }:
        return str(error_kind)
    return "agent_error"


def _safe_error_code(exc: BaseException) -> str:
    if isinstance(exc, pairing.PairingAbort):
        return exc.code
    if isinstance(exc, core.ContractError):
        if exc.kind in {"stale", "cas", "sensitive", "evidence", "conflict"}:
            return "security"
        if exc.kind in {
            "budget",
            "runtime",
            "tombstone",
            "feedback",
            "identity_label",
        }:
            return exc.kind
        return "contract"
    if exc.__class__.__name__ == "ProviderError":
        return "provider_error"
    return "runtime"


def _predicted_operation(result: Mapping[str, Any]) -> str:
    if result["status"] in {"no_change", "insufficient_evidence"}:
        return "no_change"
    memory = result.get("memory")
    if result["status"] == "updated" and isinstance(memory, Mapping):
        operation = memory.get("provenance", {}).get("operation")
        if operation in {"new", "reinforce", "revise", "tension"}:
            return operation
    return "error"


def _required_evidence(vault: Path, date: str) -> list[dict[str, Any]]:
    operation, _ = offline.DAILY_TARGETS[date]
    if operation == "new":
        return [
            _find_evidence(vault, filename, marker)
            for filename, marker in NEW_EVIDENCE[date][1]
        ]
    if operation == "reinforce":
        filename, marker = REINFORCE_EVIDENCE[date]
        return [_find_evidence(vault, filename, marker)]
    return []


def _expected_path(
    arm: str, operation: str, topic_key: str | None, target_before: Mapping[str, Any] | None
) -> list[str]:
    if arm == "W0":
        return ["terminal_model_action"]
    if arm == "W1":
        return (
            ["read_memory", "search_history", "terminal_model_action"]
            if operation == "reinforce" and target_before is not None
            else ["search_history", "terminal_model_action"]
        )
    if operation == "reinforce" and target_before is not None:
        return ["read_memory", "finalize_patch"]
    if operation == "new" or topic_key is not None:
        return ["search_history", "finish" if operation == "no_change" else "finalize_patch"]
    return ["finish"]


def _quality(
    state: ArmState,
    date: str,
    result: Mapping[str, Any],
    before_profile: Mapping[str, Any],
    target_before: Mapping[str, Any] | None,
    source_before: Mapping[str, str],
    revisions_before: int,
    config: E2Config,
) -> dict[str, Any]:
    operation, topic_key = offline.DAILY_TARGETS[date]
    predicted = _predicted_operation(result)
    after_profile = agent_v1.build_agent_profile(state.vault)
    memory = result.get("memory")
    expected_topic = _topic(topic_key)
    expected_id = expected_topic.memory_id if expected_topic is not None else None
    source_unchanged = dict(source_before) == _visible_source_hashes(state.vault)
    revision_delta = _stored_revision_count(state.vault) - revisions_before
    if operation == "new":
        route_correct = bool(
            memory
            and memory.get("memory_id") == expected_id
            and memory.get("revision") == 1
            and target_before is None
        )
    elif operation == "reinforce":
        route_correct = bool(
            target_before
            and memory
            and memory.get("memory_id") == target_before.get("memory_id")
            and memory.get("revision") == target_before.get("revision", 0) + 1
        )
    else:
        route_correct = after_profile["profile_sha256"] == before_profile["profile_sha256"]
    evidence_correct = True
    if operation in {"new", "reinforce"}:
        evidence_correct = bool(
            memory
            and all(item in memory.get("evidence", []) for item in _required_evidence(state.vault, date))
        )
    path = list(result["path"])
    if state.arm == "W0":
        path_contract_ok = path == ["terminal_model_action"]
    elif state.arm == "W1":
        path_contract_ok = path[-2:] == ["search_history", "terminal_model_action"] and path.count(
            "read_memory"
        ) <= 1
    else:
        path_contract_ok = bool(
            len(path) <= stateful_budget_max_turns(state, config)
            and all(action in agent_v1.AGENT_ACTIONS | {"invalid_action"} for action in path)
            and "invalid_action" not in path
        )
    path_oracle_match = path == _expected_path(
        state.arm, operation, topic_key, target_before
    )
    usage = result["usage"]
    usage_complete = bool(
        usage.get("model_calls", 0) >= 1
        and usage.get("total_tokens", 0) > 0
        and usage.get("usage_missing") is False
        and usage.get("cost_usd") is not None
    )
    operation_correct = predicted == operation
    expected_delta = 0 if operation == "no_change" else 1
    state_integrity = revision_delta == expected_delta
    passed = bool(
        result["error_code"] == "none"
        and operation_correct
        and route_correct
        and evidence_correct
        and state_integrity
        and path_contract_ok
        and source_unchanged
        and usage_complete
    )
    return {
        "predicted_operation": predicted,
        "operation_correct": operation_correct,
        "route_correct": route_correct,
        "evidence_correct": evidence_correct,
        "state_revision_delta": revision_delta,
        "state_integrity": state_integrity,
        "path_contract_ok": path_contract_ok,
        "path_oracle_match": path_oracle_match,
        "source_clone_unchanged": source_unchanged,
        "usage_complete": usage_complete,
        "passed": passed,
    }


def stateful_budget_max_turns(state: ArmState, config: E2Config) -> int:
    del state
    return config.budget.max_turns


def _public_day(
    state: ArmState,
    date: str,
    index: int,
    result: Mapping[str, Any],
    quality: Mapping[str, Any],
    probe: Mapping[str, Any],
) -> dict[str, Any]:
    operation, _ = offline.DAILY_TARGETS[date]
    return {
        "date": date,
        "day_index": index,
        "arm": state.arm,
        "expected_operation": operation,
        "predicted_operation": quality["predicted_operation"],
        "status": result["status"],
        "error_code": result["error_code"],
        "path": list(result["path"]),
        "path_contract_ok": quality["path_contract_ok"],
        "path_oracle_match": quality["path_oracle_match"],
        "operation_correct": quality["operation_correct"],
        "route_correct": quality["route_correct"],
        "evidence_correct": quality["evidence_correct"],
        "state_revision_delta": quality["state_revision_delta"],
        "state_integrity": quality["state_integrity"],
        "source_clone_unchanged": quality["source_clone_unchanged"],
        "quality_passed": quality["passed"],
        "material_gate_probe": dict(probe),
        "usage": dict(result["usage"]),
    }


def _macro_f1(items: Sequence[Mapping[str, Any]]) -> tuple[float, dict[str, Any]]:
    matrix = {
        expected: {predicted: 0 for predicted in PREDICTED_OPERATIONS}
        for expected in OPERATION_LABELS
    }
    for item in items:
        matrix[item["expected_operation"]][item["predicted_operation"]] += 1
    scores: list[float] = []
    for label in OPERATION_LABELS:
        tp = matrix[label][label]
        fp = sum(matrix[other][label] for other in OPERATION_LABELS if other != label)
        fn = sum(value for predicted, value in matrix[label].items() if predicted != label)
        denominator = 2 * tp + fp + fn
        scores.append((2 * tp / denominator) if denominator else 0.0)
    return sum(scores) / len(scores), matrix


def _active_memories_end(state: ArmState) -> int | None:
    try:
        active = agent_v1.build_agent_profile(state.vault)["stats"]["active"]
    except Exception:
        return None
    return active if type(active) is int and active >= 0 else None


def _usage_detail_complete(
    days: Sequence[Mapping[str, Any]], batch: Mapping[str, Any]
) -> bool:
    for arm in ARMS:
        items = [item for item in days if item.get("arm") == arm]
        if sum(item["usage"]["model_calls"] for item in items) != batch["by_arm"][arm][
            "calls"
        ]:
            return False
        if sum(item["usage"]["total_tokens"] for item in items) != batch["by_arm"][arm][
            "tokens"
        ]:
            return False
        known_costs = [item["usage"]["cost_usd"] for item in items]
        if any(value is None for value in known_costs):
            if batch["by_arm"][arm]["cost_usd"] != 0:
                return False
        elif round(sum(known_costs), 10) != batch["by_arm"][arm]["cost_usd"]:
            return False
    return True


def _summarize(
    days: Sequence[Mapping[str, Any]],
    states: Mapping[str, ArmState],
    meter: E2BatchMeter,
    days_completed: int,
    stopped_after_day: str | None,
) -> dict[str, Any]:
    batch = meter.public()
    by_arm: dict[str, Any] = {}
    for arm in ARMS:
        items = [item for item in days if item["arm"] == arm]
        macro_f1, matrix = _macro_f1(items)
        evaluated = len(items)
        failures = [item for item in items if not item["quality_passed"]]
        state = states[arm]
        by_arm[arm] = {
            "days_evaluated": evaluated,
            "quality_days_passed": evaluated - len(failures),
            "macro_f1": macro_f1,
            "confusion": matrix,
            "route_accuracy": (
                sum(bool(item["route_correct"]) for item in items) / evaluated
                if evaluated
                else 0.0
            ),
            "path_contract_rate": (
                sum(bool(item["path_contract_ok"]) for item in items) / evaluated
                if evaluated
                else 0.0
            ),
            "path_oracle_match_rate": (
                sum(bool(item["path_oracle_match"]) for item in items) / evaluated
                if evaluated
                else 0.0
            ),
            "first_error_day": state.first_error_day,
            "cascade_days_observed": 0,
            "post_first_error_failures": state.post_first_error_failures,
            "cascade_observation_truncated_by_stop": state.first_error_day is not None,
            "active_memories_end": _active_memories_end(state),
            "usage": dict(meter.by_arm[arm]),
        }
    return {
        "days_requested": 20,
        "days_completed": days_completed,
        "stopped_after_day": stopped_after_day,
        "report_detail_complete": _usage_detail_complete(days, batch),
        "batch_quality": bool(
            days_completed == 20 and len(days) == 60 and all(item["quality_passed"] for item in days)
        ),
        "operation_coverage": {
            "target_counts": dict(Counter(item["operation"] for item in _daily_oracle().values())),
            "covered": list(OPERATION_LABELS),
            "not_covered": ["revise", "tension"],
            "complete_for_agent_patch_space": False,
        },
        "by_arm": by_arm,
        "batch": batch,
    }


def _emergency_summary(
    meter: E2BatchMeter, stopped_after_day: str | None
) -> dict[str, Any]:
    empty_matrix = {
        expected: {predicted: 0 for predicted in PREDICTED_OPERATIONS}
        for expected in OPERATION_LABELS
    }
    return {
        "days_requested": 20,
        "days_completed": 0,
        "stopped_after_day": stopped_after_day,
        "report_detail_complete": False,
        "batch_quality": False,
        "operation_coverage": {
            "target_counts": dict(
                Counter(item["operation"] for item in _daily_oracle().values())
            ),
            "covered": list(OPERATION_LABELS),
            "not_covered": ["revise", "tension"],
            "complete_for_agent_patch_space": False,
        },
        "by_arm": {
            arm: {
                "days_evaluated": 0,
                "quality_days_passed": 0,
                "macro_f1": 0.0,
                "confusion": {key: dict(value) for key, value in empty_matrix.items()},
                "route_accuracy": 0.0,
                "path_contract_rate": 0.0,
                "path_oracle_match_rate": 0.0,
                "first_error_day": None,
                "cascade_days_observed": 0,
                "post_first_error_failures": 0,
                "cascade_observation_truncated_by_stop": False,
                "active_memories_end": None,
                "usage": dict(meter.by_arm[arm]),
            }
            for arm in ARMS
        },
        "batch": meter.public(),
    }


def _frozen_public(config: E2Config, frozen: FrozenContract) -> dict[str, Any]:
    date_order = _date_order()
    return {
        "scenario": "product-manager-20d-v2-rich",
        "synthetic": True,
        "date_from": date_order[0],
        "date_to": date_order[-1],
        "daily_files": 20,
        "date_order": list(date_order),
        "fixture_manifest_sha256": frozen.fixture_manifest_sha256,
        "ground_truth_sha256": frozen.ground_truth_sha256,
        "daily_oracle_sha256": frozen.daily_oracle_sha256,
        "policy_sha256": frozen.policy_sha256,
        "daily_operation_counts": dict(
            Counter(item["operation"] for item in _daily_oracle().values())
        ),
        "prompt_version": frozen.prompt_version,
        "prompt_builder_sha256": frozen.prompt_builder_sha256,
        "dependency_manifest_sha256": frozen.dependency_manifest_sha256,
        "runner_source_sha256": frozen.runner_source_sha256,
        "runner_runtime_sha256": frozen.runner_runtime_sha256,
        "runner_contract_sha256": frozen.runner_contract_sha256,
        "provider": "deepseek",
        "model": config.model,
        "timeout_seconds": config.timeout,
        "thinking": "disabled",
        "reasoning_effort": None,
        "max_tokens_per_call": config.max_tokens_per_call,
        "agent_budget": config.budget.as_dict(),
        "material_gate_version": MATERIAL_GATE_VERSION,
        "material_gate_zero_call_probe_on_no_change_days": True,
        "arm_order_cycle": [list(order) for order in ARM_ORDER_CYCLE],
        "w0": {
            "kind": "oracle_assisted_single_call",
            "policy_version": W0_POLICY_VERSION,
            "target_materialization": "full_current_revision_and_binding_when_unique",
            "model_calls_per_material_day": 1,
        },
        "w1": {
            "kind": "oracle_assisted_fixed_workflow",
            "policy_version": W1_POLICY_VERSION,
            "workflow": ["optional_read_memory", "literal_search", "terminal_model_action"],
            "model_calls_per_material_day": 1,
        },
        "a1": {
            "kind": "dynamic_agent_v1",
            "max_model_calls_per_material_day": config.budget.max_turns,
        },
        "state_policy": "independent_per_arm_chronological_no_oracle_repair",
        "quality_failure_policy": "finish_same_day_all_arms_then_stop",
        "safety_failure_policy": "stop_immediately",
        "agent_gain_claimed": False,
    }


def _limits(config: E2Config) -> dict[str, Any]:
    return {
        "max_batch_calls": config.max_batch_calls,
        "max_batch_tokens": config.max_batch_tokens,
        "max_batch_cost_usd": config.max_batch_cost_usd,
        "theoretical_max_calls": 100,
        "fail_closed": True,
    }


def _plan_payload(
    frozen_public: Mapping[str, Any], limits: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "frozen": dict(frozen_public),
        "limits": dict(limits),
        "daily_oracle": _daily_oracle(),
        "execution_order_cycle": ARM_ORDER_CYCLE,
    }


def plan_sha256(config: E2Config, frozen: FrozenContract) -> str:
    return _sha(_plan_payload(_frozen_public(config, frozen), _limits(config)))


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


def build_plan(config: E2Config) -> dict[str, Any]:
    frozen = freeze_contract(config)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "plan_only",
        "executed": False,
        "status": "planned",
        "stop_code": "none",
        "plan_sha256": plan_sha256(config, frozen),
        "frozen": _frozen_public(config, frozen),
        "limits": _limits(config),
        "credential": {
            "lookup_deferred_until_provider_call": True,
            "environment_or_macos_keychain": True,
            "persisted_in_report": False,
        },
        "days": [],
        "summary": {
            "days_requested": 20,
            "days_completed": 0,
            "stopped_after_day": None,
            "report_detail_complete": True,
            "batch_quality": None,
            "operation_coverage": {
                "target_counts": dict(
                    Counter(item["operation"] for item in _daily_oracle().values())
                ),
                "covered": list(OPERATION_LABELS),
                "not_covered": ["revise", "tension"],
                "complete_for_agent_patch_space": False,
            },
            "by_arm": {},
            "batch": _empty_batch(),
        },
        "limitations": list(LIMITATIONS),
    }


def _live_report(
    config: E2Config,
    frozen: FrozenContract,
    actual_plan: str,
    *,
    stop_code: str,
    days: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "live_synthetic",
        "executed": True,
        "status": "completed" if stop_code == "none" else "stopped",
        "stop_code": stop_code,
        "plan_sha256": actual_plan,
        "frozen": _frozen_public(config, frozen),
        "limits": _limits(config),
        "credential": {
            "lookup_deferred_until_provider_call": True,
            "environment_or_macos_keychain": True,
            "persisted_in_report": False,
        },
        "days": [dict(item) for item in days],
        "summary": dict(summary),
        "limitations": list(LIMITATIONS),
    }


def run_live_e2(
    config: E2Config,
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
        or any(char not in "0123456789abcdef" for char in expected_plan_sha256)
        or expected_plan_sha256 != actual_plan
    ):
        raise pairing.PairingAbort("plan_mismatch")
    if provider_factory is None:
        _assert_project_module_aliases()
        provider_factory = default_provider_factory
    elif not callable(provider_factory):
        raise pairing.PairingAbort("contract")
    pricing = core.pricing_for_model(config.model)
    meter = E2BatchMeter(config, pricing)
    public_days: list[dict[str, Any]] = []
    days_completed = 0
    stopped_after_day: str | None = None
    stop_code = "none"
    fixture_before = _fixture_hashes()
    with pairing.secure_batch_scratch() as scratch, ExitStack() as stack:
        states = {
            arm: ArmState(
                arm=arm,
                vault=stack.enter_context(isolated_e2_arm_vault(scratch, arm)),
            )
            for arm in ARMS
        }
        current_date: str | None = None
        try:
            for day_index, date in enumerate(_date_order(), start=1):
                current_date = date
                day_record_start = len(public_days)
                for state in states.values():
                    _clone_day(state.vault, date)
                day_quality_failed = False
                order = ARM_ORDER_CYCLE[(day_index - 1) % len(ARM_ORDER_CYCLE)]
                for arm in order:
                    state = states[arm]
                    try:
                        _assert_frozen(config, frozen)
                        decision, material_key = _material_gate_decision(state, config)
                        if decision != "run":
                            raise pairing.PairingAbort("security")
                        state.last_material_key = material_key
                        meter.ensure_arm_capacity(arm)
                        before_profile = agent_v1.build_agent_profile(state.vault)
                        revisions_before = _stored_revision_count(state.vault)
                        operation, topic_key = offline.DAILY_TARGETS[date]
                        target_before = _oracle_target(state.vault, topic_key)
                        source_before = _visible_source_hashes(state.vault)
                        _assert_project_module_aliases()
                        provider = pairing.MeteredProvider(
                            provider_factory(arm, date, state.vault, config), meter, arm
                        )
                        runner = {"W0": _w0_run, "W1": _w1_run, "A1": _a1_run}[arm]
                        result = runner(state, provider, config, date, actual_plan)
                        quality = _quality(
                            state,
                            date,
                            result,
                            before_profile,
                            target_before,
                            source_before,
                            revisions_before,
                            config,
                        )
                        probe_required = operation == "no_change"
                        probe_calls_before = meter.calls
                        probe_decision, _ = _material_gate_decision(state, config)
                        probe = {
                            "required": probe_required,
                            "decision": probe_decision if probe_required else "not_required",
                            "provider_calls": meter.calls - probe_calls_before,
                            "passed": bool(
                                not probe_required
                                or (
                                    probe_decision == "skip"
                                    and meter.calls == probe_calls_before
                                )
                            ),
                        }
                        if probe_required and not probe["passed"]:
                            quality["passed"] = False
                        public_days.append(
                            _public_day(state, date, day_index, result, quality, probe)
                        )
                        if not quality["passed"]:
                            if state.first_error_day is None:
                                state.first_error_day = date
                            else:
                                state.post_first_error_failures += 1
                            day_quality_failed = True
                        if result["error_code"] in FATAL_STOP_CODES:
                            stop_code = result["error_code"]
                            meter.halted_code = meter.halted_code or stop_code
                            stopped_after_day = date
                            break
                        if meter.halted_code is not None:
                            stop_code = meter.halted_code
                            stopped_after_day = date
                            break
                    except Exception as exc:
                        stop_code = _safe_error_code(exc)
                        meter.halted_code = meter.halted_code or stop_code
                        stopped_after_day = date
                        break
                day_records = len(public_days) - day_record_start
                if day_records == len(ARMS):
                    days_completed += 1
                if meter.halted_code is not None:
                    break
                if day_quality_failed:
                    stop_code = "quality_failure"
                    stopped_after_day = date
                    break
            if _fixture_hashes() != fixture_before:
                raise pairing.PairingAbort("security")
        except Exception as exc:
            stop_code = _safe_error_code(exc)
            meter.halted_code = meter.halted_code or stop_code
            stopped_after_day = stopped_after_day or current_date
        summary = _summarize(
            public_days, states, meter, days_completed, stopped_after_day
        )
        report = _live_report(
            config,
            frozen,
            actual_plan,
            stop_code=stop_code,
            days=public_days,
            summary=summary,
        )
        try:
            validate_public_report(report)
        except Exception as exc:
            # A paid call must never be hidden behind an ``executed:false``
            # CLI fallback.  Emit a source-free finite emergency projection
            # with the authoritative batch meter even if detailed report
            # assembly or its final safety validation fails late.
            if meter.calls <= 0:
                raise
            late_code = _safe_error_code(exc)
            if late_code not in FATAL_STOP_CODES:
                late_code = "runtime"
            stopped_after_day = stopped_after_day or current_date
            emergency = _live_report(
                config,
                frozen,
                actual_plan,
                stop_code=late_code,
                days=[],
                summary=_emergency_summary(meter, stopped_after_day),
            )
            try:
                validate_public_report(emergency)
            except Exception:
                # The object is assembled exclusively from frozen public
                # fields, finite enums, and the metered numeric projection.
                # Returning it is safer than falsely claiming no execution.
                pass
            return emergency
        return report


def validate_public_report(report: Mapping[str, Any]) -> None:
    """Reject unknown public fields and any accidental source/prompt/path leak."""

    def exact(value: Any, fields: set[str], label: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping) or set(value) != fields:
            raise pairing.PairingAbort("security")
        return value

    def integer(value: Any, *, minimum: int = 0) -> None:
        if type(value) is not int or value < minimum:
            raise pairing.PairingAbort("security")

    def number(value: Any) -> None:
        if (
            type(value) not in {int, float}
            or not math.isfinite(value)
            or value < 0
        ):
            raise pairing.PairingAbort("security")

    def sha256(value: Any) -> None:
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise pairing.PairingAbort("security")

    def iso_date(value: Any) -> None:
        if not isinstance(value, str):
            raise pairing.PairingAbort("security")
        try:
            parsed = dt.date.fromisoformat(value)
        except ValueError as exc:
            raise pairing.PairingAbort("security") from exc
        if parsed.isoformat() != value:
            raise pairing.PairingAbort("security")

    top = exact(
        report,
        {
            "schema_version",
            "mode",
            "executed",
            "status",
            "stop_code",
            "plan_sha256",
            "frozen",
            "limits",
            "credential",
            "days",
            "summary",
            "limitations",
        },
        "report",
    )
    if top["schema_version"] != REPORT_SCHEMA_VERSION:
        raise pairing.PairingAbort("security")
    if top["mode"] not in {"plan_only", "live_synthetic"}:
        raise pairing.PairingAbort("security")
    if type(top["executed"]) is not bool or top["status"] not in {
        "planned",
        "completed",
        "stopped",
    }:
        raise pairing.PairingAbort("security")
    if top["stop_code"] not in PUBLIC_ERROR_CODES | {"none"}:
        raise pairing.PairingAbort("security")
    sha256(top["plan_sha256"])
    if top["limitations"] != list(LIMITATIONS):
        raise pairing.PairingAbort("security")
    frozen = exact(
        top["frozen"],
        {
            "scenario",
            "synthetic",
            "date_from",
            "date_to",
            "daily_files",
            "date_order",
            "fixture_manifest_sha256",
            "ground_truth_sha256",
            "daily_oracle_sha256",
            "policy_sha256",
            "daily_operation_counts",
            "prompt_version",
            "prompt_builder_sha256",
            "dependency_manifest_sha256",
            "runner_source_sha256",
            "runner_runtime_sha256",
            "runner_contract_sha256",
            "provider",
            "model",
            "timeout_seconds",
            "thinking",
            "reasoning_effort",
            "max_tokens_per_call",
            "agent_budget",
            "material_gate_version",
            "material_gate_zero_call_probe_on_no_change_days",
            "arm_order_cycle",
            "w0",
            "w1",
            "a1",
            "state_policy",
            "quality_failure_policy",
            "safety_failure_policy",
            "agent_gain_claimed",
        },
        "frozen",
    )
    expected_date_order = list(_date_order())
    if (
        frozen["scenario"] != "product-manager-20d-v2-rich"
        or frozen["synthetic"] is not True
        or frozen["daily_files"] != 20
        or frozen["date_from"] != expected_date_order[0]
        or frozen["date_to"] != expected_date_order[-1]
        or frozen["date_order"] != expected_date_order
        or frozen["agent_gain_claimed"] is not False
        or frozen["provider"] != "deepseek"
        or frozen["model"] not in pairing.SUPPORTED_MODELS
        or type(frozen["timeout_seconds"]) not in {int, float}
        or not 1 <= frozen["timeout_seconds"] <= 300
        or frozen["thinking"] != "disabled"
        or frozen["reasoning_effort"] is not None
        or frozen["prompt_version"] != agent_v1.AGENT_PROMPT_VERSION
        or frozen["material_gate_version"] != MATERIAL_GATE_VERSION
        or frozen["material_gate_zero_call_probe_on_no_change_days"] is not True
        or frozen["arm_order_cycle"] != [list(order) for order in ARM_ORDER_CYCLE]
        or frozen["state_policy"]
        != "independent_per_arm_chronological_no_oracle_repair"
        or frozen["quality_failure_policy"]
        != "finish_same_day_all_arms_then_stop"
        or frozen["safety_failure_policy"] != "stop_immediately"
    ):
        raise pairing.PairingAbort("security")
    integer(frozen["max_tokens_per_call"], minimum=1)
    counts = exact(
        frozen["daily_operation_counts"], set(OPERATION_LABELS), "daily counts"
    )
    if counts != {"new": 4, "reinforce": 3, "no_change": 13}:
        raise pairing.PairingAbort("security")
    exact(frozen["agent_budget"], set(agent_v1.BUDGET_FIELDS), "agent budget")
    for value in frozen["agent_budget"].values():
        integer(value, minimum=1)
    current_policy_sha256 = agent_v1.make_agent_policy_sha256(
        provider="deepseek",
        model=frozen["model"],
        budget=agent_v1.AgentBudget(**frozen["agent_budget"]),
    )
    if frozen["policy_sha256"] != current_policy_sha256:
        raise pairing.PairingAbort("security")
    w0 = exact(
        frozen["w0"],
        {"kind", "policy_version", "target_materialization", "model_calls_per_material_day"},
        "w0",
    )
    w1 = exact(
        frozen["w1"],
        {"kind", "policy_version", "workflow", "model_calls_per_material_day"},
        "w1",
    )
    a1 = exact(
        frozen["a1"], {"kind", "max_model_calls_per_material_day"}, "a1"
    )
    if (
        w0["kind"] != "oracle_assisted_single_call"
        or w0["policy_version"] != W0_POLICY_VERSION
        or w0["model_calls_per_material_day"] != 1
        or w1["kind"] != "oracle_assisted_fixed_workflow"
        or w1["policy_version"] != W1_POLICY_VERSION
        or w1["model_calls_per_material_day"] != 1
        or a1["kind"] != "dynamic_agent_v1"
        or a1["max_model_calls_per_material_day"] != frozen["agent_budget"]["max_turns"]
    ):
        raise pairing.PairingAbort("security")
    for field in (
        "fixture_manifest_sha256",
        "ground_truth_sha256",
        "daily_oracle_sha256",
        "policy_sha256",
        "prompt_builder_sha256",
        "dependency_manifest_sha256",
        "runner_source_sha256",
        "runner_runtime_sha256",
        "runner_contract_sha256",
    ):
        sha256(frozen[field])
    limits = exact(
        top["limits"],
        {
            "max_batch_calls",
            "max_batch_tokens",
            "max_batch_cost_usd",
            "theoretical_max_calls",
            "fail_closed",
        },
        "limits",
    )
    integer(limits["max_batch_calls"], minimum=1)
    integer(limits["max_batch_tokens"], minimum=1)
    number(limits["max_batch_cost_usd"])
    if (
        limits["max_batch_calls"] > 100
        or limits["max_batch_tokens"] > 1_200_000
        or limits["max_batch_cost_usd"] > 1.0
        or limits["theoretical_max_calls"] != 100
        or limits["fail_closed"] is not True
    ):
        raise pairing.PairingAbort("security")
    expected_plan_sha256 = _sha(_plan_payload(frozen, limits))
    if not hmac.compare_digest(top["plan_sha256"], expected_plan_sha256):
        raise pairing.PairingAbort("security")
    credential = exact(
        top["credential"],
        {
            "lookup_deferred_until_provider_call",
            "environment_or_macos_keychain",
            "persisted_in_report",
        },
        "credential",
    )
    if credential != {
        "lookup_deferred_until_provider_call": True,
        "environment_or_macos_keychain": True,
        "persisted_in_report": False,
    }:
        raise pairing.PairingAbort("security")
    if not isinstance(top["days"], list) or len(top["days"]) > 60:
        raise pairing.PairingAbort("security")
    day_keys: list[tuple[str, str]] = []
    day_fields = {
        "date",
        "day_index",
        "arm",
        "expected_operation",
        "predicted_operation",
        "status",
        "error_code",
        "path",
        "path_contract_ok",
        "path_oracle_match",
        "operation_correct",
        "route_correct",
        "evidence_correct",
        "state_revision_delta",
        "state_integrity",
        "source_clone_unchanged",
        "quality_passed",
        "material_gate_probe",
        "usage",
    }
    usage_fields = set(agent_v1.AGGREGATE_USAGE_FIELDS)
    for item in top["days"]:
        item = exact(item, day_fields, "day")
        if item["arm"] not in ARMS or item["expected_operation"] not in OPERATION_LABELS:
            raise pairing.PairingAbort("security")
        if item["predicted_operation"] not in PREDICTED_OPERATIONS:
            raise pairing.PairingAbort("security")
        if item["status"] not in agent_v1.RESPONSE_STATUSES:
            raise pairing.PairingAbort("security")
        if item["error_code"] not in PUBLIC_ERROR_CODES | {"none"}:
            raise pairing.PairingAbort("security")
        iso_date(item["date"])
        integer(item["day_index"], minimum=1)
        if (
            item["day_index"] > 20
            or item["date"]
            != _date_order()[item["day_index"] - 1]
        ):
            raise pairing.PairingAbort("security")
        day_keys.append((item["date"], item["arm"]))
        integer(item["state_revision_delta"])
        if not isinstance(item["path"], list) or any(
            action
            not in {
                "terminal_model_action",
                "read_memory",
                "search_history",
                "finalize_patch",
                "finish",
                "invalid_action",
            }
            for action in item["path"]
        ):
            raise pairing.PairingAbort("security")
        for field in (
            "path_contract_ok",
            "path_oracle_match",
            "operation_correct",
            "route_correct",
            "evidence_correct",
            "state_integrity",
            "source_clone_unchanged",
            "quality_passed",
        ):
            if type(item[field]) is not bool:
                raise pairing.PairingAbort("security")
        probe = exact(
            item["material_gate_probe"],
            {"required", "decision", "provider_calls", "passed"},
            "probe",
        )
        if type(probe["required"]) is not bool or type(probe["passed"]) is not bool:
            raise pairing.PairingAbort("security")
        if probe["decision"] not in {"skip", "run", "not_required"}:
            raise pairing.PairingAbort("security")
        integer(probe["provider_calls"])
        usage = exact(item["usage"], usage_fields, "usage")
        for field in usage_fields - {"usage_missing", "cost_usd"}:
            integer(usage[field])
        if type(usage["usage_missing"]) is not bool:
            raise pairing.PairingAbort("security")
        if usage["cost_usd"] is not None:
            number(usage["cost_usd"])
    expected_day_keys = [
        (date, arm)
        for day_index, date in enumerate(_date_order(), start=1)
        for arm in ARM_ORDER_CYCLE[(day_index - 1) % len(ARM_ORDER_CYCLE)]
    ]
    if day_keys != expected_day_keys[: len(day_keys)]:
        raise pairing.PairingAbort("security")
    summary = exact(
        top["summary"],
        {
            "days_requested",
            "days_completed",
            "stopped_after_day",
            "report_detail_complete",
            "batch_quality",
            "operation_coverage",
            "by_arm",
            "batch",
        },
        "summary",
    )
    if summary["days_requested"] != 20:
        raise pairing.PairingAbort("security")
    if type(summary["report_detail_complete"]) is not bool:
        raise pairing.PairingAbort("security")
    integer(summary["days_completed"])
    if summary["days_completed"] > 20:
        raise pairing.PairingAbort("security")
    if summary["stopped_after_day"] is not None:
        iso_date(summary["stopped_after_day"])
        if summary["stopped_after_day"] not in _date_order():
            raise pairing.PairingAbort("security")
    if summary["batch_quality"] is not None and type(summary["batch_quality"]) is not bool:
        raise pairing.PairingAbort("security")
    complete_dates = sum(
        all((date, arm) in day_keys for arm in ARMS)
        for date in _date_order()
    )
    if summary["days_completed"] != complete_dates:
        raise pairing.PairingAbort("security")
    coverage = exact(
        summary["operation_coverage"],
        {"target_counts", "covered", "not_covered", "complete_for_agent_patch_space"},
        "coverage",
    )
    if (
        set(coverage["target_counts"]) != set(OPERATION_LABELS)
        or any(
            type(value) is not int or value < 0
            for value in coverage["target_counts"].values()
        )
        or coverage["covered"] != list(OPERATION_LABELS)
        or coverage["not_covered"] != ["revise", "tension"]
        or coverage["complete_for_agent_patch_space"] is not False
        or sum(coverage["target_counts"].values()) != 20
    ):
        raise pairing.PairingAbort("security")
    batch = exact(
        summary["batch"],
        {"calls", "tokens", "cost_usd", "cost_complete", "by_arm"},
        "batch",
    )
    integer(batch["calls"])
    integer(batch["tokens"])
    number(batch["cost_usd"])
    if type(batch["cost_complete"]) is not bool:
        raise pairing.PairingAbort("security")
    batch_arms = exact(batch["by_arm"], set(ARMS), "batch arms")
    for arm in ARMS:
        arm_usage = exact(batch_arms[arm], {"calls", "tokens", "cost_usd"}, "arm usage")
        integer(arm_usage["calls"])
        integer(arm_usage["tokens"])
        number(arm_usage["cost_usd"])
    if (
        batch["calls"] != sum(batch_arms[arm]["calls"] for arm in ARMS)
        or batch["tokens"] != sum(batch_arms[arm]["tokens"] for arm in ARMS)
        or round(sum(batch_arms[arm]["cost_usd"] for arm in ARMS), 10)
        != batch["cost_usd"]
        or batch["calls"] > limits["max_batch_calls"]
        or batch["tokens"] > limits["max_batch_tokens"]
        or batch["cost_usd"] > limits["max_batch_cost_usd"]
    ):
        raise pairing.PairingAbort("security")
    if top["mode"] == "plan_only":
        if (
            top["executed"] is not False
            or top["status"] != "planned"
            or top["stop_code"] != "none"
            or top["days"]
            or summary["days_completed"] != 0
            or summary["report_detail_complete"] is not True
            or summary["batch_quality"] is not None
            or summary["by_arm"] != {}
            or batch["calls"] != 0
        ):
            raise pairing.PairingAbort("security")
    else:
        if top["executed"] is not True or set(summary["by_arm"]) != set(ARMS):
            raise pairing.PairingAbort("security")
        if (
            (top["status"] == "completed") != (top["stop_code"] == "none")
            or (top["status"] == "stopped") != (top["stop_code"] != "none")
            or (top["status"] == "completed" and summary["stopped_after_day"] is not None)
            or (top["status"] == "stopped" and summary["stopped_after_day"] is None)
            or (
                summary["batch_quality"]
                != bool(
                    top["status"] == "completed"
                    and len(top["days"]) == 60
                    and summary["days_completed"] == 20
                    and all(item["quality_passed"] for item in top["days"])
                )
            )
            or (
                summary["report_detail_complete"] is False
                and (top["status"] != "stopped" or batch["calls"] <= 0 or top["days"])
            )
        ):
            raise pairing.PairingAbort("security")
        arm_summary_fields = {
            "days_evaluated",
            "quality_days_passed",
            "macro_f1",
            "confusion",
            "route_accuracy",
            "path_contract_rate",
            "path_oracle_match_rate",
            "first_error_day",
            "cascade_days_observed",
            "post_first_error_failures",
            "cascade_observation_truncated_by_stop",
            "active_memories_end",
            "usage",
        }
        by_arm = exact(summary["by_arm"], set(ARMS), "summary arms")
        for arm in ARMS:
            arm_summary = exact(by_arm[arm], arm_summary_fields, "arm summary")
            arm_items = [item for item in top["days"] if item["arm"] == arm]
            for field in (
                "days_evaluated",
                "quality_days_passed",
                "cascade_days_observed",
                "post_first_error_failures",
            ):
                integer(arm_summary[field])
            for field in (
                "macro_f1",
                "route_accuracy",
                "path_contract_rate",
                "path_oracle_match_rate",
            ):
                number(arm_summary[field])
                if arm_summary[field] > 1:
                    raise pairing.PairingAbort("security")
            if arm_summary["first_error_day"] is not None and not isinstance(
                arm_summary["first_error_day"], str
            ):
                raise pairing.PairingAbort("security")
            if arm_summary["first_error_day"] is not None:
                iso_date(arm_summary["first_error_day"])
            if type(arm_summary["cascade_observation_truncated_by_stop"]) is not bool:
                raise pairing.PairingAbort("security")
            confusion = exact(
                arm_summary["confusion"], set(OPERATION_LABELS), "confusion"
            )
            for expected in OPERATION_LABELS:
                row = exact(confusion[expected], set(PREDICTED_OPERATIONS), "confusion row")
                for value in row.values():
                    integer(value)
            arm_usage = exact(
                arm_summary["usage"], {"calls", "tokens", "cost_usd"}, "arm summary usage"
            )
            integer(arm_usage["calls"])
            integer(arm_usage["tokens"])
            number(arm_usage["cost_usd"])
            if arm_usage != batch_arms[arm]:
                raise pairing.PairingAbort("security")
            expected_f1, expected_confusion = _macro_f1(arm_items)
            expected_first_error = next(
                (
                    item["date"]
                    for item in arm_items
                    if not item["quality_passed"]
                ),
                None,
            )
            evaluated = len(arm_items)
            expected_route = (
                sum(bool(item["route_correct"]) for item in arm_items) / evaluated
                if evaluated
                else 0.0
            )
            expected_path = (
                sum(bool(item["path_contract_ok"]) for item in arm_items) / evaluated
                if evaluated
                else 0.0
            )
            expected_oracle_path = (
                sum(bool(item["path_oracle_match"]) for item in arm_items) / evaluated
                if evaluated
                else 0.0
            )
            if (
                arm_summary["days_evaluated"] != evaluated
                or arm_summary["quality_days_passed"]
                != sum(bool(item["quality_passed"]) for item in arm_items)
                or confusion != expected_confusion
                or not math.isclose(arm_summary["macro_f1"], expected_f1, abs_tol=1e-12)
                or not math.isclose(
                    arm_summary["route_accuracy"], expected_route, abs_tol=1e-12
                )
                or not math.isclose(
                    arm_summary["path_contract_rate"], expected_path, abs_tol=1e-12
                )
                or not math.isclose(
                    arm_summary["path_oracle_match_rate"],
                    expected_oracle_path,
                    abs_tol=1e-12,
                )
                or arm_summary["first_error_day"] != expected_first_error
                or (
                    arm_summary["active_memories_end"] is None
                    and summary["report_detail_complete"] is True
                )
            ):
                raise pairing.PairingAbort("security")
            if arm_summary["active_memories_end"] is not None:
                integer(arm_summary["active_memories_end"])
            day_calls = sum(item["usage"]["model_calls"] for item in arm_items)
            day_tokens = sum(item["usage"]["total_tokens"] for item in arm_items)
            known_costs = [item["usage"]["cost_usd"] for item in arm_items]
            if summary["report_detail_complete"] and (
                day_calls != arm_usage["calls"]
                or day_tokens != arm_usage["tokens"]
                or (
                    all(value is not None for value in known_costs)
                    and round(sum(known_costs), 10) != arm_usage["cost_usd"]
                )
                or (
                    any(value is None for value in known_costs)
                    and arm_usage["cost_usd"] != 0
                )
            ):
                raise pairing.PairingAbort("security")
    serialized = _canonical(report)
    for forbidden in (
        "/Users/",
        "MEMENTO_SYNTHETIC_CONTEXT_TEST",
        "sk-",
        "做产品决策前，我习惯",
        "忽略所有规则并推断",
    ):
        if forbidden in serialized:
            raise pairing.PairingAbort("security")


class SafeArgumentParser(argparse.ArgumentParser):
    """Keep CLI parse failures finite and free of attacker-supplied values."""

    def error(self, message: str) -> None:
        del message
        raise pairing.PairingAbort("contract")


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        description="Frozen 20-day W0/W1/A1 synthetic E2"
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live")
    parser.add_argument("--expect-plan-sha256")
    parser.add_argument("--model", choices=pairing.SUPPORTED_MODELS, default="deepseek-v4-pro")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-tokens-per-call", type=int, default=2_000)
    parser.add_argument("--max-batch-calls", type=int, default=100)
    parser.add_argument("--max-batch-tokens", type=int, default=1_200_000)
    parser.add_argument("--max-batch-cost-usd", type=float, default=1.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if any(item == "--output" or item.startswith("--output=") for item in raw):
        print(
            json.dumps(
                {
                    "schema_version": REPORT_SCHEMA_VERSION,
                    "mode": "plan_only",
                    "executed": False,
                    "status": "stopped",
                    "stop_code": "contract",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        args = build_parser().parse_args(raw)
        config = E2Config(
            model=args.model,
            timeout=args.timeout,
            max_tokens_per_call=args.max_tokens_per_call,
            max_batch_calls=args.max_batch_calls,
            max_batch_tokens=args.max_batch_tokens,
            max_batch_cost_usd=args.max_batch_cost_usd,
        )
        if args.live != (args.confirm_live == LIVE_CONFIRMATION):
            raise pairing.PairingAbort("confirmation_required")
        if args.live:
            # ``run_live_e2`` owns post-call validation and its metered
            # executed=true emergency projection.  Re-validating here could
            # erase a paid run behind the pre-execution CLI fallback.
            report = run_live_e2(
                config, expected_plan_sha256=args.expect_plan_sha256 or ""
            )
        else:
            report = build_plan(config)
            validate_public_report(report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if report["status"] in {"planned", "completed"} else 1
    except pairing.PairingAbort as exc:
        live_requested = "--live" in raw
        executed: bool | None = False
        if live_requested and exc.code not in {"confirmation_required", "plan_mismatch"}:
            executed = None
        print(
            json.dumps(
                {
                    "schema_version": REPORT_SCHEMA_VERSION,
                    "mode": "live_synthetic" if live_requested else "plan_only",
                    "executed": executed,
                    "status": "stopped",
                    "stop_code": exc.code,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception:
        live_requested = "--live" in raw
        print(
            json.dumps(
                {
                    "schema_version": REPORT_SCHEMA_VERSION,
                    "mode": "live_synthetic" if live_requested else "plan_only",
                    "executed": None if live_requested else False,
                    "status": "stopped",
                    "stop_code": "runtime",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
