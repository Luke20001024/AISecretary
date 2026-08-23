from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from memento_backend.domain.errors import ContractError
from memento_backend.interfaces.v1_source_adapter import adapt_new_v1_source_record
from memento_backend.storage.atomic import AtomicFileStore
from memento_backend.storage.revision_store import RevisionStore
from memento_backend.workflows.ingest_record import IngestRecordWorkflow
from tests.contracts.samples import source_record


def v1_record() -> dict[str, Any]:
    value: dict[str, Any] = copy.deepcopy(source_record())
    value["schema_version"] = "1.0"
    value.pop("committed_by")
    return value


def test_new_v1_parser_record_adapts_and_is_saved_before_agents_run(tmp_path: Path) -> None:
    root = tmp_path / "isolated-v2"
    root.mkdir(mode=0o700)
    revisions = RevisionStore(AtomicFileStore(root))
    adapted = adapt_new_v1_source_record(v1_record())
    ref = IngestRecordWorkflow(revisions).ingest(adapted)
    assert ref["kind"] == "source_record"
    assert revisions.load_head("source_record", str(ref["id"])) == adapted


def test_v1_bridge_fails_closed_for_an_edited_chain_or_extra_field() -> None:
    edited = v1_record()
    edited["revision"] = 2
    edited["previous_revision_sha256"] = "a" * 64
    with pytest.raises(ContractError) as exc:
        adapt_new_v1_source_record(edited)
    assert exc.value.kind == "migration"
    extra = v1_record()
    extra["raw_content"] = "must stay in the source file"
    with pytest.raises(ContractError):
        adapt_new_v1_source_record(extra)
