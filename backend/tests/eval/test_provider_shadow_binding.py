from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest

from memento_backend.domain.errors import ContractError
from memento_backend.evaluation.provider_shadow_binding import bind_provider_shadow_producer
from memento_backend.evaluation.shadow_consent import build_shadow_consent
from memento_backend.evaluation.shadow_runner import build_shadow_plan
from memento_backend.evaluation.shadow_snapshot import create_read_only_snapshot
from memento_backend.evaluation.shadow_worker import (
    ShadowCaseInput,
    ShadowCasePrediction,
    ShadowProducer,
)
from memento_backend.projections.bundle_projector import ProjectionBundle, build_projection_bundle
from memento_backend.providers.protocol import ProviderUsage

from tests.fixtures.formal_20d import formal_20d_inputs


def _thresholds() -> dict[str, float | int]:
    return {
        "false_link_rate_max": 0.1,
        "missed_link_rate_max": 0.1,
        "over_inference_rate_max": 0.1,
        "stop_f1_min": 0.8,
        "stop_case_count_min": 3,
        "source_reference_valid_rate_min": 0.9,
        "self_traceability_rate_min": 0.9,
        "resource_as_user_false_rate_max": 0.0,
        "stale_resurrection_count_max": 0,
        "adapter_pass_rate_min": 1.0,
        "source_hash_stability_rate_min": 1.0,
        "estimated_cost_usd_max": 1.0,
        "latency_p95_ms_max": 1000,
    }


def _consent(tmp_path: Path) -> dict[str, Any]:
    source = tmp_path / "source"
    return build_shadow_consent(
        dataset_scope={
            "source_label": "approved-shadow-vault",
            "source_root": str(source.resolve()),
            "snapshot_kind": "real_vault_snapshot",
            "allowed_suffixes": [".md"],
            "read_only_only": True,
        },
        thresholds=_thresholds(),
        material_gates={
            "theme": {"min_active_atoms": 2, "min_distinct_days": 2, "require_formal_relation": True, "dormant_after_days": 30},
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
            "provider": "approved-provider",
            "model": "approved-model",
            "prompt_versions": ["shadow-prompt-v1"],
            "policy_versions": ["shadow-policy-v1"],
            "budget": {"max_prompt_tokens": 1000, "max_completion_tokens": 1000, "max_cost_usd": 1.0, "max_latency_ms": 1000},
        },
        confirmed_at="2026-08-23T10:00:00+08:00",
    )


def _plan(tmp_path: Path, consent: Mapping[str, Any]) -> dict[str, Any]:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    (source / "record.md").write_text("bound input", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir(mode=0o700, exist_ok=True)
    output.chmod(0o700)
    snapshot = create_read_only_snapshot(
        source,
        output,
        source_label="approved-shadow-vault",
        snapshot_kind="real_vault_snapshot",
        authorization_ref=str(consent["consent_id"]),
        allowed_suffixes=(".md",),
        created_at="2026-08-23T10:01:00+08:00",
    )
    return build_shadow_plan(
        snapshot_id=str(snapshot.manifest["snapshot_id"]),
        dataset_kind="real_vault_snapshot",
        execution_mode="provider_shadow",
        created_at="2026-08-23T10:02:00+08:00",
        thresholds=_thresholds(),
        user_confirmation_status="confirmed",
        user_confirmation_ref=str(consent["consent_id"]),
        provider="approved-provider",
        model="approved-model",
        prompt_versions=("shadow-prompt-v1",),
        policy_versions=("shadow-policy-v1",),
        max_prompt_tokens=1000,
        max_completion_tokens=1000,
        max_cost_usd=1.0,
        max_latency_ms=1000,
        consent=consent,
        case_set_ref={"case_set_id": "shcs_" + "a" * 24, "case_set_sha256": "a" * 64},
    )


class _Producer:
    def __init__(self, usage: ProviderUsage) -> None:
        self.usage = usage

    def produce_case(self, case: ShadowCaseInput) -> ShadowCasePrediction:
        return ShadowCasePrediction(
            case_id=case.case_id,
            predicted_links=frozenset(), predicted_inferences=frozenset(), did_stop=True,
            valid_source_references=0, valid_self_traces=0, resource_as_user_errors=0,
            stale_resurrections=0, adapter_passes=0, stable_source_hashes=0, usage=self.usage,
        )

    def candidate_bundle(self) -> ProjectionBundle:
        return build_projection_bundle(
            formal_20d_inputs(), as_of="2026-08-18", generated_at="2026-08-23T10:03:00+08:00"
        )


def _usage(*, provider: str = "approved-provider", model: str = "approved-model") -> ProviderUsage:
    return ProviderUsage("provider", provider, model, "succeeded", 1, 1, 2, 0.01, 10)


def _case() -> ShadowCaseInput:
    return ShadowCaseInput(case_id="case-1", files={}, checks={})


def test_binding_requires_real_confirmed_provider_plan_and_exact_usage(tmp_path: Path) -> None:
    consent = _consent(tmp_path)
    bound = bind_provider_shadow_producer(
        plan=_plan(tmp_path, consent), consent=consent, producer=_Producer(_usage())
    )
    assert isinstance(bound, ShadowProducer)
    assert bound.binding.provider == "approved-provider"
    assert bound.binding.model == "approved-model"
    assert bound.produce_case(_case()).usage == _usage()


def test_binding_rejects_wrong_provider_usage_before_worker_evaluation(tmp_path: Path) -> None:
    consent = _consent(tmp_path)
    bound = bind_provider_shadow_producer(
        plan=_plan(tmp_path, consent), consent=consent,
        producer=_Producer(_usage(model="other-model")),
    )
    with pytest.raises(ContractError, match="differs from sealed plan"):
        bound.produce_case(_case())


def test_binding_rejects_deterministic_usage_and_unconfirmed_or_synthetic_plans(tmp_path: Path) -> None:
    consent = _consent(tmp_path)
    plan = _plan(tmp_path, consent)
    bound = bind_provider_shadow_producer(
        plan=plan, consent=consent, producer=_Producer(ProviderUsage.deterministic())
    )
    with pytest.raises(ContractError, match="deterministic usage"):
        bound.produce_case(_case())

    synthetic = replace_plan(plan, dataset_kind="synthetic_fixture")
    with pytest.raises(ContractError):
        bind_provider_shadow_producer(
            plan=synthetic, consent=consent, producer=_Producer(_usage())
        )


def replace_plan(plan: Mapping[str, Any], **changes: Any) -> dict[str, Any]:
    value = dict(plan)
    value.update(changes)
    return value
