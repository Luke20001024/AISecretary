"""Append-only user action inbox, terminal results and invalidation watermark."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_json, validate_datetime, validate_id
from memento_backend.domain.refs import ObjectRef

from .atomic import AtomicFileStore


ACTION_WATERMARK_PATH = "indexes/user-action-watermark.json"
EMPTY_ACTION_WATERMARK = sha256_json({"actions": []})


class ActionInbox:
    """Record every user action and exactly one immutable terminal outcome."""

    def __init__(self, files: AtomicFileStore) -> None:
        self.files = files
        self.files.ensure_directory("actions/inbox")
        self.files.ensure_directory("actions/results")
        self.files.ensure_directory("indexes")

    def current_watermark(self) -> str:
        return str(self._rebuild_watermark_value()["user_action_watermark_sha256"])

    def load_watermark(self) -> Mapping[str, Any]:
        if not self.files.exists(ACTION_WATERMARK_PATH):
            return self._build_watermark([])
        value = self.files.read_json(ACTION_WATERMARK_PATH)
        self._validate_watermark(value)
        return value

    def assert_watermark(self, expected: str) -> None:
        if expected != self.current_watermark():
            raise ContractError("user action watermark changed", kind="conflict")

    def submit(self, action: Mapping[str, Any]) -> Mapping[str, Any]:
        value = dict(action)
        validate_contract("user-action-v1.schema.json", value)
        validate_id("user_action", value["action_id"], "action_id")
        ObjectRef.from_dict(value["target_ref"])
        with self.files.lock("user-actions"):
            before = self._rebuild_watermark_value()
            path = self._action_path(str(value["action_id"]))
            if self.files.exists(path):
                existing = self.files.read_json(path)
                if existing != value:
                    raise ContractError("action id is already bound to different content", kind="conflict")
                after = self._rebuild_watermark_value()
                self.files.replace_json(ACTION_WATERMARK_PATH, after)
                before_retry = self._rebuild_watermark_value(exclude_action_id=str(value["action_id"]))
                if value["base_user_action_watermark_sha256"] != before_retry["user_action_watermark_sha256"]:
                    self._record_conflict_locked(
                        value,
                        reason_code="base_watermark_stale",
                        current_ref=None,
                        processed_at=str(value["submitted_at"]),
                        watermark=str(after["user_action_watermark_sha256"]),
                    )
                    raise ContractError("stale user action was retained with a conflict result", kind="conflict")
                return existing
            self.files.write_new_json_idempotent(path, value)
            after = self._rebuild_watermark_value()
            self.files.replace_json(ACTION_WATERMARK_PATH, after)
            if value["base_user_action_watermark_sha256"] != before["user_action_watermark_sha256"]:
                self._record_conflict_locked(
                    value,
                    reason_code="base_watermark_stale",
                    current_ref=None,
                    processed_at=str(value["submitted_at"]),
                    watermark=str(after["user_action_watermark_sha256"]),
                )
                raise ContractError("stale user action was retained with a conflict result", kind="conflict")
            return value

    def load_action(self, action_id: str) -> Mapping[str, Any]:
        validate_id("user_action", action_id, "action_id")
        value = self.files.read_json(self._action_path(action_id))
        validate_contract("user-action-v1.schema.json", value)
        return value

    def load_result(self, action_id: str) -> Optional[Mapping[str, Any]]:
        validate_id("user_action", action_id, "action_id")
        path = self._result_path(action_id)
        if not self.files.exists(path):
            return None
        value = self.files.read_json(path)
        validate_contract("action-result-v1.schema.json", value)
        return value

    def guard_target(
        self,
        action_id: str,
        *,
        current_ref: Optional[Mapping[str, Any]],
        processed_at: str,
    ) -> bool:
        validate_datetime(processed_at, "processed_at")
        with self.files.lock("user-actions"):
            action = self.load_action(action_id)
            expected = ObjectRef.from_dict(action["target_ref"])
            actual = None if current_ref is None else ObjectRef.from_dict(current_ref)
            if actual is not None and actual == expected:
                return True
            self._record_conflict_locked(
                action,
                reason_code="target_revision_stale",
                current_ref=None if actual is None else actual.to_dict(),
                processed_at=processed_at,
                watermark=self.current_watermark(),
            )
            return False

    def record_result(self, result: Mapping[str, Any]) -> Mapping[str, Any]:
        value = dict(result)
        validate_contract("action-result-v1.schema.json", value)
        with self.files.lock("user-actions"):
            action = self.load_action(str(value["action_id"]))
            self._validate_result_binding(action, value)
            if value["user_action_watermark_sha256"] != self.current_watermark():
                raise ContractError("action result was computed from a stale user watermark", kind="conflict")
            self.files.write_new_json_idempotent(self._result_path(str(value["action_id"])), value)
            return value

    def recover_watermark(self) -> Mapping[str, Any]:
        with self.files.lock("user-actions"):
            rebuilt = self._rebuild_watermark_value()
            if dict(self.load_watermark()) != rebuilt:
                self.files.replace_json(ACTION_WATERMARK_PATH, rebuilt)
            return rebuilt

    def _record_conflict_locked(
        self,
        action: Mapping[str, Any],
        *,
        reason_code: str,
        current_ref: Optional[Mapping[str, Any]],
        processed_at: str,
        watermark: str,
    ) -> Mapping[str, Any]:
        base = {
            "action_id": action["action_id"],
            "action_sha256": sha256_json(action),
            "status": "conflict",
            "reason_code": reason_code,
            "target_ref": action["target_ref"],
            "current_ref": current_ref,
            "committed_ref": None,
            "processed_at": processed_at,
            "user_action_watermark_sha256": watermark,
        }
        result = {
            "schema_version": "1.0",
            "kind": "memento_action_result",
            "result_id": make_id("action_result", "action-result-v1", base),
            **base,
        }
        validate_contract("action-result-v1.schema.json", result)
        self.files.write_new_json_idempotent(self._result_path(str(action["action_id"])), result)
        return result

    @staticmethod
    def _validate_result_binding(action: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        if result["action_sha256"] != sha256_json(action) or result["target_ref"] != action["target_ref"]:
            raise ContractError("action result is not bound to the immutable action", kind="evidence")
        base = {
            key: result[key]
            for key in (
                "action_id", "action_sha256", "status", "reason_code", "target_ref",
                "current_ref", "committed_ref", "processed_at", "user_action_watermark_sha256",
            )
        }
        expected_id = make_id("action_result", "action-result-v1", base)
        if result["result_id"] != expected_id:
            raise ContractError("action result id does not bind its terminal payload", kind="evidence")

    def _rebuild_watermark_value(self, *, exclude_action_id: Optional[str] = None) -> dict[str, Any]:
        paths = self.files.list_files("actions/inbox", suffix=".json")
        actions = []
        updated_at = "1970-01-01T00:00:00+00:00"
        for path in paths:
            value = self.files.read_json(path)
            validate_contract("user-action-v1.schema.json", value)
            if value["action_id"] == exclude_action_id:
                continue
            actions.append({"action_id": value["action_id"], "sha256": sha256_json(value)})
            updated_at = max(updated_at, str(value["submitted_at"]))
        actions.sort(key=lambda item: str(item["action_id"]))
        return self._build_watermark(actions, updated_at=updated_at)

    @staticmethod
    def _build_watermark(actions: Sequence[Mapping[str, Any]], *, updated_at: str = "1970-01-01T00:00:00+00:00") -> dict[str, Any]:
        normalized = [dict(item) for item in actions]
        value = {
            "schema_version": "1.0",
            "kind": "memento_user_action_watermark",
            "action_count": len(normalized),
            "updated_at": updated_at,
            "user_action_watermark_sha256": sha256_json({"actions": normalized}),
            "actions": normalized,
        }
        ActionInbox._validate_watermark(value)
        return value

    @staticmethod
    def _validate_watermark(value: Mapping[str, Any]) -> None:
        validate_contract("action-watermark-v1.schema.json", value)
        actions = list(value["actions"])
        if actions != sorted(actions, key=lambda item: str(item["action_id"])):
            raise ContractError("action watermark entries must be sorted")
        if value["action_count"] != len(actions):
            raise ContractError("action watermark count is stale", kind="evidence")
        if value["user_action_watermark_sha256"] != sha256_json({"actions": actions}):
            raise ContractError("action watermark hash is stale", kind="evidence")

    @staticmethod
    def _action_path(action_id: str) -> str:
        return f"actions/inbox/{action_id}.json"

    @staticmethod
    def _result_path(action_id: str) -> str:
        return f"actions/results/{action_id}.json"
