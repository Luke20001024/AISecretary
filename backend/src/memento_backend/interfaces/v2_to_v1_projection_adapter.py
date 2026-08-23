"""Compatibility adapter for the currently frozen JavaScript V1 contract."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from memento_backend.domain.ids import canonical_json, sha256_json, validate_sha256
from memento_backend.projections.bundle_projector import (
    ProjectionBundle,
    validate_projection_bundle_contract,
)


JSONObject = Dict[str, Any]

SOURCE_TYPE_MAP = {
    "text": "text",
    "url": "file_note",
    "web_page": "file_note",
    "screenshot_ocr": "screenshot_ocr",
    "voice_transcript": "voice_transcript",
    "image_note": "image_note",
    "file_note": "file_note",
    "external_trace": "file_note",
}

STATUS_MAP = {
    "raw_saved": "raw_saved",
    "ready": "ready",
    "needs_review": "needs_review",
    "original_only": "original_only",
    "connected": "merged",
    "failed_preserved": "failed",
}

CONTENT_TYPE_MAP = {"quoted_material": "quote"}
RELATION_TYPE_MAP = {"derived_from": "supports"}


@dataclass(frozen=True)
class V1ProjectionPair:
    home: Mapping[str, Any]
    landscape: Mapping[str, Any]
    landscape_sha256: str
    authority: Mapping[str, Any]
    portrait: List[Mapping[str, Any]]


def adapt_v2_bundle_to_v1(
    bundle: ProjectionBundle,
    *,
    user_action_watermark_sha256: str,
) -> V1ProjectionPair:
    validate_sha256(user_action_watermark_sha256, "user_action_watermark_sha256")
    validate_projection_bundle_contract(bundle)
    landscape_v2 = bundle.projection("projections/landscape.json")
    timeline_v2 = bundle.projection("projections/timeline.json")
    self_v2 = bundle.projection("projections/self.json")
    detail_index = bundle.projection("projections/detail-index.json")
    record_details = _record_details(bundle, detail_index)
    landscape = _adapt_landscape(
        landscape_v2,
        self_v2,
        user_action_watermark_sha256=user_action_watermark_sha256,
    )
    landscape_sha256 = sha256_json(landscape)
    home = _adapt_home(
        bundle=bundle,
        timeline=timeline_v2,
        landscape=landscape,
        landscape_sha256=landscape_sha256,
        record_details=record_details,
        agent_profile_sha256=sha256_json(self_v2),
        user_action_watermark_sha256=user_action_watermark_sha256,
    )
    authority = _build_authority(home, landscape)
    return V1ProjectionPair(
        home=home,
        landscape=landscape,
        landscape_sha256=landscape_sha256,
        authority=authority,
        portrait=_adapt_portrait(self_v2),
    )


def _adapt_landscape(
    landscape: Mapping[str, Any],
    self_projection: Mapping[str, Any],
    *,
    user_action_watermark_sha256: str,
) -> JSONObject:
    peaks: List[JSONObject] = []
    for peak in landscape["peaks"]:
        theme_ref = _v1_ref(peak["theme_ref"])
        lifecycle = str(peak["lifecycle"])
        if lifecycle == "forming":
            lifecycle = "active"
        peaks.append({
            "peak_id": f"peak_{theme_ref['id'][4:]}",
            "understanding_ref": theme_ref,
            "x": peak["x"], "y": peak["y"], "elevation": peak["elevation"],
            "evidence_count": peak["evidence_count"], "counterevidence_count": peak["counterevidence_count"],
            "recent_change": peak["recent_change"], "lifecycle": lifecycle,
        })
    nodes: List[JSONObject] = [{
        "memory_ref": _v1_ref(node["memory_atom_ref"]),
        "x": node["x"], "y": node["y"], "state": "committed", "recent": node["recent"],
    } for node in landscape["nodes"]]
    endpoint_map = {
        str(peak["theme_ref"]["id"]): str(_v1_ref(peak["theme_ref"])["id"])
        for peak in landscape["peaks"]
    }
    endpoint_map.update({
        str(node["memory_atom_ref"]["id"]): str(_v1_ref(node["memory_atom_ref"])["id"])
        for node in landscape["nodes"]
    })
    edges: List[JSONObject] = [{
        "relation_ref": _v1_ref(edge["relation_ref"]),
        "from_id": endpoint_map[edge["from_id"]], "to_id": endpoint_map[edge["to_id"]],
        "type": RELATION_TYPE_MAP.get(str(edge["type"]), str(edge["type"])),
    } for edge in landscape["edges"]]
    summary = {
        "active_understandings": len(peaks),
        "recent_changes": sum(1 for peak in peaks if peak["recent_change"]),
        "observing_candidates": int(landscape["summary"]["forming_themes"]),
    }
    memory_refs = sorted((node["memory_ref"] for node in nodes), key=lambda ref: ref["id"])
    relation_refs = sorted((edge["relation_ref"] for edge in edges), key=lambda ref: ref["id"])
    agent_profile_sha256 = sha256_json(self_projection)
    return {
        "schema_version": "1.0", "kind": "memento_landscape_snapshot",
        "snapshot_id": landscape["projection_id"], "created_at": landscape["generated_at"], "as_of": landscape["as_of"],
        "projection_version": "cognitive-landscape-v1",
        "input_hashes": {
            "agent_profile_sha256": agent_profile_sha256,
            "reusable_memory_head_sha256": _sha_value(memory_refs),
            "relation_head_sha256": _sha_value(relation_refs),
            "user_action_watermark_sha256": user_action_watermark_sha256,
        },
        "summary": summary,
        "terrain": {"algorithm_version": "stable-anchor-kde-v1", "grid_size": 96, "contour_levels": 12, "coordinate_space": "normalized_0_1"},
        "peaks": peaks, "nodes": nodes, "edges": edges,
        "previous_snapshot_sha256": landscape["previous_projection_sha256"],
    }


def _adapt_home(
    *,
    bundle: ProjectionBundle,
    timeline: Mapping[str, Any],
    landscape: Mapping[str, Any],
    landscape_sha256: str,
    record_details: Mapping[str, Mapping[str, Any]],
    agent_profile_sha256: str,
    user_action_watermark_sha256: str,
) -> JSONObject:
    local_date = str(bundle.manifest["as_of"])
    timeline_entries = [entry for entry in timeline["entries"] if entry["local_date"] == local_date]
    records = []
    for entry in timeline_entries:
        record_ref = entry["record_ref"]
        interpretation_ref = entry["interpretation_ref"]
        status = STATUS_MAP[str(entry["status"])]
        detail = record_details[str(record_ref["id"])]
        interpretation = detail["interpretation"]
        receipt_ref: Optional[JSONObject]
        if interpretation_ref is None:
            receipt_ref = None
        else:
            receipt_ref = {
                "kind": "interpretation_receipt",
                "id": _make_receipt_id(str(record_ref["id"])),
                "revision": interpretation_ref["revision"],
                "revision_sha256": interpretation_ref["revision_sha256"],
            }
        original_only = status == "original_only"
        records.append({
            "record_ref": dict(record_ref), "receipt_ref": receipt_ref,
            "captured_at": entry["captured_at"], "source_type": SOURCE_TYPE_MAP[str(entry["source_type"])],
            "source_app": entry["source_app"], "status": status,
            "summary": None if original_only else entry["summary"],
            "content_types": [] if interpretation is None or original_only else [CONTENT_TYPE_MAP.get(value, value) for value in interpretation["content_types"]],
            "topics": [] if interpretation is None or original_only else list(interpretation["topics"]),
            "purposes": [] if interpretation is None or original_only else list(interpretation["purposes"]),
            "memory_refs": [] if original_only else [_v1_ref(ref) for ref in detail["memory_atom_refs"]],
            "understanding_refs": [] if original_only else [_v1_ref(ref) for ref in detail["theme_refs"]],
        })
    record_refs = [record["record_ref"] for record in records]
    receipt_refs = sorted((record["receipt_ref"] for record in records if record["receipt_ref"] is not None), key=lambda ref: ref["id"])
    summary = landscape["summary"]
    v2_home = bundle.projection("projections/home.json")
    return {
        "schema_version": "1.0", "kind": "memento_home_projection", "projection_version": "cognitive-secretary-home-v1",
        "generated_at": bundle.manifest["generated_at"], "local_date": local_date,
        "input_hashes": {
            "record_head_sha256": _sha_value(record_refs),
            "receipt_head_sha256": _sha_value(receipt_refs),
            "daily_bundle_head_sha256": bundle.bundle_sha256,
            "agent_profile_sha256": agent_profile_sha256,
            "landscape_snapshot_sha256": landscape_sha256,
            "user_action_watermark_sha256": user_action_watermark_sha256,
        },
        "landscape_ref": {"snapshot_id": landscape["snapshot_id"], "snapshot_sha256": landscape_sha256},
        "landscape_summary": dict(summary),
        "today_status": {
            "saved": len(records),
            "interpreted": sum(1 for record in records if record["receipt_ref"] is not None or record["status"] == "no_candidate"),
            "merged": sum(1 for record in records if record["status"] == "merged"),
            "needs_review": sum(1 for record in records if record["status"] == "needs_review"),
            "daily_run_status": "committed" if records else "no_change",
        },
        "records": records,
        "schedule": {
            "enabled": v2_home["schedule"]["enabled"], "hour": v2_home["schedule"]["hour"],
            "minute": v2_home["schedule"]["minute"], "next_due_at": v2_home["schedule"]["next_due_at"],
            "last_run_status": "committed" if records else "no_change",
        },
        "warnings": list(v2_home["warnings"]),
    }


def _record_details(bundle: ProjectionBundle, detail_index: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    result: Dict[str, Mapping[str, Any]] = {}
    for entry in detail_index["entries"]:
        if entry["detail_kind"] == "record":
            result[str(entry["subject_ref"]["id"])] = bundle.projection(str(entry["path"]))
    return result


def _build_authority(home: Mapping[str, Any], landscape: Mapping[str, Any]) -> JSONObject:
    return {
        "agent_profile_sha256": home["input_hashes"]["agent_profile_sha256"],
        "active_understanding_refs": [peak["understanding_ref"] for peak in landscape["peaks"]],
        "current_memory_refs": [node["memory_ref"] for node in landscape["nodes"]],
        "current_relation_refs": [edge["relation_ref"] for edge in landscape["edges"]],
        "user_action_watermark_sha256": home["input_hashes"]["user_action_watermark_sha256"],
        "today_record_refs": [record["record_ref"] for record in home["records"]],
        "today_receipt_refs": [record["receipt_ref"] for record in home["records"] if record["receipt_ref"] is not None],
        "daily_bundle_head_sha256": home["input_hashes"]["daily_bundle_head_sha256"],
    }


def _adapt_portrait(self_projection: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    values: List[Mapping[str, Any]] = []
    candidates: List[Mapping[str, Any]] = []
    if self_projection["primary_insight"] is not None:
        candidates.append(self_projection["primary_insight"])
    candidates.extend(self_projection["other_insights"])
    for insight in candidates:
        values.append({
            "id": f"portrait_{str(insight['insight_ref']['id'])[4:]}",
            "title": insight["title"], "maturity": insight["maturity"], "statement": insight["statement"],
            "themeIds": [str(_v1_ref(ref)["id"]) for ref in insight["theme_refs"]],
            "boundary": f"适用范围：{insight['scope']}；当前不确定性：{insight['uncertainty']}",
            "synthetic": False,
        })
    return values


def _v1_ref(ref: Mapping[str, Any]) -> JSONObject:
    kind = str(ref["kind"])
    object_id = str(ref["id"])
    if kind == "theme":
        kind, object_id = "understanding", f"mem_{object_id[4:]}"
    elif kind == "memory_atom":
        kind, object_id = "reusable_memory", f"rmem_{object_id[4:]}"
    return {"kind": kind, "id": object_id, "revision": ref["revision"], "revision_sha256": ref["revision_sha256"]}


def _make_receipt_id(record_id: str) -> str:
    digest = hashlib.sha256(canonical_json({"namespace": "receipt-v1", "record_id": record_id}).encode("utf-8")).hexdigest()
    return f"rcp_{digest[:24]}"


def _sha_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
