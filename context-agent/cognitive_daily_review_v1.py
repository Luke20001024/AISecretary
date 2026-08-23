"""Crash-safe Markdown projection for committed Cognitive Secretary summaries.

The structured ``DailySummaryRevision`` remains the fact source.  This module
only creates a local, human-readable ``Reviews/Daily/YYYY-MM-DD.md`` projection
from a committed summary and its exact source/receipt revisions.  It never
exposes Daily Review prose as long-term evidence and never calls a Provider.

Everything after the ``## 我的补充`` heading is user-owned opaque UTF-8.
That byte tail is carried across regenerations without parsing or reflowing it.
"""

from __future__ import annotations

import contextlib
import ctypes
import datetime as dt
import errno
import fcntl
import html
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from core import ContractError, canonical_json, sha256_bytes
from cognitive_v1 import (
    DailySummaryRevision,
    InterpretationReceiptRevision,
    ObjectRef,
    SourceRecordRevision,
)


PROJECTION_VERSION = "cognitive-daily-review-v1"
MAX_REVIEW_BYTES = 4 * 1024 * 1024
MAX_JOURNAL_BYTES = 128 * 1024
SUPPLEMENT_HEADING = "## 我的补充"
SUPPLEMENT_MARKER = SUPPLEMENT_HEADING.encode("utf-8")
DEFAULT_SUPPLEMENT = "\n\n无\n".encode("utf-8")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TX_RE = re.compile(r"^rvtx_[0-9a-f]{24}$")
JOURNAL_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "transaction_id",
        "local_date",
        "review_file",
        "candidate_name",
        "candidate_sha256",
        "expected_review_sha256",
        "user_supplement_sha256",
    }
)
OWN_FRONTMATTER_FIELDS = frozenset(
    {
        "date",
        "type",
        "period",
        "projection",
        "summary_id",
        "source",
        "source_manifest_sha256",
        "receipt_manifest_sha256",
    }
)
OWN_SECTIONS = (
    "## 今日概览",
    "## 今日主题",
    "## 发生的变化",
    "## 尚未解决",
    "## 行动线索",
    "## 整理索引",
    SUPPLEMENT_HEADING,
)
LEGACY_SECTIONS = (
    "## 工作与生活现场",
    "## 行动线索",
    "## 灵感与想法",
    "## 个人记录/情绪",
    "## 已忽略",
    "## 来源索引",
    SUPPLEMENT_HEADING,
)
SOURCE_TYPE_LABELS = {
    "text": "文字",
    "screenshot_ocr": "截图·OCR",
    "voice_transcript": "语音",
    "image_note": "图片",
    "file_note": "文件",
}


def _sha(value: bytes) -> str:
    return sha256_bytes(value)


def _manifest_sha(refs: Sequence[ObjectRef]) -> str:
    rows = [
        ref.to_dict()
        for ref in sorted(
            refs,
            key=lambda item: (item.kind, item.id, item.revision, item.revision_sha256),
        )
    ]
    return _sha(canonical_json(rows).encode("utf-8"))


def _safe_inline(value: str) -> str:
    """Keep Agent/user text visible without allowing it to create structure."""

    text = " ".join(str(value).replace("\x00", "").splitlines()).strip()
    text = html.escape(text, quote=False)
    for token in ("\\", "`", "*", "_", "[", "]", "#", "|", ">"):
        text = text.replace(token, "\\" + token)
    return text or "无"


def _list_markdown(values: Sequence[str]) -> list[str]:
    return ["- " + _safe_inline(value) for value in values] or ["无"]


def _timestamp_label(value: str) -> str:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.strftime("%H:%M")


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _read_frontmatter(prefix: str) -> tuple[dict[str, str], list[str]]:
    normalized = prefix.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0] != "---":
        raise ContractError("Daily Review 缺少 frontmatter", kind="evidence")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise ContractError("Daily Review frontmatter 未闭合", kind="evidence") from exc
    metadata: dict[str, str] = {}
    for line in lines[1:closing]:
        if ":" not in line:
            raise ContractError("Daily Review frontmatter 含无效行", kind="evidence")
        key, raw = line.split(":", 1)
        if not key or key in metadata:
            raise ContractError("Daily Review frontmatter 字段重复", kind="evidence")
        metadata[key] = raw.lstrip()
    return metadata, lines[closing + 1 :]


