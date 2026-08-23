from __future__ import annotations

from memento_backend.projections import build_projection_bundle

from tests.fixtures.formal_20d import formal_20d_inputs


def test_self_projection_is_a_distinct_third_layer_with_its_own_detail() -> None:
    bundle = build_projection_bundle(
        formal_20d_inputs(), as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    )
    self_projection = bundle.projection("projections/self.json")
    primary = self_projection["primary_insight"]
    assert primary["insight_ref"]["kind"] == "self_insight"
    assert len(primary["theme_refs"]) == 3
    assert primary["support_count"] >= 2
    assert primary["confirmation"] == "observed"
    assert primary["visibility"] == "grant_only"

    detail_entry = next(
        entry for entry in bundle.projection("projections/detail-index.json")["entries"]
        if entry["projection_id"] == primary["detail_projection_id"]
    )
    detail = bundle.projection(detail_entry["path"])
    assert detail["insight_ref"] == primary["insight_ref"]
    assert len(detail["themes"]) == 3
    assert len(detail["support_refs"]) >= 2
    assert detail["confirmation"] == "observed"
