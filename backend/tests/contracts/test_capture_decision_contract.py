from __future__ import annotations

import pytest

from memento_backend.contracts import ContractValidationError, validate_contract

from .samples import capture_decision


SCHEMA = "capture-decision-v1.schema.json"


def test_link_read_later_routes_to_ask_on_use() -> None:
    validate_contract(SCHEMA, capture_decision())


def test_read_later_cannot_route_directly_to_interpretation() -> None:
    value = capture_decision()
    value["processing_route"] = "interpret"
    with pytest.raises(ContractValidationError):
        validate_contract(SCHEMA, value)


def test_resource_without_signal_cannot_claim_personal_interpretation() -> None:
    value = capture_decision()
    value.update(
        content_role="resource",
        processing_route="resource_index",
        reason_code="resource_without_user_signal",
        resource_scope="whole_resource",
    )
    validate_contract(SCHEMA, value)
    value["processing_route"] = "interpret"
    with pytest.raises(ContractValidationError):
        validate_contract(SCHEMA, value)


def test_capture_decision_rejects_projection_fields() -> None:
    value = capture_decision()
    value["terrain_peak"] = True
    with pytest.raises(ContractValidationError, match="Additional properties"):
        validate_contract(SCHEMA, value)
