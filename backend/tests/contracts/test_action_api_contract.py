from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest

from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_json
from memento_backend.interfaces.action_api import ActionApi
from memento_backend.storage.action_inbox import EMPTY_ACTION_WATERMARK, ActionInbox
from memento_backend.storage.atomic import AtomicFileStore
from memento_backend.storage.run_request_inbox import RunRequestInbox
from tests.fixtures.formal_20d import formal_20d_inputs


def action_stack(tmp_path: Path) -> tuple[ActionApi, ActionInbox, RunRequestInbox]:
    root = tmp_path / "isolated-v2"
    root.mkdir(mode=0o700)
    inbox = ActionInbox(AtomicFileStore(root))
    run_requests = RunRequestInbox(inbox.files, inbox)
    return ActionApi(inbox, run_requests), inbox, run_requests


def source_ref() -> dict[str, Any]:
    record = formal_20d_inputs().source_records[0]
    return {
        "kind": "source_record",
        "id": record["record_id"],
        "revision": record["revision"],
        "revision_sha256": sha256_json(record),
    }


def action_value(*, nonce: str, watermark: str) -> dict[str, Any]:
    body = {
        "action": "edit",
        "target_ref": source_ref(),
        "payload": {"note": nonce},
        "base_user_action_watermark_sha256": watermark,
        "submitted_at": "2026-08-23T11:00:00+08:00",
        "submitted_by": "user",
    }
    return {
        "schema_version": "1.0",
        "kind": "memento_user_action",
        "action_id": make_id("user_action", "user-action-v1", {"nonce": nonce, **body}),
        **body,
    }


def terminal_result(action: Mapping[str, Any], inbox: ActionInbox) -> dict[str, Any]:
    base = {
        "action_id": action["action_id"],
        "action_sha256": sha256_json(action),
        "status": "applied",
        "reason_code": "user_edit_applied",
        "target_ref": action["target_ref"],
        "current_ref": action["target_ref"],
        "committed_ref": action["target_ref"],
        "processed_at": "2026-08-23T11:01:00+08:00",
        "user_action_watermark_sha256": inbox.current_watermark(),
    }
    return {
        "schema_version": "1.0",
        "kind": "memento_action_result",
        "result_id": make_id("action_result", "action-result-v1", base),
        **base,
    }


def test_action_api_submits_and_polls_one_terminal_result(tmp_path: Path) -> None:
    api, inbox, _ = action_stack(tmp_path)
    action = action_value(nonce="front-contract", watermark=EMPTY_ACTION_WATERMARK)

    returned = api.submit_action(action)
    assert returned == action
    assert api.poll_action_result(str(action["action_id"])) is None

    result = terminal_result(action, inbox)
    inbox.record_result(result)
    assert api.poll_action_result(str(action["action_id"])) == result


def test_action_api_returns_detached_values_and_preserves_idempotency(tmp_path: Path) -> None:
    api, inbox, _ = action_stack(tmp_path)
    action = action_value(nonce="detached", watermark=EMPTY_ACTION_WATERMARK)
    returned = api.submit_action(action)
    returned["payload"]["note"] = "caller mutation"

    assert inbox.load_action(str(action["action_id"])) == action
    assert api.submit_action(action) == action


def test_action_api_rejects_invalid_or_stale_actions_with_existing_reasoning(tmp_path: Path) -> None:
    api, _, _ = action_stack(tmp_path)
    invalid = action_value(nonce="invalid", watermark=EMPTY_ACTION_WATERMARK)
    invalid["target_ref"]["id"] = "thm_ffffffffffffffffffffffff"
    with pytest.raises(ContractError):
        api.submit_action(invalid)

    first = action_value(nonce="first", watermark=EMPTY_ACTION_WATERMARK)
    api.submit_action(first)
    stale = action_value(nonce="stale", watermark=EMPTY_ACTION_WATERMARK)
    with pytest.raises(ContractError) as raised:
        api.submit_action(stale)
    assert raised.value.kind == "conflict"
    result = api.poll_action_result(str(stale["action_id"]))
    assert result is not None
    assert result["reason_code"] == "base_watermark_stale"


def test_request_run_creates_an_immutable_queued_request(tmp_path: Path) -> None:
    api, _, run_requests = action_stack(tmp_path)
    request = api.request_run(
        "rebuild_projections",
        {"local_date": "2026-08-23", "topics": ["产品方法"]},
        requested_at="2026-08-23T12:00:00+08:00",
    )

    assert request["request_id"].startswith("rrq_")
    assert run_requests.load_request(str(request["request_id"])) == request
    assert run_requests.read_status(str(request["request_id"]))["status"] == "queued"
    assert api.request_run(
        "rebuild_projections",
        {"local_date": "2026-08-23", "topics": ["产品方法"]},
        requested_at="2026-08-23T12:00:00+08:00",
    ) == request


def test_run_result_is_bound_to_the_request_and_becomes_terminal(tmp_path: Path) -> None:
    api, _, run_requests = action_stack(tmp_path)
    request = api.request_run(
        "daily_integrator",
        {"local_date": "2026-08-23"},
        requested_at="2026-08-23T12:10:00+08:00",
    )
    base = {
        "request_id": request["request_id"],
        "request_sha256": sha256_json(request),
        "status": "completed",
        "reason_code": "run_completed",
        "agent_run_ids": [],
        "committed_refs": [],
        "finished_at": "2026-08-23T12:10:02+08:00",
    }
    result = {
        "schema_version": "1.0",
        "kind": "memento_run_result",
        "result_id": make_id("run_result", "run-result-v1", base),
        **base,
    }
    run_requests.record_result(result)
    status = run_requests.read_status(str(request["request_id"]))
    assert status["status"] == "completed"
    assert status["finished_at"] == result["finished_at"]

    forged = dict(result)
    forged["request_sha256"] = "f" * 64
    forged_base = {
        key: forged[key]
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
    forged["result_id"] = make_id("run_result", "run-result-v1", forged_base)
    with pytest.raises(ContractError) as raised:
        run_requests.record_result(forged)
    assert raised.value.kind == "evidence"
