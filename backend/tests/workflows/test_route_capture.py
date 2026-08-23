from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any

import pytest

from memento_backend.agents.capture_understanding_agent import CaptureInput, CaptureUnderstandingAgent
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_json
from memento_backend.providers.protocol import ProviderRequest, ProviderResponse, ProviderUsage
from memento_backend.storage.action_inbox import ActionInbox, EMPTY_ACTION_WATERMARK
from memento_backend.storage.atomic import AtomicFileStore
from memento_backend.storage.revision_store import RevisionStore
from memento_backend.storage.run_ledger import RunLedger
from memento_backend.workflows.route_capture import CaptureWorkflow
from tests.contracts.samples import source_record


def environment(tmp_path: Path) -> tuple[RevisionStore, ActionInbox, CaptureWorkflow]:
    root = tmp_path / "isolated-v2"
    root.mkdir(mode=0o700)
    files = AtomicFileStore(root)
    revisions = RevisionStore(files)
    actions = ActionInbox(files)
    return revisions, actions, CaptureWorkflow(revisions, actions, CaptureUnderstandingAgent(), RunLedger(files))


def saved_record(revisions: RevisionStore, source_type: str) -> dict[str, Any]:
    value: dict[str, Any] = copy.deepcopy(source_record())
    value["source_type"] = source_type
    revisions.commit(value)
    return value


def test_link_read_later_commits_one_atomic_three_object_route(tmp_path: Path) -> None:
    revisions, _, workflow = environment(tmp_path)
    record = saved_record(revisions, "url")
    result = workflow.route(
        CaptureInput(
            record,
            "https://example.com/article\n待会再看",
            user_note="待会再看",
            resource_url="https://example.com/article",
            resource_title="待阅读文章",
        ),
        created_at="2026-08-23T10:00:00+08:00",
    )
    assert result.processing_route == "ask_on_use"
    assert [ref["kind"] for ref in result.committed_refs] == [
        "capture_decision", "resource_card", "read_later_intent",
    ]
    assert revisions.list_heads("record_interpretation") == []
    assert revisions.list_heads("memory_atom") == []
    assert revisions.list_heads("theme") == []


def test_long_resource_and_highlighted_resource_keep_different_boundaries(tmp_path: Path) -> None:
    revisions, _, workflow = environment(tmp_path)
    plain = saved_record(revisions, "screenshot_ocr")
    plain_result = workflow.route(
        CaptureInput(plain, "整页 OCR 作者内容", resource_title="网页截图"),
        created_at="2026-08-23T10:00:00+08:00",
    )
    assert plain_result.processing_route == "resource_index"
    assert len(plain_result.committed_refs) == 2
    decision = revisions.list_heads("capture_decision")[0]
    assert decision["user_signal_spans"] == []

    highlighted: dict[str, Any] = copy.deepcopy(source_record())
    highlighted["record_id"] = "rec_222222222222222222222222"
    highlighted["source_type"] = "screenshot_ocr"
    highlighted["entry_sha256"] = "e" * 64
    revisions.commit(highlighted)
    mixed_result = workflow.route(
        CaptureInput(
            highlighted,
            "作者段落\n这个和当前 Context 问题有关",
            selected_text="作者段落",
            user_note="这个和当前 Context 问题有关",
            resource_title="带备注截图",
        ),
        created_at="2026-08-23T10:01:00+08:00",
    )
    assert mixed_result.processing_route == "resource_index_and_interpret"
    assert len(mixed_result.committed_refs) == 2


def test_personal_text_commits_decision_and_waits_for_l1(tmp_path: Path) -> None:
    revisions, _, workflow = environment(tmp_path)
    record = saved_record(revisions, "text")
    result = workflow.route(
        CaptureInput(record, "先记录判断发生变化的理由", user_authored=True),
        created_at="2026-08-23T10:00:00+08:00",
    )
    assert result.processing_route == "interpret"
    assert [ref["kind"] for ref in result.committed_refs] == ["capture_decision"]
    assert revisions.list_heads("record_interpretation") == []


class BlockingProvider:
    def __init__(self, entered: threading.Event, resume: threading.Event) -> None:
        self.entered = entered
        self.resume = resume

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.entered.set()
        assert self.resume.wait(timeout=5)
        return ProviderResponse(
            output={
                "content_role": "personal_signal",
                "processing_route": "interpret",
                "resource_scope": "none",
                "reason_code": "explicit_user_judgment",
                "confidence": "medium",
                "needs_user_confirmation": False,
            },
            usage=ProviderUsage("provider", "fixture", "fixture-model", "succeeded", 20, 8, 28, 0.0, 20),
        )


def test_user_action_during_provider_attempt_invalidates_candidate_commit(tmp_path: Path) -> None:
    revisions, actions, _ = environment(tmp_path)
    record = saved_record(revisions, "voice_transcript")
    entered = threading.Event()
    resume = threading.Event()
    workflow = CaptureWorkflow(
        revisions,
        actions,
        CaptureUnderstandingAgent(BlockingProvider(entered, resume)),
        RunLedger(revisions.files),
    )
    errors: list[BaseException] = []

    def run() -> None:
        try:
            workflow.route(
                CaptureInput(record, "那个方向再看一下", user_note="那个方向再看一下"),
                created_at="2026-08-23T10:00:00+08:00",
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert entered.wait(timeout=5)
    target_ref = {
        "kind": "source_record",
        "id": record["record_id"],
        "revision": record["revision"],
        "revision_sha256": sha256_json(record),
    }
    body = {
        "action": "edit",
        "target_ref": target_ref,
        "payload": {"note": "用户在运行中修改"},
        "base_user_action_watermark_sha256": EMPTY_ACTION_WATERMARK,
        "submitted_at": "2026-08-23T10:00:01+08:00",
        "submitted_by": "user",
    }
    actions.submit({
        "schema_version": "1.0",
        "kind": "memento_user_action",
        "action_id": make_id("user_action", "user-action-v1", body),
        **body,
    })
    resume.set()
    thread.join(timeout=5)
    assert len(errors) == 1
    assert isinstance(errors[0], ContractError)
    assert errors[0].kind == "conflict"
    assert revisions.list_heads("capture_decision") == []
