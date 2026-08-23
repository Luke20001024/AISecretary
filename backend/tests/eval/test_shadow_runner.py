from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import sha256_bytes
from memento_backend.evaluation.shadow_consent import build_shadow_consent
from memento_backend.evaluation.shadow_metrics import ShadowObservation
from memento_backend.evaluation.shadow_runner import build_shadow_plan, run_shadow_evaluation
from memento_backend.evaluation.shadow_snapshot import ReadOnlySnapshot, create_read_only_snapshot
from memento_backend.evaluation.shadow_worker import (
    ShadowCaseInput,
    ShadowCasePrediction,
    build_shadow_case_set,
    execute_shadow_cases,
    shadow_case_set_ref,
)
from memento_backend.projections.bundle_projector import ProjectionBundle, build_projection_bundle
from memento_backend.providers.protocol import ProviderUsage
from memento_backend.storage.atomic import AtomicFileStore

from tests.fixtures.formal_20d import formal_20d_inputs


def _directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True)
    return path


def _thresholds() -> dict[str, float | int]:
    return {
        "false_link_rate_max": 0.0,
        "missed_link_rate_max": 0.0,
        "over_inference_rate_max": 0.0,
        "stop_f1_min": 1.0,
        "stop_case_count_min": 3,
        "source_reference_valid_rate_min": 1.0,
        "self_traceability_rate_min": 1.0,
        "resource_as_user_false_rate_max": 0.0,
        "stale_resurrection_count_max": 0,
        "adapter_pass_rate_min": 1.0,
        "source_hash_stability_rate_min": 1.0,
        "estimated_cost_usd_max": 0.0,
        "latency_p95_ms_max": 0,
    }


def _observations() -> list[ShadowObservation]:
    values = []
    for index in range(3):
        values.append(ShadowObservation(
            case_id=f"case-{index}",
            expected_links=frozenset({f"link-{index}"}),
            predicted_links=frozenset({f"link-{index}"}),
            allowed_inferences=frozenset({f"insight-{index}"}),
            predicted_inferences=frozenset({f"insight-{index}"}),
            should_stop=True,
            did_stop=True,
            source_reference_checks=1,
            valid_source_references=1,
            self_traceability_checks=1,
            valid_self_traces=1,
            resource_opinion_checks=1,
            resource_as_user_errors=0,
            adapter_checks=1,
            adapter_passes=1,
            source_hash_checks=1,
            stable_source_hashes=1,
        ))
    return values


def _source_label(tmp_path: Path, kind: str) -> str:
    return f"{kind}-{tmp_path.name}"


def _real_thresholds() -> dict[str, float | int]:
    return {**_thresholds(), "estimated_cost_usd_max": 1.0, "latency_p95_ms_max": 1000}


def _consent(tmp_path: Path, *, prompt_versions: tuple[str, ...]) -> dict[str, Any]:
    return build_shadow_consent(
        dataset_scope={
            "source_label": _source_label(tmp_path, "real_vault_snapshot"),
            "source_root": str(
                (tmp_path.resolve() / "source-real_vault_snapshot")
            ),
            "snapshot_kind": "real_vault_snapshot",
            "allowed_suffixes": [".json", ".md", ".txt"],
            "read_only_only": True,
        },
        thresholds=_real_thresholds(),
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
            "capture_understanding": {
                "frequency": "event_driven", "local_time": None, "weekday": None,
            },
            "record_interpreter": {
                "frequency": "event_driven", "local_time": None, "weekday": None,
            },
            "daily_integrator": {"frequency": "daily", "local_time": "22:00", "weekday": None},
            "theme_synthesizer": {"frequency": "weekly", "local_time": "08:00", "weekday": 0},
            "self_understanding": {"frequency": "weekly", "local_time": "09:00", "weekday": 6},
            "context_router": {
                "frequency": "event_driven", "local_time": None, "weekday": None,
            },
        },
        provider={
            "provider": "provider-x",
            "model": "model-y",
            "prompt_versions": list(prompt_versions),
            "policy_versions": ["shadow-policy-v1"],
            "budget": {
                "max_prompt_tokens": 1000,
                "max_completion_tokens": 1000,
                "max_cost_usd": 1.0,
                "max_latency_ms": 1000,
            },
        },
        confirmed_at="2026-08-23T09:59:00+08:00",
    )


