"""Pre-registered shadow runs that can only publish isolated evaluation artefacts."""

from __future__ import annotations

import os
import math
import stat
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import (
    sha256_bytes,
    sha256_json,
    validate_datetime,
    validate_relative_path,
    validate_sha256,
)
from memento_backend.projections.bundle_projector import ProjectionBundle, validate_projection_bundle_contract
from memento_backend.storage.atomic import AtomicFileStore
from memento_backend.storage.bundle_store import BundleStore

from .shadow_metrics import ShadowObservation, aggregate_shadow_metrics, evaluate_shadow_gates
from .shadow_consent import consent_ref as build_consent_ref, validate_shadow_consent
from .shadow_snapshot import ReadOnlySnapshot, verify_read_only_snapshot
from .shadow_worker import (
    shadow_case_set_ref,
    validate_shadow_case_set,
    validate_shadow_work_product,
)


@dataclass(frozen=True)
class ShadowRunResult:
    report: Mapping[str, Any]
    run_root: Path


def build_shadow_plan(
    *,
    snapshot_id: str,
    dataset_kind: str,
    execution_mode: str,
    created_at: str,
    thresholds: Mapping[str, Any],
    user_confirmation_status: str,
    user_confirmation_ref: Optional[str],
    provider: Optional[str] = None,
    model: Optional[str] = None,
    prompt_versions: Sequence[str] = (),
    policy_versions: Sequence[str] = (),
    max_prompt_tokens: int = 0,
    max_completion_tokens: int = 0,
    max_cost_usd: float = 0.0,
    max_latency_ms: int = 0,
    consent: Optional[Mapping[str, Any]] = None,
    case_set_ref: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    validate_datetime(created_at, "created_at")
    base = {
        "snapshot_id": snapshot_id,
        "case_set_ref": None if case_set_ref is None else dict(case_set_ref),
        "dataset_kind": dataset_kind,
        "execution_mode": execution_mode,
        "write_mode": "shadow_only",
        "provider": {
            "provider": provider,
            "model": model,
            "prompt_versions": sorted(prompt_versions),
            "policy_versions": sorted(policy_versions),
            "budget": {
                "max_prompt_tokens": max_prompt_tokens,
                "max_completion_tokens": max_completion_tokens,
                "max_cost_usd": max_cost_usd,
                "max_latency_ms": max_latency_ms,
            },
        },
        "thresholds": dict(thresholds),
        "user_confirmation": {
            "status": user_confirmation_status,
            "confirmation_ref": user_confirmation_ref,
        },
        "consent_ref": None if consent is None else build_consent_ref(consent),
        "created_at": created_at,
    }
    value = {
        "schema_version": "1.0",
        "kind": "memento_shadow_plan",
        "plan_id": "shp_" + sha256_json(base)[:24],
        **base,
    }
    validate_contract("shadow-plan-v1.schema.json", value)
    _validate_shadow_plan_policy(value)
    _validate_plan_consent_binding(value, consent)
    return value


def validate_shadow_plan(
    plan: Mapping[str, Any], consent: Optional[Mapping[str, Any]] = None
) -> None:
    """Validate the complete sealed policy for one shadow execution.

    Keeping this public avoids a second, weaker provider-worker validation
    path.  A real Provider-backed worker must use the same consent binding as
    the snapshot and report publisher.
    """

    validate_contract("shadow-plan-v1.schema.json", plan)
    _validate_shadow_plan_policy(plan)
    _validate_plan_consent_binding(plan, consent)


def run_shadow_evaluation(
    *,
    plan: Mapping[str, Any],
    snapshot: ReadOnlySnapshot,
    observations: Sequence[ShadowObservation],
    output_root: Path,
    finished_at: str,
    candidate_bundle: Optional[ProjectionBundle] = None,
    baseline_bundle_sha256: Optional[str] = None,
    baseline_snapshot_path: Optional[str] = None,
    consent: Optional[Mapping[str, Any]] = None,
    case_set: Optional[Mapping[str, Any]] = None,
    work_product: Optional[Mapping[str, Any]] = None,
) -> ShadowRunResult:
    """Evaluate candidates and publish only beneath an explicit shadow root."""

    validate_shadow_plan(plan, consent)
    if (
        plan["dataset_kind"] == "real_vault_snapshot"
        and plan["user_confirmation"]["status"] != "confirmed"
    ):
        raise ContractError("real Vault shadow run requires explicit user confirmation", kind="permission")
    validate_datetime(finished_at, "finished_at")
    verified = verify_read_only_snapshot(snapshot.path)
    if dict(verified.manifest) != dict(snapshot.manifest):
        raise ContractError("shadow snapshot changed after plan creation", kind="evidence")
    if plan["snapshot_id"] != snapshot.manifest["snapshot_id"]:
        raise ContractError("shadow plan is bound to another snapshot", kind="conflict")
    if plan["dataset_kind"] != snapshot.manifest["snapshot_kind"]:
        raise ContractError("shadow plan dataset kind differs from its snapshot", kind="conflict")
    _validate_run_chronology(plan, snapshot, finished_at)
    _validate_snapshot_consent_binding(snapshot, consent)
    if candidate_bundle is not None:
        validate_projection_bundle_contract(candidate_bundle)
    if (case_set is None) != (work_product is None):
        raise ContractError(
            "shadow case set and work product must be provided together",
            kind="evidence",
        )
    case_ref = None
    work_ref = None
    if case_set is not None and work_product is not None:
        if candidate_bundle is None:
            raise ContractError("shadow work product requires its candidate bundle", kind="evidence")
        validate_shadow_case_set(case_set, snapshot)
        derived_observations = validate_shadow_work_product(
            work_product=work_product,
            plan=plan,
            case_set=case_set,
            snapshot=snapshot,
            candidate_bundle=candidate_bundle,
        )
        if [_observation_identity(item) for item in observations] != [
            _observation_identity(item) for item in derived_observations
        ]:
            raise ContractError(
                "shadow observations differ from the sealed work product",
                kind="evidence",
            )
        case_ref = shadow_case_set_ref(case_set)
        work_ref = {
            "work_product_id": str(work_product["work_product_id"]),
            "work_product_sha256": sha256_json(work_product),
        }
        if plan["case_set_ref"] != case_ref:
            raise ContractError("shadow plan case set binding is inconsistent", kind="conflict")
        if _timestamp(work_product["finished_at"]) > _timestamp(finished_at):
            raise ContractError("shadow report predates its work product", kind="conflict")
    elif plan["case_set_ref"] is not None:
        raise ContractError("shadow plan references missing case set evidence", kind="evidence")
    if baseline_bundle_sha256 is not None:
        validate_sha256(baseline_bundle_sha256, "baseline_bundle_sha256")
    if baseline_snapshot_path is not None:
        validate_relative_path(baseline_snapshot_path)
    _validate_baseline_binding(
        snapshot=snapshot,
        baseline_bundle_sha256=baseline_bundle_sha256,
        baseline_snapshot_path=baseline_snapshot_path,
    )
    resolved_output = output_root.resolve(strict=True)
    if resolved_output != snapshot.path.parent.resolve(strict=True):
        raise ContractError(
            "shadow run output must use the isolated workspace that created the snapshot",
            kind="path",
        )

    metrics = aggregate_shadow_metrics(observations)
    gate_results, all_gates_passed = evaluate_shadow_gates(metrics, plan["thresholds"])
    _enforce_provider_budget(plan, metrics)
    candidate_sha = None if candidate_bundle is None else candidate_bundle.bundle_sha256
    real_provider_request = (
        plan["dataset_kind"] == "real_vault_snapshot"
        and plan["execution_mode"] == "provider_shadow"
        and plan["user_confirmation"]["status"] == "confirmed"
    )
    real_provider_gate = (
        real_provider_request
        and candidate_bundle is not None
        and baseline_bundle_sha256 is not None
        and baseline_snapshot_path is not None
        and case_ref is not None
        and work_ref is not None
    )
    if real_provider_request and not real_provider_gate:
        raise ContractError(
            "real quality evaluation requires case-set work evidence, a candidate bundle, and a snapshot-bound baseline",
            kind="evidence",
        )
    expected_status = (
        "passed" if all_gates_passed else "failed"
    ) if real_provider_gate else "infrastructure_only"
    observation_document = {
        "observations": [_observation_identity(item) for item in observations]
    }
    observation_sha256 = sha256_json(observation_document)
    run_identity = {
        "plan_sha256": sha256_json(plan),
        "snapshot_sha256": sha256_json(snapshot.manifest),
        "case_set_ref": case_ref,
        "work_product_ref": work_ref,
        "observation_sha256": observation_sha256,
        "candidate_bundle_sha256": candidate_sha,
        "baseline_bundle_sha256": baseline_bundle_sha256,
        "baseline_snapshot_path": baseline_snapshot_path,
        "finished_at": finished_at,
    }
    run_id = "shr_" + sha256_json(run_identity)[:24]
    root_store = AtomicFileStore(resolved_output)
    root_store.ensure_directory("shadow-runs/staging")
    root_store.ensure_directory("shadow-runs/runs")
    sealed_relative = f"shadow-runs/runs/{run_id}"
    if root_store.directory_exists(sealed_relative):
        sealed_root = resolved_output / sealed_relative
        existing = _load_sealed_run(
            sealed_root=sealed_root,
            expected_run_id=run_id,
            expected_plan=plan,
            expected_snapshot=snapshot,
            expected_case_set=case_set,
            expected_work_product=work_product,
            expected_observations=observation_document,
            expected_candidate_sha256=candidate_sha,
            expected_baseline_sha256=baseline_bundle_sha256,
            expected_baseline_path=baseline_snapshot_path,
            expected_finished_at=finished_at,
            expected_metrics=metrics,
            expected_gate_results=gate_results,
            expected_all_gates_passed=all_gates_passed,
            expected_status=expected_status,
            expected_real_quality_validated=real_provider_gate,
            expected_consent=consent,
        )
        return ShadowRunResult(report=existing, run_root=sealed_root)
    run_relative = f"shadow-runs/staging/{run_id}"
    run_root = root_store.ensure_directory(run_relative)
    run_store = AtomicFileStore(run_root)

    candidate_pointer: Optional[Mapping[str, Any]] = None
    if candidate_bundle is not None:
        candidate_root = run_store.ensure_directory("candidate")
        candidate_pointer = BundleStore(AtomicFileStore(candidate_root)).publish(
            candidate_bundle, published_at=finished_at
        )

    report = {
        "schema_version": "1.0",
        "kind": "memento_shadow_report",
        "run_id": run_id,
        "status": expected_status,
        "plan_id": plan["plan_id"],
        "plan_sha256": sha256_json(plan),
        "snapshot_id": snapshot.manifest["snapshot_id"],
        "snapshot_sha256": sha256_json(snapshot.manifest),
        "case_set_ref": case_ref,
        "work_product_ref": work_ref,
        "observation_sha256": observation_sha256,
        "dataset_kind": plan["dataset_kind"],
        "execution_mode": plan["execution_mode"],
        "consent_ref": plan["consent_ref"],
        "metrics": metrics,
        "gate_results": gate_results,
        "all_gates_passed": all_gates_passed,
        "candidate_bundle_sha256": candidate_sha,
        "candidate_pointer_sha256": None if candidate_pointer is None else sha256_json(candidate_pointer),
        "baseline_bundle_sha256": baseline_bundle_sha256,
        "baseline_snapshot_path": baseline_snapshot_path,
        "formal_write_count": 0,
        "source_snapshot_verified": True,
        "real_quality_validated": real_provider_gate,
        "finished_at": finished_at,
    }
    validate_contract("shadow-report-v1.schema.json", report)
    run_store.write_new_json_idempotent("plan.json", plan)
    if consent is not None:
        run_store.write_new_json_idempotent("consent.json", consent)
    run_store.write_new_json_idempotent("snapshot-ref.json", {
        "snapshot_id": snapshot.manifest["snapshot_id"],
        "snapshot_sha256": sha256_json(snapshot.manifest),
    })
    if case_set is not None and work_product is not None:
        run_store.write_new_json_idempotent("case-set.json", case_set)
        run_store.write_new_json_idempotent("work-product.json", work_product)
    run_store.write_new_json_idempotent("observations.json", observation_document)
    run_store.write_new_json_idempotent("report.json", report)
    _seal_run_tree(run_root, seal_root=False)
    try:
        root_store.rename_directory_new(run_relative, sealed_relative)
    except BaseException:
        _make_run_tree_writable(run_root)
        raise
    sealed_root = resolved_output / sealed_relative
    os.chmod(sealed_root, 0o500, follow_symlinks=False)
    return ShadowRunResult(report=report, run_root=sealed_root)


def _validate_baseline_binding(
    *,
    snapshot: ReadOnlySnapshot,
    baseline_bundle_sha256: Optional[str],
    baseline_snapshot_path: Optional[str],
) -> None:
    if (baseline_bundle_sha256 is None) != (baseline_snapshot_path is None):
        raise ContractError(
            "baseline path and hash must be provided together",
            kind="evidence",
        )
    if baseline_bundle_sha256 is None or baseline_snapshot_path is None:
        return
    matching = [
        entry
        for entry in snapshot.manifest["files"]
        if entry["path"] == baseline_snapshot_path
    ]
    if not matching:
        raise ContractError("baseline is not present in the bound snapshot", kind="evidence")
    if matching[0]["sha256"] != baseline_bundle_sha256:
        raise ContractError("baseline hash differs from the bound snapshot", kind="evidence")


def _seal_run_tree(root: Path, *, seal_root: bool = True) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ContractError("shadow run contains a symlink", kind="path")
        if stat.S_ISREG(info.st_mode):
            os.chmod(path, 0o400, follow_symlinks=False)
        elif stat.S_ISDIR(info.st_mode):
            os.chmod(path, 0o500, follow_symlinks=False)
        else:
            raise ContractError("shadow run contains a special file", kind="path")
    os.chmod(root, 0o500 if seal_root else 0o700, follow_symlinks=False)


def _make_run_tree_writable(root: Path) -> None:
    if not root.exists() or root.is_symlink():
        return
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            os.chmod(path, 0o700, follow_symlinks=False)
        elif stat.S_ISREG(info.st_mode):
            os.chmod(path, 0o600, follow_symlinks=False)
    os.chmod(root, 0o700, follow_symlinks=False)


def _load_sealed_run(
    *,
    sealed_root: Path,
    expected_run_id: str,
    expected_plan: Mapping[str, Any],
    expected_snapshot: ReadOnlySnapshot,
    expected_case_set: Optional[Mapping[str, Any]],
    expected_work_product: Optional[Mapping[str, Any]],
    expected_observations: Mapping[str, Any],
    expected_candidate_sha256: Optional[str],
    expected_baseline_sha256: Optional[str],
    expected_baseline_path: Optional[str],
    expected_finished_at: str,
    expected_metrics: Mapping[str, Any],
    expected_gate_results: Sequence[Mapping[str, Any]],
    expected_all_gates_passed: bool,
    expected_status: str,
    expected_real_quality_validated: bool,
    expected_consent: Optional[Mapping[str, Any]],
) -> Mapping[str, Any]:
    for path in (sealed_root, *sorted(sealed_root.rglob("*"))):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ContractError("sealed shadow run contains a symlink", kind="path")
        if stat.S_IMODE(info.st_mode) & 0o222:
            raise ContractError("sealed shadow run is writable", kind="permission")
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ContractError("sealed shadow run contains a special file", kind="path")
    store = AtomicFileStore(sealed_root)
    report = store.read_json("report.json")
    validate_contract("shadow-report-v1.schema.json", report)
    plan = store.read_json("plan.json")
    snapshot_ref = store.read_json("snapshot-ref.json")
    observations = store.read_json("observations.json")
    consent_path = sealed_root / "consent.json"
    stored_consent = store.read_json("consent.json") if consent_path.is_file() else None
    case_set_path = sealed_root / "case-set.json"
    stored_case_set = store.read_json("case-set.json") if case_set_path.is_file() else None
    work_product_path = sealed_root / "work-product.json"
    stored_work_product = (
        store.read_json("work-product.json") if work_product_path.is_file() else None
    )
    expected_case_ref = (
        None if expected_case_set is None else shadow_case_set_ref(expected_case_set)
    )
    expected_work_ref = (
        None
        if expected_work_product is None
        else {
            "work_product_id": str(expected_work_product["work_product_id"]),
            "work_product_sha256": sha256_json(expected_work_product),
        }
    )
    expected = {
        "run_id": expected_run_id,
        "plan_id": expected_plan["plan_id"],
        "plan_sha256": sha256_json(expected_plan),
        "snapshot_id": expected_snapshot.manifest["snapshot_id"],
        "snapshot_sha256": sha256_json(expected_snapshot.manifest),
        "case_set_ref": expected_case_ref,
        "work_product_ref": expected_work_ref,
        "observation_sha256": sha256_json(expected_observations),
        "candidate_bundle_sha256": expected_candidate_sha256,
        "baseline_bundle_sha256": expected_baseline_sha256,
        "baseline_snapshot_path": expected_baseline_path,
        "finished_at": expected_finished_at,
        "dataset_kind": expected_plan["dataset_kind"],
        "execution_mode": expected_plan["execution_mode"],
        "consent_ref": expected_plan["consent_ref"],
        "metrics": dict(expected_metrics),
        "gate_results": [dict(item) for item in expected_gate_results],
        "all_gates_passed": expected_all_gates_passed,
        "status": expected_status,
        "formal_write_count": 0,
        "source_snapshot_verified": True,
        "real_quality_validated": expected_real_quality_validated,
    }
    if plan != dict(expected_plan):
        raise ContractError("sealed shadow run plan differs from request", kind="evidence")
    if observations != dict(expected_observations):
        raise ContractError("sealed shadow observations differ from request", kind="evidence")
    if stored_consent != (None if expected_consent is None else dict(expected_consent)):
        raise ContractError("sealed shadow consent differs from request", kind="evidence")
    if stored_case_set != (None if expected_case_set is None else dict(expected_case_set)):
        raise ContractError("sealed shadow case set differs from request", kind="evidence")
    if stored_work_product != (
        None if expected_work_product is None else dict(expected_work_product)
    ):
        raise ContractError("sealed shadow work product differs from request", kind="evidence")
    if snapshot_ref != {
        "snapshot_id": expected["snapshot_id"],
        "snapshot_sha256": expected["snapshot_sha256"],
    }:
        raise ContractError("sealed shadow snapshot reference is inconsistent", kind="evidence")
    if any(report[field] != value for field, value in expected.items()):
        raise ContractError("sealed shadow report identity is inconsistent", kind="evidence")
    if expected_candidate_sha256 is not None:
        candidate_store = BundleStore(AtomicFileStore(sealed_root / "candidate"))
        pointer = candidate_store.load_current_pointer()
        bundle = candidate_store.load_current()
        if bundle is None or bundle.bundle_sha256 != expected_candidate_sha256:
            raise ContractError("sealed shadow candidate is inconsistent", kind="evidence")
        if pointer is None or report["candidate_pointer_sha256"] != sha256_json(pointer):
            raise ContractError("sealed shadow candidate pointer is inconsistent", kind="evidence")
    elif report["candidate_pointer_sha256"] is not None:
        raise ContractError("sealed shadow report names an unexpected candidate", kind="evidence")
    return report


def _validate_shadow_plan_policy(plan: Mapping[str, Any]) -> None:
    execution_mode = str(plan["execution_mode"])
    confirmation = plan["user_confirmation"]
    provider = plan["provider"]
    if plan["write_mode"] != "shadow_only":
        raise ContractError("R9 only allows shadow-only output", kind="permission")
    identity = {
        "snapshot_id": plan["snapshot_id"],
        "case_set_ref": plan["case_set_ref"],
        "dataset_kind": plan["dataset_kind"],
        "execution_mode": plan["execution_mode"],
        "write_mode": plan["write_mode"],
        "provider": plan["provider"],
        "thresholds": plan["thresholds"],
        "user_confirmation": plan["user_confirmation"],
        "consent_ref": plan["consent_ref"],
        "created_at": plan["created_at"],
    }
    if plan["plan_id"] != "shp_" + sha256_json(identity)[:24]:
        raise ContractError("shadow plan identity is inconsistent", kind="evidence")
    if confirmation["status"] == "confirmed" and not confirmation["confirmation_ref"]:
        raise ContractError("confirmed shadow plan requires a confirmation reference", kind="permission")
    if plan["dataset_kind"] == "real_vault_snapshot":
        if confirmation["status"] == "not_required":
            raise ContractError("real Vault plan cannot waive user confirmation", kind="permission")
    elif confirmation["status"] != "not_required":
        raise ContractError("non-real shadow plan must use not_required confirmation", kind="permission")
    if confirmation["status"] != "confirmed" and confirmation["confirmation_ref"] is not None:
        raise ContractError("unconfirmed shadow plan cannot carry a confirmation reference", kind="permission")
    numeric_values = [
        *plan["thresholds"].values(),
        *provider["budget"].values(),
    ]
    if any(type(value) not in {int, float} or not math.isfinite(float(value)) for value in numeric_values):
        raise ContractError("shadow plan numbers must be finite")
    if not provider["policy_versions"]:
        raise ContractError("shadow plan must pin at least one policy version")
    if execution_mode == "deterministic_zero":
        budget = provider["budget"]
        if provider["provider"] is not None or provider["model"] is not None:
            raise ContractError("deterministic shadow plan cannot name a product provider")
        if any((budget["max_prompt_tokens"], budget["max_completion_tokens"], budget["max_cost_usd"], budget["max_latency_ms"])):
            raise ContractError("deterministic shadow plan must have a zero provider budget")
    elif not provider["provider"] or not provider["model"]:
        raise ContractError("provider shadow plan must pin provider and model")
    elif not provider["prompt_versions"]:
        raise ContractError("provider shadow plan must pin prompt versions")


def _validate_plan_consent_binding(
    plan: Mapping[str, Any], consent: Optional[Mapping[str, Any]]
) -> None:
    is_real = plan["dataset_kind"] == "real_vault_snapshot"
    is_confirmed = plan["user_confirmation"]["status"] == "confirmed"
    if consent is None:
        if is_real and is_confirmed:
            raise ContractError(
                "confirmed real Vault plan requires structured user consent",
                kind="permission",
            )
        if plan["consent_ref"] is not None:
            raise ContractError("shadow plan references missing consent", kind="evidence")
        return
    validate_shadow_consent(consent)
    if not (is_real and is_confirmed):
        raise ContractError(
            "structured consent is only valid for a confirmed real Vault plan",
            kind="permission",
        )
    expected_ref = build_consent_ref(consent)
    if plan["consent_ref"] != expected_ref:
        raise ContractError("shadow plan consent hash is inconsistent", kind="evidence")
    if plan["user_confirmation"]["confirmation_ref"] != expected_ref["consent_id"]:
        raise ContractError("shadow plan confirmation does not name its consent", kind="permission")
    if dict(plan["thresholds"]) != dict(consent["thresholds"]):
        raise ContractError("shadow plan thresholds differ from user consent", kind="permission")
    if dict(plan["provider"]) != dict(consent["provider"]):
        raise ContractError("shadow plan provider policy differs from user consent", kind="permission")
    if _timestamp(consent["confirmed_at"]) > _timestamp(plan["created_at"]):
        raise ContractError("shadow plan predates user consent", kind="permission")


def _validate_snapshot_consent_binding(
    snapshot: ReadOnlySnapshot, consent: Optional[Mapping[str, Any]]
) -> None:
    if snapshot.manifest["snapshot_kind"] != "real_vault_snapshot":
        if consent is not None:
            raise ContractError("non-real snapshot cannot use real Vault consent", kind="permission")
        return
    if consent is None:
        return
    validate_shadow_consent(consent)
    scope = consent["dataset_scope"]
    if snapshot.manifest["authorization_ref"] != consent["consent_id"]:
        raise ContractError("snapshot authorization does not name its consent", kind="permission")
    if snapshot.manifest["source_label"] != scope["source_label"]:
        raise ContractError("snapshot source differs from user consent", kind="permission")
    if snapshot.manifest["source_root_sha256"] != sha256_bytes(
        str(scope["source_root"]).encode("utf-8")
    ):
        raise ContractError("snapshot root differs from user consent", kind="permission")
    if list(snapshot.manifest["allowed_suffixes"]) != sorted(scope["allowed_suffixes"]):
        raise ContractError("snapshot file scope differs from user consent", kind="permission")
    if _timestamp(consent["confirmed_at"]) > _timestamp(snapshot.manifest["created_at"]):
        raise ContractError("shadow snapshot predates user consent", kind="permission")


def _validate_run_chronology(
    plan: Mapping[str, Any], snapshot: ReadOnlySnapshot, finished_at: str
) -> None:
    snapshot_created = _timestamp(snapshot.manifest["created_at"])
    plan_created = _timestamp(plan["created_at"])
    finished = _timestamp(finished_at)
    if snapshot_created > plan_created:
        raise ContractError("shadow plan predates its snapshot", kind="conflict")
    if plan_created > finished:
        raise ContractError("shadow run finished before its plan was created", kind="conflict")


def _timestamp(value: Any) -> dt.datetime:
    validate_datetime(value)
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _enforce_provider_budget(plan: Mapping[str, Any], metrics: Mapping[str, Any]) -> None:
    budget = plan["provider"]["budget"]
    usage = metrics["usage"]
    values = (
        (usage["prompt_tokens"], budget["max_prompt_tokens"], "prompt token"),
        (usage["completion_tokens"], budget["max_completion_tokens"], "completion token"),
        (usage["estimated_cost_usd"], budget["max_cost_usd"], "cost"),
        (usage["latency_max_ms"], budget["max_latency_ms"], "latency"),
    )
    if plan["execution_mode"] == "deterministic_zero":
        if metrics["counts"]["provider_attempt_case_count"] != 0 or any(
            actual != 0 for actual, _, _ in values
        ):
            raise ContractError("deterministic shadow run recorded provider usage", kind="budget")
        return
    if metrics["counts"]["provider_attempt_case_count"] == 0:
        raise ContractError("provider shadow run has no recorded provider attempt", kind="evidence")
    for actual, maximum, label in values:
        if actual > maximum:
            raise ContractError(f"shadow {label} budget exceeded", kind="budget")


def _observation_identity(item: ShadowObservation) -> dict[str, Any]:
    item.validate()
    return {
        "case_id": item.case_id,
        "expected_links": sorted(item.expected_links),
        "predicted_links": sorted(item.predicted_links),
        "allowed_inferences": sorted(item.allowed_inferences),
        "predicted_inferences": sorted(item.predicted_inferences),
        "should_stop": item.should_stop,
        "did_stop": item.did_stop,
        "source_reference_checks": item.source_reference_checks,
        "valid_source_references": item.valid_source_references,
        "self_traceability_checks": item.self_traceability_checks,
        "valid_self_traces": item.valid_self_traces,
        "resource_opinion_checks": item.resource_opinion_checks,
        "resource_as_user_errors": item.resource_as_user_errors,
        "stale_resurrections": item.stale_resurrections,
        "adapter_checks": item.adapter_checks,
        "adapter_passes": item.adapter_passes,
        "source_hash_checks": item.source_hash_checks,
        "stable_source_hashes": item.stable_source_hashes,
        "provider_attempted": item.provider_attempted,
        "prompt_tokens": item.prompt_tokens,
        "completion_tokens": item.completion_tokens,
        "estimated_cost_usd": item.estimated_cost_usd,
        "latency_ms": item.latency_ms,
    }
