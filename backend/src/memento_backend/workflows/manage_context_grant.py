"""User-authorised Context grant lifecycle."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_json, validate_datetime
from memento_backend.policies.context_policy import assert_time_scope, normalize_topics, parse_timestamp
from memento_backend.storage.revision_store import RevisionStore


class ContextGrantWorkflow:
    def __init__(self, revisions: RevisionStore) -> None:
        self.revisions = revisions

    def grant(
        self,
        *,
        client_id: str,
        allowed_kinds: Sequence[str],
        topic_scope: Sequence[str],
        time_scope: Optional[Mapping[str, str]],
        max_sensitivity: str,
        allow_source_quotes: bool,
        allowed_writeback: Sequence[str],
        expires_at: Optional[str],
        created_at: str,
    ) -> Mapping[str, Any]:
        validate_datetime(created_at, "created_at")
        if expires_at is not None:
            validate_datetime(expires_at, "expires_at")
            if parse_timestamp(expires_at) <= parse_timestamp(created_at):
                raise ContractError("context grant expiry must be in the future", kind="authorization")
        assert_time_scope(time_scope, name="grant time scope")
        if not client_id.strip() or len(client_id) > 240:
            raise ContractError("client_id is empty or too long", kind="authorization")
        topics = list(dict.fromkeys(value.strip() for value in topic_scope if value.strip()))
        normalize_topics(topics)
        kinds = sorted(set(str(value) for value in allowed_kinds))
        writeback = sorted(set(str(value) for value in allowed_writeback))
        identity = {
            "client_id": client_id, "allowed_kinds": kinds, "topic_scope": topics,
            "time_scope": None if time_scope is None else dict(time_scope),
            "max_sensitivity": max_sensitivity, "allow_source_quotes": allow_source_quotes,
            "allowed_writeback": writeback, "expires_at": expires_at, "created_at": created_at,
        }
        grant_id = make_id("context_grant", "context-grant-v1", identity)
        value = {
            "schema_version": "1.0", "kind": "memento_context_grant_revision", "grant_id": grant_id,
            "revision": 1, "previous_revision_sha256": None, "status": "active", "operation": "grant",
            **identity, "revoked_at": None, "committed_by": "user",
        }
        validate_contract("context-grant-v1.schema.json", value)
        current = self.revisions.current_ref("context_grant", grant_id)
        if current is not None:
            if self.revisions.load_head("context_grant", grant_id) != value:
                raise ContractError("context grant identifier collision", kind="conflict")
            return current
        return self.revisions.commit(value, committed_at=created_at)

    def revoke(self, grant_id: str, *, revoked_at: str) -> Mapping[str, Any]:
        validate_datetime(revoked_at, "revoked_at")
        current_ref = self.revisions.current_ref("context_grant", grant_id)
        if current_ref is None:
            raise ContractError("context grant does not exist", kind="not_found")
        current = self.revisions.load_head("context_grant", grant_id)
        if current["status"] == "revoked":
            return current_ref
        if parse_timestamp(revoked_at) < parse_timestamp(str(current["created_at"])):
            raise ContractError("context grant revocation predates the current revision", kind="authorization")
        value = {
            **dict(current), "revision": int(current["revision"]) + 1,
            "previous_revision_sha256": sha256_json(current), "status": "revoked", "operation": "revoke",
            "revoked_at": revoked_at, "created_at": revoked_at,
        }
        validate_contract("context-grant-v1.schema.json", value)
        return self.revisions.commit(value, expected_ref=current_ref, committed_at=revoked_at)
