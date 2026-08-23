from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from memento_backend.agents.capture_understanding_agent import CaptureInput, CaptureUnderstandingAgent
from memento_backend.agents.resource_reader import ResourceReadInput, ResourceReader
from memento_backend.providers.protocol import ProviderRequest, ProviderResponse, ProviderUsage
from memento_backend.storage.action_inbox import ActionInbox
from memento_backend.storage.atomic import AtomicFileStore
from memento_backend.storage.revision_store import RevisionStore
from memento_backend.storage.run_ledger import RunLedger
from memento_backend.workflows.read_resource import ResourceReadWorkflow
from memento_backend.workflows.route_capture import CaptureWorkflow
from tests.contracts.samples import source_record


class CitedProvider:
    def __init__(self, quote: str) -> None:
        self.quote = quote

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(
            output={
                "answer": "这段资料建议先保留原始来源，再验证结论",
                "citation_quotes": [self.quote],
                "unknowns": ["资料未说明长期效果"],
            },
            usage=ProviderUsage("provider", "fixture", "fixture-model", "succeeded", 40, 15, 55, 0.0, 9),
        )


def setup_resource(tmp_path: Path) -> tuple[RevisionStore, ActionInbox, RunLedger, Mapping[str, Any], Mapping[str, Any], str]:
    root = tmp_path / "isolated-v2"
    root.mkdir(mode=0o700)
    files = AtomicFileStore(root)
    revisions = RevisionStore(files)
    actions = ActionInbox(files)
    ledger = RunLedger(files)
    record: dict[str, Any] = copy.deepcopy(source_record())
    record["source_type"] = "web_page"
    revisions.commit(record)
    text = "原文指出：重要结论需要能够回到来源。其他段落只提供背景。"
    capture = CaptureWorkflow(revisions, actions, CaptureUnderstandingAgent(), ledger).route(
        CaptureInput(record, text, resource_url="https://example.com/a", resource_title="合成资料"),
        created_at="2026-08-23T13:00:00+08:00",
    )
    resource_ref = next(ref for ref in capture.committed_refs if ref["kind"] == "resource_card")
    resource = revisions.load_head("resource_card", str(resource_ref["id"]))
    return revisions, actions, ledger, record, resource, text


def test_resource_reader_returns_cited_ephemeral_result_without_formal_belief(tmp_path: Path) -> None:
    revisions, actions, ledger, record, resource, text = setup_resource(tmp_path)
    workflow = ResourceReadWorkflow(revisions, actions, ResourceReader(CitedProvider("重要结论需要能够回到来源")), ledger)
    candidate = workflow.read(
        ResourceReadInput(resource, record, text, "这段资料与证据链有什么关系"),
        created_at="2026-08-23T13:01:00+08:00",
    )
    assert candidate["proposed_kind"] == "resource_read_result"
    assert candidate["proposed_object"]["citations"][0]["quote"] == "重要结论需要能够回到来源"
    assert revisions.list_heads("record_interpretation") == []
    assert revisions.list_heads("memory_atom") == []
    assert revisions.list_heads("theme") == []
    assert ledger.load(str(candidate["run_id"]))["terminal_status"] == "returned"


def test_resource_reader_rejects_invented_citation_as_stopped_candidate(tmp_path: Path) -> None:
    revisions, actions, _, record, resource, text = setup_resource(tmp_path)
    candidate = ResourceReadWorkflow(
        revisions, actions, ResourceReader(CitedProvider("原文里不存在的句子")), RunLedger(revisions.files),
    ).read(
        ResourceReadInput(resource, record, text, "给我结论"),
        created_at="2026-08-23T13:01:00+08:00",
    )
    assert candidate["action"] == "stop"
    assert candidate["reason_code"] == "provider_invalid_output"
