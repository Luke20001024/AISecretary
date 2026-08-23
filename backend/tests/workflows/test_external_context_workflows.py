from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, Tuple

import pytest

from memento_backend.agents.context_router import ContextRequest, ContextRouter
from memento_backend.domain.errors import ContractError
from memento_backend.interfaces.context_tools import TOOL_MANIFEST, ContextToolFacade
from memento_backend.interfaces.mcp_server import READ_TOOLS, WRITE_TOOLS, LocalMcpContextServer
from memento_backend.projections.common import ProjectionInputs
from memento_backend.storage.atomic import AtomicFileStore
from memento_backend.storage.external_context_store import ExternalContextStore
from memento_backend.storage.revision_store import RevisionStore
from memento_backend.workflows.append_external_trace import AppendExternalTraceWorkflow, ExternalTraceInput
from memento_backend.workflows.create_context_pack import CreateContextPackWorkflow
from memento_backend.workflows.manage_context_grant import ContextGrantWorkflow
from memento_backend.workflows.open_external_session import ExternalSessionWorkflow
from tests.fixtures.formal_20d import formal_20d_inputs


def environment(tmp_path: Path) -> Tuple[RevisionStore, ExternalContextStore, ContextGrantWorkflow, ExternalSessionWorkflow, CreateContextPackWorkflow, AppendExternalTraceWorkflow]:
    root = tmp_path / "isolated-v2"
    root.mkdir(mode=0o700)
    files = AtomicFileStore(root)
    revisions = RevisionStore(files)
    artefacts = ExternalContextStore(files)
    return (
        revisions, artefacts, ContextGrantWorkflow(revisions), ExternalSessionWorkflow(revisions),
        CreateContextPackWorkflow(revisions, artefacts, ContextRouter()),
        AppendExternalTraceWorkflow(revisions, artefacts),
    )


def confirmed_inputs() -> ProjectionInputs:
    fixture = formal_20d_inputs()
    confirmed = copy.deepcopy(dict(fixture.self_insights[0]))
    confirmed["confirmation"] = "user_confirmed"
    confirmed["committed_by"] = "user"
    confirmed["committing_action_id"] = "uact_111111111111111111111111"
    observed = copy.deepcopy(dict(fixture.self_insights[0]))
    observed["insight_id"] = "sin_222222222222222222222222"
    observed["title"] = "仍未由用户确认的理解"
    observed["confirmation"] = "observed"
    observed["visibility"] = "local_only"
    return ProjectionInputs(
        source_records=fixture.source_records, interpretations=fixture.interpretations,
        memory_atoms=fixture.memory_atoms, relations=fixture.relations, themes=fixture.themes,
        self_insights=(confirmed, observed), resource_cards=fixture.resource_cards,
        read_later_intents=fixture.read_later_intents,
    )


def authority(
    revisions: RevisionStore,
    grants: ContextGrantWorkflow,
    sessions: ExternalSessionWorkflow,
    *,
    allowed_kinds: tuple[str, ...] = ("self_insight", "theme", "memory_atom", "source_record"),
    allowed_writeback: tuple[str, ...] = ("decision", "correction", "outcome", "new_question"),
    expires_at: str = "2026-08-24T10:00:00+08:00",
) -> tuple[str, str, ProjectionInputs]:
    inputs = confirmed_inputs()
    revisions.commit_many(inputs.all_objects(), committed_at="2026-08-23T09:50:00+08:00")
    grant_ref = grants.grant(
        client_id="research-ai", allowed_kinds=allowed_kinds, topic_scope=("产品方法",),
        time_scope=None, max_sensitivity="normal", allow_source_quotes=True,
        allowed_writeback=allowed_writeback, expires_at=expires_at,
        created_at="2026-08-23T09:59:00+08:00",
    )
    session_ref = sessions.open(
        grant_id=str(grant_ref["id"]), client_id="research-ai", task="继续 Memento 后端设计",
        topic_scope=("产品方法",), time_scope=None, opened_at="2026-08-23T10:00:00+08:00",
    )
    return str(grant_ref["id"]), str(session_ref["id"]), inputs


def request(grant_id: str, session_id: str, *, topics: tuple[str, ...] = ("产品方法",)) -> ContextRequest:
    return ContextRequest(
        grant_id=grant_id, session_id=session_id, client_id="research-ai",
        task="继续 Memento 后端设计", topic_scope=topics, include_source_quotes=True,
    )


