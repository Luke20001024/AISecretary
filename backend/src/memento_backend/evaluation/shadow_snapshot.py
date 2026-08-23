"""Create a stable read-only copy without ever opening the source for writing."""

from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import sha256_bytes, sha256_json, validate_datetime, validate_relative_path


DEFAULT_SUFFIXES = (".json", ".md", ".txt")
DEFAULT_EXCLUDED_DIRECTORIES = (".git", ".mypy_cache", ".pytest_cache", "__pycache__")
MANIFEST_NAME = "snapshot-manifest.json"


@dataclass(frozen=True)
class ReadOnlySnapshot:
    path: Path
    manifest: Mapping[str, Any]


def create_read_only_snapshot(
    source_root: Path,
    output_root: Path,
    *,
    source_label: str,
    snapshot_kind: str,
    created_at: str,
    authorization_ref: Optional[str] = None,
    allowed_suffixes: Sequence[str] = DEFAULT_SUFFIXES,
    excluded_directories: Sequence[str] = DEFAULT_EXCLUDED_DIRECTORIES,
) -> ReadOnlySnapshot:
    """Copy one stable source tree into a sealed, immutable shadow snapshot.

    The source descriptor is only opened with ``O_RDONLY`` and every source hash
    is checked again after copying.  A concurrent source edit aborts publication.
    """

    validate_datetime(created_at, "created_at")
    if snapshot_kind not in {"synthetic_fixture", "anonymized_samples", "real_vault_snapshot"}:
        raise ContractError("shadow snapshot kind is invalid")
    if snapshot_kind == "real_vault_snapshot" and not (authorization_ref or "").strip():
        raise ContractError("real Vault snapshot requires an explicit user authorization reference", kind="permission")
    if not source_label.strip():
        raise ContractError("shadow snapshot source label is required")
    source = _real_directory(source_root, "snapshot source")
    output = _secure_output_directory(output_root)
    _require_disjoint(source, output)
    suffixes = _normalize_suffixes(allowed_suffixes)
    excluded = frozenset(excluded_directories)
    before, excluded_count = _scan_source(source, suffixes=suffixes, excluded_directories=excluded)
    fingerprint = sha256_json({"files": before})
    source_root_sha256 = sha256_bytes(str(source).encode("utf-8"))
    identity = {
        "snapshot_kind": snapshot_kind,
        "source_label": source_label,
        "source_root_sha256": source_root_sha256,
        "source_fingerprint_sha256": fingerprint,
        "created_at": created_at,
        "authorization_ref": authorization_ref,
        "allowed_suffixes": list(suffixes),
    }
    snapshot_id = "snp_" + sha256_json(identity)[:24]
    target = output / snapshot_id
    if target.exists() or target.is_symlink():
        snapshot = verify_read_only_snapshot(target)
        if snapshot.manifest["source_fingerprint_sha256"] != fingerprint:
            raise ContractError("snapshot id is bound to different source bytes", kind="conflict")
        return snapshot

    staging = output / f".{snapshot_id}.staging-{os.getpid()}"
    if staging.exists() or staging.is_symlink():
        raise ContractError("shadow snapshot staging path already exists", kind="conflict")
    staging.mkdir(mode=0o700)
    try:
        for entry in before:
            relative = str(entry["path"])
            destination = staging.joinpath(*relative.split("/"))
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            data = _read_source_file(source, relative)
            if len(data) != entry["byte_size"] or sha256_bytes(data) != entry["sha256"]:
                raise ContractError("snapshot source changed during copy", kind="conflict")
            _write_new_file(destination, data)

        after, after_excluded_count = _scan_source(
            source, suffixes=suffixes, excluded_directories=excluded
        )
        if before != after or excluded_count != after_excluded_count:
            raise ContractError("snapshot source changed during copy", kind="conflict")
        manifest = {
            "schema_version": "1.0",
            "kind": "memento_shadow_snapshot",
            "snapshot_id": snapshot_id,
            "snapshot_kind": snapshot_kind,
            "source_label": source_label,
            "source_root_sha256": source_root_sha256,
            "source_fingerprint_sha256": fingerprint,
            "created_at": created_at,
            "authorization_ref": authorization_ref,
            "allowed_suffixes": list(suffixes),
            "file_count": len(before),
            "byte_count": sum(int(entry["byte_size"]) for entry in before),
            "excluded_count": excluded_count,
            "files": before,
            "read_only": True,
        }
        validate_contract("shadow-snapshot-v1.schema.json", manifest)
        _write_new_file(
            staging / MANIFEST_NAME,
            (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        )
        _seal_tree(staging)
        os.rename(staging, target)
        _fsync_directory(output)
        return verify_read_only_snapshot(target)
    except BaseException:
        _remove_staging(staging)
        raise


def verify_read_only_snapshot(snapshot_root: Path) -> ReadOnlySnapshot:
    root = _real_directory(snapshot_root, "shadow snapshot")
    if stat.S_IMODE(root.lstat().st_mode) & 0o222:
        raise ContractError("shadow snapshot root is writable", kind="permission")
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ContractError("shadow snapshot manifest is missing", kind="evidence")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_contract("shadow-snapshot-v1.schema.json", manifest)
    file_entries = list(manifest["files"])
    paths = [str(entry["path"]) for entry in file_entries]
    if paths != sorted(paths) or len(set(paths)) != len(paths):
        raise ContractError("shadow snapshot manifest paths must be unique and sorted", kind="evidence")
    if manifest["file_count"] != len(file_entries):
        raise ContractError("shadow snapshot file count is inconsistent", kind="evidence")
    if manifest["byte_count"] != sum(int(entry["byte_size"]) for entry in file_entries):
        raise ContractError("shadow snapshot byte count is inconsistent", kind="evidence")
    suffixes = tuple(manifest["allowed_suffixes"])
    for relative in paths:
        validate_relative_path(relative)
        if Path(relative).suffix.lower() not in suffixes:
            raise ContractError("shadow snapshot path is outside its suffix allow-list", kind="evidence")
    identity = {
        "snapshot_kind": manifest["snapshot_kind"],
        "source_label": manifest["source_label"],
        "source_root_sha256": manifest["source_root_sha256"],
        "source_fingerprint_sha256": manifest["source_fingerprint_sha256"],
        "created_at": manifest["created_at"],
        "authorization_ref": manifest["authorization_ref"],
        "allowed_suffixes": list(manifest["allowed_suffixes"]),
    }
    if manifest["snapshot_id"] != "snp_" + sha256_json(identity)[:24]:
        raise ContractError("shadow snapshot identity is inconsistent", kind="evidence")
    expected_paths = set(paths)
    actual_paths: set[str] = set()
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ContractError("shadow snapshot contains a symlink", kind="path")
        if stat.S_ISREG(info.st_mode):
            relative = path.relative_to(root).as_posix()
            if relative != MANIFEST_NAME:
                actual_paths.add(relative)
            if stat.S_IMODE(info.st_mode) & 0o222:
                raise ContractError("shadow snapshot file is writable", kind="permission")
        elif stat.S_ISDIR(info.st_mode):
            if stat.S_IMODE(info.st_mode) & 0o222:
                raise ContractError("shadow snapshot directory is writable", kind="permission")
        else:
            raise ContractError("shadow snapshot contains a special file", kind="path")
    if actual_paths != expected_paths:
        raise ContractError("shadow snapshot file set differs from manifest", kind="evidence")
    entries = {str(entry["path"]): entry for entry in file_entries}
    for relative in sorted(actual_paths):
        data = _read_source_file(root, relative)
        entry = entries[relative]
        if len(data) != entry["byte_size"] or sha256_bytes(data) != entry["sha256"]:
            raise ContractError("shadow snapshot file hash mismatch", kind="evidence")
    if sha256_json({"files": file_entries}) != manifest["source_fingerprint_sha256"]:
        raise ContractError("shadow snapshot fingerprint is inconsistent", kind="evidence")
    return ReadOnlySnapshot(path=root, manifest=manifest)


def _scan_source(
    root: Path,
    *,
    suffixes: Sequence[str],
    excluded_directories: frozenset[str],
) -> tuple[list[dict[str, Any]], int]:
    entries: list[dict[str, Any]] = []
    excluded_count = 0
    for path in sorted(root.rglob("*")):
        relative_path = path.relative_to(root)
        if any(part in excluded_directories for part in relative_path.parts[:-1]):
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ContractError("snapshot source contains a symlink", kind="path")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ContractError("snapshot source contains a special file", kind="path")
        if path.suffix.lower() not in suffixes:
            excluded_count += 1
            continue
        relative = relative_path.as_posix()
        validate_relative_path(relative)
        data = _read_source_file(root, relative)
        entries.append({"path": relative, "sha256": sha256_bytes(data), "byte_size": len(data)})
    return entries, excluded_count


def _read_source_file(root: Path, relative: str) -> bytes:
    validate_relative_path(relative)
    path = root.joinpath(*relative.split("/"))
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ContractError("snapshot file escapes source root", kind="path") from exc
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ContractError("snapshot file must be regular and non-symlinked", kind="path")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def _write_new_file(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _seal_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        info = path.lstat()
        if stat.S_ISREG(info.st_mode):
            os.chmod(path, 0o400, follow_symlinks=False)
        elif stat.S_ISDIR(info.st_mode):
            os.chmod(path, 0o500, follow_symlinks=False)
    os.chmod(root, 0o500, follow_symlinks=False)


def _remove_staging(path: Path) -> None:
    if not path.exists() or path.is_symlink():
        return
    for item in path.rglob("*"):
        if item.is_dir() and not item.is_symlink():
            os.chmod(item, 0o700, follow_symlinks=False)
        elif item.exists() and not item.is_symlink():
            os.chmod(item, 0o600, follow_symlinks=False)
    os.chmod(path, 0o700, follow_symlinks=False)
    shutil.rmtree(path)


def _real_directory(path: Path, name: str) -> Path:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ContractError(f"{name} does not exist", kind="path") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ContractError(f"{name} must be a real directory", kind="path")
    if info.st_uid != os.getuid():
        raise ContractError(f"{name} has a foreign owner", kind="permission")
    return path.resolve(strict=True)


def _secure_output_directory(path: Path) -> Path:
    root = _real_directory(path, "snapshot output root")
    if stat.S_IMODE(path.lstat().st_mode) & 0o077:
        raise ContractError("snapshot output root must be owner-only", kind="permission")
    return root


def _require_disjoint(source: Path, output: Path) -> None:
    try:
        source.relative_to(output)
    except ValueError:
        pass
    else:
        raise ContractError("snapshot source and output roots overlap", kind="path")
    try:
        output.relative_to(source)
    except ValueError:
        pass
    else:
        raise ContractError("snapshot source and output roots overlap", kind="path")


def _normalize_suffixes(values: Iterable[str]) -> tuple[str, ...]:
    suffixes = tuple(sorted({str(value).lower() for value in values}))
    if not suffixes or any(not value.startswith(".") or "/" in value for value in suffixes):
        raise ContractError("snapshot suffix allow-list is invalid")
    return suffixes


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
