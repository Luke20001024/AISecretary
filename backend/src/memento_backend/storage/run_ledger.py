"""Append-only terminal audit records for Agent candidates."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.ids import sha256_json, validate_datetime

from .atomic import AtomicFileStore


class RunLedger:
    def __init__(self, files: AtomicFileStore) -> None:
        self.files = files
        self.files.ensure_directory("agent-runs")

    def record(
        self,
        candidate: Mapping[str, Any],
        *,
        terminal_status: str,
        committed_refs: Sequence[Mapping[str, Any]] = (),
        finished_at: str,
    ) -> Mapping[str, Any]:
        validate_contract("agent-action-candidate-v1.schema.json", candidate)
        validate_datetime(finished_at, "finished_at")
        value = {
            "schema_version": "1.0",
            "kind": "memento_agent_run",
            "run_id": candidate["run_id"],
            "agent_role": candidate["agent_role"],
            "prompt_version": candidate["prompt_version"],
            "policy_version": candidate["policy_version"],
            "input_sha256": candidate["input_sha256"],
            "user_action_watermark_sha256": candidate["user_action_watermark_sha256"],
            "candidate_id": candidate["candidate_id"],
            "candidate_sha256": sha256_json(candidate),
            "terminal_status": terminal_status,
            "committed_refs": [dict(ref) for ref in committed_refs],
            "usage": dict(candidate["usage"]),
            "created_at": candidate["created_at"],
            "finished_at": finished_at,
        }
        validate_contract("agent-run-v1.schema.json", value)
        self.files.write_new_json_idempotent(f"agent-runs/{candidate['run_id']}.json", value)
        return value

    def load(self, run_id: str) -> Mapping[str, Any]:
        value = self.files.read_json(f"agent-runs/{run_id}.json")
        validate_contract("agent-run-v1.schema.json", value)
        return value
