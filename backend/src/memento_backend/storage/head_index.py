"""Rebuildable formal-object head index contract."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import validate_datetime, validate_relative_path
from memento_backend.domain.refs import ObjectRef


HEAD_INDEX_PATH = "indexes/formal-head-index.json"


def empty_head_index() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": "memento_formal_head_index",
        "generation": 0,
        "updated_at": "1970-01-01T00:00:00+00:00",
        "last_transaction_sha256": None,
        "heads": [],
    }


def validate_head_index(value: Mapping[str, Any]) -> None:
    validate_contract("formal-head-index-v1.schema.json", value)
    seen: set[tuple[str, str]] = set()
    previous: tuple[str, str] | None = None
    for raw in value["heads"]:
        if not isinstance(raw, Mapping):
            raise ContractError("head index entry must be an object")
        object_kind = str(raw["object_kind"])
        object_id = str(raw["object_id"])
        key = (object_kind, object_id)
        if key in seen or (previous is not None and key <= previous):
            raise ContractError("head index entries must be unique and sorted")
        ref = ObjectRef.from_dict(raw["ref"])
        if ref.kind != object_kind or ref.id != object_id:
            raise ContractError("head index identity differs from its reference")
        validate_relative_path(raw["path"], "head.path")
        seen.add(key)
        previous = key
    validate_datetime(value["updated_at"], "head.updated_at")


def build_head_index(
    heads: Iterable[Mapping[str, Any]],
    *,
    generation: int,
    updated_at: str,
    last_transaction_sha256: str | None,
) -> dict[str, Any]:
    value = {
        "schema_version": "1.0",
        "kind": "memento_formal_head_index",
        "generation": generation,
        "updated_at": updated_at,
        "last_transaction_sha256": last_transaction_sha256,
        "heads": sorted((dict(item) for item in heads), key=lambda item: (item["object_kind"], item["object_id"])),
    }
    validate_head_index(value)
    return value


def heads_by_key(index: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    validate_head_index(index)
    return {
        (str(item["object_kind"]), str(item["object_id"])): item
        for item in index["heads"]
    }
