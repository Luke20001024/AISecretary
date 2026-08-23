"""Open an external Context session bound to one current grant revision."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, validate_datetime
from memento_backend.policies.context_policy import assert_grant_allows, normalize_topics
from memento_backend.storage.revision_store import RevisionStore


class ExternalSessionWorkflow:
    def __init__(self, revisions: RevisionStore) -> None:
        self.revisions = revisions

    def open(
        self,
        *,
        grant_id: str,
        client_id: str,
        task: str,
        topic_scope: Sequence[str],
        time_scope: Optional[Mapping[str, str]],
        opened_at: str,
    ) -> Mapping[str, Any]:
        validate_datetime(opened_at, "opened_at")
        topics = list(dict.fromkeys(value.strip() for value in topic_scope if value.strip()))
        normalize_topics(topics)
        if not task.strip() or len(task) > 1000:
            raise ContractError("external session task is empty or too long", kind="authorization")
        grant = self.revisions.load_head("context_grant", grant_id)
        grant_ref = self.revisions.current_ref("context_grant", grant_id)
        if grant_ref is None:
            raise ContractError("context grant does not exist", kind="not_found")
        assert_grant_allows(grant, client_id=client_id, topics=topics, time_scope=time_scope, requested_at=opened_at)
        identity = {
            "grant_ref": dict(grant_ref), "client_id": client_id, "task": task,
            "topic_scope": topics, "time_scope": None if time_scope is None else dict(time_scope),
            "opened_at": opened_at,
        }
        session_id = make_id("external_session", "external-session-v1", identity)
        value = {
            "schema_version": "1.0", "kind": "memento_external_session_revision", "session_id": session_id,
            "revision": 1, "previous_revision_sha256": None, "status": "active", "operation": "open",
            **identity, "closed_at": None, "created_at": opened_at, "committed_by": "workflow",
        }
        validate_contract("external-session-v1.schema.json", value)
        current = self.revisions.current_ref("external_session", session_id)
        if current is not None:
            if self.revisions.load_head("external_session", session_id) != value:
                raise ContractError("external session identifier collision", kind="conflict")
            return current
        return self.revisions.commit(value, committed_at=opened_at)
