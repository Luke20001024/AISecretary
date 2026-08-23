#!/usr/bin/env python3
"""Memento Context Agent CLI."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from core import (
    ContractError,
    DAILY_NAME_RE,
    Pricing,
    append_usage_log,
    build_context_pack,
    build_generation_messages,
    calculate_cost,
    collect_sources,
    create_pending,
    decide_candidate,
    normalize_usage,
    pricing_for_model,
    read_json,
    source_hashes,
    usage_is_missing,
    validate_confirmed,
    validate_model_response,
    validate_pending,
)
from deepseek_provider import DEFAULT_MODEL, DeepSeekProvider, ProviderError
from agent_v1 import (
    AgentBudget,
    MockPlanner,
    agent_v1_root,
    build_agent_profile,
    create_agent_request,
    disable_agent_schedule,
    disable_agent_v1,
    enable_agent_schedule,
    enable_agent_v1,
    inspect_agent_schedule,
    inspect_agent_v1_gate,
    persist_agent_profile,
    process_agent_request,
    reconcile_agent_state,
    require_agent_v1_enabled,
    response_path as agent_response_path,
    validate_agent_profile,
    validate_agent_request,
    validate_agent_response,
    validate_memory_revision,
    validate_user_action,
)
from reflection import (
    DEFAULT_MAX_SOURCE_CHARS as DEFAULT_REFLECTION_MAX_SOURCE_CHARS,
    build_active_profile,
    build_profile_pack,
    process_reflection_request,
    response_path as reflection_response_path,
    self_query_root,
    validate_query_response,
    validate_reflection_feedback,
    validate_reflection_request,
)
from cognitive_actions_v1 import CognitiveActionStore
from cognitive_bundle_store_v1 import CognitiveBundleStore
from cognitive_day_orchestrator_v1 import (
    CognitiveDayOrchestrator,
    CognitiveDayResult,
    inspect_cognitive_day_status,
)
from cognitive_migration_v1 import CognitiveMigration
from cognitive_manual_request_v1 import ManualDayRequestStore
from cognitive_pipeline_v1 import CognitivePipeline
from cognitive_projection_v1 import CognitiveProjectionPublisher
from cognitive_record_worker_v1 import CognitiveRecordWorker, RecordWorkerResult
from cognitive_runtime_v1 import CognitiveRuntime
from cognitive_schedule_v1 import CognitiveScheduleCore, inspect_day_completion
from cognitive_store_v1 import RecordStore
from cognitive_v1 import ObjectRef, make_receipt_id


SUPPORTED_MODELS = ("deepseek-v4-pro", "deepseek-v4-flash")
PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CASES_DIR = PACKAGE_DIR / "eval" / "cases"
AGENT_ENABLE_CONFIRMATION = "enable-remember-agent-v1"
AGENT_DISABLE_CONFIRMATION = "disable-remember-agent-v1"
AGENT_SCHEDULE_ENABLE_CONFIRMATION = "enable-remember-agent-daily-21"
AGENT_SCHEDULE_DISABLE_CONFIRMATION = "disable-remember-agent-daily-21"


def _vault_path(value: str | None) -> Path:
    selected = value or os.environ.get("MEMENTO_VAULT")
    if not selected:
        raise ContractError("请提供 --vault 或设置 MEMENTO_VAULT")
    return Path(selected).expanduser().resolve()


def _json_dump(value: Any, *, stream: Any | None = None) -> None:
    if stream is None:
        stream = sys.stdout
    json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
    stream.write("\n")


def _parse_json_text(text: str, source_name: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError(
            f"{source_name} 不是合法 JSON（第 {exc.lineno} 行）"
        ) from exc
    if not isinstance(value, dict):
        raise ContractError(f"{source_name} 顶层必须是 JSON object")
    return value


def _pricing_from_args(args: argparse.Namespace, model: str = DEFAULT_MODEL) -> Pricing:
    defaults = pricing_for_model(model)
    return Pricing(
        cache_hit_input_usd_per_million=(
            defaults.cache_hit_input_usd_per_million
            if args.cache_hit_rate is None
            else args.cache_hit_rate
        ),
        cache_miss_input_usd_per_million=(
            defaults.cache_miss_input_usd_per_million
            if args.cache_miss_rate is None
            else args.cache_miss_rate
        ),
        output_usd_per_million=(
            defaults.output_usd_per_million
            if args.output_rate is None
            else args.output_rate
        ),
        effective_date=args.pricing_date or defaults.effective_date,
    )


def _provider(
    args: argparse.Namespace,
    model: str | None = None,
    *,
    max_tokens: int = 1200,
) -> DeepSeekProvider:
    return DeepSeekProvider(
        model=model or args.model,
        timeout=args.timeout,
        thinking=args.thinking,
        reasoning_effort=args.reasoning_effort,
        max_tokens=max_tokens,
    )


def _log_provider_error_usage(
    vault: Path,
    args: argparse.Namespace,
    error: ProviderError,
    *,
    requested_model: str,
) -> Mapping[str, Any] | None:
    """Persist billing metadata from a structured but failed completion."""

    if error.usage is None:
        return None
    return append_usage_log(
        vault,
        model=error.model or requested_model,
        provider="deepseek",
        usage=error.usage,
        pricing=_pricing_from_args(args, requested_model),
        request_id=error.request_id,
    )


def command_generate(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    paths = collect_sources(
        vault,
        args.source,
        limit=args.latest,
        maximum_chars=args.max_source_chars,
    )
    hashes = source_hashes(paths)
    usage_event: Mapping[str, Any] | None = None
    response_model = args.model

    if args.mock_response:
        response = read_json(Path(args.mock_response).expanduser())
        provider_name = "mock"
    else:
        provider_name = "deepseek"
        try:
            completion = _provider(args).complete(build_generation_messages(paths))
        except ProviderError as exc:
            _log_provider_error_usage(
                vault, args, exc, requested_model=args.model
            )
            raise
        response_model = completion.model
        usage_event = append_usage_log(
            vault,
            model=completion.model,
            provider=provider_name,
            usage=completion.usage,
            pricing=_pricing_from_args(args, args.model),
            request_id=completion.request_id,
        )
        response = _parse_json_text(completion.content, "模型输出")

    response = validate_model_response(response, vault)
    if response["status"] == "no_candidate":
        result: dict[str, Any] = {
            "schema_version": "1.0",
            "status": "no_candidate",
            "sources": [item["file"] for item in hashes],
        }
    else:
        pending, path = create_pending(
            response,
            vault,
            provider=provider_name,
            model=response_model,
            hashes=hashes,
        )
        result = {
            "schema_version": "1.0",
            "status": "candidate",
            "candidate": pending,
            "candidate_path": str(path),
        }
    if usage_event is not None:
        result["usage"] = usage_event
    _json_dump(result)
    return 0


def command_validate(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    path = Path(args.input).expanduser().resolve()
    value = read_json(path)
    if not isinstance(value, dict):
        raise ContractError("输入顶层必须是 JSON object")
    record_kind = value.get("kind")
    if record_kind == "remember_agent_request":
        validate_agent_request(value)
        kind = "remember_agent_request"
    elif record_kind == "remember_agent_response":
        validate_agent_response(value, vault)
        kind = "remember_agent_response"
    elif record_kind == "remember_memory_revision":
        validate_memory_revision(value, vault)
        kind = "remember_memory_revision"
    elif record_kind == "remember_agent_user_action":
        validate_user_action(value)
        kind = "remember_agent_user_action"
    elif record_kind == "remember_agent_profile":
        validate_agent_profile(value, vault)
        kind = "remember_agent_profile"
    elif record_kind == "self_reflection_request":
        validate_reflection_request(value)
        kind = "self_reflection_request"
    elif record_kind == "self_reflection_response":
        validate_query_response(value, vault)
        kind = "self_reflection_response"
    elif record_kind == "self_reflection_feedback":
        request_id = value.get("request_id")
        if not isinstance(request_id, str):
            raise ContractError("feedback.request_id 格式无效")
        related_response = reflection_response_path(vault, request_id)
        if not related_response.is_file():
            raise ContractError("feedback 引用的 response 不存在", kind="not_found")
        validate_reflection_feedback(value, response_bytes=related_response.read_bytes())
        kind = "self_reflection_feedback"
    elif value.get("status") == "active":
        validate_confirmed(value, vault)
        kind = "confirmed_context"
    elif "id" in value:
        validate_pending(value, vault)
        kind = "candidate"
    else:
        validate_model_response(value, vault)
        kind = "model_response"
    _json_dump({"valid": True, "kind": kind, "path": str(path)})
    return 0


def command_decide(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    result = decide_candidate(
        vault,
        args.candidate,
        args.action,
        statement=args.statement,
        scope=args.scope,
    )
    _json_dump(result)
    return 0


def command_pack(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    markdown, stats = build_context_pack(vault, scope=args.scope)
    if args.output:
        output = Path(args.output).expanduser().resolve()
        resolved_vault = vault.resolve()
        if (
            DAILY_NAME_RE.fullmatch(output.name)
            and output.parent == resolved_vault
        ):
            raise ContractError("Context Pack 不得覆盖 vault 的原始每日记录")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
        _json_dump({"status": "written", "path": str(output), **stats})
    else:
        sys.stdout.write(markdown)
    return 0


def command_profile(args: argparse.Namespace) -> int:
    """Build the read-only active profile without invoking a model."""

    vault = _vault_path(args.vault)
    if args.format == "markdown":
        markdown, _ = build_profile_pack(vault)
        sys.stdout.write(markdown)
    else:
        _json_dump(build_active_profile(vault))
    return 0


def _reflection_mock_response(args: argparse.Namespace) -> Mapping[str, Any] | None:
    if not args.mock_response:
        return None
    value = read_json(Path(args.mock_response).expanduser().resolve())
    if not isinstance(value, dict):
        raise ContractError("--mock-response 顶层必须是 JSON object")
    return value


def _process_reflection_reference(
    args: argparse.Namespace,
    reference: str,
    *,
    provider_client: DeepSeekProvider,
    mock_response: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], Path]:
    vault = _vault_path(args.vault)
    return process_reflection_request(
        vault,
        reference,
        provider_client=provider_client,
        provider_name="mock" if mock_response is not None else "deepseek",
        model="fixture" if mock_response is not None else args.model,
        pricing=_pricing_from_args(args, args.model),
        maximum_chars=args.max_source_chars,
        mock_response=mock_response,
    )


def command_reflect(args: argparse.Namespace) -> int:
    mock_response = _reflection_mock_response(args)
    provider_client = _provider(args, max_tokens=3000)
    response, _ = _process_reflection_reference(
        args,
        args.request,
        provider_client=provider_client,
        mock_response=mock_response,
    )
    _json_dump(response)
    return 1 if response["status"] == "error" else 0


def command_self_reflection_worker(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    requests_dir = self_query_root(vault) / "requests"
    paths = sorted(requests_dir.glob("*.json")) if requests_dir.is_dir() else []
    mock_response = _reflection_mock_response(args)
    provider_client = _provider(args, max_tokens=3000)
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "self_reflection_worker_run",
        "requests_seen": len(paths),
        "processed": 0,
        "skipped": 0,
        "ready": 0,
        "insufficient_evidence": 0,
        "errors": 0,
        "invalid": 0,
    }
    for path in paths:
        request_id = path.stem
        try:
            output = reflection_response_path(vault, request_id)
        except ContractError:
            report["invalid"] += 1
            continue
        existed = output.is_file()
        try:
            response, _ = _process_reflection_reference(
                args,
                request_id,
                provider_client=provider_client,
                mock_response=mock_response,
            )
        except ContractError:
            report["invalid"] += 1
            continue
        if existed:
            report["skipped"] += 1
            continue
        report["processed"] += 1
        status = response["status"]
        if status == "ready":
            report["ready"] += 1
        elif status == "insufficient_evidence":
            report["insufficient_evidence"] += 1
        else:
            report["errors"] += 1
    _json_dump(report)
    return 0


def _agent_budget_from_args(args: argparse.Namespace) -> AgentBudget:
    return AgentBudget(
        max_turns=args.max_turns,
        max_tool_calls=args.max_tool_calls,
        max_total_tokens=args.max_total_tokens,
        max_prompt_chars=args.max_prompt_chars,
    ).validate()


def _agent_mock_steps(args: argparse.Namespace) -> list[dict[str, Any]] | None:
    if not args.mock_steps:
        return None
    value = read_json(Path(args.mock_steps).expanduser().resolve())
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ContractError("--mock-steps 必须是 JSON object array")
    return [dict(item) for item in value]


def command_agent_request(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    require_agent_v1_enabled(vault)
    request, path = create_agent_request(
        vault,
        as_of=args.as_of,
        request_id=args.request_id,
    )
    _json_dump({"request": request, "request_path": str(path)})
    return 0


def _process_agent_reference(
    args: argparse.Namespace,
    reference: str,
    *,
    mock_steps: Sequence[Mapping[str, Any]] | None,
) -> tuple[dict[str, Any], Path]:
    vault = _vault_path(args.vault)
    # This is the command boundary for every provider-backed mission.  The
    # gate is re-read for each worker item so disabling prevents subsequent
    # missions from starting; an already-running provider call is not killed.
    require_agent_v1_enabled(vault)
    if mock_steps is None:
        provider_client: Any = _provider(args, max_tokens=2400)
        provider_name = "deepseek-agentic-workflow"
        model = args.model
    else:
        provider_client = MockPlanner(mock_steps)
        provider_name = "mock-agentic-workflow"
        model = "fixture"
    return process_agent_request(
        vault,
        reference,
        provider_client=provider_client,
        provider_name=provider_name,
        model=model,
        pricing=_pricing_from_args(args, args.model),
        budget=_agent_budget_from_args(args),
        maximum_chars=args.max_source_chars,
    )


def command_agent_run(args: argparse.Namespace) -> int:
    response, _ = _process_agent_reference(
        args, args.request, mock_steps=_agent_mock_steps(args)
    )
    _json_dump(response)
    return 0 if response["status"] in {
        "updated",
        "no_change",
        "insufficient_evidence",
    } else 1


def command_agent_worker(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    # Reconciliation can materialize user edits/deletions, so it is behind the
    # same exact gate and occurs only after this fail-closed check.
    require_agent_v1_enabled(vault)
    reconciliation = reconcile_agent_state(vault)
    requests_dir = agent_v1_root(vault) / "requests"
    paths = sorted(requests_dir.glob("*.json")) if requests_dir.is_dir() else []
    mock_steps = _agent_mock_steps(args)
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "remember_agent_worker_run",
        "requests_seen": len(paths),
        "processed": 0,
        "skipped": 0,
        "updated": 0,
        "no_change": 0,
        "insufficient_evidence": 0,
        "budget_exhausted": 0,
        "stale": 0,
        "errors": 0,
        "invalid": 0,
        "reconciliation": reconciliation,
    }
    for path in paths:
        request_id = path.stem
        try:
            output = agent_response_path(vault, request_id)
        except ContractError:
            report["invalid"] += 1
            continue
        if output.is_file():
            report["skipped"] += 1
            continue
        try:
            response, _ = _process_agent_reference(
                args,
                request_id,
                # A new deterministic planner is required per request.  Real
                # workers share no conversation state between missions either.
                mock_steps=mock_steps,
            )
        except ContractError:
            report["invalid"] += 1
            continue
        report["processed"] += 1
        status = response["status"]
        if status in report:
            report[status] += 1
        else:
            report["errors"] += 1
    persist_agent_profile(vault)
    _json_dump(report)
    return 0


def command_agent_profile(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    profile, _ = persist_agent_profile(vault)
    _json_dump(profile)
    return 0


def command_agent_status(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    _json_dump(inspect_agent_v1_gate(vault))
    return 0


def command_agent_enable(args: argparse.Namespace) -> int:
    if args.confirm != AGENT_ENABLE_CONFIRMATION:
        raise ContractError(
            f"--confirm 必须精确为 {AGENT_ENABLE_CONFIRMATION}",
            kind="authorization",
        )
    vault = _vault_path(args.vault)
    _json_dump(enable_agent_v1(vault))
    return 0


def command_agent_disable(args: argparse.Namespace) -> int:
    if args.confirm != AGENT_DISABLE_CONFIRMATION:
        raise ContractError(
            f"--confirm 必须精确为 {AGENT_DISABLE_CONFIRMATION}",
            kind="authorization",
        )
    vault = _vault_path(args.vault)
    _json_dump(disable_agent_v1(vault))
    return 0


def _local_date(value: str | None = None) -> str:
    if value is None:
        return dt.datetime.now().astimezone().date().isoformat()
    try:
        parsed = dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ContractError("--date 必须是有效 YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ContractError("--date 必须是 YYYY-MM-DD")
    return value


def _safe_schedule_status(report: Mapping[str, Any], *, changed: bool | None = None) -> dict[str, Any]:
    state = report.get("state")
    enabled = report.get("enabled")
    reason = report.get("reason")
    if state not in {"enabled", "disabled", "invalid"} or type(enabled) is not bool:
        raise ContractError("schedule 状态合同无效", kind="evidence")
    if state != "invalid" and enabled != (state == "enabled"):
        raise ContractError("schedule 状态合同不一致", kind="evidence")
    if not isinstance(reason, str) or not reason or len(reason) > 80:
        raise ContractError("schedule reason 无效", kind="evidence")
    schedule = report.get("schedule")
    if schedule is not None and not isinstance(schedule, Mapping):
        raise ContractError("schedule 配置合同无效", kind="evidence")
    return {
        "schema_version": "1.0",
        "kind": "memento_cognitive_schedule_status",
        "state": state,
        "enabled": enabled,
        "reason": reason,
        "cadence": None if schedule is None else schedule.get("cadence"),
        "hour": None if schedule is None else schedule.get("hour"),
        "minute": None if schedule is None else schedule.get("minute"),
        "updated_at": None if schedule is None else schedule.get("updated_at"),
        "changed": bool(report.get("changed", False) if changed is None else changed),
    }


def _projection_schedule(vault: Path, local_date: str, now: dt.datetime | None = None) -> dict[str, Any]:
    current = now or dt.datetime.now().astimezone()
    if current.tzinfo is None or current.utcoffset() is None:
        raise ContractError("本地时区不可用", kind="runtime")
    report = inspect_agent_schedule(vault)
    state = report.get("state")
    if state == "invalid":
        raise ContractError("Agent schedule 无效", kind="evidence")
    if state not in {"enabled", "disabled"} or type(report.get("enabled")) is not bool:
        raise ContractError("Agent schedule 状态合同无效", kind="evidence")
    slot = current.replace(hour=21, minute=0, second=0, microsecond=0)
    if current >= slot:
        slot += dt.timedelta(days=1)
    last_run_status = "not_started"
    day_status = inspect_cognitive_day_status(vault, local_date)
    if (
        day_status is not None
        and day_status.get("stage") == "finished"
        and day_status.get("status")
        in {
            "committed",
            "committed_with_warnings",
            "no_change",
            "no_candidate",
            "no_records",
            "no_receipts",
            "stale",
            "error",
            "budget_exhausted",
        }
    ):
        last_run_status = day_status["status"]
    return {
        "enabled": bool(report["enabled"]),
        "hour": 21,
        "minute": 0,
        "next_due_at": slot.isoformat(timespec="seconds"),
        "last_run_status": last_run_status,
    }


def _cognitive_usage_auditor(args: argparse.Namespace) -> Any:
    def audit(
        vault: Path,
        *,
        model: str,
        provider: str,
        usage: Mapping[str, Any],
        request_id: str | None,
    ) -> Mapping[str, Any]:
        return append_usage_log(
            vault,
            model=model,
            provider=provider,
            usage=usage,
            pricing=_pricing_from_args(args, args.model),
            request_id=request_id,
        )

    return audit


class _CognitiveProviderRouter:
    """Keep the two frozen token policies while reusing the existing client."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.record = _provider(args, max_tokens=2400)
        self.daily = _provider(args, max_tokens=3600)

    def complete(self, messages: Sequence[Mapping[str, str]]) -> Any:
        if not messages or not isinstance(messages[0], Mapping):
            raise ContractError("cognitive provider prompt 缺少 system 消息", kind="runtime")
        system = messages[0].get("content")
        if not isinstance(system, str):
            raise ContractError("cognitive provider system 消息无效", kind="runtime")
        if system.startswith("你是 Memento Record Interpreter"):
            return self.record.complete(messages)
        if system.startswith("你是 Memento Daily Integrator"):
            return self.daily.complete(messages)
        raise ContractError("cognitive provider prompt 类型未授权", kind="runtime")


