"""Deterministic R6 policies for daily memory and Theme material gates."""

from __future__ import annotations

import datetime as dt
from typing import Any, Mapping, Sequence

from memento_backend.domain.errors import ContractError


DAILY_POLICY_VERSION = "daily-integration-policy-v1"
DAILY_PROMPT_VERSION = "daily-integrator-v1"
THEME_POLICY_VERSION = "theme-material-policy-v1"
THEME_PROMPT_VERSION = "theme-synthesizer-v1"
DORMANT_AFTER_DAYS = 30


def normalize_topic(value: str) -> str:
    return " ".join(value.strip().lower().split())


def shared_topics(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    left_by_key = {normalize_topic(str(topic)): str(topic) for topic in left["topics"]}
    right_keys = {normalize_topic(str(topic)) for topic in right["topics"]}
    return [left_by_key[key] for key in sorted(set(left_by_key).intersection(right_keys))]


def theme_material_gate(atoms: Sequence[Mapping[str, Any]], relations: Sequence[Mapping[str, Any]]) -> str:
    if len(atoms) < 2:
        return "insufficient_atoms"
    days = {str(atom["last_seen_on"]) for atom in atoms if atom.get("status") == "active"}
    if len(days) < 2:
        return "insufficient_days"
    atom_ids = {str(atom["memory_atom_id"]) for atom in atoms}
    has_connection = any(
        relation.get("status") == "active"
        and relation.get("relation_type") in {"same_topic", "supports", "revises", "counterexample", "scope_boundary"}
        and str(relation["from_ref"]["id"]) in atom_ids
        and str(relation["to_ref"]["id"]) in atom_ids
        for relation in relations
    )
    if not has_connection:
        return "missing_formal_relation"
    return "passed"


def is_dormant(*, last_seen_on: str, as_of: str) -> bool:
    try:
        last = dt.date.fromisoformat(last_seen_on)
        current = dt.date.fromisoformat(as_of)
    except ValueError as exc:
        raise ContractError("theme material dates are invalid") from exc
    return (current - last).days > DORMANT_AFTER_DAYS
