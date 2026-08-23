from __future__ import annotations

from memento_backend.projections import build_projection_bundle

from tests.fixtures.formal_20d import formal_20d_inputs


def test_landscape_has_theme_peaks_and_can_return_to_records() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(), as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )
    landscape = bundle.projection("projections/landscape.json")
    assert len(landscape["peaks"]) == 3
    assert {peak["theme_ref"]["kind"] for peak in landscape["peaks"]} == {"theme"}
    assert all(0.0 <= peak["x"] <= 1.0 and 0.0 <= peak["y"] <= 1.0 for peak in landscape["peaks"])

    for peak in landscape["peaks"]:
        detail_path = next(
            entry["path"]
            for entry in bundle.projection("projections/detail-index.json")["entries"]
            if entry["projection_id"] == peak["detail_projection_id"]
        )
        detail = bundle.projection(detail_path)
        assert len(detail["memory_atoms"]) >= 2
        assert len(detail["record_refs"]) >= 2


def test_landscape_does_not_turn_self_insights_into_peaks() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(), as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )
    landscape = bundle.projection("projections/landscape.json")
    assert all(not peak["theme_ref"]["id"].startswith("sin_") for peak in landscape["peaks"])
