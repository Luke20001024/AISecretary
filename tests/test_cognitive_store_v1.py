from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_AGENT = ROOT / "context-agent"
if str(CONTEXT_AGENT) not in sys.path:
    sys.path.insert(0, str(CONTEXT_AGENT))

from core import ContractError  # noqa: E402
from cognitive_v1 import SourceRecordRevision  # noqa: E402
from cognitive_store_v1 import (  # noqa: E402
    LOCATOR_VERSION,
    RecordStore,
    parse_daily_markdown,
    validate_source_record_revision,
)


NOW = dt.datetime(2026, 8, 18, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
DAY = "2026-08-18.md"
FRONTMATTER = b"---\ndate: 2026-08-18\ntype: memento-daily\n---\n"


def block(
    body: str,
    *,
    time: str = "10:50",
    source: str = "Chrome",
    tag: str | None = None,
    newline: str = "\n",
    delimiter: bool = True,
) -> bytes:
    suffix = f" · #{tag}" if tag else ""
    value = (
        f"{newline}## {time} · 周二 · {source}{suffix}{newline}{newline}"
        f"{body}{newline}{newline}"
    )
    if delimiter:
        value += f"---{newline}"
    return value.encode("utf-8")


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="memento-record-store-")
        self.vault = Path(self.temporary.name) / "vault"
        self.vault.mkdir(mode=0o700)
        (self.vault / "assets").mkdir(mode=0o700)
        self.day = self.vault / DAY
        self.store = RecordStore(self.vault)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_day(self, content: bytes) -> None:
        self.day.write_bytes(content)
        self.day.chmod(0o600)

    def read_index(self) -> dict:
        return json.loads(self.store.index_path.read_text(encoding="utf-8"))

    def entry(self, record_id: str) -> dict:
        return next(item for item in self.read_index()["records"] if item["record_id"] == record_id)


class ParseTests(StoreCase):
    def test_crlf_hashes_exact_record_bytes(self) -> None:
        content = FRONTMATTER.replace(b"\n", b"\r\n") + block(
            "CRLF 正文", newline="\r\n"
        )
        parsed = parse_daily_markdown(content, DAY, attachment_loader=lambda _: b"")
        self.assertEqual(len(parsed.records), 1)
        record = parsed.records[0]
        self.assertIn(b"\r\n", record.raw_block)
        self.assertEqual(record.entry_sha256, hashlib.sha256(record.raw_block).hexdigest())
        self.assertEqual(parsed.source_snapshot_sha256, hashlib.sha256(content).hexdigest())

    def test_missing_delimiter_fails_closed(self) -> None:
        self.write_day(FRONTMATTER + block("未完整", delimiter=False))
        before = self.day.read_bytes()
        result = self.store.reconcile_day(DAY, now=NOW)
        self.assertFalse(result.changed)
        self.assertEqual([issue.code for issue in result.needs_review], ["missing_delimiter"])
        self.assertFalse(self.store.index_path.exists())
        self.assertEqual(self.day.read_bytes(), before)

    def test_parses_all_capture_shapes_note_tag_and_attachments(self) -> None:
        assets = {
            "shot.png": b"png-bytes",
            "voice.m4a": b"voice-bytes",
            "image.jpg": b"image-bytes",
            "paper.pdf": b"pdf-bytes",
            "portrait.jpg": b"portrait-bytes",
        }
        for name, payload in assets.items():
            path = self.vault / "assets" / name
            path.write_bytes(payload)
            path.chmod(0o600)
        content = FRONTMATTER
        content += block("我的文本\n\n> 备注: 稍后核对", source="Feishu", tag="灵感")
        content += block("OCR 文本\n\n> ![原截图](./assets/shot.png)", time="10:51", source="截图·OCR")
        content += block("本地转写\n> 来源: Chrome\n> 时长: 4.2 秒\n> [原始录音](./assets/voice.m4a)", time="10:52", source="语音")
        content += block("![](./assets/image.jpg)", time="10:53", source="Memento")
        content += block("[附件](./assets/paper.pdf)", time="10:54", source="Finder")
        content += block("> 时间: 2026-08-18 10:55 CST\n> 天气: 晴\n\n![每日第一帧](./assets/portrait.jpg)", time="10:55", source="每日第一帧")
        self.write_day(content)
        parsed = self.store.parse_day(DAY)
        self.assertEqual([record.source_type for record in parsed.records], [
            "text", "screenshot_ocr", "voice_transcript", "image_note", "file_note", "image_note"
        ])
        self.assertEqual(parsed.records[0].note, "稍后核对")
        self.assertEqual(parsed.records[0].tag, "灵感")
        self.assertEqual(parsed.records[2].source_app, "Chrome")
        self.assertEqual(parsed.records[1].attachments[0].sha256, hashlib.sha256(b"png-bytes").hexdigest())
        self.assertEqual(parsed.records[4].attachments[0].mime_type, "application/pdf")


