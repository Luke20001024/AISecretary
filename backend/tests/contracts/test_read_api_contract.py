from __future__ import annotations

from pathlib import Path
import pytest

from memento_backend.agents.capture_understanding_agent import (
    CaptureInput,
    CaptureUnderstandingAgent,
)
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id
from memento_backend.interfaces.read_api import ProjectionReadApi
from memento_backend.projections.bundle_projector import (
    ProjectionBundle,
    build_projection_bundle,
)
from memento_backend.storage.action_inbox import ActionInbox, EMPTY_ACTION_WATERMARK
from memento_backend.storage.atomic import AtomicFileStore
from memento_backend.storage.bundle_store import BundleStore
from memento_backend.storage.revision_store import RevisionStore
from memento_backend.storage.run_ledger import RunLedger
from memento_backend.storage.run_request_inbox import RunRequestInbox
from memento_backend.workflows.manage_context_grant import ContextGrantWorkflow
from memento_backend.workflows.open_external_session import ExternalSessionWorkflow
from tests.fixtures.formal_20d import STATEMENTS, formal_20d_inputs


def api_stack(
    tmp_path: Path,
) -> tuple[ProjectionReadApi, BundleStore, RevisionStore, RunLedger, RunRequestInbox]:
    root = tmp_path / "isolated-v2"
    root.mkdir(mode=0o700)
    files = AtomicFileStore(root)
    bundles = BundleStore(files)
    revisions = RevisionStore(files)
    runs = RunLedger(files)
    actions = ActionInbox(files)
    run_requests = RunRequestInbox(files, actions)
    return (
        ProjectionReadApi(bundles, revisions, runs, run_requests),
        bundles,
        revisions,
        runs,
        run_requests,
    )


def publish_fixture(bundles: BundleStore) -> ProjectionBundle:
    bundle = build_projection_bundle(
        formal_20d_inputs(),
        as_of="2026-08-18",
        generated_at="2026-08-18T22:00:00+08:00",
    )
    bundles.publish(bundle)
    return bundle


def test_top_level_reads_share_one_current_manifest(tmp_path: Path) -> None:
    api, bundles, _, _, _ = api_stack(tmp_path)
    bundle = publish_fixture(bundles)

    manifest = api.read_projection_manifest()
    values = (
        api.read_home(),
        api.read_timeline(bundle.projection("projections/timeline.json")["range"]),
        api.read_landscape(),
        api.read_self(),
    )

    assert manifest["bundle_id"] == bundle.manifest["bundle_id"]
    assert all(value["bundle_id"] == manifest["bundle_id"] for value in values)


def test_all_four_detail_kinds_resolve_through_the_published_index(tmp_path: Path) -> None:
    api, bundles, _, _, _ = api_stack(tmp_path)
    publish_fixture(bundles)
    inputs = formal_20d_inputs()

    assert api.read_record_detail(str(inputs.source_records[0]["record_id"]))["record_ref"]["id"] == inputs.source_records[0]["record_id"]
    assert api.read_resource_detail(str(inputs.resource_cards[0]["resource_id"]))["resource_ref"]["id"] == inputs.resource_cards[0]["resource_id"]
    assert api.read_theme_detail(str(inputs.themes[0]["theme_id"]))["theme_ref"]["id"] == inputs.themes[0]["theme_id"]
    assert api.read_self_insight_detail(str(inputs.self_insights[0]["insight_id"]))["insight_ref"]["id"] == inputs.self_insights[0]["insight_id"]


def test_read_api_fails_closed_for_absent_bundle_range_and_detail(tmp_path: Path) -> None:
    api, bundles, _, _, _ = api_stack(tmp_path)
    with pytest.raises(ContractError) as absent:
        api.read_home()
    assert absent.value.kind == "not_found"

    publish_fixture(bundles)
    with pytest.raises(ContractError) as unavailable_range:
        api.read_timeline({"start": "2026-08-01", "end": "2026-08-18", "days": 18})
    assert unavailable_range.value.kind == "not_found"
    with pytest.raises(ContractError) as absent_detail:
        api.read_theme_detail("thm_ffffffffffffffffffffffff")
    assert absent_detail.value.kind == "not_found"


def test_returned_values_are_detached_from_the_store(tmp_path: Path) -> None:
    api, bundles, _, _, _ = api_stack(tmp_path)
    publish_fixture(bundles)
    first = api.read_home()
    first["today_status"]["saved"] = 999
    assert api.read_home()["today_status"]["saved"] != 999


def test_external_session_and_terminal_run_reads_use_formal_stores(tmp_path: Path) -> None:
    api, _, revisions, runs, run_requests = api_stack(tmp_path)
    grant_ref = ContextGrantWorkflow(revisions).grant(
        client_id="fixture-client",
        allowed_kinds=("theme",),
        topic_scope=("产品方法",),
        time_scope=None,
        max_sensitivity="normal",
        allow_source_quotes=False,
        allowed_writeback=("outcome",),
        expires_at="2026-08-24T10:00:00+08:00",
        created_at="2026-08-23T10:00:00+08:00",
    )
    session_ref = ExternalSessionWorkflow(revisions).open(
        grant_id=str(grant_ref["id"]),
        client_id="fixture-client",
        task="继续产品方案",
        topic_scope=("产品方法",),
        time_scope=None,
        opened_at="2026-08-23T10:01:00+08:00",
    )
    assert api.read_external_session(str(session_ref["id"]))["task"] == "继续产品方案"

    record = formal_20d_inputs().source_records[0]
    candidate = CaptureUnderstandingAgent().evaluate(
        CaptureInput(
            source_record=record,
            authorized_text=STATEMENTS[0],
            selected_text=STATEMENTS[0],
            user_authored=True,
        ),
        user_action_watermark_sha256=EMPTY_ACTION_WATERMARK,
        created_at="2026-08-23T10:02:00+08:00",
    )
    runs.record(
        candidate,
        terminal_status="returned",
        finished_at="2026-08-23T10:02:01+08:00",
    )
    status = api.read_run_status(str(candidate["run_id"]))
    assert status["terminal_status"] == "returned"
    assert status["candidate_id"] == candidate["candidate_id"]

    request_base = {
        "run_kind": "rebuild_projections",
        "scope": {"local_date": "2026-08-23"},
        "base_user_action_watermark_sha256": EMPTY_ACTION_WATERMARK,
        "requested_at": "2026-08-23T10:03:00+08:00",
        "requested_by": "user",
    }
    request = {
        "schema_version": "1.0",
        "kind": "memento_run_request",
        "request_id": make_id("run_request", "run-request-v1", request_base),
        **request_base,
    }
    run_requests.submit(request)
    queued = api.read_run_status(str(request["request_id"]))
    assert queued["status"] == "queued"
    assert queued["run_kind"] == "rebuild_projections"
