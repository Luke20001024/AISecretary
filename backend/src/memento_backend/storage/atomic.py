"""Secure atomic filesystem primitives for the isolated Memento V2 store."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional, Union, cast

from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import validate_relative_path


FaultHook = Callable[[str, str], None]
JsonValue = Union[None, bool, int, float, str, list["JsonValue"], dict[str, "JsonValue"]]


class AtomicFileStore:
    """Owner-only, symlink-safe reads and atomic writes under an explicit root.

    The caller must supply an already-created isolated root. There is deliberately
    no implicit production Vault default, so tests and later composition cannot
    accidentally write to a user's real memory directory.
    """

    def __init__(self, root: Path, *, fault_hook: Optional[FaultHook] = None) -> None:
        self.root = root.resolve(strict=False)
        self._fault_hook = fault_hook
        self._validate_root(root)

    def _fault(self, stage: str, relative_path: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(stage, relative_path)

    @staticmethod
    def _validate_secure_mode(mode: int, name: str) -> None:
        if stat.S_IMODE(mode) & 0o077:
            raise ContractError(f"{name} must be owner-only", kind="permission")

    def _validate_root(self, original: Path) -> None:
        try:
            info = original.lstat()
        except FileNotFoundError as exc:
            raise ContractError("storage root does not exist", kind="path") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ContractError("storage root must be a real directory", kind="path")
        if info.st_uid != os.getuid():
            raise ContractError("storage root has a foreign owner", kind="permission")
        self._validate_secure_mode(info.st_mode, "storage root")
        if original.resolve(strict=True) != self.root:
            raise ContractError("storage root resolution changed", kind="path")

    def _parts(self, relative_path: str) -> tuple[str, ...]:
        normalized = validate_relative_path(relative_path)
        return tuple(normalized.split("/"))

    def _absolute(self, relative_path: str) -> Path:
        parts = self._parts(relative_path)
        value = self.root.joinpath(*parts)
        try:
            value.relative_to(self.root)
        except ValueError as exc:
            raise ContractError("path escapes storage root", kind="path") from exc
        return value

    def _validate_directory(self, path: Path) -> None:
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            raise ContractError("storage directory does not exist", kind="path") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ContractError("storage path contains a non-directory", kind="path")
        if info.st_uid != os.getuid():
            raise ContractError("storage directory has a foreign owner", kind="permission")
        self._validate_secure_mode(info.st_mode, "storage directory")

    def ensure_directory(self, relative_path: str) -> Path:
        parts = self._parts(relative_path)
        current = self.root
        for part in parts:
            current = current / part
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            self._validate_directory(current)
        return current

    def _secure_parent(self, relative_path: str, *, create: bool) -> tuple[Path, Path]:
        target = self._absolute(relative_path)
        parent_relative = "/".join(self._parts(relative_path)[:-1])
        if parent_relative:
            parent = self.ensure_directory(parent_relative) if create else self._absolute(parent_relative)
            self._validate_directory(parent)
        else:
            parent = self.root
            self._validate_directory(parent)
        return parent, target

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _json_bytes(value: Mapping[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")

    def _write_temp(self, parent: Path, relative_path: str, data: bytes) -> Path:
        descriptor, raw_path = tempfile.mkstemp(prefix=".memento-v2-", dir=str(parent))
        temp_path = Path(raw_path)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._fault("after_temp_fsync", relative_path)
            return temp_path
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temp_path.unlink(missing_ok=True)
            raise

    def write_new_bytes(self, relative_path: str, data: bytes) -> None:
        parent, target = self._secure_parent(relative_path, create=True)
        temp_path = self._write_temp(parent, relative_path, data)
        try:
            self._fault("before_link", relative_path)
            try:
                os.link(str(temp_path), str(target), follow_symlinks=False)
            except FileExistsError as exc:
                raise ContractError("append-only target already exists", kind="conflict") from exc
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ContractError("unsafe append-only target", kind="path") from exc
                raise
            # Remove the staging name before any reader can validate the public
            # inode. The target is already complete, while its link count is now
            # the required one instead of a transient two.
            temp_path.unlink()
            os.chmod(target, 0o600, follow_symlinks=False)
            self._fsync_directory(parent)
            self._fault("after_link", relative_path)
        finally:
            temp_path.unlink(missing_ok=True)

    def write_new_json(self, relative_path: str, value: Mapping[str, Any]) -> None:
        self.write_new_bytes(relative_path, self._json_bytes(value))

    def write_new_json_idempotent(self, relative_path: str, value: Mapping[str, Any]) -> None:
        try:
            self.write_new_json(relative_path, value)
        except ContractError as exc:
            if exc.kind != "conflict" or self.read_json(relative_path) != dict(value):
                raise

    def replace_bytes(self, relative_path: str, data: bytes) -> None:
        if not (relative_path.startswith("indexes/") or relative_path == "projections/current.json"):
            raise ContractError("atomic replace is limited to rebuildable indexes and the current pointer", kind="authorization")
        parent, target = self._secure_parent(relative_path, create=True)
        if target.exists() or target.is_symlink():
            self._validate_regular_file(target)
        temp_path = self._write_temp(parent, relative_path, data)
        try:
            self._fault("before_replace", relative_path)
            os.replace(str(temp_path), str(target))
            self._fsync_directory(parent)
            self._fault("after_replace", relative_path)
        finally:
            temp_path.unlink(missing_ok=True)

    def replace_json(self, relative_path: str, value: Mapping[str, Any]) -> None:
        self.replace_bytes(relative_path, self._json_bytes(value))

    def _validate_regular_file(self, path: Path) -> os.stat_result:
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            raise ContractError("storage file does not exist", kind="not_found") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ContractError("storage file must be a regular file", kind="path")
        if info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ContractError("storage file ownership is unsafe", kind="permission")
        self._validate_secure_mode(info.st_mode, "storage file")
        return info

    def read_bytes(self, relative_path: str, *, max_bytes: int = 16 * 1024 * 1024) -> bytes:
        parent, target = self._secure_parent(relative_path, create=False)
        del parent
        expected = self._validate_regular_file(target)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(str(target), flags)
        try:
            actual = os.fstat(descriptor)
            if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                raise ContractError("storage file changed during open", kind="conflict")
            if actual.st_size > max_bytes:
                raise ContractError("storage file exceeds read limit", kind="size")
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            value = b"".join(chunks)
            if len(value) > max_bytes:
                raise ContractError("storage file exceeds read limit", kind="size")
            return value
        finally:
            os.close(descriptor)

    def read_json(self, relative_path: str, *, max_bytes: int = 16 * 1024 * 1024) -> Mapping[str, Any]:
        try:
            value = json.loads(self.read_bytes(relative_path, max_bytes=max_bytes).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("storage JSON is invalid", kind="schema") from exc
        if not isinstance(value, dict):
            raise ContractError("storage JSON root must be an object", kind="schema")
        return cast(Mapping[str, Any], value)

    def exists(self, relative_path: str) -> bool:
        target = self._absolute(relative_path)
        try:
            self._validate_regular_file(target)
        except ContractError as exc:
            if exc.kind == "not_found":
                return False
            raise
        return True

    def list_files(self, relative_path: str, *, suffix: str = "") -> list[str]:
        directory = self._absolute(relative_path)
        self._validate_directory(directory)
        output: list[str] = []
        for child in directory.iterdir():
            if child.name.startswith("."):
                continue
            self._validate_regular_file(child)
            if not suffix or child.name.endswith(suffix):
                output.append(f"{relative_path}/{child.name}")
        return sorted(output)

    def list_tree_files(
        self,
        relative_path: str,
        *,
        maximum_files: int = 8192,
    ) -> list[str]:
        """List every regular file below a secure directory.

        Projection bundles use this stricter recursive inventory to prove that
        their manifest covers the complete sealed directory. Hidden files are
        intentionally included: an undeclared file cannot become invisible to
        the bundle integrity check merely because its name starts with a dot.
        """

        root = self._absolute(relative_path)
        self._validate_directory(root)
        output: list[str] = []

        def visit(directory: Path, prefix: str) -> None:
            self._validate_directory(directory)
            for child in sorted(directory.iterdir(), key=lambda item: item.name):
                child_relative = f"{prefix}/{child.name}"
                try:
                    info = child.lstat()
                except FileNotFoundError as exc:
                    raise ContractError(
                        "storage tree changed during inventory", kind="conflict"
                    ) from exc
                if stat.S_ISLNK(info.st_mode):
                    raise ContractError(
                        "storage tree contains a symbolic link", kind="path"
                    )
                if stat.S_ISDIR(info.st_mode):
                    visit(child, child_relative)
                    continue
                self._validate_regular_file(child)
                output.append(child_relative)
                if len(output) > maximum_files:
                    raise ContractError(
                        "storage tree exceeds file inventory limit", kind="size"
                    )

        visit(root, relative_path)
        return output

    def directory_exists(self, relative_path: str) -> bool:
        directory = self._absolute(relative_path)
        try:
            self._validate_directory(directory)
        except ContractError as exc:
            if exc.kind == "path" and not directory.exists() and not directory.is_symlink():
                return False
            raise
        return True

    def rename_directory_new(self, source_relative: str, target_relative: str) -> None:
        source = self._absolute(source_relative)
        self._validate_directory(source)
        source_parent_relative = "/".join(self._parts(source_relative)[:-1])
        target_parent_relative = "/".join(self._parts(target_relative)[:-1])
        source_parent = self.root if not source_parent_relative else self._absolute(source_parent_relative)
        target_parent = self.root if not target_parent_relative else self.ensure_directory(target_parent_relative)
        self._validate_directory(source_parent)
        self._validate_directory(target_parent)
        target = self._absolute(target_relative)
        if target.exists() or target.is_symlink():
            raise ContractError("immutable directory target already exists", kind="conflict")
        self._fault("before_directory_rename", target_relative)
        try:
            os.rename(str(source), str(target))
        except FileExistsError as exc:
            raise ContractError("immutable directory target already exists", kind="conflict") from exc
        self._fsync_directory(source_parent)
        if target_parent != source_parent:
            self._fsync_directory(target_parent)
        self._validate_directory(target)
        self._fault("after_directory_rename", target_relative)

    @contextlib.contextmanager
    def lock(self, name: str) -> Iterator[None]:
        if not name or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in name):
            raise ContractError("lock name is invalid", kind="path")
        path = f"locks/{name}.lock"
        if not self.exists(path):
            try:
                self.write_new_bytes(path, b"")
            except ContractError as exc:
                if exc.kind != "conflict":
                    raise
        target = self._absolute(path)
        expected = self._validate_regular_file(target)
        descriptor = os.open(str(target), os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            actual = os.fstat(descriptor)
            if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                raise ContractError("lock file changed during open", kind="conflict")
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