def latest_audit(revisions: RevisionStore) -> Mapping[str, Any]:
    return max(revisions.list_heads("context_read_audit"), key=lambda value: str(value["completed_at"]))


def test_context_pack_is_minimal_confirmed_and_audited(tmp_path: Path) -> None:
    revisions, artefacts, grants, sessions, reads, _ = environment(tmp_path)
    grant_id, session_id, inputs = authority(revisions, grants, sessions)
    result = reads.create(
        request(grant_id, session_id), inputs=inputs,
        requested_at="2026-08-23T10:02:00+08:00", completed_at="2026-08-23T10:02:01+08:00",
    )
    assert len(result.pack["self_insights"]) == 1
    assert result.pack["self_insights"][0]["title"] != "仍未由用户确认的理解"
    assert result.pack["themes"] and result.pack["memories"] and result.pack["source_quotes"]
    assert artefacts.load_pack(str(result.pack["pack_id"])) == result.pack
    audit = revisions.load_head("context_read_audit", str(result.audit_ref["id"]))
    assert audit["status"] == "allowed"
    assert audit["accessed_refs"] == result.pack["selected_refs"]
    repeated = reads.create(
        request(grant_id, session_id), inputs=inputs,
        requested_at="2026-08-23T10:02:00+08:00", completed_at="2026-08-23T10:02:01+08:00",
    )
    assert repeated.pack == result.pack


def test_context_pack_does_not_leak_ungranted_kinds(tmp_path: Path) -> None:
    revisions, _, grants, sessions, reads, _ = environment(tmp_path)
    grant_id, session_id, inputs = authority(
        revisions, grants, sessions, allowed_kinds=("self_insight",), allowed_writeback=(),
    )
    pack = reads.create(
        request(grant_id, session_id), inputs=inputs,
        requested_at="2026-08-23T10:01:00+08:00", completed_at="2026-08-23T10:01:01+08:00",
    ).pack
    assert pack["self_insights"]
    assert not pack["themes"] and not pack["memories"] and not pack["source_quotes"]
    assert pack["self_insights"][0]["theme_refs"] == []
    assert pack["self_insights"][0]["boundary_refs"] == []
    assert {ref["kind"] for ref in pack["selected_refs"]} == {"self_insight"}


def test_stale_projection_input_is_rejected_and_audited(tmp_path: Path) -> None:
    revisions, _, grants, sessions, reads, _ = environment(tmp_path)
    grant_id, session_id, inputs = authority(revisions, grants, sessions)
    injected = copy.deepcopy(dict(inputs.memory_atoms[0]))
    injected["statement"] = "调用方伪造的理解"
    tainted = ProjectionInputs(
        source_records=inputs.source_records, interpretations=inputs.interpretations,
        memory_atoms=(injected, *inputs.memory_atoms[1:]), relations=inputs.relations,
        themes=inputs.themes, self_insights=inputs.self_insights,
        resource_cards=inputs.resource_cards, read_later_intents=inputs.read_later_intents,
    )
    with pytest.raises(ContractError):
        reads.create(
            request(grant_id, session_id), inputs=tainted,
            requested_at="2026-08-23T10:02:00+08:00", completed_at="2026-08-23T10:02:01+08:00",
        )
    denied = latest_audit(revisions)
    assert denied["reason_code"] == "input_authority_invalid"


def test_missing_expired_revoked_and_out_of_scope_reads_are_audited(tmp_path: Path) -> None:
    revisions, _, grants, sessions, reads, _ = environment(tmp_path)
    with pytest.raises(ContractError):
        reads.create(
            request("grt_111111111111111111111111", "ses_222222222222222222222222"),
            inputs=ProjectionInputs(), requested_at="2026-08-23T10:00:00+08:00",
            completed_at="2026-08-23T10:00:01+08:00",
        )
    assert latest_audit(revisions)["reason_code"] == "authority_not_found"

    grant_id, session_id, inputs = authority(
        revisions, grants, sessions, expires_at="2026-08-23T10:01:00+08:00",
    )
    with pytest.raises(ContractError):
        reads.create(
            request(grant_id, session_id), inputs=inputs,
            requested_at="2026-08-23T10:01:00+08:00", completed_at="2026-08-23T10:01:01+08:00",
        )
    assert latest_audit(revisions)["reason_code"] == "grant_expired"

    scoped_parent = tmp_path / "scoped"
    scoped_parent.mkdir()
    revisions2, _, grants2, sessions2, reads2, _ = environment(scoped_parent)
    grant_id2, session_id2, inputs2 = authority(revisions2, grants2, sessions2)
    with pytest.raises(ContractError):
        reads2.create(
            request(grant_id2, session_id2, topics=("研究",)), inputs=inputs2,
            requested_at="2026-08-23T11:02:00+08:00", completed_at="2026-08-23T11:02:01+08:00",
        )
    assert latest_audit(revisions2)["reason_code"] == "topic_out_of_scope"


