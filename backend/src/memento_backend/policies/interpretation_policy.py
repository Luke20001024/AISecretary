"""Conservative L1 interpretation policy and closed vocabularies."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from memento_backend.domain.errors import ContractError


INTERPRETATION_POLICY_VERSION = "interpretation-policy-v1"
INTERPRETATION_PROMPT_VERSION = "record-interpreter-v1"

CONTENT_TYPES = {
    "own_idea", "observation", "question", "decision", "action",
    "experience", "learning", "quoted_material",
}
PURPOSES = {
    "find_later", "continue_thinking", "create", "future_decision",
    "action_clue", "preserve_only",
}
STANCES = {"agree", "doubt", "reject", "inspired", "self_observation", "unresolved", "unknown"}
UNCERTAINTIES = {"low", "medium", "high"}


def validate_interpretation_output(value: Mapping[str, Any]) -> None:
    required = {"summary", "content_types", "topics", "purposes", "stance", "uncertainty"}
    if set(value) != required:
        raise ContractError("record interpreter provider output fields are invalid")
    summary = value["summary"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 600:
        raise ContractError("record interpreter summary is invalid")
    _validate_string_list(value["content_types"], CONTENT_TYPES, 12, "content_types")
    _validate_string_list(value["purposes"], PURPOSES, 12, "purposes")
    topics = value["topics"]
    if not isinstance(topics, Sequence) or isinstance(topics, (str, bytes)) or len(topics) > 24:
        raise ContractError("record interpreter topics are invalid")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 80 for item in topics):
        raise ContractError("record interpreter topic is invalid")
    if len(set(topics)) != len(topics):
        raise ContractError("record interpreter topics contain duplicates")
    if value["stance"] not in STANCES or value["uncertainty"] not in UNCERTAINTIES:
        raise ContractError("record interpreter stance or uncertainty is invalid")


def _validate_string_list(raw: Any, allowed: set[str], limit: int, name: str) -> None:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) > limit:
        raise ContractError(f"record interpreter {name} is invalid")
    if any(not isinstance(item, str) or item not in allowed for item in raw) or len(set(raw)) != len(raw):
        raise ContractError(f"record interpreter {name} is invalid")
