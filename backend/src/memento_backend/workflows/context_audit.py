"""Append-only read/writeback audit helpers."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id
from memento_backend.storage.revision_store import RevisionStore


def build_context_audit(
    *,
    operation: str,
    requested_grant_id: str,
    requested_session_id: str,
    grant_ref: Optional[Mapping[str, Any]],
    session_ref: Optional[Mapping[str, Any]],
    client_id: str,
    request_sha256: str,
    status: str,
    reason_code: str,
    pack_id: Optional[str],
    pack_sha256: Optional[str],
    accessed_refs: Sequence[Mapping[str, Any]],
    requested_at: str,
    completed_at: str,
) -> Mapping[str, Any]:
    body = {
        "operation": operation, "requested_grant_id": requested_grant_id,
        "requested_session_id": requested_session_id,
        "grant_ref": None if grant_ref is None else dict(grant_ref),
        "session_ref": None if session_ref is None else dict(session_ref),
        "client_id": client_id, "request_sha256": request_sha256, "status": status,
        "reason_code": reason_code, "pack_id": pack_id, "pack_sha256": pack_sha256,
        "accessed_refs": [dict(value) for value in accessed_refs],
        "requested_at": requested_at, "completed_at": completed_at,
    }
    audit_id = make_id("context_read_audit", "context-read-audit-v1", body)
    value = {
        "schema_version": "1.0", "kind": "memento_context_read_audit_revision", "audit_id": audit_id,
        "revision": 1, "previous_revision_sha256": None, **body,
        "created_at": completed_at, "committed_by": "workflow",
    }
    validate_contract("context-read-audit-v1.schema.json", value)
    return value


def commit_audit_idempotent(revisions: RevisionStore, audit: Mapping[str, Any]) -> Mapping[str, Any]:
    audit_id = str(audit["audit_id"])
    current = revisions.current_ref("context_read_audit", audit_id)
    if current is not None:
        if revisions.load_head("context_read_audit", audit_id) != audit:
            raise ContractError("context audit identifier collision", kind="conflict")
        return current
    return revisions.commit(audit, committed_at=str(audit["created_at"]))