class ReconcileTests(StoreCase):
    def test_preallocated_capture_id_and_strict_revision_contract(self) -> None:
        self.write_day(FRONTMATTER + block("新采集"))
        record_id = "rec_" + "a" * 24
        result = self.store.reconcile_day(DAY, preallocated_record_id=record_id, now=NOW)
        self.assertEqual(result.created_record_ids, (record_id,))
        head = self.store.load_head(record_id)
        self.assertEqual(head["ingest_origin"], "capture_service")
        self.assertEqual(head["entry_sha256"], self.entry(record_id)["entry_sha256"])
        self.assertEqual(validate_source_record_revision(head), head)
        revision_sha = hash_file(self.store.records_dir / f"{record_id}.r000001.json")
        self.assertEqual(self.store.revision_file_sha(record_id, 1), revision_sha)
        head_ref = self.store.load_head_ref(record_id)
        self.assertEqual(
            head_ref,
            {"kind": "source_record", "id": record_id, "revision": 1, "revision_sha256": revision_sha},
        )
        self.assertEqual(SourceRecordRevision.from_dict(head).sha256, revision_sha)
        self.assertEqual(SourceRecordRevision.from_dict(head).sha256, head_ref["revision_sha256"])

    def test_append_keeps_existing_id_and_revision(self) -> None:
        first_bytes = FRONTMATTER + block("第一条", time="09:10")
        self.write_day(first_bytes)
        first = self.store.reconcile_day(DAY, now=NOW)
        first_id = first.created_record_ids[0]
        first_revision_path = self.store.records_dir / f"{first_id}.r000001.json"
        first_revision_sha = hash_file(first_revision_path)

        appended = first_bytes + block("第二条", time="11:20")
        self.write_day(appended)
        result = self.store.reconcile_day(DAY, now=NOW + dt.timedelta(minutes=1))
        self.assertEqual(len(result.created_record_ids), 1)
        self.assertIn(first_id, result.refreshed_locator_record_ids)
        self.assertEqual(self.entry(first_id)["current_revision"], 1)
        self.assertEqual(hash_file(first_revision_path), first_revision_sha)
        self.assertEqual(self.entry(first_id)["source_snapshot_sha256"], hashlib.sha256(appended).hexdigest())

    def test_front_insertion_refreshes_locator_without_revision(self) -> None:
        original = FRONTMATTER + block("稳定记录", time="10:50")
        self.write_day(original)
        record_id = self.store.reconcile_day(DAY, now=NOW).created_record_ids[0]
        old_line = self.entry(record_id)["line_start"]
        original_revision = hash_file(self.store.records_dir / f"{record_id}.r000001.json")

        inserted = FRONTMATTER + block("新的前置记录", time="09:00") + block("稳定记录", time="10:50")
        self.write_day(inserted)
        result = self.store.reconcile_day(DAY, now=NOW + dt.timedelta(minutes=2))
        self.assertIn(record_id, result.refreshed_locator_record_ids)
        self.assertGreater(self.entry(record_id)["line_start"], old_line)
        self.assertEqual(self.entry(record_id)["current_revision"], 1)
        self.assertEqual(hash_file(self.store.records_dir / f"{record_id}.r000001.json"), original_revision)

    def test_reordering_unique_records_keeps_ids_and_revisions(self) -> None:
        first_block = block("第一条", time="09:10", source="Chrome")
        second_block = block("第二条", time="11:20", source="Craft")
        self.write_day(FRONTMATTER + first_block + second_block)
        initial = self.store.reconcile_day(DAY, now=NOW)
        ids_by_time = {
            self.entry(record_id)["time"]: record_id
            for record_id in initial.created_record_ids
        }
        revision_hashes = {
            record_id: hash_file(self.store.records_dir / f"{record_id}.r000001.json")
            for record_id in initial.created_record_ids
        }

        reordered = FRONTMATTER + second_block + first_block
        self.write_day(reordered)
        result = self.store.reconcile_day(DAY, now=NOW + dt.timedelta(minutes=2))

        self.assertEqual(set(result.refreshed_locator_record_ids), set(initial.created_record_ids))
        self.assertEqual(
            {self.entry(record_id)["time"]: record_id for record_id in initial.created_record_ids},
            ids_by_time,
        )
        for record_id, digest in revision_hashes.items():
            self.assertEqual(self.entry(record_id)["current_revision"], 1)
            self.assertEqual(hash_file(self.store.records_dir / f"{record_id}.r000001.json"), digest)
            self.assertEqual(
                self.entry(record_id)["source_snapshot_sha256"],
                hashlib.sha256(reordered).hexdigest(),
            )

    def test_unique_body_edit_keeps_id_and_appends_revision(self) -> None:
        original = FRONTMATTER + block("原文", time="14:20", source="Craft")
        self.write_day(original)
        record_id = self.store.reconcile_day(DAY, now=NOW).created_record_ids[0]
        first_path = self.store.records_dir / f"{record_id}.r000001.json"
        first_sha = hash_file(first_path)

        edited = FRONTMATTER + block("修改后的正文", time="14:20", source="Craft")
        self.write_day(edited)
        before = self.day.read_bytes()
        result = self.store.reconcile_day(DAY, now=NOW + dt.timedelta(minutes=3))
        self.assertEqual(result.revised_record_ids, (record_id,))
        chain = self.store.load_chain(record_id)
        self.assertEqual([item["revision"] for item in chain], [1, 2])
        self.assertEqual(chain[1]["operation"], "source_edit")
        self.assertEqual(chain[1]["previous_revision_sha256"], first_sha)
        self.assertNotEqual(chain[0]["entry_sha256"], chain[1]["entry_sha256"])
        self.assertEqual(self.day.read_bytes(), before)

    def test_delete_appends_tombstone_without_touching_source(self) -> None:
        first_block = block("保留", time="09:00")
        second_block = block("删除", time="10:00")
        self.write_day(FRONTMATTER + first_block + second_block)
        initial = self.store.reconcile_day(DAY, now=NOW)
        by_time = {self.entry(record_id)["time"]: record_id for record_id in initial.created_record_ids}
        deleted_id = by_time["10:00"]
        self.write_day(FRONTMATTER + first_block)
        source_before = self.day.read_bytes()
        result = self.store.reconcile_day(DAY, now=NOW + dt.timedelta(minutes=4))
        self.assertEqual(result.tombstoned_record_ids, (deleted_id,))
        head = self.store.load_head(deleted_id)
        self.assertEqual(head["status"], "tombstone")
        self.assertEqual(head["operation"], "user_delete")
        self.assertEqual(self.day.read_bytes(), source_before)

    def test_exact_duplicates_fail_closed_as_needs_review(self) -> None:
        duplicate = block("完全相同", time="12:00", source="Chrome")
        self.write_day(FRONTMATTER + duplicate + duplicate)
        initial = self.store.reconcile_day(DAY, now=NOW)
        self.assertEqual(len(initial.created_record_ids), 2)
        duplicate_entries = [self.entry(record_id) for record_id in initial.created_record_ids]
        self.assertEqual(
            sorted(entry["original_occurrence_ordinal"] for entry in duplicate_entries),
            [1, 2],
        )
        self.assertEqual({entry["locator_version"] for entry in duplicate_entries}, {LOCATOR_VERSION})
        revision_hashes = {
            record_id: hash_file(self.store.records_dir / f"{record_id}.r000001.json")
            for record_id in initial.created_record_ids
        }
        second = self.store.reconcile_day(DAY, now=NOW + dt.timedelta(minutes=5))
        self.assertFalse(second.changed)
        self.assertEqual([issue.code for issue in second.needs_review], ["duplicate_exact_match"])
        self.assertEqual(set(second.unchanged_record_ids), set(initial.created_record_ids))
        for record_id, digest in revision_hashes.items():
            self.assertEqual(hash_file(self.store.records_dir / f"{record_id}.r000001.json"), digest)

    def test_ambiguous_edits_never_reassign_or_create_records(self) -> None:
        self.write_day(
            FRONTMATTER
            + block("原始 A", time="12:00", source="Chrome")
            + block("原始 B", time="12:00", source="Chrome")
        )
        initial = self.store.reconcile_day(DAY, now=NOW)
        revision_hashes = {
            record_id: hash_file(self.store.records_dir / f"{record_id}.r000001.json")
            for record_id in initial.created_record_ids
        }

        edited = (
            FRONTMATTER
            + block("修改 A", time="12:00", source="Chrome")
            + block("修改 B", time="12:00", source="Chrome")
        )
        self.write_day(edited)
        source_before = self.day.read_bytes()
        result = self.store.reconcile_day(DAY, now=NOW + dt.timedelta(minutes=6))

        self.assertEqual([issue.code for issue in result.needs_review], ["ambiguous_source_edit"])
        self.assertEqual(set(result.unchanged_record_ids), set(initial.created_record_ids))
        self.assertFalse(result.created_record_ids)
        self.assertFalse(result.revised_record_ids)
        self.assertFalse(result.tombstoned_record_ids)
        for record_id, digest in revision_hashes.items():
            self.assertEqual(len(self.store.load_chain(record_id)), 1)
            self.assertEqual(hash_file(self.store.records_dir / f"{record_id}.r000001.json"), digest)
        self.assertEqual(self.day.read_bytes(), source_before)

    def test_far_moved_edit_fails_closed_instead_of_delete_and_recreate(self) -> None:
        self.write_day(FRONTMATTER + block("原始正文", time="12:10", source="Chrome"))
        initial = self.store.reconcile_day(DAY, now=NOW)
        record_id = initial.created_record_ids[0]
        revision_sha = hash_file(self.store.records_dir / f"{record_id}.r000001.json")

        moved = FRONTMATTER + (b"padding\n" * 120) + block("已修改正文", time="12:10", source="Chrome")
        self.write_day(moved)
        result = self.store.reconcile_day(DAY, now=NOW + dt.timedelta(minutes=7))

        self.assertEqual([issue.code for issue in result.needs_review], ["source_edit_outside_locator"])
        self.assertEqual(result.unchanged_record_ids, (record_id,))
        self.assertFalse(result.created_record_ids)
        self.assertFalse(result.revised_record_ids)
        self.assertFalse(result.tombstoned_record_ids)
        self.assertEqual(hash_file(self.store.records_dir / f"{record_id}.r000001.json"), revision_sha)

    def test_idempotent_reconcile_preserves_all_state_bytes(self) -> None:
        self.write_day(FRONTMATTER + block("幂等记录"))
        first = self.store.reconcile_day(DAY, now=NOW)
        tracked = [path for path in self.store.root.rglob("*") if path.is_file() and path.name != "records.lock"]
        before = {path.relative_to(self.store.root).as_posix(): path.read_bytes() for path in tracked}
        second = self.store.reconcile_day(DAY, now=NOW + dt.timedelta(hours=1))
        self.assertFalse(second.changed)
        after_paths = [path for path in self.store.root.rglob("*") if path.is_file() and path.name != "records.lock"]
        after = {path.relative_to(self.store.root).as_posix(): path.read_bytes() for path in after_paths}
        self.assertEqual(before, after)
        self.assertEqual(second.unchanged_record_ids, first.created_record_ids)

    def test_original_markdown_and_attachment_bytes_are_invariant(self) -> None:
        asset = self.vault / "assets" / "capture.png"
        asset.write_bytes(b"immutable-image")
        asset.chmod(0o600)
        self.write_day(FRONTMATTER + block("OCR\n> ![原截图](./assets/capture.png)", source="截图·OCR"))
        before = {DAY: hash_file(self.day), "assets/capture.png": hash_file(asset)}
        self.store.reconcile_day(DAY, now=NOW)
        after = {DAY: hash_file(self.day), "assets/capture.png": hash_file(asset)}
        self.assertEqual(before, after)

    def test_attachment_content_change_appends_same_record_revision(self) -> None:
        asset = self.vault / "assets" / "capture.png"
        asset.write_bytes(b"image-v1")
        asset.chmod(0o600)
        self.write_day(
            FRONTMATTER
            + block("OCR\n> ![原截图](./assets/capture.png)", source="截图·OCR")
        )
        source_before = self.day.read_bytes()
        initial = self.store.reconcile_day(DAY, now=NOW)
        record_id = initial.created_record_ids[0]
        first = self.store.load_head(record_id)

        asset.write_bytes(b"image-v2")
        asset.chmod(0o600)
        result = self.store.reconcile_day(DAY, now=NOW + dt.timedelta(minutes=1))
        chain = self.store.load_chain(record_id)

        self.assertEqual(result.revised_record_ids, (record_id,))
        self.assertEqual([revision["revision"] for revision in chain], [1, 2])
        self.assertEqual(chain[0]["entry_sha256"], chain[1]["entry_sha256"])
        self.assertNotEqual(chain[0]["attachments"][0]["sha256"], chain[1]["attachments"][0]["sha256"])
        self.assertEqual(first["attachments"], chain[0]["attachments"])
        self.assertEqual(self.day.read_bytes(), source_before)

    def test_attachment_change_during_reconcile_fails_stale_before_commit(self) -> None:
        asset = self.vault / "assets" / "capture.png"
        asset.write_bytes(b"image-v1")
        asset.chmod(0o600)
        self.write_day(
            FRONTMATTER
            + block("OCR\n> ![原截图](./assets/capture.png)", source="截图·OCR")
        )
        reads = 0

        def changing_attachment(_: str) -> bytes:
            nonlocal reads
            reads += 1
            return b"image-v1" if reads == 1 else b"image-v2"

        with mock.patch.object(self.store, "_load_attachment", side_effect=changing_attachment):
            with self.assertRaises(ContractError) as raised:
                self.store.reconcile_day(DAY, now=NOW)
        self.assertEqual(raised.exception.kind, "stale")
        self.assertFalse(self.store.index_path.exists())
        self.assertFalse(list(self.store.records_dir.glob("*.json")))

    def test_interrupted_staging_is_recovered_without_duplicate_revision(self) -> None:
        self.write_day(FRONTMATTER + block("可恢复记录"))
        original_replace = self.store._safe_write_replace
        interrupted = False

        def fail_before_index(path: Path, value: dict) -> None:
            nonlocal interrupted
            if path == self.store.index_path and not interrupted:
                interrupted = True
                raise RuntimeError("crash")
            original_replace(path, value)

        with mock.patch.object(self.store, "_safe_write_replace", side_effect=fail_before_index):
            with self.assertRaisesRegex(RuntimeError, "crash"):
                self.store.reconcile_day(DAY, now=NOW)
        staged = list(self.store.staging_dir.iterdir())
        self.assertEqual(len(staged), 1)
        self.assertFalse(self.store.index_path.exists())
        materialized = list(self.store.records_dir.glob("*.r000001.json"))
        self.assertEqual(len(materialized), 1)
        materialized_sha = hash_file(materialized[0])

        recovered_store = RecordStore(self.vault)
        result = recovered_store.reconcile_day(DAY, now=NOW + dt.timedelta(minutes=1))
        self.assertFalse(result.created_record_ids)
        index = json.loads(recovered_store.index_path.read_text(encoding="utf-8"))
        self.assertEqual(len(index["records"]), 1)
        record_id = index["records"][0]["record_id"]
        self.assertEqual(len(recovered_store.load_chain(record_id)), 1)
        self.assertEqual(
            hash_file(recovered_store.records_dir / f"{record_id}.r000001.json"),
            materialized_sha,
        )
        self.assertFalse(list(recovered_store.staging_dir.iterdir()))
        self.assertEqual(len(list(recovered_store.committed_dir.iterdir())), 1)

    def test_revision_chain_rejects_tampered_predecessor_bytes(self) -> None:
        self.write_day(FRONTMATTER + block("第一版", time="14:20", source="Craft"))
        record_id = self.store.reconcile_day(DAY, now=NOW).created_record_ids[0]
        self.write_day(FRONTMATTER + block("第二版", time="14:20", source="Craft"))
        self.store.reconcile_day(DAY, now=NOW + dt.timedelta(minutes=1))

        predecessor = self.store.records_dir / f"{record_id}.r000001.json"
        predecessor.write_bytes(predecessor.read_bytes() + b"\n")
        predecessor.chmod(0o600)
        with self.assertRaises(ContractError) as raised:
            self.store.load_head(record_id)
        self.assertEqual(raised.exception.kind, "evidence")


    def test_lists_validated_heads_and_refs_in_capture_order(self) -> None:
        self.write_day(
            FRONTMATTER
            + block("较晚记录", time="14:20", source="Chrome")
            + block("较早记录", time="09:10", source="Craft")
        )
        created = self.store.reconcile_day(DAY, now=NOW).created_record_ids
        heads = self.store.list_heads(local_date="2026-08-18")
        self.assertEqual([head["captured_at"][11:16] for head in heads], ["09:10", "14:20"])
        refs = self.store.list_head_refs(local_date="2026-08-18")
        self.assertEqual([ref["id"] for ref in refs], [head["record_id"] for head in heads])
        self.assertEqual(
            [ref["revision_sha256"] for ref in refs],
            [self.store.revision_file_sha(head["record_id"], head["revision"]) for head in heads],
        )

        self.write_day(FRONTMATTER + block("较晚记录", time="14:20", source="Chrome"))
        deleted = self.store.reconcile_day(DAY, now=NOW + dt.timedelta(minutes=1)).tombstoned_record_ids
        self.assertEqual(len(deleted), 1)
        self.assertEqual(len(self.store.list_heads()), 1)
        all_heads = self.store.list_heads(include_tombstones=True)
        self.assertEqual(len(all_heads), 2)
        self.assertEqual({head["record_id"] for head in all_heads}, set(created))


