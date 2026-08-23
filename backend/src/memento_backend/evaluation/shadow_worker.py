"""Bounded R9 worker that keeps gold labels outside the producer boundary."""

from __future__ import annotations

import datetime as dt
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence, Tuple, runtime_checkable

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import (
    sha256_bytes,
    sha256_json,
    validate_datetime,
    validate_relative_path,
)
from memento_backend.projections.bundle_projector import (
    ProjectionBundle,
    validate_projection_bundle_contract,
)
from memento_backend.providers.protocol import ProviderUsage

from .shadow_metrics import ShadowObservation
from .shadow_snapshot import ReadOnlySnapshot, verify_read_only_snapshot


CHECK_FIELDS = (
    "source_reference_checks",
    "self_traceability_checks",
    "resource_opinion_checks",
    "adapter_checks",
    "source_hash_checks",
)


@dataclass(frozen=True)
class ShadowCaseInput:
    """The complete producer view. Gold labels are deliberately absent."""

    case_id: str
    files: Mapping[str, bytes]
    checks: Mapping[str, int]


@dataclass(frozen=True)
class ShadowCasePrediction:
    case_id: str
    predicted_links: frozenset[str]
    predicted_inferences: frozenset[str]
    did_stop: bool
    valid_source_references: int
    valid_self_traces: int
    resource_as_user_errors: int
    stale_resurrections: int
    adapter_passes: int
    stable_source_hashes: int
    usage: ProviderUsage


@runtime_checkable
class ShadowProducer(Protocol):
    def produce_case(self, case: ShadowCaseInput) -> ShadowCasePrediction:
        """Produce one prediction from only the bounded input view."""

    def candidate_bundle(self) -> ProjectionBundle:
        """Return the projection bundle produced during this execution."""


@dataclass(frozen=True)
class ShadowExecutionResult:
    work_product: Mapping[str, Any]
    observations: Tuple[ShadowObservation, ...]
    candidate_bundle: ProjectionBundle


