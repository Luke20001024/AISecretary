"""Authorised Context Pack creation with an audit on every terminal path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from memento_backend.agents.context_router import ContextRequest, ContextRouter
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import sha256_json
from memento_backend.policies.context_policy import (
    assert_grant_allows,
    assert_operation_window,
    assert_session_allows,
    parse_timestamp,
)
from memento_backend.projections.common import ProjectionInputs, object_identity
from memento_backend.storage.external_context_store import ExternalContextStore
from memento_backend.storage.revision_store import RevisionStore

from .context_audit import build_context_audit, commit_audit_idempotent


@dataclass(frozen=True)
class ContextPackResult:
    pack: Mapping[str, Any]
    audit_ref: Mapping[str, Any]


class CreateContextPackWorkflow:
    def __init__(self, revisions: RevisionStore, artefacts: ExternalContextStore, router: ContextRouter) -> None:
        self.revisions = revisions
        self.artefacts = artefacts
        self.router = router

    def create(
        self,
        request: ContextRequest,
        *,
        inputs: ProjectionInputs,
        requested_at: str,
        completed_at: str,
    ) -> ContextPackResult:
        request_sha256 = sha256_json(request.canonical_payload())
        grant_ref = None
        session_ref = None
        try:
            request.validate()
            grant = self.revisions.load_head("context_grant", request.grant_id)
            grant_ref = self.revisions.current_ref("context_grant", request.grant_id)
            session = self.revisions.load_head("external_session", request.session_id)
            session_ref = self.revisions.current_ref("external_session", request.session_id)
            if grant_ref is None or session_ref is None:
                raise ContractError("context authority is unavailable", kind="not_found")
            assert_grant_allows(
                grant, client_id=request.client_id, topics=request.topic_scope,
                time_scope=request.time_scope, requested_at=requested_at,
            )
            assert_grant_allows(
                grant, client_id=request.client_id, topics=request.topic_scope,
                time_scope=request.time_scope, requested_at=completed_at,
            )
            assert_session_allows(
                session, grant_ref, client_id=request.client_id,
                task=request.task, topics=request.topic_scope, time_scope=request.time_scope,
            )
            if session["session_id"] != request.session_id:
                raise ContractError("external session identity does not match request", kind="authorization")
            assert_operation_window(
                requested_at=requested_at, completed_at=completed_at,
                not_before=max(
                    (str(grant["created_at"]), str(session["opened_at"])),
                    key=parse_timestamp,
                ),
                expires_at=None if grant["expires_at"] is None else str(grant["expires_at"]),
                authority_name="Context Grant",
            )
            self._assert_current_inputs(inputs)
            pack = self.router.project(
                request, grant=grant, grant_ref=grant_ref, session_ref=session_ref,
                inputs=inputs, generated_at=completed_at,
            )
            self.artefacts.save_pack(pack)
            audit = build_context_audit(
                operation="read", requested_grant_id=request.grant_id,
                requested_session_id=request.session_id, grant_ref=grant_ref, session_ref=session_ref,
                client_id=request.client_id, request_sha256=request_sha256, status="allowed",
                reason_code="context_pack_created", pack_id=str(pack["pack_id"]),
                pack_sha256=sha256_json(pack), accessed_refs=pack["selected_refs"],
                requested_at=requested_at, completed_at=completed_at,
            )
            audit_ref = commit_audit_idempotent(self.revisions, audit)
            return ContextPackResult(pack=pack, audit_ref=audit_ref)
        except ContractError as exc:
            audit = build_context_audit(
                operation="read", requested_grant_id=request.grant_id,
                requested_session_id=request.session_id, grant_ref=grant_ref, session_ref=session_ref,
                client_id=request.client_id, request_sha256=request_sha256, status="denied",
                reason_code=self._reason(exc), pack_id=None, pack_sha256=None, accessed_refs=(),
                requested_at=requested_at, completed_at=completed_at,
            )
            commit_audit_idempotent(self.revisions, audit)
            raise

    def audit_target_denial(
        self,
        request: ContextRequest,
        *,
        reason_code: str,
        requested_at: str,
        completed_at: str,
    ) -> Mapping[str, Any]:
        """Record a tool-specific miss after its bounded pack read completed."""
        audit = build_context_audit(
            operation="read", requested_grant_id=request.grant_id,
            requested_session_id=request.session_id,
            grant_ref=self.revisions.current_ref("context_grant", request.grant_id),
            session_ref=self.revisions.current_ref("external_session", request.session_id),
            client_id=request.client_id, request_sha256=sha256_json(request.canonical_payload()),
            status="denied", reason_code=reason_code, pack_id=None, pack_sha256=None,
            accessed_refs=(), requested_at=requested_at, completed_at=completed_at,
        )
        return commit_audit_idempotent(self.revisions, audit)

    def _assert_current_inputs(self, inputs: ProjectionInputs) -> None:
        for value in inputs.all_objects():
            identity = object_identity(value)
            if identity is None:
                raise ContractError("Context input has no formal identity", kind="evidence")
            ref_kind, object_id = identity
            expected = self._ref(ref_kind, object_id, value)
            if self.revisions.current_ref(ref_kind, object_id) != expected:
                raise ContractError("Context input is stale or unpublished", kind="conflict")

    @staticmethod
    def _ref(kind: str, object_id: str, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"kind": kind, "id": object_id, "revision": value["revision"], "revision_sha256": sha256_json(value)}

    @staticmethod
    def _reason(exc: ContractError) -> str:
        message = str(exc).casefold()
        if exc.kind == "not_found":
            return "authority_not_found"
        if "expired" in message:
            return "grant_expired"
        if "not active" in message:
            return "authority_inactive"
        if "another client" in message or "authority is stale" in message:
            return "client_or_session_mismatch"
        if "task does not match" in message:
            return "task_mismatch"
        if "topics exceed" in message:
            return "topic_out_of_scope"
        if "time scope exceeds" in message:
            return "time_scope_exceeded"
        if exc.kind in {"conflict", "evidence"}:
            return "input_authority_invalid"
        return "request_denied"
