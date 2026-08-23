from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_AGENT = ROOT / "context-agent"
if str(CONTEXT_AGENT) not in os.sys.path:
    os.sys.path.insert(0, str(CONTEXT_AGENT))

from cognitive_migration_v1 import CognitiveMigration
from core import ContractError


NOW = dt.datetime(2026, 8, 18, 9, 30, tzinfo=dt.timezone(dt.timedelta(hours=8)))


def _daily(text: str, time_value: str = "09:12") -> str:
    return (
        "---\n"
        "date: 2026-08-18\n"
        "type: memento-daily\n"
        "---\n\n"
        f"## {time_value} · 周二 · 手动\n\n{text}\n\n---\n"
    )


class CognitiveMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.vault = Path(self.temporary.name)
        (self.vault / "2026-08-18.md").write_text(
            _daily("做产品判断前，我会先找反例。"), encoding="utf-8"
        )
        (self.vault / "Reviews" / "Daily").mkdir(parents=True)
        (self.vault / "Reviews" / "Daily" / "2026-08-18.md").write_text(
            "# Daily Review\n", encoding="utf-8"
        )
        agent = self.vault / ".context-agent" / "agent-v1"
        (agent / "memories").mkdir(parents=True)
        (agent / "user-actions").mkdir()
        (agent / "memories" / "mem_example.r000001.json").write_text(
            "{}\n", encoding="utf-8"
        )
        (agent / "user-actions" / "uact_example.json").write_text(
            "{}\n", encoding="utf-8"
        )
        (agent / "profile.json").write_text(
            json.dumps({"schema_version": "fixture"}) + "\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_inventory_and_backfill_preserve_all_legacy_bytes(self) -> None:
        migration = CognitiveMigration(self.vault)
        before = migration.inventory()
        source_bytes = (self.vault / "2026-08-18.md").read_bytes()
        review_bytes = (self.vault / "Reviews" / "Daily" / "2026-08-18.md").read_bytes()
        memory_bytes = (
            self.vault
            / ".context-agent"
            / "agent-v1"
            / "memories"
            / "mem_example.r000001.json"
        ).read_bytes()
        result = migration.backfill_record_index(now=NOW)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.created_count, 1)
        self.assertEqual(result.parsed_count, 1)
        self.assertEqual((self.vault / "2026-08-18.md").read_bytes(), source_bytes)
        self.assertEqual(
            (self.vault / "Reviews" / "Daily" / "2026-08-18.md").read_bytes(),
            review_bytes,
        )
        self.assertEqual(
            (
                self.vault
                / ".context-agent"
                / "agent-v1"
                / "memories"
                / "mem_example.r000001.json"
            ).read_bytes(),
            memory_bytes,
        )
        after = migration.inventory()
        self.assertEqual(before.public_summary(), after.public_summary())

    def test_replay_is_zero_change_and_keeps_record_id(self) -> None:
        migration = CognitiveMigration(self.vault)
        first = migration.backfill_record_index(now=NOW)
        first_ids = [row["record_id"] for row in migration.records.list_heads()]
        second = migration.backfill_record_index(now=NOW + dt.timedelta(minutes=1))
        second_ids = [row["record_id"] for row in migration.records.list_heads()]
        self.assertEqual(first.created_count, 1)
        self.assertEqual(second.created_count, 0)
        self.assertEqual(second.unchanged_count, 1)
        self.assertEqual(first_ids, second_ids)

    def test_append_new_record_preserves_existing_id(self) -> None:
        migration = CognitiveMigration(self.vault)
        migration.backfill_record_index(now=NOW)
        first_id = migration.records.list_heads()[0]["record_id"]
        with (self.vault / "2026-08-18.md").open("a", encoding="utf-8") as handle:
            handle.write("\n## 10:20 · 周二 · 手动\n\n我需要更早验证方案。\n\n---\n")
        result = migration.backfill_record_index(now=NOW + dt.timedelta(minutes=2))
        heads = migration.records.list_heads()
        self.assertEqual(result.created_count, 1)
        self.assertEqual(len(heads), 2)
        self.assertEqual(heads[0]["record_id"], first_id)

    def test_source_symlink_is_rejected_without_sidecars(self) -> None:
        target = self.vault / "target.md"
        target.write_text(_daily("不应读取。"), encoding="utf-8")
        source = self.vault / "2026-08-19.md"
        source.symlink_to(target)
        with self.assertRaises(ContractError):
            CognitiveMigration(self.vault).inventory()
        self.assertFalse(
            (self.vault / ".context-agent" / "cognitive-secretary-v1").exists()
        )

    def test_invalid_or_duplicate_selection_is_rejected(self) -> None:
        migration = CognitiveMigration(self.vault)
        with self.assertRaises(ContractError):
            migration.backfill_record_index(source_files=["other.md"], now=NOW)
        with self.assertRaises(ContractError):
            migration.backfill_record_index(
                source_files=["2026-08-18.md", "2026-08-18.md"], now=NOW
            )


if __name__ == "__main__":
    unittest.main()
