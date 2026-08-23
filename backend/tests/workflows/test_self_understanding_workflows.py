from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

import pytest

from memento_backend.agents.self_understanding_agent import (
    SelfUnderstandingAgent,
    SelfUnderstandingInput,
    self_insight_ref,
)
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_json
from memento_backend.projections import ProjectionInputs, build_projection_bundle
from memento_backend.storage.action_inbox import ActionInbox
from memento_backend.storage.atomic import AtomicFileStore
from memento_backend.storage.revision_store import RevisionStore
from memento_backend.storage.run_ledger import RunLedger
from memento_backend.workflows.apply_self_action import ApplySelfActionWorkflow
from memento_backend.workflows.update_self_understanding import UpdateSelfUnderstandingWorkflow
from tests.fixtures.formal_20d import formal_20d_inputs


def environment(
    tmp_path: Path,
) -> Tuple[RevisionStore, ActionInbox, UpdateSelfUnderstandingWorkflow, ApplySelfActionWorkflow]:
    root = tmp_path / "isolated-v2"
    root.mkdir(mode=0o700)
    files = AtomicFileStore(root)
    revisions = RevisionStore(files)
    actions = ActionInbox(files)
    ledger = RunLedger(files)
    return (
        revisions,
        actions,
        UpdateSelfUnderstandingWorkflow(revisions, actions, SelfUnderstandingAgent(), ledger),
        ApplySelfActionWorkflow(revisions, actions),
    )


def commit_material(
    revisions: RevisionStore,
    *,
    themes: Sequence[Mapping[str, Any]],
) -> ProjectionInputs:
    fixture = formal_20d_inputs()
    inputs = ProjectionInputs(
        source_records=fixture.source_records,
        interpretations=fixture.interpretations,
        memory_atoms=fixture.memory_atoms,
        relations=fixture.relations,
        themes=tuple(themes),
        resource_cards=fixture.resource_cards,
        read_later_intents=fixture.read_later_intents,
    )
    revisions.commit_many(inputs.all_objects(), committed_at="2026-08-18T22:00:00+08:00")
    return inputs


def self_input(
    inputs: ProjectionInputs,
    *,
    themes: Sequence[Mapping[str, Any]],
    existing: Optional[Mapping[str, Any]] = None,
) -> SelfUnderstandingInput:
    return SelfUnderstandingInput(
        insight_key="长期工作方式",
        as_of="2026-08-18",
        themes=tuple(themes),
        support_atoms=inputs.memory_atoms,
        boundary_atoms=(inputs.memory_atoms[3],),
        existing_insight=existing,
    )


def revise_theme(
    revisions: RevisionStore,
    theme: Mapping[str, Any],
    *,
    lifecycle: str = "tension",
) -> Mapping[str, Any]:
    revised = {
        **copy.deepcopy(dict(theme)),
        "revision": int(theme["revision"]) + 1,
        "previous_revision_sha256": sha256_json(theme),
        "lifecycle": lifecycle,
        "change_reason": "新证据揭示了这一长期主题的适用边界",
        "created_at": "2026-08-18T22:20:00+08:00",
    }
    revisions.commit(revised, expected_ref=theme_ref(theme), committed_at=str(revised["created_at"]))
    return revised


def theme_ref(theme: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": "theme",
        "id": theme["theme_id"],
        "revision": theme["revision"],
        "revision_sha256": sha256_json(theme),
    }


def user_action(
    actions: ActionInbox,
    target: Mapping[str, Any],
    *,
    operation: str,
    payload: Mapping[str, Any],
    nonce: str,
    submitted_at: str,
) -> Mapping[str, Any]:
    body = {
        "action": operation,
        "target_ref": dict(target),
        "payload": dict(payload),
        "base_user_action_watermark_sha256": actions.current_watermark(),
        "submitted_at": submitted_at,
        "submitted_by": "user",
    }
    value = {
        "schema_version": "1.0",
        "kind": "memento_user_action",
        "action_id": make_id("user_action", "user-action-v1", {"nonce": nonce, **body}),
        **body,
    }
    return actions.submit(value)


