"""Backward-compatible import path for the V2 to V1 adapter."""

from .v2_to_v1_projection_adapter import V1ProjectionPair, adapt_v2_bundle_to_v1

__all__ = ["V1ProjectionPair", "adapt_v2_bundle_to_v1"]
