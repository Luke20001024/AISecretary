"""Metadata-only readiness checks before issuing one real R9 consent.

The preflight deliberately does not read source file contents, invoke a
provider, create a snapshot, or persist an authorization.  It only validates
the reviewed configuration, the source tree shape, and a 12--15 case draft so
that all startup blockers can be fixed before the user seals a consent.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from memento_backend.domain.ids import sha256_bytes, validate_relative_path

from .shadow_consent import build_shadow_consent
from .shadow_snapshot import DEFAULT_EXCLUDED_DIRECTORIES
from .shadow_worker import CHECK_FIELDS


CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
CASE_FIELDS = frozenset(
    {
        "case_id",
        "input_paths",
        "expected_links",
        "allowed_inferences",
        "should_stop",
        "checks",
    }
)
MIN_REAL_CASES = 12
MAX_REAL_CASES = 15


def preflight_real_shadow(
    *,
    reviewed_draft: Mapping[str, Any],
    source_root: Path,
    case_draft: Mapping[str, Any],
    validation_at: str,
) -> dict[str, Any]:
    """Return every detectable blocker without reading source content.

    ``validation_at`` is used only to exercise the timestamp and consent
    contract.  A successful result is ready for explicit user confirmation;
    it is not itself a consent and cannot authorize a snapshot or provider.
    """

    checks: list[dict[str, Any]] = []
    blocking_errors: list[dict[str, str]] = []

    consent_candidate: Optional[dict[str, Any]] = None
    try:
        _validate_draft_envelope(reviewed_draft)
        consent_candidate = build_shadow_consent(
            dataset_scope=_mapping(reviewed_draft.get("dataset_scope"), "dataset_scope"),
            thresholds=_mapping(reviewed_draft.get("thresholds"), "thresholds"),
            material_gates=_mapping(
                reviewed_draft.get("material_gates"), "material_gates"
            ),
            sensitivity_policy=_mapping(
                reviewed_draft.get("sensitivity_policy"), "sensitivity_policy"
            ),
            agent_schedule=_mapping(
                reviewed_draft.get("agent_schedule"), "agent_schedule"
            ),
            provider=_mapping(reviewed_draft.get("provider"), "provider"),
            confirmed_at=validation_at,
        )
    except (KeyError, OSError, ValueError, TypeError) as exc:
        _failed(checks, blocking_errors, "consent_candidate", str(exc))
    else:
        _passed(
            checks,
            "consent_candidate",
            "configuration satisfies the frozen consent contract",
        )

    resolved_source: Optional[Path] = None
    eligible_paths: set[str] = set()
    eligible_file_count = 0
    eligible_total_bytes = 0
    excluded_file_count = 0
    suffixes: tuple[str, ...] = ()
    scope = reviewed_draft.get("dataset_scope")
    if isinstance(scope, Mapping):
        suffixes = _draft_suffixes(scope.get("allowed_suffixes"))
    try:
        resolved_source = _real_source_directory(source_root)
        if not isinstance(scope, Mapping):
            raise ValueError("dataset_scope must be an object")
        configured_root = scope.get("source_root")
        if configured_root != str(resolved_source):
            raise ValueError("source path differs from the reviewed dataset scope")
        if not suffixes:
            raise ValueError("allowed suffixes are invalid")
        (
            eligible_paths,
            eligible_file_count,
            eligible_total_bytes,
            excluded_file_count,
        ) = _scan_source_metadata(resolved_source, suffixes)
        if eligible_file_count == 0:
            raise ValueError("source contains no eligible files")
    except (OSError, ValueError) as exc:
        _failed(checks, blocking_errors, "source_metadata", str(exc))
    else:
        _passed(
            checks,
            "source_metadata",
            f"{eligible_file_count} eligible files can be snapshotted after consent",
        )

    case_count = 0
    stop_case_count = 0
    try:
        cases = case_draft.get("cases")
        if not isinstance(cases, list):
            raise ValueError("case draft must contain a cases array")
        case_count = len(cases)
        if not MIN_REAL_CASES <= case_count <= MAX_REAL_CASES:
            raise ValueError(
                f"real shadow case count must be {MIN_REAL_CASES}--{MAX_REAL_CASES}"
            )
        if resolved_source is None or not eligible_paths:
            raise ValueError("case inputs cannot be checked until source metadata passes")
        case_ids: set[str] = set()
        expected_link_count = 0
        allowed_inference_count = 0
        check_totals = {field: 0 for field in CHECK_FIELDS}
        for index, raw_case in enumerate(cases):
            case = _mapping(raw_case, f"cases[{index}]")
            should_stop = _validate_case_draft(
                case, index=index, eligible_paths=eligible_paths, seen_ids=case_ids
            )
            stop_case_count += int(should_stop)
            expected_link_count += len(case["expected_links"])
            allowed_inference_count += len(case["allowed_inferences"])
            for field in CHECK_FIELDS:
                check_totals[field] += int(case["checks"][field])
        threshold_document = reviewed_draft.get("thresholds")
        thresholds = _mapping(threshold_document, "thresholds")
        stop_case_minimum = thresholds.get("stop_case_count_min")
        if type(stop_case_minimum) is not int:
            raise ValueError("stop_case_count_min must be an integer")
        if stop_case_count < stop_case_minimum:
            raise ValueError(
                "case draft has fewer positive stop cases than stop_case_count_min"
            )
        if stop_case_count == case_count:
            raise ValueError("case draft requires at least one non-stop control case")
        if expected_link_count == 0:
            raise ValueError("case draft requires expected links for link-quality scoring")
        if allowed_inference_count == 0:
            raise ValueError("case draft requires allowed inferences for inference scoring")
        missing_denominators = [
            field for field, total in check_totals.items() if total == 0
        ]
        if missing_denominators:
            raise ValueError(
                "case draft has zero quality denominators: "
                + ", ".join(missing_denominators)
            )
    except (KeyError, OSError, ValueError, TypeError) as exc:
        _failed(checks, blocking_errors, "case_draft", str(exc))
    else:
        _passed(
            checks,
            "case_draft",
            f"{case_count} cases include {stop_case_count} stop cases and bounded inputs",
        )

    configuration_ready = not blocking_errors
    source_root_sha256 = (
        None
        if resolved_source is None
        else sha256_bytes(str(resolved_source).encode("utf-8"))
    )
    return {
        "kind": "memento_shadow_preflight",
        "configuration_ready": configuration_ready,
        "authorization_issued": False,
        "ready_for": (
            "explicit_user_confirmation"
            if configuration_ready
            else "configuration_correction"
        ),
        "checks": checks,
        "blocking_errors": blocking_errors,
        "summary": {
            "eligible_file_count": eligible_file_count,
            "eligible_total_bytes": eligible_total_bytes,
            "excluded_file_count": excluded_file_count,
            "case_count": case_count,
            "stop_case_count": stop_case_count,
            "source_root_sha256": source_root_sha256,
            "consent_contract_preview_valid": consent_candidate is not None,
        },
        "side_effects": {
            "source_content_read": False,
            "provider_called": False,
            "snapshot_created": False,
            "files_written": 0,
            "real_vault_written": False,
        },
    }


def _validate_case_draft(
    case: Mapping[str, Any],
    *,
    index: int,
    eligible_paths: set[str],
    seen_ids: set[str],
) -> bool:
    if set(case) != CASE_FIELDS:
        raise ValueError(f"cases[{index}] fields are invalid")
    case_id = case.get("case_id")
    if type(case_id) is not str or CASE_ID_PATTERN.fullmatch(case_id) is None:
        raise ValueError(f"cases[{index}].case_id is invalid")
    if case_id in seen_ids:
        raise ValueError(f"duplicate case id: {case_id}")
    seen_ids.add(case_id)

    raw_paths = case.get("input_paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ValueError(f"cases[{index}].input_paths must be a non-empty array")
    paths: list[str] = []
    for raw_path in raw_paths:
        path = validate_relative_path(raw_path, f"cases[{index}].input_path")
        if path not in eligible_paths:
            raise ValueError(f"cases[{index}] input is absent from eligible source: {path}")
        paths.append(path)
    if len(paths) != len(set(paths)):
        raise ValueError(f"cases[{index}].input_paths must be unique")

    _validate_labels(case.get("expected_links"), f"cases[{index}].expected_links")
    _validate_labels(
        case.get("allowed_inferences"), f"cases[{index}].allowed_inferences"
    )
    should_stop = case.get("should_stop")
    if type(should_stop) is not bool:
        raise ValueError(f"cases[{index}].should_stop must be a strict boolean")
    checks = case.get("checks")
    if not isinstance(checks, Mapping) or set(checks) != set(CHECK_FIELDS):
        raise ValueError(f"cases[{index}].checks fields are invalid")
    for field in CHECK_FIELDS:
        value = checks[field]
        if type(value) is not int or value < 0:
            raise ValueError(f"cases[{index}].checks.{field} must be non-negative")
    return should_stop


def _validate_labels(value: Any, name: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    if any(type(item) is not str or not item.strip() or len(item) > 240 for item in value):
        raise ValueError(f"{name} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{name} must be unique")


def _scan_source_metadata(
    root: Path, suffixes: Sequence[str]
) -> tuple[set[str], int, int, int]:
    allowed = frozenset(suffixes)
    excluded_directories = frozenset(DEFAULT_EXCLUDED_DIRECTORIES)
    eligible_paths: set[str] = set()
    total_bytes = 0
    excluded_count = 0
    for path in sorted(root.rglob("*")):
        relative_path = path.relative_to(root)
        if any(part in excluded_directories for part in relative_path.parts[:-1]):
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("source contains a symlink")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("source contains a special file")
        if path.suffix.lower() not in allowed:
            excluded_count += 1
            continue
        if stat.S_IMODE(info.st_mode) & 0o444 == 0:
            raise ValueError("eligible source file has no read permission bits")
        relative = validate_relative_path(relative_path.as_posix())
        eligible_paths.add(relative)
        total_bytes += int(info.st_size)
    return eligible_paths, len(eligible_paths), total_bytes, excluded_count


def _real_source_directory(path: Path) -> Path:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError("source directory does not exist") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("source must be a real directory")
    if info.st_uid != os.getuid():
        raise ValueError("source directory has a foreign owner")
    return path.resolve(strict=True)


def _draft_suffixes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or any(type(item) is not str for item in value):
        return ()
    suffixes = tuple(sorted(set(value)))
    if not suffixes or any(not item.startswith(".") or "/" in item for item in suffixes):
        return ()
    return suffixes


def _validate_draft_envelope(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != "1.0":
        raise ValueError("reviewed draft schema_version must be 1.0")
    if value.get("kind") != "memento_shadow_consent_draft":
        raise ValueError("reviewed draft kind is invalid")
    if value.get("status") != "pending_user_confirmation":
        raise ValueError("preflight requires a pending, unsigned consent draft")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _passed(checks: list[dict[str, Any]], name: str, detail: str) -> None:
    checks.append({"name": name, "status": "passed", "detail": detail})


def _failed(
    checks: list[dict[str, Any]],
    blocking_errors: list[dict[str, str]],
    name: str,
    detail: str,
) -> None:
    checks.append({"name": name, "status": "failed", "detail": detail})
    blocking_errors.append({"check": name, "message": detail})
