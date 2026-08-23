"""R6 proof that terrain growth is a projection replay, not model-authored state."""

from __future__ import annotations

from memento_backend.domain.ids import sha256_json
from memento_backend.projections import ProjectionBundle, build_projection_bundle

from tests.fixtures.formal_20d import formal_20d_inputs


def _growth_replay() -> tuple[ProjectionBundle, ...]:
    inputs = formal_20d_inputs()
    checkpoints = (
        ("2026-07-30", "2026-07-30T22:00:00+08:00"),
        ("2026-08-03", "2026-08-03T22:00:00+08:00"),
        ("2026-08-09", "2026-08-09T22:00:00+08:00"),
        ("2026-08-18", "2026-08-18T22:00:00+08:00"),
    )
    bundles: list[ProjectionBundle] = []
    previous_bundle_sha256 = None
    previous_landscape_sha256 = None
    for as_of, generated_at in checkpoints:
        bundle = build_projection_bundle(
            inputs,
            as_of=as_of,
            generated_at=generated_at,
            previous_bundle_sha256=previous_bundle_sha256,
            previous_landscape_sha256=previous_landscape_sha256,
        )
        bundles.append(bundle)
        previous_bundle_sha256 = bundle.bundle_sha256
        previous_landscape_sha256 = sha256_json(bundle.projection("projections/landscape.json"))
    return tuple(bundles)


def test_twenty_day_replay_grows_from_point_to_local_terrain_to_complete_terrain() -> None:
    day_one, day_five, day_eleven, day_twenty = _growth_replay()
    landscapes = [
        bundle.projection("projections/landscape.json")
        for bundle in (day_one, day_five, day_eleven, day_twenty)
    ]

    assert [(len(value["nodes"]), len(value["edges"]), len(value["peaks"])) for value in landscapes] == [
        (1, 0, 0),
        (2, 1, 1),
        (3, 1, 1),
        (6, 3, 3),
    ]
    assert landscapes[0]["previous_projection_sha256"] is None
    for previous, current in zip(landscapes, landscapes[1:]):
        assert current["previous_projection_sha256"] == sha256_json(previous)
    assert {peak["title"] for peak in landscapes[-1]["peaks"]} == {
        "产品决策", "证据优先", "长期积累",
    }


def test_growth_replay_is_byte_deterministic_at_every_checkpoint() -> None:
    first = _growth_replay()
    second = _growth_replay()
    assert [bundle.bundle_sha256 for bundle in first] == [bundle.bundle_sha256 for bundle in second]
    assert [bundle.projections for bundle in first] == [bundle.projections for bundle in second]
