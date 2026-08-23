"""Shared deterministic projection primitives.

Projection code is deliberately pure: it receives already committed formal
objects and returns JSON-compatible dictionaries.  It performs no model calls,
file writes or Vault reads.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from memento_backend.domain.ids import canonical_json, sha256_json


JSONObject = Dict[str, Any]
ReadObject = Mapping[str, Any]

ID_FIELDS: Mapping[str, Tuple[str, str]] = {
    "memento_source_record_revision": ("source_record", "record_id"),
    "memento_capture_decision_revision": ("capture_decision", "decision_id"),
    "memento_resource_card_revision": ("resource_card", "resource_id"),
    "memento_read_later_intent_revision": ("read_later_intent", "intent_id"),
    "memento_record_interpretation_revision": ("record_interpretation", "interpretation_id"),
    "memento_memory_atom_revision": ("memory_atom", "memory_atom_id"),
    "memento_relation_revision": ("relation", "relation_id"),
    "memento_theme_revision": ("theme", "theme_id"),
    "memento_self_insight_revision": ("self_insight", "insight_id"),
}

DETAIL_PREFIXES: Mapping[str, str] = {
    "record": "rdt",
    "resource": "rsd",
    "theme": "tdt",
    "self_insight": "sdt",
}


def stable_id(prefix: str, namespace: str, payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        canonical_json({"namespace": namespace, "payload": dict(payload)}).encode("utf-8")
    ).hexdigest()
    return f"{prefix}_{digest[:24]}"


def round_unit(value: float) -> float:
    return min(1.0, max(0.0, round(value, 6)))


def hash_unit(seed: str, offset: int = 0) -> float:
    digest = hashlib.sha256(f"{seed}:{offset}".encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(0xFFFFFFFFFFFF)


def projection_sha256(value: Mapping[str, Any]) -> str:
    return sha256_json(value)


def date_part(timestamp: str) -> str:
    return timestamp[:10]


def timestamp_sort_key(timestamp: str) -> Tuple[dt.datetime, str]:
    """Return an instant-aware, deterministic key for RFC 3339 timestamps.

    ISO text order is only chronological while every value uses the same UTC
    offset.  Projection inputs may legitimately preserve timestamps from
    different sources, so ordering must compare their actual instants.  The
    original text is retained as a stable tie-breaker for equal instants.
    """

    normalized = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ProjectionInputError("projection timestamp must include a timezone")
    return parsed.astimezone(dt.timezone.utc), timestamp


def day_distance(left: str, right: str) -> int:
    return abs((dt.date.fromisoformat(left) - dt.date.fromisoformat(right)).days)


def is_recent_on_or_before(value: str, as_of: str, *, days: int = 7) -> bool:
    distance = (dt.date.fromisoformat(as_of) - dt.date.fromisoformat(value)).days
    return 0 <= distance <= days


def detail_projection_id(detail_kind: str, subject_id: str, bundle_id: str) -> str:
    return stable_id(
        DETAIL_PREFIXES[detail_kind],
        f"{detail_kind}-detail-projection-v1",
        {"subject_id": subject_id, "bundle_id": bundle_id},
    )


def detail_path(detail_kind: str, subject_id: str) -> str:
    return f"projections/details/{detail_kind}/{subject_id}.json"


@dataclass(frozen=True)
class ProjectionInputs:
    source_records: Tuple[ReadObject, ...] = ()
    interpretations: Tuple[ReadObject, ...] = ()
    memory_atoms: Tuple[ReadObject, ...] = ()
    relations: Tuple[ReadObject, ...] = ()
    themes: Tuple[ReadObject, ...] = ()
    self_insights: Tuple[ReadObject, ...] = ()
    resource_cards: Tuple[ReadObject, ...] = ()
    read_later_intents: Tuple[ReadObject, ...] = ()

    def canonical_payload(self) -> JSONObject:
        groups: Mapping[str, Sequence[ReadObject]] = {
            "source_records": self.source_records,
            "interpretations": self.interpretations,
            "memory_atoms": self.memory_atoms,
            "relations": self.relations,
            "themes": self.themes,
            "self_insights": self.self_insights,
            "resource_cards": self.resource_cards,
            "read_later_intents": self.read_later_intents,
        }
        return {
            name: sorted((dict(item) for item in values), key=_object_sort_key)
            for name, values in groups.items()
        }

    @property
    def input_sha256(self) -> str:
        return sha256_json(self.canonical_payload())

    def visible_as_of(self, as_of: str) -> "ProjectionInputs":
        def visible(values: Tuple[ReadObject, ...]) -> Tuple[ReadObject, ...]:
            return tuple(
                item for item in values
                if not isinstance(item.get("created_at"), str)
                or date_part(str(item["created_at"])) <= as_of
            )

        return ProjectionInputs(
            source_records=visible(self.source_records),
            interpretations=visible(self.interpretations),
            memory_atoms=visible(self.memory_atoms),
            relations=visible(self.relations),
            themes=visible(self.themes),
            self_insights=visible(self.self_insights),
            resource_cards=visible(self.resource_cards),
            read_later_intents=visible(self.read_later_intents),
        )

    def latest_created_at(self) -> str:
        values = [
            str(item["created_at"])
            for item in self.all_objects()
            if isinstance(item.get("created_at"), str)
        ]
        if not values:
            return "1970-01-01T00:00:00+00:00"
        return max(values, key=timestamp_sort_key)

    def all_objects(self) -> Tuple[ReadObject, ...]:
        return (
            self.source_records
            + self.interpretations
            + self.memory_atoms
            + self.relations
            + self.themes
            + self.self_insights
            + self.resource_cards
            + self.read_later_intents
        )


def _object_sort_key(value: ReadObject) -> str:
    for field_name in (
        "record_id", "interpretation_id", "memory_atom_id", "relation_id",
        "theme_id", "insight_id", "resource_id", "intent_id",
    ):
        if field_name in value:
            return str(value[field_name])
    return canonical_json(value)


@dataclass
class ProjectionContext:
    inputs: ProjectionInputs
    as_of: str
    generated_at: str
    bundle_id: str
    previous_bundle_sha256: Optional[str] = None
    previous_landscape_sha256: Optional[str] = None
    _objects_by_id: Dict[str, ReadObject] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        for item in self.inputs.all_objects():
            identity = object_identity(item)
            if identity is not None:
                if identity[1] in self._objects_by_id:
                    raise ProjectionInputError(f"duplicate formal object id: {identity[1]}")
                self._objects_by_id[identity[1]] = item

    @property
    def input_sha256(self) -> str:
        return self.inputs.input_sha256

    def metadata(self, *, kind: str, version: str, projection_id: str, schema_version: str) -> JSONObject:
        return {
            "schema_version": schema_version,
            "kind": kind,
            "projection_version": version,
            "projection_id": projection_id,
            "bundle_id": self.bundle_id,
            "generated_at": self.generated_at,
            "as_of": self.as_of,
            "input_sha256": self.input_sha256,
        }

    def current_ref(self, item: ReadObject) -> JSONObject:
        identity = object_identity(item)
        if identity is None:
            raise ValueError("formal object has no known identity")
        ref_kind, object_id = identity
        return {
            "kind": ref_kind,
            "id": object_id,
            "revision": int(item["revision"]),
            "revision_sha256": sha256_json(item),
        }

    def resolve_ref(self, raw_ref: ReadObject) -> JSONObject:
        object_id = str(raw_ref["id"])
        current = self._objects_by_id.get(object_id)
        if current is None:
            raise ProjectionInputError(f"unresolved formal object reference: {object_id}")
        resolved = self.current_ref(current)
        if resolved["kind"] != raw_ref["kind"]:
            raise ProjectionInputError(f"formal object reference kind mismatch: {object_id}")
        if resolved["revision"] != raw_ref["revision"] or resolved["revision_sha256"] != raw_ref["revision_sha256"]:
            raise ProjectionInputError(f"formal object reference revision mismatch: {object_id}")
        return resolved

    def object_for_ref(self, raw_ref: ReadObject) -> Optional[ReadObject]:
        return self._objects_by_id.get(str(raw_ref["id"]))

    def active(self, values: Iterable[ReadObject]) -> Tuple[ReadObject, ...]:
        return tuple(item for item in values if item.get("status") != "tombstone" and item.get("lifecycle") != "tombstone" and item.get("maturity") != "tombstone")


def object_identity(item: ReadObject) -> Optional[Tuple[str, str]]:
    specification = ID_FIELDS.get(str(item.get("kind")))
    if specification is None:
        return None
    ref_kind, id_field = specification
    return ref_kind, str(item[id_field])


class ProjectionInputError(ValueError):
    """Raised when committed formal heads cannot form one consistent graph."""


def make_context(
    inputs: ProjectionInputs,
    *,
    as_of: str,
    generated_at: Optional[str] = None,
    previous_bundle_sha256: Optional[str] = None,
    previous_landscape_sha256: Optional[str] = None,
) -> ProjectionContext:
    visible_inputs = inputs.visible_as_of(as_of)
    timestamp = generated_at or (
        visible_inputs.latest_created_at()
        if visible_inputs.all_objects()
        else f"{as_of}T00:00:00+00:00"
    )
    bundle_id = stable_id(
        "prjb",
        "projection-bundle-v1",
        {
            "as_of": as_of,
            "generated_at": timestamp,
            "input_sha256": visible_inputs.input_sha256,
            "previous_bundle_sha256": previous_bundle_sha256,
            "previous_landscape_sha256": previous_landscape_sha256,
        },
    )
    return ProjectionContext(
        inputs=visible_inputs,
        as_of=as_of,
        generated_at=timestamp,
        bundle_id=bundle_id,
        previous_bundle_sha256=previous_bundle_sha256,
        previous_landscape_sha256=previous_landscape_sha256,
    )
