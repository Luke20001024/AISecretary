"""Append-only frontend run requests and terminal orchestration results."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_json, validate_id
from memento_backend.domain.refs import ObjectRef

from .action_inbox import ActionInbox
from .atomic import AtomicFileStore


class RunRequestInbox:
    """Queue bounded requests without giving the frontend Agent access."""

    def __init__(self, files: AtomicFileStore, actions: ActionInbox) -> None:
        self.files = files
        self.actions = actions
        self.files.ensure_directory("run-requests/inbox")
        self.files.ensure_directory("run-requests/results")

    def submit(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        value = dict(request)
        self._validate_request(value)
        request_id = str(value["request_id"])
        with self.files.lock("run-requests"):
            path = self._request_path(request_id)
            if self.files.exists(path):
                existing = self.files.read_json(path)
                if existing != value:
                    raise ContractError(
                        "run request id is already bound to different content",
                        kind="conflict",
                    )
                return existing
            self.files.write_new_json(path, value)
            if value["base_user_action_watermark_sha256"] != self.actions.current_watermark():
                self._record_conflict_locked(value)
                raise ContractError(
                    "stale run request was retained with a conflict result",
                    kind="conflict",
                )
            return value

    def load_request(self, request_id: str) -> Mapping[str, Any]:
        validate_id("run_request", request_id, "request_id")
        value = self.files.read_json(self._request_path(request_id))
        self._validate_request(value)
        return value

    def load_result(self, request_id: str) -> Optional[Mapping[str, Any]]:
        validate_id("run_request", request_id, "request_id")
        path = self._result_path(request_id)
        if not self.files.exists(path):
            return None
        value = self.files.read_json(path)
        self._validate_result(value)
        request = self.load_request(request_id)
        self._validate_result_binding(request, value)
        return value

    def record_result(self, result: Mapping[str, Any]) -> Mapping[str, Any]:
        value = dict(result)
        self._validate_result(value)
        request_id = str(value["request_id"])
        with self.files.lock("run-requests"):
            request = self.load_request(request_id)
            self._validate_result_binding(request, value)
            self.files.write_new_json_idempotent(self._result_path(request_id), value)
            return value

    def read_status(self, request_id: str) -> Mapping[str, Any]:
        request = self.load_request(request_id)
        result = self.load_result(request_id)
        value = {
            "schema_version": "1.0",
            "kind": "memento_run_status_projection",
            "request_id": request_id,
            "run_kind": request["run_kind"],
            "status": "queued" if result is None else result["status"],
            "requested_at": request["requested_at"],
            "finished_at": None if result is None else result["finished_at"],
            "reason_code": None if result is None else result["reason_code"],
            "agent_run_ids": [] if result is None else list(result["agent_run_ids"]),
            "committed_refs": [] if result is None else list(result["committed_refs"]),
        }
        validate_contract("run-status-projection-v1.schema.json", value)
        return value

    def _record_conflict_locked(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        base = {
            "request_id": request["request_id"],
            "request_sha256": sha256_json(request),
            "status": "conflict",
            "reason_code": "base_watermark_stale",
            "agent_run_ids": [],
            "committed_refs": [],
            "finished_at": request["requested_at"],
        }
        result = {
            "schema_version": "1.0",
            "kind": "memento_run_result",
            "result_id": make_id("run_result", "run-result-v1", base),
            **base,
        }
        self._validate_result(result)
        self.files.write_new_json_idempotent(
            self._result_path(str(request["request_id"])), result
        )
        return result

    @staticmethod
    def _validate_request(value: Mapping[str, Any]) -> None:
        validate_contract("run-request-v1.schema.json", value)
        validate_id("run_request", value["request_id"], "request_id")
        for ref in value["scope"].get("target_refs", []):
            ObjectRef.from_dict(ref)
        base = {
            key: value[key]
            for key in (
                "run_kind",
                "scope",
                "base_user_action_watermark_sha256",
                "requested_at",
                "requested_by",
            )
        }
        if value["request_id"] != make_id(
            "run_request", "run-request-v1", base
        ):
            raise ContractError(
                "run request id does not bind its immutable payload",
                kind="evidence",
            )

    @staticmethod
    def _validate_result(value: Mapping[str, Any]) -> None:
        validate_contract("run-result-v1.schema.json", value)
        validate_id("run_result", value["result_id"], "result_id")
        for ref in value["committed_refs"]:
            ObjectRef.from_dict(ref)
        base = {
            key: value[key]
            for key in (
                "request_id",
                "request_sha256",
                "status",
                "reason_code",
                "agent_run_ids",
                "committed_refs",
                "finished_at",
            )
        }
        if value["result_id"] != make_id("run_result", "run-result-v1", base):
            raise ContractError(
                "run result id does not bind its terminal payload",
                kind="evidence",
            )

    @staticmethod
    def _validate_result_binding(
        request: Mapping[str, Any], result: Mapping[str, Any]
    ) -> None:
        if result["request_id"] != request["request_id"]:
            raise ContractError("run result request id is stale", kind="evidence")
        if result["request_sha256"] != sha256_json(request):
            raise ContractError("run result request hash is stale", kind="evidence")

    @staticmethod
    def _request_path(request_id: str) -> str:
        return f"run-requests/inbox/{request_id}.json"

    @staticmethod
    def _result_path(request_id: str) -> str:
        return f"run-requests/results/{request_id}.json"