def _structural_headings(lines: Sequence[str]) -> tuple[str, ...]:
    headings: list[str] = []
    in_fence = False
    for line in lines:
        if re.match(r"^\s*(?:```|~~~)", line):
            in_fence = not in_fence
            continue
        if not in_fence and (line.startswith("# ") or line.startswith("## ")):
            headings.append(line)
    return tuple(headings)


def _extract_supplement(content: bytes, local_date: str) -> tuple[bytes, str]:
    if len(content) > MAX_REVIEW_BYTES or b"\x00" in content:
        raise ContractError("Daily Review 内容不安全", kind="evidence")
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("Daily Review 必须是 UTF-8", kind="evidence") from exc

    offset = 0
    marker_end: int | None = None
    in_fence = False
    for raw_line in content.splitlines(keepends=True):
        try:
            line = raw_line.rstrip(b"\r\n").decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError("Daily Review 必须是 UTF-8", kind="evidence") from exc
        if re.match(r"^\s*(?:```|~~~)", line):
            in_fence = not in_fence
        elif not in_fence and line == SUPPLEMENT_HEADING:
            marker_end = offset + len(raw_line)
            break
        offset += len(raw_line)
    if marker_end is None:
        # splitlines() drops a final non-newline line from the offset accounting;
        # the loop still observes it, so absence here is unambiguous.
        raise ContractError("Daily Review 缺少『我的补充』区", kind="evidence")

    prefix = content[:marker_end]
    metadata, body_lines = _read_frontmatter(prefix.decode("utf-8"))
    if metadata.get("date") != local_date or metadata.get("period") != "daily":
        raise ContractError("Daily Review 日期或周期不匹配", kind="evidence")
    review_type = metadata.get("type")
    headings = _structural_headings(body_lines)
    expected_h1 = f"# Daily Review · {local_date}"
    if review_type == "memento-cognitive-review":
        if frozenset(metadata) != OWN_FRONTMATTER_FIELDS:
            raise ContractError("认知 Daily Review frontmatter 字段不符合合同", kind="evidence")
        if metadata.get("projection") != PROJECTION_VERSION:
            raise ContractError("Daily Review 投影版本无效", kind="evidence")
        if metadata.get("summary_id") != "dsum_" + local_date.replace("-", ""):
            raise ContractError("Daily Review summary_id 与日期不一致", kind="evidence")
        if metadata.get("source") != f'"[[{local_date}]]"':
            raise ContractError("Daily Review 来源链接无效", kind="evidence")
        for key in ("source_manifest_sha256", "receipt_manifest_sha256"):
            raw = metadata.get(key, "")
            if not (len(raw) == 66 and raw.startswith('"') and raw.endswith('"') and SHA_RE.fullmatch(raw[1:-1])):
                raise ContractError(f"Daily Review {key} 无效", kind="evidence")
        expected = (expected_h1, *OWN_SECTIONS)
    elif review_type == "memento-review":
        # One-time compatibility with the already installed Daily Review.  Its
        # system-owned prefix is replaced, while the supplement tail survives.
        expected = (expected_h1, *LEGACY_SECTIONS)
    else:
        raise ContractError("Daily Review 类型不受支持", kind="evidence")
    if headings != expected:
        raise ContractError("Daily Review 系统结构损坏或冲突", kind="evidence")
    # Decode of the whole document above proves the opaque tail is valid UTF-8.
    return content[marker_end:], decoded


def _supplement_sha(payload: bytes) -> str | None:
    semantic = payload.decode("utf-8").strip()
    return None if not semantic or semantic == "无" else _sha(payload)


