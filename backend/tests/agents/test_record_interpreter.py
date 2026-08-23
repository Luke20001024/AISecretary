from __future__ import annotations

import copy
from typing import Any

from memento_backend.agents.capture_understanding_agent import CaptureInput, CaptureUnderstandingAgent
from memento_backend.agents.record_interpreter import InterpretationInput, RecordInterpreter
from memento_backend.providers.protocol import ProviderFailure, ProviderRequest, ProviderResponse, ProviderUsage
from tests.contracts.samples import source_record


def prepared() -> InterpretationInput:
    record: dict[str, Any] = copy.deepcopy(source_record())
    record["source_type"] = "text"
    text = "我希望记录判断发生变化的理由"
    decision = CaptureUnderstandingAgent().evaluate(
        CaptureInput(record, text, user_authored=True),
        user_action_watermark_sha256="d" * 64,
        created_at="2026-08-23T11:00:00+08:00",
    )["proposed_object"]
    return InterpretationInput(record, decision, text)


def test_deterministic_interpreter_only_uses_l0_authorized_span() -> None:
    value = prepared()
    candidate = RecordInterpreter().evaluate(
        value,
        user_action_watermark_sha256="d" * 64,
        created_at="2026-08-23T11:01:00+08:00",
    )
    result = candidate["proposed_object"]
    assert candidate["agent_role"] == "record_interpreter"
    assert result["summary"] == "我希望记录判断发生变化的理由"
    assert result["topics"] == []
    assert result["source_spans"] == value.capture_decision["user_signal_spans"]
    assert candidate["usage"]["mode"] == "deterministic"


class FailedProvider:
    def complete(self, request: ProviderRequest) -> ProviderResponse:
        raise ProviderFailure(
            "fixture failure",
            usage=ProviderUsage("provider", "fixture", "fixture-model", "failed", 12, 0, 12, 0.0, 3),
        )


def test_interpreter_provider_failure_stops_without_formal_object() -> None:
    candidate = RecordInterpreter(FailedProvider()).evaluate(
        prepared(),
        user_action_watermark_sha256="d" * 64,
        created_at="2026-08-23T11:01:00+08:00",
    )
    assert candidate["action"] == "stop"
    assert candidate["proposed_object"] is None
    assert candidate["usage"]["attempt_status"] == "failed"
