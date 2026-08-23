"""L0: route one saved source without attributing resource text to the user."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_bytes, sha256_json, validate_datetime
from memento_backend.domain.refs import SourceSpan
from memento_backend.policies.capture_policy import (
    CAPTURE_POLICY_VERSION,
    RESOURCE_SOURCE_TYPES,
    CaptureRoute,
    has_read_later_marker,
)
from memento_backend.providers.protocol import Provider, ProviderFailure, ProviderRequest, ProviderUsage

from .protocol import AgentRunContext, make_candidate


CAPTURE_PROMPT_VERSION = "capture-understanding-v1"


@dataclass(frozen=True)
class CaptureInput:
    source_record: Mapping[str, Any]
    authorized_text: str
    user_note: Optional[str] = None
    selected_text: Optional[str] = None
    user_authored: Optional[bool] = None
    resource_url: Optional[str] = None
    resource_title: Optional[str] = None
    requested_reading: bool = False

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "source_record_ref": source_record_ref(self.source_record),
            "source_type": self.source_record["source_type"],
            "authorized_text": self.authorized_text,
            "user_note": self.user_note,
            "selected_text": self.selected_text,
            "user_authored": self.user_authored,
            "resource_url": self.resource_url,
            "resource_title": self.resource_title,
            "requested_reading": self.requested_reading,
        }

    def validate(self) -> None:
        validate_contract("source-record-v2.schema.json", self.source_record)
        if len(self.authorized_text) > 200_000:
            raise ContractError("capture authorized text exceeds the L0 limit", kind="size")
        for name, value, limit in (
            ("user_note", self.user_note, 2_000),
            ("selected_text", self.selected_text, 20_000),
            ("resource_title", self.resource_title, 500),
            ("resource_url", self.resource_url, 4_096),
        ):
            if value is not None and (not value or len(value) > limit):
                raise ContractError(f"capture {name} is invalid")


def source_record_ref(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": "source_record",
        "id": record["record_id"],
        "revision": record["revision"],
        "revision_sha256": sha256_json(record),
    }


class CaptureUnderstandingAgent:
    def __init__(self, provider: Optional[Provider] = None) -> None:
        self.provider = provider

    def evaluate(
        self,
        value: CaptureInput,
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
            CAPTURE_PROMPT_VERSION,
            {"input_sha256": input_sha, "watermark": user_action_watermark_sha256, "created_at": created_at},
        )
        spans = self._user_signal_spans(value)
        route = self._deterministic_route(value, spans)
        usage = ProviderUsage.deterministic()
        failure_reason: Optional[str] = None
        if route is None:
            if self.provider is None:
                route = CaptureRoute("ambiguous", "needs_confirmation", "none", "ambiguous_input", "low", True)
            else:
                request = ProviderRequest(
                    run_id=run_id,
                    agent_role="capture_understanding",
                    prompt_version=CAPTURE_PROMPT_VERSION,
                    policy_version=CAPTURE_POLICY_VERSION,
                    input_payload=payload,
                )
                request.validate()
                try:
                    response = self.provider.complete(request)
                    usage = response.usage
                    route = self._provider_route(response.output)
                except ProviderFailure as exc:
                    usage = exc.usage
                    failure_reason = "provider_failed"
                except (ContractError, KeyError, TypeError, ValueError):
                    failure_reason = "provider_invalid_output"
        context = AgentRunContext(
            run_id=run_id,
            agent_role="capture_understanding",
            prompt_version=CAPTURE_PROMPT_VERSION,
            policy_version=CAPTURE_POLICY_VERSION,
            input_sha256=input_sha,
            user_action_watermark_sha256=user_action_watermark_sha256,
            created_at=created_at,
            usage=usage,
        )
        record_ref = source_record_ref(value.source_record)
        if route is None:
            return make_candidate(
                context,
                action="stop",
                proposed_kind="none",
                proposed_object=None,
                source_refs=[record_ref],
                source_spans=[],
                reason_code=failure_reason or "provider_failed",
                confidence="low",
            )
        route.validate()
        if route.processing_route in {"interpret", "resource_index_and_interpret"} and not spans:
            route = CaptureRoute("ambiguous", "needs_confirmation", route.resource_scope, "ambiguous_input", "low", True)
        decision_id = make_id("capture_decision", "capture-decision-v1", {"source_record_ref": record_ref})
        decision = {
            "schema_version": "1.0",
            "kind": "memento_capture_decision_revision",
            "decision_id": decision_id,
            "revision": 1,
            "previous_revision_sha256": None,
            "status": "active",
            "operation": "route",
            "source_record_ref": record_ref,
            "content_role": route.content_role,
            "processing_route": route.processing_route,
            "user_signal_spans": spans,
            "resource_scope": route.resource_scope,
            "reason_code": route.reason_code,
            "confidence": route.confidence,
            "needs_user_confirmation": route.needs_user_confirmation,
            "prompt_version": CAPTURE_PROMPT_VERSION,
            "policy_version": CAPTURE_POLICY_VERSION,
            "user_action_watermark_sha256": user_action_watermark_sha256,
            "created_at": created_at,
            "committed_by": "workflow",
        }
        validate_contract("capture-decision-v1.schema.json", decision)
        return make_candidate(
            context,
            action="propose_create",
            proposed_kind="capture_decision",
            proposed_object=decision,
            source_refs=[record_ref],
            source_spans=spans,
            reason_code=route.reason_code,
            confidence=route.confidence,
        )

    @staticmethod
    def _deterministic_route(value: CaptureInput, spans: Sequence[Mapping[str, Any]]) -> Optional[CaptureRoute]:
        source_type = str(value.source_record["source_type"])
        if source_type == "external_trace":
            if spans:
                return CaptureRoute("external_trace", "interpret", "none", "external_trace", "high", False)
            return CaptureRoute("ambiguous", "needs_confirmation", "none", "ambiguous_input", "low", True)
        if has_read_later_marker(value.user_note):
            return CaptureRoute("read_later", "ask_on_use", "whole_resource", "explicit_read_later_intent", "high", False)
        if source_type in RESOURCE_SOURCE_TYPES:
            if value.user_note is not None or value.selected_text is not None:
                if spans:
                    return CaptureRoute("mixed", "resource_index_and_interpret", "selected_spans", "highlighted_user_signal", "high", False)
                return CaptureRoute("ambiguous", "needs_confirmation", "whole_resource", "ambiguous_input", "low", True)
            return CaptureRoute("resource", "resource_index", "whole_resource", "resource_without_user_signal", "high", False)
        if value.user_authored is True and spans:
            return CaptureRoute("personal_signal", "interpret", "none", "explicit_user_judgment", "high", False)
        if value.user_authored is False and source_type in {"text", "voice_transcript"}:
            return CaptureRoute("archive_only", "archive_only", "none", "resource_without_user_signal", "medium", False)
        return None

    @staticmethod
    def _provider_route(value: Mapping[str, Any]) -> CaptureRoute:
        required = {
            "content_role", "processing_route", "resource_scope", "reason_code",
            "confidence", "needs_user_confirmation",
        }
        if set(value) != required or type(value["needs_user_confirmation"]) is not bool:
            raise ContractError("capture provider output fields are invalid")
        route = CaptureRoute(
            content_role=str(value["content_role"]),
            processing_route=str(value["processing_route"]),
            resource_scope=str(value["resource_scope"]),
            reason_code=str(value["reason_code"]),
            confidence=str(value["confidence"]),
            needs_user_confirmation=bool(value["needs_user_confirmation"]),
        )
        route.validate()
        return route

    @staticmethod
    def _user_signal_spans(value: CaptureInput) -> list[dict[str, Any]]:
        quote: Optional[str] = None
        if value.user_note is not None and value.user_note in value.authorized_text:
            quote = value.user_note
        elif value.user_authored is True and value.authorized_text.strip():
            quote = value.authorized_text.strip()
        if quote is None:
            return []
        record = value.source_record
        span = {
            "record_id": record["record_id"],
            "record_revision": record["revision"],
            "record_revision_sha256": sha256_json(record),
            "source_file": record["source_file"],
            "line_start": record["line_start"],
            "line_end": record["line_end"],
            "quote": quote,
            "quote_sha256": sha256_bytes(quote.encode("utf-8")),
        }
        SourceSpan.from_dict(span)
        return [span]
