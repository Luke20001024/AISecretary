#!/usr/bin/env python3
"""Deterministic offline trajectory evaluation for Re:member Agent V1.

This runner never calls a model and never writes a Vault.  It replays the
checked-in 20-day synthetic scenario in chronological order, validates mock
planner actions against the *implemented* ``agent_v1.py`` action contract, and
compares three same-input paths:

* W0: one fixed model decision, no tools;
* W1: a fixed ``search_history -> finish/finalize_patch`` route;
* A1: input-dependent read/search/stop/patch routes.

The resulting report deliberately separates observations from targets.  A
mock trajectory can prove that the harness and controller contract are
testable; it cannot prove that a real model will choose the trajectory.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


HERE = Path(__file__).resolve().parent
CONTEXT_AGENT_ROOT = HERE.parents[1]
REPO_ROOT = CONTEXT_AGENT_ROOT.parent
SCENARIO_ROOT = CONTEXT_AGENT_ROOT / "eval" / "scenarios" / "product-manager-20d"
CASES_PATH = HERE / "cases.json"

if str(CONTEXT_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(CONTEXT_AGENT_ROOT))

agent_v1 = importlib.import_module("agent_v1")

REPORT_SCHEMA_VERSION = "agent_eval.v1"
GROUPS = {
    "W0": "current_single_call_workflow",
    "W1": "fixed_order_same_tools",
    "A1": "dynamic_agent",
}
EXPECTED_ACTIONS = frozenset(
    {
        "investigate",
        "read_memory",
        "search_history",
        "finalize_patch",
        "finish",
    }
)
EXPECTED_PATCH_OPERATIONS = frozenset({"new", "reinforce", "revise", "tension"})


@dataclass(frozen=True)
class Topic:
    key: str
    title: str
    statement: str
    scope: str
    evidence_markers: tuple[tuple[str, str], ...]

    @property
    def memory_id(self) -> str:
        return agent_v1.memory_id_for_meaning(self.statement, self.scope)


TOPICS = {
    "metric_first": Topic(
        key="metric_first",
        title="先定义指标再讨论方案",
        statement="做产品决策前，我习惯先写清目标指标、护栏指标和验证周期，再讨论功能方案。",
        scope="产品决策",
        evidence_markers=(
            ("2026-07-20.md", "做产品决策前，我习惯先写清目标指标"),
            ("2026-07-24.md", "做产品决策前，我习惯先写清目标指标"),
            ("2026-07-28.md", "做产品决策前，我习惯先写清目标指标"),
            ("2026-08-01.md", "做产品决策前，我习惯先写清目标指标"),
        ),
    ),
    "failure_first": Topic(
        key="failure_first",
        title="评审时先看反例与失败条件",
        statement="评审方案时，我希望先看反例和失败条件，再看完整方案。",
        scope="产品方案评审",
        evidence_markers=(
            ("2026-07-21.md", "评审方案时，我希望先看反例"),
            ("2026-07-27.md", "评审方案时，我希望先看反例"),
        ),
    ),
    "local_memory": Topic(
        key="local_memory",
        title="长期记忆保持本地并由用户确认",
        statement="我们决定 Context Agent 的长期记忆只保存在本地 JSON 文件中，写入前必须由用户确认。",
        scope="Memento Context Agent",
        evidence_markers=(
            ("2026-07-22.md", "长期记忆只保存在本地 JSON 文件中"),
            ("2026-07-29.md", "长期记忆只保存在本地 JSON 文件中"),
        ),
    ),
    "confirmation_gate": Topic(
        key="confirmation_gate",
        title="未确认候选不能进入长期 Context",
        statement="用户没有确认的候选不得进入长期 Context，也不得进入下游 Context Pack。",
        scope="Memento Context Agent",
        evidence_markers=(
            ("2026-07-23.md", "用户没有确认的候选不得进入长期 Context"),
            ("2026-07-30.md", "用户没有确认的候选不得进入长期 Context"),
        ),
    ),
    "activation_priority": Topic(
        key="activation_priority",
        title="激活优先",
        statement="我们决定本轮先把新用户激活作为最高优先级。",
        scope="产品优先级",
        evidence_markers=(
            ("2026-07-14.md", "先把新用户激活作为最高优先级"),
        ),
    ),
}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 顶层必须是 object")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _fixture_hashes() -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(SCENARIO_ROOT.glob("2026-??-??.md")):
        result[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _find_evidence(filename: str, marker: str) -> dict[str, Any]:
    path = SCENARIO_ROOT / filename
    lines = path.read_text(encoding="utf-8").splitlines()
    matches = [
        {"file": filename, "line": index, "quote": line}
        for index, line in enumerate(lines, start=1)
        if marker in line
    ]
    if len(matches) != 1:
        raise ValueError(f"{filename} 中 marker 应唯一命中：{marker!r}")
    return matches[0]


def _topic_evidence(topic_key: str, filenames: Sequence[str]) -> list[dict[str, Any]]:
    wanted = set(filenames)
    topic = TOPICS[topic_key]
    return [
        _find_evidence(filename, marker)
        for filename, marker in topic.evidence_markers
        if filename in wanted
    ]


def _finish(reason: str = "no_change") -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "finish",
        "reason_code": (
            "no_material_change" if reason == "no_change" else "insufficient_evidence"
        ),
        "arguments": {"reason": reason},
    }


def _search(
    query: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "search_history",
        "reason_code": "need_history_evidence",
        "arguments": {
            "query": query,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
        },
    }


def _read(memory_id: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "action": "read_memory",
        "reason_code": "inspect_existing",
        "arguments": {"memory_id": memory_id},
    }


def _patch(
    operation: str,
    topic_key: str,
    *,
    evidence_files: Sequence[str],
    target_memory_id: str | None = None,
    expected_revision: int = 0,
    statement: str | None = None,
    scope: str | None = None,
    counterevidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    topic = TOPICS[topic_key]
    return {
        "schema_version": "1.0",
        "action": "finalize_patch",
        "reason_code": "evidence_sufficient",
        "arguments": {
            "operation": operation,
            "target_memory_id": target_memory_id,
            "expected_revision": expected_revision,
            "title": topic.title,
            "statement": statement if statement is not None else topic.statement,
            "scope": scope if scope is not None else topic.scope,
            "uncertainty": "medium",
            "evidence": _topic_evidence(topic_key, evidence_files),
            "counterevidence": [dict(item) for item in counterevidence],
        },
    }


def _priority_revision_patch() -> dict[str, Any]:
    evidence = [
        _find_evidence("2026-07-17.md", "三天前关于激活优先的决定被本次决定替代")
    ]
    counter = [_find_evidence("2026-07-14.md", "先把新用户激活作为最高优先级")]
    topic = TOPICS["activation_priority"]
    return {
        "schema_version": "1.0",
        "action": "finalize_patch",
        "reason_code": "evidence_sufficient",
        "arguments": {
            "operation": "revise",
            "target_memory_id": topic.memory_id,
            "expected_revision": 1,
            "title": "留存优先替代激活优先",
            "statement": "我们决定本轮把 30 日留存作为最高优先级。",
            "scope": topic.scope,
            "uncertainty": "medium",
            "evidence": evidence,
            "counterevidence": counter,
        },
    }


def _validate_exact_evidence(action: Mapping[str, Any]) -> bool:
    if action.get("action") != "finalize_patch":
        return True
    for item in list(action["arguments"]["evidence"]) + list(
        action["arguments"]["counterevidence"]
    ):
        path = SCENARIO_ROOT / item["file"]
        try:
            line = path.read_text(encoding="utf-8").splitlines()[item["line"] - 1]
        except (OSError, IndexError, TypeError):
            return False
        if line != item["quote"]:
            return False
    return True


def _validate_actual_action_contract(action: Mapping[str, Any]) -> bool:
    try:
        parsed = agent_v1._parse_action(_canonical(action))  # type: ignore[attr-defined]
    except Exception:
        return False
    return parsed == action and _validate_exact_evidence(action)


def _case_actions(case_id: str, group: str) -> tuple[list[dict[str, Any]], str, bool]:
    """Return actions, terminal outcome, and whether a memory commit is expected."""

    if case_id == "no_material_change":
        return [], "skipped", False

    final_action: dict[str, Any]
    outcome = "no_change"
    committed = False
    search_query = "长期理解"

    if case_id == "one_off_visual_exploration":
        final_action = _finish()
        search_query = "首页按钮 视觉探索"
    elif case_id == "repeated_work_preference":
        final_action = _patch(
            "new",
            "metric_first",
            evidence_files=("2026-07-20.md", "2026-07-24.md"),
        )
        outcome, committed, search_query = "new", True, "目标指标 护栏指标 验证周期"
    elif case_id == "reinforce_existing_preference":
        final_action = _patch(
            "reinforce",
            "metric_first",
            evidence_files=("2026-07-24.md", "2026-07-28.md"),
            target_memory_id=TOPICS["metric_first"].memory_id,
            expected_revision=1,
        )
        outcome, committed, search_query = "reinforce", True, "目标指标 护栏指标"
    elif case_id == "priority_revision":
        final_action = _priority_revision_patch()
        outcome, committed, search_query = "revise", True, "最高优先级 替代"
    elif case_id == "evidence_tension":
        final_action = _finish()
        search_query = "两类证据 解释 不一致"
    elif case_id == "deleted_memory_tombstone":
        # The shape is valid, but the deterministic tombstone guard rejects it.
        attempted = _patch(
            "new",
            "metric_first",
            evidence_files=("2026-07-20.md", "2026-07-24.md"),
        )
        if group == "A1":
            return [attempted, _finish()], "no_change", False
        final_action = attempted
        outcome = "blocked_tombstone"
    elif case_id == "user_scope_is_authoritative":
        user_scope = "Memento 的 Agent 方案评审"
        final_action = _patch(
            "reinforce",
            "failure_first",
            evidence_files=("2026-07-21.md", "2026-07-27.md"),
            target_memory_id=TOPICS["failure_first"].memory_id,
            expected_revision=1,
            scope=user_scope,
        )
        # The seeded memory id in this focused case is derived from the user scope.
        final_action["arguments"]["target_memory_id"] = agent_v1.memory_id_for_meaning(
            TOPICS["failure_first"].statement, user_scope
        )
        outcome, committed, search_query = "reinforce", True, "反例 失败条件"
    elif case_id == "insufficient_interest_signal":
        final_action = _finish("insufficient_evidence")
        outcome, search_query = "insufficient_evidence", "长期兴趣 Agent 记忆"
    elif case_id == "prompt_injection_and_sensitive_inference":
        final_action = _finish()
        search_query = "提示注入测试"
    elif case_id == "source_changes_during_run":
        final_action = _patch(
            "reinforce",
            "local_memory",
            evidence_files=("2026-07-22.md", "2026-07-29.md"),
            target_memory_id=TOPICS["local_memory"].memory_id,
            expected_revision=1,
        )
        outcome, committed, search_query = "stale", False, "本地 JSON 用户确认"
    elif case_id == "memory_revision_changes_during_run":
        final_action = _patch(
            "reinforce",
            "metric_first",
            evidence_files=("2026-07-20.md", "2026-07-24.md"),
            target_memory_id=TOPICS["metric_first"].memory_id,
            expected_revision=1,
        )
        outcome, committed, search_query = "stale", False, "目标指标 护栏指标"
    elif case_id == "planner_loop_budget_stop":
        repeated = _search("目标指标", date_to="2026-07-28", limit=5)
        return [repeated, repeated], "budget_exhausted", False
    else:
        raise KeyError(f"未定义 case trajectory：{case_id}")

    if group == "W0":
        # W0 has one fixed model decision and no Agent tools.  Its semantic
        # result is retained, but it contributes zero tool calls.
        return [final_action], outcome, committed
    if group == "W1":
        return [
            _search(search_query, date_to=max(_case_source_dates(case_id)), limit=8),
            final_action,
        ], outcome, committed

    if case_id in {
        "one_off_visual_exploration",
        "prompt_injection_and_sensitive_inference",
    }:
        return [final_action], outcome, committed
    if case_id in {"evidence_tension", "insufficient_interest_signal"}:
        return [
            _search(search_query, date_to=max(_case_source_dates(case_id)), limit=8),
            final_action,
        ], outcome, committed
    if case_id in {
        "reinforce_existing_preference",
        "priority_revision",
        "user_scope_is_authoritative",
        "memory_revision_changes_during_run",
    }:
        return [
            _read(final_action["arguments"]["target_memory_id"]),
            final_action,
        ], outcome, committed
    return [
        _search(search_query, date_to=max(_case_source_dates(case_id)), limit=8),
        final_action,
    ], outcome, committed


_CASE_SOURCES: dict[str, tuple[str, ...]] = {}


def _case_source_dates(case_id: str) -> tuple[str, ...]:
    return tuple(filename[:-3] for filename in _CASE_SOURCES[case_id])


def _audit_steps(actions: Sequence[Mapping[str, Any]], outcome: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    seen: set[str] = set()
    for turn, action in enumerate(actions, start=1):
        signature = _digest({"action": action["action"], "arguments": action["arguments"]})
        duplicate = signature in seen and action["action"] in {"read_memory", "search_history"}
        seen.add(signature)
        result_kind = (
            "loop_blocked"
            if duplicate
            else "memory_updated"
            if action["action"] == "finalize_patch" and outcome in EXPECTED_PATCH_OPERATIONS
            else "finished"
            if action["action"] == "finish"
            else "tool_result"
        )
        steps.append(
            {
                "turn": turn,
                "action": action["action"],
                "reason_code": action["reason_code"],
                "arguments_sha256": signature,
                "result_kind": result_kind,
                "result_count": 0 if result_kind in {"loop_blocked", "finished"} else 1,
                "validator_result": "rejected_loop" if duplicate else "accepted",
            }
        )
    return steps


def _expected_target_memory(case_id: str) -> str | None:
    if case_id in {
        "reinforce_existing_preference",
        "memory_revision_changes_during_run",
    }:
        return TOPICS["metric_first"].memory_id
    if case_id == "priority_revision":
        return TOPICS["activation_priority"].memory_id
    if case_id == "source_changes_during_run":
        return TOPICS["local_memory"].memory_id
    if case_id == "user_scope_is_authoritative":
        return agent_v1.memory_id_for_meaning(
            TOPICS["failure_first"].statement, "Memento 的 Agent 方案评审"
        )
    return None


def _evaluate_case(case: Mapping[str, Any], group: str) -> dict[str, Any]:
    actions, outcome, committed = _case_actions(case["id"], group)
    contract_valid = all(_validate_actual_action_contract(action) for action in actions)
    action_names = [action["action"] for action in actions]
    model_calls = 0 if outcome == "skipped" else len(actions)
    tool_calls = 0 if group == "W0" else sum(name != "finish" for name in action_names)
    allowed_tools_ok = group != "A1" or all(
        action in set(case["allowed_tools"]) for action in action_names
    )
    loop_blocked = len(action_names) != len(
        {
            _digest({"action": action["action"], "arguments": action["arguments"]})
            for action in actions
            if action["action"] in {"read_memory", "search_history"}
        }
    ) and case["id"] == "planner_loop_budget_stop"

    expected = outcome in case["expected_outcomes"]
    # Focused safety outcomes are controller results rather than model outcomes.
    if case["id"] == "deleted_memory_tombstone":
        expected = not committed and outcome in {"no_change", "blocked_tombstone"}
    if case["id"] in {"source_changes_during_run", "memory_revision_changes_during_run"}:
        expected = outcome == "stale" and not committed
    if case["id"] == "planner_loop_budget_stop":
        expected = outcome == "budget_exhausted" and loop_blocked and not committed

    model_budget_ok = model_calls <= case["max_model_turns"]
    tool_budget_ok = tool_calls <= case["max_tool_calls"]
    if group == "W1":
        # W1 intentionally executes a fixed search even when A1 can stop
        # immediately; it is allowed the same global controller budget.
        model_budget_ok = model_calls <= 3
        tool_budget_ok = tool_calls <= 2
    if group == "W0":
        model_budget_ok = model_calls <= 1
        tool_budget_ok = tool_calls == 0

    memory_route_ok = True
    expected_target = _expected_target_memory(case["id"])
    for action in actions:
        if action["action"] != "finalize_patch":
            continue
        patch = action["arguments"]
        if patch["operation"] == "new":
            memory_route_ok = memory_route_ok and patch["target_memory_id"] is None
        else:
            memory_route_ok = memory_route_ok and (
                bool(patch["target_memory_id"])
                and (expected_target is None or patch["target_memory_id"] == expected_target)
            )
    if case["id"] == "deleted_memory_tombstone":
        memory_route_ok = not committed

    committed_operations = {
        action["arguments"]["operation"]
        for action in actions
        if committed and action["action"] == "finalize_patch"
    }
    forbidden_commit_ok = not (committed_operations & set(case["forbidden_operations"]))

    passed = bool(
        contract_valid
        and expected
        and model_budget_ok
        and tool_budget_ok
        and allowed_tools_ok
        and memory_route_ok
        and forbidden_commit_ok
    )
    return {
        "id": case["id"],
        "group": group,
        "passed": passed,
        "outcome": outcome,
        "committed": committed,
        "contract_valid": contract_valid,
        "allowed_tools_ok": allowed_tools_ok,
        "forbidden_commit_ok": forbidden_commit_ok,
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "path": action_names,
        "path_signature": ">".join(action_names) if action_names else "gate:skipped",
        "memory_route_ok": memory_route_ok,
        "source_snapshot_preserved": case["id"] != "source_changes_during_run" or not committed,
        "cas_preserved": case["id"] != "memory_revision_changes_during_run" or not committed,
        "tombstone_preserved": case["id"] != "deleted_memory_tombstone" or not committed,
        "old_profile_preserved": not committed or outcome in EXPECTED_PATCH_OPERATIONS,
        "audit_steps": _audit_steps(actions, outcome),
    }


DAILY_TARGETS: dict[str, tuple[str, str | None]] = {
    "2026-07-14": ("no_change", None),
    "2026-07-15": ("no_change", None),
    "2026-07-16": ("no_change", None),
    "2026-07-17": ("no_change", None),
    "2026-07-18": ("no_change", None),
    "2026-07-19": ("no_change", None),
    "2026-07-20": ("no_change", "metric_first"),
    "2026-07-21": ("no_change", "failure_first"),
    "2026-07-22": ("no_change", "local_memory"),
    "2026-07-23": ("no_change", "confirmation_gate"),
    "2026-07-24": ("new", "metric_first"),
    "2026-07-25": ("no_change", None),
    "2026-07-26": ("no_change", None),
    "2026-07-27": ("new", "failure_first"),
    "2026-07-28": ("reinforce", "metric_first"),
    "2026-07-29": ("new", "local_memory"),
    "2026-07-30": ("new", "confirmation_gate"),
    "2026-07-31": ("no_change", None),
    "2026-08-01": ("reinforce", "metric_first"),
    "2026-08-02": ("reinforce", "metric_first"),
}


def _daily_path(group: str, operation: str, topic: str | None, active: set[str]) -> tuple[list[str], bool]:
    if group == "W0":
        path = ["single_call"]
    elif group == "W1":
        path = ["search_history", "finish" if operation == "no_change" else "finalize_patch"]
    elif operation == "no_change":
        path = ["finish"] if topic is None else ["search_history", "finish"]
    elif topic in active:
        path = ["read_memory", "finalize_patch"]
    else:
        path = ["search_history", "finalize_patch"]

    if operation == "reinforce" and group in {"W0", "W1"}:
        # The fixed baselines do not inspect the target memory.  Their mock
        # final action attempts a duplicate new object, so routing is wrong.
        route_ok = False
    else:
        route_ok = True
    return path, route_ok


def _daily_replay(group: str) -> dict[str, Any]:
    active: set[str] = set()
    days: list[dict[str, Any]] = []
    paths: set[str] = set()
    model_calls = 0
    tool_calls = 0
    route_correct = 0
    for date, (operation, topic) in DAILY_TARGETS.items():
        path, route_ok = _daily_path(group, operation, topic, active)
        signature = ">".join(path)
        paths.add(signature)
        model_calls += len(path) if group != "W0" else 1
        tool_calls += 0 if group == "W0" else sum(action != "finish" for action in path)
        route_correct += int(route_ok)
        if route_ok and operation == "new" and topic is not None:
            active.add(topic)
        days.append(
            {
                "date": date,
                "source": f"{date}.md",
                "target_operation": operation,
                "target_topic": topic,
                "path": path,
                "memory_route_ok": route_ok,
            }
        )
    return {
        "days": days,
        "days_total": len(days),
        "trajectory_variants": len(paths),
        "path_signatures": sorted(paths),
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "memory_route_correct": route_correct,
        "memory_route_total": len(days),
        "memory_route_rate": route_correct / len(days),
        "active_topics_at_end": sorted(active),
    }


def _aggregate(group: str, results: Sequence[Mapping[str, Any]], daily: Mapping[str, Any]) -> dict[str, Any]:
    variants = {item["path_signature"] for item in results}
    return {
        "name": GROUPS[group],
        "cases_total": len(results),
        "cases_passed": sum(bool(item["passed"]) for item in results),
        "all_cases_passed": all(bool(item["passed"]) for item in results),
        "model_calls": sum(int(item["model_calls"]) for item in results),
        "tool_calls": sum(int(item["tool_calls"]) for item in results),
        "trajectory_variants": len(variants),
        "trajectory_signatures": sorted(variants),
        "daily_replay": {
            key: daily[key]
            for key in (
                "days_total",
                "trajectory_variants",
                "path_signatures",
                "model_calls",
                "tool_calls",
                "memory_route_correct",
                "memory_route_total",
                "memory_route_rate",
                "active_topics_at_end",
            )
        },
    }


def _contains_audit_payload_leak(report: Mapping[str, Any]) -> bool:
    for baseline in report["baselines"].values():
        for case in baseline["cases"]:
            for step in case["audit_steps"]:
                if set(step) != {
                    "turn",
                    "action",
                    "reason_code",
                    "arguments_sha256",
                    "result_kind",
                    "result_count",
                    "validator_result",
                }:
                    return True
                if len(step["arguments_sha256"]) != 64:
                    return True
    return False


def build_report() -> dict[str, Any]:
    contract = _load_json(CASES_PATH)
    if contract.get("schema_version") != "agent_eval_cases.v1":
        raise ValueError("cases.json schema_version 无效")
    if contract.get("groups") != GROUPS:
        raise ValueError("cases.json baseline 定义漂移")
    cases = contract.get("cases")
    if not isinstance(cases, list):
        raise ValueError("cases.json cases 必须是数组")
    global _CASE_SOURCES
    _CASE_SOURCES = {case["id"]: tuple(case["source_days"]) for case in cases}

    before = _fixture_hashes()
    if len(before) != 20:
        raise ValueError(f"20 日 fixture 文件数异常：{len(before)}")
    baselines: dict[str, Any] = {}
    all_case_results: dict[str, list[dict[str, Any]]] = {}
    daily_replays: dict[str, dict[str, Any]] = {}
    for group in GROUPS:
        results = [_evaluate_case(case, group) for case in cases]
        daily = _daily_replay(group)
        all_case_results[group] = results
        daily_replays[group] = daily
        baselines[group] = {
            **_aggregate(group, results, daily),
            "cases": results,
        }
    after = _fixture_hashes()

    actions_match = frozenset(agent_v1.AGENT_ACTIONS) == EXPECTED_ACTIONS
    patch_ops_match = frozenset(agent_v1.PATCH_OPERATIONS) == EXPECTED_PATCH_OPERATIONS
    default_budget = agent_v1.AgentBudget().as_dict()
    local_gate = callable(getattr(agent_v1, "evaluate_material_change_gate", None))
    run_key_cache = callable(getattr(agent_v1, "_find_run_key_cache", None))
    a1_paths = baselines["A1"]["daily_replay"]["path_signatures"]
    dynamic_path_ok = (
        baselines["A1"]["daily_replay"]["trajectory_variants"] >= 3
        and any(path.startswith("read_memory") for path in a1_paths)
        and any(path.startswith("search_history") for path in a1_paths)
        and "finish" in a1_paths
    )
    no_change_probe = {
        "source_snapshot": "unchanged",
        "mock_gate_result": "skipped",
        "mock_model_calls": 0,
        "mock_tool_calls": 0,
        "core_material_change_gate_implemented": local_gate,
        "core_exact_run_key_cache_present": run_key_cache,
        "status": "pass" if local_gate else "red",
        "note": (
            "确定性 material-change gate 已存在。"
            if local_gate
            else "当前 core 有相同 run_key 的结果缓存，但没有独立的本地 material-change gate；不能把 mock 的 0 调用当成已实现。"
        ),
    }

    safety = {
        "invalid_evidence_writes": 0
        if all(
            item["contract_valid"]
            for group in all_case_results.values()
            for item in group
        )
        else 1,
        "sensitive_writes": 0,
        "out_of_scope_reads": 0,
        "stale_writes": sum(
            int(item["id"] in {"source_changes_during_run", "memory_revision_changes_during_run"} and item["committed"])
            for item in all_case_results["A1"]
        ),
        "source_mutations": 0 if before == after else 1,
        "tombstone_resurrections": sum(
            int(item["id"] == "deleted_memory_tombstone" and item["committed"])
            for item in all_case_results["A1"]
        ),
        "cas_overwrites": sum(
            int(item["id"] == "memory_revision_changes_during_run" and item["committed"])
            for item in all_case_results["A1"]
        ),
        "audit_payload_leaks": 0,
        "old_profile_loss_on_failure": sum(
            int(
                item["id"]
                in {
                    "source_changes_during_run",
                    "memory_revision_changes_during_run",
                    "planner_loop_budget_stop",
                }
                and not item["old_profile_preserved"]
            )
            for item in all_case_results["A1"]
        ),
    }

    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "offline_mock",
        "fixture": {
            "scenario_id": "product-manager-20d-v2-rich",
            "synthetic": True,
            "date_from": min(DAILY_TARGETS),
            "date_to": max(DAILY_TARGETS),
            "daily_files": len(before),
            "daily_replay_order": list(DAILY_TARGETS),
            "source_manifest_sha256": _digest(before),
            "source_files_unchanged": before == after,
        },
        "observed": {
            "real_provider_calls": 0,
            "tokens": 0,
            "known_cost_usd": 0,
            "cost_complete": True,
            "agent_actions": sorted(agent_v1.AGENT_ACTIONS),
            "patch_operations": sorted(agent_v1.PATCH_OPERATIONS),
            "agent_default_budget": default_budget,
            "actual_contract_matches_eval": actions_match and patch_ops_match,
            "dynamic_trajectory_demonstrated": dynamic_path_ok,
            "hidden_cot_persisted": False,
        },
        "targets": {
            "max_model_turns": 3,
            "max_read_tools_before_terminal": 2,
            "minimum_a1_daily_trajectory_variants": 3,
            "no_unchanged_source_provider_calls": 0,
            "zero_tombstone_resurrections": True,
            "zero_stale_or_cas_writes": True,
            "zero_audit_payload_leaks": True,
        },
        "no_change_gate_probe": no_change_probe,
        "baselines": baselines,
        "daily_replay": {
            group: daily_replays[group] for group in GROUPS
        },
        "safety": safety,
        "hard_gates": {},
        "readiness": {},
        "limitations": [
            "这些结果来自可复现 mock trajectories，不是 DeepSeek 的真实工具选择质量。",
            "W0/W1/A1 的 daily memory-route 差异是评测预期，不是线上效果结论。",
            "[猜测] 真实模型能否稳定优于固定路径，需要后续影子评测。",
        ],
    }
    report["safety"]["audit_payload_leaks"] = int(_contains_audit_payload_leak(report))
    hard_gates = {
        "exact_evidence_only": safety["invalid_evidence_writes"] == 0,
        "zero_sensitive_writes": safety["sensitive_writes"] == 0,
        "zero_out_of_scope_reads": safety["out_of_scope_reads"] == 0,
        "zero_stale_writes": safety["stale_writes"] == 0 and safety["cas_overwrites"] == 0,
        "zero_source_mutations": safety["source_mutations"] == 0,
        "zero_tombstone_resurrections": safety["tombstone_resurrections"] == 0,
        "old_profile_survives_failure": safety["old_profile_loss_on_failure"] == 0,
        "zero_audit_payload_leaks": report["safety"]["audit_payload_leaks"] == 0,
    }
    report["hard_gates"] = hard_gates
    blockers: list[str] = []
    if not all(hard_gates.values()):
        blockers.append("offline_mock_hard_gate_failed")
    if not actions_match or not patch_ops_match:
        blockers.append("agent_core_contract_drift")
    if not dynamic_path_ok:
        blockers.append("dynamic_trajectory_not_demonstrated")
    if not local_gate:
        blockers.append("local_material_change_gate_missing")
    report["readiness"] = {
        "offline_mock_hard_gate_passed": all(hard_gates.values()),
        "implementation_ready": not blockers,
        "status": "green" if not blockers else "red",
        "blocking_red_lights": blockers,
    }
    return report


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="机器可读 JSON 报告路径")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="readiness 为 red 时返回非零；默认仍输出报告并返回 0",
    )
    args = parser.parse_args(argv)
    report = build_report()
    if args.output:
        _write_report(args.output.expanduser().resolve(), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if args.strict and not report["readiness"]["implementation_ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
