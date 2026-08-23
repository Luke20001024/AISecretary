"""Validate data against versioned local JSON Schemas."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError


SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "schemas"


class ContractValidationError(ValueError):
    """A stable wrapper around jsonschema implementation details."""


@lru_cache(maxsize=64)
def load_schema(name: str) -> Mapping[str, Any]:
    if not name.endswith(".schema.json") or "/" in name or "\\" in name:
        raise ContractValidationError("schema name is invalid")
    path = SCHEMA_ROOT / name
    if not path.is_file():
        raise ContractValidationError(f"schema does not exist: {name}")
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ContractValidationError(f"schema root must be an object: {name}")
    Draft202012Validator.check_schema(value)
    return cast(Mapping[str, Any], value)


def validate_contract(name: str, value: Any) -> None:
    validator = Draft202012Validator(load_schema(name), format_checker=FormatChecker())
    try:
        validator.validate(value)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or "<root>"
        raise ContractValidationError(f"{name} at {location}: {exc.message}") from exc