def test_external_trace_returns_atomically_to_l0(tmp_path: Path) -> None:
    revisions, artefacts, grants, sessions, reads, writes = environment(tmp_path)
    grant_id, session_id, inputs = authority(revisions, grants, sessions)
    theme_heads_before = revisions.list_heads("theme")
    self_heads_before = revisions.list_heads("self_insight")
    pack = reads.create(
        request(grant_id, session_id), inputs=inputs,
        requested_at="2026-08-23T10:02:00+08:00", completed_at="2026-08-23T10:02:01+08:00",
    ).pack
    trace_input = ExternalTraceInput(
        grant_id=grant_id, session_id=session_id, pack_id=str(pack["pack_id"]),
        client_id="research-ai", trace_type="outcome",
        content="这次先确认失败条件，再推进接口设计，减少了返工",
        context_refs=tuple(pack["selected_refs"]),
    )
    result = writes.append(
        trace_input, requested_at="2026-08-23T10:03:00+08:00",
        completed_at="2026-08-23T10:03:01+08:00",
    )
    source = revisions.load_head("source_record", str(result.trace["source_record_ref"]["id"]))
    assert source["source_type"] == "external_trace" and source["ingest_origin"] == "external_context"
    assert source["source_app"] == "External Context · research-ai"
    assert artefacts.files.read_bytes(str(source["source_file"]))
    assert revisions.list_heads("theme") == theme_heads_before
    assert revisions.list_heads("self_insight") == self_heads_before
    audit = revisions.load_head("context_read_audit", str(result.audit_ref["id"]))
    assert audit["operation"] == "writeback" and audit["status"] == "allowed"
    repeated = writes.append(
        trace_input, requested_at="2026-08-23T10:03:00+08:00",
        completed_at="2026-08-23T10:03:01+08:00",
    )
    assert repeated.trace == result.trace


def test_unauthorized_writeback_and_unconfirmed_correction_are_denied(tmp_path: Path) -> None:
    revisions, _, grants, sessions, reads, writes = environment(tmp_path)
    grant_id, session_id, inputs = authority(
        revisions, grants, sessions, allowed_writeback=("outcome",),
    )
    pack = reads.create(
        request(grant_id, session_id), inputs=inputs,
        requested_at="2026-08-23T10:02:00+08:00", completed_at="2026-08-23T10:02:01+08:00",
    ).pack
    source_count = len(revisions.list_heads("source_record"))
    with pytest.raises(ContractError):
        writes.append(
            ExternalTraceInput(
                grant_id=grant_id, session_id=session_id, pack_id=str(pack["pack_id"]),
                client_id="research-ai", trace_type="session_note", content="越权写回", context_refs=(),
            ), requested_at="2026-08-23T10:03:00+08:00", completed_at="2026-08-23T10:03:01+08:00",
        )
    assert revisions.list_heads("external_trace") == []
    assert len(revisions.list_heads("source_record")) == source_count
    assert latest_audit(revisions)["status"] == "denied"

    correction_parent = tmp_path / "correction"
    correction_parent.mkdir()
    revisions2, _, grants2, sessions2, reads2, writes2 = environment(correction_parent)
    grant_id2, session_id2, inputs2 = authority(revisions2, grants2, sessions2)
    pack2 = reads2.create(
        request(grant_id2, session_id2), inputs=inputs2,
        requested_at="2026-08-23T10:02:00+08:00", completed_at="2026-08-23T10:02:01+08:00",
    ).pack
    with pytest.raises(ContractError):
        writes2.append(
            ExternalTraceInput(
                grant_id=grant_id2, session_id=session_id2, pack_id=str(pack2["pack_id"]),
                client_id="research-ai", trace_type="correction", content="这条理解需要收窄",
                context_refs=(), user_confirmed=False,
            ), requested_at="2026-08-23T10:03:00+08:00", completed_at="2026-08-23T10:03:01+08:00",
        )
    assert latest_audit(revisions2)["reason_code"] == "user_confirmation_required"


