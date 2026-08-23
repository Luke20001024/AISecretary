from __future__ import annotations

import math

import pytest

from memento_backend.domain.errors import ContractError
from memento_backend.evaluation.shadow_metrics import (
    ShadowObservation,
    aggregate_shadow_metrics,
    evaluate_shadow_gates,
)


def thresholds() -> dict[str, float | int]:
    return {
        "false_link_rate_max": 0.25,
        "missed_link_rate_max": 0.25,
        "over_inference_rate_max": 0.25,
        "stop_f1_min": 0.8,
        "stop_case_count_min": 1,
        "source_reference_valid_rate_min": 1.0,
        "self_traceability_rate_min": 1.0,
        "resource_as_user_false_rate_max": 0.0,
        "stale_resurrection_count_max": 0,
        "adapter_pass_rate_min": 1.0,
        "source_hash_stability_rate_min": 1.0,
        "estimated_cost_usd_max": 0.1,
        "latency_p95_ms_max": 100,
    }


def test_metrics_keep_false_links_misses_over_inference_and_stop_quality_separate() -> None:
    observations = [
        ShadowObservation(
            case_id="one",
            expected_links=frozenset({"a", "b"}),
            predicted_links=frozenset({"a", "x"}),
            allowed_inferences=frozenset({"safe"}),
            predicted_inferences=frozenset({"safe", "extra"}),
            should_stop=True,
            did_stop=True,
            source_reference_checks=2,
            valid_source_references=2,
            self_traceability_checks=1,
            valid_self_traces=1,
            resource_opinion_checks=1,
            adapter_checks=1,
            adapter_passes=1,
            source_hash_checks=1,
            stable_source_hashes=1,
            provider_attempted=True,
            latency_ms=20,
        ),
        ShadowObservation(
            case_id="two",
            should_stop=False,
            did_stop=False,
            provider_attempted=True,
            latency_ms=40,
        ),
    ]
    metrics = aggregate_shadow_metrics(observations)
    assert metrics["rates"]["false_link_rate"] == 0.5
    assert metrics["rates"]["missed_link_rate"] == 0.5
    assert metrics["rates"]["over_inference_rate"] == 0.5
    assert metrics["rates"]["stop_f1"] == 1.0
    gates, passed = evaluate_shadow_gates(metrics, thresholds())
    assert not passed
    assert {item["metric"] for item in gates if item["status"] == "failed"} == {
        "false_link_rate", "missed_link_rate", "over_inference_rate"
    }


def test_missing_denominator_is_not_reported_as_a_pass() -> None:
    metrics = aggregate_shadow_metrics([ShadowObservation(case_id="empty")])
    gates, passed = evaluate_shadow_gates(metrics, thresholds())
    assert not passed
    assert any(item["status"] == "not_evaluated" for item in gates)


def test_observation_rejects_impossible_counts_and_duplicate_case_ids() -> None:
    with pytest.raises(ContractError, match="exceeds"):
        aggregate_shadow_metrics([
            ShadowObservation(case_id="bad", source_reference_checks=1, valid_source_references=2)
        ])
    with pytest.raises(ContractError, match="unique"):
        aggregate_shadow_metrics([
            ShadowObservation(case_id="same"), ShadowObservation(case_id="same")
        ])


def test_observation_rejects_unbound_usage_invalid_sets_and_non_finite_cost() -> None:
    with pytest.raises(ContractError, match="attempted provider"):
        aggregate_shadow_metrics([ShadowObservation(case_id="usage", prompt_tokens=1)])
    with pytest.raises(ContractError, match="non-empty strings"):
        aggregate_shadow_metrics([
            ShadowObservation(case_id="sets", expected_links=frozenset({"", "valid"}))
        ])
    with pytest.raises(ContractError, match="cost"):
        aggregate_shadow_metrics([
            ShadowObservation(case_id="nan", provider_attempted=True, estimated_cost_usd=math.nan)
        ])
