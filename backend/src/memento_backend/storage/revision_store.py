"""Append-only formal revision store with atomic multi-object visibility."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from memento_backend.contracts.validator import validate_contract
from memento_backend.domain.errors import ContractError
from memento_backend.domain.ids import make_id, sha256_json, validate_datetime, validate_id
from memento_backend.domain.refs import ObjectRef
from memento_backend.domain.revisions import validate_append_only_transition

from .atomic import AtomicFileStore
from .head_index import HEAD_INDEX_PATH, build_head_index, empty_head_index, heads_by_key, validate_head_index


RevisionFaultHook = Callable[[str, str], None]


@dataclass(frozen=True)
class FormalSpec:
    kind_value: str
    ref_kind: str
    id_field: str
    schema_name: str
    directory: str


FORMAL_SPECS: Mapping[str, FormalSpec] = {
    "memento_source_record_revision": FormalSpec("memento_source_record_revision", "source_record", "record_id", "source-record-v2.schema.json", "records"),
    "memento_capture_decision_revision": FormalSpec("memento_capture_decision_revision", "capture_decision", "decision_id", "capture-decision-v1.schema.json", "capture-decisions"),
    "memento_resource_card_revision": FormalSpec("memento_resource_card_revision", "resource_card", "resource_id", "resource-card-v1.schema.json", "resources"),
    "memento_read_later_intent_revision": FormalSpec("memento_read_later_intent_revision", "read_later_intent", "intent_id", "read-later-intent-v1.schema.json", "read-later"),
    "memento_record_interpretation_revision": FormalSpec("memento_record_interpretation_revision", "record_interpretation", "interpretation_id", "record-interpretation-v2.schema.json", "interpretations"),
    "memento_memory_atom_revision": FormalSpec("memento_memory_atom_revision", "memory_atom", "memory_atom_id", "memory-atom-v2.schema.json", "memory-atoms"),
    "memento_relation_revision": FormalSpec("memento_relation_revision", "relation", "relation_id", "relation-v2.schema.json", "relations"),
    "memento_theme_revision": FormalSpec("memento_theme_revision", "theme", "theme_id", "theme-v2.schema.json", "themes"),
    "memento_self_insight_revision": FormalSpec("memento_self_insight_revision", "self_insight", "insight_id", "self-insight-v2.schema.json", "self-insights"),
    "memento_context_grant_revision": FormalSpec("memento_context_grant_revision", "context_grant", "grant_id", "context-grant-v1.schema.json", "context-grants"),
    "memento_external_session_revision": FormalSpec("memento_external_session_revision", "external_session", "session_id", "external-session-v1.schema.json", "external-sessions"),
    "memento_context_read_audit_revision": FormalSpec("memento_context_read_audit_revision", "context_read_audit", "audit_id", "context-read-audit-v1.schema.json", "context-audits"),
    "memento_external_trace_revision": FormalSpec("memento_external_trace_revision", "external_trace", "trace_id", "external-trace-v1.schema.json", "external-traces"),
}
SPECS_BY_REF_KIND: Mapping[str, FormalSpec] = {spec.ref_kind: spec for spec in FORMAL_SPECS.values()}


class RevisionStore:
    """Persist formal objects behind one transaction-manifest visibility boundary."""

    def __init__(self, files: AtomicFileStore, *, fault_hook: Optional[RevisionFaultHook] = None) -> None:
        self.files = files
        self._fault_hook = fault_hook
        for spec in FORMAL_SPECS.values():
            self.files.ensure_directory(f"{spec.directory}/revisions")
        self.files.ensure_directory("transactions")
        self.files.ensure_directory("indexes")

    def _fault(self, stage: str, identifier: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(stage, identifier)

    def load_index(self) -> Mapping[str, Any]:
        if not self.files.exists(HEAD_INDEX_PATH):
            return empty_head_index()
        value = self.files.read_json(HEAD_INDEX_PATH)
        validate_head_index(value)
        return value

    def current_ref(self, object_kind: str, object_id: str) -> Optional[Mapping[str, Any]]:
        validate_id(object_kind, object_id, "object_id")
        entry = heads_by_key(self.load_index()).get((object_kind, object_id))
        return None if entry is None else dict(entry["ref"])

    def load_head(self, object_kind: str, object_id: str) -> Mapping[str, Any]:
        validate_id(object_kind, object_id, "object_id")
        entry = heads_by_key(self.load_index()).get((object_kind, object_id))
        if entry is None:
            raise ContractError("formal object head does not exist", kind="not_found")
        return self._load_member(entry)

    def load_revision(self, object_kind: str, object_id: str, revision: int) -> Mapping[str, Any]:
        """Read one revision only when it belongs to the published head chain."""
        if type(revision) is not int or revision < 1:
            raise ContractError("revision must be a positive integer")
        history = self.list_revisions(object_kind, object_id)
        if revision > len(history):
            raise ContractError("formal object revision does not exist", kind="not_found")
        return history[revision - 1]

    def list_revisions(self, object_kind: str, object_id: str) -> list[Mapping[str, Any]]:
        """Return the visible append-only history, excluding unpublished files."""
        validate_id(object_kind, object_id, "object_id")
        spec = SPECS_BY_REF_KIND.get(object_kind)
        if spec is None:
            raise ContractError("formal object kind is unsupported")
        head = self.current_ref(object_kind, object_id)
        if head is None:
            raise ContractError("formal object head does not exist", kind="not_found")
        history: list[Mapping[str, Any]] = []
        previous_sha256: Optional[str] = None
        for number in range(1, int(head["revision"]) + 1):
            path = self._revision_path(spec, object_id, number)
            value = self.files.read_json(path)
            validate_contract(spec.schema_name, value)
            if value.get(spec.id_field) != object_id or value.get("revision") != number:
                raise ContractError("formal revision history identity is inconsistent", kind="evidence")
            if value.get("previous_revision_sha256") != previous_sha256:
                raise ContractError("formal revision history chain is broken", kind="evidence")
            previous_sha256 = sha256_json(value)
            history.append(value)
        if previous_sha256 != head["revision_sha256"]:
            raise ContractError("formal revision history does not reach current head", kind="evidence")
        return history

    def list_heads(self, object_kind: Optional[str] = None) -> list[Mapping[str, Any]]:
        if object_kind is not None and object_kind not in SPECS_BY_REF_KIND:
            raise ContractError("formal object kind is unsupported")
        index = self.load_index()
        return [
            self._load_member(entry)
            for entry in index["heads"]
            if object_kind is None or entry["object_kind"] == object_kind
        ]

    def commit(
        self,
        value: Mapping[str, Any],
        *,
        expected_ref: Optional[Mapping[str, Any]] = None,
        committed_at: Optional[str] = None,
    ) -> Mapping[str, Any]:
        refs = self.commit_many(
            [value],
            expected_refs=() if expected_ref is None else (expected_ref,),
            committed_at=committed_at,
        )
        return refs[0]

    def commit_many(
        self,
        values: Sequence[Mapping[str, Any]],
        *,
        expected_refs: Sequence[Mapping[str, Any]] = (),
        committed_at: Optional[str] = None,
    ) -> list[Mapping[str, Any]]:
        if not values:
            raise ContractError("revision transaction requires at least one object")
        prepared = [self._prepare(value) for value in values]
        keys = [(spec.ref_kind, str(value[spec.id_field])) for spec, value in prepared]
        if len(set(keys)) != len(keys):
            raise ContractError("revision transaction contains duplicate objects", kind="conflict")
        expected_by_key: dict[tuple[str, str], ObjectRef] = {}
        for raw in expected_refs:
            expected_ref = ObjectRef.from_dict(raw)
            key = (expected_ref.kind, expected_ref.id)
            if key in expected_by_key:
                raise ContractError("expected refs contain duplicates", kind="conflict")
            expected_by_key[key] = expected_ref
        when = committed_at or max(str(value["created_at"]) for _, value in prepared)
        validate_datetime(when, "committed_at")

        with self.files.lock("formal-revisions"):
            index = self.load_index()
            current = heads_by_key(index)
            if set(expected_by_key) - set(keys):
                raise ContractError("expected refs include objects outside this transaction", kind="conflict")
            members: list[dict[str, Any]] = []
            for spec, value in prepared:
                object_id = str(value[spec.id_field])
                key = (spec.ref_kind, object_id)
                previous_entry = current.get(key)
                expected = expected_by_key.get(key)
                if previous_entry is None:
                    if expected is not None or value["revision"] != 1 or value["previous_revision_sha256"] is not None:
                        raise ContractError("new formal object must begin at revision 1", kind="conflict")
                else:
                    if expected is None or expected.to_dict() != previous_entry["ref"]:
                        raise ContractError("formal object compare-and-swap failed", kind="conflict")
                    previous = self._load_member(previous_entry)
                    validate_append_only_transition(
                        previous,
                        value,
                        id_field=spec.id_field,
                        previous_sha256=str(previous_entry["ref"]["revision_sha256"]),
                    )
                ref = {
                    "kind": spec.ref_kind,
                    "id": object_id,
                    "revision": value["revision"],
                    "revision_sha256": sha256_json(value),
                }
                path = self._revision_path(spec, object_id, int(value["revision"]))
                members.append({"object_kind": spec.ref_kind, "object_id": object_id, "ref": ref, "path": path})

            generation = int(index["generation"]) + 1
            transaction_base = {
                "generation": generation,
                "previous_transaction_sha256": index["last_transaction_sha256"],
                "committed_at": when,
                "members": sorted(members, key=lambda member: (member["object_kind"], member["object_id"])),
            }
            transaction_id = make_id("revision_transaction", "revision-transaction-v1", transaction_base)
            transaction = {
                "schema_version": "1.0",
                "kind": "memento_revision_transaction",
                "transaction_id": transaction_id,
                **transaction_base,
            }
            validate_contract("revision-transaction-v1.schema.json", transaction)

            for (spec, value), member in zip(prepared, members):
                del spec
                self.files.write_new_json_idempotent(str(member["path"]), value)
            self._fault("after_revisions", transaction_id)
            transaction_path = f"transactions/{generation:012d}-{transaction_id}.json"
            self.files.write_new_json_idempotent(transaction_path, transaction)
            transaction_sha = sha256_json(transaction)
            self._fault("after_transaction", transaction_id)

            next_heads = dict(current)
            for member in members:
                next_heads[(str(member["object_kind"]), str(member["object_id"]))] = member
            next_index = build_head_index(
                next_heads.values(),
                generation=generation,
                updated_at=when,
                last_transaction_sha256=transaction_sha,
            )
            self._fault("before_head_publish", transaction_id)
            self.files.replace_json(HEAD_INDEX_PATH, next_index)
            self._fault("after_head_publish", transaction_id)
            return [dict(member["ref"]) for member in members]

    def recover(self) -> Mapping[str, Any]:
        with self.files.lock("formal-revisions"):
            rebuilt = self._rebuild_from_transactions()
            current = self.load_index()
            if dict(current) != rebuilt:
                self.files.replace_json(HEAD_INDEX_PATH, rebuilt)
            return rebuilt

    def _rebuild_from_transactions(self) -> dict[str, Any]:
        paths = self.files.list_files("transactions", suffix=".json")
        heads: dict[tuple[str, str], Mapping[str, Any]] = {}
        last_sha: Optional[str] = None
        updated_at = "1970-01-01T00:00:00+00:00"
        expected_generation = 1
        for path in paths:
            transaction = self.files.read_json(path)
            validate_contract("revision-transaction-v1.schema.json", transaction)
            if transaction["generation"] != expected_generation:
                raise ContractError("revision transaction generation has a gap", kind="recovery")
            if transaction["previous_transaction_sha256"] != last_sha:
                raise ContractError("revision transaction chain is broken", kind="recovery")
            for member in transaction["members"]:
                key = (str(member["object_kind"]), str(member["object_id"]))
                previous_entry = heads.get(key)
                current_value = self._load_member(member)
                spec = SPECS_BY_REF_KIND[key[0]]
                if previous_entry is None:
                    if current_value["revision"] != 1 or current_value["previous_revision_sha256"] is not None:
                        raise ContractError("rebuilt object does not begin at revision 1", kind="recovery")
                else:
                    previous_value = self._load_member(previous_entry)
                    validate_append_only_transition(
                        previous_value,
                        current_value,
                        id_field=spec.id_field,
                        previous_sha256=str(previous_entry["ref"]["revision_sha256"]),
                    )
                heads[key] = member
            last_sha = sha256_json(transaction)
            updated_at = str(transaction["committed_at"])
            expected_generation += 1
        return build_head_index(
            heads.values(),
            generation=expected_generation - 1,
            updated_at=updated_at,
            last_transaction_sha256=last_sha,
        )

    def _prepare(self, raw: Mapping[str, Any]) -> tuple[FormalSpec, Mapping[str, Any]]:
        value = dict(raw)
        spec = FORMAL_SPECS.get(str(value.get("kind")))
        if spec is None:
            raise ContractError("formal object kind is unsupported")
        validate_contract(spec.schema_name, value)
        validate_id(spec.ref_kind, value[spec.id_field], spec.id_field)
        return spec, value

    @staticmethod
    def _revision_path(spec: FormalSpec, object_id: str, revision: int) -> str:
        return f"{spec.directory}/revisions/{object_id}.r{revision:06d}.json"

    def _load_member(self, raw: Mapping[str, Any]) -> Mapping[str, Any]:
        object_kind = str(raw["object_kind"])
        object_id = str(raw["object_id"])
        spec = SPECS_BY_REF_KIND.get(object_kind)
        if spec is None:
            raise ContractError("formal member kind is unsupported")
        ref = ObjectRef.from_dict(raw["ref"])
        if ref.kind != object_kind or ref.id != object_id:
            raise ContractError("formal member identity is inconsistent")
        expected_path = self._revision_path(spec, object_id, ref.revision)
        if raw["path"] != expected_path:
            raise ContractError("formal member path is inconsistent", kind="path")
        value = self.files.read_json(expected_path)
        validate_contract(spec.schema_name, value)
        if value.get(spec.id_field) != object_id or value.get("revision") != ref.revision:
            raise ContractError("formal revision identity differs from its reference", kind="evidence")
        if sha256_json(value) != ref.revision_sha256:
            raise ContractError("formal revision hash differs from its reference", kind="evidence")
        return value
