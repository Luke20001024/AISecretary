from __future__ import annotations

import pytest

from memento_backend.contracts import ContractValidationError, validate_contract

from .samples import read_later_intent


SCHEMA = "read-later-intent-v1.schema.json"


def test_read_later_intent_is_a_lightweight_open_state() -> None:
    validate_contract(SCHEMA, read_later_intent())


def test_read_later_intent_cannot_contain_theme_evidence() -> None:
    value = read_later_intent()
    value["theme_refs"] = []
    with pytest.raises(ContractValidationError, match="Additional properties"):
        validate_contract(SCHEMA, value)


def test_first_revision_cannot_start_completed() -> None:
    value = read_later_intent()
    value.update(status="completed", operation="complete")
    with pytest.raises(ContractValidationError):
        validate_contract(SCHEMA, value)
