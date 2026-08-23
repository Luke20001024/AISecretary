from __future__ import annotations

import copy
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from memento_backend.interfaces import V1ProjectionPair, adapt_v2_bundle_to_v1
from memento_backend.projections import ProjectionBundle, ProjectionBundleError, build_projection_bundle
from memento_backend.projections.common import ProjectionInputs

from tests.fixtures.formal_20d import SHA_USER_ACTION, formal_20d_inputs


def test_adapter_passes_the_frozen_javascript_projection_pair_validator() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(), as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )
    pair = adapt_v2_bundle_to_v1(
        bundle, user_action_watermark_sha256=SHA_USER_ACTION
    )
    payload = json.dumps({
        "home": pair.home,
        "landscape": pair.landscape,
        "landscape_sha256": pair.landscape_sha256,
        "authority": pair.authority,
    }, ensure_ascii=False)
    repository_root = Path(__file__).resolve().parents[3]
    script = """
const fs = require('fs');
const lib = require('./chrome-newtab/cognitive-home-library.js');
const value = JSON.parse(fs.readFileSync(0, 'utf8'));
lib.validateProjectionPair(value.home, value.landscape, value.landscape_sha256);
lib.validateProjectionAuthority(value.home, value.landscape, value.authority);
process.stdout.write('validated');
"""
    result = subprocess.run(
        ["node", "-e", script], input=payload, text=True, capture_output=True,
        cwd=repository_root, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "validated"


def test_adapter_keeps_theme_and_self_identifiers_separate() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(), as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )
    pair = adapt_v2_bundle_to_v1(
        bundle, user_action_watermark_sha256=SHA_USER_ACTION
    )
    assert all(peak["understanding_ref"]["id"].startswith("mem_") for peak in pair.landscape["peaks"])
    assert pair.portrait[0]["id"].startswith("portrait_")
    assert pair.portrait[0]["synthetic"] is False


def test_adapter_validates_empty_and_historical_bundles() -> None:
    bundles = (
        build_projection_bundle(
            ProjectionInputs(), as_of="2026-07-30", generated_at="2026-07-30T22:00:00+08:00"
        ),
        build_projection_bundle(
            formal_20d_inputs(), as_of="2026-07-30", generated_at="2026-07-30T22:00:00+08:00"
        ),
    )
    for bundle in bundles:
        pair = adapt_v2_bundle_to_v1(
            bundle, user_action_watermark_sha256=SHA_USER_ACTION
        )
        _validate_with_javascript(pair)


def test_adapter_validates_multiple_today_records_with_mixed_processing_states() -> None:
    inputs = formal_20d_inputs()
    extra = copy.deepcopy(dict(inputs.source_records[-1]))
    extra["record_id"] = "rec_ffffffffffffffffffffffff"
    extra["created_at"] = "2026-08-18T10:00:01+08:00"
    extra["captured_at"] = "2026-08-18T10:00:00+08:00"
    extra["line_start"] = 40
    extra["line_end"] = 41
    bundle = build_projection_bundle(
        replace(inputs, source_records=inputs.source_records + (extra,)),
        as_of="2026-08-18",
        generated_at="2026-08-18T22:00:00+08:00",
    )
    pair = adapt_v2_bundle_to_v1(
        bundle, user_action_watermark_sha256=SHA_USER_ACTION
    )

    assert pair.home["today_status"]["saved"] == 2
    assert {record["status"] for record in pair.home["records"]} == {"merged", "raw_saved"}
    assert len(pair.authority["today_record_refs"]) == 2
    _validate_with_javascript(pair)


def test_adapter_binds_the_explicit_action_watermark_instead_of_the_bundle_input_hash() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(), as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )
    pair = adapt_v2_bundle_to_v1(
        bundle, user_action_watermark_sha256=SHA_USER_ACTION
    )

    assert SHA_USER_ACTION != bundle.manifest["input_sha256"]
    assert pair.home["input_hashes"]["user_action_watermark_sha256"] == SHA_USER_ACTION
    assert pair.landscape["input_hashes"]["user_action_watermark_sha256"] == SHA_USER_ACTION
    assert pair.authority["user_action_watermark_sha256"] == SHA_USER_ACTION
    _validate_with_javascript(pair)


def test_adapter_rejects_a_bundle_that_no_longer_matches_its_manifest() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(), as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )
    projections = copy.deepcopy(dict(bundle.projections))
    home = copy.deepcopy(dict(projections["projections/home.json"]))
    home["warnings"] = ["tampered"]
    projections["projections/home.json"] = home

    with pytest.raises(ProjectionBundleError, match="manifest hash"):
        adapt_v2_bundle_to_v1(
            ProjectionBundle(manifest=bundle.manifest, projections=projections),
            user_action_watermark_sha256=SHA_USER_ACTION,
        )


def _validate_with_javascript(pair: V1ProjectionPair) -> None:
    payload = json.dumps({
        "home": pair.home,
        "landscape": pair.landscape,
        "landscape_sha256": pair.landscape_sha256,
        "authority": pair.authority,
    }, ensure_ascii=False)
    repository_root = Path(__file__).resolve().parents[3]
    script = """
const fs = require('fs');
const lib = require('./chrome-newtab/cognitive-home-library.js');
const value = JSON.parse(fs.readFileSync(0, 'utf8'));
lib.validateProjectionPair(value.home, value.landscape, value.landscape_sha256);
lib.validateProjectionAuthority(value.home, value.landscape, value.authority);
"""
    result = subprocess.run(
        ["node", "-e", script], input=payload, text=True, capture_output=True,
        cwd=repository_root, check=False,
    )
    assert result.returncode == 0, result.stderr
