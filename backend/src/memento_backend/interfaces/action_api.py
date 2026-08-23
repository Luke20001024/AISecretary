"""Stable, transport-neutral user action façade."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Optional

from memento_backend.domain.ids import make_id
from memento_backend.storage.action_inbox import ActionInbox
from memento_backend.storage.run_request_inbox import RunRequestInbox


class ActionApi:
    """Expose append-only action submission and terminal result polling."""

    def __init__(self, inbox: ActionInbox, run_requests: RunRequestInbox) -> None:
        self.inbox = inbox
        self.run_requests = run_requests

    def submit_action(self, action: Mapping[str, Any]) -> dict[str, Any]:
        saved = self.inbox.submit(copy.deepcopy(dict(action)))
        return copy.deepcopy(dict(saved))

    def poll_action_result(self, action_id: str) -> Optional[dict[str, Any]]:
        result = self.inbox.load_result(action_id)
        return None if result is None else copy.deepcopy(dict(result))

    def request_run(
        self,
        run_kind: str,
        scope: Mapping[str, Any],
        *,
        requested_at: str,
    ) -> dict[str, Any]:
        base = {
            "run_kind": run_kind,
            "scope": copy.deepcopy(dict(scope)),
            "base_user_action_watermark_sha256": self.inbox.current_watermark(),
            "requested_at": requested_at,
            "requested_by": "user",
        }
        request = {
            "schema_version": "1.0",
            "kind": "memento_run_request",
            "request_id": make_id("run_request", "run-request-v1", base),
            **base,
        }
        saved = self.run_requests.submit(request)
        return copy.deepcopy(dict(saved))
