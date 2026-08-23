"""Fail-closed binding for a real Provider-backed R9 worker.

The R9 CLI intentionally never invokes a model.  Product-specific runtime
workers are injected through :class:`ShadowProducer` because only the real
Agent/Workflow graph can create a candidate ProjectionBundle.  This module
binds that injected worker to the exact Provider, model, Prompt/Policy
versions, budget and user consent sealed in the R9 plan.

It deliberately contains no credentials, network client or generic prompt.
That prevents a development convenience wrapper from becoming an unreviewed
second runtime path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Tuple

from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import sha256_json
from memento_backend.projections.bundle_projector import ProjectionBundle
from memento_backend.providers.protocol import ProviderUsage

from .shadow_consent import consent_ref, validate_shadow_consent
from .shadow_runner import validate_shadow_plan
from .shadow_worker import ShadowCaseInput, ShadowCasePrediction, ShadowProducer


@dataclass(frozen=True)
class ProviderShadowBinding:
    """The immutable provenance a real shadow worker is allowed to claim."""

    plan_id: str
    plan_sha256: str
    consent_id: str
    consent_sha256: str
    provider: str
    model: str
    prompt_versions: Tuple[str, ...]
    policy_versions: Tuple[str, ...]


def bind_provider_shadow_producer(
    *,
    plan: Mapping[str, Any],
    consent: Mapping[str, Any],
    producer: ShadowProducer,
) -> "PlanBoundProviderShadowProducer":
    """Return a Provider worker that can only report the sealed provenance.

    Calling this function does not read a snapshot, call a Provider, or write
    any data.  It rejects synthetic plans, incomplete consent, plan/consent
    drift and an object that does not implement the bounded producer protocol.
    """

    validate_shadow_plan(plan, consent)
    validate_shadow_consent(consent)
    if plan["dataset_kind"] != "real_vault_snapshot":
        raise ContractError("provider shadow binding requires a real Vault plan", kind="permission")
    if plan["execution_mode"] != "provider_shadow":
        raise ContractError("provider shadow binding requires provider_shadow mode", kind="permission")
    if plan["user_confirmation"]["status"] != "confirmed":
        raise ContractError("provider shadow binding requires confirmed consent", kind="permission")
    if not isinstance(producer, ShadowProducer):
        raise ContractError("provider shadow worker does not implement ShadowProducer")

    provider = plan["provider"]
    return PlanBoundProviderShadowProducer(
        binding=ProviderShadowBinding(
            plan_id=str(plan["plan_id"]),
            plan_sha256=sha256_json(plan),
            consent_id=str(consent["consent_id"]),
            consent_sha256=consent_ref(consent)["consent_sha256"],
            provider=str(provider["provider"]),
            model=str(provider["model"]),
            prompt_versions=tuple(str(item) for item in provider["prompt_versions"]),
            policy_versions=tuple(str(item) for item in provider["policy_versions"]),
        ),
        _delegate=producer,
    )


@dataclass(frozen=True)
class PlanBoundProviderShadowProducer:
    """Adapter that rejects mismatched or non-attempt Provider usage."""

    binding: ProviderShadowBinding
    _delegate: ShadowProducer

    def produce_case(self, case: ShadowCaseInput) -> ShadowCasePrediction:
        prediction = self._delegate.produce_case(case)
        _validate_usage(prediction.usage, self.binding)
        return prediction

    def candidate_bundle(self) -> ProjectionBundle:
        return self._delegate.candidate_bundle()


def _validate_usage(usage: ProviderUsage, binding: ProviderShadowBinding) -> None:
    value = usage.to_dict()
    if value["mode"] != "provider":
        raise ContractError("bound provider shadow worker reported deterministic usage", kind="evidence")
    if value["provider"] != binding.provider or value["model"] != binding.model:
        raise ContractError("bound provider shadow worker differs from sealed plan", kind="evidence")
