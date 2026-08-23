from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Iterator, Mapping, Tuple, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from memento_backend.evaluation.real_capture_dataset import RealCaptureDatasetStore
from memento_backend.interfaces.capture_dataset_monitor import (
    CaptureDatasetMonitorApp,
    make_server,
    monitor_snapshot,
)


def private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)
    return path


def private_file(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    os.chmod(path, 0o600)


def daily(blocks: str = "") -> bytes:
    return ("---\ndate: 2026-08-23\ntype: memento-daily\n---\n" + blocks).encode("utf-8")


def text_block(*, note: bool = False) -> str:
    note_text = "\n> 备注: 这会改变我的产品判断\n" if note else ""
    return f"""
## 10:10 · 周日 · Safari

来自网页的一段客体原文
{note_text}
---
"""


def configured_store(tmp_path: Path, *, include_text: bool = True) -> Tuple[RealCaptureDatasetStore, Path]:
    vault = private_directory(tmp_path / "AISecretary")
    private_directory(vault / "assets")
    private_file(vault / "2026-08-23.md", daily(text_block() if include_text else ""))
    store = RealCaptureDatasetStore(private_directory(tmp_path / "dataset"))
    store.start_session(
        vault,
        source_label="Test AISecretary",
        include_existing_dates=["2026-08-23"] if include_text else [],
        capture_window_start_date="2026-08-23",
    )
    return store, vault


def test_monitor_snapshot_lists_real_input_and_requirement_progress(tmp_path: Path) -> None:
    store, _ = configured_store(tmp_path)
    snapshot = monitor_snapshot(store)
    assert snapshot["capture_count"] == 1
    captures = cast(list[Mapping[str, Any]], snapshot["captures"])
    assert captures[0]["preview"] == "来自网页的一段客体原文"
    assert captures[0]["source_app"] == "Safari"
    requirements = cast(list[Mapping[str, Any]], snapshot["requirements"])
    completed = {item["requirement_id"] for item in requirements if item["complete"]}
    assert completed == {"external_text"}
    assert snapshot["provider_enabled"] is False
    assert snapshot["formal_vault_write_enabled"] is False


@pytest.fixture
def running_monitor(tmp_path: Path) -> Iterator[Tuple[str, RealCaptureDatasetStore, Path]]:
    store, vault = configured_store(tmp_path, include_text=False)
    server = make_server(CaptureDatasetMonitorApp(store), "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = cast(str, server.server_address[0])
    port = server.server_address[1]
    try:
        yield f"http://{host}:{port}", store, vault
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def read_json(request: Request) -> Mapping[str, Any]:
    with urlopen(request, timeout=3) as response:
        return cast(Mapping[str, Any], json.loads(response.read().decode("utf-8")))


def test_monitor_page_collects_existing_shortcut_delta(running_monitor: Tuple[str, RealCaptureDatasetStore, Path]) -> None:
    base, _, vault = running_monitor
    with urlopen(f"{base}/", timeout=3) as response:
        html = response.read().decode("utf-8")
    assert "希望你留下这些样本" in html
    assert "截图" in html

    private_file(vault / "2026-08-23.md", daily(text_block(note=True)))
    value = read_json(
        Request(
            f"{base}/v1/collect",
            data=b"",
            method="POST",
            headers={"Origin": base},
        )
    )
    assert value["collection"]["new_capture_count"] == 1
    monitor = cast(Mapping[str, Any], value["monitor"])
    assert monitor["capture_count"] == 1
    requirements = cast(list[Mapping[str, Any]], monitor["requirements"])
    completed = {item["requirement_id"] for item in requirements if item["complete"]}
    assert "annotated_text" in completed


def test_monitor_rejects_cross_origin_collection(running_monitor: Tuple[str, RealCaptureDatasetStore, Path]) -> None:
    base, _, _ = running_monitor
    request = Request(
        f"{base}/v1/collect",
        data=b"",
        method="POST",
        headers={"Origin": "https://evil.example"},
    )
    with pytest.raises(HTTPError) as forbidden:
        urlopen(request, timeout=3)
    assert forbidden.value.code == 403
