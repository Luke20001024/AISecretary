#!/usr/bin/env python3
"""Isolated W1/A1 live pairing for the priority-revision case.

The default command is plan-only and cannot construct a provider.  Live mode
requires two explicit switches and always runs against freshly cloned,
checked-in synthetic records.  No CLI option accepts a Vault path.

Public reports contain only a deliberately small allow-list of aggregate
metrics, hashes, action names, and finite error codes.  Prompts, record text,
memory text, local paths, request identifiers, and credentials are never
serialized.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import dataclasses
import hashlib
import importlib
import inspect
import json
import math
import os
import re
import stat
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
CONTEXT_AGENT_ROOT = HERE.parents[1]
SCENARIO_ROOT = CONTEXT_AGENT_ROOT / "eval" / "scenarios" / "product-manager-20d"

if str(CONTEXT_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(CONTEXT_AGENT_ROOT))

agent_v1 = importlib.import_module("agent_v1")
core = importlib.import_module("core")
deepseek_provider = importlib.import_module("deepseek_provider")
reflection = importlib.import_module("reflection")


REPORT_SCHEMA_VERSION = "remember_agent_live_pairing.v1"
LIVE_CONFIRMATION = "LIVE_SYNTHETIC_ONLY"
CASE_ID = "priority_revision"
CASE_AS_OF = "2026-07-17"
CASE_SOURCES = ("2026-07-14.md", "2026-07-17.md")
OLD_MARKER = "先把新用户激活作为最高优先级"
NEW_MARKER = "三天前关于激活优先的决定被本次决定替代"
NEW_PRIORITY_MARKER = "我们决定本轮把 30 日留存作为最高优先级"
SEARCH_QUERY = "本次决定替代"
W1_TERMINAL_POLICY_VERSION = "w1-terminal-only-v1"
W1_TERMINAL_INSTRUCTION = (
    "<workflow_constraint version=\"w1-terminal-only-v1\">"
    "读取记忆与检索步骤已由固定 Workflow 完成。本轮只能直接输出 "
    "finalize_patch 或 finish 的顶层四键 JSON 对象；不得再输出 read_memory "
    "或 search_history。</workflow_constraint>"
)
SUPPORTED_MODELS = ("deepseek-v4-pro", "deepseek-v4-flash")
PUBLIC_ERROR_CODES = frozenset(
    {
        "none",
        "confirmation_required",
        "provider_error",
        "usage_missing",
        "call_limit",
        "token_limit",
        "cost_limit",
        "security",
        "contract",
        "invalid_terminal_action",
        "plan_mismatch",
        "agent_error",
        "runtime",
    }
)


class PairingAbort(RuntimeError):
    """Finite, report-safe stop signal; never carries model or source text."""

    def __init__(
        self,
        code: str,
        *,
        usage: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        model: str | None = None,
    ) -> None:
        if code not in PUBLIC_ERROR_CODES:
            code = "contract"
        super().__init__(code)
        self.code = code
        self.usage = dict(usage) if isinstance(usage, Mapping) else None
        self.request_id = request_id
        self.model = model


@dataclasses.dataclass(frozen=True)
class PairingConfig:
    model: str = "deepseek-v4-pro"
    repeats: int = 1
    timeout: float = 60.0
    max_tokens_per_call: int = 2000
    max_batch_calls: int = 4
    max_batch_tokens: int = 80_000
    max_batch_cost_usd: float = 0.10
    budget: Any = dataclasses.field(default_factory=agent_v1.AgentBudget)

    def validate(self) -> "PairingConfig":
        if self.model not in SUPPORTED_MODELS:
            raise PairingAbort("contract")
        if type(self.repeats) is not int or not 1 <= self.repeats <= 20:
            raise PairingAbort("contract")
        if type(self.timeout) not in {int, float} or not 1 <= self.timeout <= 300:
            raise PairingAbort("contract")
        for value, maximum in (
            (self.max_tokens_per_call, 20_000),
            (self.max_batch_calls, 160),
            (self.max_batch_tokens, 5_000_000),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise PairingAbort("contract")
        if (
            type(self.max_batch_cost_usd) not in {int, float}
            or not 0 < self.max_batch_cost_usd <= 100
        ):
            raise PairingAbort("contract")
        self.budget.validate()
        required_calls = self.repeats * (1 + self.budget.max_turns)
        if self.max_batch_calls < required_calls:
            # Reserve the maximum possible calls before a batch starts.  This
            # prevents the controller from mistaking a local refusal for a
            # paid provider attempt in the middle of A1.
            raise PairingAbort("call_limit")
        return self


@dataclasses.dataclass(frozen=True)
class FrozenContract:
    prompt_version: str
    prompt_builder_sha256: str
    policy_sha256: str
    fixture_sha256: str
    baseline_sha256: str
    dependency_manifest_sha256: str
    runner_source_sha256: str
    runner_runtime_sha256: str
    runner_contract_sha256: str


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    payload = value if isinstance(value, bytes) else _canonical(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


RUNNER_SECURITY_SURFACE = (
    "PairingAbort",
    "PairingAbort.__init__",
    "PairingConfig",
    "PairingConfig.validate",
    "FrozenContract",
    "_canonical",
    "_sha",
    "_runner_source_sha256",
    "_runtime_symbol_fingerprint",
    "_stable_constant",
    "_stable_dependency_value",
    "_dependency_symbol_fingerprint",
    "_class_runtime_members",
    "_module_owned_surface",
    "_module_uppercase_constant_surface",
    "_module_namespace_manifest",
    "_module_namespace_sha256",
    "_assert_project_module_aliases",
    "_project_import_alias_bindings",
    "_project_import_aliases",
    "_stable_code_manifest",
    "_resolve_security_symbol",
    "_runtime_safety_sha256",
    "_resolved_safety_path",
    "_paths_related",
    "_dangerous_roots",
    "_validate_private_directory",
    "_trusted_system_temp_parent",
    "secure_batch_scratch",
    "_secure_source_file_sha256",
    "_dependency_contract",
    "_find_line",
    "_source_hashes",
    "_seed_record",
    "_revision_action",
    "_fixture_contract",
    "freeze_contract",
    "_assert_frozen",
    "_secure_write_clone",
    "isolated_case_vault",
    "_request_id",
    "_create_request",
    "BatchMeter",
    "BatchMeter.__init__",
    "BatchMeter._abort",
    "BatchMeter.ensure_arm_capacity",
    "BatchMeter.before_call",
    "BatchMeter.observe",
    "BatchMeter.observe_unpriced",
    "BatchMeter.public",
    "MeteredProvider",
    "MeteredProvider.__init__",
    "MeteredProvider.complete",
    "default_provider_factory",
    "_empty_usage",
    "_single_usage",
    "_strict_usage_valid",
    "_quality",
    "_safe_error_code",
    "_w1_run",
    "_a1_run",
    "_frozen_public",
    "plan_sha256",
    "build_plan",
    "run_live_pairing",
    "validate_public_report",
    "build_parser",
    "main",
)

# ``agent_v1.py`` imports these 30 names directly.  Calls and constant reads
# therefore use the aliases in the agent_v1 module, not a later lookup on the
# source modules.  Bind that complete local external-alias closure so an
# in-process replacement cannot continue under an already-reviewed plan.
AGENT_CORE_EXTERNAL_ALIAS_SURFACE = (
    "DAILY_NAME_RE",
    "EVIDENCE_FIELDS",
    "SOURCE_HASH_FIELDS",
    "ContractError",
    "Pricing",
    "_ensure_object",
    "_ensure_text",
    "_secure_directory",
    "_source_path",
    "append_usage_log",
    "atomic_write_json",
    "canonical_json",
    "normalize_usage",
    "provider_call_lock",
    "read_json",
    "sha256_bytes",
    "sha256_file",
    "source_hashes",
    "usage_is_missing",
    "utc_now",
)

AGENT_REFLECTION_EXTERNAL_ALIAS_SURFACE = (
    "EXPLICIT_CHANGE_EVIDENCE_PATTERNS",
    "EXPLICIT_TENSION_EVIDENCE_PATTERNS",
    "IDENTITY_LABEL_PATTERNS",
    "ISO_DATETIME_RE",
    "_contains_forbidden_text",
    "_collect_profile_feedback",
    "_collect_ready_profile_responses",
    "build_active_profile",
    "collect_reflection_feedback",
    "collect_reflection_sources",
)

AGENT_EXTERNAL_ALIAS_SURFACE = (
    AGENT_CORE_EXTERNAL_ALIAS_SURFACE + AGENT_REFLECTION_EXTERNAL_ALIAS_SURFACE
)

REFLECTION_EXTERNAL_ALIAS_SURFACE = (
    "DAILY_NAME_RE",
    "EVIDENCE_FIELDS",
    "SENSITIVE_PATTERNS",
    "SOURCE_HASH_FIELDS",
    "ContractError",
    "Pricing",
    "_ensure_object",
    "_ensure_text",
    "_secure_directory",
    "_source_path",
    "append_usage_log",
    "atomic_write_json",
    "canonical_json",
    "read_json",
    "sha256_bytes",
    "sha256_file",
    "source_hashes",
    "utc_now",
    "validate_confirmed",
)

MODULE_EXTERNAL_ALIAS_SURFACES = {
    "agent_v1": AGENT_EXTERNAL_ALIAS_SURFACE,
    "core": (),
    "deepseek_provider": (),
    "reflection": REFLECTION_EXTERNAL_ALIAS_SURFACE,
}

MODULE_EXTERNAL_ALIAS_SOURCE_MAPS = {
    "agent_v1": {
        **{
            name: ("core", name)
            for name in AGENT_CORE_EXTERNAL_ALIAS_SURFACE
        },
        **{
            name: ("reflection", name)
            for name in AGENT_REFLECTION_EXTERNAL_ALIAS_SURFACE
        },
    },
    "core": {},
    "deepseek_provider": {},
    "reflection": {
        name: ("core", name) for name in REFLECTION_EXTERNAL_ALIAS_SURFACE
    },
}

PROJECT_MODULE_ALIASES = (
    ("agent_v1", "agent_v1"),
    ("core", "core"),
    ("deepseek_provider", "deepseek_provider"),
    ("reflection", "reflection"),
)


def _runner_source_sha256(path: Path | None = None) -> str:
    """Hash the entire runner source without embedding a self-referential digest."""

    source_path = (path or Path(__file__)).resolve(strict=True)
    if source_path.is_symlink() or not source_path.is_file():
        raise PairingAbort("security")
    return hashlib.sha256(source_path.read_bytes()).hexdigest()


def _runtime_symbol_fingerprint(value: Any) -> dict[str, Any]:
    """Return a stable fingerprint for the object actually called at runtime."""

    result: dict[str, Any] = {
        "kind": type(value).__name__,
        "module": getattr(value, "__module__", None),
        "qualname": getattr(value, "__qualname__", None),
    }
    code = getattr(value, "__code__", None)
    if code is not None:
        result["code"] = _stable_code_manifest(code)
        result["defaults"] = _stable_constant(getattr(value, "__defaults__", None))
        result["kwdefaults"] = _stable_constant(
            getattr(value, "__kwdefaults__", None)
        )
    try:
        source = inspect.getsource(value)
    except (OSError, TypeError):
        source = None
    result["source_sha256"] = (
        hashlib.sha256(source.encode("utf-8")).hexdigest()
        if source is not None
        else None
    )
    if inspect.isclass(value):
        result["bases"] = [
            f"{base.__module__}.{base.__qualname__}" for base in value.__bases__
        ]
        if dataclasses.is_dataclass(value):
            result["dataclass_fields"] = [field.name for field in dataclasses.fields(value)]
    return result


def _stable_constant(value: Any) -> Any:
    if value is None or type(value) in {bool, int, float, str}:
        return value
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, Path):
        return {
            "path_sha256": hashlib.sha256(
                os.fspath(value).encode("utf-8")
            ).hexdigest()
        }
    if dataclasses.is_dataclass(value) and not inspect.isclass(value):
        return {
            "dataclass": f"{type(value).__module__}.{type(value).__qualname__}",
            "fields": [
                [field.name, _stable_dependency_value(getattr(value, field.name))]
                for field in dataclasses.fields(value)
            ],
        }
    if isinstance(value, re.Pattern):
        return {"regex": {"pattern": value.pattern, "flags": value.flags}}
    if isinstance(value, tuple):
        return {"tuple": [_stable_constant(item) for item in value]}
    if isinstance(value, list):
        return {"list": [_stable_constant(item) for item in value]}
    if isinstance(value, frozenset):
        return {
            "frozenset": sorted(
                (_stable_constant(item) for item in value), key=_canonical
            )
        }
    if isinstance(value, set):
        return {
            "set": sorted(
                (_stable_constant(item) for item in value), key=_canonical
            )
        }
    if inspect.iscode(value):
        return {"code": _stable_code_manifest(value)}
    if isinstance(value, Mapping):
        return {
            "mapping": [
                [str(key), _stable_constant(item)]
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            ]
        }
    return {
        "type": f"{type(value).__module__}.{type(value).__qualname__}"
    }


def _stable_dependency_value(value: Any) -> Any:
    """Strict structural form for security-relevant dependency constants."""

    def convert(item: Any, ancestors: set[int]) -> Any:
        if item is None or type(item) in {bool, int, str}:
            return item
        if type(item) is float:
            if not math.isfinite(item):
                raise PairingAbort("security")
            return item
        if isinstance(item, bytes):
            return {"bytes_sha256": hashlib.sha256(item).hexdigest()}
        if isinstance(item, Path):
            return {
                "path_sha256": hashlib.sha256(
                    os.fspath(item).encode("utf-8")
                ).hexdigest()
            }
        if isinstance(item, re.Pattern):
            return {
                "regex": {
                    "pattern": convert(item.pattern, ancestors),
                    "flags": item.flags,
                }
            }

        is_dataclass_instance = dataclasses.is_dataclass(item) and not inspect.isclass(
            item
        )
        is_container = is_dataclass_instance or isinstance(
            item, (tuple, list, frozenset, set, Mapping)
        )
        identity = id(item)
        if is_container:
            if identity in ancestors:
                raise PairingAbort("security")
            ancestors.add(identity)
        try:
            if is_dataclass_instance:
                return {
                    "dataclass": f"{type(item).__module__}.{type(item).__qualname__}",
                    "fields": [
                        [field.name, convert(getattr(item, field.name), ancestors)]
                        for field in dataclasses.fields(item)
                    ],
                }
            if isinstance(item, tuple):
                return {"tuple": [convert(value, ancestors) for value in item]}
            if isinstance(item, list):
                return {"list": [convert(value, ancestors) for value in item]}
            if isinstance(item, frozenset):
                return {
                    "frozenset": sorted(
                        (convert(value, ancestors) for value in item), key=_canonical
                    )
                }
            if isinstance(item, set):
                return {
                    "set": sorted(
                        (convert(value, ancestors) for value in item), key=_canonical
                    )
                }
            if isinstance(item, Mapping):
                items = [
                    [convert(key, ancestors), convert(value, ancestors)]
                    for key, value in item.items()
                ]
                return {"mapping": sorted(items, key=_canonical)}
            raise PairingAbort("security")
        finally:
            if is_container:
                ancestors.remove(identity)

    return convert(value, set())


def _dependency_symbol_fingerprint(value: Any) -> dict[str, Any]:
    fingerprint = _runtime_symbol_fingerprint(value)
    if inspect.isclass(value):
        fingerprint["class_runtime_members"] = _class_runtime_members(value)
    elif not callable(value):
        fingerprint["value"] = _stable_dependency_value(value)
    return fingerprint


def _class_runtime_members(value: type[Any]) -> dict[str, Any]:
    members: dict[str, Any] = {}
    for name, member in sorted(vars(value).items()):
        if inspect.isfunction(member):
            members[name] = {
                "kind": "function",
                "fingerprint": _runtime_symbol_fingerprint(member),
            }
        elif isinstance(member, (staticmethod, classmethod)):
            members[name] = {
                "kind": type(member).__name__,
                "fingerprint": _runtime_symbol_fingerprint(member.__func__),
            }
        elif isinstance(member, property):
            accessors = {}
            for accessor_name in ("fget", "fset", "fdel"):
                accessor = getattr(member, accessor_name)
                if accessor is not None:
                    accessors[accessor_name] = _runtime_symbol_fingerprint(accessor)
            members[name] = {"kind": "property", "accessors": accessors}
    return members


def _module_owned_surface(module: Any) -> tuple[str, ...]:
    return tuple(
        sorted(
            name
            for name, value in vars(module).items()
            if (inspect.isfunction(value) or inspect.isclass(value))
            and getattr(value, "__module__", None) == module.__name__
        )
    )


def _module_uppercase_constant_surface(module: Any) -> tuple[str, ...]:
    return tuple(sorted(name for name in vars(module) if name.isupper()))


def _module_namespace_manifest(module: Any) -> dict[str, Any]:
    """Fingerprint one project runner without serializing its raw path values."""

    owned_surface = _module_owned_surface(module)
    constant_surface = _module_uppercase_constant_surface(module)
    return {
        "module_owned_functions_and_classes": {
            name: _dependency_symbol_fingerprint(
                inspect.getattr_static(module, name)
            )
            for name in owned_surface
        },
        "uppercase_constants": {
            name: {
                "value": _stable_dependency_value(
                    inspect.getattr_static(module, name)
                )
            }
            for name in constant_surface
        },
        "surface": {
            "module_owned_functions_and_classes": list(owned_surface),
            "uppercase_constants": list(constant_surface),
        },
    }


def _module_namespace_sha256(module: Any) -> str:
    return _sha(_module_namespace_manifest(module))


def _assert_project_module_aliases() -> dict[str, Any]:
    aliases: list[dict[str, Any]] = []
    for alias, expected_name in PROJECT_MODULE_ALIASES:
        value = globals().get(alias)
        if (
            not inspect.ismodule(value)
            or getattr(value, "__name__", None) != expected_name
            or sys.modules.get(expected_name) is not value
        ):
            raise PairingAbort("security")
        aliases.append(
            {
                "alias": alias,
                "expected_module": expected_name,
                "sys_modules_identity": True,
            }
        )
    return {"aliases": aliases}


def _project_import_alias_bindings(
    module: Any, project_module_names: frozenset[str]
) -> dict[str, tuple[str, str | None]]:
    module_file = getattr(module, "__file__", None)
    if not isinstance(module_file, str):
        raise PairingAbort("security")
    path = Path(module_file)
    if path.is_symlink() or not path.is_file():
        raise PairingAbort("security")
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise PairingAbort("security") from exc
    aliases: dict[str, tuple[str, str | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in project_module_names:
            for imported in node.names:
                if imported.name == "*":
                    raise PairingAbort("security")
                local_name = imported.asname or imported.name
                binding = (node.module, imported.name)
                if local_name in aliases and aliases[local_name] != binding:
                    raise PairingAbort("security")
                aliases[local_name] = binding
        elif isinstance(node, ast.Import):
            for imported in node.names:
                root = imported.name.split(".", 1)[0]
                if root in project_module_names:
                    local_name = imported.asname or root
                    binding = (root, None)
                    if local_name in aliases and aliases[local_name] != binding:
                        raise PairingAbort("security")
                    aliases[local_name] = binding
    return dict(sorted(aliases.items()))


def _project_import_aliases(
    module: Any, project_module_names: frozenset[str]
) -> tuple[str, ...]:
    return tuple(_project_import_alias_bindings(module, project_module_names))


def _stable_code_manifest(code: Any) -> dict[str, Any]:
    return {
        "bytecode_sha256": hashlib.sha256(code.co_code).hexdigest(),
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
        "nlocals": code.co_nlocals,
        "flags": code.co_flags,
        "names": list(code.co_names),
        "varnames": list(code.co_varnames),
        "freevars": list(code.co_freevars),
        "cellvars": list(code.co_cellvars),
        "constants": [_stable_constant(item) for item in code.co_consts],
    }


def _resolve_security_symbol(name: str) -> Any:
    parts = name.split(".")
    value = globals().get(parts[0])
    if value is None:
        raise PairingAbort("security")
    for part in parts[1:]:
        try:
            value = inspect.getattr_static(value, part)
        except AttributeError as exc:
            raise PairingAbort("security") from exc
    return value


def _runtime_safety_sha256() -> str:
    module = sys.modules.get(__name__)
    if module is None:
        raise PairingAbort("security")
    return _module_namespace_sha256(module)


def _resolved_safety_path(path: Path, *, must_exist: bool) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise PairingAbort("security")
    try:
        return candidate.resolve(strict=must_exist)
    except OSError as exc:
        raise PairingAbort("security") from exc


def _paths_related(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _dangerous_roots() -> tuple[Path, ...]:
    candidates: list[tuple[Path, bool]] = [
        (CONTEXT_AGENT_ROOT.parent, True),
        (SCENARIO_ROOT, True),
        (Path.home() / "AISecretary", False),
    ]
    configured_vault = os.environ.get("MEMENTO_VAULT")
    if configured_vault:
        candidates.append((Path(configured_vault), True))
    roots: list[Path] = []
    for candidate, must_exist in candidates:
        resolved = _resolved_safety_path(candidate, must_exist=must_exist)
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def _validate_private_directory(path: Path, *, expected_parent: Path | None) -> Path:
    if path.is_symlink():
        raise PairingAbort("security")
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise PairingAbort("security") from exc
    if not stat.S_ISDIR(metadata.st_mode) or resolved != path.absolute():
        raise PairingAbort("security")
    if not hasattr(os, "getuid") or metadata.st_uid != os.getuid():
        raise PairingAbort("security")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise PairingAbort("security")
    if expected_parent is not None and resolved.parent != expected_parent:
        raise PairingAbort("security")
    for dangerous in _dangerous_roots():
        if _paths_related(resolved, dangerous):
            raise PairingAbort("security")
    return resolved


def _trusted_system_temp_parent() -> Path:
    # TMPDIR is deliberately not used as the allocation parent.  It is still
    # validated below so an inherited Vault/repository path cannot silently
    # influence tempfile helpers elsewhere in the process.
    preferred = Path("/private/tmp")
    candidate = preferred if preferred.is_dir() and not preferred.is_symlink() else Path("/tmp")
    if candidate.is_symlink():
        raise PairingAbort("security")
    try:
        metadata = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise PairingAbort("security") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise PairingAbort("security")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o022 and not mode & stat.S_ISVTX:
        raise PairingAbort("security")
    if metadata.st_uid not in {0, os.getuid()}:
        raise PairingAbort("security")
    inherited_tmp = os.environ.get("TMPDIR")
    if inherited_tmp:
        inherited = _resolved_safety_path(Path(inherited_tmp), must_exist=True)
        if not inherited.is_dir():
            raise PairingAbort("security")
        for dangerous in _dangerous_roots():
            if _paths_related(inherited, dangerous):
                raise PairingAbort("security")
    return resolved


@contextlib.contextmanager
def secure_batch_scratch() -> Iterator[Path]:
    """Create one private batch root outside every protected data tree."""

    parent = _trusted_system_temp_parent()
    with tempfile.TemporaryDirectory(
        prefix="memento-agent-pairing-batch-", dir=parent
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        resolved = _validate_private_directory(root, expected_parent=parent)
        yield resolved


def _secure_source_file_sha256(path: Path) -> str:
    """Hash one ordinary source file without following a symlink input."""

    source_path = Path(path)
    if source_path.is_symlink() or not source_path.is_file():
        raise PairingAbort("security")
    try:
        resolved = source_path.resolve(strict=True)
    except OSError as exc:
        raise PairingAbort("security") from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise PairingAbort("security")
    return hashlib.sha256(resolved.read_bytes()).hexdigest()


def _dependency_contract() -> dict[str, Any]:
    project_module_aliases = _assert_project_module_aliases()
    modules = {
        "agent_v1": agent_v1,
        "core": core,
        "deepseek_provider": deepseek_provider,
        "reflection": reflection,
    }
    files: dict[str, str] = {}
    runtime: dict[str, dict[str, Any]] = {}
    namespace_closure: dict[str, dict[str, list[str]]] = {}
    project_module_names = frozenset(modules)
    for module_name, module in sorted(modules.items()):
        module_file = getattr(module, "__file__", None)
        if not isinstance(module_file, str) or not module_file.endswith(".py"):
            raise PairingAbort("security")
        files[module_name] = _secure_source_file_sha256(Path(module_file))
        discovered_bindings = _project_import_alias_bindings(
            module, project_module_names
        )
        configured_bindings = dict(
            sorted(MODULE_EXTERNAL_ALIAS_SOURCE_MAPS[module_name].items())
        )
        if discovered_bindings != configured_bindings:
            raise PairingAbort("security")
        if tuple(discovered_bindings) != tuple(
            sorted(MODULE_EXTERNAL_ALIAS_SURFACES[module_name])
        ):
            raise PairingAbort("security")
        for local_name, (source_module_name, source_attribute) in configured_bindings.items():
            source_module = modules[source_module_name]
            expected_value = (
                source_module
                if source_attribute is None
                else inspect.getattr_static(source_module, source_attribute)
            )
            if inspect.getattr_static(module, local_name) is not expected_value:
                raise PairingAbort("security")
        owned_surface = _module_owned_surface(module)
        constant_surface = _module_uppercase_constant_surface(module)
        namespace_closure[module_name] = {
            "module_owned_functions_and_classes": list(owned_surface),
            "uppercase_constants": list(constant_surface),
        }
        runtime[f"{module_name}.module_owned"] = {
            name: _dependency_symbol_fingerprint(
                inspect.getattr_static(module, name)
            )
            for name in owned_surface
        }
        runtime[f"{module_name}.uppercase_constants"] = {
            name: {
                "value": _stable_dependency_value(
                    inspect.getattr_static(module, name)
                )
            }
            for name in constant_surface
        }
    for module_name, aliases in sorted(MODULE_EXTERNAL_ALIAS_SURFACES.items()):
        module = modules[module_name]
        runtime[f"{module_name}.external_aliases"] = {
            name: _dependency_symbol_fingerprint(
                inspect.getattr_static(module, name)
            )
            for name in aliases
        }
    return {
        "project_module_aliases": project_module_aliases,
        "source_files": files,
        "runtime_symbols_sha256": _sha(runtime),
        "runtime_surface": {
            "module_external_aliases": {
                module_name: list(aliases)
                for module_name, aliases in sorted(
                    MODULE_EXTERNAL_ALIAS_SURFACES.items()
                )
            },
            "module_external_alias_sources": {
                module_name: {
                    alias: (
                        source_module
                        if source_attribute is None
                        else f"{source_module}.{source_attribute}"
                    )
                    for alias, (source_module, source_attribute) in sorted(
                        bindings.items()
                    )
                }
                for module_name, bindings in sorted(
                    MODULE_EXTERNAL_ALIAS_SOURCE_MAPS.items()
                )
            },
            "project_namespace_closure": namespace_closure,
        },
    }


def _find_line(root: Path, filename: str, marker: str) -> dict[str, Any]:
    matches = [
        {"file": filename, "line": number, "quote": line}
        for number, line in enumerate(
            (root / filename).read_text(encoding="utf-8").splitlines(), start=1
        )
        if marker in line
    ]
    if len(matches) != 1:
        raise PairingAbort("security")
    return matches[0]


def _source_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for filename in CASE_SOURCES:
        path = root / filename
        if path.is_symlink() or not path.is_file():
            raise PairingAbort("security")
        resolved = path.resolve(strict=True)
        if resolved.parent != root.resolve():
            raise PairingAbort("security")
        result[filename] = core.sha256_file(resolved)
    return result


def _seed_record(root: Path) -> dict[str, Any]:
    evidence = [_find_line(root, "2026-07-14.md", OLD_MARKER)]
    statement = "我们决定本轮先把新用户激活作为最高优先级。"
    scope = "产品优先级"
    memory_id = agent_v1.memory_id_for_meaning(statement, scope)
    return {
        "schema_version": agent_v1.AGENT_SCHEMA_VERSION,
        "kind": "remember_memory_revision",
        "memory_id": memory_id,
        "revision": 1,
        "status": "active",
        "created_at": "2026-07-14T18:10:00+08:00",
        "run_id": None,
        "request_id": None,
        "operation": "new",
        "previous_revision_sha256": None,
        "base_profile_ref": None,
        "user_action_id": None,
        "title": "激活优先",
        "statement": statement,
        "scope": scope,
        "insight_kind": "observation",
        "uncertainty": "medium",
        "evidence": evidence,
        "counterevidence": [],
        "source_hashes": [
            {"file": "2026-07-14.md", "sha256": core.sha256_file(root / "2026-07-14.md")}
        ],
    }


def _revision_action(root: Path) -> dict[str, Any]:
    seed = _seed_record(root)
    return {
        "schema_version": agent_v1.AGENT_SCHEMA_VERSION,
        "action": "finalize_patch",
        "reason_code": "evidence_sufficient",
        "arguments": {
            "operation": "revise",
            "target_memory_id": seed["memory_id"],
            "expected_revision": 1,
            "title": "留存优先替代激活优先",
            "statement": "我们决定本轮把 30 日留存作为最高优先级。",
            "scope": seed["scope"],
            "uncertainty": "medium",
            "evidence": [
                _find_line(root, "2026-07-17.md", NEW_PRIORITY_MARKER),
                _find_line(root, "2026-07-17.md", NEW_MARKER),
            ],
            "counterevidence": list(seed["evidence"]),
        },
    }


def _fixture_contract() -> tuple[str, str]:
    hashes = _source_hashes(SCENARIO_ROOT)
    fixture_sha = _sha([hashes[name] for name in CASE_SOURCES])
    seed = _seed_record(SCENARIO_ROOT)
    baseline_sha = _sha(
        {
            "fixture_sha256": fixture_sha,
            "seed_revision_sha256": _sha(seed),
            "expected_action_sha256": _sha(_revision_action(SCENARIO_ROOT)),
            "w1_query": SEARCH_QUERY,
            "w1_terminal_policy_version": W1_TERMINAL_POLICY_VERSION,
            "w1_terminal_instruction_sha256": _sha(
                W1_TERMINAL_INSTRUCTION.encode("utf-8")
            ),
            "runner_contract_sha256": _sha(
                inspect.getsource(_revision_action).encode("utf-8")
            ),
        }
    )
    return fixture_sha, baseline_sha


def freeze_contract(config: PairingConfig) -> FrozenContract:
    _assert_project_module_aliases()
    config.validate()
    fixture_sha, baseline_sha = _fixture_contract()
    prompt_builder_sha = _sha(inspect.getsource(agent_v1.build_agent_messages).encode("utf-8"))
    dependency_manifest_sha = _sha(_dependency_contract())
    runner_source_sha = _runner_source_sha256()
    runner_runtime_sha = _runtime_safety_sha256()
    return FrozenContract(
        prompt_version=agent_v1.AGENT_PROMPT_VERSION,
        prompt_builder_sha256=prompt_builder_sha,
        policy_sha256=agent_v1.make_agent_policy_sha256(
            provider="deepseek", model=config.model, budget=config.budget
        ),
        fixture_sha256=fixture_sha,
        baseline_sha256=baseline_sha,
        dependency_manifest_sha256=dependency_manifest_sha,
        runner_source_sha256=runner_source_sha,
        runner_runtime_sha256=runner_runtime_sha,
        runner_contract_sha256=_sha(
            {
                "source_sha256": runner_source_sha,
                "runtime_sha256": runner_runtime_sha,
                "security_surface": list(RUNNER_SECURITY_SURFACE),
            }
        ),
    )


def _assert_frozen(config: PairingConfig, frozen: FrozenContract) -> None:
    _assert_project_module_aliases()
    if freeze_contract(config) != frozen:
        raise PairingAbort("security")


def _secure_write_clone(target: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def isolated_case_vault(scratch_root: Path) -> Iterator[Path]:
    """Yield a new 0700 synthetic Vault and remove it after one arm."""

    trusted_root = _validate_private_directory(
        Path(scratch_root), expected_parent=_trusted_system_temp_parent()
    )
    with tempfile.TemporaryDirectory(
        prefix="memento-agent-pairing-arm-", dir=trusted_root
    ) as temporary:
        vault = Path(temporary)
        vault.chmod(0o700)
        vault = _validate_private_directory(vault, expected_parent=trusted_root)
        expected = _source_hashes(SCENARIO_ROOT)
        for filename in CASE_SOURCES:
            _secure_write_clone(vault / filename, (SCENARIO_ROOT / filename).read_bytes())
            if stat.S_IMODE((vault / filename).stat().st_mode) != 0o600:
                raise PairingAbort("security")
        if _source_hashes(vault) != expected:
            raise PairingAbort("security")
        seed = _seed_record(vault)
        agent_v1.validate_memory_revision(seed, vault, verify_sources=True)
        core.atomic_write_json(agent_v1._memory_path(vault, seed["memory_id"], 1), seed)
        yield vault


def _request_id(arm: str, repetition: int, baseline_sha: str) -> str:
    return "arq_" + hashlib.sha256(
        f"{baseline_sha}:{arm}:{repetition}".encode("utf-8")
    ).hexdigest()[:24]


def _create_request(vault: Path, arm: str, repetition: int, frozen: FrozenContract) -> dict[str, Any]:
    request, _ = agent_v1.create_agent_request(
        vault,
        as_of=CASE_AS_OF,
        request_id=_request_id(arm, repetition, frozen.baseline_sha256),
        created_at=f"2026-07-17T22:{repetition:02d}:00+08:00",
    )
    return request


class BatchMeter:
    """Global fail-closed provider meter shared by all paired arms."""

    def __init__(self, config: PairingConfig, pricing: Any) -> None:
        self.config = config
        self.pricing = pricing
        self.calls = 0
        self.tokens = 0
        self.cost = 0.0
        self.usage_complete = True
        self.halted_code: str | None = None
        self.by_arm = {
            "W1": {"calls": 0, "tokens": 0, "cost_usd": 0.0},
            "A1": {"calls": 0, "tokens": 0, "cost_usd": 0.0},
        }

    def _abort(self, code: str, *, usage: Mapping[str, Any] | None = None) -> None:
        if code == "usage_missing":
            self.usage_complete = False
        self.halted_code = self.halted_code or code
        raise PairingAbort(code, usage=usage)

    def ensure_arm_capacity(self, arm: str) -> None:
        required = 1 if arm == "W1" else self.config.budget.max_turns
        if self.calls + required > self.config.max_batch_calls:
            self._abort("call_limit")

    def before_call(self, arm: str, messages: Sequence[Mapping[str, str]]) -> None:
        if self.halted_code is not None:
            self._abort(self.halted_code)
        if self.calls >= self.config.max_batch_calls:
            self._abort("call_limit")
        # UTF-8 bytes plus completion allowance is a conservative reservation
        # for the model-visible content.  The post-call measured check remains
        # authoritative and halts the batch on any provider discrepancy.
        reserved_tokens = sum(
            len(str(item.get("content", "")).encode("utf-8")) for item in messages
        ) + self.config.max_tokens_per_call + 4096
        if self.tokens + reserved_tokens > self.config.max_batch_tokens:
            self._abort("token_limit")
        worst_rate = max(
            self.pricing.cache_miss_input_usd_per_million,
            self.pricing.output_usd_per_million,
        )
        reserved_cost = reserved_tokens * worst_rate / 1_000_000
        if self.cost + reserved_cost > self.config.max_batch_cost_usd:
            self._abort("cost_limit")
        self.calls += 1
        self.by_arm[arm]["calls"] += 1

    def observe(self, arm: str, usage: Mapping[str, Any] | None) -> None:
        if not _strict_usage_valid(usage):
            self._abort("usage_missing", usage=usage)
        normalized = core.normalize_usage(usage)
        cost = core.calculate_cost(normalized, self.pricing)
        self.tokens += normalized["total_tokens"]
        self.cost = round(self.cost + cost, 10)
        self.by_arm[arm]["tokens"] += normalized["total_tokens"]
        self.by_arm[arm]["cost_usd"] = round(
            self.by_arm[arm]["cost_usd"] + cost, 10
        )
        if self.tokens > self.config.max_batch_tokens:
            self._abort("token_limit", usage=usage)
        if self.cost > self.config.max_batch_cost_usd:
            self._abort("cost_limit", usage=usage)

    def observe_unpriced(self, arm: str, usage: Mapping[str, Any] | None) -> None:
        """Record any finite token subtotal without claiming a known cost.

        Strict usage remains mandatory for a successful call.  This path is
        only the fail-closed audit projection after a provider/model failure,
        where dropping a partial non-zero subtotal would make the per-run and
        batch meters disagree about an attempt that already happened.
        """

        self.usage_complete = False
        normalized = core.normalize_usage(usage)
        self.tokens += normalized["total_tokens"]
        self.by_arm[arm]["tokens"] += normalized["total_tokens"]

    def public(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "tokens": self.tokens,
            "cost_usd": round(self.cost, 10),
            "cost_complete": self.usage_complete,
            "by_arm": {
                arm: dict(values) for arm, values in sorted(self.by_arm.items())
            },
        }


class MeteredProvider:
    def __init__(self, delegate: Any, meter: BatchMeter, arm: str) -> None:
        self.delegate = delegate
        self.meter = meter
        self.arm = arm

    def complete(self, messages: Sequence[Mapping[str, str]]) -> Any:
        self.meter.before_call(self.arm, messages)
        try:
            completion = self.delegate.complete(messages)
        except PairingAbort:
            raise
        except Exception as exc:
            usage = getattr(exc, "usage", None)
            actual_model = getattr(exc, "model", None)
            if actual_model is not None and actual_model != self.meter.config.model:
                self.meter.observe_unpriced(self.arm, usage)
                self.meter.halted_code = "security"
                raise PairingAbort(
                    "security", usage=usage, model=actual_model
                ) from exc
            if _strict_usage_valid(usage):
                self.meter.observe(self.arm, usage)
            else:
                self.meter.observe_unpriced(self.arm, usage)
            self.meter.halted_code = "provider_error"
            raise
        actual_model = getattr(completion, "model", None)
        if actual_model != self.meter.config.model:
            usage = getattr(completion, "usage", None)
            self.meter.observe_unpriced(self.arm, usage)
            self.meter.halted_code = "security"
            raise PairingAbort("security", usage=usage, model=actual_model)
        self.meter.observe(self.arm, getattr(completion, "usage", None))
        return completion


ProviderFactory = Callable[[str, int, PairingConfig], Any]


def default_provider_factory(arm: str, repetition: int, config: PairingConfig) -> Any:
    _assert_project_module_aliases()
    del arm, repetition
    # Credential lookup remains inside DeepSeekProvider.complete: plan-only
    # mode and provider construction never read the environment or Keychain.
    return deepseek_provider.DeepSeekProvider(
        model=config.model,
        timeout=config.timeout,
        thinking="disabled",
        reasoning_effort=None,
        max_tokens=config.max_tokens_per_call,
    )


def _empty_usage() -> dict[str, Any]:
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


def _single_usage(
    raw: Mapping[str, Any] | None,
    pricing: Any,
    *,
    cost_known: bool = True,
) -> dict[str, Any]:
    normalized = core.normalize_usage(raw)
    missing = not _strict_usage_valid(raw)
    return {
        "model_calls": 1,
        **normalized,
        "usage_missing": missing,
        "cost_usd": (
            None
            if missing or not cost_known
            else core.calculate_cost(normalized, pricing)
        ),
    }


def _strict_usage_valid(usage: Mapping[str, Any] | None) -> bool:
    if not isinstance(usage, Mapping):
        return False
    required = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    )
    if any(type(usage.get(field)) is not int or usage[field] < 0 for field in required):
        return False
    if usage["total_tokens"] <= 0:
        return False
    if usage["total_tokens"] < usage["prompt_tokens"] + usage["completion_tokens"]:
        return False
    if (
        usage["prompt_cache_hit_tokens"] + usage["prompt_cache_miss_tokens"]
        > usage["prompt_tokens"]
    ):
        return False
    return True


def _quality(
    vault: Path,
    memory: Mapping[str, Any] | None,
    status: str,
    sources_preserved: bool,
    trajectory: Sequence[str],
    usage: Mapping[str, Any],
    *,
    audit_clean: bool,
) -> dict[str, Any]:
    expected = _revision_action(vault)["arguments"]
    stored: Mapping[str, Any] | None = None
    strict_revision_contract = False
    if memory and memory.get("revision") == 2:
        try:
            candidate = agent_v1.validate_memory_revision(
                core.read_json(
                    agent_v1._memory_path(vault, memory["memory_id"], 2)
                ),
                vault,
                verify_sources=True,
            )
            stored = candidate
            strict_revision_contract = True
        except (OSError, core.ContractError, KeyError, TypeError):
            stored = None
    expected_hashes = [
        {"file": filename, "sha256": core.sha256_file(vault / filename)}
        for filename in CASE_SOURCES
    ]
    profile = agent_v1.build_agent_profile(vault)
    no_disallowed_path = audit_clean and not any(
        action in {"invalid_action"} for action in trajectory
    )
    checks = {
        "terminal_updated": status == "updated",
        "operation_revise": bool(
            memory and memory.get("provenance", {}).get("operation") == "revise"
        ),
        "target_preserved": bool(
            memory and memory.get("memory_id") == _seed_record(SCENARIO_ROOT)["memory_id"]
        ),
        "revision_advanced_once": bool(memory and memory.get("revision") == 2),
        "expected_statement": bool(
            stored and stored.get("statement") == expected["statement"]
        ),
        "expected_scope": bool(stored and stored.get("scope") == expected["scope"]),
        "new_evidence_exact_2026_07_17": bool(
            stored and stored.get("evidence") == expected["evidence"]
        ),
        "counterevidence_exact_2026_07_14": bool(
            stored and stored.get("counterevidence") == expected["counterevidence"]
        ),
        "source_hashes_exact": bool(
            stored and stored.get("source_hashes") == expected_hashes
        ),
        "cas_chain_exact": bool(
            stored
            and stored.get("previous_revision_sha256")
            == core.sha256_file(agent_v1._memory_path(vault, stored["memory_id"], 1))
        ),
        "strict_evidence_and_sensitive_contract": strict_revision_contract,
        "unique_memory_exactly_two_revisions": bool(
            profile["stats"]["active"] == 1
            and profile["stats"]["tombstones"] == 0
            and len(profile["memories"]) == 1
            and len(
                list(
                    agent_v1._agent_directory(vault, "memories").glob(
                        f"{expected['target_memory_id']}.r*.json"
                    )
                )
            )
            == 2
        ),
        "usage_complete": bool(
            usage.get("model_calls", 0) >= 1
            and usage.get("total_tokens", 0) > 0
            and usage.get("usage_missing") is False
            and usage.get("cost_usd") is not None
        ),
        "no_rejected_invalid_or_budget_steps": no_disallowed_path,
        "source_clone_unchanged": sources_preserved,
    }
    passed = all(checks.values())
    return {
        "passed": passed,
        "score": sum(checks.values()) / len(checks),
        "checks": checks,
    }


def _safe_error_code(exc: BaseException) -> str:
    if isinstance(exc, PairingAbort):
        return exc.code
    if exc.__class__.__name__ == "ProviderError":
        return "provider_error"
    if isinstance(exc, core.ContractError):
        return "security" if exc.kind in {"stale", "cas", "sensitive", "evidence"} else "contract"
    return "provider_error"


def _w1_run(
    vault: Path,
    provider: Any,
    config: PairingConfig,
    frozen: FrozenContract,
    repetition: int,
) -> dict[str, Any]:
    baseline = _source_hashes(vault)
    usage = _empty_usage()
    status = "error"
    error_code = "none"
    memory: Mapping[str, Any] | None = None
    trajectory = ["read_memory", "search_history"]
    calls_before = provider.meter.by_arm["W1"]["calls"]
    try:
        request = _create_request(vault, "W1", repetition, frozen)
        request_sha = core.sha256_file(agent_v1.request_path(vault, request["id"]))
        preparation = agent_v1.prepare_agent_run(
            vault, request, request_sha, maximum_chars=config.budget.max_prompt_chars
        )
        messages = agent_v1.build_agent_messages(preparation)
        memory_id = _seed_record(vault)["memory_id"]
        read_action = {
            "schema_version": "1.0",
            "action": "read_memory",
            "reason_code": "inspect_existing",
            "arguments": {"memory_id": memory_id},
        }
        read_result = agent_v1._read_memory_tool(preparation, memory_id)
        agent_v1._append_tool_result(messages, read_action, {"ok": True, **read_result})
        search_action = {
            "schema_version": "1.0",
            "action": "search_history",
            "reason_code": "need_history_evidence",
            "arguments": {
                "query": SEARCH_QUERY,
                "date_from": None,
                "date_to": CASE_AS_OF,
                "limit": 5,
            },
        }
        matches = agent_v1._literal_history_search(preparation, search_action["arguments"])
        agent_v1._append_tool_result(
            messages,
            search_action,
            {"ok": True, "matches": matches, "match_count": len(matches)},
        )
        messages.append({"role": "user", "content": W1_TERMINAL_INSTRUCTION})
        completion = provider.complete(messages)
        usage = _single_usage(completion.usage, core.pricing_for_model(config.model))
        action = agent_v1._parse_action(completion.content)
        trajectory.append(action["action"])
        if action["action"] == "finalize_patch":
            if action["arguments"]["operation"] == "new":
                raise PairingAbort("invalid_terminal_action")
            memory = agent_v1._finalize_patch(
                preparation,
                action["arguments"],
                run_id=agent_v1.make_run_id(request["id"]),
            )
            status = "updated"
        elif action["action"] == "finish":
            status = action["arguments"]["reason"]
        else:
            raise PairingAbort("invalid_terminal_action")
    except Exception as exc:
        error_code = _safe_error_code(exc)
        if (
            provider.meter.by_arm["W1"]["calls"] > calls_before
            and usage["model_calls"] == 0
        ):
            # A paid attempt remains visible even without a successful
            # completion. Missing usage is cost-unknown, never zero-cost.
            usage = _single_usage(
                getattr(exc, "usage", None),
                core.pricing_for_model(config.model),
                cost_known=not (
                    isinstance(exc, PairingAbort) and exc.code == "security"
                ),
            )
    preserved = _source_hashes(vault) == baseline
    if not preserved:
        raise PairingAbort("security")
    return {
        "arm": "W1",
        "repetition": repetition,
        "status": status,
        "error_code": error_code,
        "trajectory": trajectory,
        "initial_revision": 1,
        "result_revision": memory.get("revision") if memory else None,
        "baseline_sha256": frozen.baseline_sha256,
        "quality": _quality(
            vault,
            memory,
            status,
            preserved,
            trajectory,
            usage,
            audit_clean=(error_code == "none" and trajectory[-1:] == ["finalize_patch"]),
        ),
        "usage": usage,
    }


def _a1_run(
    vault: Path,
    provider: Any,
    config: PairingConfig,
    frozen: FrozenContract,
    repetition: int,
) -> dict[str, Any]:
    baseline = _source_hashes(vault)
    request = _create_request(vault, "A1", repetition, frozen)
    response, _ = agent_v1.process_agent_request(
        vault,
        request["id"],
        provider_client=provider,
        provider_name="deepseek",
        model=config.model,
        pricing=core.pricing_for_model(config.model),
        budget=config.budget,
        maximum_chars=config.budget.max_prompt_chars,
    )
    preserved = _source_hashes(vault) == baseline
    if not preserved:
        raise PairingAbort("security")
    run = agent_v1.validate_agent_run(
        core.read_json(agent_v1.run_path(vault, agent_v1.make_run_id(request["id"])))
    )
    audit_clean = bool(
        response["error"] is None
        and response["error_kind"] is None
        and response["trace"]["stop_reason"] == "patch_committed"
        and all(
            step["result_kind"]
            not in {
                "rejected",
                "loop_blocked",
                "budget_blocked",
                "provider_attempt_started",
            }
            and step["error_kind"] is None
            for step in run["steps"]
        )
    )
    error_code = "none"
    if response["status"] not in {"updated", "no_change", "insufficient_evidence"}:
        error_code = (
            "security"
            if response.get("error_kind") in {"stale", "cas", "sensitive", "evidence"}
            else "agent_error"
        )
    return {
        "arm": "A1",
        "repetition": repetition,
        "status": response["status"],
        "error_code": error_code,
        "trajectory": list(response["trace"]["actions"]),
        "initial_revision": 1,
        "result_revision": response["memory"].get("revision") if response["memory"] else None,
        "baseline_sha256": frozen.baseline_sha256,
        "quality": _quality(
            vault,
            response["memory"],
            response["status"],
            preserved,
            response["trace"]["actions"],
            response["usage"],
            audit_clean=audit_clean,
        ),
        "usage": dict(response["usage"]),
    }


def _frozen_public(config: PairingConfig, frozen: FrozenContract) -> dict[str, Any]:
    return {
        "case": CASE_ID,
        "as_of": CASE_AS_OF,
        "window_days": 14,
        "source_files": len(CASE_SOURCES),
        "fixture_sha256": frozen.fixture_sha256,
        "baseline_sha256": frozen.baseline_sha256,
        "prompt_version": frozen.prompt_version,
        "prompt_builder_sha256": frozen.prompt_builder_sha256,
        "policy_sha256": frozen.policy_sha256,
        "dependency_manifest_sha256": frozen.dependency_manifest_sha256,
        "runner_source_sha256": frozen.runner_source_sha256,
        "runner_runtime_sha256": frozen.runner_runtime_sha256,
        "runner_contract_sha256": frozen.runner_contract_sha256,
        "w1_query_sha256": _sha(SEARCH_QUERY.encode("utf-8")),
        "w1_terminal_policy_version": W1_TERMINAL_POLICY_VERSION,
        "w1_terminal_instruction_sha256": _sha(
            W1_TERMINAL_INSTRUCTION.encode("utf-8")
        ),
        "provider": "deepseek",
        "model": config.model,
        "thinking": "disabled",
        "reasoning_effort": None,
        "max_tokens_per_call": config.max_tokens_per_call,
        "budget": config.budget.as_dict(),
        "input_maximum_chars_both_arms": config.budget.max_prompt_chars,
        "repeats": config.repeats,
        "pairing_order": ["W1", "A1"],
        "w1_workflow": ["read_memory", "search_history", "terminal_model_action"],
        "w1_baseline_kind": "oracle_assisted_fixed_workflow",
        "w1_terminal_actions": ["finalize_patch", "finish"],
        "a1_workflow": "dynamic_agent_v1",
        "focused_gate": "same_revision_quality_cost_only",
        "agent_gain_claimed": False,
    }


def plan_sha256(config: PairingConfig, frozen: FrozenContract) -> str:
    """Bind the reviewed experiment, limits, and arm ordering."""

    return _sha(
        {
            "frozen": _frozen_public(config, frozen),
            "limits": {
                "max_batch_calls": config.max_batch_calls,
                "max_batch_tokens": config.max_batch_tokens,
                "max_batch_cost_usd": config.max_batch_cost_usd,
                "fail_closed": True,
            },
            "execution_order": ["W1", "A1"],
        }
    )


def build_plan(config: PairingConfig) -> dict[str, Any]:
    frozen = freeze_contract(config)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "plan_only",
        "executed": False,
        "status": "planned",
        "stop_code": "none",
        "plan_sha256": plan_sha256(config, frozen),
        "frozen": _frozen_public(config, frozen),
        "limits": {
            "max_batch_calls": config.max_batch_calls,
            "max_batch_tokens": config.max_batch_tokens,
            "max_batch_cost_usd": config.max_batch_cost_usd,
            "fail_closed": True,
        },
        "credential": {
            "lookup_deferred_until_provider_call": True,
            "environment_or_macos_keychain": True,
            "persisted_in_report": False,
        },
        "runs": [],
        "summary": {
            "pairs_requested": config.repeats,
            "pairs_completed": 0,
            "batch_quality": None,
            "batch": {
                "calls": 0,
                "tokens": 0,
                "cost_usd": 0.0,
                "cost_complete": True,
                "by_arm": {
                    "A1": {"calls": 0, "tokens": 0, "cost_usd": 0.0},
                    "W1": {"calls": 0, "tokens": 0, "cost_usd": 0.0},
                },
            },
        },
    }


def run_live_pairing(
    config: PairingConfig,
    *,
    expected_plan_sha256: str,
    provider_factory: ProviderFactory | None = None,
) -> dict[str, Any]:
    _assert_project_module_aliases()
    config.validate()
    frozen = freeze_contract(config)
    actual_plan_sha256 = plan_sha256(config, frozen)
    if (
        not isinstance(expected_plan_sha256, str)
        or len(expected_plan_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_plan_sha256)
        or expected_plan_sha256 != actual_plan_sha256
    ):
        raise PairingAbort("plan_mismatch")
    if provider_factory is None:
        _assert_project_module_aliases()
        provider_factory = default_provider_factory
    elif not callable(provider_factory):
        raise PairingAbort("contract")
    pricing = core.pricing_for_model(config.model)
    meter = BatchMeter(config, pricing)
    runs: list[dict[str, Any]] = []
    pairs_completed = 0
    stop_code = "none"
    with secure_batch_scratch() as scratch_root:
        for repetition in range(1, config.repeats + 1):
            pair_ok = True
            for arm, runner in (("W1", _w1_run), ("A1", _a1_run)):
                try:
                    _assert_frozen(config, frozen)
                    meter.ensure_arm_capacity(arm)
                    with isolated_case_vault(scratch_root) as vault:
                        _assert_project_module_aliases()
                        provider = MeteredProvider(
                            provider_factory(arm, repetition, config), meter, arm
                        )
                        result = runner(vault, provider, config, frozen, repetition)
                        runs.append(result)
                        if result["error_code"] != "none":
                            meter.halted_code = meter.halted_code or result["error_code"]
                    if meter.halted_code is not None:
                        raise PairingAbort(meter.halted_code)
                except Exception as exc:
                    stop_code = _safe_error_code(exc)
                    meter.halted_code = meter.halted_code or stop_code
                    pair_ok = False
                    break
            if pair_ok:
                pairs_completed += 1
            else:
                break
    status = "completed" if stop_code == "none" else "stopped"
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "live_synthetic",
        "executed": True,
        "status": status,
        "stop_code": stop_code,
        "plan_sha256": actual_plan_sha256,
        "frozen": _frozen_public(config, frozen),
        "limits": {
            "max_batch_calls": config.max_batch_calls,
            "max_batch_tokens": config.max_batch_tokens,
            "max_batch_cost_usd": config.max_batch_cost_usd,
            "fail_closed": True,
        },
        "credential": {
            "lookup_deferred_until_provider_call": True,
            "environment_or_macos_keychain": True,
            "persisted_in_report": False,
        },
        "runs": runs,
        "summary": {
            "pairs_requested": config.repeats,
            "pairs_completed": pairs_completed,
            "batch_quality": bool(
                len(runs) == config.repeats * 2
                and all(run["quality"]["passed"] for run in runs)
            ),
            "batch": meter.public(),
        },
    }
    validate_public_report(report)
    return report


def validate_public_report(report: Mapping[str, Any]) -> None:
    """Validate the complete public projection with recursive allow-lists."""

    def exact(value: Any, fields: set[str], label: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping) or set(value) != fields:
            raise PairingAbort("security")
        if any(not isinstance(key, str) for key in value):
            raise PairingAbort("security")
        return value

    def integer(value: Any, *, minimum: int = 0) -> None:
        if type(value) is not int or value < minimum:
            raise PairingAbort("security")

    def number(value: Any, *, nullable: bool = False) -> None:
        if value is None and nullable:
            return
        if (
            type(value) not in {int, float}
            or not math.isfinite(float(value))
            or value < 0
        ):
            raise PairingAbort("security")

    def sha256(value: Any) -> None:
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise PairingAbort("security")

    top = exact(
        report,
        {
            "schema_version",
            "mode",
            "executed",
            "status",
            "stop_code",
            "plan_sha256",
            "frozen",
            "limits",
            "credential",
            "runs",
            "summary",
        },
        "report",
    )
    if top["schema_version"] != REPORT_SCHEMA_VERSION:
        raise PairingAbort("security")
    if top["mode"] not in {"plan_only", "live_synthetic"}:
        raise PairingAbort("security")
    if type(top["executed"]) is not bool:
        raise PairingAbort("security")
    if top["status"] not in {"planned", "completed", "stopped"}:
        raise PairingAbort("security")
    if top["stop_code"] not in PUBLIC_ERROR_CODES:
        raise PairingAbort("security")
    sha256(top["plan_sha256"])

    frozen_fields = {
        "case",
        "as_of",
        "window_days",
        "source_files",
        "fixture_sha256",
        "baseline_sha256",
        "prompt_version",
        "prompt_builder_sha256",
        "policy_sha256",
        "dependency_manifest_sha256",
        "runner_source_sha256",
        "runner_runtime_sha256",
        "runner_contract_sha256",
        "w1_query_sha256",
        "w1_terminal_policy_version",
        "w1_terminal_instruction_sha256",
        "provider",
        "model",
        "thinking",
        "reasoning_effort",
        "max_tokens_per_call",
        "budget",
        "input_maximum_chars_both_arms",
        "repeats",
        "pairing_order",
        "w1_workflow",
        "w1_baseline_kind",
        "w1_terminal_actions",
        "a1_workflow",
        "focused_gate",
        "agent_gain_claimed",
    }
    frozen = exact(top["frozen"], frozen_fields, "frozen")
    finite_frozen = {
        "case": CASE_ID,
        "as_of": CASE_AS_OF,
        "window_days": 14,
        "source_files": len(CASE_SOURCES),
        "prompt_version": agent_v1.AGENT_PROMPT_VERSION,
        "w1_terminal_policy_version": W1_TERMINAL_POLICY_VERSION,
        "provider": "deepseek",
        "thinking": "disabled",
        "reasoning_effort": None,
        "pairing_order": ["W1", "A1"],
        "w1_workflow": ["read_memory", "search_history", "terminal_model_action"],
        "w1_baseline_kind": "oracle_assisted_fixed_workflow",
        "w1_terminal_actions": ["finalize_patch", "finish"],
        "a1_workflow": "dynamic_agent_v1",
        "focused_gate": "same_revision_quality_cost_only",
        "agent_gain_claimed": False,
    }
    if any(frozen[key] != expected for key, expected in finite_frozen.items()):
        raise PairingAbort("security")
    if frozen["model"] not in SUPPORTED_MODELS:
        raise PairingAbort("security")
    for field in (
        "fixture_sha256",
        "baseline_sha256",
        "prompt_builder_sha256",
        "policy_sha256",
        "dependency_manifest_sha256",
        "runner_source_sha256",
        "runner_runtime_sha256",
        "runner_contract_sha256",
        "w1_query_sha256",
        "w1_terminal_instruction_sha256",
    ):
        sha256(frozen[field])
    integer(frozen["max_tokens_per_call"], minimum=1)
    integer(frozen["input_maximum_chars_both_arms"], minimum=1)
    integer(frozen["repeats"], minimum=1)
    budget = exact(
        frozen["budget"],
        {"max_turns", "max_tool_calls", "max_total_tokens", "max_prompt_chars"},
        "budget",
    )
    for value in budget.values():
        integer(value, minimum=1)
    if frozen["input_maximum_chars_both_arms"] != budget["max_prompt_chars"]:
        raise PairingAbort("security")

    limits = exact(
        top["limits"],
        {"max_batch_calls", "max_batch_tokens", "max_batch_cost_usd", "fail_closed"},
        "limits",
    )
    integer(limits["max_batch_calls"], minimum=1)
    integer(limits["max_batch_tokens"], minimum=1)
    number(limits["max_batch_cost_usd"])
    if limits["fail_closed"] is not True:
        raise PairingAbort("security")
    if top["plan_sha256"] != _sha(
        {
            "frozen": dict(frozen),
            "limits": dict(limits),
            "execution_order": ["W1", "A1"],
        }
    ):
        raise PairingAbort("security")
    credential = exact(
        top["credential"],
        {
            "lookup_deferred_until_provider_call",
            "environment_or_macos_keychain",
            "persisted_in_report",
        },
        "credential",
    )
    if credential != {
        "lookup_deferred_until_provider_call": True,
        "environment_or_macos_keychain": True,
        "persisted_in_report": False,
    }:
        raise PairingAbort("security")

    aggregate_usage_fields = {
        "model_calls",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "reasoning_tokens",
        "usage_missing",
        "cost_usd",
    }
    quality_check_fields = {
        "terminal_updated",
        "operation_revise",
        "target_preserved",
        "revision_advanced_once",
        "expected_statement",
        "expected_scope",
        "new_evidence_exact_2026_07_17",
        "counterevidence_exact_2026_07_14",
        "source_hashes_exact",
        "cas_chain_exact",
        "strict_evidence_and_sensitive_contract",
        "unique_memory_exactly_two_revisions",
        "usage_complete",
        "no_rejected_invalid_or_budget_steps",
        "source_clone_unchanged",
    }
    runs = top["runs"]
    if not isinstance(runs, list) or len(runs) > frozen["repeats"] * 2:
        raise PairingAbort("security")
    for run in runs:
        run = exact(
            run,
            {
                "arm",
                "repetition",
                "status",
                "error_code",
                "trajectory",
                "initial_revision",
                "result_revision",
                "baseline_sha256",
                "quality",
                "usage",
            },
            "run",
        )
        if run["arm"] not in {"W1", "A1"}:
            raise PairingAbort("security")
        integer(run["repetition"], minimum=1)
        if run["status"] not in {
            "updated",
            "no_change",
            "insufficient_evidence",
            "budget_exhausted",
            "stale",
            "error",
        }:
            raise PairingAbort("security")
        if run["error_code"] not in PUBLIC_ERROR_CODES:
            raise PairingAbort("security")
        if not isinstance(run["trajectory"], list) or any(
            action
            not in {
                "read_memory",
                "search_history",
                "finalize_patch",
                "finish",
                "invalid_action",
            }
            for action in run["trajectory"]
        ):
            raise PairingAbort("security")
        integer(run["initial_revision"], minimum=1)
        if run["result_revision"] is not None:
            integer(run["result_revision"], minimum=1)
        sha256(run["baseline_sha256"])
        if run["baseline_sha256"] != frozen["baseline_sha256"]:
            raise PairingAbort("security")
        quality = exact(run["quality"], {"passed", "score", "checks"}, "quality")
        if type(quality["passed"]) is not bool:
            raise PairingAbort("security")
        if type(quality["score"]) not in {int, float} or not 0 <= quality["score"] <= 1:
            raise PairingAbort("security")
        checks = exact(quality["checks"], quality_check_fields, "quality checks")
        if any(type(value) is not bool for value in checks.values()):
            raise PairingAbort("security")
        if quality["passed"] != all(checks.values()):
            raise PairingAbort("security")
        usage = exact(run["usage"], aggregate_usage_fields, "usage")
        for field in aggregate_usage_fields - {"usage_missing", "cost_usd"}:
            integer(usage[field])
        if type(usage["usage_missing"]) is not bool:
            raise PairingAbort("security")
        number(usage["cost_usd"], nullable=True)

    summary = exact(
        top["summary"],
        {"pairs_requested", "pairs_completed", "batch_quality", "batch"},
        "summary",
    )
    integer(summary["pairs_requested"], minimum=1)
    integer(summary["pairs_completed"])
    if summary["pairs_requested"] != frozen["repeats"]:
        raise PairingAbort("security")
    if summary["batch_quality"] is not None and type(summary["batch_quality"]) is not bool:
        raise PairingAbort("security")
    batch = exact(
        summary["batch"],
        {"calls", "tokens", "cost_usd", "cost_complete", "by_arm"},
        "batch",
    )
    integer(batch["calls"])
    integer(batch["tokens"])
    number(batch["cost_usd"], nullable=True)
    if type(batch["cost_complete"]) is not bool:
        raise PairingAbort("security")
    by_arm = exact(batch["by_arm"], {"W1", "A1"}, "by_arm")
    for arm in ("W1", "A1"):
        values = exact(by_arm[arm], {"calls", "tokens", "cost_usd"}, "arm usage")
        integer(values["calls"])
        integer(values["tokens"])
        number(values["cost_usd"], nullable=True)
    if batch["calls"] != sum(by_arm[arm]["calls"] for arm in ("W1", "A1")):
        raise PairingAbort("security")
    if batch["tokens"] != sum(by_arm[arm]["tokens"] for arm in ("W1", "A1")):
        raise PairingAbort("security")
    if round(sum(by_arm[arm]["cost_usd"] for arm in ("W1", "A1")), 10) != batch[
        "cost_usd"
    ]:
        raise PairingAbort("security")
    if summary["pairs_completed"] > summary["pairs_requested"]:
        raise PairingAbort("security")

    if top["mode"] == "plan_only":
        if (
            top["executed"] is not False
            or top["status"] != "planned"
            or top["stop_code"] != "none"
            or runs
            or summary["pairs_completed"] != 0
            or summary["batch_quality"] is not None
        ):
            raise PairingAbort("security")
    elif top["executed"] is not True or summary["batch_quality"] != (
        len(runs) == frozen["repeats"] * 2
        and all(run["quality"]["passed"] for run in runs)
    ):
        raise PairingAbort("security")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Isolated W1/A1 Agent V1 live pairing")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live")
    parser.add_argument("--expect-plan-sha256")
    parser.add_argument("--model", choices=SUPPORTED_MODELS, default="deepseek-v4-pro")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-tokens-per-call", type=int, default=2000)
    parser.add_argument("--max-batch-calls", type=int, default=4)
    parser.add_argument("--max-batch-tokens", type=int, default=80_000)
    parser.add_argument("--max-batch-cost-usd", type=float, default=0.10)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    # This runner deliberately has no arbitrary report path. Reject the old
    # option before argparse could echo a sensitive user-supplied path.
    if any(item == "--output" or item.startswith("--output=") for item in raw_argv):
        print(
            json.dumps(
                {
                    "schema_version": REPORT_SCHEMA_VERSION,
                    "mode": "plan_only",
                    "executed": False,
                    "status": "stopped",
                    "stop_code": "contract",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    args = build_parser().parse_args(raw_argv)
    config = PairingConfig(
        model=args.model,
        repeats=args.repeats,
        timeout=args.timeout,
        max_tokens_per_call=args.max_tokens_per_call,
        max_batch_calls=args.max_batch_calls,
        max_batch_tokens=args.max_batch_tokens,
        max_batch_cost_usd=args.max_batch_cost_usd,
    )
    try:
        if args.live != (args.confirm_live == LIVE_CONFIRMATION):
            raise PairingAbort("confirmation_required")
        report = (
            run_live_pairing(
                config, expected_plan_sha256=args.expect_plan_sha256 or ""
            )
            if args.live
            else build_plan(config)
        )
        validate_public_report(report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if report["status"] in {"planned", "completed"} else 1
    except PairingAbort as exc:
        print(
            json.dumps(
                {
                    "schema_version": REPORT_SCHEMA_VERSION,
                    "mode": "live_synthetic" if args.live else "plan_only",
                    "executed": False,
                    "status": "stopped",
                    "stop_code": exc.code,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception:
        # Never expose traceback, local paths, provider bodies, prompts, or
        # credentials at the public CLI boundary.
        print(
            json.dumps(
                {
                    "schema_version": REPORT_SCHEMA_VERSION,
                    "mode": "live_synthetic" if args.live else "plan_only",
                    "executed": False,
                    "status": "stopped",
                    "stop_code": "runtime",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
