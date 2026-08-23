"""L1: interpret only the user-authored spans authorized by L0."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from memento_backend.agents.capture_understanding_agent import source_record_ref
from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_json, validate_datetime
from memento_backend.policies.interpretation_policy import (
    INTERPRETATION_POLICY_VERSION,
    INTERPRETATION_PROMPT_VERSION,
    validate_interpretation_output,
)
from memento_backend.providers.protocol import Provider, ProviderFailure, ProviderRequest, ProviderUsage

from .protocol import AgentRunContext, make_candidate


@dataclass(frozen=True)
class InterpretationInput:
    source_record: Mapping[str, Any]
    capture_decision: Mapping[str, Any]
    authorized_text: str

    def validate(self) -> None:
        validate_contract("source-record-v2.schema.json", self.source_record)
        validate_contract("capture-decision-v1.schema.json", self.capture_decision)
        if self.capture_decision["source_record_ref"] != source_record_ref(self.source_record):
            raise ContractError("interpretation capture decision is bound to another source", kind="evidence")
        if self.capture_decision["processing_route"] not in {"interpret", "resource_index_and_interpret"}:
            raise ContractError("capture route does not authorize interpretation", kind="authorization")
        spans = self.capture_decision["user_signal_spans"]
        if not spans:
            raise ContractError("interpretation requires an authorized user signal", kind="authorization")
        for span in spans:
            if span["record_revision_sha256"] != sha256_json(self.source_record):
                raise ContractError("interpretation source span is stale", kind="evidence")
            if span["quote"] not in self.authorized_text:
                raise ContractError("interpretation source quote cannot be reverified", kind="evidence")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "source_record_ref": source_record_ref(self.source_record),
            "capture_decision_ref": capture_decision_ref(self.capture_decision),
            "authorized_user_signals": [span["quote"] for span in self.capture_decision["user_signal_spans"]],
        }


def capture_decision_ref(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": "capture_decision",
        "id": value["decision_id"],
        "revision": value["revision"],
        "revision_sha256": sha256_json(value),
    }


class RecordInterpreter:
    def __init__(self, provider: Optional[Provider] = None) -> None:
        self.provider = provider

    def evaluate(
        self,
        value: InterpretationInput,
        *,
        user_action_watermark_sha256: str,
        created_at: str,
    ) -> Mapping[str, Any]:
        value.validate()
        validate_datetime(created_at, "created_at")
        payload = value.canonical_payload()
        input_sha = sha256_json(payload)
        run_id = make_id(
            "agent_run",
            INTERPRETATION_PROMPT_VERSION,
            {"input_sha256": input_sha, "watermark": user_action_watermark_sha256, "created_at": created_at},
        )
        usage = ProviderUsage.deterministic()
        output: Optional[Mapping[str, Any]] = None
        failure_reason: Optional[str] = None
        if self.provider is None:
            output = self._deterministic_output(value)
        else:
            request = ProviderRequest(
                run_id=run_id,
                agent_role="record_interpreter",
                prompt_version=INTERPRETATION_PROMPT_VERSION,
                policy_version=INTERPRETATION_POLICY_VERSION,
                input_payload=payload,
            )
            request.validate()
            try:
                response = self.provider.complete(request)
                usage = response.usage
                validate_interpretation_output(response.output)
                output = response.output
            except ProviderFailure as exc:
                usage = exc.usage
                failure_reason = "provider_failed"
            except (ContractError, KeyError, TypeError, ValueError):
                failure_reason = "provider_invalid_output"

        context = AgentRunContext(
            run_id=run_id,
            agent_role="record_interpreter",
            prompt_version=INTERPRETATION_PROMPT_VERSION,
            policy_version=INTERPRETATION_POLICY_VERSION,
            input_sha256=input_sha,
            user_action_watermark_sha256=user_action_watermark_sha256,
            created_at=created_at,
            usage=usage,
        )
        refs = [source_record_ref(value.source_record), capture_decision_ref(value.capture_decision)]
        spans = [dict(span) for span in value.capture_decision["user_signal_spans"]]
        if output is None:
            return make_candidate(
                context,
                action="stop",
                proposed_kind="none",
                proposed_object=None,
                source_refs=refs,
                source_spans=spans,
                reason_code=failure_reason or "insufficient_user_signal",
                confidence="low",
            )
        validate_interpretation_output(output)
        interpretation = {
            "schema_version": "2.0",
            "kind": "memento_record_interpretation_revision",
            "interpretation_id": make_id("record_interpretation", "record-interpretation-v2", {"source_record_ref": refs[0]}),
            "revision": 1,
            "previous_revision_sha256": None,
            "status": "ready" if output["uncertainty"] != "high" else "needs_review",
            "operation": "interpret",
            "source_record_ref": refs[0],
            "capture_decision_ref": refs[1],
            "summary": str(output["summary"]).strip(),
            "content_types": list(output["content_types"]),
            "topics": list(output["topics"]),
            "purposes": list(output["purposes"]),
            "stance": output["stance"],
            "uncertainty": output["uncertainty"],
            "source_spans": spans,
            "prompt_version": INTERPRETATION_PROMPT_VERSION,
            "policy_version": INTERPRETATION_POLICY_VERSION,
            "user_action_watermark_sha256": user_action_watermark_sha256,
            "created_at": created_at,
            "committed_by": "workflow",
        }
        validate_contract("record-interpretation-v2.schema.json", interpretation)
        return make_candidate(
            context,
            action="propose_create",
            proposed_kind="record_interpretation",
            proposed_object=interpretation,
            source_refs=refs,
            source_spans=spans,
            reason_code="authorized_user_signal_interpreted",
            confidence="high" if output["uncertainty"] == "low" else "medium",
        )

    @staticmethod
    def _deterministic_output(value: InterpretationInput) -> Mapping[str, Any]:
        signals = [str(span["quote"]).strip() for span in value.capture_decision["user_signal_spans"]]
        summary = "；".join(signals)[:600]
        content_types = ["own_idea"]
        purposes = ["continue_thinking"]
        if any(marker in summary for marker in ("？", "?", "如何", "为什么")):
            content_types.append("question")
        if any(marker in summary for marker in ("决定", "采用", "选择", "先")):
            content_types.append("decision")
            purposes.append("future_decision")
        if any(marker in summary for marker in ("要", "需要", "开始", "去做", "执行")):
            content_types.append("action")
            purposes.append("action_clue")
        return {
            "summary": summary,
            "content_types": list(dict.fromkeys(content_types)),
            "topics": [],
            "purposes": list(dict.fromkeys(purposes)),
            "stance": "self_observation",
            "uncertainty": "medium",
        }
