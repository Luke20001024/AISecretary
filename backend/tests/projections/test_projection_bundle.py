from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any, Dict, cast

import pytest

from memento_backend.contracts.validator import ContractValidationError, validate_contract
from memento_backend.domain.ids import sha256_bytes, sha256_json
from memento_backend.projections import (
    ProjectionBundle,
    ProjectionBundleError,
    build_projection_bundle,
    validate_projection_bundle_contract,
)
from memento_backend.projections.common import ProjectionInputError

from tests.fixtures.formal_20d import formal_20d_inputs
from tests.fixtures.projection_states import home_state_fixtures


def test_formal_20d_fixture_passes_all_object_contracts() -> None:
    inputs = formal_20d_inputs()
    groups = (
        ("source-record-v2.schema.json", inputs.source_records),
        ("record-interpretation-v2.schema.json", inputs.interpretations),
        ("memory-atom-v2.schema.json", inputs.memory_atoms),
        ("relation-v2.schema.json", inputs.relations),
        ("theme-v2.schema.json", inputs.themes),
        ("self-insight-v2.schema.json", inputs.self_insights),
        ("resource-card-v1.schema.json", inputs.resource_cards),
        ("read-later-intent-v1.schema.json", inputs.read_later_intents),
    )
    for schema_name, values in groups:
        for value in values:
            validate_contract(schema_name, value)