def test_revocation_stops_future_reads_and_mcp_surface_has_no_direct_mutation(tmp_path: Path) -> None:
    revisions, _, grants, sessions, reads, writes = environment(tmp_path)
    grant_id, session_id, inputs = authority(revisions, grants, sessions)
    grants.revoke(grant_id, revoked_at="2026-08-23T10:02:00+08:00")
    with pytest.raises(ContractError):
        reads.create(
            request(grant_id, session_id), inputs=inputs,
            requested_at="2026-08-23T10:03:00+08:00", completed_at="2026-08-23T10:03:01+08:00",
        )
    assert latest_audit(revisions)["reason_code"] == "authority_inactive"

    names = {str(item["name"]) for item in TOOL_MANIFEST}
    assert names == set(READ_TOOLS) | set(WRITE_TOOLS)
    assert all(not item["mutates_cognitive_objects"] for item in TOOL_MANIFEST)
    assert not names.intersection({"memento.update_theme", "memento.update_self_insight"})
    server = LocalMcpContextServer(ContextToolFacade(reads, writes))
    with pytest.raises(ContractError):
        server.call_tool(
            "memento.update_theme", {}, requested_at="2026-08-23T10:00:00+08:00",
            completed_at="2026-08-23T10:00:01+08:00", inputs=inputs,
        )


def test_read_is_bound_to_exact_session_task_and_audited(tmp_path: Path) -> None:
    revisions, _, grants, sessions, reads, _ = environment(tmp_path)
    grant_id, session_id, inputs = authority(revisions, grants, sessions)
    mismatched = ContextRequest(
        grant_id=grant_id, session_id=session_id, client_id="research-ai",
        task="把同一授权挪给另一个任务", topic_scope=("产品方法",),
    )
    with pytest.raises(ContractError, match="task"):
        reads.create(
            mismatched, inputs=inputs,
            requested_at="2026-08-23T10:02:00+08:00",
            completed_at="2026-08-23T10:02:01+08:00",
        )
    assert latest_audit(revisions)["reason_code"] == "task_mismatch"


def test_writeback_rechecks_grant_and_pack_at_completion(tmp_path: Path) -> None:
    revisions, _, grants, sessions, reads, writes = environment(tmp_path)
    grant_id, session_id, inputs = authority(
        revisions, grants, sessions, expires_at="2026-08-23T10:05:00+08:00",
    )
    pack = reads.create(
        request(grant_id, session_id), inputs=inputs,
        requested_at="2026-08-23T10:02:00+08:00",
        completed_at="2026-08-23T10:02:01+08:00",
    ).pack
    with pytest.raises(ContractError, match="expired"):
        writes.append(
            ExternalTraceInput(
                grant_id=grant_id, session_id=session_id, pack_id=str(pack["pack_id"]),
                client_id="research-ai", trace_type="outcome", content="跨过到期点的结果",
                context_refs=(),
            ),
            requested_at="2026-08-23T10:04:59+08:00",
            completed_at="2026-08-23T10:05:00+08:00",
        )
    assert revisions.list_heads("external_trace") == []
    assert latest_audit(revisions)["reason_code"] == "authority_or_pack_expired"


def test_mcp_string_false_cannot_confirm_correction(tmp_path: Path) -> None:
    revisions, _, grants, sessions, reads, writes = environment(tmp_path)
    grant_id, session_id, inputs = authority(revisions, grants, sessions)
    pack = reads.create(
        request(grant_id, session_id), inputs=inputs,
        requested_at="2026-08-23T10:02:00+08:00",
        completed_at="2026-08-23T10:02:01+08:00",
    ).pack
    server = LocalMcpContextServer(ContextToolFacade(reads, writes))
    with pytest.raises(ContractError, match="boolean"):
        server.call_tool(
            "memento.correct_context",
            {
                "grant_id": grant_id, "session_id": session_id, "pack_id": pack["pack_id"],
                "client_id": "research-ai", "content": "这条理解需要收窄",
                "context_refs": [], "user_confirmed": "false",
            },
            requested_at="2026-08-23T10:03:00+08:00",
            completed_at="2026-08-23T10:03:01+08:00",
        )
    assert revisions.list_heads("external_trace") == []
    assert latest_audit(revisions)["status"] == "denied"


