from __future__ import annotations

import os
from pathlib import Path
from typing import NoReturn

import pytest

from memento_backend.domain.errors import ContractError
from memento_backend.storage.atomic import AtomicFileStore


def secure_root(tmp_path: Path) -> Path:
    root = tmp_path / "isolated-v2"
    root.mkdir(mode=0o700)
    return root


def test_append_only_write_and_atomic_replace_are_durable(tmp_path: Path) -> None:
    store = AtomicFileStore(secure_root(tmp_path))
    store.write_new_json("records/one.json", {"value": 1})
    assert store.read_json("records/one.json") == {"value": 1}
    with pytest.raises(ContractError) as raised:
        store.write_new_json("records/one.json", {"value": 2})
    assert raised.value.kind == "conflict"
    with pytest.raises(ContractError) as immutable:
        store.replace_json("records/one.json", {"value": 2})
    assert immutable.value.kind == "authorization"

    store.replace_json("indexes/head.json", {"generation": 1})
    store.replace_json("indexes/head.json", {"generation": 2})
    assert store.read_json("indexes/head.json") == {"generation": 2}
    assert oct((store.root / "indexes/head.json").stat().st_mode & 0o777) == "0o600"


def test_interruption_before_replace_preserves_previous_value(tmp_path: Path) -> None:
    root = secure_root(tmp_path)
    AtomicFileStore(root).replace_json("indexes/head.json", {"generation": 1})

    def fail(stage: str, relative_path: str) -> None:
        if stage == "before_replace" and relative_path == "indexes/head.json":
            raise RuntimeError("simulated power loss")

    store = AtomicFileStore(root, fault_hook=fail)
    with pytest.raises(RuntimeError, match="power loss"):
        store.replace_json("indexes/head.json", {"generation": 2})
    assert AtomicFileStore(root).read_json("indexes/head.json") == {"generation": 1}
    assert not list((root / "indexes").glob(".memento-v2-*"))


def test_path_symlink_and_permission_checks_fail_closed(tmp_path: Path) -> None:
    root = secure_root(tmp_path)
    store = AtomicFileStore(root)
    for path in ("../escape.json", "/absolute.json", "records\\bad.json"):
        with pytest.raises(ContractError):
            store.write_new_json(path, {"unsafe": True})

    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ContractError) as linked:
        store.write_new_json("linked/value.json", {"unsafe": True})
    assert linked.value.kind == "path"

    insecure = tmp_path / "insecure"
    insecure.mkdir(mode=0o755)
    os.chmod(insecure, 0o755)
    with pytest.raises(ContractError) as permissions:
        AtomicFileStore(insecure)
    assert permissions.value.kind == "permission"


def test_hard_linked_file_is_rejected(tmp_path: Path) -> None:
    root = secure_root(tmp_path)
    store = AtomicFileStore(root)
    store.write_new_json("records/value.json", {"value": 1})
    os.link(root / "records/value.json", root / "records/alias.json")
    with pytest.raises(ContractError) as raised:
        store.read_json("records/value.json")
    assert raised.value.kind == "permission"