def test_self_understanding_requires_two_distinct_long_term_themes(tmp_path: Path) -> None:
    revisions, actions, workflow, apply_action = environment(tmp_path)
    del actions, apply_action
    fixture = formal_20d_inputs()
    inputs = commit_material(revisions, themes=fixture.themes)

    result = workflow.update(
        self_input(inputs, themes=fixture.themes[:1]),
        created_at="2026-08-18T22:10:00+08:00",
    )

    assert result["action"] == "no_change"
    assert result["reason_code"] == "insufficient_themes"
    assert revisions.list_heads("self_insight") == []


def test_sensitive_self_inference_stops_without_formal_write(tmp_path: Path) -> None:
    revisions, actions, workflow, apply_action = environment(tmp_path)
    del actions, apply_action
    fixture = formal_20d_inputs()
    themes = [copy.deepcopy(dict(theme)) for theme in fixture.themes]
    themes[0]["statement"] = "多条记录似乎涉及人格判断，但仍缺少用户确认"
    inputs = commit_material(revisions, themes=themes)

    result = workflow.update(
        self_input(inputs, themes=themes),
        created_at="2026-08-18T22:10:00+08:00",
    )

    assert result["action"] == "stop"
    assert result["reason_code"] == "sensitive_inference_requires_user_confirmation"
    assert revisions.list_heads("self_insight") == []


def test_self_insight_is_created_revised_and_backtraceable_in_projection(tmp_path: Path) -> None:
    revisions, actions, workflow, apply_action = environment(tmp_path)
    del actions, apply_action
    fixture = formal_20d_inputs()
    inputs = commit_material(revisions, themes=fixture.themes)

    created = workflow.update(
        self_input(inputs, themes=fixture.themes),
        created_at="2026-08-18T22:10:00+08:00",
    )
    current = revisions.load_head("self_insight", str(created["committed_ref"]["id"]))
    assert current["revision"] == 1
    assert current["confirmation"] == "draft"
    assert current["visibility"] == "local_only"
    assert len(current["theme_refs"]) == 3

    changed_theme = revise_theme(revisions, fixture.themes[0])
    current_themes = (changed_theme, fixture.themes[1], fixture.themes[2])
    revised = workflow.update(
        self_input(inputs, themes=current_themes, existing=current),
        created_at="2026-08-18T22:30:00+08:00",
    )
    current = revisions.load_head("self_insight", str(revised["committed_ref"]["id"]))
    assert current["revision"] == 2
    assert current["boundary_refs"]
    assert "边界" in current["uncertainty"]
    assert current["previous_revision_sha256"] == sha256_json(created["proposed_object"])
    history = revisions.list_revisions("self_insight", str(current["insight_id"]))
    assert [item["revision"] for item in history] == [1, 2]
    assert [item["change_reason"] for item in history] == [
        "多个长期主题首次共同支持一条当前理解",
        "长期主题中的张力为当前理解增加了边界",
    ]
    assert revisions.load_revision("self_insight", str(current["insight_id"]), 1) == created["proposed_object"]

    projection_inputs = ProjectionInputs(
        source_records=inputs.source_records,
        interpretations=inputs.interpretations,
        memory_atoms=inputs.memory_atoms,
        relations=inputs.relations,
        themes=current_themes,
        self_insights=(current,),
        resource_cards=inputs.resource_cards,
        read_later_intents=inputs.read_later_intents,
    )
    bundle = build_projection_bundle(
        projection_inputs,
        as_of="2026-08-18",
        generated_at="2026-08-18T22:40:00+08:00",
    )
    primary = bundle.projection("projections/self.json")["primary_insight"]
    detail = bundle.projection(
        f"projections/details/self_insight/{current['insight_id']}.json"
    )
    assert primary["insight_ref"] == self_insight_ref(current)
    assert primary["confirmation"] == "draft"
    assert len(detail["themes"]) == 3
    assert {item["theme_ref"]["id"] for item in detail["themes"]} == {
        theme["theme_id"] for theme in current_themes
    }
    assert any(ref["id"] == changed_theme["theme_id"] for ref in detail["boundary_refs"])


