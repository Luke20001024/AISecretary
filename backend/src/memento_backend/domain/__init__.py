"""Stable domain primitives shared by every backend layer."""

from .errors import ContractError
from .ids import canonical_json, make_id, sha256_bytes, sha256_json
from .refs import ObjectRef, SourceSpan

__all__ = [
    "ContractError",
    "ObjectRef",
    "SourceSpan",
    "canonical_json",
    "make_id",
    "sha256_bytes",
    "sha256_json",
]
