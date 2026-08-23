#!/usr/bin/env python3
"""Local R9 utility for snapshots, pre-registration and isolated evaluation.

This program never invokes a provider.  It can freeze case sets and verify work
products created through the provider-neutral ``ShadowProducer`` boundary.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.ids import validate_relative_path
from memento_backend.evaluation.shadow_consent import (
    build_shadow_consent,
    validate_shadow_consent,
)
from memento_backend.evaluation.shadow_metrics import ShadowObservation
from memento_backend.evaluation.shadow_preflight import preflight_real_shadow
from memento_backend.evaluation.shadow_runner import build_shadow_plan, run_shadow_evaluation
from memento_backend.evaluation.shadow_snapshot import (
    ReadOnlySnapshot,
    create_read_only_snapshot,
    verify_read_only_snapshot,
)
from memento_backend.evaluation.shadow_worker import (
    build_shadow_case_set,
    shadow_case_set_ref,
    validate_shadow_case_set,
    validate_shadow_work_product,
)
from memento_backend.projections.bundle_projector import ProjectionBundle, validate_projection_bundle_contract


OBSERVATION_FIELDS = frozenset(ShadowObservation.__dataclass_fields__)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "preflight":
        reviewed_draft = _read_json(Path(args.reviewed_draft))
        case_draft = _read_json(Path(args.cases))
        preflight_result = preflight_real_shadow(
            reviewed_draft=reviewed_draft,
            source_root=Path(args.source),
            case_draft=case_draft,
            validation_at=args.validation_at,
        )
        print(json.dumps(preflight_result, ensure_ascii=False, sort_keys=True))
        return 0 if preflight_result["configuration_ready"] else 1
    if args.command == "consent":
        draft = _read_json(Path(args.reviewed_draft))
        formal_consent = build_shadow_consent(
            dataset_scope=_mapping(draft.get("dataset_scope"), "dataset_scope"),
            thresholds=_mapping(draft.get("thresholds"), "thresholds"),
            material_gates=_mapping(draft.get("material_gates"), "material_gates"),
            sensitivity_policy=_mapping(
                draft.get("sensitivity_policy"), "sensitivity_policy"
            ),
            agent_schedule=_mapping(draft.get("agent_schedule"), "agent_schedule"),
            provider=_mapping(draft.get("provider"), "provider"),
            confirmed_at=args.confirmed_at,
        )
        _write_new_json(Path(args.output_file), formal_consent)
        print(json.dumps(formal_consent, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "snapshot":
        snapshot_consent: Optional[dict[str, Any]] = (
            None if args.consent is None else _read_json(Path(args.consent))
        )
        authorization_ref = args.authorization_ref
        allowed_suffixes: Sequence[str] = (".json", ".md", ".txt")
        if snapshot_consent is not None:
            validate_shadow_consent(snapshot_consent)
            scope = snapshot_consent["dataset_scope"]
            if args.snapshot_kind != scope["snapshot_kind"]:
                raise ValueError("snapshot kind differs from reviewed consent")
            if args.source_label != scope["source_label"]:
                raise ValueError("snapshot source label differs from reviewed consent")
            if str(Path(args.source).resolve(strict=True)) != scope["source_root"]:
                raise ValueError("snapshot source root differs from reviewed consent")
            if (
                authorization_ref is not None
                and authorization_ref != snapshot_consent["consent_id"]
            ):
                raise ValueError("snapshot authorization differs from reviewed consent")
            authorization_ref = str(snapshot_consent["consent_id"])
            allowed_suffixes = tuple(str(item) for item in scope["allowed_suffixes"])
        snapshot = create_read_only_snapshot(
            Path(args.source),
            Path(args.output_root),
            source_label=args.source_label,
            snapshot_kind=args.snapshot_kind,
            created_at=args.created_at,
            authorization_ref=authorization_ref,
            allowed_suffixes=allowed_suffixes,
        )
        print(json.dumps(snapshot.manifest, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "case-set":
        snapshot = verify_read_only_snapshot(Path(args.snapshot_root))
        draft = _read_json(Path(args.cases))
        cases = draft.get("cases")
        if not isinstance(cases, list):
            raise ValueError("case-set draft must contain a cases array")
        frozen_case_set = build_shadow_case_set(
            snapshot=snapshot,
            created_at=args.created_at,
            cases=[_mapping(case, "case") for case in cases],
        )
        _write_new_json(Path(args.output_file), frozen_case_set)
        print(json.dumps(frozen_case_set, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "plan":
        threshold_document = _read_json(Path(args.thresholds))
        thresholds = threshold_document.get("thresholds", threshold_document)
        plan_consent: Optional[dict[str, Any]] = (
            None if args.consent is None else _read_json(Path(args.consent))
        )
        plan_case_set: Optional[dict[str, Any]] = (
            None if args.case_set is None else _read_json(Path(args.case_set))
        )
        if plan_case_set is not None:
            validate_contract("shadow-case-set-v1.schema.json", plan_case_set)
            if plan_case_set["snapshot_ref"]["snapshot_id"] != args.snapshot_id:
                raise ValueError("case set is bound to another snapshot")
        plan = build_shadow_plan(
            snapshot_id=args.snapshot_id,
            dataset_kind=args.dataset_kind,
            execution_mode=args.execution_mode,
            created_at=args.created_at,
            thresholds=_mapping(thresholds, "thresholds"),
            user_confirmation_status=args.confirmation_status,
            user_confirmation_ref=args.confirmation_ref,
            provider=args.provider,
            model=args.model,
            prompt_versions=tuple(args.prompt_version),
            policy_versions=tuple(args.policy_version),
            max_prompt_tokens=args.max_prompt_tokens,
            max_completion_tokens=args.max_completion_tokens,
            max_cost_usd=args.max_cost_usd,
            max_latency_ms=args.max_latency_ms,
            consent=plan_consent,
            case_set_ref=(
                None if plan_case_set is None else shadow_case_set_ref(plan_case_set)
            ),
        )
        _write_new_json(Path(args.output_file), plan)
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "evaluate":
        plan = _read_json(Path(args.plan))
        snapshot = verify_read_only_snapshot(Path(args.snapshot_root))
        bundle = None if args.candidate_bundle is None else _read_bundle(Path(args.candidate_bundle))
        evaluation_case_set: Optional[dict[str, Any]] = (
            None if args.case_set is None else _read_json(Path(args.case_set))
        )
        work_product = (
            None if args.work_product is None else _read_json(Path(args.work_product))
        )
        if (evaluation_case_set is None) != (work_product is None):
            raise ValueError("case set and work product must be supplied together")
        if evaluation_case_set is not None and work_product is not None:
            if bundle is None:
                raise ValueError("work product verification requires its candidate bundle")
            validate_shadow_case_set(evaluation_case_set, snapshot)
            observations = list(validate_shadow_work_product(
                work_product=work_product,
                plan=plan,
                case_set=evaluation_case_set,
                snapshot=snapshot,
                candidate_bundle=bundle,
            ))
        elif args.observations is not None:
            observations = _read_observations(Path(args.observations))
        else:
            raise ValueError("evaluate requires observations or a case-set work product")
        evaluation_consent: Optional[dict[str, Any]] = (
            None if args.consent is None else _read_json(Path(args.consent))
        )
        result = run_shadow_evaluation(
            plan=plan,
            snapshot=ReadOnlySnapshot(snapshot.path, snapshot.manifest),
            observations=observations,
            output_root=Path(args.output_root),
            finished_at=args.finished_at,
            candidate_bundle=bundle,
            baseline_bundle_sha256=args.baseline_bundle_sha256,
            baseline_snapshot_path=args.baseline_snapshot_path,
            consent=evaluation_consent,
            case_set=evaluation_case_set,
            work_product=work_product,
        )
        print(json.dumps(result.report, ensure_ascii=False, sort_keys=True))
        return 0
    parser.error("unknown command")
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Memento R9 read-only shadow runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser(
        "preflight",
        help="check real R9 configuration and source metadata without issuing consent",
    )
    preflight.add_argument("--reviewed-draft", required=True)
    preflight.add_argument("--source", required=True)
    preflight.add_argument("--cases", required=True, help="12--15 case draft JSON")
    preflight.add_argument("--validation-at", required=True)

    consent = subparsers.add_parser(
        "consent", help="seal a user-reviewed R9 consent into a deterministic contract"
    )
    consent.add_argument("--reviewed-draft", required=True)
    consent.add_argument("--confirmed-at", required=True)
    consent.add_argument("--output-file", required=True)

    snapshot = subparsers.add_parser("snapshot", help="create one sealed read-only snapshot")
    snapshot.add_argument("--source", required=True)
    snapshot.add_argument("--output-root", required=True)
    snapshot.add_argument("--source-label", required=True)
    snapshot.add_argument(
        "--snapshot-kind",
        choices=("synthetic_fixture", "anonymized_samples", "real_vault_snapshot"),
        required=True,
    )
    snapshot.add_argument("--created-at", required=True)
    snapshot.add_argument("--authorization-ref")
    snapshot.add_argument("--consent", help="confirmed shadow-consent-v1 JSON")

    case_set = subparsers.add_parser(
        "case-set", help="freeze gold labels and exact inputs against one snapshot"
    )
    case_set.add_argument("--snapshot-root", required=True)
    case_set.add_argument("--cases", required=True, help="case-set draft JSON")
    case_set.add_argument("--created-at", required=True)
    case_set.add_argument("--output-file", required=True)

    plan = subparsers.add_parser("plan", help="freeze thresholds, versions and budget")
    plan.add_argument("--snapshot-id", required=True)
    plan.add_argument(
        "--dataset-kind",
        choices=("synthetic_fixture", "anonymized_samples", "real_vault_snapshot"),
        required=True,
    )
    plan.add_argument(
        "--execution-mode", choices=("deterministic_zero", "provider_shadow"), required=True
    )
    plan.add_argument("--thresholds", required=True)
    plan.add_argument("--created-at", required=True)
    plan.add_argument(
        "--confirmation-status", choices=("not_required", "pending", "confirmed"), required=True
    )
    plan.add_argument("--confirmation-ref")
    plan.add_argument("--consent", help="confirmed shadow-consent-v1 JSON")
    plan.add_argument("--case-set", help="sealed shadow-case-set-v1 JSON")
    plan.add_argument("--provider")
    plan.add_argument("--model")
    plan.add_argument("--prompt-version", action="append", default=[])
    plan.add_argument("--policy-version", action="append", default=[])
    plan.add_argument("--max-prompt-tokens", type=int, default=0)
    plan.add_argument("--max-completion-tokens", type=int, default=0)
    plan.add_argument("--max-cost-usd", type=float, default=0.0)
    plan.add_argument("--max-latency-ms", type=int, default=0)
    plan.add_argument("--output-file", required=True)

    evaluate = subparsers.add_parser("evaluate", help="measure observations and seal a report")
    evaluate.add_argument("--plan", required=True)
    evaluate.add_argument("--snapshot-root", required=True)
    evaluate.add_argument("--observations")
    evaluate.add_argument("--output-root", required=True)
    evaluate.add_argument("--finished-at", required=True)
    evaluate.add_argument("--candidate-bundle")
    evaluate.add_argument("--baseline-bundle-sha256")
    evaluate.add_argument("--baseline-snapshot-path")
    evaluate.add_argument("--consent", help="confirmed shadow-consent-v1 JSON")
    evaluate.add_argument("--case-set", help="sealed shadow-case-set-v1 JSON")
    evaluate.add_argument("--work-product", help="sealed shadow-work-product-v1 JSON")
    return parser


def _read_observations(path: Path) -> list[ShadowObservation]:
    document = _read_json(path)
    raw_items = document.get("observations")
    if not isinstance(raw_items, list):
        raise ValueError("observations document must contain an array")
    result = []
    for raw in raw_items:
        item = _mapping(raw, "observation")
        if set(item) - OBSERVATION_FIELDS:
            raise ValueError("observation contains unknown fields")
        values = dict(item)
        for name in ("expected_links", "predicted_links", "allowed_inferences", "predicted_inferences"):
            values[name] = frozenset(values.get(name, []))
        observation = ShadowObservation(**values)
        observation.validate()
        result.append(observation)
    return result


def _read_bundle(root: Path) -> ProjectionBundle:
    resolved_root = root.resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("candidate bundle root must be a real directory")
    manifest = _read_json(resolved_root / "manifest.json")
    validate_contract("projection-bundle-v1.schema.json", manifest)
    projections = {
        str(entry["path"]): _read_bundle_projection(resolved_root, str(entry["path"]))
        for entry in manifest["entries"]
    }
    bundle = ProjectionBundle(manifest=manifest, projections=projections)
    validate_projection_bundle_contract(bundle)
    return bundle


def _read_bundle_projection(root: Path, relative: str) -> dict[str, Any]:
    validate_relative_path(relative, "candidate projection path")
    path = root.joinpath(*relative.split("/"))
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("candidate projection escapes bundle root") from exc
    return _read_json(path)


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"JSON input must be a regular file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    return dict(_mapping(value, str(path)))


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir() or parent.stat().st_mode & 0o077:
        raise ValueError("plan output parent must be an existing owner-only directory")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags, 0o600)
    try:
        data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
