"""Read-only shadow evaluation primitives for the R9 quality gate."""

from .shadow_metrics import ShadowObservation, aggregate_shadow_metrics, evaluate_shadow_gates
from .shadow_preflight import preflight_real_shadow
from .shadow_consent import build_shadow_consent, consent_ref, validate_shadow_consent
from .shadow_runner import (
    ShadowRunResult,
    build_shadow_plan,
    run_shadow_evaluation,
    validate_shadow_plan,
)
from .provider_shadow_binding import (
    PlanBoundProviderShadowProducer,
    ProviderShadowBinding,
    bind_provider_shadow_producer,
)
from .shadow_snapshot import ReadOnlySnapshot, create_read_only_snapshot, verify_read_only_snapshot
from .shadow_worker import (
    ShadowCaseInput,
    ShadowCasePrediction,
    ShadowExecutionResult,
    ShadowProducer,
    build_shadow_case_set,
    execute_shadow_cases,
    shadow_case_set_ref,
    validate_shadow_case_set,
    validate_shadow_work_product,
)

__all__ = [
    "ReadOnlySnapshot",
    "ShadowObservation",
    "ShadowCaseInput",
    "ShadowCasePrediction",
    "ShadowExecutionResult",
    "ShadowProducer",
    "ShadowRunResult",
    "PlanBoundProviderShadowProducer",
    "ProviderShadowBinding",
    "aggregate_shadow_metrics",
    "build_shadow_consent",
    "build_shadow_plan",
    "bind_provider_shadow_producer",
    "build_shadow_case_set",
    "create_read_only_snapshot",
    "evaluate_shadow_gates",
    "preflight_real_shadow",
    "execute_shadow_cases",
    "consent_ref",
    "run_shadow_evaluation",
    "shadow_case_set_ref",
    "validate_shadow_consent",
    "validate_shadow_plan",
    "validate_shadow_case_set",
    "validate_shadow_work_product",
    "verify_read_only_snapshot",
]