@dataclass(frozen=True)
class DailyReviewProjectionResult:
    """Finite, evidence-free result that a later summary revision may bind."""

    status: str
    base_summary_ref: ObjectRef
    review_file: str
    review_sha256: str
    user_supplement_sha256: str | None

    def __post_init__(self) -> None:
        if self.status not in {"created", "updated", "unchanged", "recovered"}:
            raise ContractError("Daily Review 投影状态无效")
        if self.base_summary_ref.kind != "daily_summary":
            raise ContractError("Daily Review base_summary_ref 无效")
        if not SHA_RE.fullmatch(self.review_sha256):
            raise ContractError("Daily Review hash 无效")
        if self.user_supplement_sha256 is not None and not SHA_RE.fullmatch(self.user_supplement_sha256):
            raise ContractError("用户补充 hash 无效")

    def summary_binding(self) -> dict[str, str | None]:
        """Return only fields allowed in the next DailySummary revision."""

        return {
            "review_file": self.review_file,
            "review_sha256": self.review_sha256,
            "user_supplement_sha256": self.user_supplement_sha256,
        }


class _ProjectionLock:
    def __init__(self, renderer: "CognitiveDailyReviewRenderer") -> None:
        self.renderer = renderer
        self.descriptor: int | None = None

    def __enter__(self) -> "_ProjectionLock":
        self.renderer._ensure_layout()
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.renderer.lock_path, flags, 0o600)
        except OSError as exc:
            raise ContractError("Daily Review 锁无法安全打开", kind="evidence") from exc
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.getuid()
            or details.st_nlink != 1
            or details.st_mode & 0o077
        ):
            os.close(descriptor)
            raise ContractError("Daily Review 锁必须是当前用户的私有单链接文件", kind="evidence")
        with contextlib.suppress(OSError):
            os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        self.descriptor = descriptor
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.descriptor is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


