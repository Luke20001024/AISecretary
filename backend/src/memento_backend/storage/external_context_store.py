"""Append-only artefact storage for bounded Context packs and trace source text."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import validate_id

from .atomic import AtomicFileStore


@dataclass(frozen=True)
class ExternalContextStore:
    files: AtomicFileStore

    def __post_init__(self) -> None:
        self.files.ensure_directory("context-packs")
        self.files.ensure_directory("external-sources")

    def save_pack(self, pack: Mapping[str, Any]) -> None:
        validate_contract("context-pack-v1.schema.json", pack)
        pack_id = validate_id("context_pack", pack["pack_id"], "pack_id")
        self.files.write_new_json_idempotent(self._pack_path(pack_id), pack)

    def load_pack(self, pack_id: str) -> Mapping[str, Any]:
        validate_id("context_pack", pack_id, "pack_id")
        pack = self.files.read_json(self._pack_path(pack_id))
        validate_contract("context-pack-v1.schema.json", pack)
        if pack["pack_id"] != pack_id:
            raise ContractError("context pack identity is inconsistent", kind="evidence")
        return pack

    def save_trace_source(self, session_id: str, trace_id: str, content: bytes) -> str:
        validate_id("external_session", session_id, "session_id")
        validate_id("external_trace", trace_id, "trace_id")
        if not content or len(content) > 256 * 1024:
            raise ContractError("external trace source is empty or too large", kind="size")
        path = f"external-sources/{session_id}-{trace_id}.md"
        try:
            self.files.write_new_bytes(path, content)
        except ContractError as exc:
            if exc.kind != "conflict" or self.files.read_bytes(path, max_bytes=256 * 1024) != content:
                raise
        return path

    @staticmethod
    def _pack_path(pack_id: str) -> str:
        return f"context-packs/{pack_id}.json"
