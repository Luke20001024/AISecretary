from __future__ import annotations

import copy

import pytest

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.ids import validate_id


def test_r8_identifier_namespaces_are_isolated() -> None:
    validate_id("context_grant", "grt_111111111111111111111111")
    validate_id("external_session", "ses_222222222222222222222222")
    validate_id("context_pack", "ctxp_333333333333333333333333")
    validate_id("context_read_audit", "aud_444444444444444444444444")
    validate_id("external_trace", "xtr_555555555555555555555555")
    with pytest.raises(ValueError):
        validate_id("theme", "sin_111111111111111111111111")


def test_context_grant_and_revocation_invariants() -> None:
    active = {
        "schema_version": "1.0", "kind": "memento_context_grant_revision",
        "grant_id": "grt_111111111111111111111111", "revision": 1,
        "previous_revision_sha256": None, "status": "active", "operation": "grant",
        "client_id": "research-ai", "allowed_kinds": ["self_insight", "theme"],
        "topic_scope": ["产品工作"], "time_scope": None, "max_sensitivity": "normal",
        "allow_source_quotes": False, "allowed_writeback": ["outcome"],
        "expires_at": "2026-08-24T10:00:00+08:00", "revoked_at": None,
        "created_at": "2026-08-23T10:00:00+08:00", "committed_by": "user",
    }
    validate_contract("context-grant-v1.schema.json", active)
    invalid = copy.deepcopy(active)
    invalid["status"] = "revoked"
    with pytest.raises(ValueError):
        validate_contract("context-grant-v1.schema.json", invalid)


def test_denied_audit_cannot_claim_accessed_objects() -> None:
    denied = {
        "schema_version": "1.0", "kind": "memento_context_read_audit_revision",
        "audit_id": "aud_444444444444444444444444", "revision": 1,
        "previous_revision_sha256": None, "operation": "read",
        "requested_grant_id": "grt_111111111111111111111111",
        "requested_session_id": "ses_222222222222222222222222",
        "grant_ref": None, "session_ref": None, "client_id": "research-ai",
        "request_sha256": "a" * 64, "status": "denied", "reason_code": "authority_not_found",
        "pack_id": None, "pack_sha256": None, "accessed_refs": [],
        "requested_at": "2026-08-23T10:00:00+08:00",
        "completed_at": "2026-08-23T10:00:01+08:00",
        "created_at": "2026-08-23T10:00:01+08:00", "committed_by": "workflow",
    }
    validate_contract("context-read-audit-v1.schema.json", denied)
    denied["accessed_refs"] = [{
        "kind": "theme", "id": "thm_111111111111111111111111",
        "revision": 1, "revision_sha256": "b" * 64,
    }]
    with pytest.raises(ValueError):
        validate_contract("context-read-audit-v1.schema.json", denied)
