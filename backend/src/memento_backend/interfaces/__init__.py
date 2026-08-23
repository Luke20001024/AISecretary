"""Stable read, action and compatibility interfaces."""

from .action_api import ActionApi
from .read_api import ProjectionReadApi

from .v2_to_v1_projection_adapter import V1ProjectionPair, adapt_v2_bundle_to_v1

from .v1_source_adapter import adapt_new_v1_source_record

__all__ = [
    "ActionApi",
    "ProjectionReadApi",
    "V1ProjectionPair",
    "adapt_v2_bundle_to_v1",
    "adapt_new_v1_source_record",
]