def _cognitive_runtime(args: argparse.Namespace, vault: Path) -> CognitiveRuntime:
    provider = _CognitiveProviderRouter(args)
    return CognitiveRuntime(
        vault,
        provider,
        provider_name="deepseek",
        model=args.model,
        thinking=args.thinking,
        reasoning_effort=args.reasoning_effort,
        record_max_tokens=2400,
        daily_max_tokens=3600,
        usage_auditor=_cognitive_usage_auditor(args),
    )


class _CognitiveInspectionProvider:
    """Fail-closed placeholder for policy-exact, model-free state reads."""

    def complete(self, messages: Any) -> Any:
        del messages
        raise ContractError("inspection runtime 不得调用 Provider", kind="runtime")


def _cognitive_inspection_runtime(
    args: argparse.Namespace,
    vault: Path,
    *,
    state_root: Path | None = None,
) -> CognitiveRuntime:
    """Rebuild the production record policy without constructing a client."""

    return CognitiveRuntime(
        vault,
        _CognitiveInspectionProvider(),
        state_root=state_root,
        provider_name="deepseek",
        model=args.model,
        thinking=args.thinking,
        reasoning_effort=args.reasoning_effort,
        record_max_tokens=2400,
        daily_max_tokens=3600,
    )


def _current_no_candidate_statuses(
    args: argparse.Namespace,
    vault: Path,
    *,
    local_date: str,
    records: RecordStore,
    actions: CognitiveActionStore,
) -> dict[str, dict[str, str | None]]:
    """Restore durable no-receipt terminals for a deterministic reprojection."""

    runtime = _cognitive_inspection_runtime(
        args,
        vault,
        state_root=records.root,
    )
    _, action_watermark = actions.action_watermark()
    statuses: dict[str, dict[str, str | None]] = {}
    for head in records.list_heads(local_date=local_date):
        if head["status"] != "active":
            continue
        source_ref = ObjectRef.from_dict(
            records.load_head_ref(head["record_id"])
        )
        try:
            receipt = actions.load_receipt_head(
                make_receipt_id(head["record_id"])
            )
        except ContractError as exc:
            if exc.kind != "not_found":
                raise
            receipt = None
        if receipt is not None and receipt.record_ref == source_ref:
            continue
        terminal = runtime.get_current_interpretation_terminal(
            head["record_id"],
            feedback_watermark_sha256=action_watermark,
        )
        if terminal is None:
            continue
        if terminal.get("status") != "no_candidate":
            raise ContractError("current interpretation terminal 无效", kind="evidence")
        statuses[head["record_id"]] = {
            "status": "no_candidate",
            "error_kind": None,
        }
    return statuses


