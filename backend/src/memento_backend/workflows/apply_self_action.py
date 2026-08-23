"""Apply explicit user confirmation, edits, scope changes and withdrawal."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from memento_backend.agents.self_understanding_agent import self_insight_ref
from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_json, validate_datetime
from memento_backend.storage.action_inbox import ActionInbox
from memento_backend.storage.revision_store import RevisionStore


class ApplySelfActionWorkflow:
    def __init__(self, revisions: RevisionStore, actions: ActionInbox) -> None:
        self.revisions = revisions
        self.actions = actions

    def apply(self, action_id: str, *, processed_at: str) -> Mapping[str, Any]:
        validate_datetime(processed_at, "processed_at")
        action = self.actions.load_action(action_id)
        existing_result = self.actions.load_result(action_id)
        if existing_result is not None:
            return existing_result
        if action["target_ref"]["kind"] != "self_insight":
            raise ContractError("self action target kind is unauthorized", kind="authorization")
        current_ref = self.revisions.current_ref("self_insight", str(action["target_ref"]["id"]))
        if current_ref != action["target_ref"]:
            recovered = self._recover_applied(action)
            if recovered is not None:
                return self.actions.record_result(recovered)
        if not self.actions.guard_target(action_id, current_ref=current_ref, processed_at=processed_at):
            result = self.actions.load_result(action_id)
            if result is None:
                raise ContractError("stale self action lacks terminal result", kind="evidence")
            return result
        if current_ref is None:
            raise ContractError("self action target does not exist", kind="evidence")
        current = self.revisions.load_head("self_insight", str(action["target_ref"]["id"]))
        proposal, reason_code = self._proposal(current, action, processed_at)
        committed_ref = self.revisions.commit(proposal, expected_ref=current_ref, committed_at=processed_at)
        result = self._result(action, current_ref, committed_ref, reason_code, processed_at)
        return self.actions.record_result(result)

    @staticmethod
    def _proposal(current: Mapping[str, Any], action: Mapping[str, Any], processed_at: str) -> tuple[dict[str, Any], str]:
        operation = str(action["action"])
        payload = dict(action["payload"])
        proposal = {
            **dict(current),
            "revision": int(current["revision"]) + 1,
            "previous_revision_sha256": sha256_json(current),
            "created_at": processed_at,
            "committed_by": "user",
            "committing_action_id": action["action_id"],
        }
        confirmation, visibility = ApplySelfActionWorkflow._confirmed_state(current)
        if operation in {"confirm", "accurate"}:
            proposal["confirmation"] = confirmation
            proposal["visibility"] = visibility
            proposal["maturity"] = "stable" if current["maturity"] != "dormant" else "dormant"
            proposal["change_reason"] = "用户明确确认这条当前理解"
            reason = "self_insight_user_confirmed"
        elif operation == "scope":
            if set(payload) != {"scope"} or not isinstance(payload["scope"], str):
                raise ContractError("self scope action requires one scope string")
            proposal["scope"] = payload["scope"]
            proposal["confirmation"] = confirmation
            proposal["visibility"] = visibility
            proposal["change_reason"] = "用户限定了这条理解的适用范围"
            reason = "self_insight_scope_changed"
        elif operation in {"edit", "revise", "changed"}:
            allowed = {"title", "statement", "scope", "uncertainty"}
            if not payload or set(payload) - allowed or any(not isinstance(value, str) for value in payload.values()):
                raise ContractError("self edit action contains unauthorized fields")
            proposal.update(payload)
            proposal["confirmation"] = confirmation
            proposal["visibility"] = visibility
            proposal["change_reason"] = "用户直接修订了这条当前理解"
            reason = "self_insight_user_revised"
        elif operation in {"reject", "tombstone"}:
            if payload:
                raise ContractError("self withdrawal action does not accept payload")
            proposal["maturity"] = "tombstone"
            proposal["visibility"] = "restricted"
            proposal["change_reason"] = "用户撤回了这条当前理解"
            reason = "self_insight_withdrawn"
        else:
            raise ContractError("self action is unsupported", kind="authorization")
        validate_contract("self-insight-v2.schema.json", proposal)
        return proposal, reason

    @staticmethod
    def _confirmed_state(current: Mapping[str, Any]) -> tuple[str, str]:
        if current["sensitivity"] == "normal":
            return "user_confirmed", "grant_only"
        return "restricted", "restricted"

    def _result(
        self,
        action: Mapping[str, Any],
        current_ref: Mapping[str, Any],
        committed_ref: Mapping[str, Any],
        reason_code: str,
        processed_at: str,
    ) -> dict[str, Any]:
        base = {
            "action_id": action["action_id"],
            "action_sha256": sha256_json(action),
            "status": "applied",
            "reason_code": reason_code,
            "target_ref": action["target_ref"],
            "current_ref": dict(current_ref),
            "committed_ref": dict(committed_ref),
            "processed_at": processed_at,
            "user_action_watermark_sha256": self.actions.current_watermark(),
        }
        result = {
            "schema_version": "1.0",
            "kind": "memento_action_result",
            "result_id": make_id("action_result", "action-result-v1", base),
            **base,
        }
        validate_contract("action-result-v1.schema.json", result)
        return result

    def _recover_applied(self, action: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        target = action["target_ref"]
        try:
            history = self.revisions.list_revisions("self_insight", str(target["id"]))
        except ContractError as exc:
            if exc.kind == "not_found":
                return None
            raise
        target_revision = int(target["revision"])
        if target_revision < 1 or len(history) <= target_revision:
            return None
        target_value = history[target_revision - 1]
        committed = history[target_revision]
        if self_insight_ref(target_value) != target:
            return None
        if committed.get("committing_action_id") != action["action_id"]:
            return None
        proposal, reason_code = self._proposal(target_value, action, str(committed["created_at"]))
        if sha256_json(proposal) != sha256_json(committed):
            raise ContractError("self action recovery revision differs from action", kind="evidence")
        return self._result(
            action,
            target,
            self_insight_ref(committed),
            reason_code,
            str(committed["created_at"]),
        )
