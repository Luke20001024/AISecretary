"""Fixed standalone Home overlays for UI state contract tests.

These values exercise loading and fallback presentation only. They are not
publishable ProjectionBundles; R4 will place publication state outside the
last legal immutable bundle.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Mapping, cast

from memento_backend.contracts.validator import validate_contract
from memento_backend.projections.bundle_projector import ProjectionBundle, build_projection_bundle
from memento_backend.projections.common import ProjectionInputs


def home_state_fixtures(base: ProjectionBundle) -> Mapping[str, Mapping[str, Any]]:
    base_home = base.projection("projections/home.json")
    values: Dict[str, Dict[str, Any]] = {}
    specifications = {
        "loading": ("running", ["正在重建读模型，上一份合法 bundle 继续可用"]),
        "stale": ("stale", ["输入 head 已更新，当前仍展示上一份合法 bundle"]),
        "conflict": ("conflict", ["发现并发修订冲突，正式对象尚未被覆盖"]),
        "failed_preserved": ("failed_preserved", ["本轮生成失败，上一份合法 bundle 已保留"]),
    }
    for name, (status, warnings) in specifications.items():
        value = copy.deepcopy(dict(base_home))
        today_status = cast(Dict[str, Any], value["today_status"])
        today_status["run_status"] = status
        value["warnings"] = warnings
        validate_contract("home-projection-v2.schema.json", value)
        values[name] = value
    empty = build_projection_bundle(
        ProjectionInputs(), as_of="2026-08-18", generated_at="2026-08-18T22:00:00+08:00"
    ).projection("projections/home.json")
    values["empty"] = dict(empty)
    return values