def _cognitive_components(
    args: argparse.Namespace, vault: Path
) -> tuple[CognitiveRuntime, CognitiveActionStore, CognitiveBundleStore]:
    runtime = _cognitive_runtime(args, vault)
    root = runtime.files.root
    return (
        runtime,
        CognitiveActionStore(vault, state_root=root),
        CognitiveBundleStore(vault, state_root=root),
    )


def _safe_record_worker_result(result: RecordWorkerResult) -> dict[str, Any]:
    outcomes = Counter(item.outcome for item in result.items)
    errors = Counter(item.error_kind for item in result.items if item.error_kind is not None)
    return {
        "schema_version": "1.0",
        "kind": "memento_cognitive_record_worker_result",
        "status": result.status,
        "local_date": result.local_date,
        "selected_count": result.selected_count,
        "deferred_count": result.deferred_count,
        "actions": result.actions.to_dict(),
        "reconcile": result.reconcile.to_dict(),
        "outcomes": dict(sorted(outcomes.items())),
        "error_kinds": dict(sorted(errors.items())),
    }


def _safe_day_result(result: CognitiveDayResult) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": "memento_cognitive_day_result",
        "status": result.status,
        "local_date": result.local_date,
        "trigger": result.trigger,
        "stage": result.stage,
        "pipeline_status": result.pipeline_status,
        "record_count": result.record_count,
        "receipt_count": result.receipt_count,
        "review_status": result.review_status,
        "long_term_required": result.long_term_required,
        "long_term_status": result.long_term_status,
        "projection_status": result.projection_status,
        "warnings": list(result.warnings),
        "error_kind": result.error_kind,
        "cached": result.cached,
    }


