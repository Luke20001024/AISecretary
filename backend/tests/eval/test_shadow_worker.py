from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
from typing import Any, Mapping

import pytest

from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import sha256_json
from memento_backend.evaluation.shadow_runner import build_shadow_plan
from memento_backend.evaluation.shadow_snapshot import create_read_only_snapshot
from memento_backend.evaluation.shadow_worker import (
    ShadowCaseInput,
    ShadowCasePrediction,
    build_shadow_case_set,
    execute_shadow_cases,
    shadow_case_set_ref,
    validate_shadow_case_set,
    validate_shadow_work_product,
)
from memento_backend.projections.bundle_projector import ProjectionBundle, build_projection_bundle
from memento_backend.providers.protocol import ProviderUsage

from tests.fixtures.formal_20d import formal_20d_inputs


def _thresholds() -> dict[str, float | int]:
    return {
        "false_link_rate_max": 0.0,
        "missed_link_rate_max": 0.0,
        "over_inference_rate_max": 0.0,
        "stop_f1_min": 1.0,
        "stop_case_count_min": 1,
        "source_reference_valid_rate_min": 1.0,
        "self_traceability_rate_min": 1.0,
        "resource_as_user_false_rate_max": 0.0,
        "stale_resurrection_count_max": 0,
        "adapter_pass_rate_min": 1.0,
        "source_hash_stability_rate_min": 1.0,
        "estimated_cost_usd_max": 0.0,
        "latency_p95_ms_max": 0,
    }