class SafetyTests(StoreCase):
    def test_rejects_path_escape_symlink_and_hardlink(self) -> None:
        with self.assertRaises(ContractError):
            self.store.reconcile_day("../2026-08-18.md", now=NOW)

        target = self.vault / "real.md"
        target.write_bytes(FRONTMATTER + block("符号链接"))
        self.day.symlink_to(target)
        with self.assertRaises(ContractError):
            self.store.reconcile_day(DAY, now=NOW)
        self.day.unlink()

        self.write_day(FRONTMATTER + block("硬链接"))
        os.link(self.day, self.vault / "peer.md")
        with self.assertRaises(ContractError):
            self.store.reconcile_day(DAY, now=NOW)

    def test_rejects_nonregular_daily_source_without_blocking(self) -> None:
        os.mkfifo(self.day, mode=0o600)
        with self.assertRaises(ContractError) as raised:
            self.store.reconcile_day(DAY, now=NOW)
        self.assertEqual(raised.exception.kind, "evidence")

    def test_rejects_unsafe_attachment_parent_and_hardlink(self) -> None:
        asset = self.vault / "assets" / "shot.png"
        asset.write_bytes(b"shot")
        asset.chmod(0o600)
        os.link(asset, self.vault / "assets" / "peer.png")
        self.write_day(FRONTMATTER + block("OCR\n> ![原截图](./assets/shot.png)", source="截图·OCR"))
        result = self.store.reconcile_day(DAY, now=NOW)
        self.assertEqual([issue.code for issue in result.needs_review], ["attachment_unavailable"])
        self.assertFalse(self.store.index_path.exists())

    def test_rejects_broken_symlink_at_mutable_index_path(self) -> None:
        self.store._ensure_layout()
        self.store.index_path.symlink_to(self.store.root / "missing-index.json")
        self.write_day(FRONTMATTER + block("不应覆盖符号链接"))
        with self.assertRaises(ContractError) as raised:
            self.store.reconcile_day(DAY, now=NOW)
        self.assertEqual(raised.exception.kind, "evidence")
        self.assertTrue(self.store.index_path.is_symlink())

    def test_invalid_index_json_fails_closed(self) -> None:
        self.write_day(FRONTMATTER + block("安全记录"))
        self.store.reconcile_day(DAY, now=NOW)
        source_before = self.day.read_bytes()
        self.store.index_path.write_text("{not-json", encoding="utf-8")
        self.store.index_path.chmod(0o600)
        with self.assertRaises(ContractError) as raised:
            self.store.reconcile_day(DAY, now=NOW + dt.timedelta(minutes=1))
        self.assertEqual(raised.exception.kind, "schema")
        self.assertEqual(self.day.read_bytes(), source_before)

    def test_incomplete_staging_is_quarantined(self) -> None:
        self.store._ensure_layout()
        incomplete = self.store.staging_dir / "tx_incomplete"
        incomplete.mkdir(mode=0o700)
        (incomplete / "partial.tmp").write_text("partial", encoding="utf-8")
        self.write_day(FRONTMATTER + block("正常记录"))
        result = self.store.reconcile_day(DAY, now=NOW)
        self.assertEqual(len(result.created_record_ids), 1)
        self.assertFalse(incomplete.exists())
        self.assertEqual(len(list(self.store.quarantine_dir.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
