from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from memento_backend.domain.errors import ContractError
from memento_backend.projections.bundle_projector import ProjectionBundle, build_projection_bundle
from memento_backend.storage.atomic import AtomicFileStore
from memento_backend.storage.bundle_store import BundleStore
from tests.fixtures.formal_20d import formal_20d_inputs


def make_store(tmp_path: Path, *, fault_stage: str | None = None) -> BundleStore:
    root = tmp_path / "isolated-v2"
    if not root.exists():
        root.mkdir(mode=0o700)

    def fault(stage: str, identifier: str) -> None:
        del identifier
        if stage == fault_stage:
            raise RuntimeError(f"interrupted at {stage}")

    return BundleStore(AtomicFileStore(root), fault_hook=fault if fault_stage else None)


def bundles() -> tuple[ProjectionBundle, ProjectionBundle]:
    inputs = formal_20d_inputs()
    first = build_projection_bundle(
        inputs,
        as_of="2026-08-18",
        generated_at="2026-08-18T22:00:00+08:00",
    )
    second = build_projection_bundle(
        inputs,
        as_of="2026-08-18",
        generated_at="2026-08-18T22:05:00+08:00",
        previous_bundle_sha256=first.bundle_sha256,
    )
    return first, second


def test_bundle_publication_exposes_one_validated_pointer(tmp_path: Path) -> None:
    first, second = bundles()
    store = make_store(tmp_path)
    pointer1 = store.publish(first)
    assert pointer1["bundle_sha256"] == first.bundle_sha256
    assert store.load_current() == first
    pointer2 = store.publish(second)
    assert pointer2["sequence"] == 2
    assert store.load_current() == second
    assert store.publish(second) == pointer2


def test_staging_interruption_never_exposes_partial_bundle_and_retry_finishes(tmp_path: Path) -> None:
    first, _ = bundles()
    interrupted = make_store(tmp_path, fault_stage="after_stage")
    with pytest.raises(RuntimeError, match="after_stage"):
        interrupted.publish(first)
    assert make_store(tmp_path).load_current() is None

    recovered = make_store(tmp_path)
    recovered.publish(first)
    assert recovered.load_current() == first


def test_sealed_unpublished_bundle_does_not_replace_current(tmp_path: Path) -> None:
    first, second = bundles()
    store = make_store(tmp_path)
    store.publish(first)
    interrupted = make_store(tmp_path, fault_stage="after_bundle_seal")
    with pytest.raises(RuntimeError, match="after_bundle_seal"):
        interrupted.publish(second)
    assert make_store(tmp_path).load_current() == first


def test_complete_publication_is_recoverable_after_pointer_interruption(tmp_path: Path) -> None:
    first, second = bundles()
    make_store(tmp_path).publish(first)
    interrupted = make_store(tmp_path, fault_stage="after_publication")
    with pytest.raises(RuntimeError, match="after_publication"):
        interrupted.publish(second)
    assert make_store(tmp_path).load_current() == first

    store = make_store(tmp_path)
    recovered = store.recover_current()
    assert recovered is not None
    assert recovered["bundle_sha256"] == second.bundle_sha256
    assert store.load_current() == second


def test_corrupt_latest_bundle_rolls_back_to_last_valid_publication(tmp_path: Path) -> None:
    first, second = bundles()
    store = make_store(tmp_path)
    store.publish(first)
    store.publish(second)
    home_path = f"projections/bundles/{second.manifest['bundle_id']}/projections/home.json"
    tampered = dict(second.projection("projections/home.json"))
    tampered["status_message"] = "tampered"
    (store.files.root / home_path).write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises((ContractError, ValueError)):
        store.load_current()

    pointer = store.recover_current()
    assert pointer is not None
    assert pointer["bundle_sha256"] == first.bundle_sha256
    assert store.load_current() == first


def test_sealed_bundle_rejects_files_missing_from_its_manifest(tmp_path: Path) -> None:
    first, _ = bundles()
    store = make_store(tmp_path)
    store.publish(first)
    bundle_root = (
        store.files.root
        / "projections"
        / "bundles"
        / str(first.manifest["bundle_id"])
    )
    undeclared = bundle_root / ".undeclared.json"
    undeclared.write_text("{}\n", encoding="utf-8")
    undeclared.chmod(0o600)

    with pytest.raises(ContractError, match="manifest does not cover") as raised:
        store.load_current()
    assert raised.value.kind == "evidence"


def test_explicit_rollback_and_bundle_chain_cas(tmp_path: Path) -> None:
    first, second = bundles()
    store = make_store(tmp_path)
    store.publish(first)
    store.publish(second)
    pointer = store.rollback_to_previous(published_at="2026-08-18T22:10:00+08:00")
    assert pointer["sequence"] == 3
    assert store.load_current() == first

    stale = build_projection_bundle(
        formal_20d_inputs(),
        as_of="2026-08-18",
        generated_at="2026-08-18T22:15:00+08:00",
        previous_bundle_sha256=second.bundle_sha256,
    )
    with pytest.raises(ContractError) as raised:
        store.publish(stale)
    assert raised.value.kind == "conflict"


def test_concurrent_bundle_candidates_have_one_pointer_winner(tmp_path: Path) -> None:
    first, second = bundles()
    store = make_store(tmp_path)
    store.publish(first)
    third = build_projection_bundle(
        formal_20d_inputs(),
        as_of="2026-08-18",
        generated_at="2026-08-18T22:06:00+08:00",
        previous_bundle_sha256=first.bundle_sha256,
    )
    barrier = threading.Barrier(2)

    def attempt(value: ProjectionBundle) -> str:
        barrier.wait()
        try:
            make_store(tmp_path).publish(value)
        except ContractError as exc:
            return exc.kind
        return "published"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, (second, third)))
    assert sorted(outcomes) == ["conflict", "published"]
    current = store.load_current()
    assert current is not None
    assert current.bundle_sha256 in {second.bundle_sha256, third.bundle_sha256}
