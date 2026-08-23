from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, cast

import pytest

from memento_backend.evaluation.real_capture_dataset import (
    ReadOnlyCaptureVault,
    RealCaptureDatasetStore,
)


def private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)
    return path


def private_file(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    os.chmod(path, 0o600)


def daily(*blocks: str) -> bytes:
    return ("---\ndate: 2026-08-23\ntype: memento-daily\n---\n" + "".join(blocks)).encode("utf-8")


TEXT_BLOCK = """
## 09:10 · 周日 · Safari · #灵感

边界确认后再开始

> 备注: 这是我的判断

---
"""
SCREENSHOT_BLOCK = """
## 09:20 · 周日 · 截图·OCR

网页里的客体原文

> ![原截图](./assets/shot.png)

---
"""
VOICE_BLOCK = """
## 09:30 · 周日 · 语音

我想把变化理由留下来
> 来源: Memento
> [原始录音](./assets/voice.m4a)

---
"""


def make_vault(tmp_path: Path, initial: bytes) -> Path:
    vault = private_directory(tmp_path / "AISecretary")
    private_directory(vault / "assets")
    private_file(vault / "2026-08-23.md", initial)
    return vault


def make_store(tmp_path: Path) -> RealCaptureDatasetStore:
    return RealCaptureDatasetStore(private_directory(tmp_path / "dataset"))


def test_scanner_reads_only_complete_records_and_preserves_user_signals(tmp_path: Path) -> None:
    incomplete = "\n## 10:00 · 周日 · Safari\n\n尚未完成"
    vault = make_vault(tmp_path, daily(TEXT_BLOCK, incomplete))
    scan = ReadOnlyCaptureVault(vault).scan()
    assert len(scan.records) == 1
    assert scan.records[0].source_app == "Safari"
    assert scan.records[0].tag == "灵感"
    assert scan.records[0].note == "这是我的判断"
    assert scan.records[0].raw_block.decode("utf-8").startswith("## 09:10")
    assert [issue["code"] for issue in scan.issues] == ["missing_delimiter"]


def test_session_imports_only_real_delta_and_copies_multimodal_assets(tmp_path: Path) -> None:
    vault = make_vault(tmp_path, daily(TEXT_BLOCK))
    private_file(vault / "assets" / "shot.png", b"\x89PNG\r\nreal-shot")
    private_file(vault / "assets" / "voice.m4a", b"real-voice")
    source_before = hashlib.sha256((vault / "2026-08-23.md").read_bytes()).hexdigest()
    store = make_store(tmp_path)
    started = store.start_session(
        vault,
        source_label="Test AISecretary",
        capture_window_start_date="2026-08-23",
    )
    assert started["collection"]["new_capture_count"] == 0

    private_file(vault / "2026-08-23.md", daily(TEXT_BLOCK, SCREENSHOT_BLOCK, VOICE_BLOCK))
    collected = store.collect()
    assert collected["new_capture_count"] == 2
    assert collected["provider_invoked"] is False
    assert collected["formal_vault_write_enabled"] is False

    index = store.files.read_json(f"indexes/{collected['session_id']}.json")
    capture_ids = cast(list[str], index["capture_ids"])
    events = [store.files.read_json(f"captures/{capture_id}.json") for capture_id in capture_ids]
    assert [cast(Mapping[str, Any], event["source"])["source_type"] for event in events] == [
        "screenshot_ocr",
        "voice_transcript",
    ]
    assert len(store.files.list_files("assets")) == 2
    assert source_before != hashlib.sha256((vault / "2026-08-23.md").read_bytes()).hexdigest()

    before_collect = (vault / "2026-08-23.md").read_bytes()
    assert store.collect()["new_capture_count"] == 0
    assert (vault / "2026-08-23.md").read_bytes() == before_collect


def test_include_existing_date_and_export_leave_labels_blank(tmp_path: Path) -> None:
    vault = make_vault(tmp_path, daily(TEXT_BLOCK))
    store = make_store(tmp_path)
    result = store.start_session(
        vault,
        source_label="Test AISecretary",
        include_existing_dates=["2026-08-23"],
        capture_window_start_date="2026-08-23",
    )
    assert result["collection"]["new_capture_count"] == 1
    dataset = store.export_dataset()
    case = cast(Mapping[str, Any], dataset["cases"][0])
    source = cast(Mapping[str, Any], cast(Mapping[str, Any], case["observed_input"])["source"])
    expected = cast(Mapping[str, Any], case["expected"])
    assert source["note"] == "这是我的判断"
    assert expected["processing_route"] is None
    assert expected["should_enter_long_term_memory"] is None
    assert case["review_status"] == "needs_user_review"
    assert dataset["model_generated_labels"] is False
    assert store.export_dataset() == dataset


def test_unavailable_symlink_attachment_fails_closed_during_collection(tmp_path: Path) -> None:
    vault = make_vault(tmp_path, daily(SCREENSHOT_BLOCK))
    target = tmp_path / "outside.png"
    private_file(target, b"outside")
    (vault / "assets" / "shot.png").symlink_to(target)
    store = make_store(tmp_path)
    result = store.start_session(
        vault,
        source_label="Test AISecretary",
        include_existing_dates=["2026-08-23"],
        capture_window_start_date="2026-08-23",
    )
    assert result["collection"]["new_capture_count"] == 0
    assert result["collection"]["scan_issue_count"] == 1
    status = store.status()
    issues = cast(list[Mapping[str, Any]], status["last_scan_issues"])
    assert issues[0]["code"] == "attachment_unavailable"


def test_session_manifest_does_not_enable_provider_or_vault_writes(tmp_path: Path) -> None:
    vault = make_vault(tmp_path, daily())
    store = make_store(tmp_path)
    result = store.start_session(
        vault,
        source_label="Test AISecretary",
        capture_window_start_date="2026-08-23",
    )
    session = cast(Mapping[str, Any], result["session"])
    assert session["provider_enabled"] is False
    assert session["formal_vault_write_enabled"] is False
    pointer = store.files.read_json("indexes/current-session.json")
    assert pointer["session_id"] == session["session_id"]
    assert json.loads(
        (store.files.root / "sessions" / f"{session['session_id']}.json").read_text("utf-8")
    )["source"]["label"] == "Test AISecretary"
