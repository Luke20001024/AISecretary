from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from memento_backend.agents.daily_integrator import DailyIntegrationInput, DailyIntegrator, memory_atom_ref
from memento_backend.agents.theme_synthesizer import ThemeSynthesisInput, ThemeSynthesizer
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_bytes, sha256_json
from memento_backend.storage.action_inbox import ActionInbox
from memento_backend.storage.atomic import AtomicFileStore
from memento_backend.storage.revision_store import RevisionStore
from memento_backend.storage.run_ledger import RunLedger
from memento_backend.workflows.consolidate_day import ConsolidateDayWorkflow
from memento_backend.workflows.update_theme import UpdateThemeWorkflow
from tests.fixtures.formal_20d import formal_20d_inputs
import pytest


def environment(tmp_path: Path) -> tuple[RevisionStore, ConsolidateDayWorkflow, UpdateThemeWorkflow]:
    root = tmp_path / "isolated-v2"
    root.mkdir(mode=0o700)
    files = AtomicFileStore(root)
    revisions = RevisionStore(files)
    actions = ActionInbox(files)
    ledger = RunLedger(files)
    return (
        revisions,
        ConsolidateDayWorkflow(revisions, actions, DailyIntegrator(), ledger),
        UpdateThemeWorkflow(revisions, actions, ThemeSynthesizer(), ledger),
    )


def ingest_day(
    revisions: RevisionStore,
    workflow: ConsolidateDayWorkflow,
    record: Mapping[str, Any],
    interpretation: Mapping[str, Any],
    *,
    created_at: str,
) -> Mapping[str, Any]:
    revisions.commit_many([record, interpretation], committed_at=created_at)
    return workflow.consolidate(
        DailyIntegrationInput(
            local_date=str(record["local_date"]),
            source_records=(record,),
            interpretations=(interpretation,),
            existing_atoms=tuple(revisions.list_heads("memory_atom")),
            existing_relations=tuple(revisions.list_heads("relation")),
        ),
        created_at=created_at,
    )


def ref(kind: str, value: Mapping[str, Any], id_field: str) -> dict[str, Any]:
    return {"kind": kind, "id": value[id_field], "revision": value["revision"], "revision_sha256": sha256_json(value)}


def retopic(interpretation: Mapping[str, Any]) -> dict[str, Any]:
    return {**copy.deepcopy(interpretation), "topics": ["产品方法"]}


def test_daily_memory_requires_cross_day_relation_before_theme_creation(tmp_path: Path) -> None:
    revisions, daily, themes = environment(tmp_path)
    fixture = formal_20d_inputs()
    first_record = fixture.source_records[0]
    first_interpretation = retopic(fixture.interpretations[0])
    first = ingest_day(revisions, daily, first_record, first_interpretation, created_at="2026-07-30T22:00:00+08:00")
    assert [ref["kind"] for ref in first["committed_refs"]] == ["memory_atom"]
    one_atom = tuple(revisions.list_heads("memory_atom"))
    no_theme = themes.update(
        ThemeSynthesisInput("产品方法", "2026-07-30", one_atom, ()),
        created_at="2026-07-30T22:10:00+08:00",
    )
    assert no_theme["action"] == "no_change"
    assert no_theme["reason_code"] == "insufficient_atoms"
    assert revisions.list_heads("theme") == []

    second_record = fixture.source_records[1]
    second_interpretation = retopic(fixture.interpretations[1])
    second = ingest_day(revisions, daily, second_record, second_interpretation, created_at="2026-08-03T22:00:00+08:00")
    assert {ref["kind"] for ref in second["committed_refs"]} == {"memory_atom", "relation"}
    result = themes.update(
        ThemeSynthesisInput(
            "产品方法", "2026-08-03",
            tuple(revisions.list_heads("memory_atom")),
            tuple(revisions.list_heads("relation")),
        ),
        created_at="2026-08-03T22:10:00+08:00",
    )
    theme = revisions.load_head("theme", str(result["committed_ref"]["id"]))
    assert theme["revision"] == 1
    assert theme["lifecycle"] == "active"
    assert theme["evidence_days"] == ["2026-07-30", "2026-08-03"]


