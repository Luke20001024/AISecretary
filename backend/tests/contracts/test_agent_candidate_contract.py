from __future__ import annotations

import pytest

from memento_backend.contracts import ContractValidationError, validate_contract

from .samples import record_interpretation, source_ref


SCHEMA = "agent-action-candidate-v1.schema.json"


def candidate() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "kind": "memento_agent_action_candidate",
        "candidate_id": "cand_111111111111111111111111",
        "run_id": "run_222222222222222222222222",
        "agent_role": "record_interpreter",
        "action": "propose_create",
        "proposed_kind": "record_interpretation",
        "proposed_object": record_interpretation(),
        "source_refs": [source_ref()],
        "source_spans": [],
        "reason_code": "explicit_user_judgment",
        "confidence": "medium",
        "prompt_version": "record-interpreter-v1",
        "policy_version": "interpretation-policy-v1",
        "input_sha256": "c" * 64,
        "user_action_watermark_sha256": "d" * 64,
        "usage": {
            "mode": "deterministic",
            "provider": None,
            "model": None,
            "attempt_status": "not_started",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0,
            "latency_ms": 0,
        },
        "created_at": "2026-08-22T10:01:00+08:00",
    }


def test_agent_candidate_is_nonformal_envelope() -> None:
    validate_contract(SCHEMA, candidate())


def test_no_change_has_no_proposed_object() -> None:
    value = candidate()
    value.update(action="no_change", proposed_kind="none", proposed_object=None)
    validate_contract(SCHEMA, value)
    value["proposed_object"] = {}
    with pytest.raises(ContractValidationError):
        validate_contract(SCHEMA, value)


def test_proposal_requires_object_and_formal_kind() -> None:
    value = candidate()
    value.update(proposed_kind="none", proposed_object=None)
    with pytest.raises(ContractValidationError):
        validate_contract(SCHEMA, value)


def test_candidate_source_ref_kind_and_id_prefix_must_match() -> None:
    value = candidate()
    value["source_refs"] = [{
        "kind": "theme",
        "id": "rec_111111111111111111111111",
        "revision": 1,
        "revision_sha256": "a" * 64,
    }]
    with pytest.raises(ContractValidationError):
        validate_contract(SCHEMA, value)
