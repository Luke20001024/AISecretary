"""Project the compact homepage summary from current read models."""

from __future__ import annotations

import datetime as dt
from typing import Any, List, Mapping

from .common import (
    JSONObject,
    ProjectionContext,
    detail_projection_id,
    projection_sha256,
    stable_id,
    timestamp_sort_key,
)


def project_home(
    context: ProjectionContext,
    *,
    timeline: Mapping[str, Any],
    landscape: Mapping[str, Any],
    self_projection: Mapping[str, Any],
) -> JSONObject:
    today_entries = [item for item in timeline["entries"] if item["local_date"] == context.as_of]
    resources = sorted(
        context.active(context.inputs.resource_cards),
        key=lambda item: (
            timestamp_sort_key(str(item["created_at"])),
            str(item["resource_id"]),
        ),
        reverse=True,
    )
    intents_by_resource = {
        str(item["resource_ref"]["id"]): item
        for item in sorted(
            context.active(context.inputs.read_later_intents),
            key=lambda value: (
                timestamp_sort_key(str(value["created_at"])),
                str(value["intent_id"]),
            ),
        )
    }
    resource_entries = []
    for resource in resources[:24]:
        resource_id = str(resource["resource_id"])
        intent = intents_by_resource.get(resource_id)
        resource_entries.append({
            "resource_ref": context.current_ref(resource),
            "title": str(resource["title"]),
            "processing_route": str(resource["processing_route"]),
            "intent_status": None if intent is None else str(intent["status"]),
            "detail_projection_id": detail_projection_id("resource", resource_id, context.bundle_id),
        })
    recent_changes = _recent_changes(context)
    projection_id = stable_id("home", "home-projection-v2", {"bundle_id": context.bundle_id})
    value = context.metadata(
        kind="memento_home_projection",
        version="memento-home-v2",
        projection_id=projection_id,
        schema_version="2.0",
    )
    next_day = dt.date.fromisoformat(context.as_of) + dt.timedelta(days=1)
    value.update({
        "landscape_ref": {"projection_id": landscape["projection_id"], "sha256": projection_sha256(landscape)},
        "self_ref": {"projection_id": self_projection["projection_id"], "sha256": projection_sha256(self_projection)},
        "timeline_ref": {"projection_id": timeline["projection_id"], "sha256": projection_sha256(timeline)},
        "today_status": {
            "saved": len(today_entries),
            "interpreted": sum(1 for item in today_entries if item["interpretation_ref"] is not None),
            "connected": sum(1 for item in today_entries if item["status"] == "connected"),
            "needs_review": sum(1 for item in today_entries if item["status"] == "needs_review"),
            "run_status": "committed" if today_entries else "no_change",
        },
        "recent_changes": recent_changes,
        "resource_entries": resource_entries,
        "warnings": [],
        "schedule": {
            "enabled": True,
            "hour": 21,
            "minute": 0,
            "next_due_at": f"{next_day.isoformat()}T21:00:00+08:00",
            "last_run_status": "committed" if today_entries else "no_change",
        },
    })
    return value


def _recent_changes(context: ProjectionContext) -> List[JSONObject]:
    values: List[JSONObject] = []
    for theme in context.active(context.inputs.themes):
        theme_id = str(theme["theme_id"])
        values.append({
            "date": str(theme["created_at"])[:10],
            "subject_ref": context.current_ref(theme),
            "summary": str(theme["change_reason"]),
            "detail_projection_id": detail_projection_id("theme", theme_id, context.bundle_id),
        })
    for insight in context.active(context.inputs.self_insights):
        insight_id = str(insight["insight_id"])
        values.append({
            "date": str(insight["created_at"])[:10],
            "subject_ref": context.current_ref(insight),
            "summary": str(insight["change_reason"]),
            "detail_projection_id": detail_projection_id("self_insight", insight_id, context.bundle_id),
        })
    return sorted(values, key=lambda item: (item["date"], item["subject_ref"]["id"]), reverse=True)[:24]
