"""Read-only bridge from the installed Memento capture flow to test cases.

The installed macOS Services remain the sole capture surface.  This module
opens complete daily Markdown records after they have been durably committed,
copies referenced assets into an isolated owner-only workspace, and exports an
unlabelled dataset draft.  It never writes the source Vault or calls a model.
"""

from __future__ import annotations

import datetime as dt
import mimetypes
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Optional, Sequence, cast

from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import sha256_bytes, sha256_json
from memento_backend.storage.atomic import AtomicFileStore


DATASET_SCHEMA_VERSION = "1.0"
MAX_DAILY_BYTES = 32 * 1024 * 1024
MAX_ASSET_BYTES = 64 * 1024 * 1024
DAILY_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
HEADING_RE = re.compile(r"^##[ \t]+(\d{2}):(\d{2})(?:[ \t]*·[ \t]*(.*))?$")
WEEKDAY_RE = re.compile(r"^周[一二三四五六日天]$")
KNOWN_TAGS = frozenset({"TODO", "灵感", "下次再读"})
LOCAL_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
NOTE_RE = re.compile(r"^>\s*备注[:：]\s*(.*)$")
SOURCE_META_RE = re.compile(r"^>\s*来源[:：]\s*(.*)$")
BODY_TAG_RE = re.compile(r"(?:^|\s)#(TODO|灵感|下次再读)(?:\s|$)")
SESSION_ID_RE = re.compile(r"^rcs_[0-9a-f]{24}$")


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def _owner_only(mode: int, name: str) -> None:
    if stat.S_IMODE(mode) & 0o077:
        raise ContractError(f"{name} must be owner-only", kind="permission")


def secure_workspace(path: Path) -> Path:
    """Create or validate the isolated dataset workspace."""

    expanded = path.expanduser()
    if expanded.exists() or expanded.is_symlink():
        info = expanded.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ContractError("dataset workspace must be a real directory", kind="path")
        if info.st_uid != os.getuid():
            raise ContractError("dataset workspace has a foreign owner", kind="permission")
        _owner_only(info.st_mode, "dataset workspace")
    else:
        expanded.mkdir(mode=0o700, parents=True)
    return expanded.resolve(strict=True)


def _secure_source_root(path: Path) -> Path:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ContractError("capture source root does not exist", kind="path") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ContractError("capture source root must be a real directory", kind="path")
    if info.st_uid != os.getuid():
        raise ContractError("capture source root has a foreign owner", kind="permission")
    _owner_only(info.st_mode, "capture source root")
    resolved = path.resolve(strict=True)
    if resolved != path.absolute():
        raise ContractError("capture source root resolution changed", kind="path")
    return resolved


