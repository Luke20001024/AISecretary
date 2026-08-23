"""Project formal SelfInsight revisions into the third product layer."""

from __future__ import annotations

from typing import List

from .common import JSONObject, ProjectionContext, ReadObject, detail_projection_id, stable_id


MATURITY_ORDER = {"stable": 0, "observed": 1, "forming": 2, "dormant": 3}


def project_self(context: ProjectionContext) -> JSONObject:
    insights = sorted(
        context.active(context.inputs.self_insights),
        key=lambda item: (MATURITY_ORDER[str(item["maturity"])], str(item["insight_id"])),
    )
    projected = [_project_insight(context, item) for item in insights]
    related_theme_refs = []
    seen_themes = set()
    boundaries: List[JSONObject] = []
    recent_changes: List[JSONObject] = []
    for item in insights:
        insight_ref = context.current_ref(item)
        for raw_ref in item["theme_refs"]:
            ref = context.resolve_ref(raw_ref)
            if ref["id"] not in seen_themes:
                related_theme_refs.append(ref)
                seen_themes.add(ref["id"])
        for raw_ref in item["boundary_refs"]:
            boundaries.append({"insight_ref": insight_ref, "support_ref": context.resolve_ref(raw_ref)})
        recent_changes.append({
            "date": str(item["created_at"])[:10],
            "insight_ref": insight_ref,
            "reason": str(item["change_reason"]),
        })

    projection_id = stable_id("self", "self-projection-v1", {"bundle_id": context.bundle_id})
    value = context.metadata(
        kind="memento_self_projection",
        version="memento-self-v1",
        projection_id=projection_id,
        schema_version="1.0",
    )
    value.update({
        "primary_insight": projected[0] if projected else None,
        "other_insights": projected[1:],
        "related_theme_refs": related_theme_refs,
        "recent_changes": sorted(recent_changes, key=lambda item: (item["date"], item["insight_ref"]["id"]), reverse=True)[:24],
        "boundaries": boundaries,
    })
    return value


def _project_insight(context: ProjectionContext, item: ReadObject) -> JSONObject:
    insight_id = str(item["insight_id"])
    return {
        "insight_ref": context.current_ref(item),
        "title": str(item["title"]),
        "statement": str(item["statement"]),
        "scope": str(item["scope"]),
        "uncertainty": str(item["uncertainty"]),
        "maturity": str(item["maturity"]),
        "confirmation": str(item["confirmation"]),
        "sensitivity": str(item["sensitivity"]),
        "visibility": str(item["visibility"]),
        "theme_refs": [context.resolve_ref(ref) for ref in item["theme_refs"]],
        "support_count": len(item["support_refs"]),
        "boundary_count": len(item["boundary_refs"]),
        "change_reason": str(item["change_reason"]),
        "detail_projection_id": detail_projection_id("self_insight", insight_id, context.bundle_id),
    }