def test_pack_cannot_cross_sessions_or_bypass_its_read_audit(tmp_path: Path) -> None:
    revisions, artefacts, grants, sessions, reads, writes = environment(tmp_path)
    grant_id, session_id, inputs = authority(revisions, grants, sessions)
    pack = reads.create(
        request(grant_id, session_id), inputs=inputs,
        requested_at="2026-08-23T10:02:00+08:00",
        completed_at="2026-08-23T10:02:01+08:00",
    ).pack
    other_session = sessions.open(
        grant_id=grant_id, client_id="research-ai", task="继续 Memento 后端设计",
        topic_scope=("产品方法",), time_scope=None, opened_at="2026-08-23T10:02:10+08:00",
    )
    with pytest.raises(ContractError, match="stale"):
        writes.append(
            ExternalTraceInput(
                grant_id=grant_id, session_id=str(other_session["id"]), pack_id=str(pack["pack_id"]),
                client_id="research-ai", trace_type="outcome", content="跨会话复用",
                context_refs=(),
            ),
            requested_at="2026-08-23T10:03:00+08:00",
            completed_at="2026-08-23T10:03:01+08:00",
        )

    grant_ref = revisions.current_ref("context_grant", grant_id)
    session_ref = revisions.current_ref("external_session", session_id)
    assert grant_ref is not None and session_ref is not None
    unaudited = ContextRouter().project(
        request(grant_id, session_id),
        grant=revisions.load_head("context_grant", grant_id),
        grant_ref=grant_ref, session_ref=session_ref,
        inputs=inputs, generated_at="2026-08-23T10:04:00+08:00",
    )
    artefacts.save_pack(unaudited)
    with pytest.raises(ContractError, match="allowed read audit"):
        writes.append(
            ExternalTraceInput(
                grant_id=grant_id, session_id=session_id, pack_id=str(unaudited["pack_id"]),
                client_id="research-ai", trace_type="outcome", content="绕过读取审计",
                context_refs=(),
            ),
            requested_at="2026-08-23T10:04:30+08:00",
            completed_at="2026-08-23T10:04:31+08:00",
        )
    assert revisions.list_heads("external_trace") == []


def test_malformed_context_ref_and_missing_target_are_denied_and_audited(tmp_path: Path) -> None:
    revisions, _, grants, sessions, reads, writes = environment(tmp_path)
    grant_id, session_id, inputs = authority(revisions, grants, sessions)
    pack = reads.create(
        request(grant_id, session_id), inputs=inputs,
        requested_at="2026-08-23T10:02:00+08:00",
        completed_at="2026-08-23T10:02:01+08:00",
    ).pack
    confused_ref = dict(pack["selected_refs"][0])
    confused_ref["kind"] = "theme"
    with pytest.raises(ContractError):
        writes.append(
            ExternalTraceInput(
                grant_id=grant_id, session_id=session_id, pack_id=str(pack["pack_id"]),
                client_id="research-ai", trace_type="outcome", content="混淆引用类型",
                context_refs=(confused_ref,),
            ),
            requested_at="2026-08-23T10:03:00+08:00",
            completed_at="2026-08-23T10:03:01+08:00",
        )
    assert latest_audit(revisions)["status"] == "denied"

    server = LocalMcpContextServer(ContextToolFacade(reads, writes))
    with pytest.raises(ContractError, match="outside"):
        server.call_tool(
            "memento.get_theme",
            {
                "grant_id": grant_id, "session_id": session_id, "client_id": "research-ai",
                "task": "继续 Memento 后端设计", "topic_scope": ["产品方法"],
                "theme_id": "thm_ffffffffffffffffffffffff",
            },
            inputs=inputs, requested_at="2026-08-23T10:04:00+08:00",
            completed_at="2026-08-23T10:04:01+08:00",
        )
    assert any(
        audit["reason_code"] == "target_outside_context_pack"
        for audit in revisions.list_heads("context_read_audit")
    )


