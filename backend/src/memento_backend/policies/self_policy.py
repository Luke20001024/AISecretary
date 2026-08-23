"""R7 evidence and sensitivity policies for third-layer understanding."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence


SELF_POLICY_VERSION = "self-understanding-policy-v1"
SELF_PROMPT_VERSION = "self-understanding-v1"

SENSITIVE_INFERENCE_TERMS = (
    "人格", "性格类型", "mbti", "身份", "政治立场", "宗教", "性取向",
    "心理疾病", "抑郁", "焦虑症", "健康状况", "情绪", "家庭关系", "亲密关系",
)


def normalize_insight_key(value: str) -> str:
    return " ".join(value.strip().lower().split())


def self_material_gate(themes: Sequence[Mapping[str, Any]]) -> str:
    eligible = [
        theme for theme in themes
        if theme.get("lifecycle") in {"active", "tension"}
    ]
    if len(eligible) < 2:
        return "insufficient_themes"
    if len({str(theme["theme_id"]) for theme in eligible}) < 2:
        return "insufficient_distinct_themes"
    if any(len(theme.get("evidence_refs", ())) < 2 for theme in eligible):
        return "theme_evidence_incomplete"
    return "passed"


def sensitive_inference_reason(values: Sequence[str]) -> Optional[str]:
    text = " ".join(values).casefold()
    for term in SENSITIVE_INFERENCE_TERMS:
        if term.casefold() in text:
            return "sensitive_inference_requires_user_confirmation"
    return None