def test_daily_integrator_reinforces_exact_memory_without_losing_prior_evidence(tmp_path: Path) -> None:
    revisions, daily, themes = environment(tmp_path)
    del themes
    fixture = formal_20d_inputs()
    first_record = fixture.source_records[0]
    first_interpretation = retopic(fixture.interpretations[0])
    ingest_day(revisions, daily, first_record, first_interpretation, created_at="2026-07-30T22:00:00+08:00")
    original = revisions.list_heads("memory_atom")[0]

    next_record, next_interpretation = new_day_pair(fixture.source_records[3], fixture.interpretations[3])
    next_interpretation["summary"] = first_interpretation["summary"]
    next_interpretation["source_spans"][0]["quote"] = str(first_interpretation["summary"])
    next_interpretation["source_spans"][0]["quote_sha256"] = sha256_bytes(
        str(first_interpretation["summary"]).encode("utf-8")
    )
    result = ingest_day(revisions, daily, next_record, next_interpretation, created_at="2026-10-01T22:05:00+08:00")
    revised = revisions.load_head("memory_atom", str(original["memory_atom_id"]))

    assert [ref["kind"] for ref in result["committed_refs"]] == ["memory_atom"]
    assert revised["revision"] == 2
    assert revised["operation"] == "reinforce"
    assert revised["first_seen_on"] == "2026-07-30"
    assert revised["last_seen_on"] == "2026-10-01"
    assert len(revised["evidence_refs"]) == 2
    assert len(revised["source_spans"]) == 2


def test_theme_reinforce_dormant_recover_tension_and_no_change_are_traceable(tmp_path: Path) -> None:
    revisions, daily, themes = environment(tmp_path)
    fixture = formal_20d_inputs()
    for index in range(2):
        ingest_day(
            revisions, daily, fixture.source_records[index], retopic(fixture.interpretations[index]),
            created_at=f"{fixture.source_records[index]['local_date']}T22:00:00+08:00",
        )
    created = themes.update(
        ThemeSynthesisInput("产品方法", "2026-08-03", tuple(revisions.list_heads("memory_atom")), tuple(revisions.list_heads("relation"))),
        created_at="2026-08-03T22:10:00+08:00",
    )
    current = revisions.load_head("theme", str(created["committed_ref"]["id"]))

    ingest_day(
        revisions, daily, fixture.source_records[2], retopic(fixture.interpretations[2]),
        created_at="2026-08-09T22:00:00+08:00",
    )
    reinforced = themes.update(
        ThemeSynthesisInput("产品方法", "2026-08-09", tuple(revisions.list_heads("memory_atom")), tuple(revisions.list_heads("relation")), current),
        created_at="2026-08-09T22:10:00+08:00",
    )
    current = revisions.load_head("theme", str(reinforced["committed_ref"]["id"]))
    assert current["revision"] == 2
    assert current["confidence"] == "stable"

    atoms = tuple(revisions.list_heads("memory_atom"))
    boundary = semantic_relation("scope_boundary", atoms[-1], atoms[0], "只适用于高风险和高返工成本的工作")
    revisions.commit(boundary)
    narrowed = themes.update(
        ThemeSynthesisInput("产品方法", "2026-08-09", atoms, tuple(revisions.list_heads("relation")), current),
        created_at="2026-08-09T22:10:30+08:00",
    )
    current = revisions.load_head("theme", str(narrowed["committed_ref"]["id"]))
    assert "新增边界" in current["scope"]
    assert current["change_reason"] == "新的范围边界收窄了主题适用范围"

    revises = semantic_relation("revises", atoms[-1], atoms[0], "新证据改写了这一主题的当前表述")
    revisions.commit(revises)
    revised = themes.update(
        ThemeSynthesisInput("产品方法", "2026-08-09", atoms, tuple(revisions.list_heads("relation")), current),
        created_at="2026-08-09T22:10:45+08:00",
    )
    current = revisions.load_head("theme", str(revised["committed_ref"]["id"]))
    assert current["statement"].startswith("围绕产品方法，当前理解更新为")
    assert current["change_reason"] == "新的修订关系改变了主题当前表述"

    no_change = themes.update(
        ThemeSynthesisInput("产品方法", "2026-08-09", tuple(revisions.list_heads("memory_atom")), tuple(revisions.list_heads("relation")), current),
        created_at="2026-08-09T22:11:00+08:00",
    )
    assert no_change["action"] == "no_change"

    dormant = themes.update(
        ThemeSynthesisInput("产品方法", "2026-10-01", tuple(revisions.list_heads("memory_atom")), tuple(revisions.list_heads("relation")), current),
        created_at="2026-10-01T22:00:00+08:00",
    )
    current = revisions.load_head("theme", str(dormant["committed_ref"]["id"]))
    assert current["lifecycle"] == "dormant"

    new_record, new_interpretation = new_day_pair(fixture.source_records[3], fixture.interpretations[3])
    ingest_day(revisions, daily, new_record, new_interpretation, created_at="2026-10-01T22:05:00+08:00")
    recovered = themes.update(
        ThemeSynthesisInput("产品方法", "2026-10-01", tuple(revisions.list_heads("memory_atom")), tuple(revisions.list_heads("relation")), current),
        created_at="2026-10-01T22:10:00+08:00",
    )
    current = revisions.load_head("theme", str(recovered["committed_ref"]["id"]))
    assert current["lifecycle"] == "active"
    assert current["change_reason"] == "新的跨日证据让休眠主题重新活跃"

    atoms = tuple(revisions.list_heads("memory_atom"))
    counter = counter_relation(atoms[-1], atoms[0])
    revisions.commit(counter)
    tension = themes.update(
        ThemeSynthesisInput("产品方法", "2026-10-01", atoms, tuple(revisions.list_heads("relation")), current),
        created_at="2026-10-01T22:20:00+08:00",
    )
    current = revisions.load_head("theme", str(tension["committed_ref"]["id"]))
    assert current["lifecycle"] == "tension"
    assert current["counterevidence_refs"]
    assert current["change_reason"] == "新反例与既有支持证据同时存在"


