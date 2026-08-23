"""Pure, persistence-free contracts for Memento Cognitive Secretary MVP.

Workers allocate identifiers and write files.  This module only validates and
serializes formal domain objects.  It deliberately keeps source records,
receipts, daily summaries, reusable memories, relations, landscape snapshots
and homepage projections distinct.  Generated daily summary text cannot enter
the long-term evidence channel.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, ClassVar, Mapping, Sequence

from core import ContractError, _ensure_object, _ensure_text, canonical_json, sha256_bytes

COGNITIVE_SCHEMA_VERSION = "1.0"
HOME_PROJECTION_VERSION = "cognitive-secretary-home-v1"
LANDSCAPE_PROJECTION_VERSION = "cognitive-landscape-v1"

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RECORD_RE = re.compile(r"^rec_[0-9a-f]{24}$")
RECEIPT_RE = re.compile(r"^rcp_[0-9a-f]{24}$")
CANDIDATE_MEMORY_RE = re.compile(r"^cmem_[0-9a-f]{24}$")
CANDIDATE_RELATION_RE = re.compile(r"^crel_[0-9a-f]{24}$")
REUSABLE_MEMORY_RE = re.compile(r"^rmem_[0-9a-f]{24}$")
RELATION_RE = re.compile(r"^rel_[0-9a-f]{24}$")
UNDERSTANDING_RE = re.compile(r"^mem_[0-9a-f]{24}$")
PEAK_RE = re.compile(r"^peak_[0-9a-f]{24}$")
LANDSCAPE_RE = re.compile(r"^lnd_[0-9a-f]{24}$")
SUMMARY_RE = re.compile(r"^dsum_\d{8}$")
COGNITIVE_ACTION_RE = re.compile(r"^cact_[0-9a-f]{24}$")
COGNITIVE_ACTION_RESULT_RE = re.compile(r"^cares_[0-9a-f]{24}$")

CONTENT_TYPES = frozenset({"quote", "own_idea", "observation", "question", "decision", "action", "experience", "fact", "learning"})
PURPOSES = frozenset({"find_later", "continue_thinking", "create", "future_decision", "action_clue", "preserve_only"})
UNCERTAINTIES = frozenset({"low", "medium", "high"})
STANCES = frozenset({"agree", "doubt", "reject", "inspired", "self_observation", "unresolved", "unknown"})
COGNITIVE_STATES = frozenset({"first_seen", "repeated", "supports_existing", "conflicts_existing", "revises_existing", "verified", "unknown"})
RELATION_TYPES = frozenset({"supports", "counterexample", "revises", "scope_boundary", "same_topic"})
SOURCE_TYPES = frozenset({"text", "screenshot_ocr", "voice_transcript", "image_note", "file_note"})

OBJECT_PATTERNS = {
    "source_record": RECORD_RE, "interpretation_receipt": RECEIPT_RE,
    "daily_summary": SUMMARY_RE, "reusable_memory": REUSABLE_MEMORY_RE,
    "relation": RELATION_RE, "understanding": UNDERSTANDING_RE,
    "daily_bundle": re.compile(r"^db_\d{8}$"),
}


def _fields(value: Any, exact: frozenset[str], name: str) -> dict[str, Any]:
    return _ensure_object(value, exact, name)


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ContractError(f"{name} 必须是 SHA-256")
    return value


def _id(value: Any, pattern: re.Pattern[str], name: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ContractError(f"{name} 无效")
    return value


def _date(value: Any, name: str) -> str:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise ContractError(f"{name} 必须是 YYYY-MM-DD")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"{name} 不是有效日期") from exc
    return value


def _datetime(value: Any, name: str) -> str:
    text = _ensure_text(value, name, maximum=64)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{name} 必须是带时区的 ISO-8601 时间") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{name} 必须带时区")
    return text


def _path(value: Any, name: str = "path") -> str:
    text = _ensure_text(value, name, maximum=1024)
    path = PurePosixPath(text)
    if text.startswith("/") or "\\" in text or text != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
        raise ContractError(f"{name} 必须是 vault 内 POSIX 相对路径", kind="evidence")
    return text


def _list(value: Any, name: str, *, allowed: frozenset[str] | None = None, maximum: int = 24) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ContractError(f"{name} 必须是最多 {maximum} 项的 array")
    result = tuple(_ensure_text(item, f"{name}[{index}]", maximum=600) for index, item in enumerate(value))
    if len(set(result)) != len(result):
        raise ContractError(f"{name} 不能重复")
    if allowed is not None and any(item not in allowed for item in result):
        raise ContractError(f"{name} 含不允许值")
    return result


def _revision(revision: Any, previous: Any, name: str) -> None:
    if type(revision) is not int or not 1 <= revision <= 999_999:
        raise ContractError(f"{name}.revision 必须是正整数")
    if revision == 1:
        if previous is not None:
            raise ContractError(f"{name}.revision=1 时 previous_revision_sha256 必须是 null")
    else:
        _sha(previous, f"{name}.previous_revision_sha256")


def canonical_sha256(value: "ContractObject | Mapping[str, Any]") -> str:
    payload = value.to_dict() if isinstance(value, ContractObject) else dict(value)
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def persisted_json_bytes(value: "ContractObject | Mapping[str, Any]") -> bytes:
    """Serialize one revision exactly as the durable JSON stores do.

    Formal ``revision_sha256`` references bind the bytes on disk, including
    the final newline. Compact canonical JSON remains available separately for
    policy keys and other non-file identities.
    """

    payload = value.to_dict() if isinstance(value, ContractObject) else dict(value)
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def persisted_sha256(value: "ContractObject | Mapping[str, Any]") -> str:
    return sha256_bytes(persisted_json_bytes(value))


contract_sha256 = persisted_sha256


def _make_id(prefix: str, namespace: str, **payload: str) -> str:
    return prefix + sha256_bytes(canonical_json({"namespace": namespace, **payload}).encode("utf-8"))[:24]


def make_capture_record_id(capture_nonce: str) -> str:
    """Capture services allocate and persist this once; entry hash is excluded."""
    return _make_id("rec_", "capture-v1", nonce=_ensure_text(capture_nonce, "capture_nonce", maximum=512))


def make_legacy_record_id(source_file: str, source_record_anchor: str) -> str:
    """Legacy backfill ID based on locator-v1, also independent of entry hash."""
    return _make_id("rec_", "legacy-locator-v1", source_file=_path(source_file), anchor=_ensure_text(source_record_anchor, "source_record_anchor", maximum=256))


def make_receipt_id(record_id: str) -> str:
    return _make_id("rcp_", "receipt-v1", record_id=_id(record_id, RECORD_RE, "record_id"))


def make_reusable_memory_id(materialization_key: str) -> str:
    return _make_id("rmem_", "reusable-memory-v1", key=_ensure_text(materialization_key, "materialization_key", maximum=512))


def make_relation_id(materialization_key: str) -> str:
    return _make_id("rel_", "relation-v1", key=_ensure_text(materialization_key, "materialization_key", maximum=512))


def make_daily_summary_id(local_date: str) -> str:
    return "dsum_" + _date(local_date, "local_date").replace("-", "")


def make_cognitive_action_id(action_nonce: str) -> str:
    return _make_id(
        "cact_",
        "cognitive-user-action-v1",
        nonce=_ensure_text(action_nonce, "action_nonce", maximum=512),
    )


def make_cognitive_action_result_id(action_id: str) -> str:
    return _make_id(
        "cares_",
        "cognitive-action-result-v1",
        action_id=_id(action_id, COGNITIVE_ACTION_RE, "action_id"),
    )


def make_peak_id(understanding_id: str) -> str:
    return "peak_" + _id(understanding_id, UNDERSTANDING_RE, "understanding_id")[4:]


def make_landscape_id(input_hashes: Mapping[str, str], publication_nonce: str) -> str:
    """Create a new immutable snapshot identity for one publish operation.

    Input hashes identify what was projected; the worker supplies and persists
    ``publication_nonce`` so publishing the same inputs twice never overwrites
    an earlier snapshot revision.
    """
    keys = frozenset({"agent_profile_sha256", "reusable_memory_head_sha256", "relation_head_sha256", "user_action_watermark_sha256"})
    item = _fields(input_hashes, keys, "landscape.input_hashes")
    for key in keys:
        _sha(item[key], key)
    return _make_id(
        "lnd_",
        "landscape-v1",
        digest=sha256_bytes(canonical_json(item).encode("utf-8")),
        publication_nonce=_ensure_text(publication_nonce, "publication_nonce", maximum=512),
    )


class ContractObject:
    FIELDS: ClassVar[frozenset[str]]
    def to_dict(self) -> dict[str, Any]: raise NotImplementedError
    @property
    def sha256(self) -> str: return persisted_sha256(self)


OBJECT_REF_FIELDS = frozenset({"kind", "id", "revision", "revision_sha256"})


@dataclass(frozen=True)
class ObjectRef(ContractObject):
    kind: str; id: str; revision: int; revision_sha256: str
    FIELDS: ClassVar[frozenset[str]] = OBJECT_REF_FIELDS
    def __post_init__(self) -> None: validate_object_ref(self.to_dict())
    @classmethod
    def from_dict(cls, value: Any) -> "ObjectRef": return cls(**validate_object_ref(value))
    def to_dict(self) -> dict[str, Any]: return {"kind": self.kind, "id": self.id, "revision": self.revision, "revision_sha256": self.revision_sha256}


def validate_object_ref(value: Any) -> dict[str, Any]:
    item = _fields(value, OBJECT_REF_FIELDS, "object ref")
    pattern = OBJECT_PATTERNS.get(item["kind"])
    if pattern is None: raise ContractError("object ref kind 无效")
    _id(item["id"], pattern, "object_ref.id")
    if type(item["revision"]) is not int or item["revision"] < 1: raise ContractError("object_ref.revision 无效")
    _sha(item["revision_sha256"], "object_ref.revision_sha256")
    return dict(item)


COGNITIVE_ACTION_FIELDS = frozenset(
    {"schema_version", "kind", "id", "created_at", "action", "target_ref", "payload"}
)
COGNITIVE_ACTION_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "id",
        "action_id",
        "action_sha256",
        "status",
        "completed_at",
        "materialized_refs",
        "error_kind",
    }
)


def _validate_receipt_facets(value: Any, name: str) -> dict[str, Any]:
    facets = _fields(
        value,
        frozenset(
            {"content_types", "topics", "objects", "stance", "cognitive_state", "purposes"}
        ),
        name,
    )
    _list(facets["content_types"], f"{name}.content_types", allowed=CONTENT_TYPES)
    _list(facets["topics"], f"{name}.topics")
    _list(facets["objects"], f"{name}.objects")
    _list(facets["purposes"], f"{name}.purposes", allowed=PURPOSES)
    if facets["stance"] not in STANCES or facets["cognitive_state"] not in COGNITIVE_STATES:
        raise ContractError(f"{name} enum 无效")
    return facets


def _validate_cognitive_action_payload(action: str, value: Any) -> dict[str, Any] | None:
    if action in {"confirm_receipt", "original_only", "delete_reusable_memory", "delete_relation"}:
        if value is not None:
            raise ContractError(f"{action} payload 必须是 null")
        return None
    if action == "edit_receipt":
        payload = _fields(value, frozenset({"summary", "facets"}), "edit_receipt.payload")
        _ensure_text(payload["summary"], "edit_receipt.summary", maximum=600)
        _validate_receipt_facets(payload["facets"], "edit_receipt.facets")
        return payload
    if action == "edit_reusable_memory":
        payload = _fields(
            value,
            frozenset({"statement", "topics", "purposes"}),
            "edit_reusable_memory.payload",
        )
        _ensure_text(payload["statement"], "edit_reusable_memory.statement", maximum=1000)
        _list(payload["topics"], "edit_reusable_memory.topics")
        _list(payload["purposes"], "edit_reusable_memory.purposes", allowed=PURPOSES)
        return payload
    if action == "edit_relation":
        payload = _fields(value, frozenset({"type", "statement"}), "edit_relation.payload")
        if payload["type"] not in RELATION_TYPES:
            raise ContractError("edit_relation.type 无效")
        _ensure_text(payload["statement"], "edit_relation.statement", maximum=1000)
        return payload
    if action == "report_outcome":
        payload = _fields(value, frozenset({"outcome", "occurred_at"}), "report_outcome.payload")
        _ensure_text(payload["outcome"], "report_outcome.outcome", maximum=1200)
        _datetime(payload["occurred_at"], "report_outcome.occurred_at")
        return payload
    raise ContractError("cognitive action 无效")


@dataclass(frozen=True)
class CognitiveUserAction(ContractObject):
    schema_version: str
    kind: str
    id: str
    created_at: str
    action: str
    target_ref: ObjectRef
    payload: Mapping[str, Any] | None
    FIELDS: ClassVar[frozenset[str]] = COGNITIVE_ACTION_FIELDS

    def __post_init__(self) -> None:
        validate_cognitive_user_action(self.to_dict())

    @classmethod
    def from_dict(cls, value: Any) -> "CognitiveUserAction":
        item = validate_cognitive_user_action(value)
        item["target_ref"] = ObjectRef.from_dict(item["target_ref"])
        return cls(**item)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "id": self.id,
            "created_at": self.created_at,
            "action": self.action,
            "target_ref": self.target_ref.to_dict(),
            "payload": None if self.payload is None else dict(self.payload),
        }


def validate_cognitive_user_action(value: Any) -> dict[str, Any]:
    item = _fields(value, COGNITIVE_ACTION_FIELDS, "cognitive user action")
    if item["schema_version"] != COGNITIVE_SCHEMA_VERSION or item["kind"] != "memento_cognitive_user_action":
        raise ContractError("cognitive user action schema/kind 无效")
    _id(item["id"], COGNITIVE_ACTION_RE, "cognitive action.id")
    _datetime(item["created_at"], "cognitive action.created_at")
    target = ObjectRef.from_dict(item["target_ref"])
    target_kinds = {
        "confirm_receipt": "interpretation_receipt",
        "edit_receipt": "interpretation_receipt",
        "original_only": "interpretation_receipt",
        "edit_reusable_memory": "reusable_memory",
        "delete_reusable_memory": "reusable_memory",
        "edit_relation": "relation",
        "delete_relation": "relation",
        "report_outcome": "reusable_memory",
    }
    expected_kind = target_kinds.get(item["action"])
    if expected_kind is None or target.kind != expected_kind:
        raise ContractError("cognitive action 与 target kind 不匹配", kind="action")
    _validate_cognitive_action_payload(item["action"], item["payload"])
    return dict(item)


@dataclass(frozen=True)
class CognitiveActionResult(ContractObject):
    schema_version: str
    kind: str
    id: str
    action_id: str
    action_sha256: str
    status: str
    completed_at: str
    materialized_refs: tuple[ObjectRef, ...]
    error_kind: str | None
    FIELDS: ClassVar[frozenset[str]] = COGNITIVE_ACTION_RESULT_FIELDS

    def __post_init__(self) -> None:
        validate_cognitive_action_result(self.to_dict())

    @classmethod
    def from_dict(cls, value: Any) -> "CognitiveActionResult":
        item = validate_cognitive_action_result(value)
        item["materialized_refs"] = tuple(ObjectRef.from_dict(row) for row in item["materialized_refs"])
        return cls(**item)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "id": self.id,
            "action_id": self.action_id,
            "action_sha256": self.action_sha256,
            "status": self.status,
            "completed_at": self.completed_at,
            "materialized_refs": [row.to_dict() for row in self.materialized_refs],
            "error_kind": self.error_kind,
        }


def validate_cognitive_action_result(value: Any) -> dict[str, Any]:
    item = _fields(value, COGNITIVE_ACTION_RESULT_FIELDS, "cognitive action result")
    if item["schema_version"] != COGNITIVE_SCHEMA_VERSION or item["kind"] != "memento_cognitive_action_result":
        raise ContractError("cognitive action result schema/kind 无效")
    action_id = _id(item["action_id"], COGNITIVE_ACTION_RE, "action_result.action_id")
    if item["id"] != make_cognitive_action_result_id(action_id):
        raise ContractError("cognitive action result id 与 action 不一致")
    _sha(item["action_sha256"], "action_result.action_sha256")
    _datetime(item["completed_at"], "action_result.completed_at")
    if item["status"] not in {"applied", "rejected", "conflict"}:
        raise ContractError("cognitive action result status 无效")
    if not isinstance(item["materialized_refs"], list) or len(item["materialized_refs"]) > 24:
        raise ContractError("action_result.materialized_refs 无效")
    refs = [ObjectRef.from_dict(row) for row in item["materialized_refs"]]
    if len({(row.kind, row.id, row.revision, row.revision_sha256) for row in refs}) != len(refs):
        raise ContractError("action_result.materialized_refs 不能重复")
    if item["status"] == "applied":
        if item["error_kind"] is not None:
            raise ContractError("applied action result 不得有 error_kind")
    else:
        if item["materialized_refs"]:
            raise ContractError("未应用 action 不得物化正式对象")
        if item["error_kind"] not in {"schema", "action", "evidence", "conflict", "runtime"}:
            raise ContractError("action result error_kind 无效")
    return dict(item)


SPAN_FIELDS = frozenset({"record_id", "record_revision", "record_revision_sha256", "source_file", "line_start", "line_end", "quote", "quote_sha256"})


@dataclass(frozen=True)
class SourceSpan(ContractObject):
    record_id: str; record_revision: int; record_revision_sha256: str; source_file: str; line_start: int; line_end: int; quote: str; quote_sha256: str
    FIELDS: ClassVar[frozenset[str]] = SPAN_FIELDS
    def __post_init__(self) -> None: validate_source_span(self.to_dict())
    @classmethod
    def from_dict(cls, value: Any) -> "SourceSpan": return cls(**validate_source_span(value))
    def to_dict(self) -> dict[str, Any]: return {key: getattr(self, key) for key in SPAN_FIELDS}


def validate_source_span(value: Any) -> dict[str, Any]:
    item = _fields(value, SPAN_FIELDS, "source span")
    _id(item["record_id"], RECORD_RE, "span.record_id")
    if type(item["record_revision"]) is not int or item["record_revision"] < 1: raise ContractError("span.record_revision 无效")
    _sha(item["record_revision_sha256"], "span.record_revision_sha256"); _path(item["source_file"], "span.source_file")
    if type(item["line_start"]) is not int or type(item["line_end"]) is not int or item["line_start"] < 1 or item["line_end"] < item["line_start"]: raise ContractError("span 行号无效")
    quote = _ensure_text(item["quote"], "span.quote", maximum=20_000)
    if item["quote_sha256"] != sha256_bytes(quote.encode("utf-8")): raise ContractError("span quote hash 不一致", kind="evidence")
    return dict(item)


SOURCE_FIELDS = frozenset({"schema_version", "kind", "record_id", "revision", "status", "operation", "created_at", "captured_at", "local_date", "source_type", "source_app", "source_file", "line_start", "line_end", "entry_sha256", "source_snapshot_sha256", "attachments", "ingest_origin", "previous_revision_sha256"})


@dataclass(frozen=True)
class SourceRecordRevision(ContractObject):
    schema_version: str; kind: str; record_id: str; revision: int; status: str; operation: str; created_at: str; captured_at: str; local_date: str; source_type: str; source_app: str; source_file: str; line_start: int; line_end: int; entry_sha256: str; source_snapshot_sha256: str; attachments: tuple[Mapping[str, Any], ...]; ingest_origin: str; previous_revision_sha256: str | None
    FIELDS: ClassVar[frozenset[str]] = SOURCE_FIELDS
    def __post_init__(self) -> None: validate_source_record_revision(self.to_dict())
    @classmethod
    def from_dict(cls, value: Any) -> "SourceRecordRevision":
        item = validate_source_record_revision(value); item["attachments"] = tuple(item["attachments"]); return cls(**item)
    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "kind": self.kind, "record_id": self.record_id, "revision": self.revision, "status": self.status, "operation": self.operation, "created_at": self.created_at, "captured_at": self.captured_at, "local_date": self.local_date, "source_type": self.source_type, "source_app": self.source_app, "source_file": self.source_file, "line_start": self.line_start, "line_end": self.line_end, "entry_sha256": self.entry_sha256, "source_snapshot_sha256": self.source_snapshot_sha256, "attachments": [dict(x) for x in self.attachments], "ingest_origin": self.ingest_origin, "previous_revision_sha256": self.previous_revision_sha256}


def validate_source_record_revision(value: Any) -> dict[str, Any]:
    item = _fields(value, SOURCE_FIELDS, "source record")
    if item["schema_version"] != COGNITIVE_SCHEMA_VERSION or item["kind"] != "memento_source_record_revision": raise ContractError("source record schema/kind 无效")
    _id(item["record_id"], RECORD_RE, "record_id"); _revision(item["revision"], item["previous_revision_sha256"], "source record")
    if item["status"] not in {"active", "tombstone"} or item["operation"] not in {"ingest", "source_edit", "user_delete"}: raise ContractError("source record status/operation 无效")
    if (item["status"] == "tombstone") != (item["operation"] == "user_delete"): raise ContractError("user_delete 必须对应 tombstone")
    _datetime(item["created_at"], "created_at"); _datetime(item["captured_at"], "captured_at"); _date(item["local_date"], "local_date")
    if item["source_type"] not in SOURCE_TYPES or item["ingest_origin"] not in {"capture_service", "reconciler", "legacy_import"}: raise ContractError("source record enum 无效")
    _ensure_text(item["source_app"], "source_app", maximum=240); _path(item["source_file"], "source_file")
    if type(item["line_start"]) is not int or type(item["line_end"]) is not int or item["line_start"] < 1 or item["line_end"] < item["line_start"]: raise ContractError("source record 行号无效")
    _sha(item["entry_sha256"], "entry_sha256"); _sha(item["source_snapshot_sha256"], "source_snapshot_sha256")
    if not isinstance(item["attachments"], list): raise ContractError("attachments 必须为 array")
    for attachment in item["attachments"]:
        row = _fields(attachment, frozenset({"path", "mime_type", "byte_size", "sha256"}), "attachment")
        _path(row["path"], "attachment.path"); _ensure_text(row["mime_type"], "attachment.mime_type", maximum=128); _sha(row["sha256"], "attachment.sha256")
        if type(row["byte_size"]) is not int or row["byte_size"] < 0: raise ContractError("attachment.byte_size 无效")
    return dict(item)


def validate_source_record_transition(previous: SourceRecordRevision, current: SourceRecordRevision) -> None:
    if previous.record_id != current.record_id or current.revision != previous.revision + 1 or current.previous_revision_sha256 != previous.sha256: raise ContractError("source record revision 链无效", kind="conflict")
    if previous.status == "tombstone" or current.operation not in {"source_edit", "user_delete"}: raise ContractError("source record 不得非法复活或改写", kind="conflict")


def _spans(value: Any, name: str, *, required: bool = True) -> tuple[SourceSpan, ...]:
    if not isinstance(value, list) or (required and not value) or len(value) > 24: raise ContractError(f"{name} 必须是有效 array")
    items = tuple(SourceSpan.from_dict(row) for row in value)
    if len({item.sha256 for item in items}) != len(items): raise ContractError(f"{name} 不能重复")
    return items


CANDIDATE_MEMORY_FIELDS = frozenset({"candidate_id", "statement", "memory_kind", "topics", "purposes", "uncertainty", "source_spans"})
CANDIDATE_RELATION_FIELDS = frozenset({"candidate_id", "type", "from_ref", "to_ref", "direction", "statement", "uncertainty", "source_spans"})


def _candidate_memory(value: Any, record_id: str) -> None:
    item = _fields(value, CANDIDATE_MEMORY_FIELDS, "candidate memory"); _id(item["candidate_id"], CANDIDATE_MEMORY_RE, "candidate_id")
    _ensure_text(item["statement"], "candidate.statement", maximum=600)
    if item["memory_kind"] not in CONTENT_TYPES or item["uncertainty"] not in UNCERTAINTIES: raise ContractError("candidate memory enum 无效")
    _list(item["topics"], "candidate.topics"); _list(item["purposes"], "candidate.purposes", allowed=PURPOSES)
    if any(span.record_id != record_id for span in _spans(item["source_spans"], "candidate.source_spans")): raise ContractError("candidate 只能引用当前记录", kind="evidence")


def _candidate_target(value: Any, name: str) -> None:
    item = _fields(value, OBJECT_REF_FIELDS, name)
    if item["kind"] == "candidate_memory":
        _id(item["id"], CANDIDATE_MEMORY_RE, f"{name}.id")
        if item["revision"] is not None or item["revision_sha256"] is not None: raise ContractError("candidate target 不得有 revision")
    else: validate_object_ref(item)


def _candidate_relation(value: Any, record_id: str) -> None:
    item = _fields(value, CANDIDATE_RELATION_FIELDS, "candidate relation"); _id(item["candidate_id"], CANDIDATE_RELATION_RE, "candidate_relation_id")
    if item["type"] not in RELATION_TYPES or item["direction"] not in {"directed", "undirected"} or ((item["type"] == "same_topic") != (item["direction"] == "undirected")): raise ContractError("candidate relation 类型/方向无效")
    _candidate_target(item["from_ref"], "candidate.from_ref"); _candidate_target(item["to_ref"], "candidate.to_ref")
    _ensure_text(item["statement"], "candidate relation.statement", maximum=600)
    if item["uncertainty"] not in UNCERTAINTIES: raise ContractError("candidate relation uncertainty 无效")
    if any(span.record_id != record_id for span in _spans(item["source_spans"], "candidate relation.source_spans")): raise ContractError("candidate relation 只能引用当前记录", kind="evidence")


RECEIPT_FIELDS = frozenset({"schema_version", "kind", "receipt_id", "revision", "status", "operation", "created_at", "request_id", "run_id", "record_ref", "user_action_id", "summary", "facets", "memory_candidates", "relation_candidates", "source_spans", "contract_version", "feedback_watermark_sha256", "previous_revision_sha256"})


@dataclass(frozen=True)
class InterpretationReceiptRevision(ContractObject):
    schema_version: str; kind: str; receipt_id: str; revision: int; status: str; operation: str; created_at: str; request_id: str; run_id: str; record_ref: ObjectRef; user_action_id: str | None; summary: str | None; facets: Mapping[str, Any]; memory_candidates: tuple[Mapping[str, Any], ...]; relation_candidates: tuple[Mapping[str, Any], ...]; source_spans: tuple[SourceSpan, ...]; contract_version: str; feedback_watermark_sha256: str; previous_revision_sha256: str | None
    FIELDS: ClassVar[frozenset[str]] = RECEIPT_FIELDS
    def __post_init__(self) -> None: validate_interpretation_receipt_revision(self.to_dict())
    @classmethod
    def from_dict(cls, value: Any) -> "InterpretationReceiptRevision":
        item = validate_interpretation_receipt_revision(value); item.update(record_ref=ObjectRef.from_dict(item["record_ref"]), source_spans=tuple(SourceSpan.from_dict(x) for x in item["source_spans"]), memory_candidates=tuple(item["memory_candidates"]), relation_candidates=tuple(item["relation_candidates"])); return cls(**item)
    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "kind": self.kind, "receipt_id": self.receipt_id, "revision": self.revision, "status": self.status, "operation": self.operation, "created_at": self.created_at, "request_id": self.request_id, "run_id": self.run_id, "record_ref": self.record_ref.to_dict(), "user_action_id": self.user_action_id, "summary": self.summary, "facets": dict(self.facets), "memory_candidates": [dict(x) for x in self.memory_candidates], "relation_candidates": [dict(x) for x in self.relation_candidates], "source_spans": [x.to_dict() for x in self.source_spans], "contract_version": self.contract_version, "feedback_watermark_sha256": self.feedback_watermark_sha256, "previous_revision_sha256": self.previous_revision_sha256}


def validate_interpretation_receipt_revision(value: Any) -> dict[str, Any]:
    item = _fields(value, RECEIPT_FIELDS, "receipt")
    if item["schema_version"] != COGNITIVE_SCHEMA_VERSION or item["kind"] != "memento_interpretation_receipt_revision": raise ContractError("receipt schema/kind 无效")
    _id(item["receipt_id"], RECEIPT_RE, "receipt_id"); _revision(item["revision"], item["previous_revision_sha256"], "receipt")
    if item["status"] not in {"ready", "needs_review", "original_only", "tombstone"} or item["operation"] not in {"interpret", "user_confirm", "user_edit", "original_only", "source_superseded", "tombstone"}: raise ContractError("receipt status/operation 无效")
    _datetime(item["created_at"], "created_at"); _ensure_text(item["request_id"], "request_id", maximum=64); _ensure_text(item["run_id"], "run_id", maximum=64); _sha(item["feedback_watermark_sha256"], "feedback_watermark_sha256")
    record = ObjectRef.from_dict(item["record_ref"])
    if record.kind != "source_record" or item["receipt_id"] != make_receipt_id(record.id): raise ContractError("receipt 未精确绑定 source record", kind="evidence")
    required_action = item["operation"] in {"user_confirm", "user_edit", "original_only", "tombstone"}
    if required_action != (item["user_action_id"] is not None): raise ContractError("receipt user_action 绑定无效")
    if item["user_action_id"] is not None: _ensure_text(item["user_action_id"], "user_action_id", maximum=64)
    active = item["status"] in {"ready", "needs_review"}; spans = _spans(item["source_spans"], "receipt.source_spans", required=active)
    if any(s.record_id != record.id or s.record_revision != record.revision or s.record_revision_sha256 != record.revision_sha256 for s in spans): raise ContractError("receipt span 与 source revision 不一致", kind="evidence")
    if active:
        _ensure_text(item["summary"], "receipt.summary", maximum=600)
        _validate_receipt_facets(item["facets"], "receipt.facets")
    elif item["summary"] is not None or item["facets"] != {}: raise ContractError("original_only/tombstone 不得有整理内容")
    if not isinstance(item["memory_candidates"], list) or not isinstance(item["relation_candidates"], list): raise ContractError("receipt candidates 必须是 array")
    if not active and (item["memory_candidates"] or item["relation_candidates"]): raise ContractError("inactive receipt 不得有候选")
    candidate_memory_ids: set[str] = set()
    for candidate in item["memory_candidates"]:
        _candidate_memory(candidate, record.id)
        candidate_memory_ids.add(candidate["candidate_id"])
    if len(candidate_memory_ids) != len(item["memory_candidates"]):
        raise ContractError("receipt candidate memory 不得重复")
    candidate_relation_ids: set[str] = set()
    for candidate in item["relation_candidates"]:
        _candidate_relation(candidate, record.id)
        candidate_relation_ids.add(candidate["candidate_id"])
    if len(candidate_relation_ids) != len(item["relation_candidates"]):
        raise ContractError("receipt candidate relation 不得重复")
    return dict(item)


def validate_interpretation_receipt_transition(
    previous: InterpretationReceiptRevision,
    current: InterpretationReceiptRevision,
) -> None:
    """Validate one append-only receipt transition.

    A receipt keeps the stable identity of its source record, while an edit to
    the immutable source-record chain may advance ``record_ref`` to a newer
    revision.  Only a fresh automatic interpretation may cross that source
    revision boundary.  User actions remain exact-CAS edits of the receipt
    revision they were based on.
    """

    if (
        previous.receipt_id != current.receipt_id
        or current.revision != previous.revision + 1
        or current.previous_revision_sha256 != previous.sha256
    ):
        raise ContractError("receipt revision 链无效", kind="conflict")
    if previous.status in {"original_only", "tombstone"}:
        raise ContractError("receipt 终态之后不得追加 revision", kind="conflict")

    previous_record = previous.record_ref
    current_record = current.record_ref
    if previous_record == current_record:
        return
    if (
        previous_record.kind != "source_record"
        or current_record.kind != "source_record"
        or previous_record.id != current_record.id
        or current_record.revision <= previous_record.revision
        or current.operation != "interpret"
        or current.user_action_id is not None
        or current.status not in {"ready", "needs_review"}
    ):
        raise ContractError("receipt source revision 迁移无效", kind="conflict")


SUMMARY_FIELDS = frozenset({"schema_version", "kind", "summary_id", "revision", "status", "operation", "created_at", "local_date", "overview", "themes", "changes", "unresolved_questions", "action_clues", "source_refs", "receipt_refs", "review_file", "review_sha256", "user_supplement_sha256", "previous_revision_sha256"})


@dataclass(frozen=True)
class DailySummaryRevision(ContractObject):
    schema_version: str; kind: str; summary_id: str; revision: int; status: str; operation: str; created_at: str; local_date: str; overview: str; themes: tuple[str, ...]; changes: tuple[str, ...]; unresolved_questions: tuple[str, ...]; action_clues: tuple[str, ...]; source_refs: tuple[ObjectRef, ...]; receipt_refs: tuple[ObjectRef, ...]; review_file: str; review_sha256: str | None; user_supplement_sha256: str | None; previous_revision_sha256: str | None
    FIELDS: ClassVar[frozenset[str]] = SUMMARY_FIELDS
    def __post_init__(self) -> None: validate_daily_summary_revision(self.to_dict())
    @classmethod
    def from_dict(cls, value: Any) -> "DailySummaryRevision":
        item = validate_daily_summary_revision(value); item.update(themes=tuple(item["themes"]), changes=tuple(item["changes"]), unresolved_questions=tuple(item["unresolved_questions"]), action_clues=tuple(item["action_clues"]), source_refs=tuple(ObjectRef.from_dict(x) for x in item["source_refs"]), receipt_refs=tuple(ObjectRef.from_dict(x) for x in item["receipt_refs"])); return cls(**item)
    def to_dict(self) -> dict[str, Any]: return {"schema_version": self.schema_version, "kind": self.kind, "summary_id": self.summary_id, "revision": self.revision, "status": self.status, "operation": self.operation, "created_at": self.created_at, "local_date": self.local_date, "overview": self.overview, "themes": list(self.themes), "changes": list(self.changes), "unresolved_questions": list(self.unresolved_questions), "action_clues": list(self.action_clues), "source_refs": [x.to_dict() for x in self.source_refs], "receipt_refs": [x.to_dict() for x in self.receipt_refs], "review_file": self.review_file, "review_sha256": self.review_sha256, "user_supplement_sha256": self.user_supplement_sha256, "previous_revision_sha256": self.previous_revision_sha256}


def validate_daily_summary_revision(value: Any) -> dict[str, Any]:
    item = _fields(value, SUMMARY_FIELDS, "daily summary")
    if item["schema_version"] != COGNITIVE_SCHEMA_VERSION or item["kind"] != "memento_daily_summary_revision": raise ContractError("daily summary schema/kind 无效")
    date = _date(item["local_date"], "local_date"); _id(item["summary_id"], SUMMARY_RE, "summary_id")
    if item["summary_id"] != make_daily_summary_id(date): raise ContractError("daily summary id 与日期不一致")
    _revision(item["revision"], item["previous_revision_sha256"], "daily summary")
    if item["status"] not in {"active", "tombstone"} or item["operation"] not in {"generate", "regenerate", "user_supplement_changed", "tombstone"}: raise ContractError("daily summary status/operation 无效")
    _datetime(item["created_at"], "created_at"); _ensure_text(item["overview"], "overview", maximum=1200)
    for key in ("themes", "changes", "unresolved_questions", "action_clues"): _list(item[key], key)
    if any(ObjectRef.from_dict(x).kind != "source_record" for x in item["source_refs"]) or any(ObjectRef.from_dict(x).kind != "interpretation_receipt" for x in item["receipt_refs"]): raise ContractError("daily summary refs 无效")
    _path(item["review_file"], "review_file")
    for key in ("review_sha256", "user_supplement_sha256"):
        if item[key] is not None: _sha(item[key], key)
    return dict(item)


MEMORY_FIELDS = frozenset({"schema_version", "kind", "memory_id", "revision", "status", "operation", "created_at", "statement", "memory_kind", "topics", "purposes", "uncertainty", "source_spans", "origin_receipt_refs", "provenance", "previous_revision_sha256"})


@dataclass(frozen=True)
class ReusableMemoryRevision(ContractObject):
    schema_version: str; kind: str; memory_id: str; revision: int; status: str; operation: str; created_at: str; statement: str; memory_kind: str; topics: tuple[str, ...]; purposes: tuple[str, ...]; uncertainty: str; source_spans: tuple[SourceSpan, ...]; origin_receipt_refs: tuple[ObjectRef, ...]; provenance: Mapping[str, Any]; previous_revision_sha256: str | None
    FIELDS: ClassVar[frozenset[str]] = MEMORY_FIELDS
    def __post_init__(self) -> None: validate_reusable_memory_revision(self.to_dict())
    @classmethod
    def from_dict(cls, value: Any) -> "ReusableMemoryRevision":
        item = validate_reusable_memory_revision(value); item.update(topics=tuple(item["topics"]), purposes=tuple(item["purposes"]), source_spans=tuple(SourceSpan.from_dict(x) for x in item["source_spans"]), origin_receipt_refs=tuple(ObjectRef.from_dict(x) for x in item["origin_receipt_refs"])); return cls(**item)
    def to_dict(self) -> dict[str, Any]: return {"schema_version": self.schema_version, "kind": self.kind, "memory_id": self.memory_id, "revision": self.revision, "status": self.status, "operation": self.operation, "created_at": self.created_at, "statement": self.statement, "memory_kind": self.memory_kind, "topics": list(self.topics), "purposes": list(self.purposes), "uncertainty": self.uncertainty, "source_spans": [x.to_dict() for x in self.source_spans], "origin_receipt_refs": [x.to_dict() for x in self.origin_receipt_refs], "provenance": dict(self.provenance), "previous_revision_sha256": self.previous_revision_sha256}


def _provenance(value: Any, name: str) -> dict[str, Any]:
    item = _fields(value, frozenset({"origin", "run_id", "bundle_id", "bundle_revision", "user_action_id"}), name)
    if item["origin"] not in {"daily_integrator", "feedback_recompute", "user", "agent_v1_adapter"}: raise ContractError(f"{name}.origin 无效")
    _ensure_text(item["run_id"], f"{name}.run_id", maximum=64); _ensure_text(item["bundle_id"], f"{name}.bundle_id", maximum=64)
    if type(item["bundle_revision"]) is not int or item["bundle_revision"] < 1: raise ContractError(f"{name}.bundle_revision 无效")
    if item["user_action_id"] is not None: _ensure_text(item["user_action_id"], f"{name}.user_action_id", maximum=64)
    return item


def validate_reusable_memory_revision(value: Any) -> dict[str, Any]:
    item = _fields(value, MEMORY_FIELDS, "reusable memory")
    if item["schema_version"] != COGNITIVE_SCHEMA_VERSION or item["kind"] != "memento_reusable_memory_revision": raise ContractError("reusable memory schema/kind 无效")
    _id(item["memory_id"], REUSABLE_MEMORY_RE, "memory_id"); _revision(item["revision"], item["previous_revision_sha256"], "reusable memory")
    if item["status"] not in {"active", "tombstone"} or item["operation"] not in {"new", "revise", "user_edit", "tombstone"}: raise ContractError("reusable memory status/operation 无效")
    if item["revision"] == 1 and item["operation"] != "new": raise ContractError("reusable memory revision 1 必须为 new")
    _datetime(item["created_at"], "created_at"); _ensure_text(item["statement"], "memory.statement", maximum=1000)
    if item["memory_kind"] not in CONTENT_TYPES or item["uncertainty"] not in UNCERTAINTIES: raise ContractError("reusable memory enum 无效")
    _list(item["topics"], "memory.topics"); _list(item["purposes"], "memory.purposes", allowed=PURPOSES); _spans(item["source_spans"], "memory.source_spans")
    if any(ObjectRef.from_dict(x).kind != "interpretation_receipt" for x in item["origin_receipt_refs"]): raise ContractError("origin_receipt_refs 必须是 receipt")
    provenance = _provenance(item["provenance"], "memory.provenance")
    if item["operation"] in {"user_edit", "tombstone"} and provenance["user_action_id"] is None: raise ContractError("用户修改必须绑定 user_action")
    return dict(item)


RELATION_FIELDS = frozenset({"schema_version", "kind", "relation_id", "revision", "status", "operation", "created_at", "type", "from_ref", "to_ref", "direction", "statement", "uncertainty", "source_spans", "valid_from", "provenance", "previous_revision_sha256"})


@dataclass(frozen=True)
class RelationRevision(ContractObject):
    schema_version: str; kind: str; relation_id: str; revision: int; status: str; operation: str; created_at: str; type: str; from_ref: ObjectRef; to_ref: ObjectRef; direction: str; statement: str; uncertainty: str; source_spans: tuple[SourceSpan, ...]; valid_from: str; provenance: Mapping[str, Any]; previous_revision_sha256: str | None
    FIELDS: ClassVar[frozenset[str]] = RELATION_FIELDS
    def __post_init__(self) -> None: validate_relation_revision(self.to_dict())
    @classmethod
    def from_dict(cls, value: Any) -> "RelationRevision":
        item = validate_relation_revision(value); item.update(from_ref=ObjectRef.from_dict(item["from_ref"]), to_ref=ObjectRef.from_dict(item["to_ref"]), source_spans=tuple(SourceSpan.from_dict(x) for x in item["source_spans"])); return cls(**item)
    def to_dict(self) -> dict[str, Any]: return {"schema_version": self.schema_version, "kind": self.kind, "relation_id": self.relation_id, "revision": self.revision, "status": self.status, "operation": self.operation, "created_at": self.created_at, "type": self.type, "from_ref": self.from_ref.to_dict(), "to_ref": self.to_ref.to_dict(), "direction": self.direction, "statement": self.statement, "uncertainty": self.uncertainty, "source_spans": [x.to_dict() for x in self.source_spans], "valid_from": self.valid_from, "provenance": dict(self.provenance), "previous_revision_sha256": self.previous_revision_sha256}


def validate_relation_revision(value: Any) -> dict[str, Any]:
    item = _fields(value, RELATION_FIELDS, "relation")
    if item["schema_version"] != COGNITIVE_SCHEMA_VERSION or item["kind"] != "memento_relation_revision": raise ContractError("relation schema/kind 无效")
    _id(item["relation_id"], RELATION_RE, "relation_id"); _revision(item["revision"], item["previous_revision_sha256"], "relation")
    if item["status"] not in {"active", "tombstone"} or item["operation"] not in {"new", "revise", "user_edit", "tombstone"}: raise ContractError("relation status/operation 无效")
    if item["revision"] == 1 and item["operation"] != "new": raise ContractError("relation revision 1 必须为 new")
    if item["type"] not in RELATION_TYPES or item["direction"] not in {"directed", "undirected"} or ((item["type"] == "same_topic") != (item["direction"] == "undirected")): raise ContractError("relation type/direction 无效")
    left, right = ObjectRef.from_dict(item["from_ref"]), ObjectRef.from_dict(item["to_ref"])
    if left.kind not in {"reusable_memory", "understanding"} or right.kind not in {"reusable_memory", "understanding"} or left == right: raise ContractError("正式 relation 端点无效")
    _datetime(item["created_at"], "created_at"); _ensure_text(item["statement"], "relation.statement", maximum=1000)
    if item["uncertainty"] not in UNCERTAINTIES: raise ContractError("relation uncertainty 无效")
    _spans(item["source_spans"], "relation.source_spans"); _date(item["valid_from"], "valid_from")
    provenance = _provenance(item["provenance"], "relation.provenance")
    if item["operation"] in {"user_edit", "tombstone"} and provenance["user_action_id"] is None: raise ContractError("用户修改必须绑定 user_action")
    return dict(item)


def validate_long_term_evidence_refs(refs: Sequence[ObjectRef | Mapping[str, Any]]) -> tuple[ObjectRef, ...]:
    """Stops daily summary prose from becoming long-term Agent evidence."""
    items = tuple(row if isinstance(row, ObjectRef) else ObjectRef.from_dict(row) for row in refs)
    if any(row.kind == "daily_summary" for row in items): raise ContractError("每日总结不能作为长期理解 evidence", kind="evidence")
    if any(row.kind not in {"source_record", "interpretation_receipt", "reusable_memory", "relation"} for row in items): raise ContractError("长期理解输入必须能回到原始记录", kind="evidence")
    return items


PEAK_FIELDS = frozenset({"peak_id", "understanding_ref", "x", "y", "elevation", "evidence_count", "counterevidence_count", "recent_change", "lifecycle"})
NODE_FIELDS = frozenset({"memory_ref", "x", "y", "state", "recent"})
EDGE_FIELDS = frozenset({"relation_ref", "from_id", "to_id", "type"})
LANDSCAPE_FIELDS = frozenset({"schema_version", "kind", "snapshot_id", "created_at", "as_of", "projection_version", "input_hashes", "summary", "terrain", "peaks", "nodes", "edges", "previous_snapshot_sha256"})


def _unit(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= float(value) <= 1: raise ContractError(f"{name} 必须在 0 到 1")
    return float(value)


@dataclass(frozen=True)
class LandscapeSnapshot(ContractObject):
    schema_version: str; kind: str; snapshot_id: str; created_at: str; as_of: str; projection_version: str; input_hashes: Mapping[str, str]; summary: Mapping[str, int]; terrain: Mapping[str, Any]; peaks: tuple[Mapping[str, Any], ...]; nodes: tuple[Mapping[str, Any], ...]; edges: tuple[Mapping[str, Any], ...]; previous_snapshot_sha256: str | None
    FIELDS: ClassVar[frozenset[str]] = LANDSCAPE_FIELDS
    def __post_init__(self) -> None: validate_landscape_snapshot(self.to_dict())
    @classmethod
    def from_dict(cls, value: Any) -> "LandscapeSnapshot":
        item = validate_landscape_snapshot(value); item.update(peaks=tuple(item["peaks"]), nodes=tuple(item["nodes"]), edges=tuple(item["edges"])); return cls(**item)
    def to_dict(self) -> dict[str, Any]: return {"schema_version": self.schema_version, "kind": self.kind, "snapshot_id": self.snapshot_id, "created_at": self.created_at, "as_of": self.as_of, "projection_version": self.projection_version, "input_hashes": dict(self.input_hashes), "summary": dict(self.summary), "terrain": dict(self.terrain), "peaks": [dict(x) for x in self.peaks], "nodes": [dict(x) for x in self.nodes], "edges": [dict(x) for x in self.edges], "previous_snapshot_sha256": self.previous_snapshot_sha256}


def validate_landscape_snapshot(value: Any) -> dict[str, Any]:
    item = _fields(value, LANDSCAPE_FIELDS, "landscape")
    if item["schema_version"] != COGNITIVE_SCHEMA_VERSION or item["kind"] != "memento_landscape_snapshot" or item["projection_version"] != LANDSCAPE_PROJECTION_VERSION: raise ContractError("landscape schema/kind/version 无效")
    _id(item["snapshot_id"], LANDSCAPE_RE, "snapshot_id"); _datetime(item["created_at"], "created_at"); _date(item["as_of"], "as_of")
    input_keys = frozenset({"agent_profile_sha256", "reusable_memory_head_sha256", "relation_head_sha256", "user_action_watermark_sha256"}); hashes = _fields(item["input_hashes"], input_keys, "landscape.input_hashes")
    for key in input_keys: _sha(hashes[key], key)
    summary = _fields(item["summary"], frozenset({"active_understandings", "recent_changes", "observing_candidates"}), "landscape.summary")
    if any(type(row) is not int or row < 0 for row in summary.values()): raise ContractError("landscape summary 无效")
    terrain = _fields(item["terrain"], frozenset({"algorithm_version", "grid_size", "contour_levels", "coordinate_space"}), "landscape.terrain")
    if terrain["algorithm_version"] != "stable-anchor-kde-v1" or terrain["coordinate_space"] != "normalized_0_1" or type(terrain["grid_size"]) is not int or type(terrain["contour_levels"]) is not int: raise ContractError("terrain 无效")
    if not isinstance(item["peaks"], list) or not isinstance(item["nodes"], list) or not isinstance(item["edges"], list): raise ContractError("landscape lists 无效")
    understanding_ids: set[str] = set(); memory_ids: set[str] = set(); relation_ids: set[str] = set()
    for raw in item["peaks"]:
        peak = _fields(raw, PEAK_FIELDS, "peak"); ref = ObjectRef.from_dict(peak["understanding_ref"])
        if ref.kind != "understanding" or peak["peak_id"] != make_peak_id(ref.id): raise ContractError("peak 只能来自 current active Agent V1 understanding")
        _id(peak["peak_id"], PEAK_RE, "peak_id"); _unit(peak["x"], "peak.x"); _unit(peak["y"], "peak.y"); _unit(peak["elevation"], "peak.elevation")
        if type(peak["evidence_count"]) is not int or type(peak["counterevidence_count"]) is not int or peak["evidence_count"] < 0 or peak["counterevidence_count"] < 0 or type(peak["recent_change"]) is not bool or peak["lifecycle"] not in {"active", "tension", "dormant"}: raise ContractError("peak 字段无效")
        if ref.id in understanding_ids: raise ContractError("peak understanding 不得重复")
        understanding_ids.add(ref.id)
    for raw in item["nodes"]:
        node = _fields(raw, NODE_FIELDS, "node"); ref = ObjectRef.from_dict(node["memory_ref"])
        if ref.kind != "reusable_memory" or node["state"] != "committed" or type(node["recent"]) is not bool: raise ContractError("node 必须是 committed reusable memory")
        _unit(node["x"], "node.x"); _unit(node["y"], "node.y")
        if ref.id in memory_ids: raise ContractError("node memory 不得重复")
        memory_ids.add(ref.id)
    for raw in item["edges"]:
        edge = _fields(raw, EDGE_FIELDS, "edge"); ref = ObjectRef.from_dict(edge["relation_ref"])
        if ref.kind != "relation" or edge["type"] not in RELATION_TYPES or edge["from_id"] == edge["to_id"] or edge["from_id"] not in memory_ids | understanding_ids or edge["to_id"] not in memory_ids | understanding_ids: raise ContractError("edge 必须绑定当前正式图谱")
        if ref.id in relation_ids: raise ContractError("edge relation 不得重复")
        relation_ids.add(ref.id)
    if summary["active_understandings"] != len(understanding_ids): raise ContractError("summary.active_understandings 与 peaks 不一致")
    if item["previous_snapshot_sha256"] is not None: _sha(item["previous_snapshot_sha256"], "previous_snapshot_sha256")
    return dict(item)


def _point(identifier: str, axis: str) -> float:
    return int(sha256_bytes(f"{axis}:{identifier}".encode())[:8], 16) / 0xFFFFFFFF


def build_landscape_snapshot(*, as_of: str, created_at: str, input_hashes: Mapping[str, str], publication_nonce: str, active_understandings: Sequence[Mapping[str, Any]], reusable_memories: Sequence[ReusableMemoryRevision], relations: Sequence[RelationRevision], previous_snapshot_sha256: str | None = None) -> LandscapeSnapshot:
    """Project only current active profile understandings and committed heads."""
    _date(as_of, "as_of"); _datetime(created_at, "created_at")
    heads: dict[str, Mapping[str, Any]] = {}
    for raw in active_understandings:
        if not isinstance(raw, Mapping): raise ContractError("understanding head 必须是 object")
        memory_id = _id(raw.get("memory_id"), UNDERSTANDING_RE, "understanding.memory_id")
        if raw.get("status") != "active": continue
        if type(raw.get("revision")) is not int or raw["revision"] < 1: raise ContractError("understanding.revision 无效")
        _sha(raw.get("revision_sha256"), "understanding.revision_sha256")
        heads[memory_id] = raw
    reusable = {row.memory_id: row for row in reusable_memories if row.status == "active"}
    formal_relations = [row for row in relations if row.status == "active" and row.from_ref.id in reusable and row.to_ref.id in heads]
    peaks = []
    for memory_id, raw in sorted(heads.items()):
        ref = ObjectRef("understanding", memory_id, raw["revision"], raw["revision_sha256"])
        evidence = int(raw.get("evidence_count", 0)); counter = int(raw.get("counterevidence_count", 0)); lifecycle = raw.get("lifecycle", "active")
        if evidence < 0 or counter < 0 or lifecycle not in {"active", "tension", "dormant"}: raise ContractError("understanding profile 字段无效")
        peaks.append({"peak_id": make_peak_id(memory_id), "understanding_ref": ref.to_dict(), "x": _point(memory_id, "x"), "y": _point(memory_id, "y"), "elevation": min(1.0, .25 + min(evidence, 12) / 16), "evidence_count": evidence, "counterevidence_count": counter, "recent_change": bool(raw.get("recent_change", False)), "lifecycle": lifecycle})
    nodes = []
    for memory in sorted(reusable.values(), key=lambda row: row.memory_id):
        if any(row.from_ref.id == memory.memory_id for row in formal_relations): nodes.append({"memory_ref": ObjectRef("reusable_memory", memory.memory_id, memory.revision, memory.sha256).to_dict(), "x": _point(memory.memory_id, "x"), "y": _point(memory.memory_id, "y"), "state": "committed", "recent": memory.created_at[:10] == as_of})
    edges = [{"relation_ref": ObjectRef("relation", row.relation_id, row.revision, row.sha256).to_dict(), "from_id": row.from_ref.id, "to_id": row.to_ref.id, "type": row.type} for row in formal_relations]
    return LandscapeSnapshot(COGNITIVE_SCHEMA_VERSION, "memento_landscape_snapshot", make_landscape_id(input_hashes, publication_nonce), created_at, as_of, LANDSCAPE_PROJECTION_VERSION, dict(input_hashes), {"active_understandings": len(peaks), "recent_changes": sum(bool(row["recent_change"]) for row in peaks), "observing_candidates": 0}, {"algorithm_version": "stable-anchor-kde-v1", "grid_size": 96, "contour_levels": 18, "coordinate_space": "normalized_0_1"}, tuple(peaks), tuple(nodes), tuple(edges), previous_snapshot_sha256)


HOME_RECORD_FIELDS = frozenset({"record_ref", "receipt_ref", "captured_at", "source_type", "source_app", "status", "summary", "content_types", "topics", "purposes", "memory_refs", "understanding_refs"})
HOME_FIELDS = frozenset({"schema_version", "kind", "projection_version", "generated_at", "local_date", "input_hashes", "landscape_ref", "landscape_summary", "today_status", "records", "schedule", "warnings"})


def validate_home_projection(value: Any) -> dict[str, Any]:
    item = _fields(value, HOME_FIELDS, "home projection")
    if item["schema_version"] != COGNITIVE_SCHEMA_VERSION or item["kind"] != "memento_home_projection" or item["projection_version"] != HOME_PROJECTION_VERSION: raise ContractError("home projection schema/kind/version 无效")
    _datetime(item["generated_at"], "generated_at"); _date(item["local_date"], "local_date")
    input_keys = frozenset({"record_head_sha256", "receipt_head_sha256", "daily_bundle_head_sha256", "agent_profile_sha256", "landscape_snapshot_sha256", "user_action_watermark_sha256"})
    hashes = _fields(item["input_hashes"], input_keys, "home input_hashes")
    for key in input_keys: _sha(hashes[key], key)
    landscape = _fields(item["landscape_ref"], frozenset({"snapshot_id", "snapshot_sha256"}), "landscape_ref"); _id(landscape["snapshot_id"], LANDSCAPE_RE, "snapshot_id"); _sha(landscape["snapshot_sha256"], "snapshot_sha256")
    summary = _fields(item["landscape_summary"], frozenset({"active_understandings", "recent_changes", "observing_candidates"}), "landscape_summary")
    if any(type(row) is not int or row < 0 for row in summary.values()): raise ContractError("landscape_summary 无效")
    today = _fields(item["today_status"], frozenset({"saved", "interpreted", "merged", "needs_review", "daily_run_status"}), "today_status")
    if any(type(today[key]) is not int or today[key] < 0 for key in ("saved", "interpreted", "merged", "needs_review")) or today["daily_run_status"] not in {"not_started", "running", "committed", "committed_with_warnings", "no_change", "no_candidate", "no_records", "no_receipts", "stale", "error", "budget_exhausted"}: raise ContractError("today_status 无效")
    if not isinstance(item["records"], list): raise ContractError("records 必须是 array")
    seen: set[str] = set()
    for raw in item["records"]:
        record = _fields(raw, HOME_RECORD_FIELDS, "home record"); source = ObjectRef.from_dict(record["record_ref"])
        if source.kind != "source_record": raise ContractError("home record source ref 无效")
        if source.id in seen: raise ContractError("home record 重复")
        seen.add(source.id); _datetime(record["captured_at"], "captured_at")
        if record["source_type"] not in SOURCE_TYPES or record["status"] not in {"raw_saved", "processing", "ready", "needs_review", "original_only", "no_candidate", "failed", "merged"}: raise ContractError("home record 状态无效")
        if record["receipt_ref"] is None:
            if record["status"] not in {"raw_saved", "processing", "no_candidate", "failed"}: raise ContractError("已整理 home record 必须绑定 receipt")
        else:
            receipt = ObjectRef.from_dict(record["receipt_ref"])
            if receipt.kind != "interpretation_receipt" or receipt.id != make_receipt_id(source.id): raise ContractError("home record receipt ref 无效")
            if record["status"] in {"raw_saved", "processing", "no_candidate"}: raise ContractError("无回执 home record 不得绑定 receipt")
        if record["summary"] is not None: _ensure_text(record["summary"], "home record.summary", maximum=600)
        _list(record["content_types"], "content_types", allowed=CONTENT_TYPES); _list(record["topics"], "topics"); _list(record["purposes"], "purposes", allowed=PURPOSES)
        if any(ObjectRef.from_dict(x).kind != "reusable_memory" for x in record["memory_refs"]) or any(ObjectRef.from_dict(x).kind != "understanding" for x in record["understanding_refs"]): raise ContractError("home record downstream refs 无效")
        if record["status"] in {"original_only", "no_candidate"} and (record["summary"] is not None or record["content_types"] or record["topics"] or record["purposes"] or record["memory_refs"] or record["understanding_refs"]): raise ContractError(f"{record['status']} 不得携带 AI 整理内容或下游引用")
    schedule = _fields(item["schedule"], frozenset({"enabled", "hour", "minute", "next_due_at", "last_run_status"}), "schedule")
    if type(schedule["enabled"]) is not bool or type(schedule["hour"]) is not int or type(schedule["minute"]) is not int or not 0 <= schedule["hour"] <= 23 or not 0 <= schedule["minute"] <= 59: raise ContractError("schedule 无效")
    _datetime(schedule["next_due_at"], "next_due_at")
    if schedule["last_run_status"] not in {"not_started", "committed", "committed_with_warnings", "no_change", "no_candidate", "no_records", "no_receipts", "stale", "error", "budget_exhausted"}: raise ContractError("schedule status 无效")
    _list(item["warnings"], "warnings", maximum=12)
    return dict(item)


@dataclass(frozen=True)
class HomeProjection(ContractObject):
    schema_version: str; kind: str; projection_version: str; generated_at: str; local_date: str; input_hashes: Mapping[str, str]; landscape_ref: Mapping[str, str]; landscape_summary: Mapping[str, int]; today_status: Mapping[str, Any]; records: tuple[Mapping[str, Any], ...]; schedule: Mapping[str, Any]; warnings: tuple[str, ...]
    FIELDS: ClassVar[frozenset[str]] = HOME_FIELDS
    def __post_init__(self) -> None: validate_home_projection(self.to_dict())
    @classmethod
    def from_dict(cls, value: Any) -> "HomeProjection":
        item = validate_home_projection(value); item["records"] = tuple(item["records"]); item["warnings"] = tuple(item["warnings"]); return cls(**item)
    def to_dict(self) -> dict[str, Any]: return {"schema_version": self.schema_version, "kind": self.kind, "projection_version": self.projection_version, "generated_at": self.generated_at, "local_date": self.local_date, "input_hashes": dict(self.input_hashes), "landscape_ref": dict(self.landscape_ref), "landscape_summary": dict(self.landscape_summary), "today_status": dict(self.today_status), "records": [dict(x) for x in self.records], "schedule": dict(self.schedule), "warnings": list(self.warnings)}
