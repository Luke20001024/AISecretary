"""Transport-neutral local tool façade for later MCP composition.

The façade exposes only bounded Context reads and append-only writeback. It
contains no filesystem path parameter and no Theme/SelfInsight mutation method.
"""

from __future__ import annotations

from typing import Any, Mapping

from memento_backend.agents.context_router import ContextRequest
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import sha256_json
from memento_backend.projections.common import ProjectionInputs
from memento_backend.workflows.append_external_trace import AppendExternalTraceWorkflow, ExternalTraceInput
from memento_backend.workflows.create_context_pack import CreateContextPackWorkflow


TOOL_MANIFEST = (
    {"name": "memento.search_context", "description": "按任务、主题和时间生成最小 Context Pack", "mutates_cognitive_objects": False},
    {"name": "memento.get_self_insight", "description": "在授权 Context Pack 中读取一条当前理解及其边界", "mutates_cognitive_objects": False},
    {"name": "memento.get_theme", "description": "在授权 Context Pack 中读取主题与形成依据", "mutates_cognitive_objects": False},
    {"name": "memento.trace_evidence", "description": "在授权范围内回溯相关 Memory 与必要原文引用", "mutates_cognitive_objects": False},
    {"name": "memento.create_context_pack", "description": "同时返回 JSON 与 Markdown 形式的任务 Context", "mutates_cognitive_objects": False},
    {"name": "memento.append_trace", "description": "把外部工作的决定或新问题作为可审计痕迹写回", "mutates_cognitive_objects": False},
    {"name": "memento.correct_context", "description": "写回用户明确纠正并重新进入常规记录链路", "mutates_cognitive_objects": False},
    {"name": "memento.report_outcome", "description": "写回现实结果并重新进入常规记录链路", "mutates_cognitive_objects": False},
)


def _context_refs_for_validation(value: Any) -> tuple[Mapping[str, Any], ...]:
    """Preserve malformed JSON refs so the workflow can deny and audit them."""
    if not isinstance(value, (list, tuple)):
        return ({"invalid_context_refs": value},)
    return tuple(
        dict(item) if isinstance(item, Mapping) else {"invalid_context_ref": item}
        for item in value
    )


