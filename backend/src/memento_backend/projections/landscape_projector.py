"""Project formal Themes, MemoryAtoms and Relations into stable terrain."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Sequence

from .common import (
    JSONObject,
    ProjectionContext,
    ReadObject,
    day_distance,
    detail_projection_id,
    hash_unit,
    is_recent_on_or_before,
    round_unit,
    stable_id,
)


FRONTEND_RELATION_TYPES = {
    "supports", "counterexample", "revises", "scope_boundary", "same_topic", "derived_from"
}


def project_landscape(context: ProjectionContext) -> JSONObject:
    themes = sorted(context.active(context.inputs.themes), key=lambda item: str(item["theme_id"]))
    atoms = sorted(context.active(context.inputs.memory_atoms), key=lambda item: str(item["memory_atom_id"]))
    relations = sorted(context.active(context.inputs.relations), key=lambda item: str(item["relation_id"]))
    theme_by_atom = _theme_membership(themes)
    positions: Dict[str, tuple[float, float]] = {}
    peaks: List[JSONObject] = []

    for theme in themes:
        theme_id = str(theme["theme_id"])
        x = round_unit(0.12 + hash_unit(theme_id, 0) * 0.76)
        y = round_unit(0.12 + hash_unit(theme_id, 1) * 0.76)
        positions[theme_id] = (x, y)
        evidence_days = list(theme["evidence_days"])
        evidence_count = len(theme["evidence_refs"])
        recent = is_recent_on_or_before(max(evidence_days), context.as_of)
        span = max(1, day_distance(min(evidence_days), max(evidence_days)) + 1)
        elevation = round_unit(0.28 + min(0.4, evidence_count * 0.08) + min(0.2, span * 0.01) + (0.06 if recent else 0.0))
        peaks.append({
            "theme_ref": context.current_ref(theme),
            "title": str(theme["title"]),
            "statement": str(theme["statement"]),
            "x": x,
            "y": y,
            "elevation": elevation,
            "evidence_count": evidence_count,
            "counterevidence_count": len(theme["counterevidence_refs"]),
            "evidence_days": len(evidence_days),
            "recent_change": recent,
            "lifecycle": str(theme["lifecycle"]),
            "detail_projection_id": detail_projection_id("theme", theme_id, context.bundle_id),
        })

    nodes: List[JSONObject] = []
    for atom in atoms:
        atom_id = str(atom["memory_atom_id"])
        memberships = theme_by_atom.get(atom_id, [])
        anchor = positions.get(memberships[0], (0.5, 0.5)) if memberships else (0.5, 0.5)
        angle = hash_unit(atom_id, 2) * math.tau
        radius = 0.025 + hash_unit(atom_id, 3) * 0.055
        x = round_unit(anchor[0] + math.cos(angle) * radius)
        y = round_unit(anchor[1] + math.sin(angle) * radius)
        positions[atom_id] = (x, y)
        theme_refs = [context.current_ref(_theme_by_id(themes, theme_id)) for theme_id in memberships]
        nodes.append({
            "memory_atom_ref": context.current_ref(atom),
            "x": x,
            "y": y,
            "recent": is_recent_on_or_before(str(atom["last_seen_on"]), context.as_of),
            "theme_refs": theme_refs,
        })

    endpoint_ids = set(positions)
    edges: List[JSONObject] = []
    for relation in relations:
        relation_type = str(relation["relation_type"])
        from_id = str(relation["from_ref"]["id"])
        to_id = str(relation["to_ref"]["id"])
        if relation_type not in FRONTEND_RELATION_TYPES or from_id == to_id:
            continue
        if from_id not in endpoint_ids or to_id not in endpoint_ids:
            continue
        edges.append({
            "relation_ref": context.current_ref(relation),
            "from_id": from_id,
            "to_id": to_id,
            "type": relation_type,
        })

    projection_id = stable_id("lnd", "landscape-projection-v2", {"bundle_id": context.bundle_id})
    active_count = sum(1 for theme in themes if theme["lifecycle"] in {"active", "tension"})
    forming_count = sum(1 for theme in themes if theme["lifecycle"] == "forming")
    value = context.metadata(
        kind="memento_landscape_projection",
        version="memento-landscape-v2",
        projection_id=projection_id,
        schema_version="2.0",
    )
    value.update({
        "summary": {
            "active_themes": active_count,
            "recent_changes": sum(1 for peak in peaks if peak["recent_change"]),
            "forming_themes": forming_count,
        },
        "terrain": {
            "algorithm_version": "stable-theme-terrain-v1",
            "coordinate_space": "normalized_0_1",
            "grid_size": 96,
            "contour_levels": 12,
        },
        "peaks": peaks,
        "nodes": nodes,
        "edges": edges,
        "previous_projection_sha256": context.previous_landscape_sha256,
    })
    return value


def _theme_membership(themes: Sequence[ReadObject]) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for theme in themes:
        for ref in theme["evidence_refs"]:
            result.setdefault(str(ref["id"]), []).append(str(theme["theme_id"]))
        for ref in theme["counterevidence_refs"]:
            result.setdefault(str(ref["id"]), []).append(str(theme["theme_id"]))
    for values in result.values():
        values.sort()
    return result


def _theme_by_id(themes: Sequence[ReadObject], theme_id: str) -> ReadObject:
    for theme in themes:
        if theme["theme_id"] == theme_id:
            return theme
    raise KeyError(theme_id)
