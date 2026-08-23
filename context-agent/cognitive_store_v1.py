"""Secure local source-record index for the cognitive secretary MVP.

The store is deliberately limited to deterministic ingest/reconciliation.  It
never calls a model and never writes the user's Markdown or attachments.

Identity and content are separate:

* a newly captured record may arrive with a preallocated ``rec_<24hex>``;
* legacy records receive a versioned locator-v1 ID once and then reuse it;
* ``entry_sha256`` hashes the exact record-block bytes, not the day file;
* append/front insertion only refreshes the mutable locator projection;
* a uniquely matched body edit appends an immutable source revision;
* ambiguous matches fail closed and are persisted as ``needs_review`` issues.

Immutable revisions are published through a recoverable staging transaction.
Readers use ``record-index.json`` as a rebuildable head projection and verify
every referenced revision byte hash and revision chain before returning it.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import errno
import fcntl
import json
import mimetypes
import os
import re
import stat
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from core import ContractError, canonical_json, sha256_bytes


SCHEMA_VERSION = "1.0"
LOCATOR_VERSION = "locator-v1"
RECORD_KIND = "memento_source_record_revision"
INDEX_KIND = "memento_source_record_index"
ISSUE_KIND = "memento_source_reconcile_issues"
TRANSACTION_KIND = "memento_source_record_transaction"

RECORD_ID_RE = re.compile(r"^rec_[0-9a-f]{24}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DAILY_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
HEADING_RE = re.compile(r"^##[ \t]+(\d{2}):(\d{2})(?:[ \t]*·[ \t]*(.*))?$")
WEEKDAY_RE = re.compile(r"^周[一二三四五六日天]$")
KNOWN_TAGS = frozenset({"TODO", "灵感", "下次再读"})
LOCAL_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
NOTE_RE = re.compile(r"^>\s*备注[:：]\s*(.*)$")
SOURCE_META_RE = re.compile(r"^>\s*来源[:：]\s*(.*)$")
BODY_TAG_RE = re.compile(r"(?:^|\s)#(TODO|灵感|下次再读)(?:\s|$)")

MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024
NEARBY_LINE_DISTANCE = 96

SOURCE_TYPES = frozenset(
    {"text", "screenshot_ocr", "voice_transcript", "image_note", "file_note"}
)
STATUSES = frozenset({"active", "tombstone"})
OPERATIONS = frozenset({"ingest", "source_edit", "user_delete"})
INGEST_ORIGINS = frozenset({"capture_service", "reconciler", "legacy_import"})

ATTACHMENT_FIELDS = frozenset({"path", "mime_type", "byte_size", "sha256"})
REVISION_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "record_id",
        "revision",
        "status",
        "operation",
        "created_at",
        "captured_at",
        "local_date",
        "source_type",
        "source_app",
        "source_file",
        "line_start",
        "line_end",
        "entry_sha256",
        "source_snapshot_sha256",
        "attachments",
        "ingest_origin",
        "previous_revision_sha256",
    }
)
INDEX_ENTRY_FIELDS = frozenset(
    {
        "record_id",
        "status",
        "current_revision",
        "revision_sha256",
        "source_file",
        "locator_version",
        "original_occurrence_ordinal",
        "line_start",
        "line_end",
        "byte_start",
        "byte_end",
        "entry_sha256",
        "source_snapshot_sha256",
        "heading_sha256",
        "time",
        "weekday",
        "source_app",
        "tag",
        "note_sha256",
        "attachment_paths",
    }
)
INDEX_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "index_revision",
        "generated_at",
        "records",
    }
)
ISSUE_FIELDS = frozenset(
    {"code", "source_file", "line_start", "line_end", "record_ids", "detail"}
)
ISSUE_REPORT_FIELDS = frozenset(
    {"schema_version", "kind", "source_file", "source_snapshot_sha256", "created_at", "issues"}
)
TRANSACTION_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "transaction_id",
        "created_at",
        "source_file",
        "source_snapshot_sha256",
        "revisions",
        "target_index",
        "issue_report",
    }
)


def _object(value: Any, fields: frozenset[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{name} 必须是 JSON object")
    actual = frozenset(value)
    if actual != fields:
        missing = sorted(fields - actual)
        extra = sorted(actual - fields)
        raise ContractError(f"{name} 字段不符合合同；缺失={missing}；未知={extra}")
    return value


def _text(value: Any, name: str, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ContractError(f"{name} 必须是字符串")
    if len(value) > maximum:
        raise ContractError(f"{name} 超过 {maximum} 个字符")
    if not allow_empty and not value.strip():
        raise ContractError(f"{name} 不能为空")
    return value


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ContractError(f"{name} 必须是 SHA-256")
    return value


def _record_id(value: Any, name: str = "record_id") -> str:
    if not isinstance(value, str) or not RECORD_ID_RE.fullmatch(value):
        raise ContractError(f"{name} 无效")
    return value


def _timestamp(value: Any, name: str) -> str:
    text = _text(value, name, maximum=64)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{name} 必须是带时区的 RFC 3339 时间") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{name} 必须带时区")
    return text


def _date(value: Any, name: str) -> str:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise ContractError(f"{name} 必须是 YYYY-MM-DD")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"{name} 不是有效日期") from exc
    return value


def _relative_path(value: Any, name: str, *, root_daily: bool = False) -> str:
    text = _text(value, name, maximum=1024)
    if "\\" in text or text.startswith("/"):
        raise ContractError(f"{name} 必须是 POSIX 相对路径", kind="evidence")
    normalized = unicodedata.normalize("NFC", text)
    path = PurePosixPath(normalized)
    if normalized != text or normalized != path.as_posix():
        raise ContractError(f"{name} 必须是 NFC 标准路径", kind="evidence")
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ContractError(f"{name} 不得越过 Vault 边界", kind="evidence")
    if root_daily and (len(path.parts) != 1 or not DAILY_FILE_RE.fullmatch(path.name)):
        raise ContractError("source_file 必须是 Vault 根目录的 YYYY-MM-DD.md", kind="evidence")
    return normalized


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


@dataclass(frozen=True)
class ParsedAttachment:
    path: str
    mime_type: str
    byte_size: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "mime_type": self.mime_type,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class ParsedRecord:
    source_file: str
    local_date: str
    ordinal: int
    line_start: int
    line_end: int
    byte_start: int
    byte_end: int
    raw_block: bytes = field(repr=False)
    entry_sha256: str
    heading: str
    heading_sha256: str
    time: str
    weekday: str | None
    source_app: str
    tag: str | None
    note: str | None
    source_type: str
    attachments: tuple[ParsedAttachment, ...]

    @property
    def locator_key(self) -> str:
        payload = {
            "source_file": self.source_file,
            "byte_start": self.byte_start,
            "entry_sha256": self.entry_sha256,
        }
        return "loc_" + sha256_bytes(canonical_json(payload).encode("utf-8"))[:24]

    @property
    def attachment_paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.attachments)

    @property
    def note_sha256(self) -> str | None:
        if self.note is None:
            return None
        return sha256_bytes(self.note.encode("utf-8"))

    @property
    def identity_fingerprint(self) -> tuple[Any, ...]:
        return (self.heading_sha256, self.time, self.source_app, self.attachment_paths)


@dataclass(frozen=True)
class ReconcileIssue:
    code: str
    source_file: str
    line_start: int
    line_end: int
    record_ids: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "source_file": self.source_file,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "record_ids": list(self.record_ids),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ParseResult:
    source_file: str
    source_snapshot_sha256: str
    records: tuple[ParsedRecord, ...]
    issues: tuple[ReconcileIssue, ...]


@dataclass(frozen=True)
class ReconcileResult:
    source_file: str
    source_snapshot_sha256: str
    parsed_count: int
    created_record_ids: tuple[str, ...] = ()
    revised_record_ids: tuple[str, ...] = ()
    tombstoned_record_ids: tuple[str, ...] = ()
    unchanged_record_ids: tuple[str, ...] = ()
    refreshed_locator_record_ids: tuple[str, ...] = ()
    needs_review: tuple[ReconcileIssue, ...] = ()
    index_revision: int = 0

    @property
    def changed(self) -> bool:
        return bool(
            self.created_record_ids
            or self.revised_record_ids
            or self.tombstoned_record_ids
            or self.refreshed_locator_record_ids
        )


def _split_lines_with_offsets(content: bytes) -> list[tuple[int, int, bytes]]:
    lines: list[tuple[int, int, bytes]] = []
    offset = 0
    for raw in content.splitlines(keepends=True):
        end = offset + len(raw)
        lines.append((offset, end, raw))
        offset = end
    if offset < len(content):
        lines.append((offset, len(content), content[offset:]))
    return lines


def _line_text(raw: bytes, name: str) -> str:
    try:
        return raw.rstrip(b"\r\n").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(f"{name} 不是有效 UTF-8", kind="evidence") from exc


def _heading_metadata(heading: str) -> tuple[str, str | None, str, str | None]:
    match = HEADING_RE.fullmatch(heading)
    if match is None:
        raise ContractError("记录 heading 无效", kind="evidence")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ContractError("记录 heading 时间无效", kind="evidence")
    parts = [part.strip() for part in (match.group(3) or "").split("·") if part.strip()]
    weekday: str | None = None
    tag: str | None = None
    sources: list[str] = []
    for part in parts:
        if weekday is None and WEEKDAY_RE.fullmatch(part):
            weekday = part
        elif part.startswith("#") and part[1:] in KNOWN_TAGS and tag is None:
            tag = part[1:]
        else:
            sources.append(part)
    source_app = " · ".join(sources) if sources else "Memento"
    return f"{hour:02d}:{minute:02d}", weekday, source_app, tag


def _extract_note_and_metadata(body_lines: Sequence[str]) -> tuple[str | None, str | None, str | None]:
    note_parts: list[str] = []
    source_meta: str | None = None
    tag: str | None = None
    collecting_note = False
    for line in body_lines:
        note_match = NOTE_RE.match(line)
        if note_match:
            collecting_note = True
            note_parts.append(note_match.group(1).strip())
            continue
        if collecting_note and line.startswith("> "):
            note_parts.append(line[2:].rstrip())
            continue
        collecting_note = False
        source_match = SOURCE_META_RE.match(line)
        if source_match and source_meta is None:
            source_meta = source_match.group(1).strip() or None
        if tag is None:
            body_tag = BODY_TAG_RE.search(line)
            if body_tag:
                tag = body_tag.group(1)
    note = "\n".join(note_parts).strip() or None
    return note, source_meta, tag


def _classify_source(source_app: str, attachment_paths: Sequence[str]) -> str:
    lowered = source_app.casefold()
    if "截图" in source_app and "ocr" in lowered:
        return "screenshot_ocr"
    if "语音" in source_app:
        return "voice_transcript"
    if "每日第一帧" in source_app or "截图" in source_app:
        return "image_note"
    if attachment_paths:
        image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".tif", ".tiff", ".bmp"}
        if all(PurePosixPath(path).suffix.casefold() in image_extensions for path in attachment_paths):
            return "image_note"
        if any(PurePosixPath(path).suffix.casefold() not in {".m4a", ".mp3", ".wav", ".aac"} for path in attachment_paths):
            return "file_note"
    return "text"


def _local_attachment_paths(body_lines: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for line in body_lines:
        for match in LOCAL_LINK_RE.finditer(line):
            raw = match.group(1).strip()
            if not raw or "://" in raw or raw.startswith("#"):
                continue
            # Markdown may append a quoted title after the path.
            if ' "' in raw:
                raw = raw.split(' "', 1)[0]
            if raw.startswith("./"):
                raw = raw[2:]
            try:
                normalized = _relative_path(raw, "attachment.path")
            except ContractError:
                raise
            if not normalized.startswith("assets/"):
                continue
            if normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
    return result


def _attachment_from_bytes(path: str, content: bytes) -> ParsedAttachment:
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return ParsedAttachment(
        path=path,
        mime_type=mime,
        byte_size=len(content),
        sha256=sha256_bytes(content),
    )


def parse_daily_markdown(
    content: bytes,
    source_file: str,
    *,
    attachment_loader: Any | None = None,
) -> ParseResult:
    """Parse complete ``## HH:MM ...`` records without normalizing bytes.

    ``entry_sha256`` is the SHA-256 of the exact byte slice from the heading
    through the terminating ``---`` line, including that line's newline when
    present.  Leading blank lines outside the record are excluded.
    """

    source_file = _relative_path(source_file, "source_file", root_daily=True)
    local_date = DAILY_FILE_RE.fullmatch(source_file).group(1)  # type: ignore[union-attr]
    _date(local_date, "local_date")
    if len(content) > MAX_SOURCE_BYTES:
        raise ContractError("日级 Markdown 超过允许大小", kind="evidence")
    if b"\x00" in content:
        raise ContractError("日级 Markdown 不得包含 NUL", kind="evidence")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("日级 Markdown 不是有效 UTF-8", kind="evidence") from exc

    lines = _split_lines_with_offsets(content)
    heading_indexes: list[int] = []
    for index, (_, _, raw) in enumerate(lines):
        text = _line_text(raw, f"source line {index + 1}")
        if HEADING_RE.fullmatch(text):
            heading_indexes.append(index)

    records: list[ParsedRecord] = []
    issues: list[ReconcileIssue] = []
    snapshot_sha = sha256_bytes(content)
    for ordinal, heading_index in enumerate(heading_indexes, start=1):
        next_heading = heading_indexes[ordinal] if ordinal < len(heading_indexes) else len(lines)
        delimiter_index: int | None = None
        for candidate in range(heading_index + 1, next_heading):
            if _line_text(lines[candidate][2], f"source line {candidate + 1}").strip() == "---":
                delimiter_index = candidate
                break
        if delimiter_index is None:
            issues.append(
                ReconcileIssue(
                    code="missing_delimiter",
                    source_file=source_file,
                    line_start=heading_index + 1,
                    line_end=max(heading_index + 1, next_heading),
                    detail="记录块缺少终止 ---，未建立或修改侧车索引",
                )
            )
            continue
        byte_start = lines[heading_index][0]
        byte_end = lines[delimiter_index][1]
        raw_block = content[byte_start:byte_end]
        heading = _line_text(lines[heading_index][2], f"source line {heading_index + 1}")
        try:
            time_value, weekday, source_app, tag = _heading_metadata(heading)
        except ContractError:
            issues.append(
                ReconcileIssue(
                    code="invalid_heading",
                    source_file=source_file,
                    line_start=heading_index + 1,
                    line_end=delimiter_index + 1,
                    detail="记录 heading 无法安全解析",
                )
            )
            continue
        body_lines = [
            _line_text(lines[index][2], f"source line {index + 1}")
            for index in range(heading_index + 1, delimiter_index)
        ]
        note, source_meta, body_tag = _extract_note_and_metadata(body_lines)
        if source_meta and source_app == "语音":
            source_app = source_meta
        if tag is None:
            tag = body_tag
        try:
            paths = _local_attachment_paths(body_lines)
            attachments: list[ParsedAttachment] = []
            for path in paths:
                if attachment_loader is None:
                    raise ContractError(f"无法校验附件：{path}", kind="evidence")
                attachments.append(_attachment_from_bytes(path, attachment_loader(path)))
        except ContractError as exc:
            issues.append(
                ReconcileIssue(
                    code="attachment_unavailable",
                    source_file=source_file,
                    line_start=heading_index + 1,
                    line_end=delimiter_index + 1,
                    detail=str(exc),
                )
            )
            continue
        source_type = _classify_source(source_app, paths)
        # The heading may carry the product-level source while the source type
        # still needs to preserve the native capture class.
        if "语音" in heading:
            source_type = "voice_transcript"
        elif "截图·OCR" in heading or "截图 · OCR" in heading:
            source_type = "screenshot_ocr"
        elif "每日第一帧" in heading or ("截图" in heading and source_type != "screenshot_ocr"):
            source_type = "image_note"
        records.append(
            ParsedRecord(
                source_file=source_file,
                local_date=local_date,
                ordinal=ordinal,
                line_start=heading_index + 1,
                line_end=delimiter_index + 1,
                byte_start=byte_start,
                byte_end=byte_end,
                raw_block=raw_block,
                entry_sha256=sha256_bytes(raw_block),
                heading=heading,
                heading_sha256=sha256_bytes(heading.encode("utf-8")),
                time=time_value,
                weekday=weekday,
                source_app=source_app,
                tag=tag,
                note=note,
                source_type=source_type,
                attachments=tuple(attachments),
            )
        )
    return ParseResult(
        source_file=source_file,
        source_snapshot_sha256=snapshot_sha,
        records=tuple(records),
        issues=tuple(issues),
    )


def make_legacy_record_id(record: ParsedRecord) -> str:
    payload = {
        "locator_version": LOCATOR_VERSION,
        "source_file": record.source_file,
        "entry_sha256": record.entry_sha256,
        "heading": record.heading,
        "time": record.time,
        "source_app": record.source_app,
        "attachment_paths": list(record.attachment_paths),
        "original_occurrence_ordinal": record.ordinal,
    }
    return "rec_" + sha256_bytes(canonical_json(payload).encode("utf-8"))[:24]


def validate_source_record_revision(value: Any) -> dict[str, Any]:
    item = _object(value, REVISION_FIELDS, "source record revision")
    if item["schema_version"] != SCHEMA_VERSION or item["kind"] != RECORD_KIND:
        raise ContractError("source record revision schema/kind 无效")
    _record_id(item["record_id"])
    revision = item["revision"]
    if type(revision) is not int or not 1 <= revision <= 999_999:
        raise ContractError("revision 必须是 1..999999 整数")
    if item["status"] not in STATUSES or item["operation"] not in OPERATIONS:
        raise ContractError("source record status/operation 无效")
    if (item["operation"] == "user_delete") != (item["status"] == "tombstone"):
        raise ContractError("user_delete 与 tombstone 必须同时出现")
    if revision == 1:
        if item["previous_revision_sha256"] is not None:
            raise ContractError("revision 1 的 previous_revision_sha256 必须为 null")
        if item["operation"] != "ingest":
            raise ContractError("revision 1 必须是 ingest")
    else:
        _sha(item["previous_revision_sha256"], "previous_revision_sha256")
        if item["operation"] == "ingest":
            raise ContractError("revision > 1 不能是 ingest")
    _timestamp(item["created_at"], "created_at")
    _timestamp(item["captured_at"], "captured_at")
    local_date = _date(item["local_date"], "local_date")
    source_file = _relative_path(item["source_file"], "source_file", root_daily=True)
    if not source_file.startswith(local_date):
        raise ContractError("source_file 与 local_date 不一致")
    if item["source_type"] not in SOURCE_TYPES:
        raise ContractError("source_type 无效")
    _text(item["source_app"], "source_app", maximum=200)
    for name in ("line_start", "line_end"):
        if type(item[name]) is not int or item[name] < 1:
            raise ContractError(f"{name} 必须是正整数")
    if item["line_end"] < item["line_start"]:
        raise ContractError("line_end 不得早于 line_start")
    _sha(item["entry_sha256"], "entry_sha256")
    _sha(item["source_snapshot_sha256"], "source_snapshot_sha256")
    if not isinstance(item["attachments"], list) or len(item["attachments"]) > 128:
        raise ContractError("attachments 必须是最多 128 项的 array")
    seen: set[str] = set()
    for index, raw in enumerate(item["attachments"]):
        attachment = _object(raw, ATTACHMENT_FIELDS, f"attachments[{index}]")
        path = _relative_path(attachment["path"], f"attachments[{index}].path")
        if not path.startswith("assets/") or path in seen:
            raise ContractError("附件必须是唯一的 assets/ 相对路径")
        seen.add(path)
        _text(attachment["mime_type"], f"attachments[{index}].mime_type", maximum=128)
        if type(attachment["byte_size"]) is not int or attachment["byte_size"] < 0:
            raise ContractError("attachment.byte_size 必须是非负整数")
        _sha(attachment["sha256"], f"attachments[{index}].sha256")
    if item["ingest_origin"] not in INGEST_ORIGINS:
        raise ContractError("ingest_origin 无效")
    return dict(item)


def _validate_index_entry(value: Any) -> dict[str, Any]:
    item = _object(value, INDEX_ENTRY_FIELDS, "record index entry")
    _record_id(item["record_id"])
    if item["status"] not in STATUSES:
        raise ContractError("index.status 无效")
    if type(item["current_revision"]) is not int or item["current_revision"] < 1:
        raise ContractError("index.current_revision 无效")
    _sha(item["revision_sha256"], "index.revision_sha256")
    _relative_path(item["source_file"], "index.source_file", root_daily=True)
    if item["locator_version"] != LOCATOR_VERSION:
        raise ContractError("index.locator_version 无效")
    if type(item["original_occurrence_ordinal"]) is not int or item["original_occurrence_ordinal"] < 1:
        raise ContractError("original_occurrence_ordinal 无效")
    for name in ("line_start", "line_end", "byte_start", "byte_end"):
        if type(item[name]) is not int or item[name] < (1 if name.startswith("line_") else 0):
            raise ContractError(f"index.{name} 无效")
    if item["line_end"] < item["line_start"] or item["byte_end"] < item["byte_start"]:
        raise ContractError("index locator 范围无效")
    for name in ("entry_sha256", "source_snapshot_sha256", "heading_sha256"):
        _sha(item[name], f"index.{name}")
    _text(item["time"], "index.time", maximum=5)
    if item["weekday"] is not None:
        _text(item["weekday"], "index.weekday", maximum=16)
    _text(item["source_app"], "index.source_app", maximum=200)
    if item["tag"] is not None and item["tag"] not in KNOWN_TAGS:
        raise ContractError("index.tag 无效")
    if item["note_sha256"] is not None:
        _sha(item["note_sha256"], "index.note_sha256")
    if not isinstance(item["attachment_paths"], list) or len(item["attachment_paths"]) > 128:
        raise ContractError("index.attachment_paths 无效")
    paths = [_relative_path(path, "index.attachment_path") for path in item["attachment_paths"]]
    if paths != sorted(set(paths)):
        raise ContractError("index.attachment_paths 必须唯一且有序")
    return dict(item)


def validate_record_index(value: Any) -> dict[str, Any]:
    item = _object(value, INDEX_FIELDS, "record index")
    if item["schema_version"] != SCHEMA_VERSION or item["kind"] != INDEX_KIND:
        raise ContractError("record index schema/kind 无效")
    if type(item["index_revision"]) is not int or item["index_revision"] < 0:
        raise ContractError("index_revision 无效")
    _timestamp(item["generated_at"], "index.generated_at")
    if not isinstance(item["records"], list) or len(item["records"]) > 100_000:
        raise ContractError("index.records 无效")
    records = [_validate_index_entry(raw) for raw in item["records"]]
    ids = [record["record_id"] for record in records]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ContractError("index.records 必须按 record_id 唯一排序")
    return dict(item)


def _validate_issue(value: Any) -> dict[str, Any]:
    item = _object(value, ISSUE_FIELDS, "reconcile issue")
    _text(item["code"], "issue.code", maximum=80)
    _relative_path(item["source_file"], "issue.source_file", root_daily=True)
    for name in ("line_start", "line_end"):
        if type(item[name]) is not int or item[name] < 1:
            raise ContractError(f"issue.{name} 无效")
    if item["line_end"] < item["line_start"]:
        raise ContractError("issue line range 无效")
    if not isinstance(item["record_ids"], list):
        raise ContractError("issue.record_ids 必须是 array")
    ids = [_record_id(record_id, "issue.record_id") for record_id in item["record_ids"]]
    if ids != sorted(set(ids)):
        raise ContractError("issue.record_ids 必须唯一且有序")
    _text(item["detail"], "issue.detail", maximum=1000, allow_empty=True)
    return dict(item)


def validate_issue_report(value: Any) -> dict[str, Any]:
    item = _object(value, ISSUE_REPORT_FIELDS, "issue report")
    if item["schema_version"] != SCHEMA_VERSION or item["kind"] != ISSUE_KIND:
        raise ContractError("issue report schema/kind 无效")
    _relative_path(item["source_file"], "issue.source_file", root_daily=True)
    _sha(item["source_snapshot_sha256"], "issue.source_snapshot_sha256")
    _timestamp(item["created_at"], "issue.created_at")
    if not isinstance(item["issues"], list) or len(item["issues"]) > 10_000:
        raise ContractError("issue.issues 无效")
    [_validate_issue(raw) for raw in item["issues"]]
    return dict(item)


def _captured_at(local_date: str, time_value: str, timezone: dt.tzinfo) -> str:
    hour, minute = (int(part) for part in time_value.split(":"))
    value = dt.datetime.combine(dt.date.fromisoformat(local_date), dt.time(hour, minute), timezone)
    return value.isoformat(timespec="seconds")


def _now_text(now: dt.datetime | None) -> str:
    value = now or dt.datetime.now().astimezone()
    if value.tzinfo is None:
        raise ContractError("now 必须带时区")
    return value.isoformat(timespec="seconds")


class _StoreLock:
    def __init__(self, store: "RecordStore") -> None:
        self.store = store
        self.descriptor: int | None = None

    def __enter__(self) -> "_StoreLock":
        self.store._ensure_layout()
        path = self.store.locks_dir / "records.lock"
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise ContractError("记录索引锁无法安全打开", kind="evidence") from exc
        locked = False
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_nlink != 1
                or details.st_uid != os.getuid()
                or stat.S_IMODE(details.st_mode) & 0o077
            ):
                raise ContractError(
                    "记录索引锁必须是当前用户的 owner-only 单链接普通文件",
                    kind="evidence",
                )
            try:
                current = os.stat(path, follow_symlinks=False)
            except OSError as exc:
                raise ContractError("记录索引锁无法校验", kind="evidence") from exc
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or current.st_uid != os.getuid()
                or stat.S_IMODE(current.st_mode) & 0o077
                or current.st_dev != details.st_dev
                or current.st_ino != details.st_ino
            ):
                raise ContractError("记录索引锁在打开期间变化", kind="evidence")

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
            locked_details = os.fstat(descriptor)
            try:
                locked_path = os.stat(path, follow_symlinks=False)
            except OSError as exc:
                raise ContractError("记录索引锁在等待期间变化", kind="evidence") from exc
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
                raise ContractError("记录索引锁在等待期间变化", kind="evidence")
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
            with contextlib.suppress(OSError):
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


class RecordStore:
    """Pure/local record ingestor and reconciler.

    The class writes only beneath ``.context-agent/cognitive-secretary-v1``.
    ``source_file`` is restricted to a daily Markdown file at the Vault root.
    """

    def __init__(self, vault: Path, *, state_root: Path | None = None) -> None:
        resolved = vault.expanduser().resolve()
        if not resolved.is_dir():
            raise ContractError(f"Vault 目录不存在：{resolved}", kind="not_found")
        self.vault = resolved
        root = state_root or (resolved / ".context-agent" / "cognitive-secretary-v1")
        if not root.is_absolute():
            root = resolved / root
        root_parent = root.parent.resolve()
        candidate = root_parent / root.name
        try:
            candidate.relative_to(resolved)
        except ValueError as exc:
            raise ContractError("state_root 必须位于 Vault 内", kind="evidence") from exc
        self.root = candidate
        self.records_dir = self.root / "records"
        self.locks_dir = self.root / "locks"
        self.staging_dir = self.root / "staging" / "record-ingest"
        self.committed_dir = self.root / "committed" / "record-ingest"
        self.quarantine_dir = self.root / "quarantine" / "record-ingest"
        self.issues_dir = self.root / "reconcile-issues"
        self.index_path = self.root / "record-index.json"

    def _ensure_layout(self) -> None:
        for path in (
            self.root.parent,
            self.root,
            self.records_dir,
            self.locks_dir,
            self.staging_dir,
            self.committed_dir,
            self.quarantine_dir,
            self.issues_dir,
        ):
            self._secure_directory(path)

    def _secure_directory(self, path: Path) -> None:
        try:
            path.relative_to(self.vault)
        except ValueError as exc:
            raise ContractError("运行目录越过 Vault 边界", kind="evidence") from exc
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ContractError(f"运行路径不安全：{path}", kind="evidence")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        details = path.lstat()
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.getuid()
        ):
            raise ContractError(f"运行路径不是安全目录：{path}", kind="evidence")
        with contextlib.suppress(OSError):
            path.chmod(0o700)

    def _safe_read_bytes(self, path: Path, *, maximum: int, name: str) -> bytes:
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError as exc:
            raise ContractError(f"{name} 不存在：{path.name}", kind="not_found") from exc
        except OSError as exc:
            kind = "evidence" if exc.errno in {errno.ELOOP, errno.EISDIR} else "runtime"
            raise ContractError(f"{name} 无法安全读取", kind=kind) from exc
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.getuid()
                or before.st_size > maximum
            ):
                raise ContractError(f"{name} 必须是当前用户的单链接普通文件", kind="evidence")
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
            if any(getattr(before, field) != getattr(after, field) for field in stable):
                raise ContractError(f"{name} 在读取期间发生变化", kind="stale")
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def _safe_read_json(self, path: Path, *, name: str) -> dict[str, Any]:
        content = self._safe_read_bytes(path, maximum=MAX_JSON_BYTES, name=name)
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError(f"{name} JSON 无法解析", kind="schema") from exc
        if not isinstance(value, dict):
            raise ContractError(f"{name} 必须是 JSON object")
        return value

    def _safe_source(self, source_file: str) -> bytes:
        normalized = _relative_path(source_file, "source_file", root_daily=True)
        path = self.vault / normalized
        return self._safe_read_bytes(path, maximum=MAX_SOURCE_BYTES, name="日级 Markdown")

    def _load_attachment(self, relative_path: str) -> bytes:
        normalized = _relative_path(relative_path, "attachment.path")
        parts = PurePosixPath(normalized).parts
        if len(parts) != 2 or parts[0] != "assets":
            raise ContractError("附件必须是 assets/ 目录下的单层文件", kind="evidence")
        assets = self.vault / "assets"
        if assets.is_symlink() or not assets.is_dir():
            raise ContractError("assets 目录不安全", kind="evidence")
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(assets, directory_flags)
        descriptor: int | None = None
        try:
            directory_details = os.fstat(directory_fd)
            if (
                not stat.S_ISDIR(directory_details.st_mode)
                or directory_details.st_uid != os.getuid()
            ):
                raise ContractError("assets 目录不安全", kind="evidence")
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            try:
                descriptor = os.open(parts[1], flags, dir_fd=directory_fd)
            except OSError as exc:
                raise ContractError(f"无法安全读取附件：{normalized}", kind="evidence") from exc
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.getuid()
                or before.st_size > MAX_SOURCE_BYTES
            ):
                raise ContractError("附件必须是当前用户的单链接普通文件", kind="evidence")
            content = bytearray()
            while len(content) <= MAX_SOURCE_BYTES:
                chunk = os.read(descriptor, min(1024 * 1024, MAX_SOURCE_BYTES + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
            after = os.fstat(descriptor)
            stable = ("st_dev", "st_ino", "st_uid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            if len(content) > MAX_SOURCE_BYTES or any(getattr(before, field) != getattr(after, field) for field in stable):
                raise ContractError("附件在读取期间发生变化", kind="stale")
            return bytes(content)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory_fd)

    def parse_day(self, source_file: str) -> ParseResult:
        content = self._safe_source(source_file)
        return parse_daily_markdown(content, source_file, attachment_loader=self._load_attachment)

    def _empty_index(self, now_text: str) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": INDEX_KIND,
            "index_revision": 0,
            "generated_at": now_text,
            "records": [],
        }

    def _load_index(self, now_text: str) -> dict[str, Any]:
        if self.index_path.is_symlink():
            raise ContractError("record-index.json 不能是符号链接", kind="evidence")
        if not self.index_path.exists():
            return self._empty_index(now_text)
        index = validate_record_index(self._safe_read_json(self.index_path, name="record index"))
        for entry in index["records"]:
            revision, revision_sha = self._load_revision_with_sha(
                entry["record_id"], entry["current_revision"]
            )
            if revision_sha != entry["revision_sha256"]:
                raise ContractError("record index 引用的 revision hash 不一致", kind="evidence")
            if revision["status"] != entry["status"] or revision["entry_sha256"] != entry["entry_sha256"]:
                raise ContractError("record index 与 revision head 不一致", kind="evidence")
            self._validate_chain(entry["record_id"], entry["current_revision"])
        return index

    def _revision_path(self, record_id: str, revision: int) -> Path:
        _record_id(record_id)
        if type(revision) is not int or not 1 <= revision <= 999_999:
            raise ContractError("revision 无效")
        return self.records_dir / f"{record_id}.r{revision:06d}.json"

    def _load_revision_with_sha(self, record_id: str, revision: int) -> tuple[dict[str, Any], str]:
        path = self._revision_path(record_id, revision)
        content = self._safe_read_bytes(
            path,
            maximum=MAX_JSON_BYTES,
            name="source record revision",
        )
        try:
            raw = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("source record revision JSON 无法解析", kind="schema") from exc
        item = validate_source_record_revision(raw)
        if item["record_id"] != record_id or item["revision"] != revision:
            raise ContractError("revision 文件名与内容不一致", kind="evidence")
        return item, sha256_bytes(content)

    def _load_revision(self, record_id: str, revision: int) -> dict[str, Any]:
        return self._load_revision_with_sha(record_id, revision)[0]

    def _validate_chain(self, record_id: str, head_revision: int) -> None:
        previous_sha: str | None = None
        previous_item: dict[str, Any] | None = None
        for revision_number in range(1, head_revision + 1):
            item, revision_sha = self._load_revision_with_sha(record_id, revision_number)
            if item["previous_revision_sha256"] != previous_sha:
                raise ContractError("source record revision 链不连续", kind="evidence")
            if previous_item is not None:
                if previous_item["status"] == "tombstone":
                    raise ContractError("tombstone 之后不得追加 source revision", kind="evidence")
                stable_fields = ("record_id", "captured_at", "local_date", "source_file")
                if any(item[name] != previous_item[name] for name in stable_fields):
                    raise ContractError("source record revision 身份字段发生了变化", kind="evidence")
            previous_sha = revision_sha
            previous_item = item

    def revision_file_sha(self, record_id: str, revision: int) -> str:
        """Return the SHA-256 of the persisted pretty JSON file bytes."""

        with _StoreLock(self):
            self._recover_staging()
            _, revision_sha = self._load_revision_with_sha(record_id, revision)
            return revision_sha

    def load_head(self, record_id: str) -> dict[str, Any]:
        with _StoreLock(self):
            self._recover_staging()
            index = self._load_index(_now_text(None))
            entry = next((entry for entry in index["records"] if entry["record_id"] == record_id), None)
            if entry is None:
                raise ContractError("记录不存在", kind="not_found")
            return self._load_revision(record_id, entry["current_revision"])

    def load_head_ref(self, record_id: str) -> dict[str, Any]:
        """Return the validated current SourceRecord ObjectRef."""

        _record_id(record_id)
        with _StoreLock(self):
            self._recover_staging()
            index = self._load_index(_now_text(None))
            entry = next(
                (item for item in index["records"] if item["record_id"] == record_id),
                None,
            )
            if entry is None:
                raise ContractError("记录不存在", kind="not_found")
            return {
                "kind": "source_record",
                "id": record_id,
                "revision": entry["current_revision"],
                "revision_sha256": entry["revision_sha256"],
            }

    def load_chain(self, record_id: str) -> list[dict[str, Any]]:
        with _StoreLock(self):
            self._recover_staging()
            index = self._load_index(_now_text(None))
            entry = next((entry for entry in index["records"] if entry["record_id"] == record_id), None)
            if entry is None:
                raise ContractError("记录不存在", kind="not_found")
            self._validate_chain(record_id, entry["current_revision"])
            return [self._load_revision(record_id, revision) for revision in range(1, entry["current_revision"] + 1)]

    def list_heads(
        self,
        *,
        local_date: str | None = None,
        include_tombstones: bool = False,
    ) -> list[dict[str, Any]]:
        """Return validated current record revisions in capture order.

        This is the public enumeration boundary for workers and projectors.  It
        deliberately reads the validated index under the store lock instead of
        asking callers to inspect ``record-index.json`` directly.
        """

        if local_date is not None:
            _date(local_date, "local_date")
        with _StoreLock(self):
            self._recover_staging()
            index = self._load_index(_now_text(None))
            heads: list[dict[str, Any]] = []
            for entry in index["records"]:
                if not include_tombstones and entry["status"] == "tombstone":
                    continue
                head = self._load_revision(entry["record_id"], entry["current_revision"])
                if local_date is not None and head["local_date"] != local_date:
                    continue
                heads.append(head)
            return sorted(heads, key=lambda row: (row["captured_at"], row["record_id"]))

    def list_head_refs(
        self,
        *,
        local_date: str | None = None,
        include_tombstones: bool = False,
    ) -> list[dict[str, Any]]:
        """Return ObjectRefs for the same validated heads as :meth:`list_heads`."""

        if local_date is not None:
            _date(local_date, "local_date")
        with _StoreLock(self):
            self._recover_staging()
            index = self._load_index(_now_text(None))
            refs: list[tuple[str, dict[str, Any]]] = []
            for entry in index["records"]:
                if not include_tombstones and entry["status"] == "tombstone":
                    continue
                head, revision_sha = self._load_revision_with_sha(
                    entry["record_id"], entry["current_revision"]
                )
                if local_date is not None and head["local_date"] != local_date:
                    continue
                refs.append(
                    (
                        head["captured_at"],
                        {
                            "kind": "source_record",
                            "id": head["record_id"],
                            "revision": head["revision"],
                            "revision_sha256": revision_sha,
                        },
                    )
                )
            return [row for _, row in sorted(refs, key=lambda pair: (pair[0], pair[1]["id"]))]

    def _index_entry(
        self,
        record: ParsedRecord,
        revision: Mapping[str, Any],
        revision_sha: str,
        original_ordinal: int,
        *,
        source_snapshot_sha256: str | None = None,
    ) -> dict[str, Any]:
        return {
            "record_id": revision["record_id"],
            "status": revision["status"],
            "current_revision": revision["revision"],
            "revision_sha256": revision_sha,
            "source_file": record.source_file,
            "locator_version": LOCATOR_VERSION,
            "original_occurrence_ordinal": original_ordinal,
            "line_start": record.line_start,
            "line_end": record.line_end,
            "byte_start": record.byte_start,
            "byte_end": record.byte_end,
            "entry_sha256": record.entry_sha256,
            "source_snapshot_sha256": source_snapshot_sha256 or revision["source_snapshot_sha256"],
            "heading_sha256": record.heading_sha256,
            "time": record.time,
            "weekday": record.weekday,
            "source_app": record.source_app,
            "tag": record.tag,
            "note_sha256": record.note_sha256,
            "attachment_paths": sorted(record.attachment_paths),
        }

    def _revision_for_record(
        self,
        record: ParsedRecord,
        *,
        record_id: str,
        revision: int,
        operation: str,
        ingest_origin: str,
        snapshot_sha: str,
        created_at: str,
        previous_revision_sha256: str | None,
        timezone: dt.tzinfo,
    ) -> dict[str, Any]:
        value = {
            "schema_version": SCHEMA_VERSION,
            "kind": RECORD_KIND,
            "record_id": record_id,
            "revision": revision,
            "status": "active",
            "operation": operation,
            "created_at": created_at,
            "captured_at": _captured_at(record.local_date, record.time, timezone),
            "local_date": record.local_date,
            "source_type": record.source_type,
            "source_app": record.source_app,
            "source_file": record.source_file,
            "line_start": record.line_start,
            "line_end": record.line_end,
            "entry_sha256": record.entry_sha256,
            "source_snapshot_sha256": snapshot_sha,
            "attachments": [attachment.to_dict() for attachment in record.attachments],
            "ingest_origin": ingest_origin,
            "previous_revision_sha256": previous_revision_sha256,
        }
        return validate_source_record_revision(value)

    def _tombstone_revision(self, head: Mapping[str, Any], entry: Mapping[str, Any], *, snapshot_sha: str, created_at: str) -> dict[str, Any]:
        value = {
            **head,
            "revision": head["revision"] + 1,
            "status": "tombstone",
            "operation": "user_delete",
            "created_at": created_at,
            "source_snapshot_sha256": snapshot_sha,
            "ingest_origin": "reconciler",
            "previous_revision_sha256": entry["revision_sha256"],
        }
        return validate_source_record_revision(value)

    def _issue_path(self, source_file: str) -> Path:
        digest = sha256_bytes(source_file.encode("utf-8"))[:24]
        return self.issues_dir / f"day_{digest}.json"

    def _issue_report(self, parse: ParseResult, issues: Sequence[ReconcileIssue], created_at: str) -> dict[str, Any]:
        value = {
            "schema_version": SCHEMA_VERSION,
            "kind": ISSUE_KIND,
            "source_file": parse.source_file,
            "source_snapshot_sha256": parse.source_snapshot_sha256,
            "created_at": created_at,
            "issues": [issue.to_dict() for issue in issues],
        }
        return validate_issue_report(value)

    def _safe_write_replace(self, path: Path, value: Mapping[str, Any]) -> None:
        self._secure_directory(path.parent)
        if path.is_symlink():
            raise ContractError(f"拒绝覆盖符号链接：{path.name}", kind="evidence")
        if path.exists():
            details = path.lstat()
            if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1 or details.st_uid != os.getuid():
                raise ContractError(f"拒绝覆盖不安全文件：{path.name}", kind="evidence")
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            payload = _json_bytes(value)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()

    def _write_issue_report_if_changed(self, report: Mapping[str, Any]) -> None:
        path = self._issue_path(report["source_file"])
        if path.exists():
            current = validate_issue_report(self._safe_read_json(path, name="issue report"))
            comparable_current = {key: value for key, value in current.items() if key != "created_at"}
            comparable_new = {key: value for key, value in report.items() if key != "created_at"}
            if comparable_current == comparable_new:
                return
        self._safe_write_replace(path, report)

    def _safe_write_immutable(self, path: Path, value: Mapping[str, Any]) -> None:
        self._secure_directory(path.parent)
        payload = _json_bytes(value)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            existing = self._safe_read_bytes(path, maximum=MAX_JSON_BYTES, name="immutable JSON")
            if existing != payload:
                raise ContractError(f"拒绝覆盖已有不可变文件：{path.name}", kind="conflict")
            return
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("不可变 JSON 写入失败")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._fsync_directory(path.parent)

    def _fsync_directory(self, path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            with contextlib.suppress(OSError):
                os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _validate_transaction(self, value: Any) -> dict[str, Any]:
        item = _object(value, TRANSACTION_FIELDS, "record transaction")
        if item["schema_version"] != SCHEMA_VERSION or item["kind"] != TRANSACTION_KIND:
            raise ContractError("record transaction schema/kind 无效")
        expected_id = "tx_" + sha256_bytes(
            canonical_json(
                {
                    "source_file": item["source_file"],
                    "source_snapshot_sha256": item["source_snapshot_sha256"],
                    "revisions": item["revisions"],
                    "target_index": item["target_index"],
                    "issue_report": item["issue_report"],
                }
            ).encode("utf-8")
        )[:24]
        if item["transaction_id"] != expected_id:
            raise ContractError("transaction_id 与内容不一致", kind="evidence")
        _timestamp(item["created_at"], "transaction.created_at")
        _relative_path(item["source_file"], "transaction.source_file", root_daily=True)
        _sha(item["source_snapshot_sha256"], "transaction.source_snapshot_sha256")
        if not isinstance(item["revisions"], list) or len(item["revisions"]) > 10_000:
            raise ContractError("transaction.revisions 无效")
        revisions = [validate_source_record_revision(revision) for revision in item["revisions"]]
        revision_keys = [(revision["record_id"], revision["revision"]) for revision in revisions]
        if len(revision_keys) != len(set(revision_keys)):
            raise ContractError("transaction.revisions 不得重复")
        target_index = validate_record_index(item["target_index"])
        issue_report = validate_issue_report(item["issue_report"])
        if (
            issue_report["source_file"] != item["source_file"]
            or issue_report["source_snapshot_sha256"] != item["source_snapshot_sha256"]
        ):
            raise ContractError("transaction issue report 与 source snapshot 不一致")
        target_by_id = {entry["record_id"]: entry for entry in target_index["records"]}
        for revision in revisions:
            if (
                revision["source_file"] != item["source_file"]
                or revision["source_snapshot_sha256"] != item["source_snapshot_sha256"]
            ):
                raise ContractError("transaction revision 与 source snapshot 不一致")
            target = target_by_id.get(revision["record_id"])
            if (
                target is None
                or target["current_revision"] != revision["revision"]
                or target["revision_sha256"] != sha256_bytes(_json_bytes(revision))
            ):
                raise ContractError("transaction revision 与 target index 不一致")
        return dict(item)

    def _transaction_value(
        self,
        *,
        source_file: str,
        snapshot_sha: str,
        revisions: Sequence[Mapping[str, Any]],
        target_index: Mapping[str, Any],
        issue_report: Mapping[str, Any],
        created_at: str,
    ) -> dict[str, Any]:
        identity = {
            "source_file": source_file,
            "source_snapshot_sha256": snapshot_sha,
            "revisions": [dict(item) for item in revisions],
            "target_index": dict(target_index),
            "issue_report": dict(issue_report),
        }
        value = {
            "schema_version": SCHEMA_VERSION,
            "kind": TRANSACTION_KIND,
            "transaction_id": "tx_" + sha256_bytes(canonical_json(identity).encode("utf-8"))[:24],
            "created_at": created_at,
            **identity,
        }
        return self._validate_transaction(value)

    def _stage_and_commit(self, transaction: Mapping[str, Any]) -> None:
        tx_id = transaction["transaction_id"]
        stage = self.staging_dir / tx_id
        if stage.is_symlink() or (stage.exists() and not stage.is_dir()):
            raise ContractError("记录 staging 路径不安全", kind="evidence")
        self._secure_directory(stage)
        manifest_path = stage / "manifest.json"
        self._safe_write_immutable(manifest_path, transaction)
        self._commit_staged_transaction(stage, transaction)

    def _commit_staged_transaction(self, stage: Path, transaction: Mapping[str, Any]) -> None:
        transaction = self._validate_transaction(transaction)
        for revision in transaction["revisions"]:
            path = self._revision_path(revision["record_id"], revision["revision"])
            self._safe_write_immutable(path, revision)
        self._safe_write_replace(self.index_path, transaction["target_index"])
        issue_path = self._issue_path(transaction["source_file"])
        self._safe_write_replace(issue_path, transaction["issue_report"])
        destination = self.committed_dir / transaction["transaction_id"]
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                raise ContractError("committed transaction 路径不安全", kind="evidence")
            existing = self._safe_read_json(destination / "manifest.json", name="committed transaction")
            if existing != transaction:
                raise ContractError("committed transaction 内容冲突", kind="conflict")
            self._quarantine_directory(stage, "duplicate")
            return
        os.replace(stage, destination)
        self._fsync_directory(self.committed_dir)

    def _quarantine_directory(self, path: Path, reason: str) -> None:
        if not path.exists():
            return
        if path.is_symlink() or not path.is_dir():
            raise ContractError("staging 条目不安全", kind="evidence")
        suffix = sha256_bytes(f"{path.name}:{reason}:{os.stat(path).st_mtime_ns}".encode("utf-8"))[:12]
        destination = self.quarantine_dir / f"{path.name}.{reason}.{suffix}"
        os.replace(path, destination)
        self._fsync_directory(self.staging_dir)

    def _recover_staging(self) -> None:
        self._ensure_layout()
        for stage in sorted(self.staging_dir.iterdir(), key=lambda item: item.name):
            if stage.is_symlink() or not stage.is_dir():
                raise ContractError("staging 只能包含安全事务目录", kind="evidence")
            manifest = stage / "manifest.json"
            if not manifest.exists():
                self._quarantine_directory(stage, "incomplete")
                continue
            transaction = self._validate_transaction(self._safe_read_json(manifest, name="staged transaction"))
            if stage.name != transaction["transaction_id"]:
                raise ContractError("staging 目录名与 transaction_id 不一致", kind="evidence")
            self._commit_staged_transaction(stage, transaction)

    def reconcile_day(
        self,
        source_file: str,
        *,
        preallocated_record_ids: Mapping[str, str] | None = None,
        preallocated_record_id: str | None = None,
        now: dt.datetime | None = None,
        timezone: dt.tzinfo | None = None,
    ) -> ReconcileResult:
        """Reconcile one daily Markdown file into immutable sidecar revisions.

        Preallocated IDs may be keyed by a parsed record's ``locator_key`` or,
        when that hash is unique in the file, by ``entry_sha256``.  The singular
        ``preallocated_record_id`` is accepted only when exactly one new record
        remains after matching existing heads.
        """

        source_file = _relative_path(source_file, "source_file", root_daily=True)
        created_at = _now_text(now)
        zone = timezone or (now.tzinfo if now is not None else dt.datetime.now().astimezone().tzinfo)
        if zone is None:
            raise ContractError("本地时区不可用", kind="runtime")
        hints = dict(preallocated_record_ids or {})
        for key, value in hints.items():
            _text(key, "preallocated key", maximum=128)
            _record_id(value, "preallocated record_id")
        if preallocated_record_id is not None:
            _record_id(preallocated_record_id, "preallocated_record_id")

        with _StoreLock(self):
            self._recover_staging()
            index = self._load_index(created_at)
            source_bytes = self._safe_source(source_file)
            parsed = parse_daily_markdown(source_bytes, source_file, attachment_loader=self._load_attachment)
            current_entries = [entry for entry in index["records"] if entry["source_file"] == source_file]
            other_entries = [entry for entry in index["records"] if entry["source_file"] != source_file]

            if parsed.issues:
                report = self._issue_report(parsed, parsed.issues, created_at)
                self._write_issue_report_if_changed(report)
                return ReconcileResult(
                    source_file=source_file,
                    source_snapshot_sha256=parsed.source_snapshot_sha256,
                    parsed_count=len(parsed.records),
                    unchanged_record_ids=tuple(sorted(entry["record_id"] for entry in current_entries)),
                    needs_review=parsed.issues,
                    index_revision=index["index_revision"],
                )

            active_entries = [entry for entry in current_entries if entry["status"] == "active"]
            tombstone_entries = [entry for entry in current_entries if entry["status"] == "tombstone"]
            unmatched_old = {entry["record_id"]: entry for entry in active_entries}
            unmatched_new = {record.ordinal: record for record in parsed.records}
            matches: dict[str, ParsedRecord] = {}
            issues: list[ReconcileIssue] = []
            ambiguous_ids: set[str] = set()
            ambiguous_ordinals: set[int] = set()

            old_by_hash: dict[str, list[dict[str, Any]]] = {}
            new_by_hash: dict[str, list[ParsedRecord]] = {}
            for entry in active_entries:
                old_by_hash.setdefault(entry["entry_sha256"], []).append(entry)
            for record in parsed.records:
                new_by_hash.setdefault(record.entry_sha256, []).append(record)
            for digest in sorted(set(old_by_hash) & set(new_by_hash)):
                old_group, new_group = old_by_hash[digest], new_by_hash[digest]
                if len(old_group) == 1 and len(new_group) == 1:
                    entry, record = old_group[0], new_group[0]
                    matches[entry["record_id"]] = record
                    unmatched_old.pop(entry["record_id"], None)
                    unmatched_new.pop(record.ordinal, None)
                else:
                    record_ids = tuple(sorted(entry["record_id"] for entry in old_group))
                    line_start = min(record.line_start for record in new_group)
                    line_end = max(record.line_end for record in new_group)
                    ambiguous_ids.update(record_ids)
                    ambiguous_ordinals.update(record.ordinal for record in new_group)
                    issues.append(
                        ReconcileIssue(
                            code="duplicate_exact_match",
                            source_file=source_file,
                            line_start=line_start,
                            line_end=line_end,
                            record_ids=record_ids,
                            detail="完全重复的记录无法唯一匹配，已停在 needs_review",
                        )
                    )

            # A body edit may preserve the heading time/source/attachment
            # fingerprint.  It is accepted only when both sides are unique and
            # remain near the previous locator.
            old_by_fingerprint: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
            new_by_fingerprint: dict[tuple[Any, ...], list[ParsedRecord]] = {}
            for entry in unmatched_old.values():
                if entry["record_id"] in ambiguous_ids:
                    continue
                fingerprint = (
                    entry["heading_sha256"],
                    entry["time"],
                    entry["source_app"],
                    tuple(entry["attachment_paths"]),
                )
                old_by_fingerprint.setdefault(fingerprint, []).append(entry)
            for record in unmatched_new.values():
                if record.ordinal in ambiguous_ordinals:
                    continue
                new_by_fingerprint.setdefault(record.identity_fingerprint, []).append(record)
            for fingerprint in sorted(set(old_by_fingerprint) & set(new_by_fingerprint), key=repr):
                old_group, new_group = old_by_fingerprint[fingerprint], new_by_fingerprint[fingerprint]
                nearby = [
                    (old, new)
                    for old in old_group
                    for new in new_group
                    if abs(old["line_start"] - new.line_start) <= NEARBY_LINE_DISTANCE
                ]
                if len(old_group) == 1 and len(new_group) == 1 and len(nearby) == 1:
                    entry, record = nearby[0]
                    matches[entry["record_id"]] = record
                    unmatched_old.pop(entry["record_id"], None)
                    unmatched_new.pop(record.ordinal, None)
                else:
                    record_ids = tuple(sorted(entry["record_id"] for entry in old_group))
                    ambiguous_ids.update(record_ids)
                    ambiguous_ordinals.update(record.ordinal for record in new_group)
                    detail = (
                        "heading/time/source/attachment 匹配到多个候选，未创建新 revision"
                        if nearby
                        else "候选记录超出旧 locator 附近范围，已停在 needs_review"
                    )
                    issues.append(
                        ReconcileIssue(
                            code="ambiguous_source_edit" if nearby else "source_edit_outside_locator",
                            source_file=source_file,
                            line_start=min(record.line_start for record in new_group),
                            line_end=max(record.line_end for record in new_group),
                            record_ids=record_ids,
                            detail=detail,
                        )
                    )

            revisions: list[dict[str, Any]] = []
            target_entries: list[dict[str, Any]] = list(other_entries) + list(tombstone_entries)
            created_ids: list[str] = []
            revised_ids: list[str] = []
            tombstoned_ids: list[str] = []
            unchanged_ids: list[str] = []
            refreshed_ids: list[str] = []

            by_id = {entry["record_id"]: entry for entry in active_entries}
            for record_id, record in sorted(matches.items()):
                entry = by_id[record_id]
                head = self._load_revision(record_id, entry["current_revision"])
                current_attachments = [attachment.to_dict() for attachment in record.attachments]
                if (
                    entry["entry_sha256"] == record.entry_sha256
                    and head["attachments"] == current_attachments
                ):
                    new_entry = self._index_entry(
                        record,
                        head,
                        entry["revision_sha256"],
                        entry["original_occurrence_ordinal"],
                        source_snapshot_sha256=parsed.source_snapshot_sha256,
                    )
                    if new_entry != entry:
                        refreshed_ids.append(record_id)
                    else:
                        unchanged_ids.append(record_id)
                    target_entries.append(new_entry)
                else:
                    revision = self._revision_for_record(
                        record,
                        record_id=record_id,
                        revision=head["revision"] + 1,
                        operation="source_edit",
                        ingest_origin="reconciler",
                        snapshot_sha=parsed.source_snapshot_sha256,
                        created_at=created_at,
                        previous_revision_sha256=entry["revision_sha256"],
                        timezone=zone,
                    )
                    revision_sha = sha256_bytes(_json_bytes(revision))
                    revisions.append(revision)
                    target_entries.append(self._index_entry(record, revision, revision_sha, entry["original_occurrence_ordinal"]))
                    revised_ids.append(record_id)

            # Preserve every ambiguous old head unchanged.  The reconciler must
            # not silently turn ambiguity into deletion.
            for record_id in sorted(ambiguous_ids):
                entry = unmatched_old.pop(record_id, None)
                if entry is not None:
                    target_entries.append(entry)
                    unchanged_ids.append(record_id)

            # Unmatched old records are true deletions only after all unique
            # exact and metadata-preserving edit matches have been exhausted.
            for record_id, entry in sorted(unmatched_old.items()):
                head = self._load_revision(record_id, entry["current_revision"])
                revision = self._tombstone_revision(
                    head,
                    entry,
                    snapshot_sha=parsed.source_snapshot_sha256,
                    created_at=created_at,
                )
                revision_sha = sha256_bytes(_json_bytes(revision))
                revisions.append(revision)
                tombstone_entry = {**entry, "status": "tombstone", "current_revision": revision["revision"], "revision_sha256": revision_sha, "source_snapshot_sha256": parsed.source_snapshot_sha256}
                target_entries.append(_validate_index_entry(tombstone_entry))
                tombstoned_ids.append(record_id)

            candidates = [record for ordinal, record in sorted(unmatched_new.items()) if ordinal not in ambiguous_ordinals]
            if preallocated_record_id is not None and len(candidates) != 1:
                raise ContractError("preallocated_record_id 只能用于唯一新记录", kind="conflict")
            hash_counts: dict[str, int] = {}
            for record in parsed.records:
                hash_counts[record.entry_sha256] = hash_counts.get(record.entry_sha256, 0) + 1
            known_ids = {entry["record_id"] for entry in index["records"]}
            for record in candidates:
                hinted = hints.get(record.locator_key)
                if hinted is None and hash_counts[record.entry_sha256] == 1:
                    hinted = hints.get(record.entry_sha256)
                if hinted is None and preallocated_record_id is not None:
                    hinted = preallocated_record_id
                record_id = hinted or make_legacy_record_id(record)
                _record_id(record_id)
                if record_id in known_ids or record_id in created_ids:
                    raise ContractError("新记录的 record_id 已存在", kind="conflict")
                origin = "capture_service" if hinted is not None else "legacy_import"
                revision = self._revision_for_record(
                    record,
                    record_id=record_id,
                    revision=1,
                    operation="ingest",
                    ingest_origin=origin,
                    snapshot_sha=parsed.source_snapshot_sha256,
                    created_at=created_at,
                    previous_revision_sha256=None,
                    timezone=zone,
                )
                revision_sha = sha256_bytes(_json_bytes(revision))
                revisions.append(revision)
                target_entries.append(self._index_entry(record, revision, revision_sha, record.ordinal))
                created_ids.append(record_id)
                known_ids.add(record_id)

            target_entries = sorted(target_entries, key=lambda item: item["record_id"])
            index_changed = target_entries != index["records"]
            target_index = {
                "schema_version": SCHEMA_VERSION,
                "kind": INDEX_KIND,
                "index_revision": index["index_revision"] + (1 if index_changed else 0),
                "generated_at": created_at if index_changed else index["generated_at"],
                "records": target_entries,
            }
            validate_record_index(target_index)
            issue_report = self._issue_report(parsed, issues, created_at)

            # The capture writer does not share this lock.  Re-read immediately
            # before staging so no state is committed for an already changed
            # Markdown snapshot.
            if sha256_bytes(self._safe_source(source_file)) != parsed.source_snapshot_sha256:
                raise ContractError("日级 Markdown 在 reconcile 期间发生变化", kind="stale")
            attachment_snapshot: dict[str, ParsedAttachment] = {}
            for record in parsed.records:
                for attachment in record.attachments:
                    attachment_snapshot.setdefault(attachment.path, attachment)
            for path, expected in sorted(attachment_snapshot.items()):
                current = _attachment_from_bytes(path, self._load_attachment(path))
                if current != expected:
                    raise ContractError("附件在 reconcile 期间发生变化", kind="stale")

            if revisions or index_changed:
                transaction = self._transaction_value(
                    source_file=source_file,
                    snapshot_sha=parsed.source_snapshot_sha256,
                    revisions=revisions,
                    target_index=target_index,
                    issue_report=issue_report,
                    created_at=created_at,
                )
                self._stage_and_commit(transaction)
            else:
                self._write_issue_report_if_changed(issue_report)

            return ReconcileResult(
                source_file=source_file,
                source_snapshot_sha256=parsed.source_snapshot_sha256,
                parsed_count=len(parsed.records),
                created_record_ids=tuple(sorted(created_ids)),
                revised_record_ids=tuple(sorted(revised_ids)),
                tombstoned_record_ids=tuple(sorted(tombstoned_ids)),
                unchanged_record_ids=tuple(sorted(set(unchanged_ids))),
                refreshed_locator_record_ids=tuple(sorted(refreshed_ids)),
                needs_review=tuple(issues),
                index_revision=target_index["index_revision"],
            )


__all__ = [
    "LOCATOR_VERSION",
    "ParseResult",
    "ParsedAttachment",
    "ParsedRecord",
    "ReconcileIssue",
    "ReconcileResult",
    "RecordStore",
    "make_legacy_record_id",
    "parse_daily_markdown",
    "validate_record_index",
    "validate_source_record_revision",
]