def test_theme_input_rejects_future_or_incomplete_evidence_windows(tmp_path: Path) -> None:
    revisions, daily, themes = environment(tmp_path)
    fixture = formal_20d_inputs()
    for index in range(2):
        ingest_day(
            revisions, daily, fixture.source_records[index], retopic(fixture.interpretations[index]),
            created_at=f"{fixture.source_records[index]['local_date']}T22:00:00+08:00",
        )
    atoms = tuple(revisions.list_heads("memory_atom"))
    relations = tuple(revisions.list_heads("relation"))
    with pytest.raises(ContractError, match="future memory"):
        themes.update(
            ThemeSynthesisInput("产品方法", "2026-07-30", atoms, relations),
            created_at="2026-08-03T22:10:00+08:00",
        )

    created = themes.update(
        ThemeSynthesisInput("产品方法", "2026-08-03", atoms, relations),
        created_at="2026-08-03T22:10:00+08:00",
    )
    theme = revisions.load_head("theme", str(created["committed_ref"]["id"]))
    with pytest.raises(ContractError, match="evidence is missing"):
        ThemeSynthesisInput("产品方法", "2026-08-03", atoms[:1], relations, theme).validate()


def new_day_pair(record_raw: Mapping[str, Any], interpretation_raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    record: dict[str, Any] = copy.deepcopy(dict(record_raw))
    record.update({
        "record_id": "rec_999999999999999999999999", "created_at": "2026-10-01T09:00:01+08:00",
        "captured_at": "2026-10-01T09:00:00+08:00", "local_date": "2026-10-01",
        "source_file": "daily/2026-10-01.md", "entry_sha256": "9" * 64, "source_snapshot_sha256": "8" * 64,
    })
    quote = "新工作再次要求先确认边界再推进"
    interpretation: dict[str, Any] = copy.deepcopy(dict(interpretation_raw))
    interpretation.update({
        "interpretation_id": "int_999999999999999999999999",
        "source_record_ref": ref("source_record", record, "record_id"),
        "summary": quote, "topics": ["产品方法"], "created_at": "2026-10-01T09:10:00+08:00",
        "source_spans": [{
            "record_id": record["record_id"], "record_revision": 1, "record_revision_sha256": sha256_json(record),
            "source_file": record["source_file"], "line_start": record["line_start"], "line_end": record["line_start"],
            "quote": quote, "quote_sha256": sha256_bytes(quote.encode("utf-8")),
        }],
    })
    return record, interpretation


def counter_relation(counter_atom: Mapping[str, Any], supported_atom: Mapping[str, Any]) -> dict[str, Any]:
    return semantic_relation("counterexample", counter_atom, supported_atom, "这条近期记录表明该倾向存在适用边界")


def semantic_relation(
    relation_type: str,
    from_atom: Mapping[str, Any],
    to_atom: Mapping[str, Any],
    statement: str,
) -> dict[str, Any]:
    from_ref = memory_atom_ref(from_atom)
    to_ref = memory_atom_ref(to_atom)
    base = {"from_ref": from_ref, "to_ref": to_ref, "type": relation_type}
    return {
        "schema_version": "2.0", "kind": "memento_relation_revision",
        "relation_id": make_id("relation", f"{relation_type}-v1", base),
        "revision": 1, "previous_revision_sha256": None, "status": "active", "operation": "materialize",
        "relation_type": relation_type, "direction": "directed", "from_ref": from_ref, "to_ref": to_ref,
        "statement": statement, "confidence": "high",
        "evidence_refs": [from_ref, to_ref], "change_reason": "新增语义关系进入主题证据链",
        "policy_version": "theme-material-policy-v1", "created_at": "2026-10-01T22:15:00+08:00", "committed_by": "workflow",
    }
