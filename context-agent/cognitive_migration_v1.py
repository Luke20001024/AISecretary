"""Read-only inventory and index-only backfill for Cognitive Secretary V1.

The migration boundary is intentionally narrow: source Markdown and existing
Daily Review / Agent V1 objects are inventoried, while only the cognitive
record sidecar store may be written.  Provider calls, receipts, daily bundles,
long-term memories and UI projections are outside this module.
"""

from __future__ import annotations

import datetime as dt
import errno
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from cognitive_store_v1 import RecordStore
from core import ContractError, canonical_json, sha256_bytes


MIGRATION_VERSION = "cognitive-migration-v1.0"
DAILY_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_STATE_BYTES = 16 * 1024 * 1024


def _local_now(value: dt.datetime | None) -> dt.datetime:
    result = dt.datetime.now().astimezone() if value is None else value
    if result.tzinfo is None or result.utcoffset() is None:
        raise ContractError("migration now 必须是带时区 datetime")
    return result


def _safe_regular_bytes(path: Path, *, maximum: int, required: bool) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        if required:
            raise ContractError("迁移源文件不存在", kind="not_found")
        return None
    except OSError as exc:
        kind = "evidence" if exc.errno in {errno.ELOOP, errno.EISDIR} else "runtime"
        raise ContractError("迁移文件无法安全读取", kind=kind) from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or before.st_size > maximum
        ):
            raise ContractError("迁移文件必须是当前用户的单链接普通文件", kind="evidence")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise ContractError("迁移文件超过大小限制", kind="evidence")
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_uid",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(getattr(before, key) != getattr(after, key) for key in stable):
            raise ContractError("迁移文件在读取期间发生变化", kind="stale")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _aggregate(rows: Iterable[tuple[str, str]]) -> str:
    return sha256_bytes(canonical_json([list(row) for row in sorted(rows)]).encode("utf-8"))


def _json_sha(path: Path) -> str | None:
    payload = _safe_regular_bytes(path, maximum=MAX_STATE_BYTES, required=False)
    if payload is None:
        return None
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("迁移盘点发现无效 JSON", kind="evidence") from exc
    if not isinstance(parsed, Mapping):
        raise ContractError("迁移盘点 JSON 必须是 object", kind="evidence")
    return sha256_bytes(payload)


@dataclass(frozen=True)
class MigrationInventory:
    source_files: tuple[str, ...]
    source_hashes: Mapping[str, str]
    daily_review_count: int
    daily_review_aggregate_sha256: str
    agent_memory_revision_count: int
    agent_memory_aggregate_sha256: str
    agent_action_count: int
    agent_action_aggregate_sha256: str
    agent_profile_sha256: str | None

    @property
    def source_aggregate_sha256(self) -> str:
        return _aggregate(self.source_hashes.items())

    def public_summary(self) -> dict[str, Any]:
        return {
            "source_file_count": len(self.source_files),
            "source_aggregate_sha256": self.source_aggregate_sha256,
            "daily_review_count": self.daily_review_count,
            "daily_review_aggregate_sha256": self.daily_review_aggregate_sha256,
            "agent_memory_revision_count": self.agent_memory_revision_count,
            "agent_memory_aggregate_sha256": self.agent_memory_aggregate_sha256,
            "agent_action_count": self.agent_action_count,
            "agent_action_aggregate_sha256": self.agent_action_aggregate_sha256,
            "agent_profile_sha256": self.agent_profile_sha256,
        }


@dataclass(frozen=True)
class MigrationResult:
    status: str
    source_file_count: int
    parsed_count: int
    created_count: int
    revised_count: int
    unchanged_count: int
    needs_review_count: int
    source_aggregate_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "kind": "memento_cognitive_migration_result",
            "migration_version": MIGRATION_VERSION,
            "status": self.status,
            "source_file_count": self.source_file_count,
            "parsed_count": self.parsed_count,
            "created_count": self.created_count,
            "revised_count": self.revised_count,
            "unchanged_count": self.unchanged_count,
            "needs_review_count": self.needs_review_count,
            "source_aggregate_sha256": self.source_aggregate_sha256,
        }


