"""Shared append-only revision invariants."""

from __future__ import annotations

from typing import Any, Mapping

from .errors import ContractError
from .ids import validate_datetime, validate_sha256


def validate_revision_metadata(value: Mapping[str, Any]) -> None:
    revision = value.get("revision")
    previous = value.get("previous_revision_sha256")
    if type(revision) is not int or revision < 1:
        raise ContractError("revision must be a positive integer")
    if revision == 1:
        if previous is not None:
            raise ContractError("revision 1 requires a null previous_revision_sha256")
    else:
        validate_sha256(previous, "previous_revision_sha256")
    validate_datetime(value.get("created_at"), "created_at")
    if value.get("committed_by") not in {"workflow", "user", "migration"}:
        raise ContractError("committed_by is invalid")


def validate_append_only_transition(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    id_field: str,
    previous_sha256: str,
) -> None:
    if previous.get(id_field) != current.get(id_field):
        raise ContractError("revision identity changed", kind="conflict")
    previous_revision = previous.get("revision")
    if type(previous_revision) is not int or current.get("revision") != previous_revision + 1:
        raise ContractError("revision sequence is invalid", kind="conflict")
    if current.get("previous_revision_sha256") != previous_sha256:
        raise ContractError("previous revision hash is stale", kind="conflict")
    if any(previous.get(field) == "tombstone" for field in ("status", "lifecycle", "maturity")):
        raise ContractError("tombstoned objects cannot be revived", kind="conflict")