class CognitiveDailyReviewRenderer:
    """Deterministic and recoverable renderer for one Vault."""

    def __init__(
        self,
        vault: Path,
        *,
        state_root: Path | None = None,
        fault_hook: Callable[[str], None] | None = None,
    ) -> None:
        original = vault.expanduser().absolute()
        if original.is_symlink():
            raise ContractError("Vault 路径不得是符号链接", kind="evidence")
        try:
            resolved = original.resolve(strict=True)
        except OSError as exc:
            raise ContractError("Vault 目录不存在", kind="not_found") from exc
        info = resolved.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise ContractError("Vault 必须是当前用户目录", kind="evidence")
        self.vault = resolved
        root = state_root or (resolved / ".context-agent" / "cognitive-secretary-v1" / "daily-review-projection")
        if not root.is_absolute():
            root = resolved / root
        root = root.absolute()
        try:
            root.relative_to(resolved)
        except ValueError as exc:
            raise ContractError("Daily Review state_root 必须位于 Vault 内", kind="evidence") from exc
        self.root = root
        self.staging_dir = root / "staging"
        self.journal_dir = root / "journals"
        self.recovery_dir = resolved / "Reviews" / ".recovery" / "CognitiveDaily"
        self.review_dir = resolved / "Reviews" / "Daily"
        self.lock_path = root / "projection.lock"
        self._fault_hook = fault_hook

    # --------------------------------------------------------------
    # Filesystem safety
    # --------------------------------------------------------------
    def _fault(self, stage: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(stage)

    def _secure_directory(self, path: Path, *, private: bool = True) -> None:
        try:
            relative = path.absolute().relative_to(self.vault)
        except ValueError as exc:
            raise ContractError("目录越过 Vault 边界", kind="evidence") from exc
        current = self.vault
        for part in relative.parts:
            current = current / part
            effective_private = private and current != self.review_dir.parent
            forbidden_mode = 0o077 if effective_private else 0o022
            if current.exists() or current.is_symlink():
                info = current.lstat()
                if (
                    stat.S_ISLNK(info.st_mode)
                    or not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or (info.st_mode & forbidden_mode)
                ):
                    raise ContractError(f"运行目录不安全：{current.name}", kind="evidence")
            else:
                current.mkdir(mode=0o700)
            if current.lstat().st_mode & forbidden_mode:
                raise ContractError(f"运行目录权限不私有：{current.name}", kind="evidence")

    def _ensure_layout(self) -> None:
        for path in (
            self.root.parent.parent,
            self.root.parent,
            self.root,
            self.staging_dir,
            self.journal_dir,
            self.recovery_dir.parent,
            self.recovery_dir,
        ):
            self._secure_directory(path)
        # Existing Memento Vaults commonly expose ``Reviews`` as a readable
        # navigation directory.  It must still be owned by the user and never
        # group/world writable; Review files themselves remain mode 0600.
        self._secure_directory(self.review_dir.parent, private=False)
        self._secure_directory(self.review_dir, private=False)

    def _safe_read(self, path: Path, *, name: str, maximum: int = MAX_REVIEW_BYTES) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError as exc:
            raise ContractError(f"{name} 不存在", kind="not_found") from exc
        except OSError as exc:
            kind = "evidence" if exc.errno in {errno.ELOOP, errno.EISDIR} else "runtime"
            raise ContractError(f"{name} 无法安全读取", kind=kind) from exc
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or before.st_mode & 0o077
                or before.st_size > maximum
            ):
                raise ContractError(f"{name} 必须是当前用户的私有单链接普通文件", kind="evidence")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum:
                    raise ContractError(f"{name} 超过允许大小", kind="evidence")
            after = os.fstat(descriptor)
            stable = ("st_dev", "st_ino", "st_uid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(before, key) != getattr(after, key) for key in stable):
                raise ContractError(f"{name} 在读取期间变化", kind="stale")
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def _safe_write_new(self, path: Path, payload: bytes) -> None:
        self._secure_directory(path.parent)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise ContractError(f"拒绝覆盖暂存文件：{path.name}", kind="conflict") from exc
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("文件写入失败")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._fsync_directory(path.parent)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _fsync_file(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _renamex(source: Path, destination: Path, flags: int) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        if hasattr(libc, "renamex_np"):
            function = libc.renamex_np
            function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            function.restype = ctypes.c_int
            if function(os.fsencode(source), os.fsencode(destination), flags) != 0:
                number = ctypes.get_errno()
                raise OSError(number, os.strerror(number), str(source), str(destination))
            return
        if hasattr(libc, "renameat2"):
            # Linux renameat2 uses the same numeric value (2) for EXCHANGE but
            # NOREPLACE is 1 rather than macOS RENAME_EXCL (4).
            linux_flags = 2 if flags == 2 else 1
            function = libc.renameat2
            function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            function.restype = ctypes.c_int
            if function(-100, os.fsencode(source), -100, os.fsencode(destination), linux_flags) != 0:
                number = ctypes.get_errno()
                raise OSError(number, os.strerror(number), str(source), str(destination))
            return
        raise ContractError("当前系统缺少安全原子 rename 能力", kind="runtime")

    # --------------------------------------------------------------
    # Input and Markdown contracts
    # --------------------------------------------------------------
    def _validate_inputs(
        self,
        summary: DailySummaryRevision,
        summary_ref: ObjectRef,
        sources: Sequence[SourceRecordRevision],
        receipts: Sequence[InterpretationReceiptRevision],
    ) -> tuple[tuple[SourceRecordRevision, ...], tuple[InterpretationReceiptRevision, ...]]:
        if summary.status != "active":
            raise ContractError("只能投影 active Daily Summary", kind="evidence")
        if summary_ref != ObjectRef("daily_summary", summary.summary_id, summary.revision, summary.sha256):
            raise ContractError("Daily Summary 未与已提交 ref 精确绑定", kind="evidence")
        expected_file = f"Reviews/Daily/{summary.local_date}.md"
        if summary.review_file != expected_file:
            raise ContractError("Daily Review 路径不符合日期合同", kind="evidence")

        source_by_key: dict[tuple[str, int, str], SourceRecordRevision] = {}
        for source in sources:
            if source.status != "active" or source.local_date != summary.local_date:
                raise ContractError("Daily Review source 非当日 active revision", kind="evidence")
            key = (source.record_id, source.revision, source.sha256)
            if key in source_by_key:
                raise ContractError("Daily Review source 重复", kind="evidence")
            source_by_key[key] = source
        expected_sources = {(ref.id, ref.revision, ref.revision_sha256) for ref in summary.source_refs}
        if len(expected_sources) != len(summary.source_refs) or set(source_by_key) != expected_sources:
            raise ContractError("Daily Review source refs 与已提交摘要不一致", kind="evidence")

        receipt_by_key: dict[tuple[str, int, str], InterpretationReceiptRevision] = {}
        receipt_record_ids: set[str] = set()
        source_ref_by_id = {ref.id: ref for ref in summary.source_refs}
        for receipt in receipts:
            key = (receipt.receipt_id, receipt.revision, receipt.sha256)
            if key in receipt_by_key or receipt.record_ref.id in receipt_record_ids:
                raise ContractError("Daily Review receipt 重复", kind="evidence")
            source_ref = source_ref_by_id.get(receipt.record_ref.id)
            if source_ref is None or receipt.record_ref != source_ref:
                raise ContractError("Daily Review receipt 未绑定当日 source", kind="evidence")
            if receipt.status == "tombstone":
                raise ContractError("Daily Review 不得投影 tombstone receipt", kind="evidence")
            receipt_by_key[key] = receipt
            receipt_record_ids.add(receipt.record_ref.id)
        expected_receipts = {(ref.id, ref.revision, ref.revision_sha256) for ref in summary.receipt_refs}
        if len(expected_receipts) != len(summary.receipt_refs) or set(receipt_by_key) != expected_receipts:
            raise ContractError("Daily Review receipt refs 与已提交摘要不一致", kind="evidence")

        ordered_sources = tuple(sorted(sources, key=lambda row: (row.captured_at, row.record_id)))
        ordered_receipts = tuple(sorted(receipts, key=lambda row: (row.record_ref.id, row.receipt_id)))
        return ordered_sources, ordered_receipts

    def _render_bytes(
        self,
        summary: DailySummaryRevision,
        sources: Sequence[SourceRecordRevision],
        receipts: Sequence[InterpretationReceiptRevision],
        supplement: bytes,
    ) -> bytes:
        source_manifest = _manifest_sha(summary.source_refs)
        receipt_manifest = _manifest_sha(summary.receipt_refs)
        lines = [
            "---",
            f"date: {summary.local_date}",
            "type: memento-cognitive-review",
            "period: daily",
            f"projection: {PROJECTION_VERSION}",
            f"summary_id: {summary.summary_id}",
            f'source: "[[{summary.local_date}]]"',
            f'source_manifest_sha256: "{source_manifest}"',
            f'receipt_manifest_sha256: "{receipt_manifest}"',
            "---",
            "",
            f"# Daily Review · {summary.local_date}",
            "",
            "## 今日概览",
            "",
            _safe_inline(summary.overview),
            "",
            "## 今日主题",
            "",
            *_list_markdown(summary.themes),
            "",
            "## 发生的变化",
            "",
            *_list_markdown(summary.changes),
            "",
            "## 尚未解决",
            "",
            *_list_markdown(summary.unresolved_questions),
            "",
            "## 行动线索",
            "",
            *_list_markdown(summary.action_clues),
            "",
            "## 整理索引",
            "",
        ]
        receipt_by_record = {receipt.record_ref.id: receipt for receipt in receipts}
        if not sources:
            lines.append("无")
        for source in sources:
            receipt = receipt_by_record.get(source.record_id)
            if receipt is None:
                result = "仅保留原文，未形成整理回执"
                receipt_label = "无"
            elif receipt.status == "original_only":
                result = "仅保留原文"
                receipt_label = f"{receipt.receipt_id}@r{receipt.revision}"
            else:
                result = _safe_inline(receipt.summary or "无")
                receipt_label = f"{receipt.receipt_id}@r{receipt.revision}"
            label = SOURCE_TYPE_LABELS.get(source.source_type, source.source_type)
            lines.extend(
                [
                    f"- {_timestamp_label(source.captured_at)} · {_safe_inline(label)} · {result}",
                    f"  - 原文: `{source.record_id}@r{source.revision}` · 回执: `{receipt_label}` · [[{summary.local_date}]]",
                ]
            )
        lines.extend(["", SUPPLEMENT_HEADING])
        prefix = ("\n".join(lines) + "\n").encode("utf-8")
        candidate = prefix + supplement
        tail, _ = _extract_supplement(candidate, summary.local_date)
        if tail != supplement:
            raise ContractError("Daily Review 用户补充字节未完整保留", kind="evidence")
        return candidate

    # --------------------------------------------------------------
    # Journal and recovery
    # --------------------------------------------------------------
    def _journal_value(
        self,
        *,
        local_date: str,
        review_file: str,
        candidate_name: str,
        candidate_sha256: str,
        expected_review_sha256: str | None,
        user_supplement_sha256: str | None,
    ) -> dict[str, Any]:
        identity: dict[str, Any] = {
            "schema_version": "1.0",
            "kind": "memento_daily_review_projection_transaction",
            "local_date": local_date,
            "review_file": review_file,
            "candidate_name": candidate_name,
            "candidate_sha256": candidate_sha256,
            "expected_review_sha256": expected_review_sha256,
            "user_supplement_sha256": user_supplement_sha256,
        }
        identity["transaction_id"] = "rvtx_" + _sha(canonical_json(identity).encode("utf-8"))[:24]
        return identity

    def _validate_journal(self, value: Any, path: Path | None = None) -> dict[str, Any]:
        if not isinstance(value, dict) or frozenset(value) != JOURNAL_FIELDS:
            raise ContractError("Daily Review journal 字段无效", kind="evidence")
        if value["schema_version"] != "1.0" or value["kind"] != "memento_daily_review_projection_transaction":
            raise ContractError("Daily Review journal schema/kind 无效", kind="evidence")
        local_date = value["local_date"]
        if not isinstance(local_date, str) or not DATE_RE.fullmatch(local_date):
            raise ContractError("Daily Review journal 日期无效", kind="evidence")
        if value["review_file"] != f"Reviews/Daily/{local_date}.md":
            raise ContractError("Daily Review journal 路径无效", kind="evidence")
        if not isinstance(value["candidate_name"], str) or not re.fullmatch(r"\.candidate\.[0-9a-f]{24}\.md", value["candidate_name"]):
            raise ContractError("Daily Review journal candidate 无效", kind="evidence")
        for key in ("candidate_sha256",):
            if not isinstance(value[key], str) or not SHA_RE.fullmatch(value[key]):
                raise ContractError(f"Daily Review journal {key} 无效", kind="evidence")
        for key in ("expected_review_sha256", "user_supplement_sha256"):
            if value[key] is not None and (not isinstance(value[key], str) or not SHA_RE.fullmatch(value[key])):
                raise ContractError(f"Daily Review journal {key} 无效", kind="evidence")
        tx_id = value["transaction_id"]
        if not isinstance(tx_id, str) or not TX_RE.fullmatch(tx_id):
            raise ContractError("Daily Review transaction_id 无效", kind="evidence")
        identity = {key: value[key] for key in JOURNAL_FIELDS if key != "transaction_id"}
        expected_id = "rvtx_" + _sha(canonical_json(identity).encode("utf-8"))[:24]
        if tx_id != expected_id or (path is not None and path.name != f"{tx_id}.json"):
            raise ContractError("Daily Review journal 身份不一致", kind="evidence")
        return dict(value)

    def _load_journal(self, path: Path) -> dict[str, Any]:
        raw = self._safe_read(path, name="Daily Review journal", maximum=MAX_JOURNAL_BYTES)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("Daily Review journal 无法解析", kind="evidence") from exc
        return self._validate_journal(value, path)

    def _archive_previous(self, candidate: Path, journal: Mapping[str, Any]) -> None:
        destination = self.recovery_dir / f"{journal['local_date']}.previous.{journal['transaction_id']}.md"
        if destination.exists() or destination.is_symlink():
            if destination.is_symlink():
                raise ContractError("Daily Review 恢复路径不安全", kind="evidence")
            existing = self._safe_read(destination, name="Daily Review recovery")
            old = self._safe_read(candidate, name="Daily Review previous")
            if existing != old:
                raise ContractError("Daily Review 恢复副本冲突", kind="conflict")
            candidate.unlink()
        else:
            self._renamex(candidate, destination, 4)
        self._fsync_directory(self.recovery_dir)

    def _publish_journal(self, journal: Mapping[str, Any], *, recovering: bool = False) -> None:
        journal = self._validate_journal(journal)
        target = self.vault / journal["review_file"]
        candidate = self.staging_dir / journal["candidate_name"]
        expected = journal["expected_review_sha256"]
        candidate_hash = journal["candidate_sha256"]
        journal_path = self.journal_dir / f"{journal['transaction_id']}.json"

        target_exists = target.exists() or target.is_symlink()
        if target_exists:
            target_bytes = self._safe_read(target, name="Daily Review")
            target_hash = _sha(target_bytes)
        else:
            target_hash = None
        candidate_exists = candidate.exists() or candidate.is_symlink()

        if target_hash == candidate_hash:
            # Publish already happened.  After an exchange, candidate is the
            # previous Review; after first creation it is absent.
            if candidate_exists:
                old = self._safe_read(candidate, name="Daily Review previous")
                if expected is None or _sha(old) != expected:
                    raise ContractError("Daily Review 交换后旧版本无法核对", kind="conflict")
                self._archive_previous(candidate, journal)
            with contextlib.suppress(FileNotFoundError):
                journal_path.unlink()
            self._fsync_directory(self.journal_dir)
            return

        if not candidate_exists:
            raise ContractError("Daily Review journal 缺少候选文件", kind="evidence")
        candidate_bytes = self._safe_read(candidate, name="Daily Review candidate")
        if _sha(candidate_bytes) != candidate_hash:
            raise ContractError("Daily Review candidate hash 不一致", kind="evidence")
        _extract_supplement(candidate_bytes, journal["local_date"])

        if expected is None:
            if target_exists:
                raise ContractError("Daily Review 生成起点为空，但正式文件已出现", kind="stale")
            self._renamex(candidate, target, 4)
        else:
            if target_hash != expected:
                raise ContractError("Daily Review 在生成期间发生变化", kind="stale")
            if not recovering:
                self._fault("before_publish")
                # A user/editor does not take our lock.  Re-read immediately
                # before the atomic exchange and fail closed on any change.
                if _sha(self._safe_read(target, name="Daily Review")) != expected:
                    raise ContractError("Daily Review 在提交点发生变化", kind="stale")
            self._renamex(candidate, target, 2)
        self._fsync_file(target)
        self._fsync_directory(target.parent)
        if not recovering:
            self._fault("after_publish")

        if expected is not None:
            old = self._safe_read(candidate, name="Daily Review previous")
            if _sha(old) != expected:
                raise ContractError("Daily Review 原子交换结果无法核对", kind="conflict")
            self._archive_previous(candidate, journal)
        final = self._safe_read(target, name="Daily Review")
        if _sha(final) != candidate_hash:
            raise ContractError("Daily Review 提交后 hash 不一致", kind="conflict")
        with contextlib.suppress(FileNotFoundError):
            journal_path.unlink()
        self._fsync_directory(self.journal_dir)

    def _recover_locked(self) -> int:
        self._ensure_layout()
        recovered = 0
        for path in sorted(self.journal_dir.iterdir(), key=lambda row: row.name):
            if path.is_symlink() or not path.is_file():
                raise ContractError("Daily Review journal 目录含不安全条目", kind="evidence")
            journal = self._load_journal(path)
            self._publish_journal(journal, recovering=True)
            recovered += 1
        referenced = {
            journal["candidate_name"]
            for journal in (
                self._load_journal(path)
                for path in sorted(self.journal_dir.iterdir(), key=lambda row: row.name)
                if path.is_file() and not path.is_symlink()
            )
        }
        for path in sorted(self.staging_dir.iterdir(), key=lambda row: row.name):
            if path.name in referenced:
                continue
            if path.is_symlink() or not path.is_file():
                raise ContractError("Daily Review staging 含不安全条目", kind="evidence")
            payload = self._safe_read(path, name="Daily Review orphan candidate")
            destination = self.recovery_dir / f"orphan.{_sha(payload)[:24]}.md"
            if destination.exists() or destination.is_symlink():
                if destination.is_symlink() or self._safe_read(destination, name="Daily Review orphan recovery") != payload:
                    raise ContractError("Daily Review orphan 恢复副本冲突", kind="conflict")
                path.unlink()
            else:
                self._renamex(path, destination, 4)
        return recovered

    def recover(self) -> int:
        """Complete durable pending publishes without model or summary access."""

        with _ProjectionLock(self):
            return self._recover_locked()

    # --------------------------------------------------------------
    # Public render
    # --------------------------------------------------------------
    def render(
        self,
        *,
        summary: DailySummaryRevision,
        summary_ref: ObjectRef,
        sources: Sequence[SourceRecordRevision],
        receipts: Sequence[InterpretationReceiptRevision],
    ) -> DailyReviewProjectionResult:
        ordered_sources, ordered_receipts = self._validate_inputs(summary, summary_ref, sources, receipts)
        with _ProjectionLock(self):
            recovered = self._recover_locked()
            target = self.vault / summary.review_file
            if target.exists() or target.is_symlink():
                existing = self._safe_read(target, name="Daily Review")
                supplement, _ = _extract_supplement(existing, summary.local_date)
                expected_hash: str | None = _sha(existing)
            else:
                existing = None
                supplement = DEFAULT_SUPPLEMENT
                expected_hash = None

            candidate = self._render_bytes(summary, ordered_sources, ordered_receipts, supplement)
            candidate_hash = _sha(candidate)
            supplement_hash = _supplement_sha(supplement)
            if existing == candidate:
                status = "recovered" if recovered else "unchanged"
                return DailyReviewProjectionResult(
                    status,
                    summary_ref,
                    summary.review_file,
                    candidate_hash,
                    supplement_hash,
                )

            candidate_name = f".candidate.{candidate_hash[:24]}.md"
            candidate_path = self.staging_dir / candidate_name
            self._safe_write_new(candidate_path, candidate)
            journal = self._journal_value(
                local_date=summary.local_date,
                review_file=summary.review_file,
                candidate_name=candidate_name,
                candidate_sha256=candidate_hash,
                expected_review_sha256=expected_hash,
                user_supplement_sha256=supplement_hash,
            )
            journal_path = self.journal_dir / f"{journal['transaction_id']}.json"
            self._safe_write_new(journal_path, _json_bytes(journal))
            self._fault("after_journal")
            self._publish_journal(journal)
            status = "created" if expected_hash is None else "updated"
            return DailyReviewProjectionResult(
                status,
                summary_ref,
                summary.review_file,
                candidate_hash,
                supplement_hash,
            )


__all__ = [
    "CognitiveDailyReviewRenderer",
    "DailyReviewProjectionResult",
    "PROJECTION_VERSION",
]
