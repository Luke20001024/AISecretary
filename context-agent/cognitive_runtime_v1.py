"""Durable, bounded orchestration for Cognitive Secretary MVP agents.

This module owns the paid-provider boundary for the Record Interpreter and the
Daily Integrator.  It deliberately does not own capture, record reconciliation,
daily-bundle committing, reusable-memory committing, relation committing, or
homepage projection.

The important durability rule is at-most-once per request attempt:

* persist ``provider_attempt_started`` before entering the provider boundary;
* validate the returned action before persistence, then store only its
  canonical JSON form and usage in an immutable completion sidecar;
* only then resolve the attempt marker in the mutable run checkpoint;
* recover a sidecar without another provider call, while a marker without a
  sidecar becomes ``unknown_attempt`` and is never retried for that request.

Only strict JSON actions are persisted.  Provider requests and hidden reasoning
are never written to the run files.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Callable, ContextManager, Mapping, Protocol, Sequence, Tuple, Union

from cognitive_prompts_v1 import (
    DAILY_INTEGRATOR_CONTRACT_VERSION,
    RECORD_INTERPRETER_CONTRACT_VERSION,
    DailyIntegratorBudget,
    RecordInterpreterBudget,
    build_daily_integrator_messages,
    build_record_interpreter_messages,
    make_daily_integrator_policy_sha256,
    make_record_interpreter_policy_sha256,
    parse_daily_integrator_action,
    parse_record_interpreter_action,
)
from cognitive_store_v1 import RecordStore
from cognitive_v1 import (
    COGNITIVE_SCHEMA_VERSION,
    InterpretationReceiptRevision,
    ObjectRef,
    SourceSpan,
    make_receipt_id,
    persisted_json_bytes,
    persisted_sha256,
    validate_interpretation_receipt_transition,
)
from core import (
    ContractError,
    calculate_cost,
    canonical_json,
    normalize_usage,
    pricing_for_model,
    provider_call_lock,
    sha256_bytes,
    usage_is_missing,
)
ZERO_SHA256 = "0" * 64
MAX_JSON_BYTES = 1_000_000
MAX_COMPLETION_BYTES = 200_000
MAX_CONTEXT_BYTES = 100_000
NORMALIZED_USAGE_FIELDS = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "reasoning_tokens",
    }
)
RUN_USAGE_FIELDS = frozenset(
    {
        "model_calls",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "reasoning_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "usage_missing",
        "cost_usd",
        "cost_complete",
    }
)
REQUEST_ID_RE = re.compile(r"^(?:ireq|dreq)_[0-9a-f]{24}$")
RUN_ID_RE = re.compile(r"^(?:irun|drun)_[0-9a-f]{24}$")
RUN_KEY_RE = re.compile(r"^(?:irk|drk)_[0-9a-f]{24}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_KNOWN_INVALID_SCHEMA_RETRY_NONCE_PREFIX = "record-worker-known-invalid-v1:"


class CompletionProvider(Protocol):
    """The only provider capability this runtime accepts."""

    def complete(self, messages: Sequence[Mapping[str, str]]) -> Any:
        ...


LockFactory = Callable[[Path], ContextManager[None]]
ObjectResolver = Callable[[ObjectRef], Union[Mapping[str, Any], Tuple[Mapping[str, Any], str]]]
InspectMemory = Callable[[ObjectRef], Mapping[str, Any]]
SearchHistory = Callable[[str, Union[str, None], Union[str, None], int], Sequence[Union[SourceSpan, Mapping[str, Any]]]]
UsageAuditor = Callable[..., Any]


def _id24(prefix: str, namespace: str, value: Mapping[str, Any]) -> str:
    digest = sha256_bytes(
        canonical_json({"namespace": namespace, **dict(value)}).encode("utf-8")
    )
    return prefix + digest[:24]


def _now_text(clock: Callable[[], dt.datetime]) -> str:
    value = clock()
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise ContractError("clock 必须返回带时区的 datetime", kind="runtime")
    return value.isoformat(timespec="seconds")


def _json_only(value: Any, name: str, *, maximum: int = MAX_CONTEXT_BYTES) -> Any:
    try:
        encoded = canonical_json(value).encode("utf-8")
        decoded = json.loads(encoded.decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ContractError(f"{name} 必须只含 JSON 值") from exc
    if len(encoded) > maximum:
        raise ContractError(f"{name} 超出大小限制", kind="budget")
    return decoded


def _sha(value: str, name: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ContractError(f"{name} 必须是 SHA-256")
    return value


def make_evidence_ref_id(span: SourceSpan | Mapping[str, Any]) -> str:
    """Bind one opaque prompt ref to an exact validated source span."""

    item = span if isinstance(span, SourceSpan) else SourceSpan.from_dict(span)
    digest = sha256_bytes(canonical_json(item.to_dict()).encode("utf-8"))
    return "eref_" + digest[:16]


def make_object_ref_id(ref: ObjectRef | Mapping[str, Any]) -> str:
    """Bind one opaque prompt ref to one exact immutable object revision."""

    item = ref if isinstance(ref, ObjectRef) else ObjectRef.from_dict(ref)
    digest = sha256_bytes(canonical_json(item.to_dict()).encode("utf-8"))
    return "oref_" + digest[:16]


class _RunLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.descriptor: int | None = None

    def __enter__(self) -> "_RunLock":
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise ContractError("无法安全打开 runtime 锁", kind="runtime") from exc
        locked = False
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_nlink != 1
                or details.st_uid != os.getuid()
                or stat.S_IMODE(details.st_mode) & 0o077
            ):
                raise ContractError("runtime 锁文件不安全", kind="evidence")
            try:
                current = os.stat(self.path, follow_symlinks=False)
            except OSError as exc:
                raise ContractError("runtime 锁无法校验", kind="evidence") from exc
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or current.st_uid != os.getuid()
                or stat.S_IMODE(current.st_mode) & 0o077
                or current.st_dev != details.st_dev
                or current.st_ino != details.st_ino
            ):
                raise ContractError("runtime 锁在打开期间变化", kind="evidence")

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
            locked_details = os.fstat(descriptor)
            try:
                locked_path = os.stat(self.path, follow_symlinks=False)
            except OSError as exc:
                raise ContractError("runtime 锁在等待期间变化", kind="evidence") from exc
            if (
                not stat.S_ISREG(locked_details.st_mode)
                or locked_details.st_nlink != 1
                or locked_details.st_uid != os.getuid()
                or stat.S_IMODE(locked_details.st_mode) & 0o077
                or not stat.S_ISREG(locked_path.st_mode)
                or locked_path.st_nlink != 1
                or locked_path.st_uid != os.getuid()
                or stat.S_IMODE(locked_path.st_mode) & 0o077
                or locked_path.st_dev != locked_details.st_dev
                or locked_path.st_ino != locked_details.st_ino
            ):
                raise ContractError("runtime 锁在等待期间变化", kind="evidence")
        except BaseException:
            if locked:
                with contextlib.suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            raise
        self.descriptor = descriptor
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.descriptor is not None:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


class _DurableFiles:
    def __init__(self, vault: Path, state_root: Path | None) -> None:
        try:
            resolved_vault = vault.expanduser().resolve(strict=True)
        except OSError as exc:
            raise ContractError("Vault 不存在", kind="not_found") from exc
        if not resolved_vault.is_dir():
            raise ContractError("Vault 必须是目录", kind="not_found")
        root = state_root or (
            resolved_vault / ".context-agent" / "cognitive-secretary-v1"
        )
        if not root.is_absolute():
            root = resolved_vault / root
        candidate = root.parent.resolve() / root.name
        try:
            candidate.relative_to(resolved_vault)
        except ValueError as exc:
            raise ContractError("state_root 必须位于 Vault 内", kind="evidence") from exc
        self.vault = resolved_vault
        self.root = candidate
        self.interpretation_requests = self.root / "interpretation-requests"
        self.interpretation_runs = self.root / "interpretation-runs"
        self.receipts = self.root / "receipts"
        self.daily_requests = self.root / "daily-requests"
        self.daily_runs = self.root / "daily-runs"
        self.daily_staging = self.root / "daily-bundles" / "staging"
        self.locks = self.root / "locks"

    def ensure_layout(self) -> None:
        for path in (
            self.root.parent,
            self.root,
            self.interpretation_requests,
            self.interpretation_runs,
            self.receipts,
            self.daily_requests,
            self.daily_runs,
            self.daily_staging,
            self.locks,
        ):
            self._secure_directory(path)

    def _secure_directory(self, path: Path) -> None:
        try:
            path.relative_to(self.vault)
        except ValueError as exc:
            raise ContractError("runtime 目录越过 Vault 边界", kind="evidence") from exc
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ContractError(f"runtime 路径不安全：{path}", kind="evidence")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        details = path.lstat()
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) & 0o022
        ):
            raise ContractError(f"runtime 目录权限不安全：{path}", kind="evidence")

    def lock(self, name: str) -> _RunLock:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", name):
            raise ContractError("runtime lock 名称无效")
        self.ensure_layout()
        return _RunLock(self.locks / f"{name}.lock")

    def read_json(self, path: Path, *, name: str, maximum: int = MAX_JSON_BYTES) -> dict[str, Any]:
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ContractError(f"{name} 越过 runtime 边界", kind="evidence") from exc
        if path.is_symlink():
            raise ContractError(f"{name} 不得是符号链接", kind="evidence")
        try:
            details = path.stat()
            raw = path.read_bytes()
            after = path.stat()
        except FileNotFoundError as exc:
            raise ContractError(f"{name} 不存在", kind="not_found") from exc
        except OSError as exc:
            raise ContractError(f"{name} 不可读", kind="runtime") from exc
        stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or details.st_uid != os.getuid()
            or len(raw) > maximum
            or any(getattr(details, field) != getattr(after, field) for field in stable)
        ):
            raise ContractError(f"{name} 文件不安全或读取期间变化", kind="evidence")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError(f"{name} JSON 无法解析") from exc
        if not isinstance(value, dict):
            raise ContractError(f"{name} 必须是 JSON object")
        if raw != persisted_json_bytes(value):
            raise ContractError(f"{name} 字节不符合持久化合同", kind="evidence")
        return value

    def write_immutable(self, path: Path, value: Mapping[str, Any]) -> str:
        data = persisted_json_bytes(value)
        self._write(path, data, immutable=True)
        return sha256_bytes(data)

    def write_mutable(self, path: Path, value: Mapping[str, Any]) -> str:
        data = persisted_json_bytes(value)
        self._write(path, data, immutable=False)
        return sha256_bytes(data)

    def _write(self, path: Path, data: bytes, *, immutable: bool) -> None:
        self.ensure_layout()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ContractError("runtime 写入越过边界", kind="evidence") from exc
        self._secure_directory(path.parent)
        if path.exists() or path.is_symlink():
            if path.is_symlink():
                raise ContractError("runtime 目标不得是符号链接", kind="evidence")
            details = path.stat()
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_nlink != 1
                or details.st_uid != os.getuid()
            ):
                raise ContractError("runtime 目标文件不安全", kind="evidence")
            if immutable:
                if path.read_bytes() != data:
                    raise ContractError("不可变 runtime 对象冲突", kind="conflict")
                return
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                descriptor = -1
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)


def _default_clock() -> dt.datetime:
    return dt.datetime.now().astimezone()


class CognitiveRuntime:
    """MVP durable runtime for per-record and daily cognitive agents."""

    def __init__(
        self,
        vault: Path,
        provider: CompletionProvider,
        *,
        state_root: Path | None = None,
        provider_name: str = "deepseek-agentic-workflow",
        model: str = "deepseek-v4-pro",
        thinking: str = "disabled",
        reasoning_effort: str | None = None,
        record_max_tokens: int = 2400,
        daily_max_tokens: int = 3600,
        record_budget: RecordInterpreterBudget = RecordInterpreterBudget(),
        daily_budget: DailyIntegratorBudget = DailyIntegratorBudget(),
        lock_factory: LockFactory | None = provider_call_lock,
        object_resolver: ObjectResolver | None = None,
        usage_auditor: UsageAuditor | None = None,
        clock: Callable[[], dt.datetime] = _default_clock,
    ) -> None:
        self.files = _DurableFiles(Path(vault), state_root)
        self.files.ensure_layout()
        self.vault = self.files.vault
        self.store = RecordStore(self.vault, state_root=self.files.root)
        self.provider = provider
        self.provider_name = provider_name
        self.model = model
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort
        self.record_max_tokens = record_max_tokens
        self.daily_max_tokens = daily_max_tokens
        self.record_budget = record_budget
        self.daily_budget = daily_budget
        self.record_budget.validate()
        self.daily_budget.validate()
        self.lock_factory = lock_factory
        self.object_resolver = object_resolver
        self.usage_auditor = usage_auditor
        self.clock = clock
        self.record_policy_sha256 = make_record_interpreter_policy_sha256(
            provider=provider_name,
            model=model,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
            max_tokens=record_max_tokens,
            budget=record_budget,
        )
        self.daily_policy_sha256 = make_daily_integrator_policy_sha256(
            provider=provider_name,
            model=model,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
            max_tokens=daily_max_tokens,
            budget=daily_budget,
        )

    # ------------------------------------------------------------------
    # Immutable requests and identifiers

    def create_interpretation_request(
        self,
        record_id: str,
        *,
        trigger: str = "capture",
        feedback_watermark_sha256: str | None = None,
        request_nonce: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        if trigger not in {"capture", "reconcile", "retry", "source_changed"}:
            raise ContractError("interpretation trigger 无效")
        if feedback_watermark_sha256 is None:
            # The low-level public API must preserve the same user-priority
            # boundary as RecordWorker/Pipeline callers.  Bind an omitted
            # watermark to the canonical current action set instead of using
            # a zero sentinel that could bypass the commit-time guard.
            from cognitive_actions_v1 import CognitiveActionStore

            _, feedback = CognitiveActionStore(
                self.vault, state_root=self.files.root
            ).action_watermark()
        else:
            feedback = _sha(
                feedback_watermark_sha256, "feedback_watermark_sha256"
            )
        record_ref = ObjectRef.from_dict(self.store.load_head_ref(record_id))
        identity: dict[str, Any] = {
            "record_ref": record_ref.to_dict(),
            "feedback_watermark_sha256": feedback,
            "contract_version": RECORD_INTERPRETER_CONTRACT_VERSION,
            "trigger": trigger,
        }
        if request_nonce is not None:
            if not isinstance(request_nonce, str) or not request_nonce.strip() or len(request_nonce) > 256:
                raise ContractError("request_nonce 无效")
            identity["request_nonce"] = request_nonce
        request_id = _id24("ireq_", "interpretation-request-v1", identity)
        path = self.files.interpretation_requests / f"{request_id}.json"
        with self.files.lock(request_id):
            if path.exists():
                return self._load_interpretation_request(request_id)[0]
            request = {
                "schema_version": COGNITIVE_SCHEMA_VERSION,
                "kind": "memento_interpretation_request",
                "id": request_id,
                "status": "pending",
                "created_at": created_at or _now_text(self.clock),
                "trigger": trigger,
                "record_ref": record_ref.to_dict(),
                "contract_version": RECORD_INTERPRETER_CONTRACT_VERSION,
                "feedback_watermark_sha256": feedback,
            }
            self._validate_interpretation_request(request)
            self.files.write_immutable(path, request)
            return request

    def create_daily_request(
        self,
        local_date: str,
        *,
        trigger: str = "scheduled",
        request_nonce: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        try:
            dt.date.fromisoformat(local_date)
        except (TypeError, ValueError) as exc:
            raise ContractError("local_date 必须是有效 YYYY-MM-DD") from exc
        if trigger not in {"manual", "scheduled", "recovery"}:
            raise ContractError("daily trigger 无效")
        identity: dict[str, Any] = {
            "local_date": local_date,
            "contract_version": DAILY_INTEGRATOR_CONTRACT_VERSION,
            "trigger": trigger,
        }
        if request_nonce is not None:
            if not isinstance(request_nonce, str) or not request_nonce.strip() or len(request_nonce) > 256:
                raise ContractError("request_nonce 无效")
            identity["request_nonce"] = request_nonce
        request_id = _id24("dreq_", "daily-request-v1", identity)
        path = self.files.daily_requests / f"{request_id}.json"
        with self.files.lock(request_id):
            if path.exists():
                return self._load_daily_request(request_id)[0]
            request = {
                "schema_version": COGNITIVE_SCHEMA_VERSION,
                "kind": "memento_daily_integration_request",
                "id": request_id,
                "status": "pending",
                "created_at": created_at or _now_text(self.clock),
                "trigger": trigger,
                "local_date": local_date,
                "contract_version": DAILY_INTEGRATOR_CONTRACT_VERSION,
            }
            self._validate_daily_request(request)
            self.files.write_immutable(path, request)
            return request

    @staticmethod
    def _interpretation_material_identity(
        record_ref: ObjectRef,
        feedback_watermark_sha256: str,
        target_object_manifest_sha256: str,
        policy_sha256: str,
    ) -> tuple[dict[str, str], str]:
        input_hashes = {
            "record_revision_sha256": record_ref.revision_sha256,
            "feedback_watermark_sha256": feedback_watermark_sha256,
            "policy_sha256": policy_sha256,
        }
        run_key = _id24(
            "irk_",
            "interpretation-run-key-v1",
            {
                "record_id": record_ref.id,
                "record_revision": record_ref.revision,
                "target_object_manifest_sha256": target_object_manifest_sha256,
                **input_hashes,
            },
        )
        return input_hashes, run_key

    def _daily_material_identity(
        self,
        local_date: str,
        *,
        source_spans: Sequence[SourceSpan | Mapping[str, Any]],
        object_refs: Sequence[ObjectRef | Mapping[str, Any]] = (),
        receipt_refs: Sequence[ObjectRef | Mapping[str, Any]] = (),
        daily_context: Mapping[str, Any] | None = None,
        profile_sha256: str = ZERO_SHA256,
        user_action_watermark_sha256: str = ZERO_SHA256,
    ) -> dict[str, Any]:
        """Materialize the sole Daily Integrator input identity.

        Both the paid execution path and read-only terminal inspection use this
        method.  Keeping catalog construction and run-key derivation here
        prevents a completion reader from accepting a looser approximation of
        the material that authorized the Provider call.
        """

        try:
            parsed_date = dt.date.fromisoformat(local_date)
        except (TypeError, ValueError) as exc:
            raise ContractError("local_date 必须是有效 YYYY-MM-DD") from exc
        if parsed_date.isoformat() != local_date:
            raise ContractError("local_date 必须是 YYYY-MM-DD")

        profile_sha = _sha(profile_sha256, "profile_sha256")
        action_sha = _sha(
            user_action_watermark_sha256,
            "user_action_watermark_sha256",
        )
        context = _json_only(dict(daily_context or {}), "daily_context")
        source_catalog = self.materialize_source_spans(source_spans)
        parsed_receipt_refs = [
            raw if isinstance(raw, ObjectRef) else ObjectRef.from_dict(raw)
            for raw in receipt_refs
        ]
        if any(
            ref.kind != "interpretation_receipt"
            for ref in parsed_receipt_refs
        ):
            raise ContractError(
                "receipt_refs 只能包含 interpretation_receipt",
                kind="evidence",
            )
        all_object_refs: list[ObjectRef | Mapping[str, Any]] = [
            *object_refs,
            *parsed_receipt_refs,
        ]
        object_catalog = self.materialize_object_refs(all_object_refs)
        source_catalog_sha = sha256_bytes(
            canonical_json(source_catalog).encode("utf-8")
        )
        object_catalog_sha = sha256_bytes(
            canonical_json(object_catalog).encode("utf-8")
        )
        daily_context_sha = sha256_bytes(
            canonical_json(context).encode("utf-8")
        )
        source_record_refs: list[dict[str, Any]] = []
        seen_source_records: set[tuple[str, int, str]] = set()
        for item in source_catalog:
            span = SourceSpan.from_dict(item["span"])
            key = (
                span.record_id,
                span.record_revision,
                span.record_revision_sha256,
            )
            if key in seen_source_records:
                continue
            seen_source_records.add(key)
            source_record_refs.append(
                ObjectRef(
                    kind="source_record",
                    id=span.record_id,
                    revision=span.record_revision,
                    revision_sha256=span.record_revision_sha256,
                ).to_dict()
            )
        receipt_ref_dicts = [ref.to_dict() for ref in parsed_receipt_refs]
        source_manifest_sha = sha256_bytes(
            canonical_json(source_record_refs).encode("utf-8")
        )
        receipt_manifest_sha = sha256_bytes(
            canonical_json(receipt_ref_dicts).encode("utf-8")
        )
        input_manifest = {
            "source_refs": source_record_refs,
            "receipt_refs": receipt_ref_dicts,
            "source_manifest_sha256": source_manifest_sha,
            "receipt_manifest_sha256": receipt_manifest_sha,
            "profile_sha256": profile_sha,
            "user_action_watermark_sha256": action_sha,
            "policy_sha256": self.daily_policy_sha256,
        }
        run_key = _id24(
            "drk_",
            "daily-run-key-v1",
            {
                "local_date": local_date,
                "source_catalog_sha256": source_catalog_sha,
                "object_catalog_sha256": object_catalog_sha,
                "daily_context_sha256": daily_context_sha,
                **input_manifest,
            },
        )
        return {
            "context": context,
            "source_catalog": source_catalog,
            "object_catalog": object_catalog,
            "parsed_receipt_refs": parsed_receipt_refs,
            "all_object_refs": all_object_refs,
            "input_manifest": input_manifest,
            "run_key": run_key,
        }

    def _current_action_watermark(self) -> str:
        from cognitive_actions_v1 import CognitiveActionStore

        _, watermark = CognitiveActionStore(
            self.vault, state_root=self.files.root
        ).action_watermark()
        return watermark

    def _request_source_is_current(self, request: Mapping[str, Any]) -> bool:
        record_ref = ObjectRef.from_dict(request["record_ref"])
        try:
            current_ref = ObjectRef.from_dict(self.store.load_head_ref(record_ref.id))
        except ContractError as exc:
            if exc.kind == "not_found":
                return False
            raise
        return (
            current_ref == record_ref
            and self._current_action_watermark()
            == request["feedback_watermark_sha256"]
        )

    def _load_bound_single_attempt_completion(
        self,
        run: Mapping[str, Any],
        run_path: Path,
        attempt: Mapping[str, Any],
    ) -> dict[str, Any]:
        usage = run["usage"]
        if not isinstance(usage, Mapping) or usage.get("model_calls") != 1:
            raise ContractError(
                "interpretation terminal usage 未绑定单次 Provider 调用",
                kind="evidence",
            )
        completion_path = self._completion_path(
            run_path, run["run_id"], attempt["turn"]
        )
        if not completion_path.exists():
            raise ContractError(
                "interpretation terminal 缺少 Provider completion sidecar",
                kind="evidence",
            )
        completion = self.files.read_json(
            completion_path,
            name="interpretation terminal completion",
            maximum=MAX_COMPLETION_BYTES + 50_000,
        )
        self._validate_completion(completion, run, attempt)
        if completion["usage_missing"]:
            if not (
                usage["usage_missing"] is True
                and usage["cost_complete"] is False
                and usage["cost_usd"] is None
                and all(usage[field] is None for field in NORMALIZED_USAGE_FIELDS)
            ):
                raise ContractError(
                    "interpretation terminal 缺失 usage 与 run 不一致",
                    kind="evidence",
                )
            return completion

        completion_usage = completion["usage"]
        if not isinstance(completion_usage, Mapping) or not (
            usage["usage_missing"] is False
            and all(
                usage[field] == completion_usage[field]
                for field in NORMALIZED_USAGE_FIELDS
            )
        ):
            raise ContractError(
                "interpretation terminal usage 与 completion 不一致",
                kind="evidence",
            )
        try:
            expected_cost = calculate_cost(
                completion_usage, pricing_for_model(self.model)
            )
        except ContractError:
            if usage["cost_complete"] is not False or usage["cost_usd"] is not None:
                raise ContractError(
                    "interpretation terminal 未知定价状态无效",
                    kind="evidence",
                )
        else:
            if not (
                usage["cost_complete"] is True
                and usage["cost_usd"] == expected_cost
            ):
                raise ContractError(
                    "interpretation terminal cost 与 completion 不一致",
                    kind="evidence",
                )
        return completion

    @staticmethod
    def _known_invalid_retry_request_id(
        source_request: Mapping[str, Any], run_key: str
    ) -> str:
        identity = {
            "record_ref": dict(source_request["record_ref"]),
            "feedback_watermark_sha256": source_request[
                "feedback_watermark_sha256"
            ],
            "contract_version": RECORD_INTERPRETER_CONTRACT_VERSION,
            "trigger": "retry",
            "request_nonce": _KNOWN_INVALID_SCHEMA_RETRY_NONCE_PREFIX + run_key,
        }
        return _id24("ireq_", "interpretation-request-v1", identity)

    def _is_bound_known_schema_failure(
        self,
        run: Mapping[str, Any],
        run_path: Path,
    ) -> bool:
        """Validate the sole failure shape that may authorize one paid retry."""

        if run["status"] != "error" or run["error_kind"] != "schema":
            return False
        usage = run["usage"]
        if (
            not isinstance(usage, Mapping)
            or usage.get("model_calls") != 1
            or usage.get("usage_missing") is not False
            or usage.get("cost_complete") is not True
        ):
            return False
        steps = run["steps"]
        if len(steps) != 2:
            return False
        attempts = [row for row in steps if row["action"] == "provider_attempt"]
        invalid_actions = [
            row for row in steps if row["action"] == "invalid_action"
        ]
        if len(attempts) != 1 or len(invalid_actions) != 1:
            return False
        attempt = attempts[0]
        invalid = invalid_actions[0]
        if not (
            attempt["turn"] == invalid["turn"]
            and attempt["reason_code"] == "provider_attempt_completed"
            and attempt["result_kind"] == "provider_attempt_resolved"
            and attempt["result_count"] == 1
            and attempt["error_kind"] is None
            and invalid["reason_code"] == "validation_failed"
            and invalid["result_kind"] == "error"
            and invalid["result_count"] == 0
            and invalid["error_kind"] == "schema"
        ):
            return False
        completion = self._load_bound_single_attempt_completion(
            run, run_path, attempt
        )
        if not (
            completion["content"] is None
            and completion["validation_error_kind"] == "schema"
            and completion["usage_missing"] is False
            and isinstance(completion["usage"], Mapping)
        ):
            return False
        expected_invalid_arguments_sha = sha256_bytes(
            canonical_json(
                {"completion_sha256": completion["content_sha256"]}
            ).encode("utf-8")
        )
        if invalid["arguments_sha256"] != expected_invalid_arguments_sha:
            raise ContractError(
                "schema failure invalid_action 未绑定 completion",
                kind="evidence",
            )
        completion_paths = sorted(
            run_path.parent.glob(f"{run['run_id']}.turn*.completion.json")
        )
        expected_path = self._completion_path(
            run_path, run["run_id"], attempt["turn"]
        )
        if completion_paths != [expected_path]:
            raise ContractError(
                "schema failure completion sidecar 数量无效",
                kind="evidence",
            )
        return True

    @staticmethod
    def _is_material_block_checkpoint(run: Mapping[str, Any]) -> bool:
        steps = run.get("steps")
        return bool(
            run.get("status") == "error"
            and isinstance(steps, list)
            and len(steps) == 1
            and steps[0].get("action") == "cache_hit"
            and steps[0].get("reason_code") == "material_attempt_blocked"
            and steps[0].get("result_kind") == "error"
            and steps[0].get("result_count") == 0
        )

    def _interpretation_material_paid_call_blocker(
        self,
        *,
        request: Mapping[str, Any],
        run_key: str,
        input_hashes: Mapping[str, str],
    ) -> tuple[dict[str, Any], str] | None:
        """Return the authoritative run that blocks another material call.

        The caller holds ``run_key``'s cross-process lock.  Successful
        terminals are handled by the normal material cache before this method;
        every other durable attempt blocks arbitrary request nonces.  The only
        exception is the exact deterministic retry request derived from one
        complete-usage, sidecar-bound schema failure.
        """

        blockers: list[tuple[dict[str, Any], str]] = []
        schema_sources: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        block_checkpoints: list[tuple[dict[str, Any], str]] = []
        for run_path in sorted(self.files.interpretation_runs.glob("irun_*.json")):
            candidate = self.files.read_json(
                run_path, name="interpretation material attempt"
            )
            if (
                candidate.get("kind") != "memento_interpretation_run"
                or candidate.get("run_key") != run_key
            ):
                continue
            candidate_request_id = candidate.get("request_id")
            if not isinstance(candidate_request_id, str):
                raise ContractError(
                    "interpretation material request id 无效", kind="evidence"
                )
            candidate_request, candidate_request_sha = (
                self._load_interpretation_request(candidate_request_id)
            )
            self._validate_interpretation_run(
                candidate,
                run_path.stem,
                candidate_request_id,
                candidate_request_sha,
                run_key,
            )
            if candidate["input_hashes"] != dict(input_hashes):
                raise ContractError(
                    "interpretation material run 输入 hash 不一致",
                    kind="evidence",
                )
            if candidate["status"] in {"completed", "no_candidate"}:
                # The caller checks these through the normal cache first.  A
                # successful matching run can never authorize another call.
                blockers.append((candidate, "material_terminal_exists"))
                continue
            if self._is_material_block_checkpoint(candidate):
                block_checkpoints.append((candidate, "material_attempt_blocked"))
                continue
            if (
                candidate_request["trigger"] != "retry"
                and self._is_bound_known_schema_failure(candidate, run_path)
            ):
                schema_sources.append(
                    (candidate, candidate_request, candidate_request_sha)
                )
                continue
            blockers.append(
                (
                    candidate,
                    str(candidate.get("error_kind") or "material_attempt_incomplete"),
                )
            )

        if blockers:
            return blockers[0]
        if len(schema_sources) > 1:
            return schema_sources[0][0], "multiple_schema_attempts"
        if len(schema_sources) == 1:
            source_run, source_request, _ = schema_sources[0]
            expected_retry_id = self._known_invalid_retry_request_id(
                source_request, run_key
            )
            if (
                request["id"] == expected_retry_id
                and request["trigger"] == "retry"
            ):
                return None
            return source_run, "schema_retry_required"
        if block_checkpoints:
            # An orphaned block checkpoint is still fail-closed.  Under normal
            # operation its authoritative blocker is present in the same
            # immutable state directory.
            return block_checkpoints[0]
        return None

    def _known_invalid_schema_retry_source(
        self,
        request_id: str,
        *,
        observed_cached: bool,
        target_objects: Sequence[ObjectRef | Mapping[str, Any]] = (),
    ) -> tuple[dict[str, Any], str] | None:
        """Return the persisted source request for one bounded schema retry.

        ``observed_cached`` is the caller's proof that the original terminal
        failure has already been surfaced once.  Every durable fact that can
        authorize another paid call is independently re-read and validated
        here; the result object supplied by a caller is never trusted.
        """

        if observed_cached is not True:
            return None
        request, request_sha = self._load_interpretation_request(request_id)
        if request["trigger"] == "retry":
            return None

        record_ref = ObjectRef.from_dict(request["record_ref"])
        object_catalog = self.materialize_object_refs(target_objects)
        target_object_manifest_sha = sha256_bytes(
            canonical_json(object_catalog).encode("utf-8")
        )
        input_hashes, run_key = self._interpretation_material_identity(
            record_ref,
            request["feedback_watermark_sha256"],
            target_object_manifest_sha,
            self.record_policy_sha256,
        )
        run_id = _id24(
            "irun_",
            "interpretation-run-v1",
            {"request_id": request_id, "run_key": run_key},
        )
        run_path = self.files.interpretation_runs / f"{run_id}.json"

        with self.files.lock(run_key):
            if not run_path.exists():
                return None
            run = self.files.read_json(run_path, name="interpretation retry source run")
            self._validate_interpretation_run(
                run, run_id, request_id, request_sha, run_key
            )
            if run["status"] != "error" or run["error_kind"] != "schema":
                return None
            if run["input_hashes"] != input_hashes:
                raise ContractError(
                    "schema retry source input hashes 与 request 不一致",
                    kind="evidence",
                )

            usage = run["usage"]
            if (
                not isinstance(usage, Mapping)
                or usage.get("model_calls") != 1
                or usage.get("usage_missing") is not False
                or usage.get("cost_complete") is not True
            ):
                return None

            steps = run["steps"]
            if len(steps) != 2:
                return None
            attempts = [row for row in steps if row["action"] == "provider_attempt"]
            invalid_actions = [row for row in steps if row["action"] == "invalid_action"]
            if len(attempts) != 1 or len(invalid_actions) != 1:
                return None
            attempt = attempts[0]
            invalid = invalid_actions[0]
            if not (
                attempt["turn"] == invalid["turn"]
                and attempt["reason_code"] == "provider_attempt_completed"
                and attempt["result_kind"] == "provider_attempt_resolved"
                and attempt["result_count"] == 1
                and attempt["error_kind"] is None
                and invalid["reason_code"] == "validation_failed"
                and invalid["result_kind"] == "error"
                and invalid["result_count"] == 0
                and invalid["error_kind"] == "schema"
            ):
                return None

            completion = self._load_bound_single_attempt_completion(
                run, run_path, attempt
            )
            if not (
                completion["content"] is None
                and completion["validation_error_kind"] == "schema"
                and completion["usage_missing"] is False
                and isinstance(completion["usage"], Mapping)
            ):
                return None

            expected_invalid_arguments_sha = sha256_bytes(
                canonical_json(
                    {"completion_sha256": completion["content_sha256"]}
                ).encode("utf-8")
            )
            if invalid["arguments_sha256"] != expected_invalid_arguments_sha:
                raise ContractError(
                    "schema retry source invalid_action 未绑定 completion",
                    kind="evidence",
                )
            if not self._request_source_is_current(request):
                return None
        return request, run_key

    def is_interpretation_schema_retry_eligible(
        self,
        request_id: str,
        *,
        observed_cached: bool,
        target_objects: Sequence[ObjectRef | Mapping[str, Any]] = (),
    ) -> bool:
        """Whether one deterministic retry may follow a known schema failure."""

        return (
            self._known_invalid_schema_retry_source(
                request_id,
                observed_cached=observed_cached,
                target_objects=target_objects,
            )
            is not None
        )

    def create_interpretation_schema_retry_request(
        self,
        request_id: str,
        *,
        observed_cached: bool,
        target_objects: Sequence[ObjectRef | Mapping[str, Any]] = (),
    ) -> dict[str, Any] | None:
        """Validate and create the sole deterministic retry request, if allowed."""

        eligible = self._known_invalid_schema_retry_source(
            request_id,
            observed_cached=observed_cached,
            target_objects=target_objects,
        )
        if eligible is None:
            return None
        source, run_key = eligible
        if not self._request_source_is_current(source):
            return None

        identity = {
            "record_ref": dict(source["record_ref"]),
            "feedback_watermark_sha256": source["feedback_watermark_sha256"],
            "contract_version": RECORD_INTERPRETER_CONTRACT_VERSION,
            "trigger": "retry",
            "request_nonce": _KNOWN_INVALID_SCHEMA_RETRY_NONCE_PREFIX + run_key,
        }
        retry_request_id = _id24(
            "ireq_", "interpretation-request-v1", identity
        )
        path = self.files.interpretation_requests / f"{retry_request_id}.json"
        with self.files.lock(retry_request_id):
            if path.exists():
                existing = self._load_interpretation_request(retry_request_id)[0]
                if not (
                    existing["trigger"] == "retry"
                    and existing["record_ref"] == source["record_ref"]
                    and existing["feedback_watermark_sha256"]
                    == source["feedback_watermark_sha256"]
                ):
                    raise ContractError(
                        "deterministic retry request 与来源不一致",
                        kind="evidence",
                    )
                if not self._request_source_is_current(source):
                    return None
                return existing
            if not self._request_source_is_current(source):
                return None
            retry_request = {
                "schema_version": COGNITIVE_SCHEMA_VERSION,
                "kind": "memento_interpretation_request",
                "id": retry_request_id,
                "status": "pending",
                "created_at": _now_text(self.clock),
                "trigger": "retry",
                "record_ref": dict(source["record_ref"]),
                "contract_version": RECORD_INTERPRETER_CONTRACT_VERSION,
                "feedback_watermark_sha256": source[
                    "feedback_watermark_sha256"
                ],
            }
            self._validate_interpretation_request(retry_request)
            self.files.write_immutable(path, retry_request)
            return retry_request

    def create_known_invalid_retry_request(
        self,
        result: Mapping[str, Any],
        *,
        target_objects: Sequence[ObjectRef | Mapping[str, Any]] = (),
    ) -> dict[str, Any] | None:
        """Create the sole retry from a surfaced interpretation result.

        Only the persisted request id and the fact that the caller observed a
        cached result are taken from ``result``.  Run status, steps, usage,
        completion evidence, source revision, and feedback watermark are all
        loaded again by ``create_interpretation_schema_retry_request``.
        """

        if not isinstance(result, Mapping):
            return None
        request = result.get("request")
        request_id = request.get("id") if isinstance(request, Mapping) else None
        if not isinstance(request_id, str):
            return None
        return self.create_interpretation_schema_retry_request(
            request_id,
            observed_cached=result.get("cached") is True,
            target_objects=target_objects,
        )

    def get_current_interpretation_terminal(
        self,
        record_id: str,
        *,
        feedback_watermark_sha256: str,
        target_objects: Sequence[ObjectRef | Mapping[str, Any]] = (),
    ) -> dict[str, Any] | None:
        """Return a trusted terminal for the exact current material inputs.

        A receipt-backed ``completed`` run is already visible through the
        receipt store.  The important additional state is ``no_candidate``:
        it has no receipt, so callers need this query to keep an unchanged
        record out of a bounded pending queue without trusting a result object
        retained only in process memory.
        """

        feedback = _sha(
            feedback_watermark_sha256, "feedback_watermark_sha256"
        )
        record_ref = ObjectRef.from_dict(self.store.load_head_ref(record_id))
        if self._current_action_watermark() != feedback:
            return None
        object_catalog = self.materialize_object_refs(target_objects)
        target_manifest_sha = sha256_bytes(
            canonical_json(object_catalog).encode("utf-8")
        )
        input_hashes, run_key = self._interpretation_material_identity(
            record_ref,
            feedback,
            target_manifest_sha,
            self.record_policy_sha256,
        )

        with self.files.lock(run_key):
            try:
                current_ref = ObjectRef.from_dict(
                    self.store.load_head_ref(record_ref.id)
                )
            except ContractError as exc:
                if exc.kind == "not_found":
                    return None
                raise
            if (
                current_ref != record_ref
                or self._current_action_watermark() != feedback
            ):
                return None

            for run_path in sorted(
                self.files.interpretation_runs.glob("irun_*.json")
            ):
                run = self.files.read_json(
                    run_path, name="current interpretation terminal"
                )
                if (
                    run.get("kind") != "memento_interpretation_run"
                    or run.get("run_key") != run_key
                ):
                    continue
                request_id = run.get("request_id")
                if not isinstance(request_id, str):
                    raise ContractError(
                        "current interpretation terminal request id 无效",
                        kind="evidence",
                    )
                request, request_sha = self._load_interpretation_request(
                    request_id
                )
                self._validate_interpretation_run(
                    run,
                    run_path.stem,
                    request_id,
                    request_sha,
                    run_key,
                )
                if (
                    ObjectRef.from_dict(request["record_ref"]) != record_ref
                    or request["feedback_watermark_sha256"] != feedback
                    or run["input_hashes"] != input_hashes
                ):
                    raise ContractError(
                        "current interpretation terminal 未绑定当前输入",
                        kind="evidence",
                    )
                if run["status"] == "completed":
                    ref = ObjectRef.from_dict(run["receipt_ref"])
                    self._resolve_object(ref)
                    continue
                if run["status"] != "no_candidate":
                    continue

                steps = run["steps"]
                # Cache-hit checkpoints carry no completion sidecar of their
                # own.  Continue until the authoritative paid attempt is
                # found; a cache record alone cannot suppress pending work.
                if (
                    len(steps) == 1
                    and steps[0]["action"] == "cache_hit"
                    and steps[0]["result_kind"] == "no_candidate"
                ):
                    continue
                attempts = [
                    row for row in steps if row["action"] == "provider_attempt"
                ]
                finishes = [row for row in steps if row["action"] == "finish"]
                if len(steps) != 2 or len(attempts) != 1 or len(finishes) != 1:
                    raise ContractError(
                        "no_candidate terminal step 链无效", kind="evidence"
                    )
                attempt = attempts[0]
                finish = finishes[0]
                if not (
                    attempt["turn"] == finish["turn"]
                    and attempt["reason_code"] == "provider_attempt_completed"
                    and attempt["result_kind"] == "provider_attempt_resolved"
                    and attempt["result_count"] == 1
                    and attempt["error_kind"] is None
                    and finish["result_kind"] == "no_candidate"
                    and finish["result_count"] == 0
                    and finish["error_kind"] is None
                    and run["receipt_ref"] is None
                    and run["error_kind"] is None
                ):
                    raise ContractError(
                        "no_candidate terminal 结果无效", kind="evidence"
                    )
                completion = self._load_bound_single_attempt_completion(
                    run, run_path, attempt
                )
                if (
                    not isinstance(completion["content"], str)
                    or completion["validation_error_kind"] is not None
                ):
                    raise ContractError(
                        "no_candidate terminal completion 无效", kind="evidence"
                    )
                action = parse_record_interpreter_action(
                    completion["content"],
                    allowed_source_ref_ids=(),
                    allowed_target_ref_ids=(),
                )
                if (
                    action["action"] != "finish"
                    or action["reason_code"] != finish["reason_code"]
                    or sha256_bytes(
                        canonical_json(action["arguments"]).encode("utf-8")
                    )
                    != finish["arguments_sha256"]
                ):
                    raise ContractError(
                        "no_candidate terminal 未绑定 finish action",
                        kind="evidence",
                    )
                return {
                    "status": "no_candidate",
                    "request_id": request_id,
                    "run_id": run["run_id"],
                    "receipt_ref": None,
                }
        return None

    def _daily_material_is_current(self, material: Mapping[str, Any]) -> bool:
        """Verify that a frozen Daily identity still names every current head."""

        manifest = material["input_manifest"]
        if (
            manifest["policy_sha256"] != self.daily_policy_sha256
            or manifest["user_action_watermark_sha256"]
            != self._current_action_watermark()
        ):
            return False

        # Daily profile changes are a semantic input change even when source
        # and receipt manifests are unchanged.  Rebuild it at the read
        # boundary so a caller cannot present the hash of an older profile as
        # current completion evidence.
        from agent_v1 import build_agent_profile
        from cognitive_actions_v1 import CognitiveActionStore

        current_profile = build_agent_profile(self.vault).get("profile_sha256")
        if (
            not isinstance(current_profile, str)
            or current_profile != manifest["profile_sha256"]
        ):
            return False

        source_refs = [
            ObjectRef.from_dict(raw) for raw in manifest["source_refs"]
        ]
        for ref in source_refs:
            try:
                current = ObjectRef.from_dict(self.store.load_head_ref(ref.id))
            except ContractError as exc:
                if exc.kind == "not_found":
                    return False
                raise
            if current != ref:
                return False

        action_store = CognitiveActionStore(
            self.vault, state_root=self.files.root
        )
        receipt_refs = [
            ObjectRef.from_dict(raw) for raw in manifest["receipt_refs"]
        ]
        receipt_source_refs: list[ObjectRef] = []
        receipt_span_hashes: set[str] = set()
        for ref in receipt_refs:
            try:
                loaded_receipt_ref = action_store.load_receipt_head_ref(ref.id)
                current_receipt_ref = (
                    loaded_receipt_ref
                    if isinstance(loaded_receipt_ref, ObjectRef)
                    else ObjectRef.from_dict(loaded_receipt_ref)
                )
            except ContractError as exc:
                if exc.kind == "not_found":
                    return False
                raise
            if current_receipt_ref != ref:
                return False
            receipt = InterpretationReceiptRevision.from_dict(
                self._resolve_object(ref)
            )
            if (
                receipt.status not in {"ready", "needs_review"}
                or receipt.receipt_id != make_receipt_id(receipt.record_ref.id)
            ):
                return False
            receipt_source_refs.append(receipt.record_ref)
            receipt_span_hashes.update(span.sha256 for span in receipt.source_spans)

        source_keys = {
            (ref.kind, ref.id, ref.revision, ref.revision_sha256)
            for ref in source_refs
        }
        receipt_source_keys = {
            (ref.kind, ref.id, ref.revision, ref.revision_sha256)
            for ref in receipt_source_refs
        }
        supplied_span_hashes = {
            SourceSpan.from_dict(item["span"]).sha256
            for item in material["source_catalog"]
        }
        return bool(
            source_refs
            and receipt_refs
            and len(receipt_source_refs) == len(receipt_source_keys)
            and source_keys == receipt_source_keys
            and supplied_span_hashes == receipt_span_hashes
        )

    def get_current_daily_terminal(
        self,
        local_date: str,
        *,
        source_spans: Sequence[SourceSpan | Mapping[str, Any]],
        object_refs: Sequence[ObjectRef | Mapping[str, Any]] = (),
        receipt_refs: Sequence[ObjectRef | Mapping[str, Any]] = (),
        daily_context: Mapping[str, Any] | None = None,
        profile_sha256: str = ZERO_SHA256,
        user_action_watermark_sha256: str = ZERO_SHA256,
    ) -> dict[str, str] | None:
        """Return a trusted Daily ``no_change`` for exact current material.

        The reader never calls the Provider and never creates a request, run,
        checkpoint, or cache-hit record.  A matching mutable run is accepted
        only when every paid attempt is bound to one immutable completion
        sidecar, every non-terminal action is a successful bounded read-only
        tool call, and the final action is canonical ``finish/no_change``.
        """

        frozen_source_spans = tuple(source_spans)
        frozen_object_refs = tuple(object_refs)
        frozen_receipt_refs = tuple(receipt_refs)
        frozen_context = dict(daily_context or {})
        material = self._daily_material_identity(
            local_date,
            source_spans=frozen_source_spans,
            object_refs=frozen_object_refs,
            receipt_refs=frozen_receipt_refs,
            daily_context=frozen_context,
            profile_sha256=profile_sha256,
            user_action_watermark_sha256=user_action_watermark_sha256,
        )
        run_key = material["run_key"]

        with self.files.lock(run_key):
            # Close the gap between initial materialization and terminal
            # return.  Pipeline resolvers reject stale formal-object heads;
            # source and receipt heads are checked explicitly below.
            try:
                current_material = self._daily_material_identity(
                    local_date,
                    source_spans=frozen_source_spans,
                    object_refs=frozen_object_refs,
                    receipt_refs=frozen_receipt_refs,
                    daily_context=frozen_context,
                    profile_sha256=profile_sha256,
                    user_action_watermark_sha256=(
                        user_action_watermark_sha256
                    ),
                )
            except ContractError as exc:
                if exc.kind in {"not_found", "stale"}:
                    return None
                raise
            for field in (
                "context",
                "source_catalog",
                "object_catalog",
                "input_manifest",
                "run_key",
            ):
                if current_material[field] != material[field]:
                    return None
            if not self._daily_material_is_current(current_material):
                return None

            source_ref_ids = [
                item["ref_id"] for item in current_material["source_catalog"]
            ]
            object_ref_ids = [
                item["ref_id"] for item in current_material["object_catalog"]
            ]
            for run_path in sorted(self.files.daily_runs.glob("drun_*.json")):
                run = self.files.read_json(
                    run_path, name="current daily terminal"
                )
                if (
                    run.get("kind") != "memento_daily_integration_run"
                    or run.get("run_key") != run_key
                ):
                    continue
                request_id = run.get("request_id")
                if not isinstance(request_id, str):
                    raise ContractError(
                        "current daily terminal request id 无效",
                        kind="evidence",
                    )
                request, request_sha = self._load_daily_request(request_id)
                self._validate_daily_run(
                    run,
                    run_path.stem,
                    request_id,
                    request_sha,
                    run_key,
                )
                if (
                    request["local_date"] != local_date
                    or run["input_manifest"]
                    != current_material["input_manifest"]
                ):
                    raise ContractError(
                        "current daily terminal 未绑定当前输入",
                        kind="evidence",
                    )
                if run["status"] != "no_change":
                    continue

                steps = run["steps"]
                # A cache checkpoint has no completion sidecar of its own.
                # Keep looking for the authoritative paid run with this exact
                # material key; a cache row alone is insufficient evidence.
                if (
                    len(steps) == 1
                    and steps[0]["action"] == "cache_hit"
                    and steps[0]["result_kind"] == "no_change"
                ):
                    continue
                if (
                    not isinstance(steps, list)
                    or not steps
                    or len(steps) % 2
                ):
                    raise ContractError(
                        "daily no_change terminal step 链无效",
                        kind="evidence",
                    )
                model_turns = len(steps) // 2
                usage = run["usage"]
                if not (
                    1 <= model_turns <= self.daily_budget.max_model_turns
                    and run["stage"] == "finished"
                    and run["error_kind"] is None
                    and run["bundle_ref"] is None
                    and run["warnings"] == []
                    and run["review_status"] == "not_started"
                    and run["long_term_status"] == "not_started"
                    and run["landscape_status"] == "not_started"
                    and isinstance(usage, Mapping)
                    and usage.get("model_calls") == model_turns
                    and usage.get("usage_missing") is False
                    and usage.get("cost_complete") is True
                ):
                    raise ContractError(
                        "daily no_change terminal 结果无效",
                        kind="evidence",
                    )

                expected_completion_paths: list[Path] = []
                aggregate_usage = {
                    field: 0 for field in NORMALIZED_USAGE_FIELDS
                }
                aggregate_cost = 0.0
                tool_calls = 0
                final_reason: str | None = None
                for turn in range(1, model_turns + 1):
                    attempt = steps[(turn - 1) * 2]
                    action_step = steps[(turn - 1) * 2 + 1]
                    if not (
                        attempt["turn"] == turn
                        and attempt["action"] == "provider_attempt"
                        and attempt["reason_code"]
                        == "provider_attempt_completed"
                        and attempt["result_kind"]
                        == "provider_attempt_resolved"
                        and attempt["result_count"] == 1
                        and attempt["error_kind"] is None
                        and action_step["turn"] == turn
                    ):
                        raise ContractError(
                            "daily no_change Provider step 链无效",
                            kind="evidence",
                        )

                    completion_path = self._completion_path(
                        run_path, run["run_id"], turn
                    )
                    if not completion_path.exists():
                        raise ContractError(
                            "daily no_change 缺少 completion sidecar",
                            kind="evidence",
                        )
                    completion = self.files.read_json(
                        completion_path,
                        name="daily no_change completion",
                        maximum=MAX_COMPLETION_BYTES + 50_000,
                    )
                    self._validate_completion(completion, run, attempt)
                    if (
                        not isinstance(completion["content"], str)
                        or completion["validation_error_kind"] is not None
                        or completion["usage_missing"] is not False
                        or not isinstance(completion["usage"], Mapping)
                    ):
                        raise ContractError(
                            "daily no_change completion 无效",
                            kind="evidence",
                        )
                    expected_completion_paths.append(completion_path)
                    completion_usage = completion["usage"]
                    for field in NORMALIZED_USAGE_FIELDS:
                        aggregate_usage[field] += completion_usage[field]
                    try:
                        turn_cost = calculate_cost(
                            completion_usage, pricing_for_model(self.model)
                        )
                    except ContractError as exc:
                        raise ContractError(
                            "daily no_change completion cost 不完整",
                            kind="evidence",
                        ) from exc
                    aggregate_cost = round(aggregate_cost + turn_cost, 10)

                    action = parse_daily_integrator_action(
                        completion["content"],
                        allowed_source_ref_ids=source_ref_ids,
                        allowed_object_ref_ids=object_ref_ids,
                    )
                    if not (
                        action["action"] == action_step["action"]
                        and action["reason_code"]
                        == action_step["reason_code"]
                        and sha256_bytes(
                            canonical_json(action["arguments"]).encode(
                                "utf-8"
                            )
                        )
                        == action_step["arguments_sha256"]
                    ):
                        raise ContractError(
                            "daily no_change action 与 step 不一致",
                            kind="evidence",
                        )

                    is_final = turn == model_turns
                    if is_final:
                        if not (
                            action["action"] == "finish"
                            and action["reason_code"]
                            in {"no_change", "insufficient_evidence"}
                            and action_step["result_kind"] == "no_change"
                            and action_step["result_count"] == 0
                            and action_step["error_kind"] is None
                        ):
                            raise ContractError(
                                "daily no_change terminal 未绑定 finish action",
                                kind="evidence",
                            )
                        final_reason = action["reason_code"]
                        continue

                    if action["action"] not in {
                        "inspect_memory",
                        "search_history",
                    }:
                        raise ContractError(
                            "daily no_change 中间 action 无效",
                            kind="evidence",
                        )
                    tool_calls += 1
                    if not (
                        tool_calls <= self.daily_budget.max_tool_calls
                        and action_step["result_kind"] == "tool_result"
                        and action_step["error_kind"] is None
                    ):
                        raise ContractError(
                            "daily no_change 工具 step 无效",
                            kind="evidence",
                        )
                    if action["action"] == "inspect_memory":
                        valid_count = action_step["result_count"] == 1
                    else:
                        valid_count = (
                            0
                            <= action_step["result_count"]
                            <= min(
                                action["arguments"]["limit"],
                                self.daily_budget.max_history_results_per_search,
                            )
                        )
                    if not valid_count:
                        raise ContractError(
                            "daily no_change 工具结果数无效",
                            kind="evidence",
                        )

                if sorted(
                    run_path.parent.glob(
                        f"{run['run_id']}.turn*.completion.json"
                    )
                ) != expected_completion_paths:
                    raise ContractError(
                        "daily no_change completion sidecar 数量无效",
                        kind="evidence",
                    )
                if (
                    final_reason is None
                    or any(
                        usage[field] != aggregate_usage[field]
                        for field in NORMALIZED_USAGE_FIELDS
                    )
                    or usage.get("cost_usd") != aggregate_cost
                ):
                    raise ContractError(
                        "daily no_change aggregate usage 无效",
                        kind="evidence",
                    )

                # A source edit, user action, profile update, or formal-head
                # switch that races the sidecar checks must still invalidate
                # the result before it crosses the public boundary.
                try:
                    final_material = self._daily_material_identity(
                        local_date,
                        source_spans=frozen_source_spans,
                        object_refs=frozen_object_refs,
                        receipt_refs=frozen_receipt_refs,
                        daily_context=frozen_context,
                        profile_sha256=profile_sha256,
                        user_action_watermark_sha256=(
                            user_action_watermark_sha256
                        ),
                    )
                except ContractError as exc:
                    if exc.kind in {"not_found", "stale"}:
                        return None
                    raise
                if (
                    final_material["source_catalog"]
                    != current_material["source_catalog"]
                    or final_material["object_catalog"]
                    != current_material["object_catalog"]
                    or final_material["input_manifest"]
                    != current_material["input_manifest"]
                    or final_material["run_key"] != run_key
                    or not self._daily_material_is_current(final_material)
                ):
                    return None
                return {"run_id": run["run_id"], "status": "no_change"}
        return None

    def _load_interpretation_request(self, request_id: str) -> tuple[dict[str, Any], str]:
        if not isinstance(request_id, str) or not re.fullmatch(r"ireq_[0-9a-f]{24}", request_id):
            raise ContractError("interpretation request id 无效")
        path = self.files.interpretation_requests / f"{request_id}.json"
        value = self.files.read_json(path, name="interpretation request")
        self._validate_interpretation_request(value)
        if value["id"] != request_id:
            raise ContractError("interpretation request 文件名与内容不一致", kind="evidence")
        return value, persisted_sha256(value)

    def _load_daily_request(self, request_id: str) -> tuple[dict[str, Any], str]:
        if not isinstance(request_id, str) or not re.fullmatch(r"dreq_[0-9a-f]{24}", request_id):
            raise ContractError("daily request id 无效")
        path = self.files.daily_requests / f"{request_id}.json"
        value = self.files.read_json(path, name="daily request")
        self._validate_daily_request(value)
        if value["id"] != request_id:
            raise ContractError("daily request 文件名与内容不一致", kind="evidence")
        return value, persisted_sha256(value)

    @staticmethod
    def _validate_interpretation_request(value: Mapping[str, Any]) -> None:
        fields = {
            "schema_version", "kind", "id", "status", "created_at", "trigger",
            "record_ref", "contract_version", "feedback_watermark_sha256",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ContractError("interpretation request 字段不符合合同")
        if value["schema_version"] != COGNITIVE_SCHEMA_VERSION or value["kind"] != "memento_interpretation_request":
            raise ContractError("interpretation request schema/kind 无效")
        if not isinstance(value["id"], str) or not re.fullmatch(r"ireq_[0-9a-f]{24}", value["id"]):
            raise ContractError("interpretation request id 无效")
        if value["status"] != "pending" or value["trigger"] not in {"capture", "reconcile", "retry", "source_changed"}:
            raise ContractError("interpretation request status/trigger 无效")
        if value["contract_version"] != RECORD_INTERPRETER_CONTRACT_VERSION:
            raise ContractError("interpretation contract_version 无效")
        _sha(value["feedback_watermark_sha256"], "feedback watermark")
        ref = ObjectRef.from_dict(value["record_ref"])
        if ref.kind != "source_record":
            raise ContractError("interpretation request 必须绑定 source record")
        CognitiveRuntime._validate_timestamp(value["created_at"], "created_at")

    @staticmethod
    def _validate_daily_request(value: Mapping[str, Any]) -> None:
        fields = {"schema_version", "kind", "id", "status", "created_at", "trigger", "local_date", "contract_version"}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ContractError("daily request 字段不符合合同")
        if value["schema_version"] != COGNITIVE_SCHEMA_VERSION or value["kind"] != "memento_daily_integration_request":
            raise ContractError("daily request schema/kind 无效")
        if not isinstance(value["id"], str) or not re.fullmatch(r"dreq_[0-9a-f]{24}", value["id"]):
            raise ContractError("daily request id 无效")
        if value["status"] != "pending" or value["trigger"] not in {"manual", "scheduled", "recovery"}:
            raise ContractError("daily request status/trigger 无效")
        if value["contract_version"] != DAILY_INTEGRATOR_CONTRACT_VERSION:
            raise ContractError("daily contract_version 无效")
        try:
            dt.date.fromisoformat(value["local_date"])
        except (TypeError, ValueError) as exc:
            raise ContractError("daily local_date 无效") from exc
        CognitiveRuntime._validate_timestamp(value["created_at"], "created_at")

    @staticmethod
    def _validate_timestamp(value: Any, name: str) -> None:
        if not isinstance(value, str):
            raise ContractError(f"{name} 无效")
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractError(f"{name} 无效") from exc
        if parsed.tzinfo is None:
            raise ContractError(f"{name} 必须带时区")

    # ------------------------------------------------------------------
    # Exact evidence and object materialization

    def materialize_record_evidence(self, record_id: str) -> list[dict[str, Any]]:
        head = self.store.load_head(record_id)
        ref = ObjectRef.from_dict(self.store.load_head_ref(record_id))
        if head["status"] != "active":
            raise ContractError("已删除 source record 不得进入 Agent", kind="evidence")
        parsed = self.store.parse_day(head["source_file"])
        matches = [item for item in parsed.records if item.entry_sha256 == head["entry_sha256"]]
        if len(matches) != 1:
            raise ContractError("source record 无法唯一回到当前原文", kind="stale")
        record = matches[0]
        try:
            raw_lines = record.raw_block.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ContractError("source record 不是 UTF-8", kind="evidence") from exc
        lines = {
            record.line_start + offset: text
            for offset, text in enumerate(raw_lines)
        }
        candidates: list[tuple[int, str]] = []
        for number in range(record.line_start, record.line_end + 1):
            if number not in lines:
                raise ContractError("source record 行号越界", kind="stale")
            text = lines[number]
            stripped = text.strip()
            if not stripped or stripped == "---" or stripped.startswith("## "):
                continue
            candidates.append((number, text))
        if not candidates:
            heading_number = record.line_start
            candidates.append((heading_number, lines[heading_number]))
        if len(candidates) > 16:
            candidates = candidates[:16]
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for number, quote in candidates:
            span = SourceSpan(
                record_id=record_id,
                record_revision=ref.revision,
                record_revision_sha256=ref.revision_sha256,
                source_file=head["source_file"],
                line_start=number,
                line_end=number,
                quote=quote,
                quote_sha256=sha256_bytes(quote.encode("utf-8")),
            )
            ref_id = make_evidence_ref_id(span)
            if ref_id in seen:
                raise ContractError("物化的 evidence ref 重复", kind="evidence")
            seen.add(ref_id)
            result.append({"ref_id": ref_id, "span": span.to_dict(), "text": quote})
        return result

    def materialize_source_spans(
        self, spans: Sequence[SourceSpan | Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in spans:
            supplied_ref_id: str | None = None
            if isinstance(raw, Mapping) and set(raw) == {"ref_id", "span"}:
                supplied_ref_id = raw["ref_id"]
                raw = raw["span"]
            span = raw if isinstance(raw, SourceSpan) else SourceSpan.from_dict(raw)
            self._validate_span_exact(span)
            ref_id = make_evidence_ref_id(span)
            if supplied_ref_id is not None and supplied_ref_id != ref_id:
                raise ContractError("evidence ref id 与精确 span 不一致", kind="evidence")
            if ref_id in seen:
                raise ContractError("evidence ref 不得重复", kind="evidence")
            seen.add(ref_id)
            result.append({"ref_id": ref_id, "span": span.to_dict(), "text": span.quote})
        return result

    def _validate_span_exact(self, span: SourceSpan) -> None:
        current_ref = ObjectRef.from_dict(self.store.load_head_ref(span.record_id))
        expected_ref = ObjectRef(
            kind="source_record",
            id=span.record_id,
            revision=span.record_revision,
            revision_sha256=span.record_revision_sha256,
        )
        if current_ref != expected_ref:
            raise ContractError("source span 绑定的 record revision 已变化", kind="stale")
        head = self.store.load_head(span.record_id)
        if head["status"] != "active" or head["source_file"] != span.source_file:
            raise ContractError("source span 未绑定 active 原文", kind="evidence")
        parsed = self.store.parse_day(span.source_file)
        matches = [item for item in parsed.records if item.entry_sha256 == head["entry_sha256"]]
        if len(matches) != 1:
            raise ContractError("source span 无法唯一回到原文", kind="stale")
        record = matches[0]
        if span.line_start < record.line_start or span.line_end > record.line_end:
            raise ContractError("source span 越过绑定记录", kind="evidence")
        try:
            raw_lines = record.raw_block.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ContractError("source record 不是 UTF-8", kind="evidence") from exc
        lines = {
            record.line_start + offset: text
            for offset, text in enumerate(raw_lines)
        }
        if any(number not in lines for number in range(span.line_start, span.line_end + 1)):
            raise ContractError("source span 行号越界", kind="stale")
        exact = "\n".join(lines[number] for number in range(span.line_start, span.line_end + 1))
        if exact != span.quote:
            raise ContractError("source span quote 与当前原文不一致", kind="stale")

    def materialize_object_refs(
        self, refs: Sequence[ObjectRef | Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in refs:
            supplied_ref_id: str | None = None
            if isinstance(raw, Mapping) and set(raw) == {"ref_id", "object_ref"}:
                supplied_ref_id = raw["ref_id"]
                raw = raw["object_ref"]
            ref = raw if isinstance(raw, ObjectRef) else ObjectRef.from_dict(raw)
            ref_id = make_object_ref_id(ref)
            if supplied_ref_id is not None and supplied_ref_id != ref_id:
                raise ContractError("object ref id 与精确 revision 不一致", kind="evidence")
            if ref_id in seen:
                raise ContractError("object ref 不得重复", kind="evidence")
            seen.add(ref_id)
            snapshot = self._resolve_object(ref)
            result.append(
                {"ref_id": ref_id, "object_ref": ref.to_dict(), "snapshot": snapshot}
            )
        return result

    def _resolve_object(self, ref: ObjectRef) -> dict[str, Any]:
        if ref.kind == "interpretation_receipt":
            path = self.files.receipts / f"{ref.id}.r{ref.revision:06d}.json"
            snapshot = self.files.read_json(path, name="receipt revision")
            parsed = InterpretationReceiptRevision.from_dict(snapshot)
            if parsed.receipt_id != ref.id or parsed.revision != ref.revision:
                raise ContractError("receipt ref 文件名与内容不一致", kind="evidence")
            actual_sha = persisted_sha256(snapshot)
        else:
            if self.object_resolver is None:
                raise ContractError("缺少 object_resolver，不得授权未校验对象", kind="evidence")
            resolved = self.object_resolver(ref)
            if isinstance(resolved, tuple):
                if len(resolved) != 2 or not isinstance(resolved[0], Mapping):
                    raise ContractError("object_resolver 返回值无效", kind="evidence")
                snapshot = dict(resolved[0])
                actual_sha = resolved[1]
            elif isinstance(resolved, Mapping):
                snapshot = dict(resolved)
                actual_sha = persisted_sha256(snapshot)
            else:
                raise ContractError("object_resolver 返回值无效", kind="evidence")
        if actual_sha != ref.revision_sha256:
            raise ContractError("object ref revision hash 不一致", kind="evidence")
        return _json_only(snapshot, "object snapshot", maximum=50_000)

    # ------------------------------------------------------------------
    # Provider attempt durability

    def _completion_path(self, run_path: Path, run_id: str, turn: int) -> Path:
        return run_path.parent / f"{run_id}.turn{turn:03d}.completion.json"

    @staticmethod
    def _attempt_step(turn: int, attempt_sha256: str) -> dict[str, Any]:
        return {
            "turn": turn,
            "action": "provider_attempt",
            "reason_code": "provider_attempt_started",
            "arguments_sha256": attempt_sha256,
            "result_kind": "provider_attempt_started",
            "result_count": 0,
            "error_kind": None,
        }

    def _resolve_attempt_step(
        self,
        run: dict[str, Any],
        *,
        turn: int,
        attempt_sha256: str,
        result_kind: str,
        error_kind: str | None = None,
    ) -> None:
        matching = [
            (index, step)
            for index, step in enumerate(run["steps"])
            if step["turn"] == turn
            and step["action"] == "provider_attempt"
            and step["arguments_sha256"] == attempt_sha256
            and step["result_kind"] == "provider_attempt_started"
        ]
        if len(matching) != 1:
            raise ContractError("provider attempt marker 无法唯一解析", kind="evidence")
        index, _ = matching[0]
        run["steps"][index] = {
            "turn": turn,
            "action": "provider_attempt",
            "reason_code": f"provider_attempt_{result_kind}",
            "arguments_sha256": attempt_sha256,
            "result_kind": "provider_attempt_resolved",
            "result_count": 0 if error_kind else 1,
            "error_kind": error_kind,
        }

    def _append_action_step(
        self,
        run: dict[str, Any],
        *,
        turn: int,
        action: str,
        reason_code: str,
        arguments: Mapping[str, Any],
        result_kind: str,
        result_count: int,
        error_kind: str | None = None,
    ) -> None:
        run["steps"].append(
            {
                "turn": turn,
                "action": action,
                "reason_code": reason_code,
                "arguments_sha256": sha256_bytes(canonical_json(dict(arguments)).encode("utf-8")),
                "result_kind": result_kind,
                "result_count": result_count,
                "error_kind": error_kind,
            }
        )

    @staticmethod
    def _validate_steps(steps: Any, name: str) -> None:
        if not isinstance(steps, list):
            raise ContractError(f"{name} steps 无效", kind="evidence")
        fields = {
            "turn", "action", "reason_code", "arguments_sha256",
            "result_kind", "result_count", "error_kind",
        }
        provider_turns: set[int] = set()
        for step in steps:
            if not isinstance(step, Mapping) or set(step) != fields:
                raise ContractError(f"{name} step 字段不符合合同", kind="evidence")
            turn = step["turn"]
            if isinstance(turn, bool) or not isinstance(turn, int) or turn < 0:
                raise ContractError(f"{name} step turn 无效", kind="evidence")
            for field in ("action", "reason_code", "result_kind"):
                if not isinstance(step[field], str) or not step[field]:
                    raise ContractError(f"{name} step {field} 无效", kind="evidence")
            _sha(step["arguments_sha256"], f"{name} step arguments_sha256")
            count = step["result_count"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ContractError(f"{name} step result_count 无效", kind="evidence")
            error_kind = step["error_kind"]
            if error_kind is not None and (
                not isinstance(error_kind, str) or not error_kind
            ):
                raise ContractError(f"{name} step error_kind 无效", kind="evidence")
            if step["action"] == "provider_attempt":
                if turn in provider_turns:
                    raise ContractError(
                        f"{name} 存在重复 Provider attempt turn", kind="evidence"
                    )
                provider_turns.add(turn)

    def _provider_turn(
        self,
        *,
        run: dict[str, Any],
        run_path: Path,
        messages: Sequence[Mapping[str, str]],
        turn: int,
        action_validator: Callable[[str], Mapping[str, Any]],
    ) -> dict[str, Any]:
        attempt_sha = sha256_bytes(
            canonical_json(
                {
                    "run_id": run["run_id"],
                    "request_sha256": run["request_sha256"],
                    "turn": turn,
                    "policy_sha256": self.record_policy_sha256
                    if run["kind"] == "memento_interpretation_run"
                    else self.daily_policy_sha256,
                }
            ).encode("utf-8")
        )
        completion_path = self._completion_path(run_path, run["run_id"], turn)
        if completion_path.exists():
            raise ContractError("provider completion 在 attempt 前已存在", kind="evidence")
        run["steps"].append(self._attempt_step(turn, attempt_sha))
        run["updated_at"] = _now_text(self.clock)
        self.files.write_mutable(run_path, run)

        lock: ContextManager[None] | None = None
        entered = False
        try:
            lock = self.lock_factory(self.vault) if self.lock_factory is not None else contextlib.nullcontext()
            lock.__enter__()
            entered = True
        except BaseException as exc:
            # The paid call has not started.  Resolve this marker so recovery
            # does not misreport an unknown provider outcome.
            self._resolve_attempt_step(
                run,
                turn=turn,
                attempt_sha256=attempt_sha,
                result_kind="lock_error",
                error_kind="provider_lock",
            )
            self._terminalize(run, run_path, status="error", error_kind="provider_lock")
            if isinstance(exc, ContractError):
                raise
            raise ContractError("共享 Provider 锁无法获取", kind="runtime") from exc

        try:
            # Any exception after entering complete leaves the durable marker
            # outstanding.  Recovery must classify it as unknown_attempt.
            result = self.provider.complete(messages)
            content = getattr(result, "content", None)
            usage = getattr(result, "usage", None)
            request_id = getattr(result, "request_id", None)
            returned_model = getattr(result, "model", None)
            if isinstance(result, Mapping):
                content = result.get("content")
                usage = result.get("usage")
                request_id = result.get("request_id")
                returned_model = result.get("model")
            if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_COMPLETION_BYTES:
                raise ContractError("Provider completion content 无效", kind="provider_contract")
            raw_content_sha256 = sha256_bytes(content.encode("utf-8"))
            validation_error_kind: str | None = None
            canonical_action: str | None
            try:
                # Persist only a strict, normalized action.  Invalid provider
                # text can contain copied source material or secrets and must
                # never become a durable recovery artifact.
                canonical_action = canonical_json(dict(action_validator(content)))
            except ContractError as exc:
                canonical_action = None
                validation_error_kind = exc.kind
            raw_usage = dict(usage) if isinstance(usage, Mapping) else None
            missing = usage_is_missing(raw_usage)
            normalized_usage = None if missing else normalize_usage(raw_usage)
            safe_request_id = (
                "preq_" + sha256_bytes(request_id.encode("utf-8"))[:24]
                if isinstance(request_id, str) and request_id
                else None
            )
            persisted_content_sha256 = (
                raw_content_sha256
                if canonical_action is None
                else sha256_bytes(canonical_action.encode("utf-8"))
            )
            completion = {
                "schema_version": COGNITIVE_SCHEMA_VERSION,
                "kind": "memento_provider_action_completion",
                "run_id": run["run_id"],
                "turn": turn,
                "attempt_sha256": attempt_sha,
                "completed_at": _now_text(self.clock),
                "provider": self.provider_name,
                "model": self.model,
                "request_id": safe_request_id,
                "content": canonical_action,
                "content_sha256": persisted_content_sha256,
                "validation_error_kind": validation_error_kind,
                "usage": normalized_usage,
                "usage_missing": missing,
            }
            # This immutable write happens before the attempt is resolved.  A
            # crash afterwards is repairable without another provider call.
            self.files.write_immutable(completion_path, completion)
        except BaseException:
            try:
                assert lock is not None
                lock.__exit__(*__import__("sys").exc_info())
            finally:
                entered = False
            raise
        else:
            assert lock is not None
            lock.__exit__(None, None, None)
            entered = False
        finally:
            if entered:
                with contextlib.suppress(Exception):
                    assert lock is not None
                    lock.__exit__(None, None, None)

        self._apply_completion(run, completion)
        self._resolve_attempt_step(
            run,
            turn=turn,
            attempt_sha256=attempt_sha,
            result_kind="completed",
            error_kind=None,
        )
        run["updated_at"] = _now_text(self.clock)
        self.files.write_mutable(run_path, run)
        self._audit_completion(completion)
        return completion

    def _recover_outstanding_attempt(
        self, run: dict[str, Any], run_path: Path
    ) -> dict[str, Any] | None:
        outstanding = [
            step
            for step in run["steps"]
            if step["action"] == "provider_attempt"
            and step["result_kind"] == "provider_attempt_started"
        ]
        if not outstanding:
            return None
        if len(outstanding) != 1:
            raise ContractError("存在多个未解析 Provider attempt", kind="evidence")
        step = outstanding[0]
        turn = step["turn"]
        completion_path = self._completion_path(run_path, run["run_id"], turn)
        if not completion_path.exists():
            self._resolve_attempt_step(
                run,
                turn=turn,
                attempt_sha256=step["arguments_sha256"],
                result_kind="unknown",
                error_kind="unknown_attempt",
            )
            self._terminalize(run, run_path, status="error", error_kind="unknown_attempt")
            return {"unknown_attempt": True}
        completion = self.files.read_json(
            completion_path, name="provider completion", maximum=MAX_COMPLETION_BYTES + 50_000
        )
        self._validate_completion(completion, run, step)
        self._apply_completion(run, completion)
        self._resolve_attempt_step(
            run,
            turn=turn,
            attempt_sha256=step["arguments_sha256"],
            result_kind="completed",
            error_kind=None,
        )
        run["updated_at"] = _now_text(self.clock)
        self.files.write_mutable(run_path, run)
        return completion

    def _validate_completion(
        self, completion: Mapping[str, Any], run: Mapping[str, Any], step: Mapping[str, Any]
    ) -> None:
        fields = {
            "schema_version", "kind", "run_id", "turn", "attempt_sha256",
            "completed_at", "provider", "model", "request_id", "content",
            "content_sha256", "validation_error_kind", "usage", "usage_missing",
        }
        if set(completion) != fields:
            raise ContractError("provider completion 字段不符合合同", kind="evidence")
        if completion["schema_version"] != COGNITIVE_SCHEMA_VERSION or completion["kind"] != "memento_provider_action_completion":
            raise ContractError("provider completion schema/kind 无效", kind="evidence")
        if completion["run_id"] != run["run_id"] or completion["turn"] != step["turn"] or completion["attempt_sha256"] != step["arguments_sha256"]:
            raise ContractError("provider completion 未绑定当前 attempt", kind="evidence")
        if completion["provider"] != self.provider_name or completion["model"] != self.model:
            raise ContractError("provider completion 配置身份无效", kind="evidence")
        provider_request_id = completion["request_id"]
        if provider_request_id is not None and (
            not isinstance(provider_request_id, str)
            or not re.fullmatch(r"preq_[0-9a-f]{24}", provider_request_id)
        ):
            raise ContractError("provider completion request_id 无效", kind="evidence")
        content = completion["content"]
        validation_error_kind = completion["validation_error_kind"]
        content_sha256 = _sha(
            completion["content_sha256"], "provider completion content_sha256"
        )
        if content is None:
            if not isinstance(validation_error_kind, str) or not validation_error_kind:
                raise ContractError("provider completion 拒绝状态无效", kind="evidence")
        else:
            if not isinstance(content, str) or validation_error_kind is not None:
                raise ContractError("provider completion content 无效", kind="evidence")
            try:
                if canonical_json(json.loads(content)) != content:
                    raise ContractError(
                        "provider completion action 未规范化", kind="evidence"
                    )
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ContractError(
                    "provider completion action 无效", kind="evidence"
                ) from exc
            if sha256_bytes(content.encode("utf-8")) != content_sha256:
                raise ContractError(
                    "provider completion action hash 不匹配", kind="evidence"
                )
        usage_value = completion["usage"]
        if usage_value is not None:
            if not isinstance(usage_value, Mapping) or set(usage_value) != NORMALIZED_USAGE_FIELDS:
                raise ContractError("provider completion usage 字段无效", kind="evidence")
            if any(type(value) is not int or value < 0 for value in usage_value.values()):
                raise ContractError("provider completion usage 值无效", kind="evidence")
        expected_missing = usage_is_missing(
            usage_value if isinstance(usage_value, Mapping) else None
        )
        if completion["usage_missing"] is not expected_missing:
            raise ContractError("provider completion usage_missing 不一致", kind="evidence")
        self._validate_timestamp(completion["completed_at"], "completed_at")

    def _apply_completion(self, run: dict[str, Any], completion: Mapping[str, Any]) -> None:
        current = run.get("usage")
        if current is None:
            current = {
                "model_calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "reasoning_tokens": 0,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 0,
                "usage_missing": False,
                "cost_usd": 0.0,
                "cost_complete": True,
            }
        current = dict(current)
        current["model_calls"] += 1

        if completion["usage_missing"]:
            for field in NORMALIZED_USAGE_FIELDS:
                current[field] = None
            current["usage_missing"] = True
            current["cost_usd"] = None
            current["cost_complete"] = False
            run["usage"] = current
            return

        normalized = normalize_usage(completion["usage"])
        if current["usage_missing"]:
            # Once any paid turn has incomplete usage, aggregate token totals
            # remain unknowable.  Keep the exact call count without presenting
            # the known subset as a complete total.
            run["usage"] = current
            return
        for field in (
            "prompt_tokens", "completion_tokens", "total_tokens",
            "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "reasoning_tokens",
        ):
            current[field] += normalized[field]
        try:
            cost = calculate_cost(normalized, pricing_for_model(self.model))
        except ContractError:
            current["cost_usd"] = None
            current["cost_complete"] = False
        else:
            if current["cost_complete"]:
                current["cost_usd"] = round(float(current["cost_usd"]) + cost, 10)
        run["usage"] = current

    def _validate_run_usage(self, value: Any, name: str) -> None:
        if value is None:
            return
        if not isinstance(value, Mapping) or set(value) != RUN_USAGE_FIELDS:
            raise ContractError(f"{name} usage 字段不符合合同", kind="evidence")
        if type(value["model_calls"]) is not int or value["model_calls"] < 1:
            raise ContractError(f"{name} model_calls 无效", kind="evidence")
        if type(value["usage_missing"]) is not bool or type(value["cost_complete"]) is not bool:
            raise ContractError(f"{name} usage 状态无效", kind="evidence")
        token_values = [value[field] for field in NORMALIZED_USAGE_FIELDS]
        if value["usage_missing"]:
            if any(item is not None for item in token_values):
                raise ContractError(f"{name} 缺失 usage 冒充已知值", kind="evidence")
        elif any(type(item) is not int or item < 0 for item in token_values):
            raise ContractError(f"{name} token usage 无效", kind="evidence")
        cost = value["cost_usd"]
        if value["cost_complete"]:
            if value["usage_missing"] or type(cost) not in {int, float} or cost < 0:
                raise ContractError(f"{name} cost usage 无效", kind="evidence")
        elif cost is not None:
            raise ContractError(f"{name} 未完成 cost 必须为 null", kind="evidence")

    def _audit_completion(self, completion: Mapping[str, Any]) -> None:
        if self.usage_auditor is None:
            return
        self.usage_auditor(
            self.vault,
            model=completion["model"],
            provider=completion["provider"],
            usage=completion["usage"],
            request_id=completion["request_id"],
        )

    def _terminalize(
        self,
        run: dict[str, Any],
        run_path: Path,
        *,
        status: str,
        error_kind: str | None,
    ) -> None:
        now = _now_text(self.clock)
        run["status"] = status
        if run["kind"] == "memento_daily_integration_run":
            run["stage"] = "finished"
        run["updated_at"] = now
        run["completed_at"] = now
        run["error_kind"] = error_kind
        self.files.write_mutable(run_path, run)

    def _result(
        self,
        *,
        request: Mapping[str, Any],
        run: Mapping[str, Any],
        cached: bool,
        receipt: Mapping[str, Any] | None = None,
        candidate_bundle: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "staged" if candidate_bundle is not None else run["status"],
            "cached": cached,
            "request": dict(request),
            "run": dict(run),
            "receipt": None if receipt is None else dict(receipt),
            "candidate_bundle": None if candidate_bundle is None else dict(candidate_bundle),
        }

    # ------------------------------------------------------------------
    # Record Interpreter

    def run_interpretation(
        self,
        request_id: str,
        *,
        target_objects: Sequence[ObjectRef | Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        request, request_sha = self._load_interpretation_request(request_id)
        record_ref = ObjectRef.from_dict(request["record_ref"])
        object_catalog: list[dict[str, Any]] = self.materialize_object_refs(
            target_objects
        )
        target_object_manifest_sha = sha256_bytes(
            canonical_json(object_catalog).encode("utf-8")
        )
        input_hashes = {
            "record_revision_sha256": record_ref.revision_sha256,
            "feedback_watermark_sha256": request["feedback_watermark_sha256"],
            "policy_sha256": self.record_policy_sha256,
        }
        run_key = _id24(
            "irk_",
            "interpretation-run-key-v1",
            {
                "record_id": record_ref.id,
                "record_revision": record_ref.revision,
                "target_object_manifest_sha256": target_object_manifest_sha,
                **input_hashes,
            },
        )
        run_id = _id24(
            "irun_", "interpretation-run-v1", {"request_id": request_id, "run_key": run_key}
        )
        run_path = self.files.interpretation_runs / f"{run_id}.json"
        evidence_catalog: list[dict[str, Any]] | None = None

        # The material run key, rather than the request-derived run id, is the
        # idempotency boundary shared by concurrent requests.
        with self.files.lock(run_key):
            if run_path.exists():
                run = self.files.read_json(run_path, name="interpretation run")
                self._validate_interpretation_run(run, run_id, request_id, request_sha, run_key)
                terminal = self._record_terminal_result(request, run, cached=True)
                if terminal is not None:
                    return terminal
                current_ref = ObjectRef.from_dict(
                    self.store.load_head_ref(record_ref.id)
                )
                if current_ref != record_ref:
                    self._terminalize(
                        run, run_path, status="stale", error_kind="stale"
                    )
                    return self._result(request=request, run=run, cached=False)
                recovered = self._recover_outstanding_attempt(run, run_path)
                if recovered is not None and recovered.get("unknown_attempt") is True:
                    return self._result(request=request, run=run, cached=True)
                completion = recovered
                if completion is None:
                    completion = self._load_resolved_completion(run, run_path)
                    if completion is None:
                        self._terminalize(
                            run, run_path, status="error", error_kind="recovery_incomplete"
                        )
                        return self._result(request=request, run=run, cached=True)
            else:
                current_ref = ObjectRef.from_dict(
                    self.store.load_head_ref(record_ref.id)
                )
                if current_ref != record_ref:
                    run = self._new_interpretation_run(
                        run_id, request_id, request_sha, run_key, input_hashes
                    )
                    self._terminalize(
                        run, run_path, status="stale", error_kind="stale"
                    )
                    return self._result(request=request, run=run, cached=False)
                if (
                    self._current_action_watermark()
                    != request["feedback_watermark_sha256"]
                ):
                    # Every paid interpretation is authorized by the exact
                    # current user-action snapshot.  Resolve an obsolete or
                    # caller-supplied watermark before entering Provider;
                    # material identity changes can then create a fresh run.
                    run = self._new_interpretation_run(
                        run_id, request_id, request_sha, run_key, input_hashes
                    )
                    self._terminalize(
                        run, run_path, status="stale", error_kind="stale"
                    )
                    return self._result(request=request, run=run, cached=False)
                cached_run = self._find_interpretation_cache(run_key)
                if cached_run is not None:
                    # A cache entry is reusable only while the frozen user
                    # action set is still current.  Otherwise a newly created
                    # request could inherit an older AI result after the user
                    # edited its receipt, bypassing the commit-time guard
                    # without ever reaching that guard.
                    from cognitive_actions_v1 import CognitiveActionStore

                    _, current_feedback_watermark = CognitiveActionStore(
                        self.vault, state_root=self.files.root
                    ).action_watermark()
                    if (
                        current_feedback_watermark
                        != request["feedback_watermark_sha256"]
                    ):
                        cached_run = None
                if cached_run is not None:
                    run = self._new_interpretation_run(
                        run_id, request_id, request_sha, run_key, input_hashes
                    )
                    run["status"] = cached_run["status"]
                    run["receipt_ref"] = cached_run["receipt_ref"]
                    self._append_action_step(
                        run,
                        turn=0,
                        action="cache_hit",
                        reason_code="material_run_key_match",
                        arguments={"run_key": run_key},
                        result_kind="receipt" if run["receipt_ref"] else "no_candidate",
                        result_count=1 if run["receipt_ref"] else 0,
                    )
                    now = _now_text(self.clock)
                    run["updated_at"] = now
                    run["completed_at"] = now
                    self.files.write_mutable(run_path, run)
                    return self._record_terminal_result(request, run, cached=True)  # type: ignore[return-value]

                blocker = self._interpretation_material_paid_call_blocker(
                    request=request,
                    run_key=run_key,
                    input_hashes=input_hashes,
                )
                if blocker is not None:
                    blocker_run, blocker_reason = blocker
                    run = self._new_interpretation_run(
                        run_id, request_id, request_sha, run_key, input_hashes
                    )
                    self._append_action_step(
                        run,
                        turn=0,
                        action="cache_hit",
                        reason_code="material_attempt_blocked",
                        arguments={
                            "run_key": run_key,
                            "blocker_run_id": blocker_run["run_id"],
                            "blocker_reason": blocker_reason,
                        },
                        result_kind="error",
                        result_count=0,
                        error_kind="material_attempt_blocked",
                    )
                    self._terminalize(
                        run,
                        run_path,
                        status="error",
                        error_kind="material_attempt_blocked",
                    )
                    return self._result(
                        request=request,
                        run=run,
                        cached=True,
                    )

                evidence_catalog = self.materialize_record_evidence(record_ref.id)
                authorized_input = {
                    "record_ref": record_ref.to_dict(),
                    "feedback_watermark_sha256": request["feedback_watermark_sha256"],
                    "source_catalog": evidence_catalog,
                    "object_catalog": object_catalog,
                }
                messages = build_record_interpreter_messages(authorized_input)
                run = self._new_interpretation_run(
                    run_id, request_id, request_sha, run_key, input_hashes
                )
                self.files.write_mutable(run_path, run)
                completion = self._provider_turn(
                    run=run,
                    run_path=run_path,
                    messages=messages,
                    turn=1,
                    action_validator=lambda content: parse_record_interpreter_action(
                        content,
                        allowed_source_ref_ids=[
                            item["ref_id"] for item in evidence_catalog
                        ],
                        allowed_target_ref_ids=[
                            item["ref_id"] for item in object_catalog
                        ],
                    ),
                )

            # Recovery recomputes the exact authorized catalogs; it never
            # trusts refs found only in provider output.
            try:
                refreshed_evidence = self.materialize_record_evidence(record_ref.id)
                refreshed_objects = self.materialize_object_refs(target_objects)
            except ContractError as exc:
                if exc.kind == "stale":
                    self._terminalize(run, run_path, status="stale", error_kind="stale")
                    return self._result(request=request, run=run, cached=False)
                raise
            if evidence_catalog is not None and evidence_catalog != refreshed_evidence:
                self._terminalize(run, run_path, status="stale", error_kind="stale")
                return self._result(request=request, run=run, cached=False)
            if object_catalog is not None and object_catalog != refreshed_objects:
                self._terminalize(run, run_path, status="stale", error_kind="stale")
                return self._result(request=request, run=run, cached=False)
            evidence_catalog = refreshed_evidence
            object_catalog = refreshed_objects
            if completion["content"] is None:
                self._append_action_step(
                    run,
                    turn=1,
                    action="invalid_action",
                    reason_code="validation_failed",
                    arguments={"completion_sha256": completion["content_sha256"]},
                    result_kind="error",
                    result_count=0,
                    error_kind=completion["validation_error_kind"],
                )
                self._terminalize(
                    run,
                    run_path,
                    status="error",
                    error_kind=completion["validation_error_kind"],
                )
                return self._result(request=request, run=run, cached=False)
            try:
                action = parse_record_interpreter_action(
                    completion["content"],
                    allowed_source_ref_ids=[item["ref_id"] for item in evidence_catalog],
                    allowed_target_ref_ids=[item["ref_id"] for item in object_catalog],
                )
            except ContractError as exc:
                self._append_action_step(
                    run,
                    turn=1,
                    action="invalid_action",
                    reason_code="validation_failed",
                    arguments={"completion_sha256": completion["content_sha256"]},
                    result_kind="error",
                    result_count=0,
                    error_kind=exc.kind,
                )
                self._terminalize(run, run_path, status="error", error_kind=exc.kind)
                return self._result(request=request, run=run, cached=False)

            if action["action"] == "finish":
                self._append_action_step(
                    run,
                    turn=1,
                    action="finish",
                    reason_code=action["reason_code"],
                    arguments=action["arguments"],
                    result_kind="no_candidate",
                    result_count=0,
                )
                self._terminalize(run, run_path, status="no_candidate", error_kind=None)
                return self._result(request=request, run=run, cached=False)

            # A valid finish action is terminal and writes no AI-derived
            # object, so it remains safe to preserve the semantic
            # ``no_candidate`` result even when billing metadata is partial.
            # Any action that would materialize a receipt still requires a
            # complete usage object; unknown cost can never accompany a
            # durable derived write.
            if completion["usage_missing"]:
                self._append_action_step(
                    run,
                    turn=1,
                    action="finish",
                    reason_code="usage_missing",
                    arguments={},
                    result_kind="error",
                    result_count=0,
                    error_kind="usage_missing",
                )
                self._terminalize(run, run_path, status="error", error_kind="usage_missing")
                return self._result(request=request, run=run, cached=False)

            # Source and target CAS immediately before receipt materialization.
            if evidence_catalog != self.materialize_record_evidence(record_ref.id):
                self._terminalize(run, run_path, status="stale", error_kind="stale")
                return self._result(request=request, run=run, cached=False)
            if object_catalog != self.materialize_object_refs(target_objects):
                self._terminalize(run, run_path, status="stale", error_kind="stale")
                return self._result(request=request, run=run, cached=False)
            try:
                receipt, receipt_ref = self._commit_receipt(
                    request=request,
                    run=run,
                    action=action,
                    evidence_catalog=evidence_catalog,
                    object_catalog=object_catalog,
                )
            except ContractError as exc:
                self._append_action_step(
                    run,
                    turn=1,
                    action="propose_receipt",
                    reason_code=action["reason_code"],
                    arguments=action["arguments"],
                    result_kind="error",
                    result_count=0,
                    error_kind=exc.kind,
                )
                terminal_status = "stale" if exc.kind == "stale" else "error"
                self._terminalize(
                    run,
                    run_path,
                    status=terminal_status,
                    error_kind=exc.kind,
                )
                return self._result(request=request, run=run, cached=False)
            self._append_action_step(
                run,
                turn=1,
                action="propose_receipt",
                reason_code=action["reason_code"],
                arguments=action["arguments"],
                result_kind="receipt",
                result_count=1,
            )
            run["receipt_ref"] = receipt_ref.to_dict()
            self._terminalize(run, run_path, status="completed", error_kind=None)
            return self._result(request=request, run=run, cached=False, receipt=receipt)

    def _new_interpretation_run(
        self,
        run_id: str,
        request_id: str,
        request_sha: str,
        run_key: str,
        input_hashes: Mapping[str, str],
    ) -> dict[str, Any]:
        now = _now_text(self.clock)
        return {
            "schema_version": COGNITIVE_SCHEMA_VERSION,
            "kind": "memento_interpretation_run",
            "run_id": run_id,
            "request_id": request_id,
            "request_sha256": request_sha,
            "run_key": run_key,
            "status": "running",
            "started_at": now,
            "updated_at": now,
            "completed_at": None,
            "provider": self.provider_name,
            "model": self.model,
            "contract_version": RECORD_INTERPRETER_CONTRACT_VERSION,
            "input_hashes": dict(input_hashes),
            "steps": [],
            "usage": None,
            "receipt_ref": None,
            "error_kind": None,
        }

    def _validate_interpretation_run(
        self, run: Mapping[str, Any], run_id: str, request_id: str, request_sha: str, run_key: str
    ) -> None:
        fields = {
            "schema_version", "kind", "run_id", "request_id", "request_sha256",
            "run_key", "status", "started_at", "updated_at", "completed_at",
            "provider", "model", "contract_version", "input_hashes", "steps",
            "usage", "receipt_ref", "error_kind",
        }
        if set(run) != fields:
            raise ContractError("interpretation run 字段不符合合同", kind="evidence")
        if run["schema_version"] != COGNITIVE_SCHEMA_VERSION or run["kind"] != "memento_interpretation_run":
            raise ContractError("interpretation run schema/kind 无效", kind="evidence")
        if not isinstance(run["run_id"], str) or not re.fullmatch(
            r"irun_[0-9a-f]{24}", run["run_id"]
        ):
            raise ContractError("interpretation run id 无效", kind="evidence")
        if not isinstance(run["request_id"], str) or not re.fullmatch(
            r"ireq_[0-9a-f]{24}", run["request_id"]
        ):
            raise ContractError("interpretation request id 无效", kind="evidence")
        _sha(run["request_sha256"], "interpretation request_sha256")
        if not isinstance(run["run_key"], str) or not re.fullmatch(
            r"irk_[0-9a-f]{24}", run["run_key"]
        ):
            raise ContractError("interpretation run key 无效", kind="evidence")
        if (run["run_id"], run["request_id"], run["request_sha256"], run["run_key"]) != (run_id, request_id, request_sha, run_key):
            raise ContractError("interpretation run 未绑定当前 request/run key", kind="evidence")
        if (
            run["provider"] != self.provider_name
            or run["model"] != self.model
            or run["contract_version"] != RECORD_INTERPRETER_CONTRACT_VERSION
        ):
            raise ContractError("interpretation run provider/policy 无效", kind="evidence")
        input_fields = {
            "record_revision_sha256", "feedback_watermark_sha256", "policy_sha256"
        }
        if not isinstance(run["input_hashes"], Mapping) or set(run["input_hashes"]) != input_fields:
            raise ContractError("interpretation input_hashes 无效", kind="evidence")
        for field in input_fields:
            _sha(run["input_hashes"][field], f"interpretation {field}")
        if run["status"] not in {"running", "completed", "no_candidate", "stale", "error", "budget_exhausted"}:
            raise ContractError("interpretation run status 无效", kind="evidence")
        self._validate_timestamp(run["started_at"], "started_at")
        self._validate_timestamp(run["updated_at"], "updated_at")
        if run["status"] == "running":
            if run["completed_at"] is not None or run["receipt_ref"] is not None:
                raise ContractError("running interpretation run 带终态字段", kind="evidence")
        else:
            self._validate_timestamp(run["completed_at"], "completed_at")
        if run["receipt_ref"] is not None:
            ref = ObjectRef.from_dict(run["receipt_ref"])
            if ref.kind != "interpretation_receipt" or run["status"] != "completed":
                raise ContractError("interpretation receipt_ref 与状态不一致", kind="evidence")
        elif run["status"] == "completed":
            raise ContractError("completed interpretation 缺少 receipt_ref", kind="evidence")
        self._validate_run_usage(run["usage"], "interpretation run")
        self._validate_steps(run["steps"], "interpretation run")

    def _record_terminal_result(
        self, request: Mapping[str, Any], run: Mapping[str, Any], *, cached: bool
    ) -> dict[str, Any] | None:
        if run["status"] == "running":
            return None
        receipt = None
        if run["receipt_ref"] is not None:
            ref = ObjectRef.from_dict(run["receipt_ref"])
            if ref.kind != "interpretation_receipt":
                raise ContractError("run receipt_ref kind 无效", kind="evidence")
            receipt = self._resolve_object(ref)
        return self._result(request=request, run=run, cached=cached, receipt=receipt)

    def _find_interpretation_cache(self, run_key: str) -> dict[str, Any] | None:
        for path in sorted(self.files.interpretation_runs.glob("irun_*.json")):
            run = self.files.read_json(path, name="cached interpretation run")
            if run.get("kind") != "memento_interpretation_run" or run.get("run_key") != run_key:
                continue
            self._validate_interpretation_run(
                run, path.stem, run["request_id"], run["request_sha256"], run_key
            )
            if run.get("status") not in {"completed", "no_candidate"}:
                continue
            if run.get("receipt_ref") is not None:
                self._resolve_object(ObjectRef.from_dict(run["receipt_ref"]))
            return run
        return None

    def _load_resolved_completion(
        self, run: Mapping[str, Any], run_path: Path
    ) -> dict[str, Any] | None:
        provider_steps = [step for step in run["steps"] if step["action"] == "provider_attempt"]
        if not provider_steps:
            return None
        step = provider_steps[-1]
        path = self._completion_path(run_path, run["run_id"], step["turn"])
        if not path.exists():
            return None
        completion = self.files.read_json(path, name="provider completion", maximum=MAX_COMPLETION_BYTES + 50_000)
        self._validate_completion(completion, run, step)
        return completion

    def _receipt_chain(self, receipt_id: str) -> list[tuple[dict[str, Any], str]]:
        paths = sorted(self.files.receipts.glob(f"{receipt_id}.r*.json"))
        result: list[tuple[dict[str, Any], str]] = []
        previous_sha: str | None = None
        previous: InterpretationReceiptRevision | None = None
        for expected_revision, path in enumerate(paths, start=1):
            if path.name != f"{receipt_id}.r{expected_revision:06d}.json":
                raise ContractError("receipt revision 链不连续", kind="evidence")
            value = self.files.read_json(path, name="receipt revision")
            parsed = InterpretationReceiptRevision.from_dict(value)
            if parsed.receipt_id != receipt_id or parsed.revision != expected_revision or parsed.previous_revision_sha256 != previous_sha:
                raise ContractError("receipt revision 链无效", kind="evidence")
            if previous is not None:
                try:
                    validate_interpretation_receipt_transition(previous, parsed)
                except ContractError as exc:
                    raise ContractError("receipt revision 迁移无效", kind="evidence") from exc
            file_sha = persisted_sha256(value)
            result.append((parsed.to_dict(), file_sha))
            previous = parsed
            previous_sha = file_sha
        return result

    def _commit_receipt(
        self,
        *,
        request: Mapping[str, Any],
        run: Mapping[str, Any],
        action: Mapping[str, Any],
        evidence_catalog: Sequence[Mapping[str, Any]],
        object_catalog: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], ObjectRef]:
        frozen_watermark = request["feedback_watermark_sha256"]
        # Import locally to keep the pure prompt/runtime dependency surface
        # narrow.  The guard uses the same owner-only lock as browser action
        # submission, so the watermark check and receipt visibility are one
        # atomic user-priority boundary.
        from cognitive_actions_v1 import CognitiveActionStore

        action_store = CognitiveActionStore(
            self.vault, state_root=self.files.root
        )
        with action_store.guard_action_watermark(frozen_watermark):
            return self._commit_receipt_under_action_guard(
                request=request,
                run=run,
                action=action,
                evidence_catalog=evidence_catalog,
                object_catalog=object_catalog,
            )

    def _commit_receipt_under_action_guard(
        self,
        *,
        request: Mapping[str, Any],
        run: Mapping[str, Any],
        action: Mapping[str, Any],
        evidence_catalog: Sequence[Mapping[str, Any]],
        object_catalog: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], ObjectRef]:
        record_ref = ObjectRef.from_dict(request["record_ref"])
        receipt_id = make_receipt_id(record_ref.id)
        evidence = {item["ref_id"]: SourceSpan.from_dict(item["span"]) for item in evidence_catalog}
        objects = {item["ref_id"]: ObjectRef.from_dict(item["object_ref"]) for item in object_catalog}
        arguments = action["arguments"]
        with self.files.lock(f"receipt-{receipt_id}"):
            chain = self._receipt_chain(receipt_id)
            for existing, existing_sha in chain:
                if existing["request_id"] == request["id"] and existing["run_id"] == run["run_id"]:
                    return existing, ObjectRef(
                        kind="interpretation_receipt", id=receipt_id,
                        revision=existing["revision"], revision_sha256=existing_sha,
                    )
            if chain:
                head = chain[-1][0]
                if head["status"] in {"original_only", "tombstone"}:
                    raise ContractError("receipt 终态之后不得追加 interpret revision", kind="conflict")
                source_changed = ObjectRef.from_dict(head["record_ref"]) != record_ref
                if source_changed != (request["trigger"] == "source_changed"):
                    raise ContractError("receipt source_changed trigger 与 source revision 不一致", kind="conflict")
            elif request["trigger"] == "source_changed":
                raise ContractError("source_changed 不得创建首个 receipt revision", kind="conflict")
            revision = len(chain) + 1
            previous_sha = chain[-1][1] if chain else None
            memory_candidates: list[dict[str, Any]] = []
            for index, raw in enumerate(arguments["memory_candidates"]):
                candidate_id = _id24(
                    "cmem_", "receipt-candidate-memory-v1",
                    {"receipt_id": receipt_id, "revision": revision, "index": index, "payload": raw},
                )
                memory_candidates.append(
                    {
                        "candidate_id": candidate_id,
                        "statement": raw["statement"],
                        "memory_kind": raw["memory_kind"],
                        "topics": list(raw["topics"]),
                        "purposes": list(raw["purposes"]),
                        "uncertainty": raw["uncertainty"],
                        "source_spans": [evidence[ref_id].to_dict() for ref_id in raw["source_ref_ids"]],
                    }
                )
            relation_candidates: list[dict[str, Any]] = []
            for index, raw in enumerate(arguments["relation_candidates"]):
                relation_candidates.append(
                    {
                        "candidate_id": _id24(
                            "crel_", "receipt-candidate-relation-v1",
                            {"receipt_id": receipt_id, "revision": revision, "index": index, "payload": raw},
                        ),
                        "type": raw["type"],
                        "from_ref": {
                            "kind": "candidate_memory",
                            "id": memory_candidates[raw["from_candidate_index"]]["candidate_id"],
                            "revision": None,
                            "revision_sha256": None,
                        },
                        "to_ref": objects[raw["to_ref_id"]].to_dict(),
                        "direction": raw["direction"],
                        "statement": raw["statement"],
                        "uncertainty": raw["uncertainty"],
                        "source_spans": [evidence[ref_id].to_dict() for ref_id in raw["source_ref_ids"]],
                    }
                )
            receipt = InterpretationReceiptRevision.from_dict(
                {
                    "schema_version": COGNITIVE_SCHEMA_VERSION,
                    "kind": "memento_interpretation_receipt_revision",
                    "receipt_id": receipt_id,
                    "revision": revision,
                    "status": "ready",
                    "operation": "interpret",
                    "created_at": _now_text(self.clock),
                    "request_id": request["id"],
                    "run_id": run["run_id"],
                    "record_ref": record_ref.to_dict(),
                    "user_action_id": None,
                    "summary": arguments["summary"],
                    "facets": dict(arguments["facets"]),
                    "memory_candidates": memory_candidates,
                    "relation_candidates": relation_candidates,
                    "source_spans": [evidence[ref_id].to_dict() for ref_id in arguments["source_ref_ids"]],
                    "contract_version": RECORD_INTERPRETER_CONTRACT_VERSION,
                    "feedback_watermark_sha256": request["feedback_watermark_sha256"],
                    "previous_revision_sha256": previous_sha,
                }
            )
            if chain:
                validate_interpretation_receipt_transition(
                    InterpretationReceiptRevision.from_dict(chain[-1][0]), receipt
                )
            path = self.files.receipts / f"{receipt_id}.r{revision:06d}.json"
            revision_sha = self.files.write_immutable(path, receipt.to_dict())
            return receipt.to_dict(), ObjectRef(
                kind="interpretation_receipt", id=receipt_id,
                revision=revision, revision_sha256=revision_sha,
            )

    # ------------------------------------------------------------------
    # Daily Integrator.  This stage intentionally stops before committing
    # reusable memories or relations.

    def run_daily(
        self,
        request_id: str,
        *,
        source_spans: Sequence[SourceSpan | Mapping[str, Any]],
        object_refs: Sequence[ObjectRef | Mapping[str, Any]] = (),
        receipt_refs: Sequence[ObjectRef | Mapping[str, Any]] = (),
        daily_context: Mapping[str, Any] | None = None,
        profile_sha256: str = ZERO_SHA256,
        user_action_watermark_sha256: str = ZERO_SHA256,
        inspect_memory: InspectMemory | None = None,
        search_history: SearchHistory | None = None,
    ) -> dict[str, Any]:
        request, request_sha = self._load_daily_request(request_id)
        material = self._daily_material_identity(
            request["local_date"],
            source_spans=source_spans,
            object_refs=object_refs,
            receipt_refs=receipt_refs,
            daily_context=daily_context,
            profile_sha256=profile_sha256,
            user_action_watermark_sha256=user_action_watermark_sha256,
        )
        context = material["context"]
        source_catalog = material["source_catalog"]
        object_catalog = material["object_catalog"]
        all_object_refs = material["all_object_refs"]
        input_manifest = material["input_manifest"]
        run_key = material["run_key"]
        run_id = _id24("drun_", "daily-run-v1", {"request_id": request_id, "run_key": run_key})
        run_path = self.files.daily_runs / f"{run_id}.json"

        # Serialize all requests for the same frozen material inputs so a
        # second request can observe and reuse the first staged result.
        with self.files.lock(run_key):
            if run_path.exists():
                run = self.files.read_json(run_path, name="daily run")
                self._validate_daily_run(run, run_id, request_id, request_sha, run_key)
                terminal = self._daily_terminal_result(request, run, cached=True)
                if terminal is not None:
                    return terminal
                recovered = self._recover_outstanding_attempt(run, run_path)
                if recovered is not None and recovered.get("unknown_attempt") is True:
                    return self._result(request=request, run=run, cached=True)
                if recovered is None:
                    self._terminalize(run, run_path, status="error", error_kind="recovery_incomplete")
                    return self._result(request=request, run=run, cached=True)
                pending_completion: dict[str, Any] | None = recovered
            else:
                cached_run = self._find_daily_cache(run_key)
                if cached_run is not None:
                    run = self._new_daily_run(
                        run_id, request_id, request_sha, run_key, input_manifest
                    )
                    run["status"] = cached_run["status"]
                    run["stage"] = cached_run["stage"]
                    run["bundle_ref"] = cached_run["bundle_ref"]
                    self._append_action_step(
                        run,
                        turn=0,
                        action="cache_hit",
                        reason_code="material_run_key_match",
                        arguments={"run_key": run_key},
                        result_kind="staged_bundle" if run["bundle_ref"] else "no_change",
                        result_count=1 if run["bundle_ref"] else 0,
                    )
                    if run["status"] != "running":
                        now = _now_text(self.clock)
                        run["updated_at"] = now
                        run["completed_at"] = now
                    self.files.write_mutable(run_path, run)
                    return self._daily_terminal_result(request, run, cached=True)  # type: ignore[return-value]
                run = self._new_daily_run(
                    run_id, request_id, request_sha, run_key, input_manifest
                )
                self.files.write_mutable(run_path, run)
                pending_completion = None

            authorized_input = {
                "local_date": request["local_date"],
                "source_catalog": source_catalog,
                "object_catalog": object_catalog,
                "daily_context": context,
                "input_manifest": input_manifest,
            }
            messages = build_daily_integrator_messages(authorized_input)
            source_by_id = {item["ref_id"]: item for item in source_catalog}
            object_by_id = {item["ref_id"]: item for item in object_catalog}
            model_turns = len(
                [step for step in run["steps"] if step["action"] == "provider_attempt"]
            )
            tool_calls = len(
                [step for step in run["steps"] if step["action"] in {"inspect_memory", "search_history"}]
            )

            while True:
                if pending_completion is None:
                    if model_turns >= self.daily_budget.max_model_turns:
                        self._terminalize(run, run_path, status="budget_exhausted", error_kind="model_turn_budget")
                        return self._result(request=request, run=run, cached=False)
                    model_turns += 1
                    pending_completion = self._provider_turn(
                        run=run,
                        run_path=run_path,
                        messages=messages,
                        turn=model_turns,
                        action_validator=lambda content: parse_daily_integrator_action(
                            content,
                            allowed_source_ref_ids=list(source_by_id),
                            allowed_object_ref_ids=list(object_by_id),
                        ),
                    )
                completion = pending_completion
                pending_completion = None
                if completion["usage_missing"]:
                    self._append_action_step(
                        run, turn=model_turns, action="finish", reason_code="usage_missing",
                        arguments={}, result_kind="error", result_count=0,
                        error_kind="usage_missing",
                    )
                    self._terminalize(run, run_path, status="error", error_kind="usage_missing")
                    return self._result(request=request, run=run, cached=False)
                if completion["content"] is None:
                    self._append_action_step(
                        run,
                        turn=model_turns,
                        action="invalid_action",
                        reason_code="validation_failed",
                        arguments={"completion_sha256": completion["content_sha256"]},
                        result_kind="error",
                        result_count=0,
                        error_kind=completion["validation_error_kind"],
                    )
                    self._terminalize(
                        run,
                        run_path,
                        status="error",
                        error_kind=completion["validation_error_kind"],
                    )
                    return self._result(request=request, run=run, cached=False)
                try:
                    action = parse_daily_integrator_action(
                        completion["content"],
                        allowed_source_ref_ids=list(source_by_id),
                        allowed_object_ref_ids=list(object_by_id),
                    )
                except ContractError as exc:
                    self._append_action_step(
                        run, turn=model_turns, action="invalid_action",
                        reason_code="validation_failed",
                        arguments={"completion_sha256": completion["content_sha256"]},
                        result_kind="error", result_count=0, error_kind=exc.kind,
                    )
                    self._terminalize(run, run_path, status="error", error_kind=exc.kind)
                    return self._result(request=request, run=run, cached=False)

                name = action["action"]
                arguments = action["arguments"]
                if name == "finish":
                    self._append_action_step(
                        run, turn=model_turns, action=name,
                        reason_code=action["reason_code"], arguments=arguments,
                        result_kind="no_change", result_count=0,
                    )
                    self._terminalize(run, run_path, status="no_change", error_kind=None)
                    return self._result(request=request, run=run, cached=False)

                if name in {"inspect_memory", "search_history"}:
                    if tool_calls >= self.daily_budget.max_tool_calls:
                        self._append_action_step(
                            run, turn=model_turns, action=name,
                            reason_code=action["reason_code"], arguments=arguments,
                            result_kind="budget_exhausted", result_count=0,
                            error_kind="tool_call_budget",
                        )
                        self._terminalize(run, run_path, status="budget_exhausted", error_kind="tool_call_budget")
                        return self._result(request=request, run=run, cached=False)
                    tool_calls += 1
                    try:
                        if name == "inspect_memory":
                            ref_id = arguments["memory_ref_id"]
                            catalog_item = object_by_id[ref_id]
                            object_ref = ObjectRef.from_dict(catalog_item["object_ref"])
                            tool_result = (
                                inspect_memory(object_ref)
                                if inspect_memory is not None
                                else catalog_item["snapshot"]
                            )
                            bounded = _json_only(
                                tool_result, "inspect_memory result", maximum=50_000
                            )
                            result_payload = {
                                "object_ref_id": ref_id, "snapshot": bounded
                            }
                            result_count = 1
                        else:
                            raw_results = (
                                search_history(
                                    arguments["query"], arguments["date_from"],
                                    arguments["date_to"], arguments["limit"],
                                )
                                if search_history is not None
                                else ()
                            )
                            if len(raw_results) > min(
                                arguments["limit"],
                                self.daily_budget.max_history_results_per_search,
                            ):
                                raise ContractError(
                                    "search_history 返回超过授权上限", kind="budget"
                                )
                            additions = self.materialize_source_spans(raw_results)
                            if any(
                                item["ref_id"] in source_by_id for item in additions
                            ):
                                raise ContractError(
                                    "search_history 返回了重复 evidence ref",
                                    kind="evidence",
                                )
                            for item in additions:
                                source_by_id[item["ref_id"]] = item
                            result_payload = {"source_catalog": additions}
                            result_count = len(additions)
                    except ContractError as exc:
                        self._append_action_step(
                            run, turn=model_turns, action=name,
                            reason_code=action["reason_code"], arguments=arguments,
                            result_kind="error", result_count=0,
                            error_kind=exc.kind,
                        )
                        self._terminalize(
                            run, run_path, status="error", error_kind=exc.kind
                        )
                        return self._result(request=request, run=run, cached=False)
                    except Exception:
                        self._append_action_step(
                            run, turn=model_turns, action=name,
                            reason_code=action["reason_code"], arguments=arguments,
                            result_kind="error", result_count=0,
                            error_kind="runtime",
                        )
                        self._terminalize(
                            run, run_path, status="error", error_kind="runtime"
                        )
                        return self._result(request=request, run=run, cached=False)
                    self._append_action_step(
                        run, turn=model_turns, action=name,
                        reason_code=action["reason_code"], arguments=arguments,
                        result_kind="tool_result", result_count=result_count,
                    )
                    run["updated_at"] = _now_text(self.clock)
                    self.files.write_mutable(run_path, run)
                    messages.append({"role": "assistant", "content": canonical_json(action)})
                    messages.append(
                        {
                            "role": "user",
                            "content": canonical_json(
                                {"kind": "ToolResult", "tool": name, "result": result_payload}
                            ),
                        }
                    )
                    continue

                # propose_daily_bundle
                current_spans = [SourceSpan.from_dict(item["span"]) for item in source_by_id.values()]
                refreshed = self.materialize_source_spans(current_spans)
                if refreshed != list(source_by_id.values()):
                    self._terminalize(run, run_path, status="stale", error_kind="stale")
                    return self._result(request=request, run=run, cached=False)
                if object_catalog != self.materialize_object_refs(all_object_refs):
                    self._terminalize(run, run_path, status="stale", error_kind="stale")
                    return self._result(request=request, run=run, cached=False)
                candidate = self._materialize_daily_candidate(
                    request=request,
                    run=run,
                    action=action,
                    source_by_id=source_by_id,
                    object_by_id=object_by_id,
                )
                stage_dir = self.files.daily_staging / run_id
                candidate_path = stage_dir / "candidate.json"
                candidate_sha = self.files.write_immutable(candidate_path, candidate)
                relative = candidate_path.relative_to(self.files.root).as_posix()
                run["stage"] = "validating"
                run["bundle_ref"] = {
                    "status": "staged", "path": relative, "sha256": candidate_sha
                }
                self._append_action_step(
                    run, turn=model_turns, action=name,
                    reason_code=action["reason_code"], arguments=arguments,
                    result_kind="staged_bundle", result_count=1,
                )
                run["updated_at"] = _now_text(self.clock)
                self.files.write_mutable(run_path, run)
                return self._result(
                    request=request, run=run, cached=False, candidate_bundle=candidate
                )

    def _new_daily_run(
        self,
        run_id: str,
        request_id: str,
        request_sha: str,
        run_key: str,
        input_manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        now = _now_text(self.clock)
        return {
            "schema_version": COGNITIVE_SCHEMA_VERSION,
            "kind": "memento_daily_integration_run",
            "run_id": run_id,
            "request_id": request_id,
            "request_sha256": request_sha,
            "run_key": run_key,
            "status": "running",
            "stage": "integrating",
            "started_at": now,
            "updated_at": now,
            "completed_at": None,
            "provider": self.provider_name,
            "model": self.model,
            "contract_version": DAILY_INTEGRATOR_CONTRACT_VERSION,
            "input_manifest": dict(input_manifest),
            "steps": [],
            "usage": None,
            "bundle_ref": None,
            "review_status": "not_started",
            "long_term_status": "not_started",
            "landscape_status": "not_started",
            "warnings": [],
            "error_kind": None,
        }

    def _validate_daily_run(
        self, run: Mapping[str, Any], run_id: str, request_id: str, request_sha: str, run_key: str
    ) -> None:
        fields = {
            "schema_version", "kind", "run_id", "request_id", "request_sha256",
            "run_key", "status", "stage", "started_at", "updated_at", "completed_at",
            "provider", "model", "contract_version", "input_manifest", "steps", "usage",
            "bundle_ref", "review_status", "long_term_status", "landscape_status",
            "warnings", "error_kind",
        }
        if set(run) != fields:
            raise ContractError("daily run 字段不符合合同", kind="evidence")
        if run["schema_version"] != COGNITIVE_SCHEMA_VERSION or run["kind"] != "memento_daily_integration_run":
            raise ContractError("daily run schema/kind 无效", kind="evidence")
        if not isinstance(run["run_id"], str) or not re.fullmatch(
            r"drun_[0-9a-f]{24}", run["run_id"]
        ):
            raise ContractError("daily run id 无效", kind="evidence")
        if not isinstance(run["request_id"], str) or not re.fullmatch(
            r"dreq_[0-9a-f]{24}", run["request_id"]
        ):
            raise ContractError("daily request id 无效", kind="evidence")
        _sha(run["request_sha256"], "daily request_sha256")
        if not isinstance(run["run_key"], str) or not re.fullmatch(
            r"drk_[0-9a-f]{24}", run["run_key"]
        ):
            raise ContractError("daily run key 无效", kind="evidence")
        if (run["run_id"], run["request_id"], run["request_sha256"], run["run_key"]) != (run_id, request_id, request_sha, run_key):
            raise ContractError("daily run 未绑定当前 request/run key", kind="evidence")
        if (
            run["provider"] != self.provider_name
            or run["model"] != self.model
            or run["contract_version"] != DAILY_INTEGRATOR_CONTRACT_VERSION
        ):
            raise ContractError("daily run provider/policy 无效", kind="evidence")
        input_fields = {
            "source_refs", "receipt_refs", "source_manifest_sha256",
            "receipt_manifest_sha256", "profile_sha256",
            "user_action_watermark_sha256", "policy_sha256",
        }
        manifest = run["input_manifest"]
        if not isinstance(manifest, Mapping) or set(manifest) != input_fields:
            raise ContractError("daily input_manifest 无效", kind="evidence")
        if not isinstance(manifest["source_refs"], list) or not isinstance(
            manifest["receipt_refs"], list
        ):
            raise ContractError("daily input refs 无效", kind="evidence")
        for raw in manifest["source_refs"]:
            if ObjectRef.from_dict(raw).kind != "source_record":
                raise ContractError("daily source ref kind 无效", kind="evidence")
        for raw in manifest["receipt_refs"]:
            if ObjectRef.from_dict(raw).kind != "interpretation_receipt":
                raise ContractError("daily receipt ref kind 无效", kind="evidence")
        for field in input_fields - {"source_refs", "receipt_refs"}:
            _sha(manifest[field], f"daily {field}")
        if sha256_bytes(canonical_json(manifest["source_refs"]).encode("utf-8")) != manifest["source_manifest_sha256"]:
            raise ContractError("daily source manifest hash 不一致", kind="evidence")
        if sha256_bytes(canonical_json(manifest["receipt_refs"]).encode("utf-8")) != manifest["receipt_manifest_sha256"]:
            raise ContractError("daily receipt manifest hash 不一致", kind="evidence")
        if run["status"] not in {"running", "committed", "committed_with_warnings", "no_change", "stale", "error", "budget_exhausted"}:
            raise ContractError("daily run status 无效", kind="evidence")
        if run["stage"] not in {
            "preparing", "completing_receipts", "integrating", "validating",
            "committing_bundle", "generating_review", "judging_long_term",
            "projecting", "finished",
        }:
            raise ContractError("daily run stage 无效", kind="evidence")
        self._validate_timestamp(run["started_at"], "started_at")
        self._validate_timestamp(run["updated_at"], "updated_at")
        if run["status"] == "running":
            if run["completed_at"] is not None:
                raise ContractError("running daily run 带 completed_at", kind="evidence")
        else:
            self._validate_timestamp(run["completed_at"], "completed_at")
        if not isinstance(run["warnings"], list) or any(
            warning not in {
                "review_failed", "long_term_failed", "landscape_failed",
                "partial_source_unavailable",
            }
            for warning in run["warnings"]
        ):
            raise ContractError("daily run warnings 无效", kind="evidence")
        self._validate_run_usage(run["usage"], "daily run")
        self._validate_steps(run["steps"], "daily run")

    def _daily_terminal_result(
        self, request: Mapping[str, Any], run: Mapping[str, Any], *, cached: bool
    ) -> dict[str, Any] | None:
        if run["bundle_ref"] is not None:
            ref = run["bundle_ref"]
            if not isinstance(ref, Mapping) or set(ref) != {"status", "path", "sha256"} or ref["status"] != "staged":
                raise ContractError("daily staged bundle ref 无效", kind="evidence")
            path = self.files.root / ref["path"]
            candidate = self.files.read_json(path, name="daily candidate bundle")
            if persisted_sha256(candidate) != ref["sha256"]:
                raise ContractError("daily candidate bundle hash 不一致", kind="evidence")
            return self._result(
                request=request, run=run, cached=cached, candidate_bundle=candidate
            )
        if run["status"] == "running":
            return None
        return self._result(request=request, run=run, cached=cached)

    def _find_daily_cache(self, run_key: str) -> dict[str, Any] | None:
        for path in sorted(self.files.daily_runs.glob("drun_*.json")):
            run = self.files.read_json(path, name="cached daily run")
            if run.get("kind") != "memento_daily_integration_run" or run.get("run_key") != run_key:
                continue
            self._validate_daily_run(
                run, path.stem, run["request_id"], run["request_sha256"], run_key
            )
            if run.get("status") == "no_change" or run.get("bundle_ref") is not None:
                # Validate staged bytes before permitting a zero-call reuse.
                if run.get("bundle_ref") is not None:
                    ref = run["bundle_ref"]
                    candidate_path = self.files.root / ref["path"]
                    candidate = self.files.read_json(candidate_path, name="cached daily candidate")
                    if persisted_sha256(candidate) != ref["sha256"]:
                        raise ContractError("cached daily candidate hash 不一致", kind="evidence")
                return run
        return None

    def _materialize_daily_candidate(
        self,
        *,
        request: Mapping[str, Any],
        run: Mapping[str, Any],
        action: Mapping[str, Any],
        source_by_id: Mapping[str, Mapping[str, Any]],
        object_by_id: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        arguments = action["arguments"]
        memories: list[dict[str, Any]] = []
        for index, raw in enumerate(arguments["memory_operations"]):
            target = raw["target_memory_ref_id"]
            memories.append(
                {
                    "operation_index": index,
                    "operation": raw["operation"],
                    "target_memory_ref": None if target is None else object_by_id[target]["object_ref"],
                    "statement": raw["statement"],
                    "memory_kind": raw["memory_kind"],
                    "topics": list(raw["topics"]),
                    "purposes": list(raw["purposes"]),
                    "uncertainty": raw["uncertainty"],
                    "source_spans": [source_by_id[ref_id]["span"] for ref_id in raw["source_ref_ids"]],
                }
            )

        def endpoint(value: Mapping[str, Any]) -> dict[str, Any]:
            if value["kind"] == "memory_operation":
                return {"kind": "memory_operation", "operation_index": value["memory_operation_index"]}
            return {"kind": "object", "object_ref": object_by_id[value["object_ref_id"]]["object_ref"]}

        relations: list[dict[str, Any]] = []
        for index, raw in enumerate(arguments["relation_operations"]):
            target = raw["target_relation_ref_id"]
            relations.append(
                {
                    "operation_index": index,
                    "operation": raw["operation"],
                    "target_relation_ref": None if target is None else object_by_id[target]["object_ref"],
                    "type": raw["type"],
                    "from_endpoint": endpoint(raw["from_endpoint"]),
                    "to_endpoint": endpoint(raw["to_endpoint"]),
                    "direction": raw["direction"],
                    "statement": raw["statement"],
                    "uncertainty": raw["uncertainty"],
                    "source_spans": [source_by_id[ref_id]["span"] for ref_id in raw["source_ref_ids"]],
                }
            )
        return {
            "schema_version": COGNITIVE_SCHEMA_VERSION,
            "kind": "memento_daily_candidate_bundle",
            "status": "staged",
            "created_at": _now_text(self.clock),
            "local_date": request["local_date"],
            "request_id": request["id"],
            "run_id": run["run_id"],
            "run_key": run["run_key"],
            "input_manifest": dict(run["input_manifest"]),
            "summary": {
                "overview": arguments["overview"],
                "themes": list(arguments["themes"]),
                "changes": list(arguments["changes"]),
                "unresolved_questions": list(arguments["unresolved_questions"]),
                "action_clues": list(arguments["action_clues"]),
            },
            "memory_operations": memories,
            "relation_operations": relations,
            "source_ref_ids": sorted(
                {
                    ref_id
                    for raw in arguments["memory_operations"] + arguments["relation_operations"]
                    for ref_id in raw["source_ref_ids"]
                }
            ),
            "formal_objects_committed": False,
        }


__all__ = [
    "CompletionProvider",
    "CognitiveRuntime",
    "make_evidence_ref_id",
    "make_object_ref_id",
]
