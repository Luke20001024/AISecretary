"""Append an authorised external outcome as a new L0 source and trace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_bytes, sha256_json, validate_datetime, validate_id
from memento_backend.domain.refs import ObjectRef
from memento_backend.policies.context_policy import (
    assert_grant_allows,
    assert_operation_window,
    assert_session_allows,
)
from memento_backend.storage.external_context_store import ExternalContextStore
from memento_backend.storage.revision_store import RevisionStore

from .context_audit import build_context_audit, commit_audit_idempotent


@dataclass(frozen=True)
class ExternalTraceInput:
    grant_id: str
    session_id: str
    pack_id: str
    client_id: str
    trace_type: str
    content: str
    context_refs: tuple[Mapping[str, Any], ...]
    user_confirmed: bool = False

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id, "session_id": self.session_id, "pack_id": self.pack_id,
            "client_id": self.client_id, "trace_type": self.trace_type, "content": self.content,
            "context_refs": [dict(value) for value in self.context_refs],
            "user_confirmed": self.user_confirmed,
        }


@dataclass(frozen=True)
class ExternalTraceResult:
    trace: Mapping[str, Any]
    audit_ref: Mapping[str, Any]


class AppendExternalTraceWorkflow:
    def __init__(self, revisions: RevisionStore, artefacts: ExternalContextStore) -> None:
        self.revisions = revisions
        self.artefacts = artefacts

    def append(self, value: ExternalTraceInput, *, requested_at: str, completed_at: str) -> ExternalTraceResult:
        validate_id("context_grant", value.grant_id, "grant_id")
        validate_id("external_session", value.session_id, "session_id")
        validate_id("context_pack", value.pack_id, "pack_id")
        validate_datetime(requested_at, "requested_at")
        validate_datetime(completed_at, "completed_at")
        request_sha256 = sha256_json(value.canonical_payload())
        grant_ref = None
        session_ref = None
        try:
            self._validate_input(value)
            grant = self.revisions.load_head("context_grant", value.grant_id)
            grant_ref = self.revisions.current_ref("context_grant", value.grant_id)
            session = self.revisions.load_head("external_session", value.session_id)
            session_ref = self.revisions.current_ref("external_session", value.session_id)
            if grant_ref is None or session_ref is None:
                raise ContractError("context authority is unavailable", kind="not_found")
            pack = self.artefacts.load_pack(value.pack_id)
            assert_grant_allows(
                grant, client_id=value.client_id, topics=pack["topic_scope"],
                time_scope=pack["time_scope"], requested_at=requested_at,
            )
            assert_grant_allows(
                grant, client_id=value.client_id, topics=pack["topic_scope"],
                time_scope=pack["time_scope"], requested_at=completed_at,
            )
            assert_session_allows(
                session, grant_ref, client_id=value.client_id,
                task=str(pack["task"]), topics=pack["topic_scope"], time_scope=pack["time_scope"],
            )
            if pack["grant_ref"] != grant_ref or pack["session_ref"] != session_ref:
                raise ContractError("Context Pack authority is stale", kind="authorization")
            if session["task"] != pack["task"]:
                raise ContractError("Context Pack task does not match the session", kind="authorization")
            assert_operation_window(
                requested_at=requested_at, completed_at=completed_at,
                not_before=str(pack["generated_at"]), expires_at=str(pack["expires_at"]),
                authority_name="Context Pack",
            )
            self._assert_pack_read_audited(pack)
            if value.trace_type not in grant["allowed_writeback"] or value.trace_type not in pack["allowed_writeback"]:
                raise ContractError("writeback type is outside the grant", kind="authorization")
            if value.trace_type == "correction" and not value.user_confirmed:
                raise ContractError("correction requires explicit user confirmation", kind="authorization")
            selected = {self._ref_key(ObjectRef.from_dict(ref).to_dict()) for ref in pack["selected_refs"]}
            supplied_refs = [ObjectRef.from_dict(ref).to_dict() for ref in value.context_refs]
            if any(self._ref_key(ref) not in selected for ref in supplied_refs):
                raise ContractError("writeback references Context outside the pack", kind="authorization")

            trace_id = make_id("external_trace", "external-trace-v1", {
                "session_ref": dict(session_ref), "pack_id": value.pack_id,
                "trace_type": value.trace_type, "content": value.content,
                "captured_at": requested_at,
            })
            source = self._source_record(value, trace_id, captured_at=requested_at)
            source_ref = self._ref("source_record", "record_id", source)
            trace = {
                "schema_version": "1.0", "kind": "memento_external_trace_revision", "trace_id": trace_id,
                "revision": 1, "previous_revision_sha256": None, "status": "active", "operation": "append",
                "session_ref": dict(session_ref), "grant_ref": dict(grant_ref), "pack_id": value.pack_id,
                "client_id": value.client_id, "task": session["task"], "trace_type": value.trace_type,
                "content": value.content, "context_refs": supplied_refs,
                "source_record_ref": source_ref, "captured_at": requested_at,
                "user_confirmed": value.user_confirmed, "processing_status": "raw_saved",
                "created_at": completed_at, "committed_by": "workflow",
            }
            validate_contract("external-trace-v1.schema.json", trace)
            trace_ref = self._ref("external_trace", "trace_id", trace)
            audit = build_context_audit(
                operation="writeback", requested_grant_id=value.grant_id,
                requested_session_id=value.session_id, grant_ref=grant_ref, session_ref=session_ref,
                client_id=value.client_id, request_sha256=request_sha256, status="allowed",
                reason_code="external_trace_saved", pack_id=value.pack_id,
                pack_sha256=sha256_json(pack),
                accessed_refs=[*supplied_refs, source_ref, trace_ref],
                requested_at=requested_at, completed_at=completed_at,
            )
            audit_ref = self._ref("context_read_audit", "audit_id", audit)

            existing = self.revisions.current_ref("external_trace", trace_id)
            if existing is not None:
                if self.revisions.load_head("external_trace", trace_id) != trace:
                    raise ContractError("external trace identifier collision", kind="conflict")
                self._assert_existing("source_record", str(source["record_id"]), source)
                committed_audit = commit_audit_idempotent(self.revisions, audit)
                return ExternalTraceResult(trace, committed_audit)

            content = (value.content.rstrip() + "\n").encode("utf-8")
            self.artefacts.save_trace_source(value.session_id, trace_id, content)
            committed = self.revisions.commit_many([source, trace, audit], committed_at=completed_at)
            return ExternalTraceResult(trace, committed[2])
        except ContractError as exc:
            audit = build_context_audit(
                operation="writeback", requested_grant_id=value.grant_id,
                requested_session_id=value.session_id, grant_ref=grant_ref, session_ref=session_ref,
                client_id=value.client_id, request_sha256=request_sha256, status="denied",
                reason_code=self._reason(exc), pack_id=None, pack_sha256=None, accessed_refs=(),
                requested_at=requested_at, completed_at=completed_at,
            )
            commit_audit_idempotent(self.revisions, audit)
            raise

    def _source_record(self, value: ExternalTraceInput, trace_id: str, *, captured_at: str) -> Mapping[str, Any]:
        content = (value.content.rstrip() + "\n").encode("utf-8")
        source_file = f"external-sources/{value.session_id}-{trace_id}.md"
        record_id = make_id("source_record", "external-context-source-v1", {
            "session_id": value.session_id, "trace_id": trace_id, "sha256": sha256_bytes(content),
        })
        return {
            "schema_version": "2.0", "kind": "memento_source_record_revision", "record_id": record_id,
            "revision": 1, "previous_revision_sha256": None, "status": "active", "operation": "ingest",
            "created_at": captured_at, "captured_at": captured_at,
            "local_date": captured_at[:10], "source_type": "external_trace",
            "source_app": f"External Context · {value.client_id}", "source_file": source_file,
            "line_start": 1, "line_end": max(1, len(value.content.rstrip().splitlines())),
            "entry_sha256": sha256_bytes(content), "source_snapshot_sha256": sha256_bytes(content),
            "attachments": [], "ingest_origin": "external_context", "committed_by": "workflow",
        }

    @staticmethod
    def _validate_input(value: ExternalTraceInput) -> None:
        if not isinstance(value.client_id, str) or not value.client_id.strip() or len(value.client_id) > 240:
            raise ContractError("client_id is empty or too long", kind="authorization")
        if not isinstance(value.content, str) or not value.content.strip() or len(value.content) > 20000:
            raise ContractError("external trace content is empty or too long", kind="size")
        if type(value.user_confirmed) is not bool:
            raise ContractError("user_confirmed must be boolean", kind="authorization")
        if value.trace_type not in {"decision", "correction", "outcome", "new_question"}:
            raise ContractError("external trace type is unsupported", kind="authorization")

    def _assert_existing(self, kind: str, object_id: str, value: Mapping[str, Any]) -> None:
        current = self.revisions.current_ref(kind, object_id)
        if current is None or self.revisions.load_head(kind, object_id) != value:
            raise ContractError("idempotent external object is inconsistent", kind="conflict")

    def _assert_pack_read_audited(self, pack: Mapping[str, Any]) -> None:
        pack_sha256 = sha256_json(pack)
        for audit in self.revisions.list_heads("context_read_audit"):
            if (
                audit["operation"] == "read"
                and audit["status"] == "allowed"
                and audit["pack_id"] == pack["pack_id"]
                and audit["pack_sha256"] == pack_sha256
                and audit["grant_ref"] == pack["grant_ref"]
                and audit["session_ref"] == pack["session_ref"]
            ):
                return
        raise ContractError("Context Pack has no published allowed read audit", kind="authorization")

    @staticmethod
    def _ref(kind: str, id_field: str, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"kind": kind, "id": value[id_field], "revision": value["revision"], "revision_sha256": sha256_json(value)}

    @staticmethod
    def _ref_key(value: Mapping[str, Any]) -> tuple[str, str, int, str]:
        return (str(value["kind"]), str(value["id"]), int(value["revision"]), str(value["revision_sha256"]))

    @staticmethod
    def _reason(exc: ContractError) -> str:
        message = str(exc).casefold()
        if exc.kind == "not_found":
            return "authority_or_pack_not_found"
        if "expired" in message:
            return "authority_or_pack_expired"
        if "correction" in message:
            return "user_confirmation_required"
        if "outside" in message or "exceed" in message:
            return "writeback_scope_exceeded"
        if "stale" in message or "does not match" in message:
            return "authority_or_task_mismatch"
        return "writeback_denied"
