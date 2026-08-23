"""Atomic staging, publication and recovery for ProjectionBundle files."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_json, validate_datetime
from memento_backend.projections.bundle_projector import (
    ProjectionBundle,
    validate_projection_bundle_contract,
)

from .atomic import AtomicFileStore


BundleFaultHook = Callable[[str, str], None]
CURRENT_POINTER_PATH = "projections/current.json"


class BundleStore:
    """Expose a whole projection bundle by replacing one validated pointer."""

    def __init__(self, files: AtomicFileStore, *, fault_hook: Optional[BundleFaultHook] = None) -> None:
        self.files = files
        self._fault_hook = fault_hook
        self.files.ensure_directory("projections/staging")
        self.files.ensure_directory("projections/bundles")
        self.files.ensure_directory("projections/publications")

    def _fault(self, stage: str, identifier: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(stage, identifier)

    def load_current_pointer(self) -> Optional[Mapping[str, Any]]:
        if not self.files.exists(CURRENT_POINTER_PATH):
            return None
        pointer = self.files.read_json(CURRENT_POINTER_PATH)
        validate_contract("projection-current-v1.schema.json", pointer)
        publication = self.files.read_json(str(pointer["publication_path"]))
        validate_contract("projection-publication-v1.schema.json", publication)
        if pointer["publication_path"] != self._publication_path(publication):
            raise ContractError("current projection publication path is inconsistent", kind="evidence")
        if sha256_json(publication) != pointer["publication_sha256"]:
            raise ContractError("current projection publication hash is stale", kind="evidence")
        for field in ("publication_id", "sequence", "bundle_id", "bundle_sha256", "manifest_path", "published_at"):
            if pointer[field] != publication[field]:
                raise ContractError(f"current projection pointer differs at {field}", kind="evidence")
        bundle = self._load_bundle(str(pointer["bundle_id"]), base="projections/bundles")
        if bundle.bundle_sha256 != pointer["bundle_sha256"]:
            raise ContractError("current projection bundle hash is stale", kind="evidence")
        return pointer

    def load_current(self) -> Optional[ProjectionBundle]:
        pointer = self.load_current_pointer()
        if pointer is None:
            return None
        return self._load_bundle(str(pointer["bundle_id"]), base="projections/bundles")

    def publish(self, bundle: ProjectionBundle, *, published_at: Optional[str] = None) -> Mapping[str, Any]:
        validate_projection_bundle_contract(bundle)
        bundle_id = str(bundle.manifest["bundle_id"])
        when = published_at or str(bundle.manifest["generated_at"])
        validate_datetime(when, "published_at")
        with self.files.lock("projection-bundles"):
            current = self.load_current_pointer()
            if current is not None and current["bundle_sha256"] == bundle.bundle_sha256:
                return current
            expected_previous = None if current is None else current["bundle_sha256"]
            if bundle.manifest["previous_bundle_sha256"] != expected_previous:
                raise ContractError("projection bundle compare-and-swap failed", kind="conflict")

            self._stage_and_seal(bundle)
            self._fault("after_bundle_seal", bundle_id)
            sequence = 1 if current is None else int(current["sequence"]) + 1
            previous_publication_sha = None if current is None else current["publication_sha256"]
            proposed_publication = self._publication(
                bundle=bundle,
                sequence=sequence,
                operation="publish",
                previous_publication_sha256=previous_publication_sha,
                published_at=when,
            )
            publication, publication_path = self._append_publication_idempotent(proposed_publication)
            self._fault("after_publication", bundle_id)
            pointer = self._pointer(publication, publication_path)
            self._fault("before_pointer_publish", bundle_id)
            self.files.replace_json(CURRENT_POINTER_PATH, pointer)
            self._fault("after_pointer_publish", bundle_id)
            return pointer

    def rollback_to_previous(self, *, published_at: str) -> Mapping[str, Any]:
        validate_datetime(published_at, "published_at")
        with self.files.lock("projection-bundles"):
            current = self.load_current_pointer()
            if current is None or int(current["sequence"]) <= 1:
                raise ContractError("no previous projection publication exists", kind="not_found")
            history = self._valid_publication_chain()
            previous = next(
                (item for item in reversed(history) if int(item["sequence"]) < int(current["sequence"])),
                None,
            )
            if previous is None:
                raise ContractError("no valid projection rollback target exists", kind="recovery")
            bundle = self._load_bundle(str(previous["bundle_id"]), base="projections/bundles")
            proposed_publication = self._publication(
                bundle=bundle,
                sequence=int(current["sequence"]) + 1,
                operation="rollback",
                previous_publication_sha256=str(current["publication_sha256"]),
                published_at=published_at,
            )
            publication, path = self._append_publication_idempotent(proposed_publication)
            pointer = self._pointer(publication, path)
            self.files.replace_json(CURRENT_POINTER_PATH, pointer)
            return pointer

    def recover_current(self) -> Optional[Mapping[str, Any]]:
        with self.files.lock("projection-bundles"):
            history = self._valid_publication_chain()
            if not history:
                return None
            latest = history[-1]
            path = self._publication_path(latest)
            pointer = self._pointer(latest, path)
            self.files.replace_json(CURRENT_POINTER_PATH, pointer)
            return pointer

    def _stage_and_seal(self, bundle: ProjectionBundle) -> None:
        bundle_id = str(bundle.manifest["bundle_id"])
        sealed = f"projections/bundles/{bundle_id}"
        if self.files.directory_exists(sealed):
            existing = self._load_bundle(bundle_id, base="projections/bundles")
            if existing.bundle_sha256 != bundle.bundle_sha256:
                raise ContractError("bundle id is bound to different projection bytes", kind="conflict")
            return
        stage = f"projections/staging/{bundle_id}"
        self.files.ensure_directory(stage)
        for path, value in sorted(bundle.projections.items()):
            self.files.write_new_json_idempotent(f"{stage}/{path}", value)
        self.files.write_new_json_idempotent(f"{stage}/manifest.json", bundle.manifest)
        staged = self._load_bundle(bundle_id, base="projections/staging")
        if staged.bundle_sha256 != bundle.bundle_sha256:
            raise ContractError("staged projection bundle differs from requested bytes", kind="evidence")
        self._fault("after_stage", bundle_id)
        self.files.rename_directory_new(stage, sealed)

    def _load_bundle(self, bundle_id: str, *, base: str) -> ProjectionBundle:
        bundle_root = f"{base}/{bundle_id}"
        manifest_path = f"{bundle_root}/manifest.json"
        manifest = self.files.read_json(manifest_path)
        validate_contract("projection-bundle-v1.schema.json", manifest)
        if manifest["bundle_id"] != bundle_id:
            raise ContractError("bundle directory and manifest identity differ", kind="evidence")
        expected_files = {manifest_path}
        expected_files.update(
            f"{bundle_root}/{entry['path']}" for entry in manifest["entries"]
        )
        actual_files = set(self.files.list_tree_files(bundle_root))
        if actual_files != expected_files:
            raise ContractError(
                "projection manifest does not cover the sealed bundle directory",
                kind="evidence",
            )
        projections: dict[str, Mapping[str, Any]] = {}
        for entry in manifest["entries"]:
            path = str(entry["path"])
            projections[path] = self.files.read_json(f"{base}/{bundle_id}/{path}")
        bundle = ProjectionBundle(manifest=manifest, projections=projections)
        validate_projection_bundle_contract(bundle)
        return bundle

    def _valid_publication_chain(self) -> list[Mapping[str, Any]]:
        paths = self.files.list_files("projections/publications", suffix=".json")
        valid: list[Mapping[str, Any]] = []
        previous_sha: Optional[str] = None
        expected_sequence = 1
        for path in paths:
            try:
                publication = self.files.read_json(path)
                validate_contract("projection-publication-v1.schema.json", publication)
                if publication["sequence"] != expected_sequence or publication["previous_publication_sha256"] != previous_sha:
                    break
                if path != self._publication_path(publication):
                    break
                bundle = self._load_bundle(str(publication["bundle_id"]), base="projections/bundles")
                if bundle.bundle_sha256 != publication["bundle_sha256"]:
                    break
            except (ContractError, ValueError):
                break
            valid.append(publication)
            previous_sha = sha256_json(publication)
            expected_sequence += 1
        return valid

    def _append_publication_idempotent(
        self,
        publication: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], str]:
        sequence = int(publication["sequence"])
        prefix = f"projections/publications/{sequence:012d}-"
        existing_paths = [
            path
            for path in self.files.list_files("projections/publications", suffix=".json")
            if path.startswith(prefix)
        ]
        if len(existing_paths) > 1:
            raise ContractError("projection publication sequence is ambiguous", kind="recovery")
        if existing_paths:
            existing = self.files.read_json(existing_paths[0])
            if existing != dict(publication):
                raise ContractError("projection publication sequence already exists", kind="conflict")
            return existing, existing_paths[0]
        path = self._publication_path(publication)
        self.files.write_new_json(path, publication)
        return publication, path

    @staticmethod
    def _publication(
        *,
        bundle: ProjectionBundle,
        sequence: int,
        operation: str,
        previous_publication_sha256: Optional[str],
        published_at: str,
    ) -> dict[str, Any]:
        bundle_id = str(bundle.manifest["bundle_id"])
        base = {
            "sequence": sequence,
            "operation": operation,
            "bundle_id": bundle_id,
            "bundle_sha256": bundle.bundle_sha256,
            "manifest_path": f"projections/bundles/{bundle_id}/manifest.json",
            "previous_publication_sha256": previous_publication_sha256,
            "published_at": published_at,
        }
        value = {
            "schema_version": "1.0",
            "kind": "memento_projection_publication",
            "publication_id": make_id("projection_publication", "projection-publication-v1", base),
            **base,
        }
        validate_contract("projection-publication-v1.schema.json", value)
        return value

    @staticmethod
    def _publication_path(publication: Mapping[str, Any]) -> str:
        return f"projections/publications/{int(publication['sequence']):012d}-{publication['publication_id']}.json"

    @staticmethod
    def _pointer(publication: Mapping[str, Any], publication_path: str) -> dict[str, Any]:
        value = {
            "schema_version": "1.0",
            "kind": "memento_projection_current",
            "publication_id": publication["publication_id"],
            "publication_sha256": sha256_json(publication),
            "publication_path": publication_path,
            "sequence": publication["sequence"],
            "bundle_id": publication["bundle_id"],
            "bundle_sha256": publication["bundle_sha256"],
            "manifest_path": publication["manifest_path"],
            "published_at": publication["published_at"],
        }
        validate_contract("projection-current-v1.schema.json", value)
        return value
