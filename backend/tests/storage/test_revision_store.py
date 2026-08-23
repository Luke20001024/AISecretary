from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping

import pytest

from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import sha256_json
from memento_backend.storage.atomic import AtomicFileStore
from memento_backend.storage.head_index import HEAD_INDEX_PATH
from memento_backend.storage.revision_store import RevisionStore
from tests.contracts.samples import (
    capture_decision,
    memory_atom,
    read_later_intent,
    record_interpretation,
    relation,
    resource_card,
    self_insight,
    source_record,
    theme,
)
from tests.fixtures.formal_20d import formal_20d_inputs


def make_store(tmp_path: Path, *, fault_stage: str | None = None) -> RevisionStore:
    root = tmp_path / "isolated-v2"
    if not root.exists():
        root.mkdir(mode=0o700)

    def fault(stage: str, identifier: str) -> None:
        del identifier
        if stage == fault_stage:
            raise RuntimeError(f"interrupted at {stage}")

    return RevisionStore(AtomicFileStore(root), fault_hook=fault if fault_stage else None)


def record_revision_two(record: Mapping[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = copy.deepcopy(dict(record))
    value["revision"] = 2
    value["previous_revision_sha256"] = sha256_json(record)
    value["operation"] = "source_edit"
    value["created_at"] = "2026-08-19T09:00:00+08:00"
    return value


def test_multi_object_commit_has_one_visibility_boundary_and_exact_cas(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    inputs = formal_20d_inputs()
    refs = store.commit_many(
        [inputs.source_records[0], inputs.resource_cards[0]],
        committed_at="2026-08-18T22:00:00+08:00",
    )
    assert len(refs) == 2
    assert store.load_index()["generation"] == 1
    assert store.load_head("source_record", str(inputs.source_records[0]["record_id"])) == inputs.source_records[0]

    revised = record_revision_two(inputs.source_records[0])
    next_ref = store.commit(revised, expected_ref=refs[0])
    assert next_ref["revision"] == 2
    with pytest.raises(ContractError) as stale:
        store.commit(revised, expected_ref=refs[0])
    assert stale.value.kind == "conflict"


def test_every_frozen_formal_object_kind_can_share_one_transaction(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    values = [
        source_record(), capture_decision(), resource_card(), read_later_intent(),
        record_interpretation(), memory_atom(), relation(), theme(), self_insight(),
    ]
    refs = store.commit_many(values, committed_at="2026-08-23T09:00:00+08:00")
    assert len(refs) == 9
    assert {ref["kind"] for ref in refs} == {
        "source_record", "capture_decision", "resource_card", "read_later_intent",
        "record_interpretation", "memory_atom", "relation", "theme", "self_insight",
    }
    assert len(store.list_heads()) == 9


def test_partial_revision_files_remain_invisible_without_transaction(tmp_path: Path) -> None:
    inputs = formal_20d_inputs()
    interrupted = make_store(tmp_path, fault_stage="after_revisions")
    with pytest.raises(RuntimeError, match="after_revisions"):
        interrupted.commit_many(
            [inputs.source_records[0], inputs.resource_cards[0]],
            committed_at="2026-08-18T22:00:00+08:00",
        )

    recovered = make_store(tmp_path).recover()
    assert recovered["generation"] == 0
    assert recovered["heads"] == []


def test_complete_transaction_is_recovered_after_head_publish_interruption(tmp_path: Path) -> None:
    record = formal_20d_inputs().source_records[0]
    interrupted = make_store(tmp_path, fault_stage="after_transaction")
    with pytest.raises(RuntimeError, match="after_transaction"):
        interrupted.commit(record, committed_at="2026-08-18T22:00:00+08:00")

    store = make_store(tmp_path)
    assert store.load_index()["generation"] == 0
    rebuilt = store.recover()
    assert rebuilt["generation"] == 1
    assert store.load_head("source_record", str(record["record_id"])) == record


def test_revision_history_reads_only_the_published_head_chain(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = formal_20d_inputs().source_records[0]
    head = store.commit(record)
    interrupted = make_store(tmp_path, fault_stage="after_revisions")
    with pytest.raises(RuntimeError, match="after_revisions"):
        interrupted.commit(record_revision_two(record), expected_ref=head)

    history = make_store(tmp_path).list_revisions("source_record", str(record["record_id"]))
    assert history == [record]
    assert make_store(tmp_path).load_revision("source_record", str(record["record_id"]), 1) == record
    with pytest.raises(ContractError) as missing:
        make_store(tmp_path).load_revision("source_record", str(record["record_id"]), 2)
    assert missing.value.kind == "not_found"


def test_head_index_is_rebuildable_from_committed_transactions(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = formal_20d_inputs().source_records[0]
    ref = store.commit(record)
    store.commit(record_revision_two(record), expected_ref=ref)
    (store.files.root / HEAD_INDEX_PATH).unlink()

    rebuilt = store.recover()
    assert rebuilt["generation"] == 2
    current = store.current_ref("source_record", str(record["record_id"]))
    assert current is not None
    assert current["revision"] == 2


def test_tombstoned_theme_cannot_be_revived(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    theme: dict[str, Any] = copy.deepcopy(dict(formal_20d_inputs().themes[0]))
    ref1 = store.commit(theme)
    tombstone: dict[str, Any] = copy.deepcopy(theme)
    tombstone["revision"] = 2
    tombstone["previous_revision_sha256"] = ref1["revision_sha256"]
    tombstone["lifecycle"] = "tombstone"
    tombstone["created_at"] = "2026-08-19T09:00:00+08:00"
    ref2 = store.commit(tombstone, expected_ref=ref1)

    revived: dict[str, Any] = copy.deepcopy(tombstone)
    revived["revision"] = 3
    revived["previous_revision_sha256"] = ref2["revision_sha256"]
    revived["lifecycle"] = "active"
    revived["created_at"] = "2026-08-20T09:00:00+08:00"
    with pytest.raises(ContractError, match="cannot be revived"):
        store.commit(revived, expected_ref=ref2)


def test_concurrent_writers_cannot_both_win_the_same_head_cas(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = formal_20d_inputs().source_records[0]
    head = store.commit(record)
    left = record_revision_two(record)
    right = record_revision_two(record)
    right["created_at"] = "2026-08-19T09:01:00+08:00"
    barrier = threading.Barrier(2)

    def attempt(value: Mapping[str, Any]) -> str:
        barrier.wait()
        try:
            make_store(tmp_path).commit(value, expected_ref=head)
        except ContractError as exc:
            return exc.kind
        return "committed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, (left, right)))
    assert sorted(outcomes) == ["committed", "conflict"]
    current = store.current_ref("source_record", str(record["record_id"]))
    assert current is not None
    assert current["revision"] == 2
