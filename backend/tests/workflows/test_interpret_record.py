from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from memento_backend.agents.capture_understanding_agent import CaptureInput, CaptureUnderstandingAgent
from memento_backend.agents.record_interpreter import InterpretationInput, RecordInterpreter
from memento_backend.storage.action_inbox import ActionInbox
from memento_backend.storage.atomic import AtomicFileStore
from memento_backend.storage.revision_store import RevisionStore
from memento_backend.storage.run_ledger import RunLedger
from memento_backend.workflows.interpret_record import InterpretationWorkflow
from memento_backend.workflows.route_capture import CaptureWorkflow
from tests.contracts.samples import source_record


def test_l0_then_l1_commits_audited_interpretation_without_memory_atom(tmp_path: Path) -> None:
    root = tmp_path / "isolated-v2"
    root.mkdir(mode=0o700)
    files = AtomicFileStore(root)
    revisions = RevisionStore(files)
    actions = ActionInbox(files)
    ledger = RunLedger(files)
    record: dict[str, Any] = copy.deepcopy(source_record())
    record["source_type"] = "text"
    revisions.commit(record)
    text = "先确认边界，方向清楚后立即开始"
    capture = CaptureWorkflow(revisions, actions, CaptureUnderstandingAgent(), ledger).route(
        CaptureInput(record, text, user_authored=True),
        created_at="2026-08-23T12:00:00+08:00",
    )
    decision = revisions.load_head("capture_decision", str(capture.committed_refs[0]["id"]))
    result = InterpretationWorkflow(revisions, actions, RecordInterpreter(), ledger).interpret(
        InterpretationInput(record, decision, text),
        created_at="2026-08-23T12:01:00+08:00",
    )
    assert result.committed_ref is not None
    assert result.committed_ref["kind"] == "record_interpretation"
    interpretation = revisions.load_head("record_interpretation", str(result.committed_ref["id"]))
    assert interpretation["summary"] == text
    assert revisions.list_heads("memory_atom") == []
    assert revisions.list_heads("theme") == []
    assert ledger.load(str(result.candidate["run_id"]))["terminal_status"] == "committed"
