from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from memento_backend.contracts.validator import validate_contract


BACKEND_ROOT = Path(__file__).resolve().parents[2]
CLI = BACKEND_ROOT / "eval/run_shadow.py"
TEMPLATE = BACKEND_ROOT / "eval/consent-template.json"


def _run(*args: str) -> dict[str, Any]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(BACKEND_ROOT / "src")
    completed = subprocess.run(
        [sys.executable, str(CLI), *args],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    value = json.loads(completed.stdout)
    assert isinstance(value, dict)
    return value


def _real_case_draft(path: str, count: int = 12) -> dict[str, Any]:
    return {
        "cases": [
            {
                "case_id": f"real-case-{index + 1:02d}",
                "input_paths": [path],
                "expected_links": [f"theme:{index % 3}"],
                "allowed_inferences": [f"insight:{index % 2}"],
                "should_stop": index < 3,
                "checks": {
                    "source_reference_checks": 1,
                    "self_traceability_checks": 1,
                    "resource_opinion_checks": 1,
                    "adapter_checks": 1,
                    "source_hash_checks": 1,
                },
            }
            for index in range(count)
        ]
    }


def test_cli_preflight_reports_configuration_ready_without_issuing_consent(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    record = source / "record.md"
    record.write_text("metadata-only preflight\n", encoding="utf-8")
    original_bytes = record.read_bytes()

    draft = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    draft["dataset_scope"]["source_label"] = "preflight-cli-fixture"
    draft["dataset_scope"]["source_root"] = str(source.resolve())
    draft["provider"]["provider"] = "provider-x"
    draft["provider"]["model"] = "model-y"
    reviewed = private / "reviewed.json"
    reviewed.write_text(json.dumps(draft), encoding="utf-8")
    cases_path = private / "cases.json"
    cases_path.write_text(json.dumps(_real_case_draft("record.md")), encoding="utf-8")

    result = _run(
        "preflight",
        "--reviewed-draft", str(reviewed),
        "--source", str(source),
        "--cases", str(cases_path),
        "--validation-at", "2026-08-23T12:00:00+08:00",
    )

    assert result["configuration_ready"] is True
    assert result["authorization_issued"] is False
    assert result["side_effects"]["source_content_read"] is False
    assert record.read_bytes() == original_bytes
    assert set(private.iterdir()) == {reviewed, cases_path}


def test_cli_seals_reviewed_consent_and_binds_snapshot(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    draft = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    (source / "record.md").write_text("temporary synthetic bytes\n", encoding="utf-8")
    draft["dataset_scope"]["source_label"] = "temporary-contract-fixture"
    draft["dataset_scope"]["source_root"] = str(source.resolve())
    draft["provider"]["provider"] = "provider-x"
    draft["provider"]["model"] = "model-y"
    reviewed = private / "reviewed.json"
    reviewed.write_text(json.dumps(draft), encoding="utf-8")
    consent_path = private / "consent.json"

    consent = _run(
        "consent",
        "--reviewed-draft", str(reviewed),
        "--confirmed-at", "2026-08-23T12:00:00+08:00",
        "--output-file", str(consent_path),
    )
    validate_contract("shadow-consent-v1.schema.json", consent)

    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    snapshot = _run(
        "snapshot",
        "--source", str(source),
        "--output-root", str(output),
        "--source-label", "temporary-contract-fixture",
        "--snapshot-kind", "real_vault_snapshot",
        "--created-at", "2026-08-23T12:01:00+08:00",
        "--consent", str(consent_path),
    )
    assert snapshot["authorization_ref"] == consent["consent_id"]
    assert snapshot["source_label"] == consent["dataset_scope"]["source_label"]


def test_cli_freezes_case_set_and_binds_preregistered_plan(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    (source / "record.md").write_text("confirm boundary\n", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    snapshot = _run(
        "snapshot",
        "--source", str(source),
        "--output-root", str(output),
        "--source-label", "synthetic-cli-fixture",
        "--snapshot-kind", "synthetic_fixture",
        "--created-at", "2026-08-23T12:00:00+08:00",
    )
    case_draft = {
        "cases": [{
            "case_id": "boundary-case",
            "input_paths": ["record.md"],
            "expected_links": ["theme:boundary"],
            "allowed_inferences": ["insight:confirm-first"],
            "should_stop": True,
            "checks": {
                "source_reference_checks": 1,
                "self_traceability_checks": 1,
                "resource_opinion_checks": 1,
                "adapter_checks": 1,
                "source_hash_checks": 1,
            },
        }]
    }
    draft_path = private / "cases.json"
    draft_path.write_text(json.dumps(case_draft), encoding="utf-8")
    case_set_path = private / "case-set.json"
    case_set = _run(
        "case-set",
        "--snapshot-root", str(output / snapshot["snapshot_id"]),
        "--cases", str(draft_path),
        "--created-at", "2026-08-23T12:01:00+08:00",
        "--output-file", str(case_set_path),
    )
    validate_contract("shadow-case-set-v1.schema.json", case_set)

    thresholds = json.loads(
        (BACKEND_ROOT / "eval/preregistration-template.json").read_text(encoding="utf-8")
    )
    thresholds_path = private / "thresholds.json"
    thresholds_path.write_text(json.dumps(thresholds), encoding="utf-8")
    plan_path = private / "plan.json"
    plan = _run(
        "plan",
        "--snapshot-id", snapshot["snapshot_id"],
        "--dataset-kind", "synthetic_fixture",
        "--execution-mode", "deterministic_zero",
        "--thresholds", str(thresholds_path),
        "--created-at", "2026-08-23T12:02:00+08:00",
        "--confirmation-status", "not_required",
        "--policy-version", "shadow-policy-v1",
        "--case-set", str(case_set_path),
        "--output-file", str(plan_path),
    )
    validate_contract("shadow-plan-v1.schema.json", plan)
    assert plan["case_set_ref"]["case_set_id"] == case_set["case_set_id"]