def test_user_confirmation_has_priority_and_withdrawal_cannot_be_revived(tmp_path: Path) -> None:
    revisions, actions, workflow, apply_action = environment(tmp_path)
    fixture = formal_20d_inputs()
    inputs = commit_material(revisions, themes=fixture.themes)
    created = workflow.update(
        self_input(inputs, themes=fixture.themes),
        created_at="2026-08-18T22:10:00+08:00",
    )
    current = revisions.load_head("self_insight", str(created["committed_ref"]["id"]))

    confirm = user_action(
        actions,
        self_insight_ref(current),
        operation="confirm",
        payload={},
        nonce="confirm-self",
        submitted_at="2026-08-18T22:11:00+08:00",
    )
    confirmation_result = apply_action.apply(
        str(confirm["action_id"]),
        processed_at="2026-08-18T22:12:00+08:00",
    )
    confirmed = revisions.load_head("self_insight", str(current["insight_id"]))
    assert confirmation_result["status"] == "applied"
    assert confirmed["confirmation"] == "user_confirmed"
    assert confirmed["visibility"] == "grant_only"
    assert confirmed["committed_by"] == "user"
    assert confirmed["committing_action_id"] == confirm["action_id"]

    changed_theme = revise_theme(revisions, fixture.themes[0])
    with pytest.raises(ContractError) as raised:
        workflow.update(
            self_input(
                inputs,
                themes=(changed_theme, fixture.themes[1], fixture.themes[2]),
                existing=confirmed,
            ),
            created_at="2026-08-18T22:30:00+08:00",
        )
    assert raised.value.kind == "authorization"
    assert revisions.load_head("self_insight", str(current["insight_id"])) == confirmed

    withdraw = user_action(
        actions,
        self_insight_ref(confirmed),
        operation="tombstone",
        payload={},
        nonce="withdraw-self",
        submitted_at="2026-08-18T22:31:00+08:00",
    )
    withdrawal_result = apply_action.apply(
        str(withdraw["action_id"]),
        processed_at="2026-08-18T22:32:00+08:00",
    )
    withdrawn = revisions.load_head("self_insight", str(current["insight_id"]))
    assert withdrawal_result["status"] == "applied"
    assert withdrawn["maturity"] == "tombstone"
    assert withdrawn["visibility"] == "restricted"

    revival = {
        **dict(withdrawn),
        "revision": int(withdrawn["revision"]) + 1,
        "previous_revision_sha256": sha256_json(withdrawn),
        "maturity": "stable",
        "created_at": "2026-08-18T22:33:00+08:00",
    }
    with pytest.raises(ContractError, match="cannot be revived"):
        revisions.commit(
            revival,
            expected_ref=self_insight_ref(withdrawn),
            committed_at="2026-08-18T22:33:00+08:00",
        )


def test_stale_self_action_finishes_with_conflict_without_overwrite(tmp_path: Path) -> None:
    revisions, actions, workflow, apply_action = environment(tmp_path)
    fixture = formal_20d_inputs()
    inputs = commit_material(revisions, themes=fixture.themes)
    created = workflow.update(
        self_input(inputs, themes=fixture.themes),
        created_at="2026-08-18T22:10:00+08:00",
    )
    current = revisions.load_head("self_insight", str(created["committed_ref"]["id"]))
    stale = user_action(
        actions,
        self_insight_ref(current),
        operation="scope",
        payload={"scope": "只适用于高风险决策"},
        nonce="stale-scope",
        submitted_at="2026-08-18T22:11:00+08:00",
    )

    concurrent = {
        **dict(current),
        "revision": 2,
        "previous_revision_sha256": sha256_json(current),
        "change_reason": "模拟另一条已先完成的用户修订",
        "created_at": "2026-08-18T22:11:30+08:00",
        "committed_by": "user",
        "committing_action_id": "uact_888888888888888888888888",
    }
    revisions.commit(
        concurrent,
        expected_ref=self_insight_ref(current),
        committed_at="2026-08-18T22:11:30+08:00",
    )
    result = apply_action.apply(
        str(stale["action_id"]),
        processed_at="2026-08-18T22:12:00+08:00",
    )

    assert result["status"] == "conflict"
    assert result["reason_code"] == "target_revision_stale"
    assert result["committed_ref"] is None
    assert revisions.load_head("self_insight", str(current["insight_id"])) == concurrent


