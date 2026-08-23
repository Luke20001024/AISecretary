"""Narrow bridge from a new V1 parser revision into the V2 source contract."""

from __future__ import annotations

from typing import Any, Mapping

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import validate_id, validate_sha256


V1_SOURCE_FIELDS = {
    "schema_version", "kind", "record_id", "revision", "status", "operation",
    "created_at", "captured_at", "local_date", "source_type", "source_app",
    "source_file", "line_start", "line_end", "entry_sha256",
    "source_snapshot_sha256", "attachments", "ingest_origin",
    "previous_revision_sha256",
}


def adapt_new_v1_source_record(value: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt only a fresh V1 ingest; edited V1 chains wait for the migration stage."""

    if set(value) != V1_SOURCE_FIELDS:
        raise ContractError("V1 source bridge received unknown or missing fields")
    if value.get("schema_version") != "1.0" or value.get("kind") != "memento_source_record_revision":
        raise ContractError("V1 source bridge received another contract")
    if value.get("revision") != 1 or value.get("previous_revision_sha256") is not None:
        raise ContractError("V1 source bridge only accepts a fresh revision", kind="migration")
    if value.get("status") != "active" or value.get("operation") != "ingest":
        raise ContractError("V1 source bridge only accepts an active ingest", kind="migration")
    validate_id("source_record", value.get("record_id"), "record_id")
    validate_sha256(value.get("entry_sha256"), "entry_sha256")
    validate_sha256(value.get("source_snapshot_sha256"), "source_snapshot_sha256")
    adapted = {**dict(value), "schema_version": "2.0", "committed_by": "workflow"}
    validate_contract("source-record-v2.schema.json", adapted)
    return adapted