def _secure_read(path: Path, *, maximum: int, name: str) -> bytes:
    try:
        expected = path.lstat()
    except FileNotFoundError as exc:
        raise ContractError(f"{name} does not exist", kind="not_found") from exc
    if stat.S_ISLNK(expected.st_mode) or not stat.S_ISREG(expected.st_mode):
        raise ContractError(f"{name} must be a regular file", kind="path")
    if expected.st_uid != os.getuid() or expected.st_nlink != 1:
        raise ContractError(f"{name} ownership is unsafe", kind="permission")
    _owner_only(expected.st_mode, name)
    if expected.st_size > maximum:
        raise ContractError(f"{name} exceeds the read limit", kind="size")
    descriptor = os.open(
        str(path),
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise ContractError(f"{name} changed during open", kind="conflict")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        if len(value) > maximum:
            raise ContractError(f"{name} exceeds the read limit", kind="size")
        final = os.fstat(descriptor)
        stable_fields = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        final_fields = (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
            final.st_ctime_ns,
        )
        if final_fields != stable_fields:
            raise ContractError(f"{name} changed during read", kind="conflict")
        return value
    finally:
        os.close(descriptor)


def _split_lines(content: bytes) -> list[tuple[int, int, bytes]]:
    output: list[tuple[int, int, bytes]] = []
    offset = 0
    for raw in content.splitlines(keepends=True):
        end = offset + len(raw)
        output.append((offset, end, raw))
        offset = end
    if offset < len(content):
        output.append((offset, len(content), content[offset:]))
    return output


def _line_text(raw: bytes, name: str) -> str:
    try:
        return raw.rstrip(b"\r\n").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(f"{name} is not UTF-8", kind="evidence") from exc


def _heading_metadata(heading: str) -> tuple[str, Optional[str], str, Optional[str]]:
    match = HEADING_RE.fullmatch(heading)
    if match is None:
        raise ContractError("capture heading is invalid", kind="evidence")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ContractError("capture heading time is invalid", kind="evidence")
    weekday: Optional[str] = None
    tag: Optional[str] = None
    sources: list[str] = []
    for part in [item.strip() for item in (match.group(3) or "").split("·") if item.strip()]:
        if weekday is None and WEEKDAY_RE.fullmatch(part):
            weekday = part
        elif tag is None and part.startswith("#") and part[1:] in KNOWN_TAGS:
            tag = part[1:]
        else:
            sources.append(part)
    return f"{hour:02d}:{minute:02d}", weekday, " · ".join(sources) or "Memento", tag


def _note_and_metadata(lines: Sequence[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    note_parts: list[str] = []
    source_meta: Optional[str] = None
    tag: Optional[str] = None
    collecting_note = False
    for line in lines:
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
            body_match = BODY_TAG_RE.search(line)
            if body_match:
                tag = body_match.group(1)
    return "\n".join(note_parts).strip() or None, source_meta, tag


def _attachment_paths(lines: Sequence[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for line in lines:
        for match in LOCAL_LINK_RE.finditer(line):
            raw = match.group(1).strip()
            if not raw or "://" in raw or raw.startswith("#"):
                continue
            if ' "' in raw:
                raw = raw.split(' "', 1)[0]
            if raw.startswith("./"):
                raw = raw[2:]
            path = PurePosixPath(raw)
            if (
                len(path.parts) != 2
                or path.parts[0] != "assets"
                or path.parts[1] in {"", ".", ".."}
                or "\\" in raw
                or path.as_posix() != raw
            ):
                if raw.startswith("assets/"):
                    raise ContractError("attachment path escapes the assets boundary", kind="evidence")
                continue
            if raw not in seen:
                seen.add(raw)
                output.append(raw)
    return output


def _source_type(heading: str, source_app: str, attachment_paths: Sequence[str]) -> str:
    if "语音" in heading:
        return "voice_transcript"
    if "截图·OCR" in heading or "截图 · OCR" in heading:
        return "screenshot_ocr"
    if "截图" in heading:
        return "image_note"
    if attachment_paths:
        image_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".tif", ".tiff", ".bmp"}
        if all(PurePosixPath(path).suffix.casefold() in image_suffixes for path in attachment_paths):
            return "image_note"
        if "语音" in source_app:
            return "voice_transcript"
        return "file_note"
    return "text"


@dataclass(frozen=True)
class CaptureAttachment:
    source_path: str
    mime_type: str
    byte_size: int
    sha256: str
    data: bytes


@dataclass(frozen=True)
class CaptureRecord:
    source_file: str
    local_date: str
    ordinal: int
    line_start: int
    line_end: int
    raw_block: bytes
    entry_sha256: str
    source_snapshot_sha256: str
    time: str
    weekday: Optional[str]
    source_app: str
    tag: Optional[str]
    note: Optional[str]
    source_type: str
    attachment_paths: tuple[str, ...]

    @property
    def capture_key(self) -> str:
        return sha256_json(
            {
                "source_file": self.source_file,
                "ordinal": self.ordinal,
                "entry_sha256": self.entry_sha256,
            }
        )


@dataclass(frozen=True)
class VaultScan:
    records: tuple[CaptureRecord, ...]
    issues: tuple[Mapping[str, Any], ...]
    daily_file_count: int


class ReadOnlyCaptureVault:
    """Symlink-safe reader for records committed by the installed Services."""

    def __init__(self, root: Path) -> None:
        self.root = _secure_source_root(root.expanduser())

    def read_attachment(self, relative_path: str) -> CaptureAttachment:
        parts = PurePosixPath(relative_path).parts
        if len(parts) != 2 or parts[0] != "assets":
            raise ContractError("attachment path is outside assets", kind="evidence")
        assets = self.root / "assets"
        info = assets.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ContractError("assets must be a real directory", kind="path")
        if info.st_uid != os.getuid():
            raise ContractError("assets has a foreign owner", kind="permission")
        _owner_only(info.st_mode, "assets")
        path = assets / parts[1]
        data = _secure_read(path, maximum=MAX_ASSET_BYTES, name=f"attachment {relative_path}")
        return CaptureAttachment(
            source_path=relative_path,
            mime_type=mimetypes.guess_type(parts[1])[0] or "application/octet-stream",
            byte_size=len(data),
            sha256=sha256_bytes(data),
            data=data,
        )

    def _parse_daily(self, source_file: str, content: bytes) -> tuple[list[CaptureRecord], list[Mapping[str, Any]]]:
        match = DAILY_FILE_RE.fullmatch(source_file)
        if match is None:
            raise ContractError("daily source name is invalid", kind="evidence")
        try:
            dt.date.fromisoformat(match.group(1))
            content.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ContractError("daily source content is invalid", kind="evidence") from exc
        lines = _split_lines(content)
        headings = [
            index
            for index, (_, _, raw) in enumerate(lines)
            if HEADING_RE.fullmatch(_line_text(raw, f"{source_file}:{index + 1}"))
        ]
        records: list[CaptureRecord] = []
        issues: list[Mapping[str, Any]] = []
        snapshot_sha = sha256_bytes(content)
        for ordinal, heading_index in enumerate(headings, start=1):
            next_heading = headings[ordinal] if ordinal < len(headings) else len(lines)
            delimiter: Optional[int] = None
            for candidate in range(heading_index + 1, next_heading):
                if _line_text(lines[candidate][2], f"{source_file}:{candidate + 1}").strip() == "---":
                    delimiter = candidate
                    break
            if delimiter is None:
                issues.append(
                    {
                        "code": "missing_delimiter",
                        "source_file": source_file,
                        "line_start": heading_index + 1,
                        "detail": "incomplete record was not imported",
                    }
                )
                continue
            heading = _line_text(lines[heading_index][2], f"{source_file}:{heading_index + 1}")
            body_lines = [
                _line_text(lines[index][2], f"{source_file}:{index + 1}")
                for index in range(heading_index + 1, delimiter)
            ]
            try:
                time_value, weekday, source_app, tag = _heading_metadata(heading)
                note, source_meta, body_tag = _note_and_metadata(body_lines)
                if source_meta and source_app == "语音":
                    source_app = source_meta
                if tag is None:
                    tag = body_tag
                attachment_paths = _attachment_paths(body_lines)
            except (OSError, ContractError) as exc:
                issues.append(
                    {
                        "code": "record_unavailable",
                        "source_file": source_file,
                        "line_start": heading_index + 1,
                        "detail": str(exc),
                    }
                )
                continue
            byte_start = lines[heading_index][0]
            byte_end = lines[delimiter][1]
            raw_block = content[byte_start:byte_end]
            records.append(
                CaptureRecord(
                    source_file=source_file,
                    local_date=match.group(1),
                    ordinal=ordinal,
                    line_start=heading_index + 1,
                    line_end=delimiter + 1,
                    raw_block=raw_block,
                    entry_sha256=sha256_bytes(raw_block),
                    source_snapshot_sha256=snapshot_sha,
                    time=time_value,
                    weekday=weekday,
                    source_app=source_app,
                    tag=tag,
                    note=note,
                    source_type=_source_type(heading, source_app, attachment_paths),
                    attachment_paths=tuple(attachment_paths),
                )
            )
        return records, issues

    def scan(self, *, min_date: Optional[str] = None) -> VaultScan:
        if min_date is not None:
            try:
                min_date = dt.date.fromisoformat(min_date).isoformat()
            except ValueError as exc:
                raise ContractError("scan min_date is invalid", kind="schema") from exc
        records: list[CaptureRecord] = []
        issues: list[Mapping[str, Any]] = []
        daily_files = 0
        for child in sorted(self.root.iterdir(), key=lambda item: item.name):
            match = DAILY_FILE_RE.fullmatch(child.name)
            if match is None or (min_date is not None and match.group(1) < min_date):
                continue
            daily_files += 1
            try:
                content = _secure_read(child, maximum=MAX_DAILY_BYTES, name=f"daily source {child.name}")
                parsed, file_issues = self._parse_daily(child.name, content)
            except (OSError, ContractError) as exc:
                issues.append({"code": "daily_unavailable", "source_file": child.name, "detail": str(exc)})
                continue
            records.extend(parsed)
            issues.extend(file_issues)
        records.sort(key=lambda item: (item.local_date, item.ordinal, item.entry_sha256))
        return VaultScan(records=tuple(records), issues=tuple(issues), daily_file_count=daily_files)


def _string_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ContractError(f"{name} must be a string array", kind="schema")
    return cast(list[str], value)


class RealCaptureDatasetStore:
    """Session-based delta importer for the installed capture pipeline."""

    def __init__(self, workspace: Path) -> None:
        self.files = AtomicFileStore(workspace)
        for directory in ("assets", "captures", "exports", "sessions", "indexes"):
            self.files.ensure_directory(directory)

    @staticmethod
    def _index_path(session_id: str) -> str:
        if SESSION_ID_RE.fullmatch(session_id) is None:
            raise ContractError("session_id is invalid", kind="schema")
        return f"indexes/{session_id}.json"

    def current_session_id(self) -> str:
        pointer = self.files.read_json("indexes/current-session.json")
        value = pointer.get("session_id")
        if not isinstance(value, str) or SESSION_ID_RE.fullmatch(value) is None:
            raise ContractError("current session pointer is invalid", kind="schema")
        return value

    def _session(self, session_id: str) -> Mapping[str, Any]:
        if SESSION_ID_RE.fullmatch(session_id) is None:
            raise ContractError("session_id is invalid", kind="schema")
        session = self.files.read_json(f"sessions/{session_id}.json")
        if session.get("kind") != "memento_real_capture_session":
            raise ContractError("session manifest is invalid", kind="schema")
        return session

    def _write_asset(self, attachment: CaptureAttachment) -> Mapping[str, Any]:
        suffix = PurePosixPath(attachment.source_path).suffix.casefold()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
            suffix = ".blob"
        relative_path = f"assets/{attachment.sha256}{suffix}"
        if self.files.exists(relative_path):
            existing = self.files.read_bytes(relative_path, max_bytes=MAX_ASSET_BYTES)
            if sha256_bytes(existing) != attachment.sha256:
                raise ContractError("dataset asset digest mismatch", kind="conflict")
        else:
            self.files.write_new_bytes(relative_path, attachment.data)
        return {
            "source_path": attachment.source_path,
            "dataset_path": relative_path,
            "mime_type": attachment.mime_type,
            "byte_size": attachment.byte_size,
            "sha256": attachment.sha256,
        }

    def start_session(
        self,
        source_root: Path,
        *,
        source_label: str,
        include_existing_dates: Sequence[str] = (),
        capture_window_start_date: Optional[str] = None,
    ) -> Mapping[str, Any]:
        if not source_label.strip() or len(source_label) > 200:
            raise ContractError("source_label is invalid", kind="schema")
        include_dates: set[str] = set()
        for value in include_existing_dates:
            try:
                include_dates.add(dt.date.fromisoformat(value).isoformat())
            except ValueError as exc:
                raise ContractError("include_existing_dates contains an invalid date", kind="schema") from exc
        if capture_window_start_date is None:
            window_start = dt.datetime.now().astimezone().date().isoformat()
        else:
            try:
                window_start = dt.date.fromisoformat(capture_window_start_date).isoformat()
            except ValueError as exc:
                raise ContractError("capture_window_start_date is invalid", kind="schema") from exc
        scan_start = min(include_dates | {window_start})
        vault = ReadOnlyCaptureVault(source_root)
        scan = vault.scan(min_date=scan_start)
        session_id = "rcs_" + secrets.token_hex(12)
        ignored_keys = sorted(record.capture_key for record in scan.records if record.local_date not in include_dates)
        session = {
            "schema_version": DATASET_SCHEMA_VERSION,
            "kind": "memento_real_capture_session",
            "session_id": session_id,
            "started_at": _now(),
            "source": {
                "label": source_label.strip(),
                "root": str(vault.root),
                "root_sha256": sha256_bytes(str(vault.root).encode("utf-8")),
            },
            "include_existing_dates": sorted(include_dates),
            "capture_window_start_date": scan_start,
            "baseline_ignored_count": len(ignored_keys),
            "baseline_ignored_sha256": sha256_json({"capture_keys": ignored_keys}),
            "provider_enabled": False,
            "formal_vault_write_enabled": False,
        }
        index = {
            "schema_version": DATASET_SCHEMA_VERSION,
            "kind": "memento_real_capture_session_index",
            "session_id": session_id,
            "baseline_keys": ignored_keys,
            "collected_keys": [],
            "capture_ids": [],
            "last_collected_at": None,
            "last_scan_issues": list(scan.issues),
        }
        with self.files.lock("real-capture-dataset"):
            self.files.write_new_json(f"sessions/{session_id}.json", session)
            self.files.replace_json(self._index_path(session_id), index)
            self.files.replace_json(
                "indexes/current-session.json",
                {"schema_version": DATASET_SCHEMA_VERSION, "session_id": session_id},
            )
        collected = self.collect(session_id)
        return {"session": session, "collection": collected}

    def collect(self, session_id: Optional[str] = None) -> Mapping[str, Any]:
        selected = session_id or self.current_session_id()
        with self.files.lock("real-capture-dataset"):
            session = self._session(selected)
            source = session.get("source")
            if not isinstance(source, dict) or not isinstance(source.get("root"), str):
                raise ContractError("session source is invalid", kind="schema")
            vault = ReadOnlyCaptureVault(Path(cast(str, source["root"])))
            if sha256_bytes(str(vault.root).encode("utf-8")) != source.get("root_sha256"):
                raise ContractError("session source root changed", kind="evidence")
            window_start = session.get("capture_window_start_date")
            if not isinstance(window_start, str):
                raise ContractError("session capture window is invalid", kind="schema")
            scan = vault.scan(min_date=window_start)
            index_path = self._index_path(selected)
            index = self.files.read_json(index_path)
            baseline = set(_string_list(index.get("baseline_keys"), "baseline_keys"))
            collected_keys = _string_list(index.get("collected_keys"), "collected_keys")
            capture_ids = _string_list(index.get("capture_ids"), "capture_ids")
            known = baseline | set(collected_keys)
            imported: list[str] = []
            collection_issues: list[Mapping[str, Any]] = list(scan.issues)
            for record in scan.records:
                if record.capture_key in known:
                    continue
                try:
                    attachments = tuple(
                        vault.read_attachment(path) for path in record.attachment_paths
                    )
                except (OSError, ContractError) as exc:
                    collection_issues.append(
                        {
                            "code": "attachment_unavailable",
                            "source_file": record.source_file,
                            "line_start": record.line_start,
                            "detail": str(exc),
                        }
                    )
                    continue
                capture_id = "rcap_" + sha256_json(
                    {"session_id": selected, "capture_key": record.capture_key}
                )[:24]
                event_path = f"captures/{capture_id}.json"
                event = {
                    "schema_version": DATASET_SCHEMA_VERSION,
                    "kind": "memento_real_capture_case",
                    "capture_id": capture_id,
                    "session_id": selected,
                    "imported_at": _now(),
                    "source": {
                        "vault_label": source.get("label"),
                        "source_file": record.source_file,
                        "local_date": record.local_date,
                        "ordinal": record.ordinal,
                        "line_start": record.line_start,
                        "line_end": record.line_end,
                        "time": record.time,
                        "weekday": record.weekday,
                        "source_app": record.source_app,
                        "tag": record.tag,
                        "note": record.note,
                        "source_type": record.source_type,
                        "entry_sha256": record.entry_sha256,
                        "source_snapshot_sha256": record.source_snapshot_sha256,
                    },
                    "raw_block": record.raw_block.decode("utf-8"),
                    "attachments": [self._write_asset(item) for item in attachments],
                    "provider_invoked": False,
                    "formal_vault_write_enabled": False,
                }
                if self.files.exists(event_path):
                    existing = self.files.read_json(event_path)
                    existing_source = existing.get("source")
                    if (
                        existing.get("session_id") != selected
                        or not isinstance(existing_source, dict)
                        or existing_source.get("entry_sha256") != record.entry_sha256
                    ):
                        raise ContractError("existing capture event conflicts with source", kind="conflict")
                else:
                    self.files.write_new_json(event_path, event)
                collected_keys.append(record.capture_key)
                capture_ids.append(capture_id)
                known.add(record.capture_key)
                imported.append(capture_id)
            updated = {
                "schema_version": DATASET_SCHEMA_VERSION,
                "kind": "memento_real_capture_session_index",
                "session_id": selected,
                "baseline_keys": sorted(baseline),
                "collected_keys": collected_keys,
                "capture_ids": capture_ids,
                "last_collected_at": _now(),
                "last_scan_issues": collection_issues,
            }
            self.files.replace_json(index_path, updated)
        return {
            "session_id": selected,
            "new_capture_count": len(imported),
            "new_capture_ids": imported,
            "total_capture_count": len(capture_ids),
            "scan_issue_count": len(collection_issues),
            "provider_invoked": False,
            "formal_vault_write_enabled": False,
        }

    def status(self, session_id: Optional[str] = None) -> Mapping[str, Any]:
        selected = session_id or self.current_session_id()
        session = self._session(selected)
        index = self.files.read_json(self._index_path(selected))
        return {
            "session_id": selected,
            "source": session.get("source"),
            "started_at": session.get("started_at"),
            "include_existing_dates": session.get("include_existing_dates"),
            "capture_window_start_date": session.get("capture_window_start_date"),
            "capture_count": len(_string_list(index.get("capture_ids"), "capture_ids")),
            "last_collected_at": index.get("last_collected_at"),
            "last_scan_issues": index.get("last_scan_issues"),
            "provider_enabled": False,
            "formal_vault_write_enabled": False,
        }

    def capture_events(self, session_id: Optional[str] = None) -> list[Mapping[str, Any]]:
        """Return the selected session's isolated capture events in capture order."""

        selected = session_id or self.current_session_id()
        self._session(selected)
        index = self.files.read_json(self._index_path(selected))
        capture_ids = _string_list(index.get("capture_ids"), "capture_ids")
        return [self.files.read_json(f"captures/{capture_id}.json") for capture_id in capture_ids]

    def export_dataset(self, session_id: Optional[str] = None) -> Mapping[str, Any]:
        selected = session_id or self.current_session_id()
        with self.files.lock("real-capture-dataset"):
            index = self.files.read_json(self._index_path(selected))
            capture_ids = _string_list(index.get("capture_ids"), "capture_ids")
            dataset_id = "dataset_" + sha256_json(
                {"session_id": selected, "capture_ids": capture_ids}
            )[:24]
            export_path = f"exports/{dataset_id}.json"
            if self.files.exists(export_path):
                existing = self.files.read_json(export_path)
                if existing.get("session_id") != selected:
                    raise ContractError("existing dataset export conflicts with session", kind="conflict")
                return existing
            cases: list[Mapping[str, Any]] = []
            for capture_id in capture_ids:
                event = self.files.read_json(f"captures/{capture_id}.json")
                source = event.get("source")
                if not isinstance(source, dict):
                    raise ContractError("capture source is invalid", kind="schema")
                cases.append(
                    {
                        "case_id": "case_" + capture_id.removeprefix("rcap_"),
                        "capture_ref": capture_id,
                        "observed_input": {
                            "source": source,
                            "raw_block": event.get("raw_block"),
                            "attachments": event.get("attachments"),
                        },
                        "user_signals": {"tag": source.get("tag"), "note": source.get("note")},
                        "expected": {
                            "content_role": None,
                            "processing_route": None,
                            "should_enter_long_term_memory": None,
                            "expected_objects": [],
                            "forbidden_outputs": [],
                            "review_notes": None,
                        },
                        "review_status": "needs_user_review",
                    }
                )
            dataset = {
                "schema_version": DATASET_SCHEMA_VERSION,
                "kind": "memento_real_capture_dataset_draft",
                "dataset_id": dataset_id,
                "session_id": selected,
                "created_at": _now(),
                "case_count": len(cases),
                "cases": cases,
                "model_generated_labels": False,
                "provider_invoked": False,
                "formal_vault_write_enabled": False,
            }
            self.files.write_new_json(export_path, dataset)
            return dataset