def build_shadow_case_set(
    *,
    snapshot: ReadOnlySnapshot,
    created_at: str,
    cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Freeze gold labels and exact snapshot inputs before producer execution."""

    validate_datetime(created_at, "created_at")
    verified = _verified_snapshot(snapshot)
    normalized_cases = [_normalize_case_spec(case, verified) for case in cases]
    normalized_cases.sort(key=lambda item: str(item["case_id"]))
    base = {
        "snapshot_ref": _snapshot_ref(verified),
        "created_at": created_at,
        "cases": normalized_cases,
    }
    value = {
        "schema_version": "1.0",
        "kind": "memento_shadow_case_set",
        "case_set_id": "shcs_" + sha256_json(base)[:24],
        **base,
    }
    validate_shadow_case_set(value, verified)
    return value


def validate_shadow_case_set(
    case_set: Mapping[str, Any], snapshot: ReadOnlySnapshot
) -> None:
    validate_contract("shadow-case-set-v1.schema.json", case_set)
    verified = _verified_snapshot(snapshot)
    if dict(case_set["snapshot_ref"]) != _snapshot_ref(verified):
        raise ContractError("shadow case set is bound to another snapshot", kind="conflict")
    validate_datetime(case_set["created_at"], "created_at")
    if _timestamp(case_set["created_at"]) < _timestamp(verified.manifest["created_at"]):
        raise ContractError("shadow case set predates its snapshot", kind="conflict")
    cases = list(case_set["cases"])
    case_ids = [str(case["case_id"]) for case in cases]
    if case_ids != sorted(case_ids) or len(set(case_ids)) != len(case_ids):
        raise ContractError("shadow case ids must be unique and sorted", kind="evidence")
    manifest_entries = {
        str(entry["path"]): dict(entry) for entry in verified.manifest["files"]
    }
    for case in cases:
        inputs = list(case["inputs"])
        paths = [str(entry["path"]) for entry in inputs]
        if paths != sorted(paths) or len(set(paths)) != len(paths):
            raise ContractError("shadow case inputs must be unique and sorted", kind="evidence")
        for entry in inputs:
            validate_relative_path(entry["path"])
            if manifest_entries.get(str(entry["path"])) != dict(entry):
                raise ContractError("shadow case input differs from snapshot manifest", kind="evidence")
        for field in ("expected_links", "allowed_inferences"):
            labels = list(case[field])
            if labels != sorted(labels):
                raise ContractError(f"shadow case {field} must be sorted", kind="evidence")
        _validate_check_counts(case["checks"])
    base = {
        "snapshot_ref": dict(case_set["snapshot_ref"]),
        "created_at": case_set["created_at"],
        "cases": cases,
    }
    if case_set["case_set_id"] != "shcs_" + sha256_json(base)[:24]:
        raise ContractError("shadow case set identity is inconsistent", kind="evidence")


def shadow_case_set_ref(case_set: Mapping[str, Any]) -> dict[str, str]:
    return {
        "case_set_id": str(case_set["case_set_id"]),
        "case_set_sha256": sha256_json(case_set),
    }


def execute_shadow_cases(
    *,
    plan: Mapping[str, Any],
    snapshot: ReadOnlySnapshot,
    case_set: Mapping[str, Any],
    producer: ShadowProducer,
    started_at: str,
    finished_at: str,
) -> ShadowExecutionResult:
    """Run one pre-registered case set without exposing gold to the producer."""

    validate_contract("shadow-plan-v1.schema.json", plan)
    validate_datetime(started_at, "started_at")
    validate_datetime(finished_at, "finished_at")
    verified = _verified_snapshot(snapshot)
    validate_shadow_case_set(case_set, verified)
    _validate_plan_binding(plan, verified, case_set)
    if not isinstance(producer, ShadowProducer):
        raise ContractError("shadow producer does not implement the required boundary")
    case_created = _timestamp(case_set["created_at"])
    plan_created = _timestamp(plan["created_at"])
    started = _timestamp(started_at)
    finished = _timestamp(finished_at)
    if case_created > plan_created or plan_created > started or started > finished:
        raise ContractError("shadow worker chronology is inconsistent", kind="conflict")

    predictions = []
    observations = []
    cumulative = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
    for case in case_set["cases"]:
        input_files = {
            str(entry["path"]): _read_verified_file(verified.path, entry)
            for entry in case["inputs"]
        }
        producer_input = ShadowCaseInput(
            case_id=str(case["case_id"]),
            files=MappingProxyType(input_files),
            checks=MappingProxyType({field: int(case["checks"][field]) for field in CHECK_FIELDS}),
        )
        prediction = producer.produce_case(producer_input)
        _validate_prediction(prediction, case, plan)
        usage = prediction.usage.to_dict()
        cumulative["prompt_tokens"] += int(usage["prompt_tokens"])
        cumulative["completion_tokens"] += int(usage["completion_tokens"])
        cumulative["cost"] = round(
            float(cumulative["cost"]) + float(usage["estimated_cost_usd"]), 8
        )
        _enforce_incremental_budget(plan, cumulative, int(usage["latency_ms"]))
        prediction_value = _prediction_value(prediction)
        predictions.append(prediction_value)
        observations.append(_join_observation(case, prediction))

    candidate = producer.candidate_bundle()
    validate_projection_bundle_contract(candidate)
    base = {
        "plan_ref": {
            "plan_id": str(plan["plan_id"]),
            "plan_sha256": sha256_json(plan),
        },
        "case_set_ref": shadow_case_set_ref(case_set),
        "snapshot_ref": _snapshot_ref(verified),
        "producer": {
            "execution_mode": plan["execution_mode"],
            "provider": plan["provider"]["provider"],
            "model": plan["provider"]["model"],
            "prompt_versions": list(plan["provider"]["prompt_versions"]),
            "policy_versions": list(plan["provider"]["policy_versions"]),
        },
        "started_at": started_at,
        "finished_at": finished_at,
        "predictions": predictions,
        "candidate_bundle_sha256": candidate.bundle_sha256,
    }
    work_product = {
        "schema_version": "1.0",
        "kind": "memento_shadow_work_product",
        "work_product_id": "shw_" + sha256_json(base)[:24],
        **base,
    }
    validate_shadow_work_product(
        work_product=work_product,
        plan=plan,
        case_set=case_set,
        snapshot=verified,
        candidate_bundle=candidate,
    )
    return ShadowExecutionResult(
        work_product=work_product,
        observations=tuple(observations),
        candidate_bundle=candidate,
    )


def validate_shadow_work_product(
    *,
    work_product: Mapping[str, Any],
    plan: Mapping[str, Any],
    case_set: Mapping[str, Any],
    snapshot: ReadOnlySnapshot,
    candidate_bundle: ProjectionBundle,
) -> Tuple[ShadowObservation, ...]:
    validate_contract("shadow-work-product-v1.schema.json", work_product)
    verified = _verified_snapshot(snapshot)
    validate_shadow_case_set(case_set, verified)
    validate_projection_bundle_contract(candidate_bundle)
    _validate_plan_binding(plan, verified, case_set)
    if dict(work_product["plan_ref"]) != {
        "plan_id": str(plan["plan_id"]),
        "plan_sha256": sha256_json(plan),
    }:
        raise ContractError("shadow work product plan reference is inconsistent", kind="evidence")
    if dict(work_product["case_set_ref"]) != shadow_case_set_ref(case_set):
        raise ContractError("shadow work product case set reference is inconsistent", kind="evidence")
    if dict(work_product["snapshot_ref"]) != _snapshot_ref(verified):
        raise ContractError("shadow work product snapshot reference is inconsistent", kind="evidence")
    expected_producer = {
        "execution_mode": plan["execution_mode"],
        "provider": plan["provider"]["provider"],
        "model": plan["provider"]["model"],
        "prompt_versions": list(plan["provider"]["prompt_versions"]),
        "policy_versions": list(plan["provider"]["policy_versions"]),
    }
    if dict(work_product["producer"]) != expected_producer:
        raise ContractError("shadow work product producer policy is inconsistent", kind="evidence")
    if work_product["candidate_bundle_sha256"] != candidate_bundle.bundle_sha256:
        raise ContractError("shadow work product candidate hash is inconsistent", kind="evidence")
    started = _timestamp(work_product["started_at"])
    finished = _timestamp(work_product["finished_at"])
    if _timestamp(plan["created_at"]) > started or started > finished:
        raise ContractError("shadow work product chronology is inconsistent", kind="conflict")
    cases = {str(case["case_id"]): case for case in case_set["cases"]}
    predictions = list(work_product["predictions"])
    prediction_ids = [str(item["case_id"]) for item in predictions]
    if prediction_ids != sorted(prediction_ids) or set(prediction_ids) != set(cases):
        raise ContractError("shadow work product must cover each case once in sorted order", kind="evidence")
    observations = []
    cumulative = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
    for value in predictions:
        prediction = _prediction_from_value(value)
        case = cases[prediction.case_id]
        _validate_prediction(prediction, case, plan)
        usage = prediction.usage.to_dict()
        cumulative["prompt_tokens"] += int(usage["prompt_tokens"])
        cumulative["completion_tokens"] += int(usage["completion_tokens"])
        cumulative["cost"] = round(
            float(cumulative["cost"]) + float(usage["estimated_cost_usd"]), 8
        )
        _enforce_incremental_budget(plan, cumulative, int(usage["latency_ms"]))
        observations.append(_join_observation(case, prediction))
    base = {
        key: work_product[key]
        for key in (
            "plan_ref", "case_set_ref", "snapshot_ref", "producer", "started_at",
            "finished_at", "predictions", "candidate_bundle_sha256",
        )
    }
    if work_product["work_product_id"] != "shw_" + sha256_json(base)[:24]:
        raise ContractError("shadow work product identity is inconsistent", kind="evidence")
    return tuple(observations)


def _normalize_case_spec(
    case: Mapping[str, Any], snapshot: ReadOnlySnapshot
) -> dict[str, Any]:
    allowed = {
        "case_id", "input_paths", "expected_links", "allowed_inferences",
        "should_stop", "checks",
    }
    if set(case) != allowed:
        raise ContractError("shadow case specification fields are invalid")
    case_id = case["case_id"]
    if type(case_id) is not str or not case_id:
        raise ContractError("shadow case id is required")
    if not isinstance(case["input_paths"], (list, tuple)):
        raise ContractError("shadow case input_paths must be a sequence")
    input_paths = list(case["input_paths"])
    if not input_paths or any(type(path) is not str for path in input_paths):
        raise ContractError("shadow case requires at least one input")
    manifest_entries = {
        str(entry["path"]): dict(entry) for entry in snapshot.manifest["files"]
    }
    normalized_inputs = []
    for path in sorted(input_paths):
        validate_relative_path(path)
        entry = manifest_entries.get(path)
        if entry is None:
            raise ContractError("shadow case input is absent from snapshot", kind="evidence")
        normalized_inputs.append(entry)
    if len({entry["path"] for entry in normalized_inputs}) != len(normalized_inputs):
        raise ContractError("shadow case input paths must be unique")
    if not isinstance(case["checks"], Mapping):
        raise ContractError("shadow case checks must be an object")
    if set(case["checks"]) != set(CHECK_FIELDS):
        raise ContractError("shadow case check fields are invalid")
    checks = {field: case["checks"][field] for field in CHECK_FIELDS}
    _validate_check_counts(checks)
    if type(case["should_stop"]) is not bool:
        raise ContractError("shadow case should_stop must be a strict boolean")
    return {
        "case_id": case_id,
        "inputs": normalized_inputs,
        "expected_links": _sorted_labels(case["expected_links"], "expected_links"),
        "allowed_inferences": _sorted_labels(case["allowed_inferences"], "allowed_inferences"),
        "should_stop": case["should_stop"],
        "checks": checks,
    }


def _validate_plan_binding(
    plan: Mapping[str, Any], snapshot: ReadOnlySnapshot, case_set: Mapping[str, Any]
) -> None:
    if plan["snapshot_id"] != snapshot.manifest["snapshot_id"]:
        raise ContractError("shadow worker plan is bound to another snapshot", kind="conflict")
    if plan["dataset_kind"] != snapshot.manifest["snapshot_kind"]:
        raise ContractError("shadow worker dataset kind differs from snapshot", kind="conflict")
    plan_case_ref = plan.get("case_set_ref")
    if plan_case_ref is not None and dict(plan_case_ref) != shadow_case_set_ref(case_set):
        raise ContractError("shadow worker plan is bound to another case set", kind="conflict")


def _validate_prediction(
    prediction: ShadowCasePrediction, case: Mapping[str, Any], plan: Mapping[str, Any]
) -> None:
    if prediction.case_id != case["case_id"]:
        raise ContractError("shadow producer returned another case id", kind="evidence")
    for name in ("predicted_links", "predicted_inferences"):
        values = getattr(prediction, name)
        if not isinstance(values, frozenset) or any(
            type(item) is not str or not item for item in values
        ):
            raise ContractError(f"shadow prediction {name} is invalid")
    if type(prediction.did_stop) is not bool:
        raise ContractError("shadow prediction did_stop must be a strict boolean")
    count_pairs = (
        ("valid_source_references", "source_reference_checks"),
        ("valid_self_traces", "self_traceability_checks"),
        ("resource_as_user_errors", "resource_opinion_checks"),
        ("adapter_passes", "adapter_checks"),
        ("stable_source_hashes", "source_hash_checks"),
    )
    for value_name, check_name in count_pairs:
        value = getattr(prediction, value_name)
        denominator = case["checks"][check_name]
        if type(value) is not int or value < 0 or value > denominator:
            raise ContractError(f"shadow prediction {value_name} exceeds its case contract")
    if type(prediction.stale_resurrections) is not int or prediction.stale_resurrections < 0:
        raise ContractError("shadow prediction stale_resurrections is invalid")
    usage = prediction.usage.to_dict()
    if plan["execution_mode"] == "deterministic_zero":
        if usage != ProviderUsage.deterministic().to_dict():
            raise ContractError("deterministic shadow producer recorded provider usage", kind="budget")
    elif (
        usage["mode"] != "provider"
        or usage["provider"] != plan["provider"]["provider"]
        or usage["model"] != plan["provider"]["model"]
    ):
        raise ContractError("shadow prediction provider differs from plan", kind="evidence")


def _prediction_value(prediction: ShadowCasePrediction) -> dict[str, Any]:
    return {
        "case_id": prediction.case_id,
        "predicted_links": sorted(prediction.predicted_links),
        "predicted_inferences": sorted(prediction.predicted_inferences),
        "did_stop": prediction.did_stop,
        "valid_source_references": prediction.valid_source_references,
        "valid_self_traces": prediction.valid_self_traces,
        "resource_as_user_errors": prediction.resource_as_user_errors,
        "stale_resurrections": prediction.stale_resurrections,
        "adapter_passes": prediction.adapter_passes,
        "stable_source_hashes": prediction.stable_source_hashes,
        "usage": prediction.usage.to_dict(),
    }


def _prediction_from_value(value: Mapping[str, Any]) -> ShadowCasePrediction:
    usage = value["usage"]
    return ShadowCasePrediction(
        case_id=str(value["case_id"]),
        predicted_links=frozenset(str(item) for item in value["predicted_links"]),
        predicted_inferences=frozenset(str(item) for item in value["predicted_inferences"]),
        did_stop=value["did_stop"],
        valid_source_references=value["valid_source_references"],
        valid_self_traces=value["valid_self_traces"],
        resource_as_user_errors=value["resource_as_user_errors"],
        stale_resurrections=value["stale_resurrections"],
        adapter_passes=value["adapter_passes"],
        stable_source_hashes=value["stable_source_hashes"],
        usage=ProviderUsage(
            mode=usage["mode"],
            provider=usage["provider"],
            model=usage["model"],
            attempt_status=usage["attempt_status"],
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            total_tokens=usage["total_tokens"],
            estimated_cost_usd=usage["estimated_cost_usd"],
            latency_ms=usage["latency_ms"],
        ),
    )


def _join_observation(
    case: Mapping[str, Any], prediction: ShadowCasePrediction
) -> ShadowObservation:
    usage = prediction.usage.to_dict()
    return ShadowObservation(
        case_id=prediction.case_id,
        expected_links=frozenset(str(item) for item in case["expected_links"]),
        predicted_links=prediction.predicted_links,
        allowed_inferences=frozenset(str(item) for item in case["allowed_inferences"]),
        predicted_inferences=prediction.predicted_inferences,
        should_stop=case["should_stop"],
        did_stop=prediction.did_stop,
        source_reference_checks=case["checks"]["source_reference_checks"],
        valid_source_references=prediction.valid_source_references,
        self_traceability_checks=case["checks"]["self_traceability_checks"],
        valid_self_traces=prediction.valid_self_traces,
        resource_opinion_checks=case["checks"]["resource_opinion_checks"],
        resource_as_user_errors=prediction.resource_as_user_errors,
        stale_resurrections=prediction.stale_resurrections,
        adapter_checks=case["checks"]["adapter_checks"],
        adapter_passes=prediction.adapter_passes,
        source_hash_checks=case["checks"]["source_hash_checks"],
        stable_source_hashes=prediction.stable_source_hashes,
        provider_attempted=usage["mode"] == "provider",
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        estimated_cost_usd=usage["estimated_cost_usd"],
        latency_ms=usage["latency_ms"],
    )


def _enforce_incremental_budget(
    plan: Mapping[str, Any], cumulative: Mapping[str, Any], latency_ms: int
) -> None:
    budget = plan["provider"]["budget"]
    values = (
        (cumulative["prompt_tokens"], budget["max_prompt_tokens"], "prompt token"),
        (cumulative["completion_tokens"], budget["max_completion_tokens"], "completion token"),
        (cumulative["cost"], budget["max_cost_usd"], "cost"),
        (latency_ms, budget["max_latency_ms"], "latency"),
    )
    for actual, maximum, label in values:
        if actual > maximum:
            raise ContractError(f"shadow {label} budget exceeded", kind="budget")


def _verified_snapshot(snapshot: ReadOnlySnapshot) -> ReadOnlySnapshot:
    verified = verify_read_only_snapshot(snapshot.path)
    if dict(verified.manifest) != dict(snapshot.manifest):
        raise ContractError("shadow snapshot changed after binding", kind="evidence")
    return verified


def _snapshot_ref(snapshot: ReadOnlySnapshot) -> dict[str, str]:
    return {
        "snapshot_id": str(snapshot.manifest["snapshot_id"]),
        "snapshot_sha256": sha256_json(snapshot.manifest),
    }


def _read_verified_file(root: Path, entry: Mapping[str, Any]) -> bytes:
    relative = validate_relative_path(entry["path"])
    path = root.joinpath(*relative.split("/"))
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ContractError("shadow case input escapes snapshot", kind="path") from exc
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ContractError("shadow case input must be a regular file", kind="path")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read()
    finally:
        os.close(descriptor)
    if len(data) != entry["byte_size"] or sha256_bytes(data) != entry["sha256"]:
        raise ContractError("shadow case input hash mismatch", kind="evidence")
    return data


def _validate_check_counts(checks: Mapping[str, Any]) -> None:
    if set(checks) != set(CHECK_FIELDS):
        raise ContractError("shadow case check fields are invalid")
    for field in CHECK_FIELDS:
        value = checks[field]
        if type(value) is not int or value < 0:
            raise ContractError(f"shadow case {field} must be a non-negative integer")


def _sorted_labels(values: Any, name: str) -> list[str]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ContractError(f"shadow case {name} must be a sequence")
    labels = list(values)
    if any(type(item) is not str or not item for item in labels) or len(set(labels)) != len(labels):
        raise ContractError(f"shadow case {name} must contain unique non-empty strings")
    return sorted(labels)


def _timestamp(value: Any) -> dt.datetime:
    validate_datetime(value)
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