def test_bundle_is_deterministic_and_manifest_hashes_every_projection() -> None:
    inputs = formal_20d_inputs()
    reversed_inputs = replace(
        inputs,
        source_records=tuple(reversed(inputs.source_records)),
        interpretations=tuple(reversed(inputs.interpretations)),
        memory_atoms=tuple(reversed(inputs.memory_atoms)),
        relations=tuple(reversed(inputs.relations)),
        themes=tuple(reversed(inputs.themes)),
    )
    first = build_projection_bundle(inputs, as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00")
    second = build_projection_bundle(reversed_inputs, as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00")

    assert first.bundle_sha256 == second.bundle_sha256
    assert first.manifest == second.manifest
    assert first.projections == second.projections
    assert {entry["path"] for entry in first.manifest["entries"]} == set(first.projections)
    for entry in first.manifest["entries"]:
        assert entry["sha256"] == sha256_json(first.projection(entry["path"]))


def test_bundle_stays_deterministic_with_equal_timestamps_and_multiple_candidates() -> None:
    inputs = formal_20d_inputs()
    extra_interpretation = copy.deepcopy(dict(inputs.interpretations[0]))
    extra_interpretation["interpretation_id"] = "int_ffffffffffffffffffffffff"
    extra_interpretation["summary"] = "同一记录的更新解释"

    extra_resource = copy.deepcopy(dict(inputs.resource_cards[0]))
    extra_resource["resource_id"] = "res_ffffffffffffffffffffffff"
    extra_resource["title"] = "同一时刻保存的另一份资料"

    later_intent = copy.deepcopy(dict(inputs.read_later_intents[0]))
    later_intent["intent_id"] = "rli_ffffffffffffffffffffffff"
    later_intent["revision"] = 2
    later_intent["previous_revision_sha256"] = "e" * 64
    later_intent["status"] = "completed"
    later_intent["operation"] = "complete"

    crowded = replace(
        inputs,
        interpretations=inputs.interpretations + (extra_interpretation,),
        resource_cards=inputs.resource_cards + (extra_resource,),
        read_later_intents=inputs.read_later_intents + (later_intent,),
    )
    reversed_inputs = replace(
        crowded,
        interpretations=tuple(reversed(crowded.interpretations)),
        resource_cards=tuple(reversed(crowded.resource_cards)),
        read_later_intents=tuple(reversed(crowded.read_later_intents)),
    )
    first = build_projection_bundle(
        crowded, as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )
    second = build_projection_bundle(
        reversed_inputs, as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )

    assert first.bundle_sha256 == second.bundle_sha256
    assert first.projections == second.projections
    assert first.projection("projections/home.json")["resource_entries"][0]["resource_ref"]["id"] == extra_resource["resource_id"]
    assert first.projection("projections/home.json")["resource_entries"][1]["intent_status"] == "completed"


def test_latest_interpretation_is_ordered_by_instant_across_timezones() -> None:
    inputs = formal_20d_inputs()
    instant_later = copy.deepcopy(dict(inputs.interpretations[0]))
    instant_later["interpretation_id"] = "int_eeeeeeeeeeeeeeeeeeeeeeee"
    instant_later["summary"] = "真实时间更晚的解释"
    instant_later["created_at"] = "2026-08-18T23:30:00-05:00"

    calendar_text_later = copy.deepcopy(dict(inputs.interpretations[0]))
    calendar_text_later["interpretation_id"] = "int_ffffffffffffffffffffffff"
    calendar_text_later["summary"] = "字符串日期更晚但真实时间更早"
    calendar_text_later["created_at"] = "2026-08-19T01:00:00+08:00"

    mixed = replace(
        inputs,
        interpretations=inputs.interpretations
        + (instant_later, calendar_text_later),
    )
    bundle = build_projection_bundle(
        mixed,
        as_of="2026-08-19",
        generated_at="2026-08-19T22:00:00+08:00",
    )
    timeline_entry = next(
        item
        for item in bundle.projection("projections/timeline.json")["entries"]
        if item["record_ref"]["id"] == inputs.source_records[0]["record_id"]
    )
    record_detail = bundle.projection(
        "projections/details/record/"
        + str(inputs.source_records[0]["record_id"])
        + ".json"
    )

    assert timeline_entry["interpretation_ref"]["id"] == instant_later["interpretation_id"]
    assert record_detail["interpretation"]["summary"] == "真实时间更晚的解释"


def test_bundle_identity_changes_when_release_chain_metadata_changes() -> None:
    inputs = formal_20d_inputs()
    first = build_projection_bundle(
        inputs, as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )
    chained = build_projection_bundle(
        inputs,
        as_of="2026-08-18",
        generated_at="2026-08-18T22:00:00+08:00",
        previous_bundle_sha256=first.bundle_sha256,
        previous_landscape_sha256=sha256_json(first.projection("projections/landscape.json")),
    )

    assert first.manifest["bundle_id"] != chained.manifest["bundle_id"]
    assert first.bundle_sha256 != chained.bundle_sha256
    assert first.projection("projections/landscape.json")["projection_id"] != chained.projection("projections/landscape.json")["projection_id"]


def test_timeline_covers_twenty_days_and_detail_index_is_back_traceable() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(), as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )
    timeline = bundle.projection("projections/timeline.json")
    detail_index = bundle.projection("projections/detail-index.json")

    assert timeline["range"] == {"start": "2026-07-30", "end": "2026-08-18", "days": 20}
    assert len(timeline["entries"]) == 6
    for entry in detail_index["entries"]:
        detail = bundle.projection(entry["path"])
        assert detail["projection_id"] == entry["projection_id"]
        assert sha256_json(detail) == entry["sha256"]
    resource_detail = next(
        bundle.projection(entry["path"])
        for entry in detail_index["entries"]
        if entry["detail_kind"] == "resource"
    )
    assert resource_detail["local_asset_refs"] == ["assets/fixture-page.png"]


def test_empty_loading_stale_conflict_and_failed_preserved_states_are_fixed_contracts() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(), as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )
    fixtures = home_state_fixtures(bundle)
    assert set(fixtures) == {"empty", "loading", "stale", "conflict", "failed_preserved"}
    assert fixtures["empty"]["today_status"]["saved"] == 0
    for value in fixtures.values():
        validate_contract("home-projection-v2.schema.json", value)


def test_historical_as_of_replay_does_not_include_future_formal_objects() -> None:
    inputs = formal_20d_inputs()
    day_one = build_projection_bundle(
        inputs, as_of="2026-07-30", generated_at="2026-07-30T22:00:00+08:00"
    )
    day_five = build_projection_bundle(
        inputs, as_of="2026-08-03", generated_at="2026-08-03T22:00:00+08:00"
    )
    final = build_projection_bundle(
        inputs, as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )

    assert len(day_one.projection("projections/timeline.json")["entries"]) == 1
    assert len(day_one.projection("projections/landscape.json")["peaks"]) == 0
    assert day_one.projection("projections/self.json")["primary_insight"] is None
    assert len(day_five.projection("projections/timeline.json")["entries"]) == 2
    assert len(day_five.projection("projections/landscape.json")["peaks"]) == 1
    assert len(final.projection("projections/landscape.json")["peaks"]) == 3
    assert final.projection("projections/self.json")["primary_insight"] is not None
    assert len({day_one.manifest["input_sha256"], day_five.manifest["input_sha256"], final.manifest["input_sha256"]}) == 3


