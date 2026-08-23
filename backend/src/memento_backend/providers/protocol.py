"""Provider-neutral requests with explicit attempt and usage accounting."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import sha256_json, validate_id


class ProviderFailure(RuntimeError):
    """A provider attempt that has a known terminal or unknown outcome."""

    def __init__(self, message: str, *, usage: "ProviderUsage") -> None:
        super().__init__(message)
        self.usage = usage


@dataclass(frozen=True)
class ProviderUsage:
    mode: str
    provider: Optional[str]
    model: Optional[str]
    attempt_status: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    latency_ms: int

    @classmethod
    def deterministic(cls) -> "ProviderUsage":
        return cls("deterministic", None, None, "not_started", 0, 0, 0, 0.0, 0)

    def to_dict(self) -> dict[str, Any]:
        if self.mode not in {"deterministic", "provider"}:
            raise ContractError("provider usage mode is invalid")
        if self.attempt_status not in {"not_started", "succeeded", "failed", "unknown"}:
            raise ContractError("provider attempt status is invalid")
        values = (self.prompt_tokens, self.completion_tokens, self.total_tokens, self.latency_ms)
        if (
            any(type(value) is not int or value < 0 for value in values)
            or type(self.estimated_cost_usd) not in {int, float}
            or not math.isfinite(float(self.estimated_cost_usd))
            or self.estimated_cost_usd < 0
        ):
            raise ContractError("provider usage contains a negative value")
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ContractError("provider token total is inconsistent")
        if self.mode == "deterministic" and (
            self.provider is not None or self.model is not None or self.attempt_status != "not_started" or any(values)
        ):
            raise ContractError("deterministic usage cannot contain a provider attempt")
        if self.mode == "provider" and (not self.provider or not self.model or self.attempt_status == "not_started"):
            raise ContractError("provider usage must identify an attempted model")
        return {
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "attempt_status": self.attempt_status,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "latency_ms": self.latency_ms,
        }


@dataclass(frozen=True)
class ProviderRequest:
    run_id: str
    agent_role: str
    prompt_version: str
    policy_version: str
    input_payload: Mapping[str, Any]

    @property
    def input_sha256(self) -> str:
        return sha256_json(self.input_payload)

    def validate(self) -> None:
        validate_id("agent_run", self.run_id, "run_id")
        if self.agent_role not in {
            "capture_understanding", "record_interpreter", "daily_integrator",
            "theme_synthesizer", "self_understanding", "context_router", "resource_reader",
        }:
            raise ContractError("provider request agent role is invalid")
        if not self.prompt_version or not self.policy_version:
            raise ContractError("provider request versions are required")


@dataclass(frozen=True)
class ProviderResponse:
    output: Mapping[str, Any]
    usage: ProviderUsage


@runtime_checkable
class Provider(Protocol):
    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Return one strict JSON-compatible payload and recorded usage."""
