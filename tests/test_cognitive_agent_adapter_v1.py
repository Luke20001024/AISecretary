from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_AGENT = ROOT / "context-agent"
if str(CONTEXT_AGENT) not in sys.path:
    sys.path.insert(0, str(CONTEXT_AGENT))

from agent_v1 import (  # noqa: E402
    AgentBudget,
    build_agent_profile,
    load_agent_request,
    make_run_id,
    persist_agent_profile,
    request_path,
    response_path,
    run_path,
)
from cognitive_agent_adapter_v1 import CognitiveAgentAdapter  # noqa: E402
from cognitive_v1 import ObjectRef, persisted_sha256  # noqa: E402
from core import (  # noqa: E402
    ContractError,
    atomic_write_json,
    canonical_json,
    sha256_bytes,
)


NOW = dt.datetime(2026, 8, 18, 21, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
LOCAL_DATE = "2026-08-18"
ZERO_SHA = "0" * 64


def usage() -> dict[str, Any]:
    return {
        "model_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
        "reasoning_tokens": 0,
        "usage_missing": False,
        "cost_usd": 0.0,
    }


class FakeBundleStore:
    def __init__(
        self,
        manifest: Mapping[str, Any],
        bundle_ref: ObjectRef,
        *,
        memories: tuple[ObjectRef, ...] = (),
        relations: tuple[ObjectRef, ...] = (),
    ) -> None:
        self.manifest = dict(manifest)
        self.bundle_ref = bundle_ref
        self.memories = memories
        self.relations = relations

    def load_day_bundle_ref(self, local_date: str) -> ObjectRef | None:
        return self.bundle_ref if local_date == self.manifest["local_date"] else None

    def load_day_manifest(self, local_date: str) -> dict[str, Any] | None:
        return dict(self.manifest) if local_date == self.manifest["local_date"] else None

    def list_active_memories(self) -> tuple[ObjectRef, ...]:
        return self.memories

    def list_active_relations(self) -> tuple[ObjectRef, ...]:
        return self.relations


class FakeActionStore:
    def __init__(
        self,
        heads: tuple[tuple[Any, ObjectRef], ...],
        *,
        watermark_sha256: str,
    ) -> None:
        self.heads = heads
        self.watermark_sha256 = watermark_sha256

    def action_watermark(self):
        return (), self.watermark_sha256

    def list_receipt_heads(self, *, statuses):
        if tuple(statuses) != ("ready", "needs_review"):
            raise AssertionError("adapter requested an unexpected receipt status set")
        return self.heads


class FakeRecordStore:
    def __init__(
        self,
        rows: Mapping[str, tuple[ObjectRef, Mapping[str, Any]]],
    ) -> None:
        self.rows = dict(rows)
        self.ref_reads: list[str] = []
        self.head_reads: list[str] = []

    def load_head_ref(self, record_id: str) -> dict[str, Any]:
        self.ref_reads.append(record_id)
        return self.rows[record_id][0].to_dict()

    def load_head(self, record_id: str) -> dict[str, Any]:
        self.head_reads.append(record_id)
        return dict(self.rows[record_id][1])


class TerminalRunner:
    """Persist a valid no-change Agent response/run without a provider call."""

    def __init__(
        self,
        *,
        profile_loader: Callable[[Path], Mapping[str, Any]] = build_agent_profile,
        fail_after_write: bool = False,
        terminal_status: str = "no_change",
        persist_profile: bool = True,
    ) -> None:
        self.calls: list[tuple[Path, str]] = []
        self.profile_loader = profile_loader
        self.fail_after_write = fail_after_write
        self.terminal_status = terminal_status
        self.persist_profile = persist_profile

    def __call__(self, vault: Path, request_id: str) -> None:
        self.calls.append((vault, request_id))
        request, request_file, request_sha = load_agent_request(vault, request_id)
        profile = dict(self.profile_loader(vault))
        run_id = make_run_id(request_id)
        completed_at = (NOW + dt.timedelta(seconds=len(self.calls))).isoformat(
            timespec="seconds"
        )
        run_key = "ark_" + sha256_bytes(request_id.encode("utf-8"))[:24]
        response = {
            "schema_version": "1.0",
            "request_id": request_id,
            "request_sha256": request_sha,
            "kind": "remember_agent_response",
            "status": self.terminal_status,
            "created_at": completed_at,
            "run_id": run_id,
            "run_key": run_key,
            "cache_hit": False,
            "as_of": request["as_of"],
            "window_days": 14,
            "record_days": 0,
            "source_hashes": [],
            "input_history_sha256": ZERO_SHA,
            "input_profile_sha256": profile["profile_sha256"],
            "input_feedback_sha256": ZERO_SHA,
            "input_user_action_sha256": ZERO_SHA,
            "result_profile_sha256": profile["profile_sha256"],
            "memory": None,
            "trace": {
                "model_turns": 0,
                "tool_calls": 0,
                "actions": [],
                "reason_codes": [],
                "history_matches": 0,
                "stop_reason": self.terminal_status,
            },
            "usage": usage(),
            "error": None,
            "error_kind": None,
        }
        if self.terminal_status in {"budget_exhausted", "stale", "error"}:
            response["error"] = "bounded fake failure"
            response["error_kind"] = "budget" if self.terminal_status == "budget_exhausted" else self.terminal_status
        response_sha = sha256_bytes(canonical_json(response).encode("utf-8"))
        run = {
            "schema_version": "1.0",
            "kind": "remember_agent_run",
            "run_id": run_id,
            "run_key": run_key,
            "cache_hit": False,
            "request_id": request_id,
            "request_sha256": request_sha,
            "status": self.terminal_status,
            "started_at": completed_at,
            "completed_at": completed_at,
            "provider": "mock-agentic-workflow",
            "model": "mock-model",
            "policy_sha256": "1" * 64,
            "budget": AgentBudget().as_dict(),
            "input_hashes": {
                "source_hashes": [],
                "history_sha256": ZERO_SHA,
                "profile_sha256": profile["profile_sha256"],
                "feedback_sha256": ZERO_SHA,
                "user_action_sha256": ZERO_SHA,
            },
            "steps": [],
            "usage": usage(),
            "response_sha256": response_sha,
            "error_kind": response["error_kind"],
        }
        self.assert_request_contains_no_material(request_file)
        atomic_write_json(response_path(vault, request_id), response)
        atomic_write_json(run_path(vault, run_id), run, replace=True)
        if self.persist_profile:
            persist_agent_profile(vault)
        if self.fail_after_write:
            raise RuntimeError("simulated adapter crash after Agent commit")

    @staticmethod
    def assert_request_contains_no_material(path: Path) -> None:
        request = json.loads(path.read_text(encoding="utf-8"))
        if set(request) != {
            "schema_version",
            "id",
            "kind",
            "status",
            "created_at",
            "trigger",
            "as_of",
            "window_days",
        }:
            raise AssertionError("adapter leaked material into Agent request")


class AdapterCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="memento-agent-adapter-")
        self.vault = Path(self.temporary.name) / "vault"
        self.vault.mkdir(mode=0o700)
        self.state_root = self.vault / ".context-agent" / "cognitive-secretary-v1"
        self.memory_ref = ObjectRef(
            "reusable_memory", "rmem_" + "1" * 24, 1, "2" * 64
        )
        self.relation_ref = ObjectRef(
            "relation", "rel_" + "3" * 24, 1, "4" * 64
        )
        self.manifest = self.make_manifest(memory_refs=(self.memory_ref,))
        self.bundle_ref = ObjectRef(
            "daily_bundle",
            "db_20260818",
            1,
            persisted_sha256(self.manifest),
        )
        self.store = FakeBundleStore(
            self.manifest, self.bundle_ref, memories=(self.memory_ref,)
        )
        self.profile = build_agent_profile(self.vault)
        self.action_sha = "5" * 64

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def make_manifest(
        *,
        memory_refs: tuple[ObjectRef, ...] = (),
        relation_refs: tuple[ObjectRef, ...] = (),
        result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "kind": "memento_daily_bundle_revision",
            "bundle_id": "db_20260818",
            "revision": 1,
            "status": "committed",
            "operation": "initial_commit",
            "created_at": NOW.isoformat(timespec="seconds"),
            "committed_at": NOW.isoformat(timespec="seconds"),
            "local_date": LOCAL_DATE,
            "request_id": "dreq_" + "6" * 24,
            "run_id": "drun_" + "7" * 24,
            "input_hashes": {
                "source_manifest_sha256": "8" * 64,
                "receipt_manifest_sha256": "9" * 64,
                "profile_sha256": "a" * 64,
                "user_action_watermark_sha256": "b" * 64,
                "policy_sha256": "c" * 64,
            },
            "source_refs": [],
            "receipt_refs": [],
            "memory_refs": [row.to_dict() for row in memory_refs],
            "relation_refs": [row.to_dict() for row in relation_refs],
            "summary_ref": ObjectRef(
                "daily_summary", "dsum_20260818", 1, "d" * 64
            ).to_dict(),
            "candidate_materializations": [],
            "long_term_result_ref": None if result is None else dict(result),
            "warnings": [],
            "previous_revision_sha256": None,
        }

    def adapter(
        self,
        runner: Callable[[Path, str], Any] | None,
        *,
        store: FakeBundleStore | None = None,
        action_store: Any | None = None,
        record_store: Any | None = None,
        profile_loader: Callable[[Path], Mapping[str, Any]] = build_agent_profile,
    ) -> CognitiveAgentAdapter:
        return CognitiveAgentAdapter(
            self.vault,
            bundle_store=store or self.store,
            action_store=action_store,
            record_store=record_store,
            state_root=self.state_root,
            agent_runner=runner,
            profile_loader=profile_loader,
            gate_checker=lambda vault: {"enabled": True, "vault": str(vault)},
            clock=lambda: NOW,
        )

    def process(
        self,
        adapter: CognitiveAgentAdapter,
        *,
        store: FakeBundleStore | None = None,
        trigger: str = "manual",
        profile_sha: str | None = None,
        action_sha: str | None = None,
    ):
        selected = store or self.store
        return adapter.process(
            bundle_ref=selected.bundle_ref,
            manifest=selected.manifest,
            reusable_memory_heads=selected.memories,
            relation_heads=selected.relations,
            profile_sha256=profile_sha or self.profile["profile_sha256"],
            user_action_watermark_sha256=action_sha or self.action_sha,
            trigger=trigger,
        )