def test_sensitive_migrated_insight_remains_restricted_after_user_action(tmp_path: Path) -> None:
    revisions, actions, workflow, apply_action = environment(tmp_path)
    del workflow
    fixture = formal_20d_inputs()
    inputs = commit_material(revisions, themes=fixture.themes)
    migrated = {
        **copy.deepcopy(dict(fixture.self_insights[0])),
        "insight_id": "sin_999999999999999999999999",
        "title": "一条受限的当前理解",
        "statement": "这条迁移理解包含需要始终限制外发的敏感内容",
        "sensitivity": "sensitive",
        "visibility": "restricted",
        "confirmation": "restricted",
        "theme_refs": [theme_ref(theme) for theme in inputs.themes],
        "support_refs": [theme_ref(theme) for theme in inputs.themes[:2]],
        "boundary_refs": [],
        "committed_by": "migration",
    }
    revisions.commit(migrated, committed_at="2026-08-18T22:41:00+08:00")
    action = user_action(
        actions,
        self_insight_ref(migrated),
        operation="confirm",
        payload={},
        nonce="confirm-restricted-self",
        submitted_at="2026-08-18T22:42:00+08:00",
    )

    apply_action.apply(str(action["action_id"]), processed_at="2026-08-18T22:43:00+08:00")
    current = revisions.load_head("self_insight", str(migrated["insight_id"]))

    assert current["confirmation"] == "restricted"
    assert current["visibility"] == "restricted"


def test_self_action_retry_recovers_commit_when_terminal_result_was_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revisions, actions, workflow, apply_action = environment(tmp_path)
    fixture = formal_20d_inputs()
    inputs = commit_material(revisions, themes=fixture.themes)
    created = workflow.update(
        self_input(inputs, themes=fixture.themes),
        created_at="2026-08-18T22:10:00+08:00",
    )
    current = revisions.load_head("self_insight", str(created["committed_ref"]["id"]))
    action = user_action(
        actions,
        self_insight_ref(current),
        operation="scope",
        payload={"scope": "只适用于需要承担失败成本的工作"},
        nonce="recover-after-result-interruption",
        submitted_at="2026-08-18T22:11:00+08:00",
    )
    original_record_result = actions.record_result

    def interrupted_once(result: Mapping[str, Any]) -> Mapping[str, Any]:
        del result
        raise ContractError("simulated terminal result interruption", kind="conflict")

    monkeypatch.setattr(actions, "record_result", interrupted_once)
    with pytest.raises(ContractError, match="terminal result interruption"):
        apply_action.apply(str(action["action_id"]), processed_at="2026-08-18T22:12:00+08:00")
    committed = revisions.load_head("self_insight", str(current["insight_id"]))
    assert committed["revision"] == 2
    assert committed["committing_action_id"] == action["action_id"]
    assert actions.load_result(str(action["action_id"])) is None

    monkeypatch.setattr(actions, "record_result", original_record_result)
    recovered = apply_action.apply(
        str(action["action_id"]),
        processed_at="2026-08-18T22:13:00+08:00",
    )
    assert recovered["status"] == "applied"
    assert recovered["committed_ref"] == self_insight_ref(committed)
    assert revisions.load_head("self_insight", str(current["insight_id"])) == committed
