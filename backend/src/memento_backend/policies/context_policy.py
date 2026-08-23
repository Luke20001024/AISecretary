"""Authorization predicates shared by Context read and writeback workflows."""

from __future__ import annotations

import datetime as dt
from typing import Any, Mapping, Optional, Sequence

from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import validate_datetime


CONTEXT_POLICY_VERSION = "context-policy-v1"
SENSITIVITY_RANK = {"normal": 0, "sensitive": 1, "restricted": 2}


def parse_timestamp(value: str) -> dt.datetime:
    validate_datetime(value)
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def assert_time_scope(scope: Optional[Mapping[str, str]], *, name: str) -> None:
    if scope is None:
        return
    if not isinstance(scope, Mapping) or set(scope) != {"from", "to"}:
        raise ContractError(f"{name} must contain exact from/to dates", kind="authorization")
    try:
        start = dt.date.fromisoformat(scope["from"])
        end = dt.date.fromisoformat(scope["to"])
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{name} must contain ISO dates", kind="authorization") from exc
    if start > end:
        raise ContractError(f"{name} starts after it ends", kind="authorization")


def normalize_topics(topics: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(topic.strip().casefold() for topic in topics if topic.strip()))
    if not normalized:
        raise ContractError("topic scope cannot be empty", kind="authorization")
    return normalized


def topics_within(requested: Sequence[str], allowed: Sequence[str]) -> bool:
    allowed_normalized = normalize_topics(allowed)
    if "*" in allowed_normalized:
        return True
    return set(normalize_topics(requested)).issubset(set(allowed_normalized))


def time_scope_within(
    requested: Optional[Mapping[str, str]],
    allowed: Optional[Mapping[str, str]],
) -> bool:
    assert_time_scope(requested, name="requested time scope")
    assert_time_scope(allowed, name="grant time scope")
    if allowed is None:
        return True
    if requested is None:
        return False
    return str(allowed["from"]) <= str(requested["from"]) and str(requested["to"]) <= str(allowed["to"])


def assert_grant_allows(
    grant: Mapping[str, Any],
    *,
    client_id: str,
    topics: Sequence[str],
    time_scope: Optional[Mapping[str, str]],
    requested_at: str,
) -> None:
    if grant["status"] != "active":
        raise ContractError("context grant is not active", kind="authorization")
    if grant["client_id"] != client_id:
        raise ContractError("context grant belongs to another client", kind="authorization")
    expires_at = grant["expires_at"]
    if expires_at is not None and parse_timestamp(requested_at) >= parse_timestamp(str(expires_at)):
        raise ContractError("context grant has expired", kind="authorization")
    if not topics_within(topics, grant["topic_scope"]):
        raise ContractError("requested topics exceed the context grant", kind="authorization")
    if not time_scope_within(time_scope, grant["time_scope"]):
        raise ContractError("requested time scope exceeds the context grant", kind="authorization")


def assert_session_allows(
    session: Mapping[str, Any],
    grant_ref: Mapping[str, Any],
    *,
    client_id: str,
    task: str,
    topics: Sequence[str],
    time_scope: Optional[Mapping[str, str]],
) -> None:
    if session["status"] != "active":
        raise ContractError("external session is not active", kind="authorization")
    if session["client_id"] != client_id or session["grant_ref"] != grant_ref:
        raise ContractError("external session authority is stale", kind="authorization")
    if session["task"] != task:
        raise ContractError("requested task does not match the external session", kind="authorization")
    if not topics_within(topics, session["topic_scope"]):
        raise ContractError("requested topics exceed the external session", kind="authorization")
    if not time_scope_within(time_scope, session["time_scope"]):
        raise ContractError("requested time scope exceeds the external session", kind="authorization")


def sensitivity_allowed(value: str, maximum: str) -> bool:
    return SENSITIVITY_RANK[value] <= SENSITIVITY_RANK[maximum]


def assert_operation_window(
    *,
    requested_at: str,
    completed_at: str,
    not_before: str,
    expires_at: Optional[str] = None,
    authority_name: str,
) -> None:
    """Reject caller-supplied time travel and operations crossing expiry."""
    requested = parse_timestamp(requested_at)
    completed = parse_timestamp(completed_at)
    lower_bound = parse_timestamp(not_before)
    if requested > completed:
        raise ContractError("operation completed before it was requested", kind="authorization")
    if requested < lower_bound:
        raise ContractError(f"operation predates {authority_name}", kind="authorization")
    if expires_at is not None and completed >= parse_timestamp(expires_at):
        raise ContractError(f"{authority_name} expired before operation completion", kind="authorization")
