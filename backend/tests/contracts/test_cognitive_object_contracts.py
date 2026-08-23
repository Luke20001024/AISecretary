from __future__ import annotations

import pytest

from memento_backend.contracts import ContractValidationError, validate_contract

from .samples import (
    THEME_A_ID,
    record_interpretation,
    memory_atom,
    ref,
    relation,
    self_insight,
    theme,
)


def test_record_interpretation_requires_exact_user_signal() -> None:
    validate_contract("record-interpretation-v2.schema.json", record_interpretation())
    value = record_interpretation()
    value["source_spans"] = []
    with pytest.raises(ContractValidationError):
        validate_contract("record-interpretation-v2.schema.json", value)


def test_original_only_contains_no_model_interpretation() -> None:
    value = record_interpretation()
    value.update(
        status="original_only",
        operation="original_only",
        summary=None,
        content_types=[],
        topics=[],
        purposes=[],
        source_spans=[],
    )
    validate_contract("record-interpretation-v2.schema.json", value)
    value["summary"] = "model text leaked into original-only state"
    with pytest.raises(ContractValidationError):
        validate_contract("record-interpretation-v2.schema.json", value)


def test_memory_atom_requires_interpretation_and_source_span() -> None:
    validate_contract("memory-atom-v2.schema.json", memory_atom())
    value = memory_atom()
    value["evidence_refs"] = []
    with pytest.raises(ContractValidationError):
        validate_contract("memory-atom-v2.schema.json", value)


def test_same_topic_relation_is_undirected() -> None:
    validate_contract("relation-v2.schema.json", relation())
    value = relation()
    value["direction"] = "directed"
    with pytest.raises(ContractValidationError):
        validate_contract("relation-v2.schema.json", value)


def test_theme_requires_two_memories_across_two_dates() -> None:
    validate_contract("theme-v2.schema.json", theme())
    value = theme()
    value["evidence_refs"] = value["evidence_refs"][:1]
    with pytest.raises(ContractValidationError):
        validate_contract("theme-v2.schema.json", value)
    value = theme()
    value["evidence_days"] = ["2026-08-22"]
    with pytest.raises(ContractValidationError):
        validate_contract("theme-v2.schema.json", value)


def test_self_insight_requires_multiple_themes() -> None:
    validate_contract("self-insight-v2.schema.json", self_insight())
    value = self_insight()
    value["theme_refs"] = [ref("theme", THEME_A_ID)]
    with pytest.raises(ContractValidationError):
        validate_contract("self-insight-v2.schema.json", value)


def test_sensitive_self_insight_cannot_be_grant_visible() -> None:
    value = self_insight()
    value.update(sensitivity="sensitive", visibility="grant_only")
    with pytest.raises(ContractValidationError):
        validate_contract("self-insight-v2.schema.json", value)


def test_user_committed_self_insight_requires_action_binding() -> None:
    value = self_insight()
    value.update(committed_by="user", confirmation="user_confirmed")
    with pytest.raises(ContractValidationError):
        validate_contract("self-insight-v2.schema.json", value)
    value["committing_action_id"] = "uact_111111111111111111111111"
    validate_contract("self-insight-v2.schema.json", value)


def test_workflow_self_insight_cannot_claim_a_user_action() -> None:
    value = self_insight()
    value["committing_action_id"] = "uact_111111111111111111111111"
    with pytest.raises(ContractValidationError):
        validate_contract("self-insight-v2.schema.json", value)


def test_formal_objects_reject_candidate_metadata() -> None:
    value = theme()
    value["candidate_id"] = "cand_111111111111111111111111"
    with pytest.raises(ContractValidationError, match="Additional properties"):
        validate_contract("theme-v2.schema.json", value)
