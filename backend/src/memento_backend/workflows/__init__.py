"""Trusted local workflows that validate Agent candidates before commit."""

from .consolidate_day import ConsolidateDayWorkflow
from .apply_self_action import ApplySelfActionWorkflow
from .ingest_record import IngestRecordWorkflow
from .interpret_record import InterpretationWorkflow, InterpretationWorkflowResult
from .read_resource import ResourceReadWorkflow
from .route_capture import CaptureWorkflow, CaptureWorkflowResult
from .update_theme import UpdateThemeWorkflow
from .update_self_understanding import UpdateSelfUnderstandingWorkflow

__all__ = [
    "CaptureWorkflow",
    "CaptureWorkflowResult",
    "InterpretationWorkflow",
    "InterpretationWorkflowResult",
    "IngestRecordWorkflow",
    "ResourceReadWorkflow",
    "ConsolidateDayWorkflow",
    "UpdateThemeWorkflow",
    "UpdateSelfUnderstandingWorkflow",
    "ApplySelfActionWorkflow",
]