def command_record_ingest(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    result = RecordStore(vault).reconcile_day(
        args.source,
        preallocated_record_id=args.record_id,
    )
    status = "needs_review" if result.needs_review else ("changed" if result.changed else "no_change")
    _json_dump(
        {
            "schema_version": "1.0",
            "kind": "memento_cognitive_record_ingest_result",
            "status": status,
            "local_date": args.source[:-3],
            "parsed_count": result.parsed_count,
            "created_count": len(result.created_record_ids),
            "revised_count": len(result.revised_record_ids),
            "tombstoned_count": len(result.tombstoned_record_ids),
            "unchanged_count": len(result.unchanged_record_ids),
            "refreshed_locator_count": len(result.refreshed_locator_record_ids),
            "needs_review_count": len(result.needs_review),
            "index_revision": result.index_revision,
        }
    )
    return 0


def command_record_worker(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    require_agent_v1_enabled(vault)
    local_date = _local_date(args.date)
    runtime, actions, bundles = _cognitive_components(args, vault)
    publisher = CognitiveProjectionPublisher(
        vault,
        record_store=runtime.store,
        action_store=actions,
        bundle_store=bundles,
        state_root=runtime.files.root,
    )

    def publish(date: str, statuses: Mapping[str, Mapping[str, Any]]) -> Any:
        return publisher.publish(
            local_date=date,
            schedule=_projection_schedule(vault, date),
            record_runtime_statuses=statuses,
        )

    worker = CognitiveRecordWorker(
        vault,
        runtime=runtime,
        record_store=runtime.store,
        action_store=actions,
        formal_store=bundles,
        home_projection_hook=publish,
    )
    result = worker.run(
        local_date=local_date,
        source_file=f"{local_date}.md",
        limit=args.limit,
    )
    _json_dump(_safe_record_worker_result(result))
    return 0


def _cognitive_day_runner(args: argparse.Namespace, vault: Path) -> Any:
    def run(local_date: str, trigger: str) -> CognitiveDayResult:
        # The provider is intentionally constructed only after the schedule
        # core has re-read the master gate and schedule under its day lock.
        runtime, actions, bundles = _cognitive_components(args, vault)
        pipeline = CognitivePipeline(
            vault,
            runtime=runtime,
            record_store=runtime.store,
            action_store=actions,
            bundle_store=bundles,
        )

        def run_agent(selected_vault: Path, request_id: str) -> None:
            if selected_vault.resolve() != vault.resolve():
                raise ContractError("Agent runner Vault 不一致", kind="evidence")
            _process_agent_reference(
                args,
                request_id,
                mock_steps=_agent_mock_steps(args),
            )

        orchestrator = CognitiveDayOrchestrator(
            vault,
            pipeline=pipeline,
            bundle_store=bundles,
            agent_runner=run_agent,
            schedule_loader=lambda date, now: _projection_schedule(vault, date, now),
        )
        return orchestrator.run_day(local_date, trigger=trigger)

    return run


def command_daily_run(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    require_agent_v1_enabled(vault)
    if args.trigger != "manual":
        schedule = inspect_agent_schedule(vault)
        if schedule.get("state") == "invalid":
            raise ContractError("Agent schedule 无效", kind="evidence")
        if schedule.get("state") != "enabled" or schedule.get("enabled") is not True:
            raise ContractError(
                "scheduled/recovery 日任务需要已开启的 schedule",
                kind="authorization",
            )
    result = _cognitive_day_runner(args, vault)(_local_date(args.date), args.trigger)
    _json_dump(_safe_day_result(result))
    return 0


def command_daily_schedule_status(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    _json_dump(_safe_schedule_status(inspect_agent_schedule(vault)))
    return 0


def command_daily_schedule_enable(args: argparse.Namespace) -> int:
    if args.confirm != AGENT_SCHEDULE_ENABLE_CONFIRMATION:
        raise ContractError(
            f"--confirm 必须精确为 {AGENT_SCHEDULE_ENABLE_CONFIRMATION}",
            kind="authorization",
        )
    vault = _vault_path(args.vault)
    _json_dump(_safe_schedule_status(enable_agent_schedule(vault)))
    return 0


def command_daily_schedule_disable(args: argparse.Namespace) -> int:
    if args.confirm != AGENT_SCHEDULE_DISABLE_CONFIRMATION:
        raise ContractError(
            f"--confirm 必须精确为 {AGENT_SCHEDULE_DISABLE_CONFIRMATION}",
            kind="authorization",
        )
    vault = _vault_path(args.vault)
    _json_dump(_safe_schedule_status(disable_agent_schedule(vault)))
    return 0


def command_daily_schedule_tick(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    inspection_runtime = _cognitive_inspection_runtime(args, vault)
    report = CognitiveScheduleCore(
        vault,
        day_runner=_cognitive_day_runner(args, vault),
        completion_reader=lambda local_date: inspect_day_completion(
            vault,
            local_date,
            runtime=inspection_runtime,
        ),
    ).tick()
    _json_dump(report)
    return 0


def command_cognitive_action_worker(args: argparse.Namespace) -> int:
    """Materialize browser actions and publish their visible consequences."""

    vault = _vault_path(args.vault)
    require_agent_v1_enabled(vault)
    local_date = _local_date()
    records = RecordStore(vault)
    actions = CognitiveActionStore(vault, state_root=records.root)
    bundles = CognitiveBundleStore(vault, state_root=records.root)
    reconciliation = actions.reconcile(
        receipt_store=actions,
        formal_store=bundles,
    )
    terminal = tuple(
        receipt
        for receipt, _ in actions.list_receipt_heads(
            statuses=("original_only", "tombstone")
        )
    )
    retraction = bundles.retract_terminal_receipt_derivatives(terminal)
    runtime_statuses = _current_no_candidate_statuses(
        args,
        vault,
        local_date=local_date,
        records=records,
        actions=actions,
    )
    publication = CognitiveProjectionPublisher(
        vault,
        record_store=records,
        action_store=actions,
        bundle_store=bundles,
        state_root=records.root,
    ).publish(
        local_date=local_date,
        schedule=_projection_schedule(vault, local_date),
        record_runtime_statuses=runtime_statuses,
    )
    _json_dump(
        {
            "schema_version": "1.0",
            "kind": "memento_cognitive_action_worker_result",
            "seen": reconciliation.seen,
            "already_resolved": reconciliation.already_resolved,
            "applied": reconciliation.applied,
            "rejected": reconciliation.rejected,
            "conflict": reconciliation.conflict,
            "retracted_memories": len(retraction.memory_refs),
            "retracted_relations": len(retraction.relation_refs),
            "projected_records": len(publication.home.records),
        }
    )
    return 0


def command_daily_manual_worker(args: argparse.Namespace) -> int:
    """Consume immutable browser requests through the unified day runner."""

    vault = _vault_path(args.vault)
    report = ManualDayRequestStore(vault).consume(
        day_runner=_cognitive_day_runner(args, vault),
    )
    _json_dump(report.to_dict())
    return 0


def command_projection_rebuild(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    local_date = _local_date(args.date)
    records = RecordStore(vault)
    actions = CognitiveActionStore(vault, state_root=records.root)
    bundles = CognitiveBundleStore(vault, state_root=records.root)
    runtime_statuses = _current_no_candidate_statuses(
        args,
        vault,
        local_date=local_date,
        records=records,
        actions=actions,
    )
    publication = CognitiveProjectionPublisher(
        vault,
        record_store=records,
        action_store=actions,
        bundle_store=bundles,
        state_root=records.root,
    ).publish(
        local_date=local_date,
        schedule=_projection_schedule(vault, local_date),
        record_runtime_statuses=runtime_statuses,
    )
    _json_dump(
        {
            "schema_version": "1.0",
            "kind": "memento_cognitive_projection_rebuild_result",
            "status": "completed",
            "local_date": local_date,
            "landscape_summary": dict(publication.landscape.summary),
            "today_status": dict(publication.home.today_status),
            "warning_count": len(publication.home.warnings),
        }
    )
    return 0


def command_cognitive_migration_status(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    _json_dump(
        {
            "schema_version": "1.0",
            "kind": "memento_cognitive_migration_inventory",
            **CognitiveMigration(vault).inventory().public_summary(),
        }
    )
    return 0


def command_cognitive_migration_backfill(args: argparse.Namespace) -> int:
    vault = _vault_path(args.vault)
    result = CognitiveMigration(vault).backfill_record_index(
        source_files=args.source,
    )
    _json_dump(result.to_dict())
    return 0


# Frozen command names remain aliases only.  They can no longer create a
# legacy long-term Agent request directly or establish a second daily path.
command_agent_schedule_status = command_daily_schedule_status
command_agent_schedule_enable = command_daily_schedule_enable
command_agent_schedule_disable = command_daily_schedule_disable
command_agent_schedule_tick = command_daily_schedule_tick


def _write_case_sources(vault: Path, sources: Mapping[str, Any]) -> None:
    resolved_vault = vault.resolve()
    if not resolved_vault.is_dir():
        raise ContractError("eval case vault 不存在", kind="not_found")
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        directory_fd = os.open(resolved_vault, flags)
    except OSError as exc:
        raise ContractError("eval case vault 无法安全打开", kind="evidence") from exc
    try:
        for name, content in sources.items():
            if (
                not isinstance(name, str)
                or not DAILY_NAME_RE.fullmatch(name)
                or not isinstance(content, str)
            ):
                raise ContractError(
                    "eval case 的 sources 必须是 YYYY-MM-DD.md -> text",
                    kind="evidence",
                )
            path = resolved_vault / name
            if path.is_symlink() or path.exists():
                raise ContractError(
                    f"eval case source 已存在或为符号链接：{name}",
                    kind="conflict",
                )
            if path.resolve(strict=False).parent != resolved_vault:
                raise ContractError("eval case source 越过临时 vault 边界", kind="evidence")
            file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            file_flags |= getattr(os, "O_NOFOLLOW", 0)
            file_flags |= getattr(os, "O_CLOEXEC", 0)
            try:
                descriptor = os.open(
                    name, file_flags, 0o600, dir_fd=directory_fd
                )
            except OSError as exc:
                raise ContractError(
                    f"eval case source 无法安全创建：{name}",
                    kind="evidence",
                ) from exc
            try:
                payload = content.encode("utf-8")
                offset = 0
                while offset < len(payload):
                    written = os.write(descriptor, payload[offset:])
                    if written <= 0:
                        raise ContractError("eval case source 写入失败", kind="runtime")
                    offset += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        os.close(directory_fd)


def _evaluate_response(
    response: Mapping[str, Any],
    case_vault: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    contract_valid = True
    evidence_valid = True
    contract_error_kind: str | None = None
    try:
        validate_model_response(response, case_vault, verify_evidence=False)
    except ContractError as exc:
        contract_valid = False
        evidence_valid = False
        contract_error_kind = exc.kind
    if contract_valid:
        try:
            validate_model_response(response, case_vault, verify_evidence=True)
        except ContractError as exc:
            evidence_valid = False
            contract_error_kind = exc.kind

    actual_status = response.get("status") if isinstance(response, dict) else None
    actual_category = None
    candidate = response.get("candidate") if isinstance(response, dict) else None
    if isinstance(candidate, dict):
        actual_category = candidate.get("category")

    checks = {
        "contract_valid": contract_valid == expected.get("contract_valid", True),
        "evidence_valid": evidence_valid == expected.get("evidence_valid", True),
        "status": actual_status == expected.get("status", actual_status),
    }
    if "category" in expected:
        checks["category"] = actual_category == expected["category"]
    if "error_kind" in expected:
        checks["error_kind"] = contract_error_kind == expected["error_kind"]
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "contract_valid": contract_valid,
        "evidence_valid": evidence_valid,
        "status": actual_status,
        "category": actual_category,
        "error_kind": contract_error_kind,
    }


def _load_eval_cases(cases_dir: Path) -> list[dict[str, Any]]:
    paths = sorted(cases_dir.glob("*.json"))
    if not paths:
        raise ContractError(f"没有找到 eval cases：{cases_dir}", kind="not_found")
    cases = []
    for path in paths:
        value = read_json(path)
        if not isinstance(value, dict):
            raise ContractError(f"eval case 顶层必须是 object：{path.name}")
        required = {"name", "sources", "mock_response", "expected"}
        if not required.issubset(value):
            raise ContractError(f"eval case 缺少字段：{path.name}")
        value["_path"] = str(path)
        cases.append(value)
    return cases


def _sum_usage(total: dict[str, int], usage: Mapping[str, Any]) -> None:
    normalized = normalize_usage(usage)
    for field in total:
        total[field] += normalized[field]


def _failed_eval_result(name: str, *, error_kind: str, error: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": False,
        "checks": {
            "contract_valid": False,
            "evidence_valid": False,
            "status": False,
        },
        "contract_valid": False,
        "evidence_valid": False,
        "status": None,
        "category": None,
        "error_kind": error_kind,
        "error": error,
    }


def _model_eval_report(
    *,
    label: str,
    cases: Sequence[dict[str, Any]],
    live: bool,
    args: argparse.Namespace,
    usage_vault: Path | None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    calls_attempted = 0
    calls_completed = 0
    errors_total = 0
    provider_errors = 0
    invalid_json_errors = 0
    contract_errors = 0
    usage_missing = 0
    totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
        "reasoning_tokens": 0,
    }
    pricing = _pricing_from_args(args, label if live else DEFAULT_MODEL)
    with tempfile.TemporaryDirectory(prefix="memento-context-eval-") as temporary:
        base = Path(temporary)
        for index, case in enumerate(cases):
            if live and not case.get("live_eval", False):
                continue
            case_vault = base / f"case-{index}"
            case_vault.mkdir()
            try:
                _write_case_sources(case_vault, case["sources"])
            except ContractError as exc:
                errors_total += 1
                contract_errors += 1
                results.append(
                    _failed_eval_result(
                        case["name"], error_kind=exc.kind, error=str(exc)
                    )
                )
                continue
            if live:
                try:
                    paths = collect_sources(case_vault, sorted(case["sources"]))
                except ContractError as exc:
                    errors_total += 1
                    contract_errors += 1
                    results.append(
                        _failed_eval_result(
                            case["name"], error_kind=exc.kind, error=str(exc)
                        )
                    )
                    continue
                if usage_vault is None:
                    raise ContractError("live eval 必须提供 --vault 记录 token usage")
                calls_attempted += 1
                try:
                    completion = _provider(args, label).complete(
                        build_generation_messages(paths)
                    )
                except ProviderError as exc:
                    errors_total += 1
                    provider_errors += 1
                    if exc.usage is None:
                        usage_missing += 1
                    else:
                        if usage_is_missing(exc.usage):
                            usage_missing += 1
                        _sum_usage(totals, exc.usage)
                        _log_provider_error_usage(
                            usage_vault,
                            args,
                            exc,
                            requested_model=label,
                        )
                    results.append(
                        _failed_eval_result(
                            case["name"],
                            error_kind="provider_error",
                            error=str(exc),
                        )
                    )
                    continue
                calls_completed += 1
                usage = completion.usage
                if usage_is_missing(usage):
                    usage_missing += 1
                _sum_usage(totals, usage)
                append_usage_log(
                    usage_vault,
                    model=completion.model,
                    provider="deepseek",
                    usage=completion.usage,
                    pricing=pricing,
                    request_id=completion.request_id,
                )
                try:
                    response = _parse_json_text(
                        completion.content, f"{case['name']} 模型输出"
                    )
                except ContractError as exc:
                    errors_total += 1
                    invalid_json_errors += 1
                    results.append(
                        _failed_eval_result(
                            case["name"],
                            error_kind="invalid_json",
                            error=str(exc),
                        )
                    )
                    continue
            else:
                usage = case.get("usage") or {}
                response = case["mock_response"]
                _sum_usage(totals, usage)
            result = _evaluate_response(response, case_vault, case["expected"])
            result["name"] = case["name"]
            if live and result.get("error_kind") is not None:
                errors_total += 1
                contract_errors += 1
            results.append(result)

    passed = sum(1 for item in results if item["passed"])
    return {
        "model": label,
        "mode": "live" if live else "offline",
        "cases_total": len(results),
        "cases_passed": passed,
        "cases_failed": len(results) - passed,
        "contract_valid": sum(1 for item in results if item["contract_valid"]),
        "evidence_valid": sum(1 for item in results if item["evidence_valid"]),
        "expected_status_passed": sum(
            1 for item in results if item["checks"].get("status", False)
        ),
        "calls_attempted": calls_attempted,
        "calls_completed": calls_completed,
        "errors_total": errors_total,
        "provider_errors": provider_errors,
        "invalid_json_errors": invalid_json_errors,
        "contract_errors": contract_errors,
        "usage_missing": usage_missing,
        "cost_complete": usage_missing == 0,
        "usage": totals,
        "cost_usd": calculate_cost(totals, pricing),
        "pricing": {
            "effective_date": pricing.effective_date,
            "cache_hit_input_usd_per_million": pricing.cache_hit_input_usd_per_million,
            "cache_miss_input_usd_per_million": pricing.cache_miss_input_usd_per_million,
            "output_usd_per_million": pricing.output_usd_per_million,
        },
        "results": results,
    }


def command_eval(args: argparse.Namespace) -> int:
    cases = _load_eval_cases(Path(args.cases_dir).expanduser().resolve())
    if args.live:
        usage_vault = _vault_path(args.vault)
        models = args.model or [DEFAULT_MODEL]
        reports = [
            _model_eval_report(
                label=model,
                cases=cases,
                live=True,
                args=args,
                usage_vault=usage_vault,
            )
            for model in models
        ]
    else:
        reports = [
            _model_eval_report(
                label="mock",
                cases=cases,
                live=False,
                args=args,
                usage_vault=None,
            )
        ]
    result = {
        "schema_version": "1.0",
        "mode": "live" if args.live else "offline",
        "all_passed": all(report["cases_failed"] == 0 for report in reports),
        "reports": reports,
    }
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        result["output"] = str(output)
    _json_dump(result)
    return 0 if result["all_passed"] else 1


def _add_vault_argument(parser: argparse.ArgumentParser, *, required: bool = False) -> None:
    parser.add_argument(
        "--vault",
        required=required,
        help="Memento vault；也可设置 MEMENTO_VAULT",
    )


def _add_pricing_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cache-hit-rate",
        type=float,
        help="覆盖所选模型的缓存命中输入价（USD / 1M tokens）",
    )
    parser.add_argument(
        "--cache-miss-rate",
        type=float,
        help="覆盖所选模型的缓存未命中输入价（USD / 1M tokens）",
    )
    parser.add_argument(
        "--output-rate",
        type=float,
        help="覆盖所选模型的输出价（USD / 1M tokens）",
    )
    parser.add_argument("--pricing-date")


def _add_provider_arguments(
    parser: argparse.ArgumentParser, *, repeat_model: bool = False
) -> None:
    parser.add_argument(
        "--model",
        choices=SUPPORTED_MODELS,
        action="append" if repeat_model else "store",
        default=None if repeat_model else DEFAULT_MODEL,
        help="DeepSeek 模型；generate 默认 deepseek-v4-pro",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--thinking", choices=("disabled", "enabled"), default="disabled")
    parser.add_argument("--reasoning-effort", choices=("high", "max"))


def _add_agent_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-source-chars", type=int, default=120_000)
    # Production Agentic Workflow normally uses two model turns (investigate,
    # then finalize/finish) and permits one bounded patch repair plus one
    # contract-correction turn.  Frozen legacy evaluators construct their own
    # AgentBudget and retain the old limits.
    parser.add_argument("--max-turns", type=int, default=5)
    parser.add_argument("--max-tool-calls", type=int, default=5)
    # This is a post-call execution stop threshold, not a strict provider-cost
    # ceiling: an already-paid completion may cross it but will not execute.
    parser.add_argument("--max-total-tokens", type=int, default=40_000)
    parser.add_argument("--max-prompt-chars", type=int, default=180_000)
    parser.add_argument(
        "--mock-steps",
        help="离线严格 JSON action array；不会调用或记录模型",
    )
    _add_provider_arguments(parser)
    _add_pricing_arguments(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="context_agent.py",
        description="Memento evidence-first Context Agent",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="生成并校验一条 Context 候选")
    _add_vault_argument(generate)
    generate.add_argument(
        "--source", action="append", help="vault 根目录中的 YYYY-MM-DD.md；可重复"
    )
    generate.add_argument("--latest", type=int, default=7)
    generate.add_argument("--max-source-chars", type=int, default=80_000)
    generate.add_argument("--mock-response", help="离线测试 JSON；不会调用或记录模型")
    _add_provider_arguments(generate)
    _add_pricing_arguments(generate)
    generate.set_defaults(handler=command_generate)

    validate = subparsers.add_parser("validate", help="严格校验候选或已确认 Context")
    _add_vault_argument(validate)
    validate.add_argument("--input", required=True)
    validate.set_defaults(handler=command_validate)

    decide = subparsers.add_parser("decide", help="确认、编辑、限域、单次使用或拒绝候选")
    _add_vault_argument(decide)
    decide.add_argument("--candidate", required=True, help="candidate id 或候选文件路径")
    decide.add_argument(
        "--action",
        required=True,
        choices=("confirm", "edit", "scope", "just_once", "reject"),
    )
    decide.add_argument("--statement")
    decide.add_argument("--scope")
    decide.set_defaults(handler=command_decide)

    pack = subparsers.add_parser("pack", help="从已确认 Context 生成任务 Context Pack")
    _add_vault_argument(pack)
    pack.add_argument("--scope")
    pack.add_argument("--output")
    pack.set_defaults(handler=command_pack)

    profile = subparsers.add_parser(
        "profile",
        help="重建可追溯标签画像；只合并精确同文，不做同义改写去重",
    )
    _add_vault_argument(profile)
    profile.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="json 投影，或只含单行严格 JSON data block 的 Markdown",
    )
    profile.set_defaults(handler=command_profile)

    reflect = subparsers.add_parser(
        "reflect", help="处理一条本地 Self Reflection 请求"
    )
    _add_vault_argument(reflect)
    reflect.add_argument(
        "--request", required=True, help="srq_<24 hex> 或 requests 目录中的请求文件"
    )
    reflect.add_argument(
        "--max-source-chars", type=int, default=DEFAULT_REFLECTION_MAX_SOURCE_CHARS
    )
    reflect.add_argument("--mock-response", help="离线测试 JSON；不会调用或记录模型")
    _add_provider_arguments(reflect)
    _add_pricing_arguments(reflect)
    reflect.set_defaults(handler=command_reflect)

    worker = subparsers.add_parser(
        "self-reflection-worker",
        help="一次处理所有尚无 response 的 Self Reflection 请求",
    )
    _add_vault_argument(worker)
    worker.add_argument(
        "--once", action="store_true", required=True, help="处理当前 pending 请求后退出"
    )
    worker.add_argument(
        "--max-source-chars", type=int, default=DEFAULT_REFLECTION_MAX_SOURCE_CHARS
    )
    worker.add_argument("--mock-response", help="离线测试 JSON；不会调用或记录模型")
    _add_provider_arguments(worker)
    _add_pricing_arguments(worker)
    worker.set_defaults(handler=command_self_reflection_worker)

    agent_status = subparsers.add_parser(
        "agent-status", help="检查 Agent V1 手动启用文件（不修改 Vault）"
    )
    _add_vault_argument(agent_status)
    agent_status.set_defaults(handler=command_agent_status)

    agent_enable = subparsers.add_parser(
        "agent-enable", help="原子创建 Agent V1 手动启用文件"
    )
    _add_vault_argument(agent_enable)
    agent_enable.add_argument(
        "--confirm",
        required=True,
        help=f"必须精确输入 {AGENT_ENABLE_CONFIRMATION}",
    )
    agent_enable.set_defaults(handler=command_agent_enable)

    agent_disable = subparsers.add_parser(
        "agent-disable", help="删除合法的 Agent V1 手动启用文件"
    )
    _add_vault_argument(agent_disable)
    agent_disable.add_argument(
        "--confirm",
        required=True,
        help=f"必须精确输入 {AGENT_DISABLE_CONFIRMATION}",
    )
    agent_disable.set_defaults(handler=command_agent_disable)

    record_ingest = subparsers.add_parser(
        "record-ingest",
        help="对一个根目录日记建立或修订本地 record sidecar",
    )
    _add_vault_argument(record_ingest)
    record_ingest.add_argument(
        "--source",
        required=True,
        help="Vault 根目录中的 YYYY-MM-DD.md",
    )
    record_ingest.add_argument(
        "--record-id",
        help="采集端预分配的 rec_<24 hex>；仅单条新记录时可用",
    )
    record_ingest.set_defaults(handler=command_record_ingest)

    record_worker = subparsers.add_parser(
        "record-worker",
        help="有界处理一个本地日期的逐条整理",
    )
    _add_vault_argument(record_worker)
    record_worker.add_argument(
        "--once", action="store_true", required=True, help="处理当前有界批次后退出"
    )
    record_worker.add_argument("--date", help="YYYY-MM-DD；默认今天")
    record_worker.add_argument("--limit", type=int, default=8)
    _add_provider_arguments(record_worker)
    _add_pricing_arguments(record_worker)
    record_worker.set_defaults(handler=command_record_worker)

    daily_run = subparsers.add_parser(
        "daily-run",
        help="统一执行逐条整理、日归并、长期门和投影",
    )
    _add_vault_argument(daily_run)
    daily_run.add_argument(
        "--once", action="store_true", required=True, help="执行一个本地日任务后退出"
    )
    daily_run.add_argument("--date", help="YYYY-MM-DD；默认今天")
    daily_run.add_argument(
        "--trigger",
        choices=("manual", "scheduled", "recovery"),
        default="manual",
    )
    _add_agent_runtime_arguments(daily_run)
    daily_run.set_defaults(handler=command_daily_run)

    daily_schedule_status = subparsers.add_parser(
        "daily-schedule-status",
        help="检查统一日归并的 21:00 开关",
    )
    _add_vault_argument(daily_schedule_status)
    daily_schedule_status.set_defaults(handler=command_daily_schedule_status)

    daily_schedule_enable = subparsers.add_parser(
        "daily-schedule-enable",
        help="开启统一日归并的每日 21:00 触发",
    )
    _add_vault_argument(daily_schedule_enable)
    daily_schedule_enable.add_argument(
        "--confirm",
        required=True,
        help=f"必须精确输入 {AGENT_SCHEDULE_ENABLE_CONFIRMATION}",
    )
    daily_schedule_enable.set_defaults(handler=command_daily_schedule_enable)

    daily_schedule_disable = subparsers.add_parser(
        "daily-schedule-disable",
        help="关闭统一日归并的每日 21:00 触发",
    )
    _add_vault_argument(daily_schedule_disable)
    daily_schedule_disable.add_argument(
        "--confirm",
        required=True,
        help=f"必须精确输入 {AGENT_SCHEDULE_DISABLE_CONFIRMATION}",
    )
    daily_schedule_disable.set_defaults(handler=command_daily_schedule_disable)

    daily_schedule_tick = subparsers.add_parser(
        "daily-schedule-tick",
        help="检查到期或恢复条件并进入唯一日归并路径",
    )
    _add_vault_argument(daily_schedule_tick)
    daily_schedule_tick.add_argument(
        "--once", action="store_true", required=True, help="检查一次后退出"
    )
    _add_agent_runtime_arguments(daily_schedule_tick)
    daily_schedule_tick.set_defaults(handler=command_daily_schedule_tick)

    cognitive_action_worker = subparsers.add_parser(
        "cognitive-action-worker",
        help="物化认知用户动作并立即重投影",
    )
    _add_vault_argument(cognitive_action_worker)
    cognitive_action_worker.add_argument(
        "--once", action="store_true", required=True, help="处理当前有界批次后退出"
    )
    _add_provider_arguments(cognitive_action_worker)
    cognitive_action_worker.set_defaults(handler=command_cognitive_action_worker)

    daily_manual_worker = subparsers.add_parser(
        "daily-manual-worker",
        help="消费浏览器写入的手动日归并请求",
    )
    _add_vault_argument(daily_manual_worker)
    daily_manual_worker.add_argument(
        "--once", action="store_true", required=True, help="处理当前有界批次后退出"
    )
    _add_agent_runtime_arguments(daily_manual_worker)
    daily_manual_worker.set_defaults(handler=command_daily_manual_worker)

    projection_rebuild = subparsers.add_parser(
        "projection-rebuild",
        help="仅从已验证本地状态重建地景与主页投影",
    )
    _add_vault_argument(projection_rebuild)
    projection_rebuild.add_argument("--date", help="YYYY-MM-DD；默认今天")
    _add_provider_arguments(projection_rebuild)
    projection_rebuild.set_defaults(handler=command_projection_rebuild)

    migration_status = subparsers.add_parser(
        "cognitive-migration-status",
        help="只读盘点可迁移状态",
    )
    _add_vault_argument(migration_status)
    migration_status.set_defaults(handler=command_cognitive_migration_status)

    migration_backfill = subparsers.add_parser(
        "cognitive-migration-backfill",
        help="仅回填 record index，不调用模型",
    )
    _add_vault_argument(migration_backfill)
    migration_backfill.add_argument(
        "--source", action="append", help="可重复的 YYYY-MM-DD.md；默认全部"
    )
    migration_backfill.set_defaults(handler=command_cognitive_migration_backfill)

    agent_schedule_status = subparsers.add_parser(
        "agent-schedule-status",
        help="兼容别名：检查统一日归并配置",
    )
    _add_vault_argument(agent_schedule_status)
    agent_schedule_status.set_defaults(handler=command_agent_schedule_status)

    agent_schedule_enable = subparsers.add_parser(
        "agent-schedule-enable",
        help="兼容别名：开启统一日归并",
    )
    _add_vault_argument(agent_schedule_enable)
    agent_schedule_enable.add_argument(
        "--confirm",
        required=True,
        help=f"必须精确输入 {AGENT_SCHEDULE_ENABLE_CONFIRMATION}",
    )
    agent_schedule_enable.set_defaults(handler=command_agent_schedule_enable)

    agent_schedule_disable = subparsers.add_parser(
        "agent-schedule-disable",
        help="兼容别名：关闭统一日归并",
    )
    _add_vault_argument(agent_schedule_disable)
    agent_schedule_disable.add_argument(
        "--confirm",
        required=True,
        help=f"必须精确输入 {AGENT_SCHEDULE_DISABLE_CONFIRMATION}",
    )
    agent_schedule_disable.set_defaults(handler=command_agent_schedule_disable)

    agent_schedule_tick = subparsers.add_parser(
        "agent-schedule-tick",
        help="兼容别名：进入统一日归并路径",
    )
    _add_vault_argument(agent_schedule_tick)
    agent_schedule_tick.add_argument(
        "--once",
        action="store_true",
        help="执行一次检查后退出（与默认行为相同）",
    )
    _add_agent_runtime_arguments(agent_schedule_tick)
    agent_schedule_tick.set_defaults(handler=command_agent_schedule_tick)

    agent_request = subparsers.add_parser(
        "agent-request", help="创建一条固定 14 日的手动 Agent V1 请求"
    )
    _add_vault_argument(agent_request)
    agent_request.add_argument("--as-of", required=True, help="YYYY-MM-DD")
    agent_request.add_argument("--request-id", help="可选 arq_<24 hex>")
    agent_request.set_defaults(handler=command_agent_request)

    agent_run = subparsers.add_parser(
        "agent-run", help="处理一条受约束的 Agent V1 mission"
    )
    _add_vault_argument(agent_run)
    agent_run.add_argument(
        "--request", required=True, help="arq_<24 hex> 或 requests 目录中的请求文件"
    )
    _add_agent_runtime_arguments(agent_run)
    agent_run.set_defaults(handler=command_agent_run)

    agent_worker = subparsers.add_parser(
        "agent-worker", help="一次处理 Agent V1 user-actions 与尚无 response 的 request"
    )
    _add_vault_argument(agent_worker)
    agent_worker.add_argument(
        "--once", action="store_true", required=True, help="处理当前 inbox 后退出"
    )
    _add_agent_runtime_arguments(agent_worker)
    agent_worker.set_defaults(handler=command_agent_worker)

    agent_profile = subparsers.add_parser(
        "agent-profile", help="重建并持久严格 Agent V1 公共投影"
    )
    _add_vault_argument(agent_profile)
    agent_profile.set_defaults(handler=command_agent_profile)

    evaluate = subparsers.add_parser("eval", help="运行合成离线或 DeepSeek 实时评测")
    _add_vault_argument(evaluate)
    evaluate.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
    evaluate.add_argument("--live", action="store_true")
    evaluate.add_argument("--output")
    _add_provider_arguments(evaluate, repeat_model=True)
    _add_pricing_arguments(evaluate)
    evaluate.set_defaults(handler=command_eval)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if hasattr(args, "latest") and args.latest < 1:
            raise ContractError("--latest 必须大于 0")
        if hasattr(args, "max_source_chars") and args.max_source_chars < 1:
            raise ContractError("--max-source-chars 必须大于 0")
        if hasattr(args, "timeout") and args.timeout <= 0:
            raise ContractError("--timeout 必须大于 0")
        if hasattr(args, "limit") and not 1 <= args.limit <= 64:
            raise ContractError("--limit 必须在 1..64 之间", kind="budget")
        for field in ("cache_hit_rate", "cache_miss_rate", "output_rate"):
            if (
                hasattr(args, field)
                and getattr(args, field) is not None
                and getattr(args, field) < 0
            ):
                raise ContractError(f"--{field.replace('_', '-')} 不能小于 0")
        return args.handler(args)
    except (ContractError, ProviderError, ValueError) as exc:
        kind = exc.kind if isinstance(exc, ContractError) else "runtime"
        _json_dump(
            {"ok": False, "error": str(exc), "error_kind": kind}, stream=sys.stderr
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
