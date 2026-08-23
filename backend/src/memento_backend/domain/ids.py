"""Deterministic identifiers, hashes and scalar contract validators."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any, Mapping

from .errors import ContractError


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ID_PATTERNS = {
    "source_record": re.compile(r"^rec_[0-9a-f]{24}$"),
    "capture_decision": re.compile(r"^cap_[0-9a-f]{24}$"),
    "resource_card": re.compile(r"^res_[0-9a-f]{24}$"),
    "read_later_intent": re.compile(r"^rli_[0-9a-f]{24}$"),
    "agent_candidate": re.compile(r"^cand_[0-9a-f]{24}$"),
    "record_interpretation": re.compile(r"^int_[0-9a-f]{24}$"),
    "memory_atom": re.compile(r"^mat_[0-9a-f]{24}$"),
    "relation": re.compile(r"^rel_[0-9a-f]{24}$"),
    "theme": re.compile(r"^thm_[0-9a-f]{24}$"),
    "self_insight": re.compile(r"^sin_[0-9a-f]{24}$"),
    "projection_bundle": re.compile(r"^prjb_[0-9a-f]{24}$"),
    "home_projection": re.compile(r"^home_[0-9a-f]{24}$"),
    "timeline_projection": re.compile(r"^tln_[0-9a-f]{24}$"),
    "landscape_projection": re.compile(r"^lnd_[0-9a-f]{24}$"),
    "self_projection": re.compile(r"^self_[0-9a-f]{24}$"),
    "detail_index_projection": re.compile(r"^dix_[0-9a-f]{24}$"),
    "record_detail_projection": re.compile(r"^rdt_[0-9a-f]{24}$"),
    "resource_detail_projection": re.compile(r"^rsd_[0-9a-f]{24}$"),
    "theme_detail_projection": re.compile(r"^tdt_[0-9a-f]{24}$"),
    "self_insight_detail_projection": re.compile(r"^sdt_[0-9a-f]{24}$"),
    "revision_transaction": re.compile(r"^rtx_[0-9a-f]{24}$"),
    "user_action": re.compile(r"^uact_[0-9a-f]{24}$"),
    "action_result": re.compile(r"^ares_[0-9a-f]{24}$"),
    "projection_publication": re.compile(r"^pub_[0-9a-f]{24}$"),
    "agent_run": re.compile(r"^run_[0-9a-f]{24}$"),
    "run_request": re.compile(r"^rrq_[0-9a-f]{24}$"),
    "run_result": re.compile(r"^rrs_[0-9a-f]{24}$"),
    "provider_attempt": re.compile(r"^pat_[0-9a-f]{24}$"),
    "resource_read_result": re.compile(r"^rrd_[0-9a-f]{24}$"),
    "context_grant": re.compile(r"^grt_[0-9a-f]{24}$"),
    "external_session": re.compile(r"^ses_[0-9a-f]{24}$"),
    "context_pack": re.compile(r"^ctxp_[0-9a-f]{24}$"),
    "context_read_audit": re.compile(r"^aud_[0-9a-f]{24}$"),
    "external_trace": re.compile(r"^xtr_[0-9a-f]{24}$"),
}


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def make_id(kind: str, namespace: str, payload: Mapping[str, Any]) -> str:
    pattern = ID_PATTERNS.get(kind)
    if pattern is None:
        raise ContractError(f"unsupported id kind: {kind}")
    prefix = pattern.pattern.split("_")[0].lstrip("^") + "_"
    digest = sha256_json({"namespace": namespace, "payload": dict(payload)})[:24]
    return prefix + digest


def validate_id(kind: str, value: Any, name: str = "id") -> str:
    pattern = ID_PATTERNS.get(kind)
    if pattern is None or not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ContractError(f"{name} does not match {kind}")
    return value


def validate_sha256(value: Any, name: str = "sha256") -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ContractError(f"{name} must be a lowercase SHA-256")
    return value


def validate_datetime(value: Any, name: str = "datetime") -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise ContractError(f"{name} must be an ISO-8601 timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{name} must include a timezone")
    return value


def validate_relative_path(value: Any, name: str = "path") -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ContractError(f"{name} must be a vault-relative POSIX path")
    path = PurePosixPath(value)
    if value.startswith("/") or value != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
        raise ContractError(f"{name} must be a vault-relative POSIX path")
    return value