def _snapshot(
    tmp_path: Path,
    *,
    kind: str = "synthetic_fixture",
    authorization_ref: str | None = None,
) -> ReadOnlySnapshot:
    source = _directory(tmp_path / f"source-{kind}")
    (source / "record.md").write_text("- 变化的理由值得留下\n", encoding="utf-8")
    output = _directory(tmp_path / f"snapshot-output-{kind}")
    return create_read_only_snapshot(
        source,
        output,
        source_label=_source_label(tmp_path, kind),
        snapshot_kind=kind,
        authorization_ref=authorization_ref if kind == "real_vault_snapshot" else None,
        created_at="2026-08-23T10:00:00+08:00",
    )


def _bundle() -> ProjectionBundle:
    return build_projection_bundle(
        formal_20d_inputs(),
        as_of="2026-08-18",
        generated_at="2026-08-23T10:02:00+08:00",
    )


def _case_specs() -> list[dict[str, Any]]:
    return [
        {
            "case_id": f"case-{index}",
            "input_paths": ["record.md"],
            "expected_links": [f"link-{index}"],
            "allowed_inferences": [f"insight-{index}"],
            "should_stop": True,
            "checks": {
                "source_reference_checks": 1,
                "self_traceability_checks": 1,
                "resource_opinion_checks": 1,
                "adapter_checks": 1,
                "source_hash_checks": 1,
            },
        }
        for index in range(3)
    ]


class _ProviderFixtureProducer:
    def __init__(self, bundle: ProjectionBundle) -> None:
        self._bundle = bundle

    def produce_case(self, case: ShadowCaseInput) -> ShadowCasePrediction:
        index = case.case_id.rsplit("-", 1)[-1]
        return ShadowCasePrediction(
            case_id=case.case_id,
            predicted_links=frozenset({f"link-{index}"}),
            predicted_inferences=frozenset({f"insight-{index}"}),
            did_stop=True,
            valid_source_references=1,
            valid_self_traces=1,
            resource_as_user_errors=0,
            stale_resurrections=0,
            adapter_passes=1,
            stable_source_hashes=1,
            usage=ProviderUsage(
                mode="provider",
                provider="provider-x",
                model="model-y",
                attempt_status="succeeded",
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                estimated_cost_usd=0.01,
                latency_ms=50,
            ),
        )

    def candidate_bundle(self) -> ProjectionBundle:
        return self._bundle


