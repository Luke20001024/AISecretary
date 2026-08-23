"""Deterministic R9 quality metrics with explicit denominators."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from memento_backend.domain.errors import ContractError


@dataclass(frozen=True)
class ShadowObservation:
    case_id: str
    expected_links: frozenset[str] = frozenset()
    predicted_links: frozenset[str] = frozenset()
    allowed_inferences: frozenset[str] = frozenset()
    predicted_inferences: frozenset[str] = frozenset()
    should_stop: bool = False
    did_stop: bool = False
    source_reference_checks: int = 0
    valid_source_references: int = 0
    self_traceability_checks: int = 0
    valid_self_traces: int = 0
    resource_opinion_checks: int = 0
    resource_as_user_errors: int = 0
    stale_resurrections: int = 0
    adapter_checks: int = 0
    adapter_passes: int = 0
    source_hash_checks: int = 0
    stable_source_hashes: int = 0
    provider_attempted: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    latency_ms: int = 0

    def validate(self) -> None:
        if type(self.case_id) is not str or not self.case_id.strip():
            raise ContractError("shadow observation case id is required")
        for field in (
            "expected_links", "predicted_links", "allowed_inferences", "predicted_inferences"
        ):
            values = getattr(self, field)
            if not isinstance(values, frozenset) or any(
                type(value) is not str or not value.strip() for value in values
            ):
                raise ContractError(f"shadow observation {field} must contain non-empty strings")
        if any(type(value) is not bool for value in (self.should_stop, self.did_stop, self.provider_attempted)):
            raise ContractError("shadow observation boolean fields must be strict booleans")
        for field in (
            "source_reference_checks", "valid_source_references", "self_traceability_checks",
            "valid_self_traces", "resource_opinion_checks", "resource_as_user_errors",
            "stale_resurrections", "adapter_checks", "adapter_passes", "source_hash_checks",
            "stable_source_hashes", "prompt_tokens", "completion_tokens", "latency_ms",
        ):
            value = getattr(self, field)
            if type(value) is not int or value < 0:
                raise ContractError(f"shadow observation {field} must be a non-negative integer")
        if (
            type(self.estimated_cost_usd) not in {int, float}
            or not math.isfinite(float(self.estimated_cost_usd))
            or self.estimated_cost_usd < 0
        ):
            raise ContractError("shadow observation cost cannot be negative")
        if not self.provider_attempted and any(
            (
                self.prompt_tokens,
                self.completion_tokens,
                self.estimated_cost_usd,
                self.latency_ms,
            )
        ):
            raise ContractError("provider usage requires an attempted provider call", kind="evidence")
        pairs = (
            (self.valid_source_references, self.source_reference_checks),
            (self.valid_self_traces, self.self_traceability_checks),
            (self.resource_as_user_errors, self.resource_opinion_checks),
            (self.adapter_passes, self.adapter_checks),
            (self.stable_source_hashes, self.source_hash_checks),
        )
        if any(numerator > denominator for numerator, denominator in pairs):
            raise ContractError("shadow observation count exceeds its denominator")


def aggregate_shadow_metrics(observations: Sequence[ShadowObservation]) -> dict[str, Any]:
    if not observations:
        raise ContractError("shadow evaluation requires at least one observation")
    ids = [item.case_id for item in observations]
    if len(set(ids)) != len(ids):
        raise ContractError("shadow observation case ids must be unique")
    for item in observations:
        item.validate()

    false_links = sum(len(item.predicted_links - item.expected_links) for item in observations)
    predicted_links = sum(len(item.predicted_links) for item in observations)
    missed_links = sum(len(item.expected_links - item.predicted_links) for item in observations)
    expected_links = sum(len(item.expected_links) for item in observations)
    over_inferences = sum(
        len(item.predicted_inferences - item.allowed_inferences) for item in observations
    )
    predicted_inferences = sum(len(item.predicted_inferences) for item in observations)
    stop_tp = sum(item.should_stop and item.did_stop for item in observations)
    stop_fp = sum((not item.should_stop) and item.did_stop for item in observations)
    stop_fn = sum(item.should_stop and (not item.did_stop) for item in observations)
    stop_cases = sum(item.should_stop for item in observations)
    stop_precision = _rate(stop_tp, stop_tp + stop_fp)
    stop_recall = _rate(stop_tp, stop_tp + stop_fn)
    stop_f1 = _f1(stop_precision, stop_recall)
    latencies = sorted(item.latency_ms for item in observations)

    counts = {
        "case_count": len(observations),
        "expected_link_count": expected_links,
        "predicted_link_count": predicted_links,
        "false_link_count": false_links,
        "missed_link_count": missed_links,
        "predicted_inference_count": predicted_inferences,
        "over_inference_count": over_inferences,
        "stop_case_count": stop_cases,
        "stop_true_positive": stop_tp,
        "stop_false_positive": stop_fp,
        "stop_false_negative": stop_fn,
        "source_reference_checks": sum(item.source_reference_checks for item in observations),
        "valid_source_references": sum(item.valid_source_references for item in observations),
        "self_traceability_checks": sum(item.self_traceability_checks for item in observations),
        "valid_self_traces": sum(item.valid_self_traces for item in observations),
        "resource_opinion_checks": sum(item.resource_opinion_checks for item in observations),
        "resource_as_user_errors": sum(item.resource_as_user_errors for item in observations),
        "stale_resurrection_count": sum(item.stale_resurrections for item in observations),
        "adapter_checks": sum(item.adapter_checks for item in observations),
        "adapter_passes": sum(item.adapter_passes for item in observations),
        "source_hash_checks": sum(item.source_hash_checks for item in observations),
        "stable_source_hashes": sum(item.stable_source_hashes for item in observations),
        "provider_attempt_case_count": sum(item.provider_attempted for item in observations),
    }
    rates = {
        "false_link_rate": _rate(false_links, predicted_links),
        "missed_link_rate": _rate(missed_links, expected_links),
        "over_inference_rate": _rate(over_inferences, predicted_inferences),
        "stop_precision": stop_precision,
        "stop_recall": stop_recall,
        "stop_f1": stop_f1,
        "source_reference_valid_rate": _rate(counts["valid_source_references"], counts["source_reference_checks"]),
        "self_traceability_rate": _rate(counts["valid_self_traces"], counts["self_traceability_checks"]),
        "resource_as_user_false_rate": _rate(counts["resource_as_user_errors"], counts["resource_opinion_checks"]),
        "adapter_pass_rate": _rate(counts["adapter_passes"], counts["adapter_checks"]),
        "source_hash_stability_rate": _rate(counts["stable_source_hashes"], counts["source_hash_checks"]),
    }
    usage = {
        "prompt_tokens": sum(item.prompt_tokens for item in observations),
        "completion_tokens": sum(item.completion_tokens for item in observations),
        "total_tokens": sum(item.prompt_tokens + item.completion_tokens for item in observations),
        "estimated_cost_usd": round(sum(item.estimated_cost_usd for item in observations), 8),
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "latency_max_ms": max(latencies),
    }
    return {"counts": counts, "rates": rates, "usage": usage}


def evaluate_shadow_gates(
    metrics: Mapping[str, Any], thresholds: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], bool]:
    rates = metrics["rates"]
    counts = metrics["counts"]
    usage = metrics["usage"]
    checks = (
        ("false_link_rate", rates["false_link_rate"], thresholds["false_link_rate_max"], "max"),
        ("missed_link_rate", rates["missed_link_rate"], thresholds["missed_link_rate_max"], "max"),
        ("over_inference_rate", rates["over_inference_rate"], thresholds["over_inference_rate_max"], "max"),
        ("stop_f1", rates["stop_f1"], thresholds["stop_f1_min"], "min"),
        ("stop_case_count", counts["stop_case_count"], thresholds["stop_case_count_min"], "min"),
        ("source_reference_valid_rate", rates["source_reference_valid_rate"], thresholds["source_reference_valid_rate_min"], "min"),
        ("self_traceability_rate", rates["self_traceability_rate"], thresholds["self_traceability_rate_min"], "min"),
        ("resource_as_user_false_rate", rates["resource_as_user_false_rate"], thresholds["resource_as_user_false_rate_max"], "max"),
        ("stale_resurrection_count", counts["stale_resurrection_count"], thresholds["stale_resurrection_count_max"], "max"),
        ("adapter_pass_rate", rates["adapter_pass_rate"], thresholds["adapter_pass_rate_min"], "min"),
        ("source_hash_stability_rate", rates["source_hash_stability_rate"], thresholds["source_hash_stability_rate_min"], "min"),
        ("estimated_cost_usd", usage["estimated_cost_usd"], thresholds["estimated_cost_usd_max"], "max"),
        ("latency_p95_ms", usage["latency_p95_ms"], thresholds["latency_p95_ms_max"], "max"),
    )
    results = []
    for name, actual, threshold, direction in checks:
        if actual is None:
            status = "not_evaluated"
        elif direction == "min":
            status = "passed" if actual >= threshold else "failed"
        else:
            status = "passed" if actual <= threshold else "failed"
        results.append({
            "metric": name,
            "direction": direction,
            "actual": actual,
            "threshold": threshold,
            "status": status,
        })
    return results, all(item["status"] == "passed" for item in results)


def _rate(numerator: int, denominator: int) -> Optional[float]:
    return None if denominator == 0 else round(numerator / denominator, 8)


def _f1(precision: Optional[float], recall: Optional[float]) -> Optional[float]:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 8)


def _percentile(values: Sequence[int], quantile: float) -> int:
    if not values:
        return 0
    index = max(0, min(len(values) - 1, int((len(values) - 1) * quantile + 0.5)))
    return values[index]
