"""Small local MCP-compatible dispatch boundary for the eight frozen tools."""

from __future__ import annotations

from typing import Any, Mapping, Optional, cast

from memento_backend.domain.errors import ContractError
from memento_backend.projections.common import ProjectionInputs

from .context_tools import ContextToolFacade, TOOL_MANIFEST


READ_TOOLS = {
    "memento.search_context": "search_context",
    "memento.get_self_insight": "get_self_insight",
    "memento.get_theme": "get_theme",
    "memento.trace_evidence": "trace_evidence",
    "memento.create_context_pack": "create_context_pack",
}
WRITE_TOOLS = {
    "memento.append_trace": "append_trace",
    "memento.correct_context": "correct_context",
    "memento.report_outcome": "report_outcome",
}
_READ_REQUIRED = frozenset({"grant_id", "session_id", "client_id", "task", "topic_scope"})
_READ_OPTIONAL = frozenset({"time_scope", "include_source_quotes"})
_WRITE_REQUIRED = frozenset({"grant_id", "session_id", "pack_id", "client_id", "content"})
_WRITE_OPTIONAL = frozenset({"context_refs", "user_confirmed"})
TOOL_ARGUMENT_FIELDS = {
    "memento.search_context": (_READ_REQUIRED, _READ_OPTIONAL),
    "memento.get_self_insight": (_READ_REQUIRED | {"insight_id"}, _READ_OPTIONAL),
    "memento.get_theme": (_READ_REQUIRED | {"theme_id"}, _READ_OPTIONAL),
    "memento.trace_evidence": (_READ_REQUIRED, frozenset({"time_scope"})),
    "memento.create_context_pack": (_READ_REQUIRED, _READ_OPTIONAL),
    "memento.append_trace": (_WRITE_REQUIRED | {"trace_type"}, _WRITE_OPTIONAL),
    "memento.correct_context": (_WRITE_REQUIRED, _WRITE_OPTIONAL),
    "memento.report_outcome": (_WRITE_REQUIRED, _WRITE_OPTIONAL),
}


class LocalMcpContextServer:
    """Allow-listed dispatcher suitable for a later stdio MCP transport."""

    def __init__(self, tools: ContextToolFacade) -> None:
        self.tools = tools

    def list_tools(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(dict(item) for item in TOOL_MANIFEST)

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        requested_at: str,
        completed_at: str,
        inputs: Optional[ProjectionInputs] = None,
    ) -> Mapping[str, Any]:
        expected = TOOL_ARGUMENT_FIELDS.get(name)
        if expected is None:
            raise ContractError("MCP tool is outside the allow-list", kind="authorization")
        required, optional = expected
        actual = frozenset(arguments)
        if required - actual or actual - required - optional:
            raise ContractError(
                f"MCP tool arguments differ; missing={sorted(required - actual)} "
                f"unknown={sorted(actual - required - optional)}",
                kind="authorization",
            )
        if name in READ_TOOLS:
            if inputs is None:
                raise ContractError("read tool requires a bounded ProjectionInputs snapshot", kind="authorization")
            method = getattr(self.tools, READ_TOOLS[name])
            return cast(Mapping[str, Any], method(arguments, inputs=inputs, requested_at=requested_at, completed_at=completed_at))
        if name in WRITE_TOOLS:
            method = getattr(self.tools, WRITE_TOOLS[name])
            return cast(Mapping[str, Any], method(arguments, requested_at=requested_at, completed_at=completed_at))
        raise ContractError("MCP tool is outside the allow-list", kind="authorization")
