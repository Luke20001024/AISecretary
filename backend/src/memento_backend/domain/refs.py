"""Exact revision references and source evidence spans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .errors import ContractError
from .ids import sha256_bytes, validate_id, validate_relative_path, validate_sha256


OBJECT_REF_FIELDS = frozenset({"kind", "id", "revision", "revision_sha256"})
SOURCE_SPAN_FIELDS = frozenset(
    {
        "record_id",
        "record_revision",
        "record_revision_sha256",
        "source_file",
        "line_start",
        "line_end",
        "quote",
        "quote_sha256",
    }
)


def _strict_mapping(value: Any, fields: frozenset[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{name} must be an object")
    actual = frozenset(value.keys())
    if actual != fields:
        raise ContractError(
            f"{name} fields differ; missing={sorted(fields - actual)} unknown={sorted(actual - fields)}"
        )
    return value


@dataclass(frozen=True)
class ObjectRef:
    kind: str
    id: str
    revision: int
    revision_sha256: str

    @classmethod
    def from_dict(cls, value: Any) -> "ObjectRef":
        item = _strict_mapping(value, OBJECT_REF_FIELDS, "object_ref")
        if not isinstance(item["kind"], str):
            raise ContractError("object_ref.kind must be text")
        validate_id(item["kind"], item["id"], "object_ref.id")
        if type(item["revision"]) is not int or item["revision"] < 1:
            raise ContractError("object_ref.revision must be a positive integer")
        validate_sha256(item["revision_sha256"], "object_ref.revision_sha256")
        return cls(
            kind=item["kind"],
            id=item["id"],
            revision=item["revision"],
            revision_sha256=item["revision_sha256"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "revision": self.revision,
            "revision_sha256": self.revision_sha256,
        }


@dataclass(frozen=True)
class SourceSpan:
    record_id: str
    record_revision: int
    record_revision_sha256: str
    source_file: str
    line_start: int
    line_end: int
    quote: str
    quote_sha256: str

    @classmethod
    def from_dict(cls, value: Any) -> "SourceSpan":
        item = _strict_mapping(value, SOURCE_SPAN_FIELDS, "source_span")
        validate_id("source_record", item["record_id"], "source_span.record_id")
        if type(item["record_revision"]) is not int or item["record_revision"] < 1:
            raise ContractError("source_span.record_revision must be a positive integer")
        validate_sha256(item["record_revision_sha256"], "source_span.record_revision_sha256")
        validate_relative_path(item["source_file"], "source_span.source_file")
        if (
            type(item["line_start"]) is not int
            or type(item["line_end"]) is not int
            or item["line_start"] < 1
            or item["line_end"] < item["line_start"]
        ):
            raise ContractError("source_span line range is invalid")
        if not isinstance(item["quote"], str) or not item["quote"] or len(item["quote"]) > 20_000:
            raise ContractError("source_span.quote must contain source text")
        expected = sha256_bytes(item["quote"].encode("utf-8"))
        if item["quote_sha256"] != expected:
            raise ContractError("source_span.quote_sha256 does not match quote", kind="evidence")
        return cls(
            record_id=item["record_id"],
            record_revision=item["record_revision"],
            record_revision_sha256=item["record_revision_sha256"],
            source_file=item["source_file"],
            line_start=item["line_start"],
            line_end=item["line_end"],
            quote=item["quote"],
            quote_sha256=item["quote_sha256"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "record_revision": self.record_revision,
            "record_revision_sha256": self.record_revision_sha256,
            "source_file": self.source_file,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "quote": self.quote,
            "quote_sha256": self.quote_sha256,
        }