def _snapshot(tmp_path: Path) -> Any:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.md").write_text("confirm boundary", encoding="utf-8")
    (source / "b.md").write_text("keep the reason", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    output.chmod(0o700)
    return create_read_only_snapshot(
        source,
        output,
        source_label="synthetic-worker-fixture",
        snapshot_kind="synthetic_fixture",
        created_at="2026-08-23T10:00:00+08:00",
    )


def _case_specs() -> list[dict[str, Any]]:
    return [{
        "case_id": "boundary-case",
        "input_paths": ["a.md", "b.md"],
        "expected_links": ["theme:boundary"],
        "allowed_inferences": ["insight:confirm-first"],
        "should_stop": True,
        "checks": {
            "source_reference_checks": 2,
            "self_traceability_checks": 1,
            "resource_opinion_checks": 1,
            "adapter_checks": 1,
            "source_hash_checks": 2,
        },
    }]


def _bundle() -> ProjectionBundle:
    return build_projection_bundle(
        formal_20d_inputs(),
        as_of="2026-08-18",
        generated_at="2026-08-23T10:02:00+08:00",
    )


class _DeterministicProducer:
    def __init__(self, bundle: ProjectionBundle) -> None:
        self._bundle = bundle
        self.views: list[ShadowCaseInput] = []

    def produce_case(self, case: ShadowCaseInput) -> ShadowCasePrediction:
        self.views.append(case)
        assert set(case.files) == {"a.md", "b.md"}
        assert not hasattr(case, "expected_links")
        assert not hasattr(case, "allowed_inferences")
        assert not hasattr(case, "should_stop")
        return ShadowCasePrediction(
            case_id=case.case_id,
            predicted_links=frozenset({"theme:boundary"}),
            predicted_inferences=frozenset({"insight:confirm-first"}),
            did_stop=True,
            valid_source_references=2,
            valid_self_traces=1,
            resource_as_user_errors=0,
            stale_resurrections=0,
            adapter_passes=1,
            stable_source_hashes=2,
            usage=ProviderUsage.deterministic(),
        )

    def candidate_bundle(self) -> ProjectionBundle:
        return self._bundle


class _ProviderProducer(_DeterministicProducer):
    def __init__(self, bundle: ProjectionBundle, usage: ProviderUsage) -> None:
        super().__init__(bundle)
        self._usage = usage

    def produce_case(self, case: ShadowCaseInput) -> ShadowCasePrediction:
        return replace(super().produce_case(case), usage=self._usage)


def _plan(snapshot: Any, case_set: Mapping[str, Any]) -> dict[str, Any]:
    return build_shadow_plan(
        snapshot_id=str(snapshot.manifest["snapshot_id"]),
        dataset_kind="synthetic_fixture",
        execution_mode="deterministic_zero",
        created_at="2026-08-23T10:01:00+08:00",
        thresholds=_thresholds(),
        user_confirmation_status="not_required",
        user_confirmation_ref=None,
        policy_versions=("shadow-policy-v1",),
        case_set_ref=shadow_case_set_ref(case_set),
    )


def test_worker_keeps_gold_outside_producer_and_builds_deterministic_evidence(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    case_set = build_shadow_case_set(
        snapshot=snapshot,
        created_at="2026-08-23T10:00:30+08:00",
        cases=_case_specs(),
    )
    plan = _plan(snapshot, case_set)
    producer = _DeterministicProducer(_bundle())
    first = execute_shadow_cases(
        plan=plan,
        snapshot=snapshot,
        case_set=case_set,
        producer=producer,
        started_at="2026-08-23T10:01:30+08:00",
        finished_at="2026-08-23T10:02:30+08:00",
    )
    second = execute_shadow_cases(
        plan=plan,
        snapshot=snapshot,
        case_set=case_set,
        producer=_DeterministicProducer(_bundle()),
        started_at="2026-08-23T10:01:30+08:00",
        finished_at="2026-08-23T10:02:30+08:00",
    )
    assert first.work_product == second.work_product
    assert first.observations == second.observations
    assert first.observations[0].expected_links == frozenset({"theme:boundary"})
    assert first.work_product["predictions"][0].get("expected_links") is None
    assert len(producer.views) == 1


def test_case_set_rejects_resealed_input_that_differs_from_snapshot(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    case_set = build_shadow_case_set(
        snapshot=snapshot,
        created_at="2026-08-23T10:00:30+08:00",
        cases=_case_specs(),
    )
    tampered = {**case_set, "cases": [dict(case_set["cases"][0])]}
    tampered["cases"][0]["inputs"] = [dict(item) for item in tampered["cases"][0]["inputs"]]
    tampered["cases"][0]["inputs"][0]["sha256"] = "f" * 64
    base = {
        "snapshot_ref": tampered["snapshot_ref"],
        "created_at": tampered["created_at"],
        "cases": tampered["cases"],
    }
    tampered["case_set_id"] = "shcs_" + sha256_json(base)[:24]
    with pytest.raises(ContractError, match="snapshot manifest"):
        validate_shadow_case_set(tampered, snapshot)


def test_work_product_rejects_resealed_prediction_count_above_gold_denominator(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    case_set = build_shadow_case_set(
        snapshot=snapshot,
        created_at="2026-08-23T10:00:30+08:00",
        cases=_case_specs(),
    )
    plan = _plan(snapshot, case_set)
    execution = execute_shadow_cases(
        plan=plan,
        snapshot=snapshot,
        case_set=case_set,
        producer=_DeterministicProducer(_bundle()),
        started_at="2026-08-23T10:01:30+08:00",
        finished_at="2026-08-23T10:02:30+08:00",
    )
    tampered = {**execution.work_product, "predictions": [dict(execution.work_product["predictions"][0])]}
    tampered["predictions"][0]["valid_self_traces"] = 2
    base = {
        key: tampered[key]
        for key in (
            "plan_ref", "case_set_ref", "snapshot_ref", "producer", "started_at",
            "finished_at", "predictions", "candidate_bundle_sha256",
        )
    }
    tampered["work_product_id"] = "shw_" + sha256_json(base)[:24]
    with pytest.raises(ContractError, match="valid_self_traces"):
        validate_shadow_work_product(
            work_product=tampered,
            plan=plan,
            case_set=case_set,
            snapshot=snapshot,
            candidate_bundle=execution.candidate_bundle,
        )


def test_worker_stops_when_provider_budget_is_exceeded(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    case_set = build_shadow_case_set(
        snapshot=snapshot,
        created_at="2026-08-23T10:00:30+08:00",
        cases=_case_specs(),
    )
    plan = build_shadow_plan(
        snapshot_id=str(snapshot.manifest["snapshot_id"]),
        dataset_kind="synthetic_fixture",
        execution_mode="provider_shadow",
        created_at="2026-08-23T10:01:00+08:00",
        thresholds=_thresholds(),
        user_confirmation_status="not_required",
        user_confirmation_ref=None,
        provider="provider-x",
        model="model-y",
        prompt_versions=("capture-v1",),
        policy_versions=("shadow-policy-v1",),
        max_prompt_tokens=5,
        max_completion_tokens=5,
        max_cost_usd=0.1,
        max_latency_ms=100,
        case_set_ref=shadow_case_set_ref(case_set),
    )
    usage = ProviderUsage(
        mode="provider",
        provider="provider-x",
        model="model-y",
        attempt_status="succeeded",
        prompt_tokens=6,
        completion_tokens=1,
        total_tokens=7,
        estimated_cost_usd=0.01,
        latency_ms=10,
    )
    with pytest.raises(ContractError, match="prompt token") as raised:
        execute_shadow_cases(
            plan=plan,
            snapshot=snapshot,
            case_set=case_set,
            producer=_ProviderProducer(_bundle(), usage),
            started_at="2026-08-23T10:01:30+08:00",
            finished_at="2026-08-23T10:02:30+08:00",
        )
    assert raised.value.kind == "budget"


def test_provider_usage_rejects_non_finite_cost() -> None:
    usage = ProviderUsage(
        mode="provider",
        provider="provider-x",
        model="model-y",
        attempt_status="succeeded",
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        estimated_cost_usd=math.nan,
        latency_ms=1,
    )
    with pytest.raises(ContractError, match="negative value"):
        usage.to_dict()