def test_atomic_validator_rejects_a_stale_home_reference() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(), as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )
    projections = cast(
        Dict[str, Dict[str, Any]], copy.deepcopy(dict(bundle.projections))
    )
    projections["projections/home.json"]["landscape_ref"]["sha256"] = "f" * 64
    tampered = ProjectionBundle(manifest=bundle.manifest, projections=projections)
    with pytest.raises(ProjectionBundleError, match="manifest hash|reference is stale"):
        validate_projection_bundle_contract(tampered)


def test_bundle_validator_rejects_a_forged_deterministic_bundle_identity() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(),
        as_of="2026-08-18",
        generated_at="2026-08-18T22:00:00+08:00",
    )
    manifest = copy.deepcopy(dict(bundle.manifest))
    projections = cast(
        Dict[str, Dict[str, Any]], copy.deepcopy(dict(bundle.projections))
    )
    manifest["bundle_id"] = "prjb_ffffffffffffffffffffffff"
    for projection in projections.values():
        projection["bundle_id"] = manifest["bundle_id"]
    _reseal_bundle(manifest, projections)

    with pytest.raises(ProjectionBundleError, match="deterministic identity"):
        validate_projection_bundle_contract(
            ProjectionBundle(manifest=manifest, projections=projections)
        )


def test_bundle_validator_rejects_an_orphan_self_theme_reference() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(),
        as_of="2026-08-18",
        generated_at="2026-08-18T22:00:00+08:00",
    )
    manifest = copy.deepcopy(dict(bundle.manifest))
    projections = cast(
        Dict[str, Dict[str, Any]], copy.deepcopy(dict(bundle.projections))
    )
    self_projection = projections["projections/self.json"]
    self_projection["primary_insight"]["theme_refs"][0] = {
        "kind": "theme",
        "id": "thm_ffffffffffffffffffffffff",
        "revision": 1,
        "revision_sha256": "e" * 64,
    }
    _reseal_bundle(manifest, projections)

    with pytest.raises(ProjectionBundleError, match="self insight theme is absent"):
        validate_projection_bundle_contract(
            ProjectionBundle(manifest=manifest, projections=projections)
        )


def test_bundle_validator_rejects_a_theme_detail_with_unrelated_records() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(),
        as_of="2026-08-18",
        generated_at="2026-08-18T22:00:00+08:00",
    )
    manifest = copy.deepcopy(dict(bundle.manifest))
    projections = cast(
        Dict[str, Dict[str, Any]], copy.deepcopy(dict(bundle.projections))
    )
    detail_index = projections["projections/detail-index.json"]
    theme_entry = next(
        item for item in detail_index["entries"] if item["detail_kind"] == "theme"
    )
    detail = projections[theme_entry["path"]]
    unrelated_record = bundle.projection("projections/timeline.json")["entries"][-1][
        "record_ref"
    ]
    detail["record_refs"] = [unrelated_record]
    _reseal_bundle(manifest, projections)

    with pytest.raises(ProjectionBundleError, match="evidence spans"):
        validate_projection_bundle_contract(
            ProjectionBundle(manifest=manifest, projections=projections)
        )


def test_bundle_validator_rejects_a_stale_home_resource_reference() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(),
        as_of="2026-08-18",
        generated_at="2026-08-18T22:00:00+08:00",
    )
    manifest = copy.deepcopy(dict(bundle.manifest))
    projections = cast(
        Dict[str, Dict[str, Any]], copy.deepcopy(dict(bundle.projections))
    )
    projections["projections/home.json"]["resource_entries"][0][
        "resource_ref"
    ]["revision_sha256"] = "f" * 64
    _reseal_bundle(manifest, projections)

    with pytest.raises(ProjectionBundleError, match="home resource is absent or stale"):
        validate_projection_bundle_contract(
            ProjectionBundle(manifest=manifest, projections=projections)
        )


def test_projection_contracts_keep_theme_and_self_identifiers_distinct() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(), as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )
    landscape = copy.deepcopy(dict(bundle.projection("projections/landscape.json")))
    landscape["peaks"][0]["theme_ref"]["id"] = "mat_" + "1" * 24
    with pytest.raises(ContractValidationError):
        validate_contract("landscape-projection-v2.schema.json", landscape)

    self_projection = copy.deepcopy(dict(bundle.projection("projections/self.json")))
    self_projection["primary_insight"]["insight_ref"]["id"] = "thm_" + "2" * 24
    with pytest.raises(ContractValidationError):
        validate_contract("self-projection-v1.schema.json", self_projection)


