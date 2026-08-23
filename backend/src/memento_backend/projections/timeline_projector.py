"""Project committed records into a chronological read model."""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional

from .common import (
    JSONObject,
    ProjectionContext,
    ReadObject,
    detail_projection_id,
    stable_id,
    timestamp_sort_key,
)


def project_timeline(context: ProjectionContext) -> JSONObject:
    records = sorted(
        context.active(context.inputs.source_records),
        key=lambda item: (str(item["captured_at"]), str(item["record_id"])),
    )
    interpretations = {
        str(item["source_record_ref"]["id"]): item
        for item in sorted(
            context.active(context.inputs.interpretations),
            key=lambda value: (
                timestamp_sort_key(str(value["created_at"])),
                str(value["interpretation_id"]),
            ),
        )
    }
    atoms_by_interpretation = _atoms_by_interpretation(context)
    themes_by_atom = _themes_by_atom(context)
    entries: List[JSONObject] = []
    for record in records:
        record_id = str(record["record_id"])
        interpretation = interpretations.get(record_id)
        atom_ids = [] if interpretation is None else atoms_by_interpretation.get(str(interpretation["interpretation_id"]), [])
        theme_ids = sorted({theme_id for atom_id in atom_ids for theme_id in themes_by_atom.get(atom_id, [])})
        theme_refs = [
            context.current_ref(_find_by_id(context.inputs.themes, "theme_id", theme_id))
            for theme_id in theme_ids
        ]
        entries.append({
            "record_ref": context.current_ref(record),
            "interpretation_ref": context.current_ref(interpretation) if interpretation is not None else None,
            "captured_at": str(record["captured_at"]),
            "local_date": str(record["local_date"]),
            "source_type": str(record["source_type"]),
            "source_app": str(record["source_app"]),
            "status": _timeline_status(interpretation, bool(atom_ids), bool(theme_ids)),
            "summary": None if interpretation is None else (str(interpretation["summary"]) or None),
            "theme_refs": theme_refs,
            "record_detail_projection_id": detail_projection_id("record", record_id, context.bundle_id),
        })

    dates = [str(record["local_date"]) for record in records] or [context.as_of]
    start = min(dates)
    end = max(max(dates), context.as_of)
    days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days + 1
    projection_id = stable_id("tln", "timeline-projection-v1", {"bundle_id": context.bundle_id})
    value = context.metadata(
        kind="memento_timeline_projection",
        version="memento-timeline-v1",
        projection_id=projection_id,
        schema_version="1.0",
    )
    change_dates = sorted({
        str(theme["created_at"])[:10] for theme in context.active(context.inputs.themes)
    } | {
        str(insight["created_at"])[:10] for insight in context.active(context.inputs.self_insights)
    })
    value.update({
        "range": {"start": start, "end": end, "days": days},
        "entries": entries,
        "change_dates": change_dates,
        "page": {"cursor": None, "next_cursor": None, "has_more": False},
    })
    return value


def _timeline_status(interpretation: Optional[ReadObject], has_atom: bool, has_theme: bool) -> str:
    if interpretation is None:
        return "raw_saved"
    status = str(interpretation["status"])
    if status == "original_only":
        return "original_only"
    if status == "needs_review":
        return "needs_review"
    if has_atom or has_theme:
        return "connected"
    return "ready"


def _atoms_by_interpretation(context: ProjectionContext) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for atom in context.active(context.inputs.memory_atoms):
        for ref in atom["evidence_refs"]:
            result.setdefault(str(ref["id"]), []).append(str(atom["memory_atom_id"]))
    for values in result.values():
        values.sort()
    return result


def _themes_by_atom(context: ProjectionContext) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for theme in context.active(context.inputs.themes):
        for ref in list(theme["evidence_refs"]) + list(theme["counterevidence_refs"]):
            result.setdefault(str(ref["id"]), []).append(str(theme["theme_id"]))
    for values in result.values():
        values.sort()
    return result


def _find_by_id(values: tuple[ReadObject, ...], field: str, object_id: str) -> ReadObject:
    for value in values:
        if value[field] == object_id:
            return value
    raise KeyError(object_id)
