from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from memento_backend.contracts.validator import ContractValidationError
from memento_backend.domain.errors import ContractError
from memento_backend.evaluation.shadow_consent import (
    build_shadow_consent,
    consent_ref,
    validate_shadow_consent,
)


def _consent() -> dict[str, Any]:
    return build_shadow_consent(
        dataset_scope={
            "source_label": "personal-vault-r9",
            "source_root": "/Users/example/MementoVault",
            "snapshot_kind": "real_vault_snapshot",
            "allowed_suffixes": [".json", ".md", ".txt"],
            "read_only_only": True,
        },
        thresholds={
            "false_link_rate_max": 0.1,
            "missed_link_rate_max": 0.1,
            "over_inference_rate_max": 0.05,
            "stop_f1_min": 0.9,
            "stop_case_count_min": 3,
            "source_reference_valid_rate_min": 1.0,
            "self_traceability_rate_min": 1.0,
            "resource_as_user_false_rate_max": 0.0,
            "stale_resurrection_count_max": 0,
            "adapter_pass_rate_min": 1.0,
            "source_hash_stability_rate_min": 1.0,
            "estimated_cost_usd_max": 0.5,
            "latency_p95_ms_max": 12000,
        },
        material_gates={
            "theme": {
                "min_active_atoms": 2,
                "min_distinct_days": 2,
                "require_formal_relation": True,
                "dormant_after_days": 30,
            },
            "self": {"min_distinct_themes": 2, "min_evidence_per_theme": 2},
        },
        sensitivity_policy={
            "sensitive_inference_action": "stop_for_user_confirmation",
            "external_context_default": "exclude_unconfirmed",
            "user_override_priority": True,
        },
        agent_schedule={
            "capture_understanding": {"frequency": "event_driven", "local_time": None, "weekday": None},
            "record_interpreter": {"frequency": "event_driven", "local_time": None, "weekday": None},
            "daily_integrator": {"frequency": "daily", "local_time": "22:00", "weekday": None},
            "theme_synthesizer": {"frequency": "weekly", "local_time": "08:00", "weekday": 0},
            "self_understanding": {"frequency": "weekly", "local_time": "09:00", "weekday": 6},
            "context_router": {"frequency": "event_driven", "local_time": None, "weekday": None},
        },
        provider={
            "provider": "provider-x",
            "model": "model-y",
            "prompt_versions": ["capture-v1"],
            "policy_versions": ["policy-v1"],
            "budget": {
                "max_prompt_tokens": 1000,
                "max_completion_tokens": 500,
                "max_cost_usd": 0.5,
                "max_latency_ms": 12000,
            },
        },
        confirmed_at="2026-08-23T09:00:00+08:00",
    )


def test_shadow_consent_is_deterministic_and_self_verifying() -> None:
    first = _consent()
    second = _consent()
    assert first == second
    assert consent_ref(first)["consent_id"] == first["consent_id"]
    validate_shadow_consent(first)


def test_shadow_consent_normalizes_set_like_scope_and_version_lists() -> None:
    value = _consent()
    assert value["dataset_scope"]["allowed_suffixes"] == [".json", ".md", ".txt"]
    assert value["provider"]["prompt_versions"] == sorted(
        value["provider"]["prompt_versions"]
    )
    assert value["provider"]["policy_versions"] == sorted(
        value["provider"]["policy_versions"]
    )


def test_shadow_consent_rejects_tampering_without_reissuing_identity() -> None:
    value = deepcopy(_consent())
    value["thresholds"]["false_link_rate_max"] = 0.2
    with pytest.raises(ContractError, match="identity"):
        validate_shadow_consent(value)


def test_shadow_consent_rejects_material_gate_drift() -> None:
    value = deepcopy(_consent())
    value["material_gates"]["theme"]["min_active_atoms"] = 3
    with pytest.raises(ContractValidationError):
        validate_shadow_consent(value)


def test_shadow_consent_rejects_incoherent_long_term_schedule() -> None:
    value = deepcopy(_consent())
    schedule = value["agent_schedule"]["theme_synthesizer"]
    schedule["frequency"] = "manual"
    schedule["local_time"] = "08:00"
    schedule["weekday"] = None
    payload = {key: value[key] for key in (
        "status", "dataset_scope", "thresholds", "material_gates", "sensitivity_policy",
        "agent_schedule", "provider", "confirmed_at",
    )}
    from memento_backend.domain.ids import sha256_json
    value["consent_id"] = "shc_" + sha256_json(payload)[:24]
    with pytest.raises(ContractError, match="manual"):
        validate_shadow_consent(value)


def test_shadow_consent_rejects_non_finite_budget() -> None:
    value = deepcopy(_consent())
    value["provider"]["budget"]["max_cost_usd"] = float("nan")
    payload = {key: value[key] for key in (
        "status", "dataset_scope", "thresholds", "material_gates", "sensitivity_policy",
        "agent_schedule", "provider", "confirmed_at",
    )}
    from memento_backend.domain.ids import sha256_json
    value["consent_id"] = "shc_" + sha256_json(payload)[:24]
    with pytest.raises(ContractError, match="finite"):
        validate_shadow_consent(value)


def test_shadow_consent_rejects_cost_gate_budget_drift() -> None:
    value = deepcopy(_consent())
    value["provider"]["budget"]["max_cost_usd"] = 0.6
    payload = {key: value[key] for key in (
        "status", "dataset_scope", "thresholds", "material_gates", "sensitivity_policy",
        "agent_schedule", "provider", "confirmed_at",
    )}
    from memento_backend.domain.ids import sha256_json
    value["consent_id"] = "shc_" + sha256_json(payload)[:24]
    with pytest.raises(ContractError, match="cost"):
        validate_shadow_consent(value)


@pytest.mark.parametrize("provider, model", [("TODO", "model-y"), ("provider-x", "placeholder-model")])
def test_shadow_consent_rejects_common_unreviewed_placeholders(
    provider: str, model: str
) -> None:
    with pytest.raises(ContractError, match="placeholder"):
        build_shadow_consent(
            dataset_scope=_consent()["dataset_scope"],
            thresholds=_consent()["thresholds"],
            material_gates=_consent()["material_gates"],
            sensitivity_policy=_consent()["sensitivity_policy"],
            agent_schedule=_consent()["agent_schedule"],
            provider={**_consent()["provider"], "provider": provider, "model": model},
            confirmed_at="2026-08-23T09:00:00+08:00",
        )
