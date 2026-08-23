from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Callable, cast

import pytest

from memento_backend.evaluation.shadow_preflight import preflight_real_shadow


BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONSENT_TEMPLATE = BACKEND_ROOT / "eval/consent-template.json"


def _draft(source: Path) -> dict[str, Any]:
    value = json.loads(CONSENT_TEMPLATE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    value["dataset_scope"]["source_label"] = "real-shadow-preflight-fixture"
    value["dataset_scope"]["source_root"] = str(source.resolve())
    value["provider"]["provider"] = "provider-x"
    value["provider"]["model"] = "model-y"
    return cast(dict[str, Any], value)


def _cases(path: str = "record.md", count: int = 12) -> dict[str, Any]:
    values = []
    for index in range(count):
        values.append(
            {
                "case_id": f"case-{index + 1:02d}",
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
        )
    return {"cases": values}


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    (source / "record.md").write_text("content preflight must not read\n", encoding="utf-8")
    (source / "ignored.pdf").write_bytes(b"ignored")
    return source


def _run(
    draft: dict[str, Any], source: Path, cases: dict[str, Any]
) -> dict[str, Any]:
    return preflight_real_shadow(
        reviewed_draft=draft,
        source_root=source,
        case_draft=cases,
        validation_at="2026-08-23T12:00:00+08:00",
    )


def _failed_check(result: dict[str, Any], name: str) -> bool:
    return any(
        check["name"] == name and check["status"] == "failed"
        for check in result["checks"]
    )


def test_preflight_reports_ready_without_content_reads_or_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    draft = _draft(source)
    cases = _cases()

    def forbidden_content_read(*args: object, **kwargs: object) -> str:
        raise AssertionError("source file content was read")

    monkeypatch.setattr(Path, "read_bytes", forbidden_content_read)
    monkeypatch.setattr(Path, "read_text", forbidden_content_read)
    result = _run(draft, source, cases)

    assert result["configuration_ready"] is True
    assert result["authorization_issued"] is False
    assert result["ready_for"] == "explicit_user_confirmation"
    assert result["summary"]["eligible_file_count"] == 1
    assert result["summary"]["excluded_file_count"] == 1
    assert result["summary"]["case_count"] == 12
    assert result["summary"]["stop_case_count"] == 3
    assert result["side_effects"] == {
        "source_content_read": False,
        "provider_called": False,
        "snapshot_created": False,
        "files_written": 0,
        "real_vault_written": False,
    }


@pytest.mark.parametrize("count", [11, 16])
def test_preflight_rejects_case_count_outside_real_range(
    tmp_path: Path, count: int
) -> None:
    source = _source(tmp_path)
    result = _run(_draft(source), source, _cases(count=count))
    assert result["configuration_ready"] is False
    assert _failed_check(result, "case_draft")
    assert "12--15" in result["blocking_errors"][-1]["message"]


@pytest.mark.parametrize(
    "mutate, message",
    [
        (
            lambda cases: cases["cases"][1].__setitem__(
                "case_id", cases["cases"][0]["case_id"]
            ),
            "duplicate case id",
        ),
        (
            lambda cases: cases["cases"][0].__setitem__("input_paths", ["../record.md"]),
            "vault-relative POSIX path",
        ),
        (
            lambda cases: cases["cases"][0].__setitem__("input_paths", ["missing.md"]),
            "absent from eligible source",
        ),
    ],
)
def test_preflight_rejects_invalid_case_bindings(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    source = _source(tmp_path)
    cases = _cases()
    mutate(cases)
    result = _run(_draft(source), source, cases)
    assert result["configuration_ready"] is False
    assert message in result["blocking_errors"][-1]["message"]


def test_preflight_rejects_source_mismatch_and_symlink(tmp_path: Path) -> None:
    source = _source(tmp_path)
    another = tmp_path / "another"
    another.mkdir(mode=0o700)
    (another / "record.md").write_text("other", encoding="utf-8")
    mismatch = _run(_draft(source), another, _cases())
    assert _failed_check(mismatch, "source_metadata")
    assert any("differs" in item["message"] for item in mismatch["blocking_errors"])

    alias = tmp_path / "source-alias"
    alias.symlink_to(source, target_is_directory=True)
    symlink = _run(_draft(source), alias, _cases())
    assert _failed_check(symlink, "source_metadata")
    assert any("real directory" in item["message"] for item in symlink["blocking_errors"])


@pytest.mark.parametrize(
    "field, value",
    [("provider", "TODO"), ("model", "placeholder-model")],
)
def test_preflight_rejects_provider_placeholders(
    tmp_path: Path, field: str, value: str
) -> None:
    source = _source(tmp_path)
    draft = _draft(source)
    draft["provider"][field] = value
    result = _run(draft, source, _cases())
    assert _failed_check(result, "consent_candidate")
    assert "placeholder" in result["blocking_errors"][0]["message"]


def test_preflight_rejects_budget_gate_drift(tmp_path: Path) -> None:
    source = _source(tmp_path)
    draft = _draft(source)
    draft["provider"]["budget"]["max_cost_usd"] = 0.6
    result = _run(draft, source, _cases())
    assert _failed_check(result, "consent_candidate")
    assert "cost" in result["blocking_errors"][0]["message"]


def test_preflight_rejects_a_draft_that_claims_to_be_confirmed(tmp_path: Path) -> None:
    source = _source(tmp_path)
    draft = _draft(source)
    draft["status"] = "confirmed"
    result = _run(draft, source, _cases())
    assert _failed_check(result, "consent_candidate")
    assert "pending" in result["blocking_errors"][0]["message"]


def test_preflight_rejects_zero_quality_denominator(tmp_path: Path) -> None:
    source = _source(tmp_path)
    cases = deepcopy(_cases())
    for case in cases["cases"]:
        case["checks"]["adapter_checks"] = 0
    result = _run(_draft(source), source, cases)
    assert _failed_check(result, "case_draft")
    assert "adapter_checks" in result["blocking_errors"][-1]["message"]
