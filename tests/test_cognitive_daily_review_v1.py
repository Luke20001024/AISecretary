from __future__ import annotations

import dataclasses
import hashlib
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_AGENT = ROOT / "context-agent"
if str(CONTEXT_AGENT) not in sys.path:
    sys.path.insert(0, str(CONTEXT_AGENT))

from core import ContractError, sha256_bytes  # noqa: E402
from cognitive_daily_review_v1 import (  # noqa: E402
    CognitiveDailyReviewRenderer,
    DailyReviewProjectionResult,
)
from cognitive_v1 import (  # noqa: E402
    COGNITIVE_SCHEMA_VERSION,
    DailySummaryRevision,
    InterpretationReceiptRevision,
    ObjectRef,
    SourceRecordRevision,
    SourceSpan,
    make_daily_summary_id,
    make_receipt_id,
)


LOCAL_DATE = "2026-08-18"
NOW = "2026-08-18T21:00:00+08:00"
RECORD_ID = "rec_111111111111111111111111"
QUOTE = "先暴露可验证的部分，再补齐完整方案。"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class DailyReviewRendererCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="memento-daily-review-")
        self.vault = Path(self.temporary.name) / "vault"
        self.vault.mkdir(mode=0o700)
        self.source = SourceRecordRevision(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_source_record_revision",
            record_id=RECORD_ID,
            revision=1,
            status="active",
            operation="ingest",
            created_at=NOW,
            captured_at="2026-08-18T10:12:00+08:00",
            local_date=LOCAL_DATE,
            source_type="text",
            source_app="Chrome",
            source_file=f"{LOCAL_DATE}.md",
            line_start=3,
            line_end=3,
            entry_sha256="1" * 64,
            source_snapshot_sha256="2" * 64,
            attachments=(),
            ingest_origin="capture_service",
            previous_revision_sha256=None,
        )
        self.source_ref = ObjectRef("source_record", RECORD_ID, 1, self.source.sha256)
        self.span = SourceSpan(
            record_id=RECORD_ID,
            record_revision=1,
            record_revision_sha256=self.source.sha256,
            source_file=f"{LOCAL_DATE}.md",
            line_start=3,
            line_end=3,
            quote=QUOTE,
            quote_sha256=sha256_bytes(QUOTE.encode("utf-8")),
        )
        self.receipt = InterpretationReceiptRevision(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_interpretation_receipt_revision",
            receipt_id=make_receipt_id(RECORD_ID),
            revision=1,
            status="ready",
            operation="interpret",
            created_at=NOW,
            request_id="ireq_111111111111111111111111",
            run_id="irun_111111111111111111111111",
            record_ref=self.source_ref,
            user_action_id=None,
            summary="这条记录强调先验证再完善。",
            facets={
                "content_types": ["observation"],
                "topics": ["产品设计"],
                "objects": ["方案评审"],
                "stance": "self_observation",
                "cognitive_state": "first_seen",
                "purposes": ["future_decision"],
            },
            memory_candidates=(),
            relation_candidates=(),
            source_spans=(self.span,),
            contract_version="1.0",
            feedback_watermark_sha256="3" * 64,
            previous_revision_sha256=None,
        )
        self.receipt_ref = ObjectRef(
            "interpretation_receipt",
            self.receipt.receipt_id,
            1,
            self.receipt.sha256,
        )
        self.summary = self.make_summary()
        self.summary_ref = self.ref_for(self.summary)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def ref_for(summary: DailySummaryRevision) -> ObjectRef:
        return ObjectRef("daily_summary", summary.summary_id, summary.revision, summary.sha256)

    def make_summary(
        self,
        *,
        revision: int = 1,
        previous: DailySummaryRevision | None = None,
        overview: str = "今天重复关注了方案的最早验证方式。",
        review_file: str | None = None,
    ) -> DailySummaryRevision:
        return DailySummaryRevision(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_daily_summary_revision",
            summary_id=make_daily_summary_id(LOCAL_DATE),
            revision=revision,
            status="active",
            operation="generate" if revision == 1 else "regenerate",
            created_at=NOW,
            local_date=LOCAL_DATE,
            overview=overview,
            themes=("早期验证",),
            changes=(),
            unresolved_questions=("什么时候已经足够验证？",),
            action_clues=("下次评审先发可验证部分。",),
            source_refs=(self.source_ref,),
            receipt_refs=(self.receipt_ref,),
            review_file=review_file or f"Reviews/Daily/{LOCAL_DATE}.md",
            review_sha256=None,
            user_supplement_sha256=None,
            previous_revision_sha256=None if previous is None else previous.sha256,
        )

    def render(
        self,
        renderer: CognitiveDailyReviewRenderer,
        summary: DailySummaryRevision | None = None,
    ) -> DailyReviewProjectionResult:
        value = summary or self.summary
        return renderer.render(
            summary=value,
            summary_ref=self.ref_for(value),
            sources=(self.source,),
            receipts=(self.receipt,),
        )

    @property
    def review_path(self) -> Path:
        return self.vault / "Reviews" / "Daily" / f"{LOCAL_DATE}.md"

    @staticmethod
    def supplement_tail(content: bytes) -> bytes:
        marker = "## 我的补充".encode("utf-8")
        start = content.index(marker)
        newline = content.index(b"\n", start)
        return content[newline + 1 :]

    def test_first_render_and_identical_replay_are_bounded_and_byte_stable(self) -> None:
        renderer = CognitiveDailyReviewRenderer(self.vault)
        first = self.render(renderer)
        first_bytes = self.review_path.read_bytes()
        before = self.review_path.stat()

        second = self.render(renderer)
        after = self.review_path.stat()

        self.assertEqual(first.status, "created")
        self.assertEqual(second.status, "unchanged")
        self.assertEqual(self.review_path.read_bytes(), first_bytes)
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))
        self.assertEqual(stat.S_IMODE(after.st_mode), 0o600)
        self.assertEqual(first.review_sha256, digest(first_bytes))
        self.assertIsNone(first.user_supplement_sha256)
        self.assertEqual(
            set(first.summary_binding()),
            {"review_file", "review_sha256", "user_supplement_sha256"},
        )
        self.assertNotIn("daily_summary", first_bytes.decode("utf-8"))

    def test_summary_update_preserves_user_supplement_bytes_exactly(self) -> None:
        renderer = CognitiveDailyReviewRenderer(self.vault)
        self.render(renderer)
        content = self.review_path.read_bytes()
        old_tail = self.supplement_tail(content)
        custom_tail = b"\r\n" + "我要保留的补充\r\n## 私人小节\r\n".encode("utf-8")
        self.review_path.write_bytes(content[: -len(old_tail)] + custom_tail)
        self.review_path.chmod(0o600)
        updated = self.make_summary(
            revision=2,
            previous=self.summary,
            overview="今天的判断已经补充了一处反例。",
        )

        result = self.render(renderer, updated)
        rendered = self.review_path.read_bytes()

        self.assertEqual(result.status, "updated")
        self.assertEqual(self.supplement_tail(rendered), custom_tail)
        self.assertEqual(result.user_supplement_sha256, digest(custom_tail))
        self.assertIn("补充了一处反例".encode("utf-8"), rendered)

    def test_valid_legacy_review_migrates_without_touching_supplement(self) -> None:
        self.review_path.parent.mkdir(mode=0o700, parents=True)
        custom_tail = "\n保留我的原文。\n## 用户标题\n".encode("utf-8")
        legacy = (
            "---\n"
            f"date: {LOCAL_DATE}\n"
            "type: memento-review\n"
            "period: daily\n"
            "---\n\n"
            f"# Daily Review · {LOCAL_DATE}\n\n"
            "## 工作与生活现场\n\n无\n\n"
            "## 行动线索\n\n无\n\n"
            "## 灵感与想法\n\n无\n\n"
            "## 个人记录/情绪\n\n无\n\n"
            "## 已忽略\n\n无\n\n"
            "## 来源索引\n\n无\n\n"
            "## 我的补充\n"
        ).encode("utf-8") + custom_tail
        self.review_path.write_bytes(legacy)
        self.review_path.chmod(0o600)

        result = self.render(CognitiveDailyReviewRenderer(self.vault))

        self.assertEqual(result.status, "updated")
        self.assertEqual(self.supplement_tail(self.review_path.read_bytes()), custom_tail)
        self.assertIn(b"type: memento-cognitive-review", self.review_path.read_bytes())

    def test_corrupt_existing_review_and_wrong_summary_ref_fail_closed(self) -> None:
        self.review_path.parent.mkdir(mode=0o700, parents=True)
        corrupt = b"---\ndate: 2026-08-18\n---\n# broken\n"
        self.review_path.write_bytes(corrupt)
        self.review_path.chmod(0o600)
        renderer = CognitiveDailyReviewRenderer(self.vault)
        with self.assertRaises(ContractError):
            self.render(renderer)
        self.assertEqual(self.review_path.read_bytes(), corrupt)

        self.review_path.unlink()
        wrong_ref = ObjectRef("daily_summary", self.summary.summary_id, 1, "f" * 64)
        with self.assertRaises(ContractError):
            renderer.render(
                summary=self.summary,
                summary_ref=wrong_ref,
                sources=(self.source,),
                receipts=(self.receipt,),
            )
        self.assertFalse(self.review_path.exists())

    def test_conflicting_editor_write_at_publish_point_is_not_overwritten(self) -> None:
        initial = CognitiveDailyReviewRenderer(self.vault)
        self.render(initial)
        updated = self.make_summary(revision=2, previous=self.summary, overview="新概览")
        user_edit = self.review_path.read_bytes().replace(b"\n\n\xe6\x97\xa0\n", b"\n\nuser edit\n", 1)

        def conflict(stage: str) -> None:
            if stage == "before_publish":
                self.review_path.write_bytes(user_edit)
                self.review_path.chmod(0o600)

        with self.assertRaises(ContractError):
            self.render(CognitiveDailyReviewRenderer(self.vault, fault_hook=conflict), updated)
        self.assertEqual(self.review_path.read_bytes(), user_edit)

    def test_path_symlink_hardlink_and_insecure_mode_attacks_fail_closed(self) -> None:
        with self.subTest("review path contract"):
            invalid = self.make_summary(review_file=f"Reviews/Other/{LOCAL_DATE}.md")
            with self.assertRaises(ContractError):
                self.render(CognitiveDailyReviewRenderer(self.vault), invalid)

        for attack in ("symlink", "hardlink", "mode"):
            with self.subTest(attack=attack):
                with tempfile.TemporaryDirectory(prefix="memento-attack-") as raw:
                    vault = Path(raw) / "vault"
                    vault.mkdir(mode=0o700)
                    target = vault / "Reviews" / "Daily" / f"{LOCAL_DATE}.md"
                    target.parent.mkdir(mode=0o700, parents=True)
                    payload = b"do not overwrite"
                    outside = Path(raw) / "outside.md"
                    outside.write_bytes(payload)
                    outside.chmod(0o600)
                    if attack == "symlink":
                        target.symlink_to(outside)
                    elif attack == "hardlink":
                        os.link(outside, target)
                    else:
                        target.write_bytes(payload)
                        target.chmod(0o644)
                    with self.assertRaises(ContractError):
                        CognitiveDailyReviewRenderer(vault).render(
                            summary=self.summary,
                            summary_ref=self.summary_ref,
                            sources=(self.source,),
                            receipts=(self.receipt,),
                        )
                    self.assertEqual(outside.read_bytes(), payload)

        with self.subTest(attack="insecure state directory"):
            with tempfile.TemporaryDirectory(prefix="memento-state-mode-") as raw:
                vault = Path(raw) / "vault"
                vault.mkdir(mode=0o700)
                state = vault / ".context-agent"
                state.mkdir(mode=0o755)
                state.chmod(0o755)
                with self.assertRaises(ContractError):
                    CognitiveDailyReviewRenderer(vault).render(
                        summary=self.summary,
                        summary_ref=self.summary_ref,
                        sources=(self.source,),
                        receipts=(self.receipt,),
                    )
                self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o755)

    def test_crash_after_journal_recovers_first_publish(self) -> None:
        def crash(stage: str) -> None:
            if stage == "after_journal":
                raise RuntimeError("simulated crash")

        renderer = CognitiveDailyReviewRenderer(self.vault, fault_hook=crash)
        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            self.render(renderer)
        self.assertFalse(self.review_path.exists())

        recovered = CognitiveDailyReviewRenderer(self.vault).recover()

        self.assertEqual(recovered, 1)
        self.assertTrue(self.review_path.exists())
        replay = self.render(CognitiveDailyReviewRenderer(self.vault))
        self.assertEqual(replay.status, "unchanged")

    def test_crash_after_exchange_recovers_previous_version(self) -> None:
        self.render(CognitiveDailyReviewRenderer(self.vault))
        old_bytes = self.review_path.read_bytes()
        updated = self.make_summary(revision=2, previous=self.summary, overview="已更新的概览")

        def crash(stage: str) -> None:
            if stage == "after_publish":
                raise RuntimeError("simulated crash")

        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            self.render(CognitiveDailyReviewRenderer(self.vault, fault_hook=crash), updated)
        self.assertIn("已更新的概览".encode("utf-8"), self.review_path.read_bytes())

        recovered = CognitiveDailyReviewRenderer(self.vault).recover()

        self.assertEqual(recovered, 1)
        recovery_files = list((self.vault / "Reviews" / ".recovery" / "CognitiveDaily").glob("*.previous.*.md"))
        self.assertEqual(len(recovery_files), 1)
        self.assertEqual(recovery_files[0].read_bytes(), old_bytes)

    def test_tampered_recovery_candidate_is_rejected(self) -> None:
        def crash(stage: str) -> None:
            if stage == "after_journal":
                raise RuntimeError("simulated crash")

        with self.assertRaises(RuntimeError):
            self.render(CognitiveDailyReviewRenderer(self.vault, fault_hook=crash))
        staging = self.vault / ".context-agent" / "cognitive-secretary-v1" / "daily-review-projection" / "staging"
        candidate = next(staging.iterdir())
        candidate.write_bytes(b"tampered")
        candidate.chmod(0o600)

        with self.assertRaises(ContractError):
            CognitiveDailyReviewRenderer(self.vault).recover()
        self.assertFalse(self.review_path.exists())


if __name__ == "__main__":
    unittest.main()
