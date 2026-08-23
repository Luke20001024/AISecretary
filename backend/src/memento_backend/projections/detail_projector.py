"""Back-traceable detail projections for each frontend object."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .common import (
    JSONObject,
    ProjectionContext,
    ReadObject,
    detail_path,
    detail_projection_id,
    projection_sha256,
    stable_id,
    timestamp_sort_key,
)


DETAIL_SCHEMA_BY_KIND = {
    "record": "record-detail-projection-v1.schema.json",
    "resource": "resource-detail-projection-v1.schema.json",
    "theme": "theme-detail-projection-v1.schema.json",
    "self_insight": "self-insight-detail-projection-v1.schema.json",
}


def project_details(context: ProjectionContext) -> Tuple[Dict[str, JSONObject], JSONObject]:
    projections: Dict[str, JSONObject] = {}
    index_entries: List[JSONObject] = []
    groups = (
        ("record", context.active(context.inputs.source_records), project_record_detail),
        ("resource", context.active(context.inputs.resource_cards), project_resource_detail),
        ("theme", context.active(context.inputs.themes), project_theme_detail),
        ("self_insight", context.active(context.inputs.self_insights), project_self_insight_detail),
    )
    for detail_kind, values, projector in groups:
        for value in values:
            subject_id = _subject_id(detail_kind, value)
            projection = projector(context, value)
            path = detail_path(detail_kind, subject_id)
            sha256 = projection_sha256(projection)
            projections[path] = projection
            index_entries.append({
                "detail_kind": detail_kind,
                "subject_ref": context.current_ref(value),
                "projection_id": projection["projection_id"],
                "path": path,
                "sha256": sha256,
            })
    index_entries.sort(key=lambda item: (item["detail_kind"], item["subject_ref"]["id"]))
    projection_id = stable_id("dix", "detail-index-projection-v1", {"bundle_id": context.bundle_id})
    index = context.metadata(
        kind="memento_detail_index_projection",
        version="memento-detail-index-v1",
        projection_id=projection_id,
        schema_version="1.0",
    )
    index["entries"] = index_entries
    return projections, index


def project_record_detail(context: ProjectionContext, record: ReadObject) -> JSONObject:
    record_id = str(record["record_id"])
    interpretations = sorted(
        (
            item
            for item in context.active(context.inputs.interpretations)
            if item["source_record_ref"]["id"] == record_id
        ),
        key=lambda item: (
            timestamp_sort_key(str(item["created_at"])),
            str(item["interpretation_id"]),
        ),
    )
    interpretation = interpretations[-1] if interpretations else None
    atoms = [] if interpretation is None else [
        atom for atom in context.active(context.inputs.memory_atoms)
        if any(ref["id"] == interpretation["interpretation_id"] for ref in atom["evidence_refs"])
    ]
    atom_ids = {str(atom["memory_atom_id"]) for atom in atoms}
    themes = [
        theme for theme in context.active(context.inputs.themes)
        if any(ref["id"] in atom_ids for ref in list(theme["evidence_refs"]) + list(theme["counterevidence_refs"]))
    ]
    projection_id = detail_projection_id("record", record_id, context.bundle_id)
    value = context.metadata(kind="memento_record_detail_projection", version="memento-record-detail-v1", projection_id=projection_id, schema_version="1.0")
    value.update({
        "record_ref": context.current_ref(record),
        "source": {
            "captured_at": str(record["captured_at"]), "local_date": str(record["local_date"]),
            "source_type": str(record["source_type"]), "source_app": str(record["source_app"]),
            "source_file": str(record["source_file"]), "line_start": int(record["line_start"]),
            "line_end": int(record["line_end"]),
            "attachment_refs": [str(item.get("path", item.get("asset_ref", "attachment"))) if isinstance(item, dict) else str(item) for item in record["attachments"]],
        },
        "interpretation": _project_interpretation(context, interpretation),
        "memory_atom_refs": [context.current_ref(item) for item in sorted(atoms, key=lambda item: str(item["memory_atom_id"]))],
        "theme_refs": [context.current_ref(item) for item in sorted(themes, key=lambda item: str(item["theme_id"]))],
    })
    return value


def project_resource_detail(context: ProjectionContext, resource: ReadObject) -> JSONObject:
    resource_id = str(resource["resource_id"])
    intents = [item for item in context.active(context.inputs.read_later_intents) if item["resource_ref"]["id"] == resource_id]
    projection_id = detail_projection_id("resource", resource_id, context.bundle_id)
    value = context.metadata(kind="memento_resource_detail_projection", version="memento-resource-detail-v1", projection_id=projection_id, schema_version="1.0")
    value.update({
        "resource_ref": context.current_ref(resource), "source_record_ref": context.resolve_ref(resource["source_record_ref"]),
        "resource_type": str(resource["resource_type"]), "url": resource["url"], "title": str(resource["title"]),
        "local_asset_refs": [
            str(item["path"]) if isinstance(item, dict) and "path" in item else str(item)
            for item in resource["local_asset_refs"]
        ],
        "user_selected_spans": list(resource["user_selected_spans"]),
        "user_note": resource["user_note"], "processing_route": str(resource["processing_route"]),
        "read_later_intents": [{
            "intent_ref": context.current_ref(item), "status": str(item["status"]), "intent_type": str(item["intent_type"]),
            "user_note": item["user_note"], "created_at": str(item["created_at"]),
        } for item in sorted(intents, key=lambda item: str(item["intent_id"]))],
    })
    return value


def project_theme_detail(context: ProjectionContext, theme: ReadObject) -> JSONObject:
    theme_id = str(theme["theme_id"])
    atom_ids = {str(ref["id"]) for ref in list(theme["evidence_refs"]) + list(theme["counterevidence_refs"])}
    atoms = [atom for atom in context.active(context.inputs.memory_atoms) if atom["memory_atom_id"] in atom_ids]
    interpretation_ids = {str(ref["id"]) for atom in atoms for ref in atom["evidence_refs"]}
    interpretations = [item for item in context.active(context.inputs.interpretations) if item["interpretation_id"] in interpretation_ids]
    record_ids = {str(item["source_record_ref"]["id"]) for item in interpretations}
    records = [item for item in context.active(context.inputs.source_records) if item["record_id"] in record_ids]
    relation_ids = {str(ref["id"]) for ref in theme["relation_refs"]}
    relations = [item for item in context.active(context.inputs.relations) if item["relation_id"] in relation_ids]
    projection_id = detail_projection_id("theme", theme_id, context.bundle_id)
    value = context.metadata(kind="memento_theme_detail_projection", version="memento-theme-detail-v1", projection_id=projection_id, schema_version="1.0")
    value.update({
        "theme_ref": context.current_ref(theme), "title": str(theme["title"]), "statement": str(theme["statement"]),
        "scope": str(theme["scope"]), "lifecycle": str(theme["lifecycle"]), "confidence": str(theme["confidence"]),
        "evidence_days": list(theme["evidence_days"]),
        "memory_atoms": [{
            "memory_atom_ref": context.current_ref(atom), "statement": str(atom["statement"]), "memory_kind": str(atom["memory_kind"]),
            "first_seen_on": str(atom["first_seen_on"]), "last_seen_on": str(atom["last_seen_on"]), "source_spans": list(atom["source_spans"]),
        } for atom in sorted(atoms, key=lambda item: str(item["memory_atom_id"]))],
        "counterevidence_refs": [context.resolve_ref(ref) for ref in theme["counterevidence_refs"]],
        "relations": [{
            "relation_ref": context.current_ref(item), "type": str(item["relation_type"]), "statement": str(item["statement"]),
            "from_ref": context.resolve_ref(item["from_ref"]), "to_ref": context.resolve_ref(item["to_ref"]),
        } for item in sorted(relations, key=lambda item: str(item["relation_id"]))],
        "record_refs": [context.current_ref(item) for item in sorted(records, key=lambda item: str(item["record_id"]))],
        "change_reason": str(theme["change_reason"]),
    })
    return value


def project_self_insight_detail(context: ProjectionContext, insight: ReadObject) -> JSONObject:
    insight_id = str(insight["insight_id"])
    theme_ids = {str(ref["id"]) for ref in insight["theme_refs"]}
    themes = [theme for theme in context.active(context.inputs.themes) if theme["theme_id"] in theme_ids]
    projection_id = detail_projection_id("self_insight", insight_id, context.bundle_id)
    value = context.metadata(kind="memento_self_insight_detail_projection", version="memento-self-insight-detail-v1", projection_id=projection_id, schema_version="1.0")
    value.update({
        "insight_ref": context.current_ref(insight), "title": str(insight["title"]), "statement": str(insight["statement"]),
        "scope": str(insight["scope"]), "uncertainty": str(insight["uncertainty"]), "maturity": str(insight["maturity"]),
        "confirmation": str(insight["confirmation"]),
        "sensitivity": str(insight["sensitivity"]), "visibility": str(insight["visibility"]),
        "themes": [{
            "theme_ref": context.current_ref(theme), "title": str(theme["title"]), "statement": str(theme["statement"]),
            "detail_projection_id": detail_projection_id("theme", str(theme["theme_id"]), context.bundle_id),
        } for theme in sorted(themes, key=lambda item: str(item["theme_id"]))],
        "support_refs": [context.resolve_ref(ref) for ref in insight["support_refs"]],
        "boundary_refs": [context.resolve_ref(ref) for ref in insight["boundary_refs"]],
        "change_reason": str(insight["change_reason"]),
    })
    return value


def _project_interpretation(context: ProjectionContext, item: Optional[ReadObject]) -> Optional[JSONObject]:
    if item is None:
        return None
    return {
        "interpretation_ref": context.current_ref(item), "status": str(item["status"]), "summary": str(item["summary"]),
        "content_types": list(item["content_types"]), "topics": list(item["topics"]), "purposes": list(item["purposes"]),
        "stance": str(item["stance"]), "uncertainty": str(item["uncertainty"]), "source_spans": list(item["source_spans"]),
    }


def _subject_id(detail_kind: str, value: ReadObject) -> str:
    field = {"record": "record_id", "resource": "resource_id", "theme": "theme_id", "self_insight": "insight_id"}[detail_kind]
    return str(value[field])
