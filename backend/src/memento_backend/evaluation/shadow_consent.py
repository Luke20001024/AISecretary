"""Structured user confirmation for one real R9 shadow evaluation policy."""

from __future__ import annotations

import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import sha256_json, validate_datetime


def build_shadow_consent(
    *,
    dataset_scope: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    material_gates: Mapping[str, Any],
    sensitivity_policy: Mapping[str, Any],
    agent_schedule: Mapping[str, Any],
    provider: Mapping[str, Any],
    confirmed_at: str,
) -> dict[str, Any]:
    validate_datetime(confirmed_at, "confirmed_at")
    normalized_scope = deepcopy(dict(dataset_scope))
    normalized_scope["allowed_suffixes"] = sorted(normalized_scope.get("allowed_suffixes", []))
    normalized_provider = deepcopy(dict(provider))
    normalized_provider["prompt_versions"] = sorted(
        normalized_provider.get("prompt_versions", [])
    )
    normalized_provider["policy_versions"] = sorted(
        normalized_provider.get("policy_versions", [])
    )
    payload = {
        "status": "confirmed",
        "dataset_scope": normalized_scope,
        "thresholds": deepcopy(dict(thresholds)),
        "material_gates": deepcopy(dict(material_gates)),
        "sensitivity_policy": deepcopy(dict(sensitivity_policy)),
        "agent_schedule": deepcopy(dict(agent_schedule)),
        "provider": normalized_provider,
        "confirmed_at": confirmed_at,
    }
    value = {
        "schema_version": "1.0",
        "kind": "memento_shadow_consent",
        "consent_id": "shc_" + sha256_json(payload)[:24],
        **payload,
    }
    validate_shadow_consent(value)
    return value


def validate_shadow_consent(value: Mapping[str, Any]) -> None:
    validate_contract("shadow-consent-v1.schema.json", value)
    validate_datetime(value["confirmed_at"], "confirmed_at")
    source_root = str(value["dataset_scope"]["source_root"])
    if not Path(source_root).is_absolute() or str(Path(source_root)) != source_root:
        raise ContractError("shadow consent source root must be a normalized absolute path")
    protected_strings = (
        value["dataset_scope"]["source_label"],
        source_root,
        value["provider"]["provider"],
        value["provider"]["model"],
    )
    if any(_is_unreviewed_placeholder(str(item)) for item in protected_strings):
        raise ContractError("shadow consent still contains an unreviewed placeholder")
    payload = {
        "status": value["status"],
        "dataset_scope": value["dataset_scope"],
        "thresholds": value["thresholds"],
        "material_gates": value["material_gates"],
        "sensitivity_policy": value["sensitivity_policy"],
        "agent_schedule": value["agent_schedule"],
        "provider": value["provider"],
        "confirmed_at": value["confirmed_at"],
    }
    if value["consent_id"] != "shc_" + sha256_json(payload)[:24]:
        raise ContractError("shadow consent identity is inconsistent", kind="evidence")
    if value["dataset_scope"]["allowed_suffixes"] != sorted(
        value["dataset_scope"]["allowed_suffixes"]
    ):
        raise ContractError("shadow consent file suffixes must be sorted", kind="evidence")
    for field in ("prompt_versions", "policy_versions"):
        if value["provider"][field] != sorted(value["provider"][field]):
            raise ContractError("shadow consent version lists must be sorted", kind="evidence")
    numbers = [
        *value["thresholds"].values(),
        *value["provider"]["budget"].values(),
    ]
    if any(type(item) not in {int, float} or not math.isfinite(float(item)) for item in numbers):
        raise ContractError("shadow consent numbers must be finite")
    if value["thresholds"]["estimated_cost_usd_max"] != value["provider"]["budget"]["max_cost_usd"]:
        raise ContractError("shadow consent quality cost and provider budget must match")
    if value["provider"]["budget"]["max_latency_ms"] < value["thresholds"]["latency_p95_ms_max"]:
        raise ContractError("shadow consent provider latency budget cannot be below its quality gate")
    for role in ("theme_synthesizer", "self_understanding"):
        schedule = value["agent_schedule"][role]
        frequency = schedule["frequency"]
        if frequency == "weekly" and (schedule["local_time"] is None or schedule["weekday"] is None):
            raise ContractError("weekly Agent schedule requires time and weekday")
        if frequency == "daily" and (schedule["local_time"] is None or schedule["weekday"] is not None):
            raise ContractError("daily Agent schedule requires time without weekday")
        if frequency == "manual" and (schedule["local_time"] is not None or schedule["weekday"] is not None):
            raise ContractError("manual Agent schedule cannot include time or weekday")


def consent_ref(value: Mapping[str, Any]) -> dict[str, str]:
    validate_shadow_consent(value)
    return {
        "consent_id": str(value["consent_id"]),
        "consent_sha256": sha256_json(value),
    }


def _is_unreviewed_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    markers = (
        "请填写",
        "<填写",
        "placeholder",
        "replace-me",
        "replace_with",
        "your-provider",
        "your-model",
    )
    return normalized in {"todo", "tbd"} or any(marker in normalized for marker in markers)
