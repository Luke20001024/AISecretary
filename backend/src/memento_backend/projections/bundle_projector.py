"""Build and validate one atomic projection bundle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.ids import sha256_json
from memento_backend.domain.refs import SourceSpan

from .common import (
    JSONObject,
    ProjectionContext,
    ProjectionInputs,
    detail_projection_id,
    make_context,
    projection_sha256,
    stable_id,
)
from .detail_projector import DETAIL_SCHEMA_BY_KIND, project_details
from .home_projector import project_home
from .landscape_projector import project_landscape
from .self_projector import project_self
from .timeline_projector import project_timeline


TOP_LEVEL_SCHEMAS = {
    "home": "home-projection-v2.schema.json",
    "timeline": "timeline-projection-v1.schema.json",
    "landscape": "landscape-projection-v2.schema.json",
    "self": "self-projection-v1.schema.json",
    "detail_index": "detail-index-projection-v1.schema.json",
}

INPUT_SCHEMAS = {
    "source_records": "source-record-v2.schema.json",
    "interpretations": "record-interpretation-v2.schema.json",
    "memory_atoms": "memory-atom-v2.schema.json",
    "relations": "relation-v2.schema.json",
    "themes": "theme-v2.schema.json",
    "self_insights": "self-insight-v2.schema.json",
    "resource_cards": "resource-card-v1.schema.json",
    "read_later_intents": "read-later-intent-v1.schema.json",
}


@dataclass(frozen=True)
class ProjectionBundle:
    manifest: Mapping[str, Any]
    projections: Mapping[str, Mapping[str, Any]]

    @property
    def bundle_sha256(self) -> str:
        return sha256_json({"manifest": dict(self.manifest), "projections": dict(self.projections)})

    def projection(self, path: str) -> Mapping[str, Any]:
        return self.projections[path]


class ProjectionBundleError(ValueError):
    """Raised when files no longer form one atomic read bundle."""


def build_projection_bundle(
    inputs: ProjectionInputs,
    *,
    as_of: str,
    generated_at: Optional[str] = None,
    previous_bundle_sha256: Optional[str] = None,
    previous_landscape_sha256: Optional[str] = None,
) -> ProjectionBundle:
    _validate_inputs(inputs)
    context = make_context(
        inputs,
        as_of=as_of,
        generated_at=generated_at,
        previous_bundle_sha256=previous_bundle_sha256,
        previous_landscape_sha256=previous_landscape_sha256,
    )
    _validate_input_reference_graph(context)
    landscape = project_landscape(context)
    self_projection = project_self(context)
    timeline = project_timeline(context)
    detail_projections, detail_index = project_details(context)
    home = project_home(context, timeline=timeline, landscape=landscape, self_projection=self_projection)

    projections: Dict[str, Mapping[str, Any]] = {
        "projections/home.json": home,
        "projections/timeline.json": timeline,
        "projections/landscape.json": landscape,
        "projections/self.json": self_projection,
        "projections/detail-index.json": detail_index,
    }
    projections.update(detail_projections)
    _validate_projections(projections)

    entries = []
    for path, projection in sorted(projections.items()):
        entries.append({
            "name": _entry_name(path),
            "projection_id": projection["projection_id"],
            "path": path,
            "sha256": projection_sha256(projection),
        })
    manifest: JSONObject = {
        "schema_version": "1.0",
        "kind": "memento_projection_bundle_manifest",
        "projection_version": "memento-projection-bundle-v1",
        "bundle_id": context.bundle_id,
        "generated_at": context.generated_at,
        "as_of": context.as_of,
        "input_sha256": context.input_sha256,
        "entries": entries,
        "previous_bundle_sha256": previous_bundle_sha256,
    }
    bundle = ProjectionBundle(manifest=manifest, projections=projections)
    validate_projection_bundle_contract(bundle)
    return bundle


def validate_projection_bundle_contract(bundle: ProjectionBundle) -> None:
    validate_contract("projection-bundle-v1.schema.json", bundle.manifest)
    _validate_projections(bundle.projections)
    entries = list(bundle.manifest["entries"])
    entries_by_path = {str(entry["path"]): entry for entry in entries}
    if len(entries_by_path) != len(entries) or set(entries_by_path) != set(bundle.projections):
        raise ProjectionBundleError("manifest paths do not exactly match projection files")
    required = {"home", "timeline", "landscape", "self", "detail_index"}
    top_names = [str(entry["name"]) for entry in entries if not str(entry["name"]).endswith("_detail")]
    if set(top_names) != required or len(top_names) != len(required):
        raise ProjectionBundleError("manifest must contain one of each top-level projection")
    projection_ids = [str(entry["projection_id"]) for entry in entries]
    if len(set(projection_ids)) != len(projection_ids):
        raise ProjectionBundleError("manifest projection ids must be unique")
    for path, projection in bundle.projections.items():
        entry = entries_by_path[path]
        if entry["name"] != _entry_name(path):
            raise ProjectionBundleError(f"manifest name does not match path: {path}")
        if entry["projection_id"] != projection["projection_id"] or entry["sha256"] != projection_sha256(projection):
            raise ProjectionBundleError(f"manifest hash or identity mismatch: {path}")
        for field in ("bundle_id", "generated_at", "as_of", "input_sha256"):
            if projection[field] != bundle.manifest[field]:
                raise ProjectionBundleError(f"projection metadata mismatch: {path}:{field}")
    home = bundle.projection("projections/home.json")
    for name in ("landscape", "self", "timeline"):
        projection = bundle.projection(f"projections/{name}.json")
        reference = home[f"{name}_ref"]
        if reference["projection_id"] != projection["projection_id"] or reference["sha256"] != projection_sha256(projection):
            raise ProjectionBundleError(f"home {name} reference is stale")
    index = bundle.projection("projections/detail-index.json")
    indexed_paths = set()
    indexed_subjects = set()
    detail_by_subject: Dict[tuple[str, str], Mapping[str, Any]] = {}
    detail_roles = {
        "record": ("source_record", "rec_", "rdt_"),
        "resource": ("resource_card", "res_", "rsd_"),
        "theme": ("theme", "thm_", "tdt_"),
        "self_insight": ("self_insight", "sin_", "sdt_"),
    }
    for entry in index["entries"]:
        path = str(entry["path"])
        detail_kind = str(entry["detail_kind"])
        subject = entry["subject_ref"]
        expected_kind, subject_prefix, projection_prefix = detail_roles[detail_kind]
        expected_path = f"projections/details/{detail_kind}/{subject['id']}.json"
        subject_key = (detail_kind, str(subject["id"]))
        if (
            subject["kind"] != expected_kind
            or not str(subject["id"]).startswith(subject_prefix)
            or not str(entry["projection_id"]).startswith(projection_prefix)
            or path != expected_path
            or subject_key in indexed_subjects
        ):
            raise ProjectionBundleError(f"detail index role mismatch: {path}")
        detail = bundle.projections.get(path)
        if detail is None or entry["projection_id"] != detail["projection_id"] or entry["sha256"] != projection_sha256(detail):
            raise ProjectionBundleError(f"detail index entry is stale: {path}")
        subject_field = {
            "record": "record_ref",
            "resource": "resource_ref",
            "theme": "theme_ref",
            "self_insight": "insight_ref",
        }[detail_kind]
        if detail[subject_field] != subject:
            raise ProjectionBundleError(f"detail subject does not match its index entry: {path}")
        indexed_paths.add(path)
        indexed_subjects.add(subject_key)
        detail_by_subject[subject_key] = entry
    actual_detail_paths = {path for path in bundle.projections if path.startswith("projections/details/")}
    if indexed_paths != actual_detail_paths:
        raise ProjectionBundleError("detail index does not cover every detail projection")
    _validate_derived_identities(bundle)
    _validate_detail_links(bundle, detail_by_subject)
    _validate_cross_projection_references(bundle, detail_by_subject)


def _validate_derived_identities(bundle: ProjectionBundle) -> None:
    manifest = bundle.manifest
    landscape = bundle.projection("projections/landscape.json")
    expected_bundle_id = stable_id(
        "prjb",
        "projection-bundle-v1",
        {
            "as_of": manifest["as_of"],
            "generated_at": manifest["generated_at"],
            "input_sha256": manifest["input_sha256"],
            "previous_bundle_sha256": manifest["previous_bundle_sha256"],
            "previous_landscape_sha256": landscape["previous_projection_sha256"],
        },
    )
    if manifest["bundle_id"] != expected_bundle_id:
        raise ProjectionBundleError("bundle id does not match its deterministic identity")

    bundle_id = str(manifest["bundle_id"])
    expected_top_level_ids = {
        "projections/home.json": stable_id(
            "home", "home-projection-v2", {"bundle_id": bundle_id}
        ),
        "projections/timeline.json": stable_id(
            "tln", "timeline-projection-v1", {"bundle_id": bundle_id}
        ),
        "projections/landscape.json": stable_id(
            "lnd", "landscape-projection-v2", {"bundle_id": bundle_id}
        ),
        "projections/self.json": stable_id(
            "self", "self-projection-v1", {"bundle_id": bundle_id}
        ),
        "projections/detail-index.json": stable_id(
            "dix", "detail-index-projection-v1", {"bundle_id": bundle_id}
        ),
    }
    for path, expected_projection_id in expected_top_level_ids.items():
        if bundle.projection(path)["projection_id"] != expected_projection_id:
            raise ProjectionBundleError(f"projection id is not deterministic: {path}")

    index = bundle.projection("projections/detail-index.json")
    for entry in index["entries"]:
        subject_id = str(entry["subject_ref"]["id"])
        expected_projection_id = detail_projection_id(
            str(entry["detail_kind"]), subject_id, bundle_id
        )
        if entry["projection_id"] != expected_projection_id:
            raise ProjectionBundleError(
                f"detail projection id is not deterministic: {entry['path']}"
            )


def _validate_projections(projections: Mapping[str, Mapping[str, Any]]) -> None:
    for path, value in projections.items():
        if path.startswith("projections/details/"):
            detail_kind = path.split("/")[2]
            validate_contract(DETAIL_SCHEMA_BY_KIND[detail_kind], value)
        else:
            name = _entry_name(path)
            validate_contract(TOP_LEVEL_SCHEMAS[name], value)


def _validate_inputs(inputs: ProjectionInputs) -> None:
    for group_name, schema_name in INPUT_SCHEMAS.items():
        for value in getattr(inputs, group_name):
            validate_contract(schema_name, value)


def _validate_input_reference_graph(context: ProjectionContext) -> None:
    ref_groups = []
    for item in context.inputs.interpretations:
        ref_groups.append((item["source_record_ref"],))
    for item in context.inputs.memory_atoms:
        ref_groups.append(tuple(item["evidence_refs"]))
    for item in context.inputs.relations:
        ref_groups.append((item["from_ref"], item["to_ref"], *item["evidence_refs"]))
    for item in context.inputs.themes:
        ref_groups.append(tuple(item["evidence_refs"]))
        ref_groups.append(tuple(item["counterevidence_refs"]))
        ref_groups.append(tuple(item["relation_refs"]))
    for item in context.inputs.self_insights:
        ref_groups.append(tuple(item["theme_refs"]))
        ref_groups.append(tuple(item["support_refs"]))
        ref_groups.append(tuple(item["boundary_refs"]))
    for item in context.inputs.resource_cards:
        ref_groups.append((item["source_record_ref"],))
    for item in context.inputs.read_later_intents:
        ref_groups.append((item["resource_ref"],))
    for refs in ref_groups:
        for ref in refs:
            context.resolve_ref(ref)

    for item in context.inputs.interpretations + context.inputs.memory_atoms:
        for span in item["source_spans"]:
            _validate_source_span(context, span)
    for resource in context.inputs.resource_cards:
        source_record_id = str(resource["source_record_ref"]["id"])
        for span in resource["user_selected_spans"]:
            _validate_source_span(context, span, expected_record_id=source_record_id)


def _validate_source_span(
    context: ProjectionContext,
    span: Mapping[str, Any],
    *,
    expected_record_id: Optional[str] = None,
) -> None:
    SourceSpan.from_dict(span)
    record_id = str(span["record_id"])
    if expected_record_id is not None and record_id != expected_record_id:
        raise ProjectionBundleError(f"source span belongs to another record: {record_id}")
    record = context.object_for_ref({"id": record_id})
    if record is None or record.get("kind") != "memento_source_record_revision":
        raise ProjectionBundleError(f"unresolved source span record: {record_id}")
    current = context.current_ref(record)
    if (
        current["revision"] != span["record_revision"]
        or current["revision_sha256"] != span["record_revision_sha256"]
    ):
        raise ProjectionBundleError(f"source span revision mismatch: {record_id}")
    if (
        span["source_file"] != record["source_file"]
        or span["line_start"] < record["line_start"]
        or span["line_end"] > record["line_end"]
    ):
        raise ProjectionBundleError(f"source span location mismatch: {record_id}")


def _validate_detail_links(
    bundle: ProjectionBundle,
    details: Mapping[tuple[str, str], Mapping[str, Any]],
) -> None:
    def expect(detail_kind: str, subject_id: str, projection_id: str) -> None:
        entry = details.get((detail_kind, subject_id))
        if entry is None or entry["projection_id"] != projection_id:
            raise ProjectionBundleError(
                f"stale {detail_kind} detail link: {subject_id}:{projection_id}"
            )

    timeline = bundle.projection("projections/timeline.json")
    for item in timeline["entries"]:
        expect("record", str(item["record_ref"]["id"]), str(item["record_detail_projection_id"]))

    landscape = bundle.projection("projections/landscape.json")
    for item in landscape["peaks"]:
        expect("theme", str(item["theme_ref"]["id"]), str(item["detail_projection_id"]))

    self_projection = bundle.projection("projections/self.json")
    insights = []
    if self_projection["primary_insight"] is not None:
        insights.append(self_projection["primary_insight"])
    insights.extend(self_projection["other_insights"])
    for item in insights:
        expect("self_insight", str(item["insight_ref"]["id"]), str(item["detail_projection_id"]))
    for (detail_kind, _), entry in details.items():
        if detail_kind != "self_insight":
            continue
        detail = bundle.projection(str(entry["path"]))
        for theme in detail["themes"]:
            expect(
                "theme",
                str(theme["theme_ref"]["id"]),
                str(theme["detail_projection_id"]),
            )

    home = bundle.projection("projections/home.json")
    home_resources = _index_items_by_ref(
        home["resource_entries"], "resource_ref", "home resource"
    )
    resource_refs = {
        subject_id: bundle.projection(str(entry["path"]))["resource_ref"]
        for (detail_kind, subject_id), entry in details.items()
        if detail_kind == "resource"
    }
    for subject_id, item in home_resources.items():
        _expect_current_ref(
            item["resource_ref"], resource_refs, "home resource"
        )
    _index_items_by_ref(
        home["recent_changes"], "subject_ref", "home recent change"
    )
    for item in home["recent_changes"]:
        subject = item["subject_ref"]
        detail_kind = "theme" if subject["kind"] == "theme" else "self_insight"
        expect(detail_kind, str(subject["id"]), str(item["detail_projection_id"]))
    for item in home["resource_entries"]:
        resource = item["resource_ref"]
        if resource["kind"] != "resource_card":
            raise ProjectionBundleError("home resource entry must reference a resource card")
        expect("resource", str(resource["id"]), str(item["detail_projection_id"]))


def _validate_cross_projection_references(
    bundle: ProjectionBundle,
    details: Mapping[tuple[str, str], Mapping[str, Any]],
) -> None:
    """Keep every duplicated ref bound to the same current read-model object.

    JSON Schema protects the shape and ID namespace of each file.  This pass
    protects the semantics between files, so a re-hashed detail or Self file
    cannot point at an absent or different revision while still looking valid
    in isolation.
    """

    timeline = bundle.projection("projections/timeline.json")
    landscape = bundle.projection("projections/landscape.json")
    self_projection = bundle.projection("projections/self.json")
    home = bundle.projection("projections/home.json")

    timeline_entries = _index_items_by_ref(
        timeline["entries"], "record_ref", "timeline record"
    )
    records = {
        object_id: item["record_ref"] for object_id, item in timeline_entries.items()
    }
    interpretations = _index_optional_refs(
        (item["interpretation_ref"] for item in timeline["entries"]),
        "timeline interpretation",
    )
    peaks = _index_items_by_ref(landscape["peaks"], "theme_ref", "landscape theme")
    themes = {object_id: item["theme_ref"] for object_id, item in peaks.items()}
    nodes = _index_items_by_ref(
        landscape["nodes"], "memory_atom_ref", "landscape memory"
    )
    memories = {
        object_id: item["memory_atom_ref"] for object_id, item in nodes.items()
    }
    edges = _index_items_by_ref(
        landscape["edges"], "relation_ref", "landscape relation"
    )
    relations = {
        object_id: item["relation_ref"] for object_id, item in edges.items()
    }
    insight_items = []
    if self_projection["primary_insight"] is not None:
        insight_items.append(self_projection["primary_insight"])
    insight_items.extend(self_projection["other_insights"])
    insights_by_id = _index_items_by_ref(
        insight_items, "insight_ref", "self insight"
    )
    insights = {
        object_id: item["insight_ref"] for object_id, item in insights_by_id.items()
    }
    domain_refs: Dict[str, Mapping[str, Any]] = {}
    domain_refs.update(themes)
    domain_refs.update(memories)
    domain_refs.update(insights)

    for node in landscape["nodes"]:
        for ref in node["theme_refs"]:
            _expect_current_ref(ref, themes, "landscape node theme")
    terrain_endpoint_ids = set(themes) | set(memories)
    for edge in landscape["edges"]:
        if (
            str(edge["from_id"]) not in terrain_endpoint_ids
            or str(edge["to_id"]) not in terrain_endpoint_ids
        ):
            raise ProjectionBundleError("landscape edge has an absent endpoint")
    expected_landscape_summary = {
        "active_themes": sum(
            1
            for item in landscape["peaks"]
            if item["lifecycle"] in {"active", "tension"}
        ),
        "recent_changes": sum(
            1 for item in landscape["peaks"] if item["recent_change"]
        ),
        "forming_themes": sum(
            1 for item in landscape["peaks"] if item["lifecycle"] == "forming"
        ),
    }
    if landscape["summary"] != expected_landscape_summary:
        raise ProjectionBundleError("landscape summary disagrees with its peaks")

    today_entries = [
        item for item in timeline["entries"] if item["local_date"] == home["as_of"]
    ]
    expected_today_status = {
        "saved": len(today_entries),
        "interpreted": sum(
            1 for item in today_entries if item["interpretation_ref"] is not None
        ),
        "connected": sum(
            1 for item in today_entries if item["status"] == "connected"
        ),
        "needs_review": sum(
            1 for item in today_entries if item["status"] == "needs_review"
        ),
    }
    for field, expected in expected_today_status.items():
        if home["today_status"][field] != expected:
            raise ProjectionBundleError(
                f"home today_status disagrees with timeline: {field}"
            )

    expected_detail_subjects = {
        "record": set(records),
        "theme": set(themes),
        "self_insight": set(insights),
    }
    for detail_kind, expected_ids in expected_detail_subjects.items():
        indexed_ids = {
            subject_id
            for indexed_kind, subject_id in details
            if indexed_kind == detail_kind
        }
        if indexed_ids != expected_ids:
            raise ProjectionBundleError(
                f"{detail_kind} details do not match their top-level subjects"
            )

    for item in timeline["entries"]:
        for ref in item["theme_refs"]:
            _expect_current_ref(ref, themes, "timeline theme")
    for ref in self_projection["related_theme_refs"]:
        _expect_current_ref(ref, themes, "self related theme")
    for item in insight_items:
        for ref in item["theme_refs"]:
            _expect_current_ref(ref, themes, "self insight theme")
    for item in self_projection["boundaries"]:
        _expect_current_ref(item["insight_ref"], insights, "self boundary insight")
        _expect_current_ref(item["support_ref"], domain_refs, "self boundary support")
    for item in home["recent_changes"]:
        subject = item["subject_ref"]
        inventory = themes if subject["kind"] == "theme" else insights
        _expect_current_ref(subject, inventory, "home recent change")

    record_details = {
        subject_id: bundle.projection(str(entry["path"]))
        for (detail_kind, subject_id), entry in details.items()
        if detail_kind == "record"
    }
    extra_relations: Dict[str, Mapping[str, Any]] = {}
    for (detail_kind, subject_id), entry in details.items():
        detail = bundle.projection(str(entry["path"]))
        if detail_kind == "record":
            timeline_item = timeline_entries[subject_id]
            _expect_current_ref(detail["record_ref"], records, "record detail subject")
            expected_interpretation = timeline_item["interpretation_ref"]
            actual_interpretation = detail["interpretation"]
            if expected_interpretation is None:
                if actual_interpretation is not None:
                    raise ProjectionBundleError(
                        f"record detail has an unlisted interpretation: {subject_id}"
                    )
            else:
                if (
                    actual_interpretation is None
                    or actual_interpretation["interpretation_ref"]
                    != expected_interpretation
                ):
                    raise ProjectionBundleError(
                        f"record detail interpretation is stale: {subject_id}"
                    )
                _expect_current_ref(
                    expected_interpretation,
                    interpretations,
                    "record detail interpretation",
                )
                for span in actual_interpretation["source_spans"]:
                    _validate_projected_source_span(
                        span, record_details, expected_record_id=subject_id
                    )
            for ref in detail["memory_atom_refs"]:
                _expect_current_ref(ref, memories, "record detail memory")
            for ref in detail["theme_refs"]:
                _expect_current_ref(ref, themes, "record detail theme")
        elif detail_kind == "resource":
            _expect_current_ref(
                detail["source_record_ref"], records, "resource detail source record"
            )
            for span in detail["user_selected_spans"]:
                _validate_projected_source_span(
                    span,
                    record_details,
                    expected_record_id=str(detail["source_record_ref"]["id"]),
                )
            _reject_duplicate_refs(
                (item["intent_ref"] for item in detail["read_later_intents"]),
                "resource detail read-later intent",
            )
        elif detail_kind == "theme":
            _expect_current_ref(detail["theme_ref"], themes, "theme detail subject")
            peak = peaks[subject_id]
            for field in ("title", "statement", "lifecycle"):
                if detail[field] != peak[field]:
                    raise ProjectionBundleError(
                        f"theme detail disagrees with landscape: {subject_id}:{field}"
                    )
            expected_record_ids = set()
            for atom in detail["memory_atoms"]:
                _expect_current_ref(
                    atom["memory_atom_ref"], memories, "theme detail memory"
                )
                for span in atom["source_spans"]:
                    _validate_projected_source_span(span, record_details)
                    expected_record_ids.add(str(span["record_id"]))
            for ref in detail["counterevidence_refs"]:
                _expect_current_ref(ref, memories, "theme counterevidence")
            for relation in detail["relations"]:
                relation_ref = relation["relation_ref"]
                relation_id = str(relation_ref["id"])
                if relation_id in relations:
                    _expect_current_ref(
                        relation_ref, relations, "theme detail relation"
                    )
                previous = extra_relations.get(relation_id)
                if previous is not None and previous != relation_ref:
                    raise ProjectionBundleError(
                        f"theme details disagree on relation revision: {relation_id}"
                    )
                extra_relations[relation_id] = relation_ref
                _expect_current_ref(
                    relation["from_ref"], domain_refs, "theme relation endpoint"
                )
                _expect_current_ref(
                    relation["to_ref"], domain_refs, "theme relation endpoint"
                )
            for ref in detail["record_refs"]:
                _expect_current_ref(ref, records, "theme detail record")
            if {str(ref["id"]) for ref in detail["record_refs"]} != expected_record_ids:
                raise ProjectionBundleError(
                    f"theme detail records do not match its evidence spans: {subject_id}"
                )
        elif detail_kind == "self_insight":
            _expect_current_ref(
                detail["insight_ref"], insights, "self detail subject"
            )
            self_item = insights_by_id[subject_id]
            detail_theme_refs = []
            for theme in detail["themes"]:
                ref = theme["theme_ref"]
                _expect_current_ref(ref, themes, "self detail theme")
                peak = peaks[str(ref["id"])]
                if theme["title"] != peak["title"] or theme["statement"] != peak["statement"]:
                    raise ProjectionBundleError(
                        f"self detail theme copy is stale: {ref['id']}"
                    )
                detail_theme_refs.append(ref)
            if _ref_keys(detail_theme_refs) != _ref_keys(self_item["theme_refs"]):
                raise ProjectionBundleError(
                    f"self detail themes disagree with SelfProjection: {subject_id}"
                )
            if (
                len(detail_theme_refs) != len(self_item["theme_refs"])
                or len(detail["support_refs"]) != self_item["support_count"]
                or len(detail["boundary_refs"]) != self_item["boundary_count"]
            ):
                raise ProjectionBundleError(
                    f"self detail counts disagree with SelfProjection: {subject_id}"
                )
            for ref in detail["support_refs"]:
                _expect_current_ref(ref, domain_refs, "self detail support")
            for ref in detail["boundary_refs"]:
                _expect_current_ref(ref, domain_refs, "self detail boundary")


def _index_items_by_ref(
    values: Iterable[Mapping[str, Any]],
    ref_field: str,
    label: str,
) -> Dict[str, Mapping[str, Any]]:
    result: Dict[str, Mapping[str, Any]] = {}
    for item in values:
        ref = item[ref_field]
        object_id = str(ref["id"])
        if object_id in result:
            raise ProjectionBundleError(f"duplicate {label}: {object_id}")
        result[object_id] = item
    return result


def _index_optional_refs(
    values: Iterable[Optional[Mapping[str, Any]]],
    label: str,
) -> Dict[str, Mapping[str, Any]]:
    result: Dict[str, Mapping[str, Any]] = {}
    for ref in values:
        if ref is None:
            continue
        object_id = str(ref["id"])
        previous = result.get(object_id)
        if previous is not None and previous != ref:
            raise ProjectionBundleError(f"duplicate {label}: {object_id}")
        result[object_id] = ref
    return result


def _expect_current_ref(
    ref: Mapping[str, Any],
    inventory: Mapping[str, Mapping[str, Any]],
    label: str,
) -> None:
    object_id = str(ref["id"])
    if inventory.get(object_id) != ref:
        raise ProjectionBundleError(f"{label} is absent or stale: {object_id}")


def _reject_duplicate_refs(
    refs: Iterable[Mapping[str, Any]],
    label: str,
) -> None:
    seen: Dict[str, Mapping[str, Any]] = {}
    for ref in refs:
        object_id = str(ref["id"])
        if object_id in seen:
            raise ProjectionBundleError(f"duplicate {label}: {object_id}")
        seen[object_id] = ref


def _ref_keys(refs: Iterable[Mapping[str, Any]]) -> set[Tuple[str, str, int, str]]:
    return {
        (
            str(ref["kind"]),
            str(ref["id"]),
            int(ref["revision"]),
            str(ref["revision_sha256"]),
        )
        for ref in refs
    }


def _validate_projected_source_span(
    span: Mapping[str, Any],
    record_details: Mapping[str, Mapping[str, Any]],
    *,
    expected_record_id: Optional[str] = None,
) -> None:
    try:
        SourceSpan.from_dict(span)
    except ValueError as exc:
        raise ProjectionBundleError("projected source span is invalid") from exc
    record_id = str(span["record_id"])
    if expected_record_id is not None and record_id != expected_record_id:
        raise ProjectionBundleError(
            f"projected source span belongs to another record: {record_id}"
        )
    record_detail = record_details.get(record_id)
    if record_detail is None:
        raise ProjectionBundleError(
            f"projected source span record is absent: {record_id}"
        )
    record_ref = record_detail["record_ref"]
    source = record_detail["source"]
    if (
        record_ref["revision"] != span["record_revision"]
        or record_ref["revision_sha256"] != span["record_revision_sha256"]
        or source["source_file"] != span["source_file"]
        or span["line_start"] < source["line_start"]
        or span["line_end"] > source["line_end"]
    ):
        raise ProjectionBundleError(
            f"projected source span does not match record detail: {record_id}"
        )


def _entry_name(path: str) -> str:
    if path.startswith("projections/details/"):
        return f"{path.split('/')[2]}_detail"
    return path.rsplit("/", 1)[-1].removesuffix(".json").replace("-", "_")
