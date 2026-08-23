"""Deterministic bridge from committed daily material to Agent V1.

The adapter is intentionally a gate and audit layer, not another memory
engine.  It never puts a Daily Summary, reusable-memory text, relation text,
or its material brief into an Agent V1 request.  Agent V1 receives its existing
strict 14-day request and therefore continues to register and verify exact raw
daily-note evidence itself.

The durable material sidecar answers only three questions: which committed
refs changed, which Agent profile/user-action snapshot was current, and which
existing Agent V1 request owns that decision.  A terminal result sidecar keeps
only the bounded ``AgentResultRef`` required by a later Daily Bundle revision.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from agent_v1 import (
    build_agent_profile,
    create_agent_request,
    load_agent_request,
    make_run_id,
    request_path,
    require_agent_v1_enabled,
    response_path,
    run_path,
    scheduled_agent_request_id,
    load_cognitive_authorization,
    persist_cognitive_authorization,
    validate_agent_profile,
    validate_agent_response,
    validate_agent_run,
)
from cognitive_actions_v1 import CognitiveActionStore
from cognitive_bundle_store_v1 import CognitiveBundleStore
from cognitive_store_v1 import RecordStore
from cognitive_v1 import ObjectRef, persisted_sha256
from core import (
    ContractError,
    atomic_write_json,
    canonical_json,
    read_json,
    sha256_bytes,
)


ADAPTER_VERSION = "cognitive-agent-adapter-v1.0"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
REQUEST_RE = re.compile(r"^arq_[0-9a-f]{24}$")
RUN_RE = re.compile(r"^arun_[0-9a-f]{24}$")
GATE_RE = re.compile(r"^ltg_[0-9a-f]{24}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

MATERIAL_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "adapter_version",
        "gate_key",
        "created_at",
        "local_date",
        "bundle_ref",
        "material_brief",
        "material_sha256",
        "profile_sha256",
        "user_action_watermark_sha256",
        "daily_trigger",
        "agent_trigger",
        "request_id",
    }
)
MATERIAL_BRIEF_FIELDS = frozenset(
    {
        "adapter_version",
        "local_date",
        "bundle_ref",
        "memory_refs",
        "relation_refs",
    }
)
RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "adapter_version",
        "gate_key",
        "completed_at",
        "request_id",
        "post_profile_sha256",
        "agent_result_ref",
    }
)
AGENT_RESULT_FIELDS = frozenset(
    {"request_id", "run_id", "response_sha256", "status", "memory_ref"}
)
AGENT_RESULT_STATUSES = frozenset(
    {
        "updated",
        "no_change",
        "insufficient_evidence",
        "budget_exhausted",
        "stale",
        "error",
    }
)


def _now_text(clock: Callable[[], dt.datetime]) -> str:
    value = clock()
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise ContractError("clock 必须返回带时区的 datetime", kind="runtime")
    return value.isoformat(timespec="seconds")


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ContractError(f"{name} 必须是 SHA-256")
    return value


def _date(value: Any, name: str = "local_date") -> str:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise ContractError(f"{name} 必须是 YYYY-MM-DD")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"{name} 无效") from exc
    if parsed.isoformat() != value:
        raise ContractError(f"{name} 无效")
    return value


def _object(value: Any, fields: frozenset[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{name} 必须是 JSON object")
    actual = frozenset(value)
    if actual != fields:
        raise ContractError(
            f"{name} 字段不符合合同；缺失={sorted(fields - actual)}；"
            f"未知={sorted(actual - fields)}"
        )
    return dict(value)


def _ref(value: ObjectRef | Mapping[str, Any], name: str) -> ObjectRef:
    try:
        return value if isinstance(value, ObjectRef) else ObjectRef.from_dict(value)
    except ContractError as exc:
        raise ContractError(f"{name} 无效：{exc}", kind=exc.kind) from exc


def _sorted_refs(
    values: Sequence[ObjectRef | Mapping[str, Any]],
    *,
    kind: str,
    name: str,
) -> tuple[ObjectRef, ...]:
    refs = tuple(sorted((_ref(row, name) for row in values), key=lambda row: (row.id, row.revision, row.revision_sha256)))
    if any(row.kind != kind for row in refs):
        raise ContractError(f"{name} kind 无效")
    if len({row.id for row in refs}) != len(refs):
        raise ContractError(f"{name} 不得包含重复 id")
    return refs


def _head_ref(value: Any, kind: str) -> ObjectRef:
    if isinstance(value, ObjectRef):
        if value.kind != kind:
            raise ContractError("正式 head kind 无效", kind="evidence")
        return value
    if kind == "reusable_memory":
        identifier = getattr(value, "memory_id", None)
    else:
        identifier = getattr(value, "relation_id", None)
    revision = getattr(value, "revision", None)
    digest = getattr(value, "sha256", None)
    return ObjectRef(kind, identifier, revision, digest)


def validate_agent_result_ref(value: Any) -> dict[str, Any]:
    item = _object(value, AGENT_RESULT_FIELDS, "AgentResultRef")
    if not isinstance(item["request_id"], str) or not REQUEST_RE.fullmatch(item["request_id"]):
        raise ContractError("AgentResultRef.request_id 无效")
    if not isinstance(item["run_id"], str) or not RUN_RE.fullmatch(item["run_id"]):
        raise ContractError("AgentResultRef.run_id 无效")
    _sha(item["response_sha256"], "AgentResultRef.response_sha256")
    if item["status"] not in AGENT_RESULT_STATUSES:
        raise ContractError("AgentResultRef.status 无效")
    if item["status"] == "updated":
        memory = _ref(item["memory_ref"], "AgentResultRef.memory_ref")
        if memory.kind != "understanding":
            raise ContractError("updated AgentResultRef 必须引用 understanding")
        item["memory_ref"] = memory.to_dict()
    elif item["memory_ref"] is not None:
        raise ContractError("非 updated AgentResultRef.memory_ref 必须是 null")
    return item


@dataclass(frozen=True)
class LongTermAdapterResult:
    status: str
    material_sha256: str
    request_id: str | None
    request_created: bool
    runner_called: bool
    agent_result_ref: Mapping[str, Any] | None
    warning: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "material_sha256": self.material_sha256,
            "request_id": self.request_id,
            "request_created": self.request_created,
            "runner_called": self.runner_called,
            "agent_result_ref": (
                None
                if self.agent_result_ref is None
                else dict(self.agent_result_ref)
            ),
            "warning": self.warning,
        }


class _AdapterLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.descriptor: int | None = None

    def __enter__(self) -> "_AdapterLock":
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise ContractError("无法安全打开长期适配锁", kind="runtime") from exc
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) & 0o077
        ):
            os.close(descriptor)
            raise ContractError("长期适配锁不安全", kind="evidence")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        self.descriptor = descriptor
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.descriptor is not None:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


class CognitiveAgentAdapter:
    """Create or recover one bounded Agent V1 decision for a daily bundle."""

    def __init__(
        self,
        vault: Path,
        *,
        bundle_store: CognitiveBundleStore | None = None,
        action_store: CognitiveActionStore | None = None,
        record_store: RecordStore | None = None,
        state_root: Path | None = None,
        agent_runner: Callable[[Path, str], Any] | None = None,
        profile_loader: Callable[[Path], Mapping[str, Any]] = build_agent_profile,
        gate_checker: Callable[[Path], Mapping[str, Any]] = require_agent_v1_enabled,
        clock: Callable[[], dt.datetime] = lambda: dt.datetime.now().astimezone(),
    ) -> None:
        try:
            resolved = Path(vault).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ContractError("Vault 不存在", kind="not_found") from exc
        if not resolved.is_dir():
            raise ContractError("Vault 必须是目录", kind="not_found")
        root = state_root or (resolved / ".context-agent" / "cognitive-secretary-v1")
        if not root.is_absolute():
            root = resolved / root
        root = root.parent.resolve() / root.name
        try:
            root.relative_to(resolved)
        except ValueError as exc:
            raise ContractError("state_root 必须位于 Vault 内", kind="evidence") from exc
        self.vault = resolved
        self.root = root
        self.material_dir = root / "long-term-agent-adapter" / "materials"
        self.result_dir = root / "long-term-agent-adapter" / "results"
        self.lock_path = root / "locks" / "long-term-agent-adapter.lock"
        self.bundle_store = bundle_store or CognitiveBundleStore(
            resolved, state_root=root
        )
        self.action_store = action_store or CognitiveActionStore(
            resolved, state_root=root
        )
        self.record_store = record_store or RecordStore(
            resolved, state_root=root
        )
        self.agent_runner = agent_runner
        self.profile_loader = profile_loader
        self.gate_checker = gate_checker
        self.clock = clock

    def _cognitive_authorization_refs(
        self,
        manifest: Mapping[str, Any],
        *,
        expected_action_sha256: str,
    ) -> tuple[dict[str, Any], ...] | None:
        """Freeze the active per-record authorization for a Cognitive run.

        Old standalone Adapter fixtures and legacy Agent V1 requests contain
        no receipt refs.  They keep their existing request behavior.  A real
        Cognitive Daily Bundle always carries receipt refs, so it must bind
        both the exact current Cognitive action watermark and the complete
        active receipt-head set whose current SourceRecord is no later than
        the bundle day before the Agent can see any raw source line.
        """

        raw_manifest_refs = manifest.get("receipt_refs")
        if not isinstance(raw_manifest_refs, list):
            raise ContractError("daily bundle receipt_refs 无效", kind="evidence")
        if not raw_manifest_refs:
            return None
        manifest_action_sha = manifest.get("input_hashes", {}).get(
            "user_action_watermark_sha256"
        )
        if manifest_action_sha != expected_action_sha256:
            raise ContractError("daily bundle 用户动作已变化", kind="stale")
        _, current_action_sha = self.action_store.action_watermark()
        if current_action_sha != expected_action_sha256:
            raise ContractError("长期授权用户动作已变化", kind="stale")
        cutoff = _date(manifest.get("local_date"))
        heads = self.action_store.list_receipt_heads(
            statuses=("ready", "needs_review")
        )
        authorized: list[dict[str, Any]] = []
        for receipt, receipt_ref in heads:
            if receipt.sha256 != receipt_ref.revision_sha256:
                raise ContractError(
                    "cognitive receipt head hash 不一致", kind="evidence"
                )
            current_record_ref = self.record_store.load_head_ref(
                receipt.record_ref.id
            )
            current_record = self.record_store.load_head(receipt.record_ref.id)
            if (
                self.record_store.load_head_ref(receipt.record_ref.id)
                != current_record_ref
            ):
                raise ContractError(
                    "cognitive receipt 绑定的原记录读取期间已变化",
                    kind="stale",
                )
            record_date = _date(
                current_record.get("local_date"), "source record local_date"
            )
            if record_date > cutoff:
                continue
            if current_record_ref != receipt.record_ref.to_dict():
                raise ContractError(
                    "cognitive receipt 绑定的原记录已变化", kind="stale"
                )
            if current_record.get("status") != "active":
                raise ContractError(
                    "cognitive receipt 指向非 active 原记录", kind="stale"
                )
            authorized.append(receipt_ref.to_dict())
        return tuple(authorized)

    def _assert_current_cognitive_gate(
        self,
        *,
        bundle_ref: ObjectRef,
        manifest: Mapping[str, Any],
        expected_action_sha256: str,
    ) -> tuple[dict[str, Any], ...] | None:
        """Re-read the Daily and per-record authorization commit boundary."""

        _, current_manifest = self._validate_current_bundle(bundle_ref, manifest)
        return self._cognitive_authorization_refs(
            current_manifest,
            expected_action_sha256=expected_action_sha256,
        )

    def _ensure_layout(self) -> None:
        for path in (
            self.root.parent,
            self.root,
            self.root / "locks",
            self.material_dir.parent,
            self.material_dir,
            self.result_dir,
        ):
            try:
                path.relative_to(self.vault)
            except ValueError as exc:
                raise ContractError("长期适配目录越过 Vault 边界", kind="evidence") from exc
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise ContractError("长期适配目录不安全", kind="evidence")
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            details = path.lstat()
            if (
                not stat.S_ISDIR(details.st_mode)
                or details.st_uid != os.getuid()
                or stat.S_IMODE(details.st_mode) & 0o022
            ):
                raise ContractError("长期适配目录权限不安全", kind="evidence")

    def _read_sidecar(self, path: Path, *, name: str) -> dict[str, Any]:
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ContractError(f"{name} 越过状态边界", kind="evidence") from exc
        if path.is_symlink():
            raise ContractError(f"{name} 不得是符号链接", kind="evidence")
        try:
            before = path.stat()
            value = read_json(path)
            after = path.stat()
        except OSError as exc:
            raise ContractError(f"{name} 无法安全读取", kind="evidence") from exc
        stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) & 0o077
            or any(getattr(before, field) != getattr(after, field) for field in stable)
        ):
            raise ContractError(f"{name} 不安全或读取期间变化", kind="evidence")
        return value

    def _validated_profile(self, expected_sha256: str | None = None) -> dict[str, Any]:
        profile = validate_agent_profile(
            dict(self.profile_loader(self.vault)), self.vault, verify_sources=True
        )
        if expected_sha256 is not None and profile["profile_sha256"] != expected_sha256:
            raise ContractError("Agent profile 已变化", kind="stale")
        return profile

    def _validate_current_bundle(
        self,
        bundle_ref: ObjectRef | Mapping[str, Any],
        manifest: Mapping[str, Any],
    ) -> tuple[ObjectRef, dict[str, Any]]:
        ref = _ref(bundle_ref, "bundle_ref")
        if ref.kind != "daily_bundle":
            raise ContractError("bundle_ref 必须是 daily_bundle")
        local_date = _date(manifest.get("local_date"))
        current_ref = self.bundle_store.load_day_bundle_ref(local_date)
        current_manifest = self.bundle_store.load_day_manifest(local_date)
        if current_ref is None or current_manifest is None:
            raise ContractError("当日 daily bundle 不存在", kind="not_found")
        if current_ref != ref or dict(current_manifest) != dict(manifest):
            raise ContractError("daily bundle 已变化", kind="stale")
        if (
            manifest.get("schema_version") != "1.0"
            or manifest.get("kind") != "memento_daily_bundle_revision"
            or manifest.get("status") != "committed"
            or manifest.get("bundle_id") != ref.id
            or manifest.get("revision") != ref.revision
            or persisted_sha256(manifest) != ref.revision_sha256
        ):
            raise ContractError("daily bundle ref/manifest 绑定无效", kind="evidence")
        return ref, dict(manifest)

    def _validate_formal_heads(
        self,
        memory_heads: Sequence[ObjectRef | Mapping[str, Any]],
        relation_heads: Sequence[ObjectRef | Mapping[str, Any]],
    ) -> tuple[tuple[ObjectRef, ...], tuple[ObjectRef, ...]]:
        supplied_memories = _sorted_refs(
            memory_heads, kind="reusable_memory", name="reusable_memory_heads"
        )
        supplied_relations = _sorted_refs(
            relation_heads, kind="relation", name="relation_heads"
        )
        current_memories = tuple(
            sorted(
                (
                    _head_ref(row, "reusable_memory")
                    for row in self.bundle_store.list_active_memories()
                ),
                key=lambda row: (row.id, row.revision, row.revision_sha256),
            )
        )
        current_relations = tuple(
            sorted(
                (
                    _head_ref(row, "relation")
                    for row in self.bundle_store.list_active_relations()
                ),
                key=lambda row: (row.id, row.revision, row.revision_sha256),
            )
        )
        if supplied_memories != current_memories or supplied_relations != current_relations:
            raise ContractError("正式 memory/relation head 快照已变化", kind="stale")
        return supplied_memories, supplied_relations

    @staticmethod
    def _material_refs(
        manifest: Mapping[str, Any],
        memory_heads: Sequence[ObjectRef],
        relation_heads: Sequence[ObjectRef],
    ) -> tuple[tuple[ObjectRef, ...], tuple[ObjectRef, ...]]:
        manifest_memories = _sorted_refs(
            manifest.get("memory_refs", ()),
            kind="reusable_memory",
            name="bundle.memory_refs",
        )
        manifest_relations = _sorted_refs(
            manifest.get("relation_refs", ()),
            kind="relation",
            name="bundle.relation_refs",
        )
        active_memory_by_id = {row.id: row for row in memory_heads}
        active_relation_by_id = {row.id: row for row in relation_heads}
        # A later user edit remains material under the same stable object id.
        # A tombstone is absent from the active head snapshot and contributes
        # no positive material to a new long-term inference.
        memories = tuple(
            sorted(
                (
                    active_memory_by_id[row.id]
                    for row in manifest_memories
                    if row.id in active_memory_by_id
                ),
                key=lambda row: (row.id, row.revision, row.revision_sha256),
            )
        )
        relations = tuple(
            sorted(
                (
                    active_relation_by_id[row.id]
                    for row in manifest_relations
                    if row.id in active_relation_by_id
                ),
                key=lambda row: (row.id, row.revision, row.revision_sha256),
            )
        )
        return memories, relations

    def _material_audit(
        self,
        *,
        gate_key: str,
        local_date: str,
        bundle_ref: ObjectRef,
        material_brief: Mapping[str, Any],
        material_sha256: str,
        profile_sha256: str,
        action_sha256: str,
        daily_trigger: str,
        agent_trigger: str,
        request_id: str | None,
    ) -> tuple[dict[str, Any], bool]:
        path = self.material_dir / f"{gate_key}.json"
        identity = {
            "schema_version": "1.0",
            "kind": "memento_agent_adapter_material",
            "adapter_version": ADAPTER_VERSION,
            "gate_key": gate_key,
            "local_date": local_date,
            "bundle_ref": bundle_ref.to_dict(),
            "material_brief": dict(material_brief),
            "material_sha256": material_sha256,
            "profile_sha256": profile_sha256,
            "user_action_watermark_sha256": action_sha256,
            "daily_trigger": daily_trigger,
            "agent_trigger": agent_trigger,
            "request_id": request_id,
        }
        if path.exists() or path.is_symlink():
            item = self._validate_material_sidecar(
                self._read_sidecar(path, name="material sidecar")
            )
            # scheduled and recovery deliberately share one Agent scheduled
            # request.  Keep the first daily trigger as audit provenance while
            # treating a later replay through the other entry point as the
            # same gate decision.
            comparable = {
                key: value
                for key, value in item.items()
                if key not in {"created_at", "daily_trigger"}
            }
            expected = {
                key: value for key, value in identity.items() if key != "daily_trigger"
            }
            if comparable != expected:
                raise ContractError("material sidecar 与幂等键冲突", kind="conflict")
            return item, True
        item = {**identity, "created_at": _now_text(self.clock)}
        item = self._validate_material_sidecar(item)
        atomic_write_json(path, item)
        return item, False

    @staticmethod
    def _validate_material_brief(value: Any) -> dict[str, Any]:
        item = _object(value, MATERIAL_BRIEF_FIELDS, "material brief")
        if item["adapter_version"] != ADAPTER_VERSION:
            raise ContractError("material brief adapter_version 无效")
        _date(item["local_date"], "material brief local_date")
        bundle = _ref(item["bundle_ref"], "material brief bundle_ref")
        if bundle.kind != "daily_bundle":
            raise ContractError("material brief bundle_ref 无效")
        memories = _sorted_refs(
            item["memory_refs"],
            kind="reusable_memory",
            name="material brief memory_refs",
        )
        relations = _sorted_refs(
            item["relation_refs"],
            kind="relation",
            name="material brief relation_refs",
        )
        normalized = {
            "adapter_version": item["adapter_version"],
            "local_date": item["local_date"],
            "bundle_ref": bundle.to_dict(),
            "memory_refs": [row.to_dict() for row in memories],
            "relation_refs": [row.to_dict() for row in relations],
        }
        if item != normalized:
            raise ContractError("material brief 必须按稳定顺序序列化")
        return normalized

    @staticmethod
    def _validate_material_sidecar(value: Any) -> dict[str, Any]:
        item = _object(value, MATERIAL_FIELDS, "material sidecar")
        if (
            item["schema_version"] != "1.0"
            or item["kind"] != "memento_agent_adapter_material"
            or item["adapter_version"] != ADAPTER_VERSION
        ):
            raise ContractError("material sidecar 版本无效")
        if not isinstance(item["gate_key"], str) or not GATE_RE.fullmatch(item["gate_key"]):
            raise ContractError("material sidecar gate_key 无效")
        _date(item["local_date"])
        _ref(item["bundle_ref"], "material.bundle_ref")
        item["material_brief"] = CognitiveAgentAdapter._validate_material_brief(
            item["material_brief"]
        )
        material_sha = _sha(item["material_sha256"], "material_sha256")
        if material_sha != sha256_bytes(
            canonical_json(item["material_brief"]).encode("utf-8")
        ):
            raise ContractError("material_sha256 与 material brief 不一致")
        _sha(item["profile_sha256"], "profile_sha256")
        _sha(item["user_action_watermark_sha256"], "user_action_watermark_sha256")
        if item["daily_trigger"] not in {"manual", "scheduled", "recovery"}:
            raise ContractError("material daily_trigger 无效")
        if item["agent_trigger"] not in {"manual", "scheduled"}:
            raise ContractError("material agent_trigger 无效")
        if item["request_id"] is not None and (
            not isinstance(item["request_id"], str)
            or not REQUEST_RE.fullmatch(item["request_id"])
        ):
            raise ContractError("material request_id 无效")
        if not isinstance(item["created_at"], str):
            raise ContractError("material created_at 无效")
        return item

    @staticmethod
    def _validate_result_sidecar(value: Any) -> dict[str, Any]:
        item = _object(value, RESULT_FIELDS, "Agent result sidecar")
        if (
            item["schema_version"] != "1.0"
            or item["kind"] != "memento_agent_adapter_result"
            or item["adapter_version"] != ADAPTER_VERSION
        ):
            raise ContractError("Agent result sidecar 版本无效")
        if not isinstance(item["gate_key"], str) or not GATE_RE.fullmatch(item["gate_key"]):
            raise ContractError("Agent result gate_key 无效")
        if not isinstance(item["request_id"], str) or not REQUEST_RE.fullmatch(item["request_id"]):
            raise ContractError("Agent result request_id 无效")
        _sha(item["post_profile_sha256"], "post_profile_sha256")
        item["agent_result_ref"] = validate_agent_result_ref(item["agent_result_ref"])
        if not isinstance(item["completed_at"], str):
            raise ContractError("Agent result completed_at 无效")
        return item

    def _request_bound_elsewhere(self, request_id: str, gate_key: str) -> bool:
        for path in sorted(self.material_dir.glob("ltg_*.json")):
            item = self._validate_material_sidecar(
                self._read_sidecar(path, name="material sidecar")
            )
            if item["request_id"] == request_id and item["gate_key"] != gate_key:
                return True
        return False

    def _recover_prior_terminal(
        self,
        *,
        local_date: str,
        bundle_ref: ObjectRef,
        material_sha256: str,
        action_sha256: str,
        agent_trigger: str,
        cognitive_receipt_refs: tuple[dict[str, Any], ...] | None,
    ) -> tuple[str, str, dict[str, Any], str] | None:
        """Recover a terminal whose own commit changed the Agent profile.

        Agent V1 commits its memory/profile before this adapter can persist its
        bounded result sidecar.  A crash in that small interval means the
        caller correctly observes the *post*-Agent profile on replay, while
        the immutable material audit still contains the pre-Agent profile.
        Matching the exact bundle/material/action/trigger and then strictly
        validating the response/run against the current profile closes that
        interval without creating a second request.
        """

        matches: list[tuple[str, str, dict[str, Any], str]] = []
        expected_bundle = bundle_ref.to_dict()
        for path in sorted(self.material_dir.glob("ltg_*.json")):
            audit = self._validate_material_sidecar(
                self._read_sidecar(path, name="material sidecar")
            )
            request_id = audit["request_id"]
            if (
                request_id is None
                or audit["local_date"] != local_date
                or audit["bundle_ref"] != expected_bundle
                or audit["material_sha256"] != material_sha256
                or audit["user_action_watermark_sha256"] != action_sha256
                or audit["agent_trigger"] != agent_trigger
            ):
                continue
            authorization = load_cognitive_authorization(self.vault, request_id)
            if cognitive_receipt_refs is None:
                if authorization is not None:
                    raise ContractError(
                        "旧 Agent 请求意外携带认知授权", kind="evidence"
                    )
            elif (
                authorization is None
                or authorization["material_gate_key"] != audit["gate_key"]
                or authorization["material_sha256"] != material_sha256
                or authorization["user_action_watermark_sha256"] != action_sha256
                or authorization["receipt_refs"] != list(cognitive_receipt_refs)
            ):
                raise ContractError("Agent 认知授权与恢复材料不一致", kind="stale")
            try:
                terminal = self._terminal_result(
                    request_id, as_of=local_date, trigger=agent_trigger
                )
            except ContractError as exc:
                # A valid older terminal whose result profile is no longer
                # current belongs to an earlier profile gate.  Other binding
                # failures are evidence problems and must fail closed.
                if exc.kind == "stale":
                    continue
                raise
            if terminal is None:
                continue
            result, post_profile_sha = terminal
            persisted_path = self.result_dir / f"{audit['gate_key']}.json"
            if persisted_path.exists() or persisted_path.is_symlink():
                persisted = self._validate_result_sidecar(
                    self._read_sidecar(
                        persisted_path, name="Agent result sidecar"
                    )
                )
                if (
                    persisted["request_id"] != request_id
                    or persisted["agent_result_ref"] != result
                    or persisted["post_profile_sha256"] != post_profile_sha
                ):
                    raise ContractError(
                        "Agent result sidecar 与终态不一致", kind="evidence"
                    )
            matches.append(
                (audit["gate_key"], request_id, result, post_profile_sha)
            )
        if not matches:
            return None
        if len(matches) != 1:
            raise ContractError("同一长期材料存在多个当前终态", kind="conflict")
        # Do not publish the adapter result here.  The caller must first
        # re-read the Cognitive bundle/action authorization boundary.  This
        # closes the crash-recovery race in which a terminal Agent result is
        # found just as the user submits a newer Cognitive action.
        return matches[0]

    def _terminal_result(
        self, request_id: str, *, as_of: str, trigger: str
    ) -> tuple[dict[str, Any], str] | None:
        output = response_path(self.vault, request_id)
        if output.is_symlink():
            raise ContractError("Agent response 不得是符号链接", kind="evidence")
        if not output.exists():
            return None
        request, _, request_sha = load_agent_request(self.vault, request_id)
        if request["as_of"] != as_of or request["trigger"] != trigger:
            raise ContractError("Agent request 绑定不一致", kind="conflict")
        response = validate_agent_response(read_json(output), self.vault)
        run_id = make_run_id(request_id)
        run = validate_agent_run(read_json(run_path(self.vault, run_id)))
        response_sha = sha256_bytes(canonical_json(response).encode("utf-8"))
        if (
            response["request_id"] != request_id
            or response["request_sha256"] != request_sha
            or response["run_id"] != run_id
            or run["request_id"] != request_id
            or run["request_sha256"] != request_sha
            or run["run_id"] != run_id
            or run["status"] != response["status"]
            or run["completed_at"] is None
            or run["response_sha256"] != response_sha
        ):
            raise ContractError("Agent response/run 绑定不一致", kind="evidence")
        profile = self._validated_profile()
        if profile["profile_sha256"] != response["result_profile_sha256"]:
            raise ContractError("Agent response/profile 绑定不一致", kind="stale")
        memory_ref = None
        if response["status"] == "updated":
            memory = response["memory"]
            memory_ref = ObjectRef(
                "understanding",
                memory["memory_id"],
                memory["revision"],
                memory["revision_sha256"],
            )
            matches = [
                row
                for row in profile["memories"]
                if row["memory_id"] == memory_ref.id
                and row["revision"] == memory_ref.revision
                and row["revision_sha256"] == memory_ref.revision_sha256
            ]
            if len(matches) != 1:
                raise ContractError("updated Agent memory 不在当前 profile", kind="evidence")
        result = validate_agent_result_ref(
            {
                "request_id": request_id,
                "run_id": run_id,
                "response_sha256": response_sha,
                "status": response["status"],
                "memory_ref": None if memory_ref is None else memory_ref.to_dict(),
            }
        )
        return result, profile["profile_sha256"]

    def _persist_result(
        self,
        *,
        gate_key: str,
        request_id: str,
        result: Mapping[str, Any],
        post_profile_sha256: str,
    ) -> dict[str, Any]:
        path = self.result_dir / f"{gate_key}.json"
        if path.exists() or path.is_symlink():
            item = self._validate_result_sidecar(
                self._read_sidecar(path, name="Agent result sidecar")
            )
            if (
                item["request_id"] != request_id
                or item["post_profile_sha256"] != post_profile_sha256
                or item["agent_result_ref"] != dict(result)
            ):
                raise ContractError("Agent result sidecar 冲突", kind="conflict")
            return item
        item = self._validate_result_sidecar(
            {
                "schema_version": "1.0",
                "kind": "memento_agent_adapter_result",
                "adapter_version": ADAPTER_VERSION,
                "gate_key": gate_key,
                "completed_at": _now_text(self.clock),
                "request_id": request_id,
                "post_profile_sha256": post_profile_sha256,
                "agent_result_ref": dict(result),
            }
        )
        atomic_write_json(path, item)
        return item

    @staticmethod
    def _warning(
        material_sha256: str,
        request_id: str | None,
        *,
        request_created: bool = False,
        runner_called: bool = False,
        result: Mapping[str, Any] | None = None,
    ) -> LongTermAdapterResult:
        return LongTermAdapterResult(
            status="warning",
            material_sha256=material_sha256,
            request_id=request_id,
            request_created=request_created,
            runner_called=runner_called,
            agent_result_ref=result,
            warning="long_term_failed",
        )

    def process(
        self,
        *,
        bundle_ref: ObjectRef | Mapping[str, Any],
        manifest: Mapping[str, Any],
        reusable_memory_heads: Sequence[ObjectRef | Mapping[str, Any]],
        relation_heads: Sequence[ObjectRef | Mapping[str, Any]],
        profile_sha256: str,
        user_action_watermark_sha256: str,
        trigger: str,
    ) -> LongTermAdapterResult:
        """Gate, create/recover, and strictly validate one Agent V1 result.

        ``reusable_memory_heads`` and ``relation_heads`` are the complete
        current active formal head snapshots.  Supplying a partial or stale
        snapshot fails closed before any Agent request can be created.
        """

        _sha(profile_sha256, "profile_sha256")
        action_sha = _sha(
            user_action_watermark_sha256, "user_action_watermark_sha256"
        )
        if trigger not in {"manual", "scheduled", "recovery"}:
            raise ContractError("daily trigger 无效")
        self._ensure_layout()
        with _AdapterLock(self.lock_path):
            current_bundle_ref, current_manifest = self._validate_current_bundle(
                bundle_ref, manifest
            )
            cognitive_receipt_refs = self._cognitive_authorization_refs(
                current_manifest,
                expected_action_sha256=action_sha,
            )
            local_date = _date(current_manifest["local_date"])
            self._validated_profile(profile_sha256)
            memories, relations = self._validate_formal_heads(
                reusable_memory_heads, relation_heads
            )
            material_memories, material_relations = self._material_refs(
                current_manifest, memories, relations
            )
            agent_trigger = "manual" if trigger == "manual" else "scheduled"
            material_payload = {
                "adapter_version": ADAPTER_VERSION,
                "local_date": local_date,
                "bundle_ref": current_bundle_ref.to_dict(),
                "memory_refs": [row.to_dict() for row in material_memories],
                "relation_refs": [row.to_dict() for row in material_relations],
            }
            material_sha = sha256_bytes(
                canonical_json(material_payload).encode("utf-8")
            )
            gate_key = "ltg_" + sha256_bytes(
                canonical_json(
                    {
                        "material_sha256": material_sha,
                        "profile_sha256": profile_sha256,
                        "user_action_watermark_sha256": action_sha,
                        "agent_trigger": agent_trigger,
                    }
                ).encode("utf-8")
            )[:24]

            existing_result = current_manifest.get("long_term_result_ref")
            if existing_result is not None:
                result = validate_agent_result_ref(existing_result)
                return LongTermAdapterResult(
                    status="already_linked",
                    material_sha256=material_sha,
                    request_id=result["request_id"],
                    request_created=False,
                    runner_called=False,
                    agent_result_ref=result,
                    warning=(
                        "long_term_failed"
                        if result["status"]
                        in {"budget_exhausted", "stale", "error"}
                        else None
                    ),
                )

            try:
                prior_terminal = self._recover_prior_terminal(
                    local_date=local_date,
                    bundle_ref=current_bundle_ref,
                    material_sha256=material_sha,
                    action_sha256=action_sha,
                    agent_trigger=agent_trigger,
                    cognitive_receipt_refs=cognitive_receipt_refs,
                )
            except Exception:
                return self._warning(material_sha, None)
            if prior_terminal is not None:
                (
                    prior_gate_key,
                    prior_request_id,
                    prior_result,
                    prior_profile_sha,
                ) = prior_terminal
                try:
                    self._assert_current_cognitive_gate(
                        bundle_ref=current_bundle_ref,
                        manifest=current_manifest,
                        expected_action_sha256=action_sha,
                    )
                    self._persist_result(
                        gate_key=prior_gate_key,
                        request_id=prior_request_id,
                        result=prior_result,
                        post_profile_sha256=prior_profile_sha,
                    )
                except Exception:
                    return self._warning(material_sha, prior_request_id)
                return LongTermAdapterResult(
                    status="recovered",
                    material_sha256=material_sha,
                    request_id=prior_request_id,
                    request_created=False,
                    runner_called=False,
                    agent_result_ref=prior_result,
                    warning=(
                        "long_term_failed"
                        if prior_result["status"]
                        in {"budget_exhausted", "stale", "error"}
                        else None
                    ),
                )

            if not material_memories and not material_relations:
                self._material_audit(
                    gate_key=gate_key,
                    local_date=local_date,
                    bundle_ref=current_bundle_ref,
                    material_brief=material_payload,
                    material_sha256=material_sha,
                    profile_sha256=profile_sha256,
                    action_sha256=action_sha,
                    daily_trigger=trigger,
                    agent_trigger=agent_trigger,
                    request_id=None,
                )
                return LongTermAdapterResult(
                    status="no_material",
                    material_sha256=material_sha,
                    request_id=None,
                    request_created=False,
                    runner_called=False,
                    agent_result_ref=None,
                    warning=None,
                )

            # A calendar date is not a durable request identity: the same day
            # can acquire a new committed bundle, profile revision, or user
            # action watermark after an earlier scheduled run. Bind the
            # request to the exact material gate. Recovery derives the same
            # gate and therefore reuses the same request, while a genuine
            # same-day material change receives a fresh request ID.
            request_id = (
                scheduled_agent_request_id(local_date, material_key=gate_key)
                if agent_trigger == "scheduled"
                else "arq_"
                + sha256_bytes(
                    f"cognitive-agent-adapter-manual-v1:{gate_key}".encode(
                        "utf-8"
                    )
                )[:24]
            )
            if self._request_bound_elsewhere(request_id, gate_key):
                return self._warning(material_sha, request_id)
            _, audit_existed = self._material_audit(
                gate_key=gate_key,
                local_date=local_date,
                bundle_ref=current_bundle_ref,
                material_brief=material_payload,
                material_sha256=material_sha,
                profile_sha256=profile_sha256,
                action_sha256=action_sha,
                daily_trigger=trigger,
                agent_trigger=agent_trigger,
                request_id=request_id,
            )
            if cognitive_receipt_refs is not None:
                try:
                    persist_cognitive_authorization(
                        self.vault,
                        request_id=request_id,
                        material_gate_key=gate_key,
                        material_sha256=material_sha,
                        user_action_watermark_sha256=action_sha,
                        receipt_refs=cognitive_receipt_refs,
                    )
                except Exception:
                    return self._warning(material_sha, request_id)

            result_path = self.result_dir / f"{gate_key}.json"
            if result_path.exists() or result_path.is_symlink():
                try:
                    self._assert_current_cognitive_gate(
                        bundle_ref=current_bundle_ref,
                        manifest=current_manifest,
                        expected_action_sha256=action_sha,
                    )
                    persisted = self._validate_result_sidecar(
                        self._read_sidecar(
                            result_path, name="Agent result sidecar"
                        )
                    )
                    terminal = self._terminal_result(
                        request_id, as_of=local_date, trigger=agent_trigger
                    )
                    if terminal is None:
                        raise ContractError("Agent result 缺少终态文件", kind="evidence")
                    result, post_profile_sha = terminal
                    if (
                        persisted["request_id"] != request_id
                        or persisted["agent_result_ref"] != result
                        or persisted["post_profile_sha256"] != post_profile_sha
                    ):
                        raise ContractError("Agent result sidecar 与终态不一致", kind="evidence")
                    return LongTermAdapterResult(
                        status="recovered",
                        material_sha256=material_sha,
                        request_id=request_id,
                        request_created=False,
                        runner_called=False,
                        agent_result_ref=result,
                        warning=(
                            "long_term_failed"
                            if result["status"]
                            in {"budget_exhausted", "stale", "error"}
                            else None
                        ),
                    )
                except Exception:
                    return self._warning(material_sha, request_id)

            request_file = request_path(self.vault, request_id)
            request_created = False
            if request_file.exists() or request_file.is_symlink():
                if not audit_existed:
                    # A pre-existing request without the adapter audit cannot
                    # be retroactively claimed as this material decision.
                    return self._warning(material_sha, request_id)
                try:
                    request, _, _ = load_agent_request(self.vault, request_id)
                    if (
                        request["as_of"] != local_date
                        or request["trigger"] != agent_trigger
                    ):
                        raise ContractError("Agent request 已绑定其他输入", kind="conflict")
                except Exception:
                    return self._warning(material_sha, request_id)
            else:
                try:
                    self.gate_checker(self.vault)
                    create_agent_request(
                        self.vault,
                        as_of=local_date,
                        request_id=request_id,
                        created_at=_now_text(self.clock),
                        trigger=agent_trigger,
                        scheduled_material_key=(
                            gate_key if agent_trigger == "scheduled" else None
                        ),
                    )
                    request_created = True
                except Exception:
                    return self._warning(material_sha, request_id)

            try:
                terminal = self._terminal_result(
                    request_id, as_of=local_date, trigger=agent_trigger
                )
            except Exception:
                return self._warning(
                    material_sha,
                    request_id,
                    request_created=request_created,
                )
            if terminal is not None:
                result, post_profile_sha = terminal
                try:
                    self._assert_current_cognitive_gate(
                        bundle_ref=current_bundle_ref,
                        manifest=current_manifest,
                        expected_action_sha256=action_sha,
                    )
                    self._persist_result(
                        gate_key=gate_key,
                        request_id=request_id,
                        result=result,
                        post_profile_sha256=post_profile_sha,
                    )
                except Exception:
                    return self._warning(
                        material_sha,
                        request_id,
                        request_created=request_created,
                        result=result,
                    )
                return LongTermAdapterResult(
                    status="recovered",
                    material_sha256=material_sha,
                    request_id=request_id,
                    request_created=request_created,
                    runner_called=False,
                    agent_result_ref=result,
                    warning=(
                        "long_term_failed"
                        if result["status"]
                        in {"budget_exhausted", "stale", "error"}
                        else None
                    ),
                )

            if self.agent_runner is None:
                return self._warning(
                    material_sha,
                    request_id,
                    request_created=request_created,
                )
            try:
                # Only the existing request identity crosses this boundary.
                # No Daily Summary, material brief, or formal-memory text is
                # passed to the Agent runner.
                self.agent_runner(self.vault, request_id)
                terminal = self._terminal_result(
                    request_id, as_of=local_date, trigger=agent_trigger
                )
                if terminal is None:
                    raise ContractError("Agent runner 未产生合法终态", kind="runtime")
                result, post_profile_sha = terminal
                self._assert_current_cognitive_gate(
                    bundle_ref=current_bundle_ref,
                    manifest=current_manifest,
                    expected_action_sha256=action_sha,
                )
                self._persist_result(
                    gate_key=gate_key,
                    request_id=request_id,
                    result=result,
                    post_profile_sha256=post_profile_sha,
                )
            except Exception:
                return self._warning(
                    material_sha,
                    request_id,
                    request_created=request_created,
                    runner_called=True,
                )
            return LongTermAdapterResult(
                status="completed",
                material_sha256=material_sha,
                request_id=request_id,
                request_created=request_created,
                runner_called=True,
                agent_result_ref=result,
                warning=(
                    "long_term_failed"
                    if result["status"]
                    in {"budget_exhausted", "stale", "error"}
                    else None
                ),
            )


__all__ = [
    "ADAPTER_VERSION",
    "AgentResultRef",
    "CognitiveAgentAdapter",
    "LongTermAdapterResult",
    "validate_agent_result_ref",
]


# Runtime type alias kept last so it cannot be confused with a second storage
# model.  The persisted representation is always the exact five-field mapping.
AgentResultRef = Mapping[str, Any]
