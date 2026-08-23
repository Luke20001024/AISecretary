from __future__ import annotations

import copy

import pytest

from memento_backend.contracts import ContractValidationError, validate_contract

from .samples import SHA_A, source_record


SCHEMA = "source-record-v2.schema.json"


def test_source_record_contract_accepts_exact_input() -> None:
    validate_contract(SCHEMA, source_record())


def test_source_record_contract_rejects_unknown_fields() -> None:
    value = source_record()
    value["model_summary"] = "a source record cannot contain model interpretation"
    with pytest.raises(ContractValidationError, match="Additional properties"):
        validate_contract(SCHEMA, value)


def test_first_revision_requires_ingest_and_null_previous_hash() -> None:
    value = source_record()
    value["previous_revision_sha256"] = SHA_A
    with pytest.raises(ContractValidationError):
        validate_contract(SCHEMA, value)

    value = source_record()
    value["operation"] = "source_edit"
    with pytest.raises(ContractValidationError):
        validate_contract(SCHEMA, value)


def test_tombstone_and_user_delete_are_bound() -> None:
    value = source_record()
    value.update(
        revision=2,
        previous_revision_sha256=SHA_A,
        status="tombstone",
        operation="source_edit",
    )
    with pytest.raises(ContractValidationError):
        validate_contract(SCHEMA, value)

    valid = copy.deepcopy(value)
    valid["operation"] = "user_delete"
    validate_contract(SCHEMA, valid)
