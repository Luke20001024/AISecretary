"""Shared candidate envelope helpers for every Agent layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.ids import make_id, sha256_json, validate_datetime, validate_id
from memento_backend.providers.protocol import ProviderUsage


@dataclass(frozen=True)
class AgentRunContext:
    run_id: str
    agent_role: str
    prompt_version: str
    policy_version: str
    input_sha256: str
    user_action_watermark_sha256: str
    created_at: str
    usage: ProviderUsage

    def validate(self) -> None:
        validate_id("agent_run", self.run_id, "run_id")
        validate_datetime(self.created_at, "created_at")
        if not self.prompt_version or not self.policy_version:
            raise ValueError("agent versions are required")
        self.usage.to_dict()


def make_candidate(
    context: AgentRunContext,
    *,
    action: str,
    proposed_kind: str,
    proposed_object: Optional[Mapping[str, Any]],
    source_refs: Sequence[Mapping[str, Any]],
    source_spans: Sequence[Mapping[str, Any]],
    reason_code: str,
    confidence: str,
) -> dict[str, Any]:
    context.validate()
    base = {
        "run_id": context.run_id,
        "agent_role": context.agent_role,
        "action": action,
        "proposed_kind": proposed_kind,
        "proposed_object": None if proposed_object is None else dict(proposed_object),
        "source_refs": [dict(value) for value in source_refs],
        "source_spans": [dict(value) for value in source_spans],
        "reason_code": reason_code,
        "confidence": confidence,
        "prompt_version": context.prompt_version,
        "policy_version": context.policy_version,
        "input_sha256": context.input_sha256,
        "user_action_watermark_sha256": context.user_action_watermark_sha256,
        "usage": context.usage.to_dict(),
        "created_at": context.created_at,
    }
    candidate = {
        "schema_version": "1.0",
        "kind": "memento_agent_action_candidate",
        "candidate_id": make_id("agent_candidate", "agent-action-candidate-v1", base),
        **base,
    }
    validate_contract("agent-action-candidate-v1.schema.json", candidate)
    return candidate