def test_deterministic_shadow_run_publishes_only_isolated_candidate_and_report(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    output = snapshot.path.parent
    plan = build_shadow_plan(
        snapshot_id=str(snapshot.manifest["snapshot_id"]),
        dataset_kind="synthetic_fixture",
        execution_mode="deterministic_zero",
        created_at="2026-08-23T10:01:00+08:00",
        thresholds=_thresholds(),
        user_confirmation_status="not_required",
        user_confirmation_ref=None,
        policy_versions=("shadow-policy-v1",),
    )
    bundle = _bundle()
    result = run_shadow_evaluation(
        plan=plan,
        snapshot=snapshot,
        observations=_observations(),
        output_root=output,
        finished_at="2026-08-23T10:03:00+08:00",
        candidate_bundle=bundle,
    )
    assert result.report["status"] == "infrastructure_only"
    assert result.report["all_gates_passed"] is True
    assert result.report["formal_write_count"] == 0
    assert result.report["real_quality_validated"] is False
    assert result.report["observation_sha256"]
    assert (result.run_root / "candidate/projections/current.json").is_file()
    assert (result.run_root / "observations.json").is_file()
    assert result.run_root.stat().st_mode & 0o222 == 0
    assert all(path.stat().st_mode & 0o222 == 0 for path in result.run_root.rglob("*"))
    assert not (output / "records").exists()
    assert not (output / "themes").exists()
    assert not (output / "self-insights").exists()


def test_real_shadow_plan_can_be_preregistered_pending_but_cannot_run(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path, kind="real_vault_snapshot", authorization_ref="pending-r9")
    output = _directory(tmp_path / "shadow-output")
    plan = build_shadow_plan(
        snapshot_id=str(snapshot.manifest["snapshot_id"]),
        dataset_kind="real_vault_snapshot",
        execution_mode="provider_shadow",
        created_at="2026-08-23T10:01:00+08:00",
        thresholds={**_thresholds(), "estimated_cost_usd_max": 1.0, "latency_p95_ms_max": 1000},
        user_confirmation_status="pending",
        user_confirmation_ref=None,
        provider="provider-x",
        model="model-y",
        prompt_versions=("capture-v1",),
        policy_versions=("shadow-policy-v1",),
        max_prompt_tokens=1000,
        max_completion_tokens=1000,
        max_cost_usd=1.0,
        max_latency_ms=1000,
    )
    with pytest.raises(ContractError) as raised:
        run_shadow_evaluation(
            plan=plan,
            snapshot=snapshot,
            observations=_observations(),
            output_root=output,
            finished_at="2026-08-23T10:03:00+08:00",
        )
    assert raised.value.kind == "permission"
    assert list(output.iterdir()) == []


def test_confirmed_provider_shadow_run_can_reach_a_real_quality_terminal_state(tmp_path: Path) -> None:
    consent = _consent(tmp_path, prompt_versions=("capture-v1", "interpret-v1"))
    snapshot = _snapshot(
        tmp_path,
        kind="real_vault_snapshot",
        authorization_ref=str(consent["consent_id"]),
    )
    case_set = build_shadow_case_set(
        snapshot=snapshot,
        created_at="2026-08-23T10:00:30+08:00",
        cases=_case_specs(),
    )
    plan = build_shadow_plan(
        snapshot_id=str(snapshot.manifest["snapshot_id"]),
        dataset_kind="real_vault_snapshot",
        execution_mode="provider_shadow",
        created_at="2026-08-23T10:01:00+08:00",
        thresholds=_real_thresholds(),
        user_confirmation_status="confirmed",
        user_confirmation_ref=str(consent["consent_id"]),
        provider="provider-x",
        model="model-y",
        prompt_versions=("capture-v1", "interpret-v1"),
        policy_versions=("shadow-policy-v1",),
        max_prompt_tokens=1000,
        max_completion_tokens=1000,
        max_cost_usd=1.0,
        max_latency_ms=1000,
        consent=consent,
        case_set_ref=shadow_case_set_ref(case_set),
    )
    execution = execute_shadow_cases(
        plan=plan,
        snapshot=snapshot,
        case_set=case_set,
        producer=_ProviderFixtureProducer(_bundle()),
        started_at="2026-08-23T10:01:30+08:00",
        finished_at="2026-08-23T10:02:30+08:00",
    )
    result = run_shadow_evaluation(
        plan=plan,
        snapshot=snapshot,
        observations=execution.observations,
        output_root=snapshot.path.parent,
        finished_at="2026-08-23T10:03:00+08:00",
        candidate_bundle=execution.candidate_bundle,
        baseline_bundle_sha256=sha256_bytes((snapshot.path / "record.md").read_bytes()),
        baseline_snapshot_path="record.md",
        consent=consent,
        case_set=case_set,
        work_product=execution.work_product,
    )
    assert result.report["status"] == "passed"
    assert result.report["real_quality_validated"] is True
    assert result.report["metrics"]["counts"]["provider_attempt_case_count"] == 3
    assert (result.run_root / "case-set.json").is_file()
    assert (result.run_root / "work-product.json").is_file()
    again = run_shadow_evaluation(
        plan=plan,
        snapshot=snapshot,
        observations=execution.observations,
        output_root=snapshot.path.parent,
        finished_at="2026-08-23T10:03:00+08:00",
        candidate_bundle=execution.candidate_bundle,
        baseline_bundle_sha256=sha256_bytes((snapshot.path / "record.md").read_bytes()),
        baseline_snapshot_path="record.md",
        consent=consent,
        case_set=case_set,
        work_product=execution.work_product,
    )
    assert again.report == result.report
    assert again.run_root == result.run_root

    altered = list(execution.observations)
    altered[0] = replace(altered[0], did_stop=False)
    with pytest.raises(ContractError, match="sealed work product") as raised:
        run_shadow_evaluation(
            plan=plan,
            snapshot=snapshot,
            observations=altered,
            output_root=snapshot.path.parent,
            finished_at="2026-08-23T10:03:00+08:00",
            candidate_bundle=execution.candidate_bundle,
            baseline_bundle_sha256=sha256_bytes((snapshot.path / "record.md").read_bytes()),
            baseline_snapshot_path="record.md",
            consent=consent,
            case_set=case_set,
            work_product=execution.work_product,
        )
    assert raised.value.kind == "evidence"


def test_confirmed_real_plan_rejects_missing_structured_consent() -> None:
    with pytest.raises(ContractError) as raised:
        build_shadow_plan(
            snapshot_id="snp_" + "a" * 24,
            dataset_kind="real_vault_snapshot",
            execution_mode="provider_shadow",
            created_at="2026-08-23T10:01:00+08:00",
            thresholds=_real_thresholds(),
            user_confirmation_status="confirmed",
            user_confirmation_ref="free-text-is-not-consent",
            provider="provider-x",
            model="model-y",
            prompt_versions=("capture-v1",),
            policy_versions=("shadow-policy-v1",),
            max_prompt_tokens=1000,
            max_completion_tokens=1000,
            max_cost_usd=1.0,
            max_latency_ms=1000,
        )
    assert raised.value.kind == "permission"


def test_real_plan_rejects_thresholds_outside_confirmed_consent(tmp_path: Path) -> None:
    consent = _consent(tmp_path, prompt_versions=("capture-v1",))
    with pytest.raises(ContractError, match="thresholds") as raised:
        build_shadow_plan(
            snapshot_id="snp_" + "a" * 24,
            dataset_kind="real_vault_snapshot",
            execution_mode="provider_shadow",
            created_at="2026-08-23T10:01:00+08:00",
            thresholds={**_real_thresholds(), "false_link_rate_max": 0.2},
            user_confirmation_status="confirmed",
            user_confirmation_ref=str(consent["consent_id"]),
            provider="provider-x",
            model="model-y",
            prompt_versions=("capture-v1",),
            policy_versions=("shadow-policy-v1",),
            max_prompt_tokens=1000,
            max_completion_tokens=1000,
            max_cost_usd=1.0,
            max_latency_ms=1000,
            consent=consent,
        )
    assert raised.value.kind == "permission"


def test_real_plan_cannot_predate_its_consent(tmp_path: Path) -> None:
    consent = _consent(tmp_path, prompt_versions=("capture-v1",))
    with pytest.raises(ContractError, match="predates") as raised:
        build_shadow_plan(
            snapshot_id="snp_" + "a" * 24,
            dataset_kind="real_vault_snapshot",
            execution_mode="provider_shadow",
            created_at="2026-08-23T09:58:00+08:00",
            thresholds=_real_thresholds(),
            user_confirmation_status="confirmed",
            user_confirmation_ref=str(consent["consent_id"]),
            provider="provider-x",
            model="model-y",
            prompt_versions=("capture-v1",),
            policy_versions=("shadow-policy-v1",),
            max_prompt_tokens=1000,
            max_completion_tokens=1000,
            max_cost_usd=1.0,
            max_latency_ms=1000,
            consent=consent,
        )
    assert raised.value.kind == "permission"


def test_real_run_rejects_consent_or_snapshot_scope_mismatch(tmp_path: Path) -> None:
    consent = _consent(tmp_path, prompt_versions=("capture-v1",))
    snapshot = _snapshot(
        tmp_path,
        kind="real_vault_snapshot",
        authorization_ref=str(consent["consent_id"]),
    )
    plan = build_shadow_plan(
        snapshot_id=str(snapshot.manifest["snapshot_id"]),
        dataset_kind="real_vault_snapshot",
        execution_mode="provider_shadow",
        created_at="2026-08-23T10:01:00+08:00",
        thresholds=_real_thresholds(),
        user_confirmation_status="confirmed",
        user_confirmation_ref=str(consent["consent_id"]),
        provider="provider-x",
        model="model-y",
        prompt_versions=("capture-v1",),
        policy_versions=("shadow-policy-v1",),
        max_prompt_tokens=1000,
        max_completion_tokens=1000,
        max_cost_usd=1.0,
        max_latency_ms=1000,
        consent=consent,
    )
    other_consent = _consent(tmp_path / "other", prompt_versions=("capture-v1",))
    with pytest.raises(ContractError, match="consent hash") as consent_error:
        run_shadow_evaluation(
            plan=plan,
            snapshot=snapshot,
            observations=_observations(),
            output_root=snapshot.path.parent,
            finished_at="2026-08-23T10:03:00+08:00",
            consent=other_consent,
        )
    assert consent_error.value.kind == "evidence"

    wrong_snapshot = _snapshot(
        tmp_path / "wrong-auth",
        kind="real_vault_snapshot",
        authorization_ref="shc_" + "f" * 24,
    )
    rebound_plan = {**plan, "snapshot_id": wrong_snapshot.manifest["snapshot_id"]}
    from memento_backend.domain.ids import sha256_json
    identity = {key: rebound_plan[key] for key in (
        "snapshot_id", "case_set_ref", "dataset_kind", "execution_mode", "write_mode", "provider",
        "thresholds", "user_confirmation", "consent_ref", "created_at",
    )}
    rebound_plan["plan_id"] = "shp_" + sha256_json(identity)[:24]
    with pytest.raises(ContractError, match="authorization") as snapshot_error:
        run_shadow_evaluation(
            plan=rebound_plan,
            snapshot=wrong_snapshot,
            observations=_observations(),
            output_root=wrong_snapshot.path.parent,
            finished_at="2026-08-23T10:03:00+08:00",
            consent=consent,
        )
    assert snapshot_error.value.kind == "permission"


@pytest.mark.parametrize(
    ("candidate", "baseline_hash", "baseline_path"),
    (
        (None, None, None),
        ("bundle", None, None),
        ("bundle", "snapshot", None),
        ("bundle", "wrong", "record.md"),
        ("bundle", "snapshot", "missing.md"),
    ),
)
def test_confirmed_real_quality_requires_candidate_and_snapshot_bound_baseline(
    tmp_path: Path,
    candidate: str | None,
    baseline_hash: str | None,
    baseline_path: str | None,
) -> None:
    consent = _consent(tmp_path, prompt_versions=("capture-v1",))
    snapshot = _snapshot(
        tmp_path,
        kind="real_vault_snapshot",
        authorization_ref=str(consent["consent_id"]),
    )
    plan = build_shadow_plan(
        snapshot_id=str(snapshot.manifest["snapshot_id"]),
        dataset_kind="real_vault_snapshot",
        execution_mode="provider_shadow",
        created_at="2026-08-23T10:01:00+08:00",
        thresholds=_real_thresholds(),
        user_confirmation_status="confirmed",
        user_confirmation_ref=str(consent["consent_id"]),
        provider="provider-x",
        model="model-y",
        prompt_versions=("capture-v1",),
        policy_versions=("shadow-policy-v1",),
        max_prompt_tokens=1000,
        max_completion_tokens=1000,
        max_cost_usd=1.0,
        max_latency_ms=1000,
        consent=consent,
    )
    observations = [
        replace(item, provider_attempted=True, latency_ms=50)
        for item in _observations()
    ]
    snapshot_hash = sha256_bytes((snapshot.path / "record.md").read_bytes())
    resolved_hash = (
        snapshot_hash if baseline_hash == "snapshot"
        else "f" * 64 if baseline_hash == "wrong"
        else None
    )
    with pytest.raises(ContractError) as raised:
        run_shadow_evaluation(
            plan=plan,
            snapshot=snapshot,
            observations=observations,
            output_root=snapshot.path.parent,
            finished_at="2026-08-23T10:03:00+08:00",
            candidate_bundle=_bundle() if candidate == "bundle" else None,
            baseline_bundle_sha256=resolved_hash,
            baseline_snapshot_path=baseline_path,
            consent=consent,
        )
    assert raised.value.kind == "evidence"
    assert not (snapshot.path.parent / "shadow-runs").exists()


def test_plan_snapshot_binding_and_zero_model_usage_are_enforced(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    other = _snapshot(tmp_path / "other")
    output = snapshot.path.parent
    plan = build_shadow_plan(
        snapshot_id=str(snapshot.manifest["snapshot_id"]),
        dataset_kind="synthetic_fixture",
        execution_mode="deterministic_zero",
        created_at="2026-08-23T10:01:00+08:00",
        thresholds=_thresholds(),
        user_confirmation_status="not_required",
        user_confirmation_ref=None,
        policy_versions=("shadow-policy-v1",),
    )
    with pytest.raises(ContractError) as raised:
        run_shadow_evaluation(
            plan=plan,
            snapshot=other,
            observations=_observations(),
            output_root=output,
            finished_at="2026-08-23T10:03:00+08:00",
        )
    assert raised.value.kind == "conflict"

    used = _observations()
    used[0] = replace(used[0], provider_attempted=True, prompt_tokens=1, latency_ms=1)
    with pytest.raises(ContractError) as budget_error:
        run_shadow_evaluation(
            plan=plan,
            snapshot=snapshot,
            observations=used,
            output_root=output,
            finished_at="2026-08-23T10:03:00+08:00",
        )
    assert budget_error.value.kind == "budget"
    assert not (output / "shadow-runs").exists()


def test_shadow_plan_rejects_non_finite_thresholds() -> None:
    with pytest.raises(ContractError, match="finite"):
        build_shadow_plan(
            snapshot_id="snp_" + "a" * 24,
            dataset_kind="synthetic_fixture",
            execution_mode="deterministic_zero",
            created_at="2026-08-23T10:01:00+08:00",
            thresholds={**_thresholds(), "false_link_rate_max": float("nan")},
            user_confirmation_status="not_required",
            user_confirmation_ref=None,
            policy_versions=("shadow-policy-v1",),
        )


def test_sealed_shadow_run_rejects_permission_tampering(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    plan = build_shadow_plan(
        snapshot_id=str(snapshot.manifest["snapshot_id"]),
        dataset_kind="synthetic_fixture",
        execution_mode="deterministic_zero",
        created_at="2026-08-23T10:01:00+08:00",
        thresholds=_thresholds(),
        user_confirmation_status="not_required",
        user_confirmation_ref=None,
        policy_versions=("shadow-policy-v1",),
    )
    observations = _observations()
    bundle = _bundle()
    first = run_shadow_evaluation(
        plan=plan,
        snapshot=snapshot,
        observations=observations,
        output_root=snapshot.path.parent,
        finished_at="2026-08-23T10:03:00+08:00",
        candidate_bundle=bundle,
    )
    report_path = first.run_root / "report.json"
    report_path.chmod(0o600)
    with pytest.raises(ContractError) as raised:
        run_shadow_evaluation(
            plan=plan,
            snapshot=snapshot,
            observations=observations,
            output_root=snapshot.path.parent,
            finished_at="2026-08-23T10:03:00+08:00",
            candidate_bundle=bundle,
        )
    assert raised.value.kind == "permission"


def test_sealed_shadow_run_recomputes_metrics_before_idempotent_reload(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    plan = build_shadow_plan(
        snapshot_id=str(snapshot.manifest["snapshot_id"]),
        dataset_kind="synthetic_fixture",
        execution_mode="deterministic_zero",
        created_at="2026-08-23T10:01:00+08:00",
        thresholds=_thresholds(),
        user_confirmation_status="not_required",
        user_confirmation_ref=None,
        policy_versions=("shadow-policy-v1",),
    )
    first = run_shadow_evaluation(
        plan=plan,
        snapshot=snapshot,
        observations=_observations(),
        output_root=snapshot.path.parent,
        finished_at="2026-08-23T10:03:00+08:00",
    )
    report_path = first.run_root / "report.json"
    first.run_root.chmod(0o700)
    report_path.chmod(0o600)
    document = json.loads(report_path.read_text(encoding="utf-8"))
    document["metrics"]["counts"]["false_link_count"] = 1
    report_path.write_text(json.dumps(document), encoding="utf-8")
    report_path.chmod(0o400)
    first.run_root.chmod(0o500)
    with pytest.raises(ContractError, match="identity"):
        run_shadow_evaluation(
            plan=plan,
            snapshot=snapshot,
            observations=_observations(),
            output_root=snapshot.path.parent,
            finished_at="2026-08-23T10:03:00+08:00",
        )


def test_failed_final_publish_restores_staging_permissions_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = _snapshot(tmp_path)
    plan = build_shadow_plan(
        snapshot_id=str(snapshot.manifest["snapshot_id"]),
        dataset_kind="synthetic_fixture",
        execution_mode="deterministic_zero",
        created_at="2026-08-23T10:01:00+08:00",
        thresholds=_thresholds(),
        user_confirmation_status="not_required",
        user_confirmation_ref=None,
        policy_versions=("shadow-policy-v1",),
    )

    def fail_publish(self: AtomicFileStore, source_relative: str, target_relative: str) -> None:
        del self, source_relative, target_relative
        raise OSError("simulated rename failure")

    monkeypatch.setattr(AtomicFileStore, "rename_directory_new", fail_publish)
    with pytest.raises(OSError, match="simulated"):
        run_shadow_evaluation(
            plan=plan,
            snapshot=snapshot,
            observations=_observations(),
            output_root=snapshot.path.parent,
            finished_at="2026-08-23T10:03:00+08:00",
        )
    staging = next((snapshot.path.parent / "shadow-runs/staging").iterdir())
    assert staging.stat().st_mode & 0o200
    assert all(path.stat().st_mode & 0o200 for path in staging.rglob("*") if path.is_file())


def test_source_tree_remains_byte_and_mode_identical_after_shadow_run(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    source_file = tmp_path / "source-synthetic_fixture/record.md"
    before = (source_file.read_bytes(), source_file.stat().st_mode)
    output = snapshot.path.parent
    plan = build_shadow_plan(
        snapshot_id=str(snapshot.manifest["snapshot_id"]),
        dataset_kind="synthetic_fixture",
        execution_mode="deterministic_zero",
        created_at="2026-08-23T10:01:00+08:00",
        thresholds=_thresholds(),
        user_confirmation_status="not_required",
        user_confirmation_ref=None,
        policy_versions=("shadow-policy-v1",),
    )
    run_shadow_evaluation(
        plan=plan,
        snapshot=snapshot,
        observations=_observations(),
        output_root=output,
        finished_at="2026-08-23T10:03:00+08:00",
    )
    assert (source_file.read_bytes(), source_file.stat().st_mode) == before


def test_shadow_run_rejects_an_output_root_outside_snapshot_workspace(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    other_output = _directory(tmp_path / "other-output")
    plan = build_shadow_plan(
        snapshot_id=str(snapshot.manifest["snapshot_id"]),
        dataset_kind="synthetic_fixture",
        execution_mode="deterministic_zero",
        created_at="2026-08-23T10:01:00+08:00",
        thresholds=_thresholds(),
        user_confirmation_status="not_required",
        user_confirmation_ref=None,
        policy_versions=("shadow-policy-v1",),
    )
    with pytest.raises(ContractError) as raised:
        run_shadow_evaluation(
            plan=plan,
            snapshot=snapshot,
            observations=_observations(),
            output_root=other_output,
            finished_at="2026-08-23T10:03:00+08:00",
        )
    assert raised.value.kind == "path"
    assert list(other_output.iterdir()) == []
