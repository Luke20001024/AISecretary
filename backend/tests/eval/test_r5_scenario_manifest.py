from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from memento_backend.agents.capture_understanding_agent import CaptureInput, CaptureUnderstandingAgent
from memento_backend.storage.action_inbox import ActionInbox
from memento_backend.storage.atomic import AtomicFileStore
from memento_backend.storage.revision_store import RevisionStore
from memento_backend.storage.run_ledger import RunLedger
from memento_backend.workflows.route_capture import CaptureWorkflow
from tests.contracts.samples import source_record


CLASS_BY_KIND = {
    "source_record": "SourceRecordRevision",
    "capture_decision": "CaptureDecisionRevision",
    "resource_card": "ResourceCardRevision",
    "read_later_intent": "ReadLaterIntentRevision",
    "record_interpretation": "RecordInterpretationRevision",
    "memory_atom": "MemoryAtomRevision",
    "theme": "ThemeRevision",
    "self_insight": "SelfInsightRevision",
}


def load_manifest() -> Mapping[str, Any]:
    path = Path(__file__).resolve().parents[2] / "eval" / "scenarios" / "manifest.json"
    with path.open("r", encoding="utf-8") as handle:
        value: Mapping[str, Any] = json.load(handle)
    return value


def test_r5_manifest_runs_all_eight_routes_without_a_provider(tmp_path: Path) -> None:
    manifest = load_manifest()
    cases = list(manifest["cases"])
    assert len(cases) == 8
    assert len({case["id"] for case in cases}) == len(cases)
    for index, case in enumerate(cases):
        root = tmp_path / str(case["id"])
        root.mkdir(mode=0o700)
        files = AtomicFileStore(root)
        revisions = RevisionStore(files)
        actions = ActionInbox(files)
        record: dict[str, Any] = copy.deepcopy(source_record())
        record["record_id"] = f"rec_{index + 1:024x}"
        record["source_type"] = case["input"]["source_type"]
        record["entry_sha256"] = f"{index + 1:x}" * 64
        record["entry_sha256"] = record["entry_sha256"][:64]
        revisions.commit(record)
        capture_input = CaptureInput(
            source_record=record,
            authorized_text=case["input"]["authorized_text"],
            user_note=case["input"].get("user_note"),
            selected_text=case["input"].get("selected_text"),
            user_authored=case["input"].get("user_authored"),
            resource_url=case["input"].get("resource_url"),
        )
        result = CaptureWorkflow(revisions, actions, CaptureUnderstandingAgent(), RunLedger(files)).route(
            capture_input,
            created_at=f"2026-08-23T14:{index:02d}:00+08:00",
        )
        decision = result.candidate["proposed_object"]
        assert decision["content_role"] == case["expected_role"]
        assert decision["processing_route"] == case["expected_route"]
        actual = {"SourceRecordRevision"}
        actual.update(CLASS_BY_KIND[str(ref["kind"])] for ref in result.committed_refs)
        assert set(case["expected_capture_objects"]) == actual
        assert not actual.intersection(case["forbidden_objects"])
