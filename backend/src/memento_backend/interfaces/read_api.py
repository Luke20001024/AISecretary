"""Stable, transport-neutral read façade for frontend composition.

Every projection read starts from the atomically published current pointer and
therefore observes one complete, validated bundle.  The façade exposes no
filesystem paths and returns detached JSON values so callers cannot mutate
stored state in memory.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping, Optional

from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import validate_id
from memento_backend.projections.bundle_projector import ProjectionBundle
from memento_backend.storage.bundle_store import BundleStore
from memento_backend.storage.revision_store import RevisionStore
from memento_backend.storage.run_ledger import RunLedger
from memento_backend.storage.run_request_inbox import RunRequestInbox


_DETAIL_ID_KINDS: Mapping[str, str] = {
    "record": "source_record",
    "resource": "resource_card",
    "theme": "theme",
    "self_insight": "self_insight",
}


class ProjectionReadApi:
    """Read the current UI contract without exposing Store internals."""

    def __init__(
        self,
        bundles: BundleStore,
        revisions: RevisionStore,
        runs: RunLedger,
        run_requests: RunRequestInbox,
    ) -> None:
        self.bundles = bundles
        self.revisions = revisions
        self.runs = runs
        self.run_requests = run_requests

    def read_projection_manifest(self) -> dict[str, Any]:
        return self._detached(self._current_bundle().manifest)

    def read_home(self) -> dict[str, Any]:
        return self._projection("projections/home.json")

    def read_timeline(
        self,
        requested_range: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        timeline = self._projection("projections/timeline.json")
        if requested_range is not None and dict(requested_range) != timeline["range"]:
            raise ContractError(
                "requested timeline range is outside the published projection",
                kind="not_found",
            )
        return timeline

    def read_landscape(self) -> dict[str, Any]:
        return self._projection("projections/landscape.json")

    def read_self(self) -> dict[str, Any]:
        return self._projection("projections/self.json")

    def read_record_detail(self, record_id: str) -> dict[str, Any]:
        return self._detail("record", record_id)

    def read_resource_detail(self, resource_id: str) -> dict[str, Any]:
        return self._detail("resource", resource_id)

    def read_theme_detail(self, theme_id: str) -> dict[str, Any]:
        return self._detail("theme", theme_id)

    def read_self_insight_detail(self, insight_id: str) -> dict[str, Any]:
        return self._detail("self_insight", insight_id)

    def read_external_session(self, session_id: str) -> dict[str, Any]:
        validate_id("external_session", session_id, "session_id")
        return self._detached(
            self.revisions.load_head("external_session", session_id)
        )

    def read_run_status(self, run_id: str) -> dict[str, Any]:
        if run_id.startswith("rrq_"):
            validate_id("run_request", run_id, "run_id")
            return self._detached(self.run_requests.read_status(run_id))
        validate_id("agent_run", run_id, "run_id")
        return self._detached(self.runs.load(run_id))

    def _current_bundle(self) -> ProjectionBundle:
        bundle = self.bundles.load_current()
        if bundle is None:
            raise ContractError(
                "no current projection bundle has been published",
                kind="not_found",
            )
        return bundle

    def _projection(self, path: str) -> dict[str, Any]:
        return self._detached(self._current_bundle().projection(path))

    def _detail(self, detail_kind: str, subject_id: str) -> dict[str, Any]:
        validate_id(_DETAIL_ID_KINDS[detail_kind], subject_id, "subject_id")
        bundle = self._current_bundle()
        index = bundle.projection("projections/detail-index.json")
        entries = [
            entry
            for entry in index["entries"]
            if entry["detail_kind"] == detail_kind
            and entry["subject_ref"]["id"] == subject_id
        ]
        if not entries:
            raise ContractError("detail projection does not exist", kind="not_found")
        if len(entries) != 1:
            raise ContractError("detail projection index is ambiguous", kind="evidence")
        return self._detached(bundle.projection(str(entries[0]["path"])))

    @staticmethod
    def _detached(value: Mapping[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(dict(value))
