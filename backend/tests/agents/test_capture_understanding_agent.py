from __future__ import annotations

import copy
from typing import Any

from memento_backend.agents.capture_understanding_agent import CaptureInput, CaptureUnderstandingAgent
from memento_backend.providers.protocol import ProviderFailure, ProviderRequest, ProviderResponse, ProviderUsage
from tests.contracts.samples import source_record


def record_for(source_type: str) -> dict[str, Any]:
    value: dict[str, Any] = copy.deepcopy(source_record())
    value["source_type"] = source_type
    return value


def test_explicit_capture_routes_are_conservative_and_deterministic() -> None:
    agent = CaptureUnderstandingAgent()
    watermark = "d" * 64
    cases = (
        (
            CaptureInput(
                record_for("url"),
                "https://example.com/article\n待会再看",
                user_note="待会再看",
                resource_url="https://example.com/article",
                resource_title="Example",
            ),
            ("read_later", "ask_on_use"),
        ),
        (
            CaptureInput(record_for("screenshot_ocr"), "整页网页 OCR，只有作者观点"),
            ("resource", "resource_index"),
        ),
        (
            CaptureInput(
                record_for("screenshot_ocr"),
                "作者段落\n这个和当前 Context 问题有关",
                selected_text="作者段落",
                user_note="这个和当前 Context 问题有关",
            ),
            ("mixed", "resource_index_and_interpret"),
        ),
        (
            CaptureInput(record_for("text"), "变化的理由比结果更值得留下", user_authored=True),
            ("personal_signal", "interpret"),
        ),
        (
            CaptureInput(record_for("voice_transcript"), "刚才那个变化应该留下来", user_authored=True),
            ("personal_signal", "interpret"),
        ),
    )
    for value, expected in cases:
        candidate = agent.evaluate(
            value,
            user_action_watermark_sha256=watermark,
            created_at="2026-08-23T10:00:00+08:00",
        )
        decision = candidate["proposed_object"]
        assert (decision["content_role"], decision["processing_route"]) == expected
        assert candidate["usage"]["mode"] == "deterministic"
        assert candidate["usage"]["total_tokens"] == 0


def test_ambiguous_input_is_saved_for_confirmation_without_provider() -> None:
    candidate = CaptureUnderstandingAgent().evaluate(
        CaptureInput(record_for("voice_transcript"), "那个东西先这样吧"),
        user_action_watermark_sha256="d" * 64,
        created_at="2026-08-23T10:00:00+08:00",
    )
    decision = candidate["proposed_object"]
    assert decision["content_role"] == "ambiguous"
    assert decision["processing_route"] == "needs_confirmation"
    assert decision["needs_user_confirmation"] is True


class FailedProvider:
    def complete(self, request: ProviderRequest) -> ProviderResponse:
        request.validate()
        raise ProviderFailure(
            "offline fixture failure",
            usage=ProviderUsage("provider", "fixture", "fixture-model", "failed", 10, 0, 10, 0.0, 12),
        )


def test_provider_failure_stops_with_usage_and_no_formal_proposal() -> None:
    candidate = CaptureUnderstandingAgent(FailedProvider()).evaluate(
        CaptureInput(record_for("voice_transcript"), "那个东西先这样吧"),
        user_action_watermark_sha256="d" * 64,
        created_at="2026-08-23T10:00:00+08:00",
    )
    assert candidate["action"] == "stop"
    assert candidate["proposed_object"] is None
    assert candidate["reason_code"] == "provider_failed"
    assert candidate["usage"]["attempt_status"] == "failed"
