from __future__ import annotations

import pytest

from memento_backend.domain import ContractError, ObjectRef, SourceSpan, make_id, sha256_bytes
from memento_backend.domain.revisions import (
    validate_append_only_transition,
    validate_revision_metadata,
)

from .samples import RECORD_ID, SHA_A, source_ref


def test_object_ref_is_exact_and_round_trips() -> None:
    value = source_ref()
    assert ObjectRef.from_dict(value).to_dict() == value


def test_object_ref_rejects_unknown_fields() -> None:
    value = source_ref()
    value["title"] = "leaked projection field"
    with pytest.raises(ContractError):
        ObjectRef.from_dict(value)


def test_source_span_binds_exact_quote_hash() -> None:
    quote = "这个链接待会再看"
    value = {
        "record_id": RECORD_ID,
        "record_revision": 1,
        "record_revision_sha256": SHA_A,
        "source_file": "2026-08-22.md",
        "line_start": 4,
        "line_end": 4,
        "quote": quote,
        "quote_sha256": sha256_bytes(quote.encode("utf-8")),
    }
    assert SourceSpan.from_dict(value).to_dict() == value
    value["quote"] = "被改写的文字"
    with pytest.raises(ContractError, match="quote_sha256"):
        SourceSpan.from_dict(value)


def test_revision_metadata_requires_append_only_chain() -> None:
    validate_revision_metadata(
        {
            "revision": 1,
            "previous_revision_sha256": None,
            "created_at": "2026-08-22T10:00:00+08:00",
            "committed_by": "workflow",
        }
    )
    with pytest.raises(ContractError):
        validate_revision_metadata(
            {
                "revision": 1,
                "previous_revision_sha256": SHA_A,
                "created_at": "2026-08-22T10:00:00+08:00",
                "committed_by": "workflow",
            }
        )


def test_transition_rejects_stale_hash_and_tombstone_revival() -> None:
    previous = {"record_id": RECORD_ID, "revision": 1, "status": "active"}
    current = {
        "record_id": RECORD_ID,
        "revision": 2,
        "status": "active",
        "previous_revision_sha256": SHA_A,
    }
    validate_append_only_transition(
        previous,
        current,
        id_field="record_id",
        previous_sha256=SHA_A,
    )
    current["previous_revision_sha256"] = "b" * 64
    with pytest.raises(ContractError, match="stale"):
        validate_append_only_transition(
            previous,
            current,
            id_field="record_id",
            previous_sha256=SHA_A,
        )

    previous_theme = {"theme_id": "thm_" + "1" * 24, "revision": 1, "lifecycle": "tombstone"}
    current_theme = {
        "theme_id": previous_theme["theme_id"],
        "revision": 2,
        "lifecycle": "active",
        "previous_revision_sha256": SHA_A,
    }
    with pytest.raises(ContractError, match="cannot be revived"):
        validate_append_only_transition(
            previous_theme,
            current_theme,
            id_field="theme_id",
            previous_sha256=SHA_A,
        )


def test_stable_id_generation_uses_kind_prefix() -> None:
    value = make_id("source_record", "capture-v2", {"nonce": "one"})
    assert value.startswith("rec_")
    assert len(value) == 28