def test_bundle_validator_rechecks_projection_schemas_and_detail_roles() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(), as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )
    projections = copy.deepcopy(dict(bundle.projections))
    timeline = projections["projections/timeline.json"]
    timeline["entries"][0]["record_ref"] = copy.deepcopy(timeline["entries"][0]["theme_refs"][0])
    manifest = copy.deepcopy(dict(bundle.manifest))
    next(entry for entry in manifest["entries"] if entry["path"] == "projections/timeline.json")["sha256"] = sha256_json(timeline)
    with pytest.raises(ContractValidationError):
        validate_projection_bundle_contract(ProjectionBundle(manifest=manifest, projections=projections))

    projections = copy.deepcopy(dict(bundle.projections))
    index = projections["projections/detail-index.json"]
    record_entry = next(entry for entry in index["entries"] if entry["detail_kind"] == "record")
    resource_entry = next(entry for entry in index["entries"] if entry["detail_kind"] == "resource")
    record_entry["subject_ref"] = copy.deepcopy(resource_entry["subject_ref"])
    manifest = copy.deepcopy(dict(bundle.manifest))
    next(entry for entry in manifest["entries"] if entry["path"] == "projections/detail-index.json")["sha256"] = sha256_json(index)
    with pytest.raises(ProjectionBundleError, match="detail index role mismatch"):
        validate_projection_bundle_contract(ProjectionBundle(manifest=manifest, projections=projections))

    manifest = copy.deepcopy(dict(bundle.manifest))
    manifest["entries"][1]["projection_id"] = manifest["entries"][0]["projection_id"]
    with pytest.raises(ProjectionBundleError, match="projection ids must be unique"):
        validate_projection_bundle_contract(
            ProjectionBundle(manifest=manifest, projections=copy.deepcopy(dict(bundle.projections)))
        )


def test_projection_bundle_manifest_rejects_parent_path_segments() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(), as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )
    manifest = copy.deepcopy(dict(bundle.manifest))
    manifest["entries"][0]["path"] = "projections/../outside.json"
    with pytest.raises(ContractValidationError):
        validate_contract("projection-bundle-v1.schema.json", manifest)


def test_projector_rejects_stale_exact_revision_references() -> None:
    inputs = formal_20d_inputs()
    insight = copy.deepcopy(dict(inputs.self_insights[0]))
    insight["theme_refs"][0]["revision_sha256"] = "f" * 64
    broken = replace(inputs, self_insights=(insight,))
    with pytest.raises(ProjectionInputError, match="revision mismatch"):
        build_projection_bundle(
            broken, as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
        )


def test_resource_selected_span_must_trace_to_its_current_source_record() -> None:
    inputs = formal_20d_inputs()
    resource = copy.deepcopy(dict(inputs.resource_cards[0]))
    source = inputs.source_records[0]
    resource["user_selected_spans"] = [{
        "record_id": source["record_id"],
        "record_revision": source["revision"],
        "record_revision_sha256": "f" * 64,
        "source_file": source["source_file"],
        "line_start": source["line_start"],
        "line_end": source["line_start"],
        "quote": "待会再看",
        "quote_sha256": sha256_bytes("待会再看".encode("utf-8")),
    }]
    broken = replace(inputs, resource_cards=(resource,), read_later_intents=())
    with pytest.raises(ProjectionBundleError, match="source span revision mismatch"):
        build_projection_bundle(
            broken, as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
        )


def _reseal_bundle(
    manifest: Dict[str, Any],
    projections: Dict[str, Dict[str, Any]],
) -> None:
    detail_index = projections["projections/detail-index.json"]
    for entry in detail_index["entries"]:
        entry["sha256"] = sha256_json(projections[entry["path"]])
    home = projections["projections/home.json"]
    for name in ("landscape", "self", "timeline"):
        projection = projections[f"projections/{name}.json"]
        home[f"{name}_ref"]["sha256"] = sha256_json(projection)
    for entry in manifest["entries"]:
        entry["sha256"] = sha256_json(projections[entry["path"]])
