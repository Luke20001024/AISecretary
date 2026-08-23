"""Deterministic L0 routing gates and enums shared with the JSON contract."""

from __future__ import annotations

from dataclasses import dataclass

from memento_backend.domain.errors import ContractError


CAPTURE_POLICY_VERSION = "capture-policy-v1"
READ_LATER_MARKERS = ("待会再看", "稍后再看", "之后再看", "收藏一下", "先存着", "回头看")
RESOURCE_SOURCE_TYPES = frozenset({"url", "web_page", "screenshot_ocr", "image_note", "file_note"})


@dataclass(frozen=True)
class CaptureRoute:
    content_role: str
    processing_route: str
    resource_scope: str
    reason_code: str
    confidence: str
    needs_user_confirmation: bool

    def validate(self) -> None:
        if self.content_role not in {
            "personal_signal", "resource", "read_later", "mixed",
            "archive_only", "external_trace", "ambiguous",
        }:
            raise ContractError("capture content role is invalid")
        if self.processing_route not in {
            "interpret", "resource_index", "ask_on_use", "resource_index_and_interpret",
            "archive_only", "needs_confirmation",
        }:
            raise ContractError("capture processing route is invalid")
        if self.resource_scope not in {"none", "selected_spans", "whole_resource"}:
            raise ContractError("capture resource scope is invalid")
        if self.reason_code not in {
            "explicit_user_judgment", "explicit_read_later_intent", "resource_without_user_signal",
            "highlighted_user_signal", "external_trace", "ambiguous_input", "user_override",
        }:
            raise ContractError("capture reason code is invalid")
        if self.confidence not in {"low", "medium", "high"}:
            raise ContractError("capture confidence is invalid")


def has_read_later_marker(value: str | None) -> bool:
    normalized = "" if value is None else value.strip()
    return any(marker in normalized for marker in READ_LATER_MARKERS)
