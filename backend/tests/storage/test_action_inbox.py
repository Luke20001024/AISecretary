from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping

import pytest

from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_json
from memento_backend.storage.action_inbox import ACTION_WATERMARK_PATH, EMPTY_ACTION_WATERMARK, ActionInbox
from memento_backend.storage.atomic import AtomicFileStore
from tests.fixtures.formal_20d import formal_20d_inputs


def make_inbox(tmp_path: Path) -> ActionInbox:
    root = tmp_path / "isolated-v2"
    if not root.exists():
        root.mkdir(mode=0o700)
    return ActionInbox(AtomicFileStore(root))


def source_ref() -> dict[str, Any]:
    record = formal_20d_inputs().source_records[0]
    return {
        "kind": "source_record",
        "id": record["record_id"],
        "revision": record["revision"],
        "revision_sha256": sha256_json(record),
    }


def action_value(
    *,
    nonce: str,
    base_watermark: str,
    target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body = {
        "action": "edit",
        "target_ref": dict(target or source_ref()),
        "payload": {"note": nonce},
        "base_user_action_watermark_sha256": base_watermark,
        "submitted_at": "2026-08-23T10:00:00+08:00",
        "submitted_by": "user",
    }
    return {
        "schema_version": "1.0",
        "kind": "memento_user_action",
        "action_id": make_id("user_action", "user-action-v1", {"nonce": nonce, **body}),
        **body,
    }


def test_submission_advances_watermark_and_exact_retry_is_idempotent(tmp_path: Path) -> None:
    inbox = make_inbox(tmp_path)
    assert inbox.current_watermark() == EMPTY_ACTION_WATERMARK
    action = action_value(nonce="one", base_watermark=EMPTY_ACTION_WATERMARK)
    assert inbox.submit(action) == action
    advanced = inbox.current_watermark()
    assert advanced != EMPTY_ACTION_WATERMARK
    assert inbox.submit(action) == action
    assert inbox.current_watermark() == advanced


def test_stale_watermark_action_is_rejected_but_reason_is_retained(tmp_path: Path) -> None:
    inbox = make_inbox(tmp_path)
    inbox.submit(action_value(nonce="first", base_watermark=EMPTY_ACTION_WATERMARK))
    stale = action_value(nonce="stale", base_watermark=EMPTY_ACTION_WATERMARK)
    with pytest.raises(ContractError) as raised:
        inbox.submit(stale)
    assert raised.value.kind == "conflict"
    result = inbox.load_result(str(stale["action_id"]))
    assert result is not None
    assert result["status"] == "conflict"
    assert result["reason_code"] == "base_watermark_stale"


def test_target_cas_guard_retains_terminal_conflict(tmp_path: Path) -> None:
    inbox = make_inbox(tmp_path)
    action = action_value(nonce="target", base_watermark=EMPTY_ACTION_WATERMARK)
    inbox.submit(action)
    changed = dict(source_ref())
    changed["revision"] = 2
    changed["revision_sha256"] = "a" * 64
    assert not inbox.guard_target(
        str(action["action_id"]),
        current_ref=changed,
        processed_at="2026-08-23T10:01:00+08:00",
    )
    result = inbox.load_result(str(action["action_id"]))
    assert result is not None
    assert result["reason_code"] == "target_revision_stale"
    assert result["current_ref"] == changed


def test_watermark_is_rebuildable_from_append_only_actions(tmp_path: Path) -> None:
    inbox = make_inbox(tmp_path)
    first = action_value(nonce="one", base_watermark=EMPTY_ACTION_WATERMARK)
    inbox.submit(first)
    expected = inbox.current_watermark()
    (inbox.files.root / ACTION_WATERMARK_PATH).unlink()
    rebuilt = inbox.recover_watermark()
    assert rebuilt["user_action_watermark_sha256"] == expected
    assert rebuilt["action_count"] == 1


def test_terminal_result_is_bound_to_action_bytes(tmp_path: Path) -> None:
    inbox = make_inbox(tmp_path)
    action = action_value(nonce="result", base_watermark=EMPTY_ACTION_WATERMARK)
    inbox.submit(action)
    base = {
        "action_id": action["action_id"],
        "action_sha256": sha256_json(action),
        "status": "applied",
        "reason_code": "user_edit_applied",
        "target_ref": action["target_ref"],
        "current_ref": action["target_ref"],
        "committed_ref": action["target_ref"],
        "processed_at": "2026-08-23T10:01:00+08:00",
        "user_action_watermark_sha256": inbox.current_watermark(),
    }
    result = {
        "schema_version": "1.0",
        "kind": "memento_action_result",
        "result_id": make_id("action_result", "action-result-v1", base),
        **base,
    }
    assert inbox.record_result(result) == result
    tampered = dict(result)
    tampered["action_sha256"] = "f" * 64
    with pytest.raises(ContractError) as raised:
        inbox.record_result(tampered)
    assert raised.value.kind == "evidence"


def test_concurrent_actions_with_one_base_watermark_have_one_clean_winner(tmp_path: Path) -> None:
    inbox = make_inbox(tmp_path)
    actions = (
        action_value(nonce="left", base_watermark=EMPTY_ACTION_WATERMARK),
        action_value(nonce="right", base_watermark=EMPTY_ACTION_WATERMARK),
    )
    barrier = threading.Barrier(2)

    def attempt(value: Mapping[str, Any]) -> str:
        barrier.wait()
        try:
            make_inbox(tmp_path).submit(value)
        except ContractError as exc:
            return exc.kind
        return "accepted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, actions))
    assert sorted(outcomes) == ["accepted", "conflict"]
    assert inbox.recover_watermark()["action_count"] == 2
    conflicts = [inbox.load_result(str(action["action_id"])) for action in actions]
    assert sum(result is not None for result in conflicts) == 1