class CognitiveMigration:
    """Inventory legacy state and backfill record sidecars without AI calls."""

    def __init__(self, vault: Path, *, record_store: RecordStore | None = None) -> None:
        try:
            resolved = Path(vault).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ContractError("Vault 不存在", kind="not_found") from exc
        if not resolved.is_dir():
            raise ContractError("Vault 必须是目录", kind="not_found")
        self.vault = resolved
        self.records = record_store or RecordStore(resolved)
        if self.records.vault != resolved:
            raise ContractError("record store 与迁移 Vault 不一致", kind="evidence")

    def _root_daily_files(self) -> tuple[str, ...]:
        result: list[str] = []
        for child in self.vault.iterdir():
            if DAILY_FILE_RE.fullmatch(child.name):
                # Validate calendar semantics before accepting a filename.
                try:
                    dt.date.fromisoformat(child.stem)
                except ValueError as exc:
                    raise ContractError("日级记录文件名日期无效", kind="evidence") from exc
                result.append(child.name)
        return tuple(sorted(result))

    def _directory_inventory(self, path: Path, pattern: str) -> tuple[int, str]:
        if not path.exists() and not path.is_symlink():
            return 0, _aggregate(())
        details = path.lstat()
        if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode) or details.st_uid != os.getuid():
            raise ContractError("迁移盘点目录不安全", kind="evidence")
        rows: list[tuple[str, str]] = []
        for child in sorted(path.glob(pattern), key=lambda row: row.name):
            payload = _safe_regular_bytes(child, maximum=MAX_STATE_BYTES, required=True)
            assert payload is not None
            rows.append((child.name, sha256_bytes(payload)))
        return len(rows), _aggregate(rows)

    def inventory(self) -> MigrationInventory:
        source_files = self._root_daily_files()
        source_hashes: dict[str, str] = {}
        for source_file in source_files:
            payload = _safe_regular_bytes(
                self.vault / source_file,
                maximum=MAX_SOURCE_BYTES,
                required=True,
            )
            assert payload is not None
            source_hashes[source_file] = sha256_bytes(payload)
        review_count, review_sha = self._directory_inventory(
            self.vault / "Reviews" / "Daily", "*.md"
        )
        memory_count, memory_sha = self._directory_inventory(
            self.vault / ".context-agent" / "agent-v1" / "memories", "*.json"
        )
        action_count, action_sha = self._directory_inventory(
            self.vault / ".context-agent" / "agent-v1" / "user-actions", "*.json"
        )
        profile_sha = _json_sha(
            self.vault / ".context-agent" / "agent-v1" / "profile.json"
        )
        return MigrationInventory(
            source_files=source_files,
            source_hashes=source_hashes,
            daily_review_count=review_count,
            daily_review_aggregate_sha256=review_sha,
            agent_memory_revision_count=memory_count,
            agent_memory_aggregate_sha256=memory_sha,
            agent_action_count=action_count,
            agent_action_aggregate_sha256=action_sha,
            agent_profile_sha256=profile_sha,
        )

    def backfill_record_index(
        self,
        *,
        source_files: Iterable[str] | None = None,
        now: dt.datetime | None = None,
    ) -> MigrationResult:
        before = self.inventory()
        selected = before.source_files if source_files is None else tuple(source_files)
        if len(selected) != len(set(selected)):
            raise ContractError("迁移 source_files 不得重复")
        if any(source_file not in before.source_hashes for source_file in selected):
            raise ContractError("迁移只允许 Vault 根目录的有效日级记录", kind="evidence")
        current_now = _local_now(now)
        parsed = created = revised = unchanged = needs_review = 0
        for source_file in selected:
            result = self.records.reconcile_day(
                source_file,
                now=current_now,
                timezone=current_now.tzinfo,
            )
            parsed += result.parsed_count
            created += len(result.created_record_ids)
            revised += len(result.revised_record_ids)
            unchanged += len(result.unchanged_record_ids)
            needs_review += len(result.needs_review)
        after = self.inventory()
        if before.source_files != after.source_files or dict(before.source_hashes) != dict(after.source_hashes):
            raise ContractError("迁移前后原始日级记录对账失败", kind="stale")
        if (
            before.daily_review_count != after.daily_review_count
            or before.daily_review_aggregate_sha256 != after.daily_review_aggregate_sha256
            or before.agent_memory_revision_count != after.agent_memory_revision_count
            or before.agent_memory_aggregate_sha256 != after.agent_memory_aggregate_sha256
            or before.agent_action_count != after.agent_action_count
            or before.agent_action_aggregate_sha256 != after.agent_action_aggregate_sha256
            or before.agent_profile_sha256 != after.agent_profile_sha256
        ):
            raise ContractError("迁移改变了不可写入的旧状态", kind="conflict")
        status = "needs_review" if needs_review else "completed"
        return MigrationResult(
            status=status,
            source_file_count=len(selected),
            parsed_count=parsed,
            created_count=created,
            revised_count=revised,
            unchanged_count=unchanged,
            needs_review_count=needs_review,
            source_aggregate_sha256=after.source_aggregate_sha256,
        )


__all__ = [
    "CognitiveMigration",
    "MIGRATION_VERSION",
    "MigrationInventory",
    "MigrationResult",
]
