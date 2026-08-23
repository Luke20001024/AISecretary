"""Commit an already-parsed source record before any Agent is allowed to run."""

from __future__ import annotations

from typing import Any, Mapping

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.storage.revision_store import RevisionStore


class IngestRecordWorkflow:
    def __init__(self, revisions: RevisionStore) -> None:
        self.revisions = revisions

    def ingest(self, source_record: Mapping[str, Any]) -> Mapping[str, Any]:
        validate_contract("source-record-v2.schema.json", source_record)
        record_id = str(source_record["record_id"])
        if self.revisions.current_ref("source_record", record_id) is not None:
            raise ContractError("source record already has a V2 head", kind="conflict")
        return self.revisions.commit(source_record, committed_at=str(source_record["created_at"]))
