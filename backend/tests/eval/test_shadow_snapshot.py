from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

import pytest

from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import sha256_bytes
from memento_backend.evaluation.shadow_snapshot import (
    create_read_only_snapshot,
    verify_read_only_snapshot,
)
from memento_backend.evaluation import shadow_snapshot as snapshot_module


def _directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    return path


def test_snapshot_is_stable_sealed_and_does_not_modify_source(tmp_path: Path) -> None:
    source = _directory(tmp_path / "source")
    output = _directory(tmp_path / "snapshots")
    note = source / "2026-08-23.md"
    note.write_text("- 保留变化发生的理由\n", encoding="utf-8")
    os.chmod(note, 0o600)
    ignored = source / "voice.m4a"
    ignored.write_bytes(b"audio")
    os.chmod(ignored, 0o600)
    before = note.read_bytes()
    before_mode = note.stat().st_mode

    snapshot = create_read_only_snapshot(
        source,
        output,
        source_label="synthetic-vault",
        snapshot_kind="synthetic_fixture",
        created_at="2026-08-23T10:00:00+08:00",
    )

    assert note.read_bytes() == before
    assert note.stat().st_mode == before_mode
    assert snapshot.manifest["file_count"] == 1
    assert snapshot.manifest["excluded_count"] == 1
    assert snapshot.manifest["files"][0]["sha256"] == sha256_bytes(before)
    assert (snapshot.path / "2026-08-23.md").stat().st_mode & 0o222 == 0
    assert snapshot.path.stat().st_mode & 0o222 == 0
    assert verify_read_only_snapshot(snapshot.path).manifest == snapshot.manifest


def test_real_vault_snapshot_requires_explicit_authorization_reference(tmp_path: Path) -> None:
    source = _directory(tmp_path / "source")
    output = _directory(tmp_path / "snapshots")
    (source / "note.md").write_text("hello", encoding="utf-8")
    with pytest.raises(ContractError) as raised:
        create_read_only_snapshot(
            source,
            output,
            source_label="real-vault",
            snapshot_kind="real_vault_snapshot",
            created_at="2026-08-23T10:00:00+08:00",
        )
    assert raised.value.kind == "permission"


def test_snapshot_rejects_symlinks_and_overlapping_output(tmp_path: Path) -> None:
    source = _directory(tmp_path / "source")
    output = _directory(tmp_path / "snapshots")
    (source / "note.md").write_text("hello", encoding="utf-8")
    (source / "alias.md").symlink_to(source / "note.md")
    with pytest.raises(ContractError) as raised:
        create_read_only_snapshot(
            source,
            output,
            source_label="unsafe",
            snapshot_kind="synthetic_fixture",
            created_at="2026-08-23T10:00:00+08:00",
        )
    assert raised.value.kind == "path"

    (source / "alias.md").unlink()
    nested = source / "output"
    nested.mkdir(mode=0o700)
    with pytest.raises(ContractError, match="overlap"):
        create_read_only_snapshot(
            source,
            nested,
            source_label="overlap",
            snapshot_kind="synthetic_fixture",
            created_at="2026-08-23T10:00:00+08:00",
        )


def test_verifier_rejects_a_tampered_snapshot(tmp_path: Path) -> None:
    source = _directory(tmp_path / "source")
    output = _directory(tmp_path / "snapshots")
    (source / "note.md").write_text("hello", encoding="utf-8")
    snapshot = create_read_only_snapshot(
        source,
        output,
        source_label="synthetic",
        snapshot_kind="synthetic_fixture",
        created_at="2026-08-23T10:00:00+08:00",
    )
    target = snapshot.path / "note.md"
    os.chmod(snapshot.path, 0o700)
    os.chmod(target, 0o600)
    target.write_text("tampered", encoding="utf-8")
    os.chmod(target, 0o400)
    os.chmod(snapshot.path, 0o500)
    with pytest.raises(ContractError, match="hash mismatch"):
        verify_read_only_snapshot(snapshot.path)


def test_concurrent_source_change_aborts_snapshot_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _directory(tmp_path / "source")
    output = _directory(tmp_path / "snapshots")
    note = source / "note.md"
    note.write_text("first", encoding="utf-8")
    original_scan = snapshot_module._scan_source
    calls = 0

    def changing_scan(
        root: Path,
        *,
        suffixes: Sequence[str],
        excluded_directories: frozenset[str],
    ) -> tuple[list[dict[str, Any]], int]:
        nonlocal calls
        calls += 1
        if calls == 2:
            note.write_text("changed while copying", encoding="utf-8")
        return original_scan(
            root, suffixes=suffixes, excluded_directories=excluded_directories
        )

    monkeypatch.setattr(snapshot_module, "_scan_source", changing_scan)
    with pytest.raises(ContractError) as raised:
        create_read_only_snapshot(
            source,
            output,
            source_label="concurrent",
            snapshot_kind="synthetic_fixture",
            created_at="2026-08-23T10:00:00+08:00",
        )
    assert raised.value.kind == "conflict"
    assert not any(path.name.startswith("snp_") for path in output.iterdir())
    assert not any(".staging-" in path.name for path in output.iterdir())