class AdapterTests(AdapterCase):
    def test_future_receipt_does_not_change_old_day_authorization(self) -> None:
        past_record_ref = ObjectRef(
            "source_record", "rec_" + "1" * 24, 1, "2" * 64
        )
        future_record_ref = ObjectRef(
            "source_record", "rec_" + "3" * 24, 1, "4" * 64
        )
        past_receipt_ref = ObjectRef(
            "interpretation_receipt", "rcp_" + "5" * 24, 1, "6" * 64
        )
        future_receipt_ref = ObjectRef(
            "interpretation_receipt", "rcp_" + "7" * 24, 1, "8" * 64
        )
        past_receipt = SimpleNamespace(
            record_ref=past_record_ref,
            sha256=past_receipt_ref.revision_sha256,
        )
        future_receipt = SimpleNamespace(
            record_ref=future_record_ref,
            sha256=future_receipt_ref.revision_sha256,
        )
        actions = FakeActionStore(
            ((past_receipt, past_receipt_ref),),
            watermark_sha256=self.action_sha,
        )
        records = FakeRecordStore(
            {
                past_record_ref.id: (
                    past_record_ref,
                    {"status": "active", "local_date": LOCAL_DATE},
                ),
                future_record_ref.id: (
                    future_record_ref,
                    {"status": "active", "local_date": "2026-08-19"},
                ),
            }
        )
        adapter = self.adapter(
            None,
            action_store=actions,
            record_store=records,
        )
        manifest = self.make_manifest(memory_refs=(self.memory_ref,))
        manifest["receipt_refs"] = [past_receipt_ref.to_dict()]
        manifest["input_hashes"] = {
            **manifest["input_hashes"],
            "user_action_watermark_sha256": self.action_sha,
        }

        before = adapter._cognitive_authorization_refs(
            manifest,
            expected_action_sha256=self.action_sha,
        )
        actions.heads = (
            (past_receipt, past_receipt_ref),
            (future_receipt, future_receipt_ref),
        )
        after = adapter._cognitive_authorization_refs(
            manifest,
            expected_action_sha256=self.action_sha,
        )

        expected = (past_receipt_ref.to_dict(),)
        self.assertEqual(before, expected)
        self.assertEqual(after, expected)
        self.assertIn(future_record_ref.id, records.ref_reads)
        self.assertIn(future_record_ref.id, records.head_reads)

    def test_authorization_rejects_receipt_not_bound_to_current_record_head(
        self,
    ) -> None:
        receipt_record_ref = ObjectRef(
            "source_record", "rec_" + "1" * 24, 1, "2" * 64
        )
        current_record_ref = ObjectRef(
            "source_record", receipt_record_ref.id, 2, "3" * 64
        )
        receipt_ref = ObjectRef(
            "interpretation_receipt", "rcp_" + "4" * 24, 1, "5" * 64
        )
        receipt = SimpleNamespace(
            record_ref=receipt_record_ref,
            sha256=receipt_ref.revision_sha256,
        )
        actions = FakeActionStore(
            ((receipt, receipt_ref),), watermark_sha256=self.action_sha
        )
        records = FakeRecordStore(
            {
                receipt_record_ref.id: (
                    current_record_ref,
                    {"status": "active", "local_date": LOCAL_DATE},
                )
            }
        )
        adapter = self.adapter(
            None,
            action_store=actions,
            record_store=records,
        )
        manifest = self.make_manifest(memory_refs=(self.memory_ref,))
        manifest["receipt_refs"] = [receipt_ref.to_dict()]
        manifest["input_hashes"] = {
            **manifest["input_hashes"],
            "user_action_watermark_sha256": self.action_sha,
        }

        with self.assertRaisesRegex(ContractError, "原记录已变化"):
            adapter._cognitive_authorization_refs(
                manifest,
                expected_action_sha256=self.action_sha,
            )

    def test_empty_receipt_refs_keep_standalone_authorization_behavior(self) -> None:
        adapter = self.adapter(None)
        self.assertIsNone(
            adapter._cognitive_authorization_refs(
                self.manifest,
                expected_action_sha256=self.action_sha,
            )
        )

    def test_no_formal_material_creates_zero_agent_requests(self) -> None:
        manifest = self.make_manifest()
        ref = ObjectRef(
            "daily_bundle", "db_20260818", 1, persisted_sha256(manifest)
        )
        store = FakeBundleStore(manifest, ref)
        runner = TerminalRunner()
        result = self.process(self.adapter(runner, store=store), store=store)
        self.assertEqual(result.status, "no_material")
        self.assertFalse(result.request_created)
        self.assertFalse(result.runner_called)
        self.assertEqual(runner.calls, [])
        requests = self.vault / ".context-agent" / "agent-v1" / "requests"
        self.assertFalse(requests.exists())

    def test_tombstoned_bundle_memory_is_not_positive_material(self) -> None:
        # The bundle still cites revision 1, while the current active-head
        # snapshot no longer contains that stable id.
        store = FakeBundleStore(self.manifest, self.bundle_ref, memories=())
        runner = TerminalRunner()
        result = self.process(self.adapter(runner, store=store), store=store)
        self.assertEqual(result.status, "no_material")
        self.assertEqual(runner.calls, [])

    def test_manual_request_is_idempotent_and_material_never_enters_request(self) -> None:
        runner = TerminalRunner()
        adapter = self.adapter(runner)
        first = self.process(adapter)
        second = self.process(adapter)
        self.assertEqual(first.status, "completed")
        self.assertTrue(first.request_created)
        self.assertEqual(first.agent_result_ref["status"], "no_change")
        self.assertEqual(second.status, "recovered")
        self.assertFalse(second.request_created)
        self.assertFalse(second.runner_called)
        self.assertEqual(len(runner.calls), 1)
        request_files = list(
            (self.vault / ".context-agent" / "agent-v1" / "requests").glob(
                "*.json"
            )
        )
        self.assertEqual(len(request_files), 1)
        request_text = request_files[0].read_text(encoding="utf-8")
        self.assertNotIn(self.memory_ref.id, request_text)
        self.assertNotIn(first.material_sha256, request_text)
        self.assertNotIn(self.profile["profile_sha256"], request_text)
        material_file = next(
            (
                self.state_root / "long-term-agent-adapter" / "materials"
            ).glob("*.json")
        )
        material = json.loads(material_file.read_text(encoding="utf-8"))
        self.assertEqual(
            material["material_brief"]["memory_refs"],
            [self.memory_ref.to_dict()],
        )
        self.assertEqual(material["material_sha256"], first.material_sha256)

    def test_recovery_reuses_terminal_files_after_runner_crash(self) -> None:
        crashing = TerminalRunner(fail_after_write=True)
        adapter = self.adapter(crashing)
        first = self.process(adapter)
        self.assertEqual(first.status, "warning")
        self.assertEqual(first.warning, "long_term_failed")
        second = self.process(adapter)
        self.assertEqual(second.status, "recovered")
        self.assertIsNone(second.warning)
        self.assertEqual(second.agent_result_ref["status"], "no_change")
        self.assertEqual(len(crashing.calls), 1)

    def test_recovery_accepts_profile_written_by_the_terminal_agent_commit(self) -> None:
        state = {"profile": dict(self.profile)}

        def loader(vault: Path) -> Mapping[str, Any]:
            del vault
            return state["profile"]

        terminal = TerminalRunner(
            profile_loader=loader,
            fail_after_write=True,
            persist_profile=False,
        )

        def profile_changing_crash(vault: Path, request_id: str) -> None:
            changed = dict(state["profile"])
            changed["profile_sha256"] = "e" * 64
            state["profile"] = changed
            terminal(vault, request_id)

        adapter = self.adapter(
            profile_changing_crash,
            profile_loader=loader,
        )
        first = self.process(
            adapter, profile_sha=self.profile["profile_sha256"]
        )
        second = self.process(adapter, profile_sha="e" * 64)
        self.assertEqual(first.status, "warning")
        self.assertEqual(second.status, "recovered")
        self.assertEqual(first.request_id, second.request_id)
        self.assertFalse(second.runner_called)
        self.assertEqual(len(terminal.calls), 1)
        self.assertEqual(
            len(
                list(
                    (
                        self.state_root
                        / "long-term-agent-adapter"
                        / "materials"
                    ).glob("*.json")
                )
            ),
            1,
        )

    def test_scheduled_and_recovery_share_deterministic_request(self) -> None:
        runner = TerminalRunner()
        adapter = self.adapter(runner)
        first = self.process(adapter, trigger="scheduled")
        second = self.process(adapter, trigger="recovery")
        self.assertEqual(first.request_id, second.request_id)
        self.assertTrue(first.request_id.startswith("arq_"))
        self.assertEqual(len(runner.calls), 1)
        sidecar = next(
            (self.state_root / "long-term-agent-adapter" / "materials").glob(
                "*.json"
            )
        )
        audit = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(audit["daily_trigger"], "scheduled")
        self.assertEqual(audit["agent_trigger"], "scheduled")

    def test_user_action_watermark_change_creates_new_manual_request(self) -> None:
        runner = TerminalRunner()
        adapter = self.adapter(runner)
        first = self.process(adapter, action_sha="5" * 64)
        second = self.process(adapter, action_sha="6" * 64)
        self.assertEqual(first.status, "completed")
        self.assertEqual(second.status, "completed")
        self.assertNotEqual(first.request_id, second.request_id)
        self.assertEqual(len(runner.calls), 2)

    def test_same_day_scheduled_material_change_creates_new_request(self) -> None:
        runner = TerminalRunner()
        adapter = self.adapter(runner)
        first = self.process(adapter, trigger="scheduled", action_sha="5" * 64)
        second = self.process(adapter, trigger="scheduled", action_sha="6" * 64)
        self.assertEqual(first.status, "completed")
        self.assertEqual(second.status, "completed")
        self.assertNotEqual(first.request_id, second.request_id)
        self.assertEqual(len(runner.calls), 2)

    def test_profile_change_creates_new_manual_request(self) -> None:
        state = {"profile": dict(self.profile)}

        def loader(vault: Path) -> Mapping[str, Any]:
            del vault
            return state["profile"]

        runner = TerminalRunner(profile_loader=loader, persist_profile=False)
        adapter = self.adapter(runner, profile_loader=loader)
        first = self.process(
            adapter, profile_sha=state["profile"]["profile_sha256"]
        )
        changed = dict(state["profile"])
        changed["profile_sha256"] = "e" * 64
        state["profile"] = changed
        second = self.process(adapter, profile_sha="e" * 64)
        self.assertEqual(first.status, "completed")
        self.assertEqual(second.status, "completed")
        self.assertNotEqual(first.request_id, second.request_id)
        self.assertEqual(len(runner.calls), 2)

    def test_agent_failure_is_warning_and_daily_bundle_is_unchanged(self) -> None:
        runner = TerminalRunner(terminal_status="error")
        adapter = self.adapter(runner)
        original = dict(self.store.manifest)
        result = self.process(adapter)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.warning, "long_term_failed")
        self.assertEqual(result.agent_result_ref["status"], "error")
        self.assertEqual(self.store.manifest, original)

    def test_missing_runner_terminal_is_warning_not_exception(self) -> None:
        adapter = self.adapter(lambda vault, request_id: None)
        result = self.process(adapter)
        self.assertEqual(result.status, "warning")
        self.assertEqual(result.warning, "long_term_failed")
        self.assertTrue(result.runner_called)

    def test_existing_bundle_result_short_circuits_agent(self) -> None:
        linked = {
            "request_id": "arq_" + "1" * 24,
            "run_id": "arun_" + "2" * 24,
            "response_sha256": "3" * 64,
            "status": "no_change",
            "memory_ref": None,
        }
        manifest = self.make_manifest(memory_refs=(self.memory_ref,), result=linked)
        ref = ObjectRef(
            "daily_bundle", "db_20260818", 1, persisted_sha256(manifest)
        )
        store = FakeBundleStore(manifest, ref, memories=(self.memory_ref,))
        runner = TerminalRunner()
        result = self.process(self.adapter(runner, store=store), store=store)
        self.assertEqual(result.status, "already_linked")
        self.assertEqual(result.agent_result_ref, linked)
        self.assertEqual(runner.calls, [])

    def test_stale_formal_head_snapshot_fails_before_request(self) -> None:
        adapter = self.adapter(TerminalRunner())
        with self.assertRaises(Exception):
            adapter.process(
                bundle_ref=self.bundle_ref,
                manifest=self.manifest,
                reusable_memory_heads=(),
                relation_heads=(),
                profile_sha256=self.profile["profile_sha256"],
                user_action_watermark_sha256=self.action_sha,
                trigger="manual",
            )
        self.assertFalse(
            (self.vault / ".context-agent" / "agent-v1" / "requests").exists()
        )


if __name__ == "__main__":
    unittest.main()
