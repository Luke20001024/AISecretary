from __future__ import annotations

import pytest

from memento_backend.contracts import ContractValidationError, validate_contract

from .samples import resource_card


SCHEMA = "resource-card-v1.schema.json"


def test_resource_card_keeps_retrieval_information() -> None:
    validate_contract(SCHEMA, resource_card())


def test_resource_card_has_no_personal_belief_field() -> None:
    value = resource_card()
    value["user_believes"] = "article claim"
    with pytest.raises(ContractValidationError, match="Additional properties"):
        validate_contract(SCHEMA, value)


def test_resource_card_rejects_invalid_remote_location() -> None:
    value = resource_card()
    value["url"] = "not a url"
    with pytest.raises(ContractValidationError):
        validate_contract(SCHEMA, value)