class ContextToolFacade:
    """Stable in-process boundary that an MCP transport may call later."""

    def __init__(self, read_workflow: CreateContextPackWorkflow, writeback_workflow: AppendExternalTraceWorkflow) -> None:
        self.read_workflow = read_workflow
        self.writeback_workflow = writeback_workflow

    def read(
        self,
        arguments: Mapping[str, Any],
        *,
        inputs: ProjectionInputs,
        requested_at: str,
        completed_at: str,
        tool_name: str = "memento.create_context_pack",
        target_id: Any = None,
    ) -> Mapping[str, Any]:
        request = ContextRequest(
            grant_id=str(arguments["grant_id"]), session_id=str(arguments["session_id"]),
            client_id=str(arguments["client_id"]), task=str(arguments["task"]),
            topic_scope=tuple(str(item) for item in arguments["topic_scope"]),
            time_scope=arguments.get("time_scope"),
            include_source_quotes=arguments.get("include_source_quotes", False),
            tool_name=tool_name, target_id=target_id,
        )
        return self.read_workflow.create(
            request, inputs=inputs, requested_at=requested_at, completed_at=completed_at,
        ).pack

    def writeback(
        self,
        arguments: Mapping[str, Any],
        *,
        requested_at: str,
        completed_at: str,
    ) -> Mapping[str, Any]:
        trace = ExternalTraceInput(
            grant_id=str(arguments["grant_id"]), session_id=str(arguments["session_id"]),
            pack_id=str(arguments["pack_id"]), client_id=arguments["client_id"],
            trace_type=arguments["trace_type"], content=arguments["content"],
            context_refs=_context_refs_for_validation(arguments.get("context_refs", ())),
            user_confirmed=arguments.get("user_confirmed", False),
        )
        result = self.writeback_workflow.append(trace, requested_at=requested_at, completed_at=completed_at)
        external_trace_ref = {
            "kind": "external_trace", "id": result.trace["trace_id"], "revision": result.trace["revision"],
            "revision_sha256": sha256_json(result.trace),
        }
        return {
            "source_record_ref": result.trace["source_record_ref"],
            "external_trace_ref": external_trace_ref, "audit_ref": dict(result.audit_ref),
            "next_route": "L0_capture_understanding",
        }

    def search_context(self, arguments: Mapping[str, Any], *, inputs: ProjectionInputs, requested_at: str, completed_at: str) -> Mapping[str, Any]:
        return self.read(
            arguments, inputs=inputs, requested_at=requested_at, completed_at=completed_at,
            tool_name="memento.search_context",
        )

    def get_self_insight(self, arguments: Mapping[str, Any], *, inputs: ProjectionInputs, requested_at: str, completed_at: str) -> Mapping[str, Any]:
        insight_id = str(arguments["insight_id"])
        request = ContextRequest(
            grant_id=str(arguments["grant_id"]), session_id=str(arguments["session_id"]),
            client_id=str(arguments["client_id"]), task=str(arguments["task"]),
            topic_scope=tuple(str(item) for item in arguments["topic_scope"]),
            time_scope=arguments.get("time_scope"),
            include_source_quotes=arguments.get("include_source_quotes", False),
            tool_name="memento.get_self_insight", target_id=insight_id,
        )
        pack = self.read_workflow.create(
            request, inputs=inputs, requested_at=requested_at, completed_at=completed_at,
        ).pack
        for item in pack["self_insights"]:
            if item["ref"]["id"] == insight_id:
                return dict(item)
        self.read_workflow.audit_target_denial(
            request, reason_code="target_outside_context_pack",
            requested_at=requested_at, completed_at=completed_at,
        )
        raise ContractError("self insight is outside this authorized Context Pack", kind="authorization")

    def get_theme(self, arguments: Mapping[str, Any], *, inputs: ProjectionInputs, requested_at: str, completed_at: str) -> Mapping[str, Any]:
        theme_id = str(arguments["theme_id"])
        request = ContextRequest(
            grant_id=str(arguments["grant_id"]), session_id=str(arguments["session_id"]),
            client_id=str(arguments["client_id"]), task=str(arguments["task"]),
            topic_scope=tuple(str(item) for item in arguments["topic_scope"]),
            time_scope=arguments.get("time_scope"),
            include_source_quotes=arguments.get("include_source_quotes", False),
            tool_name="memento.get_theme", target_id=theme_id,
        )
        pack = self.read_workflow.create(
            request, inputs=inputs, requested_at=requested_at, completed_at=completed_at,
        ).pack
        for item in pack["themes"]:
            if item["ref"]["id"] == theme_id:
                return dict(item)
        self.read_workflow.audit_target_denial(
            request, reason_code="target_outside_context_pack",
            requested_at=requested_at, completed_at=completed_at,
        )
        raise ContractError("theme is outside this authorized Context Pack", kind="authorization")

    def trace_evidence(self, arguments: Mapping[str, Any], *, inputs: ProjectionInputs, requested_at: str, completed_at: str) -> Mapping[str, Any]:
        expanded = dict(arguments)
        expanded["include_source_quotes"] = True
        pack = self.read(
            expanded, inputs=inputs, requested_at=requested_at, completed_at=completed_at,
            tool_name="memento.trace_evidence",
        )
        return {"memories": pack["memories"], "source_quotes": pack["source_quotes"]}

    def create_context_pack(self, arguments: Mapping[str, Any], *, inputs: ProjectionInputs, requested_at: str, completed_at: str) -> Mapping[str, Any]:
        pack = self.read(
            arguments, inputs=inputs, requested_at=requested_at, completed_at=completed_at,
            tool_name="memento.create_context_pack",
        )
        return {"json": pack, "markdown": render_context_pack_markdown(pack)}

    def append_trace(self, arguments: Mapping[str, Any], *, requested_at: str, completed_at: str) -> Mapping[str, Any]:
        return self.writeback(arguments, requested_at=requested_at, completed_at=completed_at)

    def correct_context(self, arguments: Mapping[str, Any], *, requested_at: str, completed_at: str) -> Mapping[str, Any]:
        expanded = dict(arguments)
        expanded["trace_type"] = "correction"
        return self.writeback(expanded, requested_at=requested_at, completed_at=completed_at)

    def report_outcome(self, arguments: Mapping[str, Any], *, requested_at: str, completed_at: str) -> Mapping[str, Any]:
        expanded = dict(arguments)
        expanded["trace_type"] = "outcome"
        return self.writeback(expanded, requested_at=requested_at, completed_at=completed_at)


def render_context_pack_markdown(pack: Mapping[str, Any]) -> str:
    """Render the same bounded snapshot without reading any additional data."""
    lines = ["# 当前任务 Context\n", f"任务：{pack['task']}", "", "## 当前最相关的理解"]
    if pack["self_insights"]:
        for item in pack["self_insights"]:
            lines.append(f"- {item['title']}：{item['statement']}（范围：{item['scope']}）")
    else:
        lines.append("- 当前没有符合授权范围且经用户确认的长期理解")
    lines.extend(["", "## 相关主题"])
    lines.extend(f"- {item['title']}：{item['statement']}" for item in pack["themes"])
    lines.extend(["", "## 关键依据"])
    lines.extend(f"- {item['statement']}" for item in pack["memories"])
    lines.extend(["", "## 仍未知"])
    lines.extend(f"- {item}" for item in pack["unknowns"])
    lines.extend(["", "## 使用边界"])
    lines.extend(f"- {item}" for item in pack["prohibited_inferences"])
    lines.extend(["", f"数据版本：{pack['input_sha256']}", f"生成时间：{pack['generated_at']}"])
    return "\n".join(lines) + "\n"