def test_external_writeback_is_atomically_visible_and_retryable(tmp_path: Path) -> None:
    revisions, artefacts, grants, sessions, reads, writes = environment(tmp_path)
    grant_id, session_id, inputs = authority(revisions, grants, sessions)
    pack = reads.create(
        request(grant_id, session_id), inputs=inputs,
        requested_at="2026-08-23T10:02:00+08:00",
        completed_at="2026-08-23T10:02:01+08:00",
    ).pack
    trace_input = ExternalTraceInput(
        grant_id=grant_id, session_id=session_id, pack_id=str(pack["pack_id"]),
        client_id="research-ai", trace_type="outcome", content="原子提交后再进入 L0",
        context_refs=(),
    )
    source_count = len(revisions.list_heads("source_record"))
    audit_count = len(revisions.list_heads("context_read_audit"))

    def fail_after_revisions(stage: str, _: str) -> None:
        if stage == "after_revisions":
            raise RuntimeError(stage)

    interrupted_store = RevisionStore(revisions.files, fault_hook=fail_after_revisions)
    interrupted = AppendExternalTraceWorkflow(interrupted_store, artefacts)
    with pytest.raises(RuntimeError, match="after_revisions"):
        interrupted.append(
            trace_input, requested_at="2026-08-23T10:03:00+08:00",
            completed_at="2026-08-23T10:03:01+08:00",
        )
    assert len(revisions.list_heads("source_record")) == source_count
    assert revisions.list_heads("external_trace") == []
    assert len(revisions.list_heads("context_read_audit")) == audit_count

    recovered = writes.append(
        trace_input, requested_at="2026-08-23T10:03:00+08:00",
        completed_at="2026-08-23T10:03:01+08:00",
    )
    assert revisions.load_head("external_trace", str(recovered.trace["trace_id"])) == recovered.trace
    assert len(revisions.list_heads("source_record")) == source_count + 1
    assert len(revisions.list_heads("context_read_audit")) == audit_count + 1


def test_source_quotes_are_independently_time_scoped(tmp_path: Path) -> None:
    revisions, _, grants, sessions, reads, _ = environment(tmp_path)
    fixture = confirmed_inputs()
    old_record = fixture.source_records[0]
    recently_seen_memory = copy.deepcopy(dict(fixture.memory_atoms[0]))
    recently_seen_memory["last_seen_on"] = "2026-08-03"
    revisions.commit_many(
        [old_record, recently_seen_memory], committed_at="2026-08-23T09:50:00+08:00",
    )
    scope = {"from": "2026-08-03", "to": "2026-08-03"}
    grant = grants.grant(
        client_id="research-ai", allowed_kinds=("memory_atom", "source_record"),
        topic_scope=("产品方法",), time_scope=scope, max_sensitivity="normal",
        allow_source_quotes=True, allowed_writeback=(),
        expires_at="2026-08-24T10:00:00+08:00", created_at="2026-08-23T09:59:00+08:00",
    )
    session = sessions.open(
        grant_id=str(grant["id"]), client_id="research-ai", task="继续 Memento 后端设计",
        topic_scope=("产品方法",), time_scope=scope, opened_at="2026-08-23T10:00:00+08:00",
    )
    pack = reads.create(
        ContextRequest(
            grant_id=str(grant["id"]), session_id=str(session["id"]), client_id="research-ai",
            task="继续 Memento 后端设计", topic_scope=("产品方法",), time_scope=scope,
            include_source_quotes=True,
        ),
        inputs=ProjectionInputs(source_records=(old_record,), memory_atoms=(recently_seen_memory,)),
        requested_at="2026-08-23T10:02:00+08:00",
        completed_at="2026-08-23T10:02:01+08:00",
    ).pack
    assert pack["memories"]
    assert pack["source_quotes"] == []
    assert {ref["kind"] for ref in pack["selected_refs"]} == {"memory_atom"}


def test_mcp_rejects_unknown_path_arguments(tmp_path: Path) -> None:
    revisions, _, grants, sessions, reads, writes = environment(tmp_path)
    grant_id, session_id, inputs = authority(revisions, grants, sessions)
    server = LocalMcpContextServer(ContextToolFacade(reads, writes))
    with pytest.raises(ContractError, match="vault_path"):
        server.call_tool(
            "memento.search_context",
            {
                "grant_id": grant_id, "session_id": session_id, "client_id": "research-ai",
                "task": "继续 Memento 后端设计", "topic_scope": ["产品方法"],
                "vault_path": "/Users/example/AISecretary",
            },
            inputs=inputs, requested_at="2026-08-23T10:02:00+08:00",
            completed_at="2026-08-23T10:02:01+08:00",
        )
