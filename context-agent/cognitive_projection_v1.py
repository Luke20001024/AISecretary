"""Deterministic, model-free projections for Cognitive Secretary V1.

The projector consumes only verified public readers owned by the record,
receipt/action, formal-bundle, and Agent V1 stores.  It never scans candidate
staging or run-private directories.  Formal revisions remain the facts;
landscape snapshots are immutable publications and the home JSON is a
replaceable projection.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import errno
import fcntl
import json
import os
import secrets
import stat
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from agent_v1 import build_agent_profile
from cognitive_v1 import (
    COGNITIVE_ACTION_RE,
    COGNITIVE_SCHEMA_VERSION,
    HOME_PROJECTION_VERSION,
    LANDSCAPE_PROJECTION_VERSION,
    HomeProjection,
    InterpretationReceiptRevision,
    LandscapeSnapshot,
    ObjectRef,
    RelationRevision,
    ReusableMemoryRevision,
    SourceRecordRevision,
    make_landscape_id,
    make_peak_id,
    make_receipt_id,
    persisted_json_bytes,
)
from core import ContractError, canonical_json, sha256_bytes


MAX_JSON_BYTES = 16 * 1024 * 1024
# These values mirror the landscape SVG geometry in chrome-newtab/dashboard.js.
# They are projection-contract inputs: changing the renderer geometry requires
# changing these values and the layout regression tests together.
PEAK_MAP_WIDTH_PX = 956.0
PEAK_MAP_HEIGHT_PX = 390.0
PEAK_LAYOUT_MIN = 0.12
PEAK_LAYOUT_MAX = 0.88
PEAK_LAYOUT_COLUMNS = 49
PEAK_LAYOUT_ROWS = 31
PEAK_LAYOUT_FALLBACK_EXTRA_SLOTS = 8
PEAK_LAYOUT_FALLBACK_MAX_SLOTS = 30
PEAK_LAYOUT_FALLBACK_SEARCH_LIMIT = 50_000
PEAK_HIT_STROKE_WIDTH_PX = 30.0
PEAK_HIT_GAP_PX = 12.0
PEAK_KDE_CLEARANCE_MULTIPLIER = 1.05
PROJECTION_WARNING_CODES = frozenset(
    {
        "review_failed",
        "long_term_failed",
        "landscape_failed",
        "partial_source_unavailable",
    }
)
RECORD_RUNTIME_STATUSES = frozenset({"processing", "no_candidate", "failed"})
RECORD_RUNTIME_ERROR_KINDS = frozenset(
    {
        "provider_error",
        "unknown_attempt",
        "invalid_response",
        "budget_exhausted",
        "stale",
        "runtime",
    }
)
DAILY_RUN_STATUSES = frozenset(
    {
        "not_started",
        "running",
        "committed",
        "committed_with_warnings",
        "no_change",
        "no_candidate",
        "no_records",
        "no_receipts",
        "stale",
        "error",
        "budget_exhausted",
    }
)


class RecordReader(Protocol):
    def list_heads(
        self, *, local_date: str | None = None, include_tombstones: bool = False
    ) -> list[dict[str, Any]]: ...

    def list_head_refs(
        self, *, local_date: str | None = None, include_tombstones: bool = False
    ) -> list[dict[str, Any]]: ...


class ReceiptActionReader(Protocol):
    def load_receipt_head(self, receipt_id: str) -> InterpretationReceiptRevision: ...

    def load_receipt_head_ref(self, receipt_id: str) -> ObjectRef: ...

    def action_watermark(self) -> tuple[Sequence[Any], str]: ...


class FormalReader(Protocol):
    def load_catalog(self) -> dict[str, Any]: ...

    def load_day_bundle_ref(self, local_date: str) -> ObjectRef | None: ...

    def load_day_manifest(self, local_date: str) -> dict[str, Any] | None: ...

    def list_active_memories(self) -> tuple[ReusableMemoryRevision, ...]: ...

    def list_active_relations(self) -> tuple[RelationRevision, ...]: ...


@dataclass(frozen=True)
class ProjectionPublication:
    landscape: LandscapeSnapshot
    landscape_path: Path
    home: HomeProjection
    home_path: Path


def _now_text(now: dt.datetime | None) -> str:
    value = now or dt.datetime.now().astimezone()
    if value.tzinfo is None:
        raise ContractError("now 必须带时区", kind="runtime")
    return value.isoformat(timespec="seconds")


def _date(value: str) -> str:
    try:
        parsed = dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ContractError("local_date 必须是有效 YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ContractError("local_date 必须是有效 YYYY-MM-DD")
    return value


def _sha(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ContractError(f"{name} 必须是 SHA-256", kind="evidence")
    return value


def _json_hash(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _same_ref(left: ObjectRef, right: ObjectRef) -> bool:
    return left.to_dict() == right.to_dict()


def _object_ref(value: ObjectRef | Mapping[str, Any], name: str) -> ObjectRef:
    try:
        return value if isinstance(value, ObjectRef) else ObjectRef.from_dict(value)
    except ContractError as exc:
        raise ContractError(f"{name} 无效", kind="evidence") from exc


@lru_cache(maxsize=8192)
def _stable_axis(identifier: str, axis: str) -> float:
    raw = int(sha256_bytes(f"{axis}:{identifier}".encode("utf-8"))[:12], 16)
    return 0.12 + 0.76 * (raw / float(0xFFFFFFFFFFFF))


def _clamp(value: float) -> float:
    return min(0.94, max(0.06, value))


@dataclass(frozen=True)
class _PeakLayoutSpec:
    identifier: str
    evidence_count: int
    elevation: float
    previous_position: tuple[float, float] | None


def _peak_pair_key(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left < right else (right, left)


def _peak_close_allowed_pairs(
    understanding_ids: Sequence[str],
    reusable_memory_ids: Sequence[str],
    relations: Sequence[RelationRevision],
) -> frozenset[tuple[str, str]]:
    """Return only the formal graph paths that permit visual proximity.

    A direct understanding-to-understanding relation qualifies.  Two
    understandings also qualify when each has a direct relation to the same
    reusable-memory head.  Longer paths and memory-to-memory bridges do not.
    """

    understandings = frozenset(understanding_ids)
    reusable_memories = frozenset(reusable_memory_ids)
    close_allowed: set[tuple[str, str]] = set()
    memory_neighbors: dict[str, set[str]] = {
        identifier: set() for identifier in reusable_memories
    }
    for relation in relations:
        if relation.status != "active":
            continue
        endpoints = (relation.from_ref.id, relation.to_ref.id)
        understanding_endpoints = sorted(
            identifier for identifier in endpoints if identifier in understandings
        )
        if len(understanding_endpoints) == 2:
            close_allowed.add(
                _peak_pair_key(
                    understanding_endpoints[0], understanding_endpoints[1]
                )
            )
        reusable_endpoints = (
            identifier for identifier in endpoints if identifier in reusable_memories
        )
        for reusable_id in reusable_endpoints:
            memory_neighbors[reusable_id].update(understanding_endpoints)
    for neighbors in memory_neighbors.values():
        ordered = sorted(neighbors)
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                close_allowed.add(_peak_pair_key(left, right))
    return frozenset(close_allowed)


@lru_cache(maxsize=8192)
def _peak_kde_sigmas(spec: _PeakLayoutSpec) -> tuple[float, float]:
    bounded = min(spec.evidence_count, 18)
    return 92.0 + bounded * 2.6, 58.0 + bounded * 1.8


@lru_cache(maxsize=8192)
def _peak_hit_radii(spec: _PeakLayoutSpec) -> tuple[float, float]:
    bounded = min(spec.evidence_count, 12)
    outer_x = 74.0 + spec.elevation * 42.0 + bounded * 2.4
    outer_y = 48.0 + spec.elevation * 31.0 + bounded * 1.7
    stroke_radius = PEAK_HIT_STROKE_WIDTH_PX / 2.0
    return (
        max(66.0, outer_x * 0.72) + stroke_radius,
        max(42.0, outer_y * 0.7) + stroke_radius,
    )


def _peak_hit_clearance(
    left: _PeakLayoutSpec,
    left_position: tuple[float, float],
    right: _PeakLayoutSpec,
    right_position: tuple[float, float],
) -> float:
    left_rx, left_ry = _peak_hit_radii(left)
    right_rx, right_ry = _peak_hit_radii(right)
    dx = abs(left_position[0] - right_position[0]) * PEAK_MAP_WIDTH_PX
    dy = abs(left_position[1] - right_position[1]) * PEAK_MAP_HEIGHT_PX
    return max(
        dx / (left_rx + right_rx + PEAK_HIT_GAP_PX),
        dy / (left_ry + right_ry + PEAK_HIT_GAP_PX),
    )


def _peak_kde_clearance_squared(
    left: _PeakLayoutSpec,
    left_position: tuple[float, float],
    right: _PeakLayoutSpec,
    right_position: tuple[float, float],
) -> float:
    left_sigma_x, left_sigma_y = _peak_kde_sigmas(left)
    right_sigma_x, right_sigma_y = _peak_kde_sigmas(right)
    dx = abs(left_position[0] - right_position[0]) * PEAK_MAP_WIDTH_PX
    dy = abs(left_position[1] - right_position[1]) * PEAK_MAP_HEIGHT_PX
    x_scale = PEAK_KDE_CLEARANCE_MULTIPLIER * (
        left_sigma_x + right_sigma_x
    )
    y_scale = PEAK_KDE_CLEARANCE_MULTIPLIER * (
        left_sigma_y + right_sigma_y
    )
    return (dx / x_scale) ** 2 + (dy / y_scale) ** 2


def _peak_position_is_safe(
    spec: _PeakLayoutSpec,
    position: tuple[float, float],
    placed_specs: Mapping[str, _PeakLayoutSpec],
    placed_positions: Mapping[str, tuple[float, float]],
    close_allowed: frozenset[tuple[str, str]],
) -> bool:
    if not (
        PEAK_LAYOUT_MIN <= position[0] <= PEAK_LAYOUT_MAX
        and PEAK_LAYOUT_MIN <= position[1] <= PEAK_LAYOUT_MAX
    ):
        return False
    for identifier, other_position in placed_positions.items():
        other = placed_specs[identifier]
        if _peak_hit_clearance(spec, position, other, other_position) < 1.0:
            return False
        if (
            _peak_pair_key(spec.identifier, identifier) not in close_allowed
            and _peak_kde_clearance_squared(
                spec, position, other, other_position
            )
            < 1.0
        ):
            return False
    return True


def _peak_layout_axis(index: int, count: int) -> float:
    width = PEAK_LAYOUT_MAX - PEAK_LAYOUT_MIN

    if count == 1:
        return PEAK_LAYOUT_MIN + width / 2.0
    if index == 0:
        return PEAK_LAYOUT_MIN
    if index == count - 1:
        return PEAK_LAYOUT_MAX
    return PEAK_LAYOUT_MIN + width * index / (count - 1)


@lru_cache(maxsize=1)
def _peak_layout_candidates() -> tuple[tuple[float, float], ...]:
    return tuple(
        (
            _peak_layout_axis(column, PEAK_LAYOUT_COLUMNS),
            _peak_layout_axis(row, PEAK_LAYOUT_ROWS),
        )
        for row in range(PEAK_LAYOUT_ROWS)
        for column in range(PEAK_LAYOUT_COLUMNS)
    )


@lru_cache(maxsize=32)
def _peak_layout_fallback_templates(
    minimum_slots: int,
) -> tuple[tuple[tuple[float, float], ...], ...]:
    if minimum_slots < 1 or minimum_slots > PEAK_LAYOUT_FALLBACK_MAX_SLOTS:
        return ()
    maximum_slots = min(
        PEAK_LAYOUT_FALLBACK_MAX_SLOTS,
        minimum_slots + PEAK_LAYOUT_FALLBACK_EXTRA_SLOTS,
    )
    target_aspect = PEAK_MAP_WIDTH_PX / PEAK_MAP_HEIGHT_PX
    shapes = [
        (columns, rows)
        for columns in range(1, maximum_slots + 1)
        for rows in range(1, maximum_slots + 1)
        if minimum_slots <= columns * rows <= maximum_slots
    ]
    shapes.sort(
        key=lambda shape: (
            shape[0] * shape[1] - minimum_slots,
            abs(shape[0] / shape[1] - target_aspect),
            shape[1],
            shape[0],
        )
    )
    return tuple(
        tuple(
            (
                _peak_layout_axis(column, columns),
                _peak_layout_axis(row, rows),
            )
            for row in range(rows)
            for column in range(columns)
        )
        for columns, rows in shapes
    )


def _peak_candidate_score(
    spec: _PeakLayoutSpec,
    candidate: tuple[float, float],
    placed_specs: Mapping[str, _PeakLayoutSpec],
    placed_positions: Mapping[str, tuple[float, float]],
    close_allowed: frozenset[tuple[str, str]],
) -> tuple[float, float, float, str]:
    related = [
        position
        for identifier, position in placed_positions.items()
        if _peak_pair_key(spec.identifier, identifier) in close_allowed
    ]
    unrelated_clearances = [
        _peak_kde_clearance_squared(
            spec, candidate, placed_specs[identifier], position
        )
        for identifier, position in placed_positions.items()
        if _peak_pair_key(spec.identifier, identifier) not in close_allowed
    ]
    minimum_unrelated_clearance = (
        min(unrelated_clearances) if unrelated_clearances else 0.0
    )
    anchor = (
        _stable_axis(spec.identifier, "peak-x"),
        _stable_axis(spec.identifier, "peak-y"),
    )
    anchor_distance = (
        (candidate[0] - anchor[0]) * PEAK_MAP_WIDTH_PX
    ) ** 2 + ((candidate[1] - anchor[1]) * PEAK_MAP_HEIGHT_PX) ** 2
    tie_breaker = _peak_candidate_tie_breaker(
        spec.identifier, candidate[0], candidate[1]
    )
    if related:
        centroid_x = sum(position[0] for position in related) / len(related)
        centroid_y = sum(position[1] for position in related) / len(related)
        related_distance = (
            (candidate[0] - centroid_x) * PEAK_MAP_WIDTH_PX
        ) ** 2 + ((candidate[1] - centroid_y) * PEAK_MAP_HEIGHT_PX) ** 2
        return (
            related_distance,
            -minimum_unrelated_clearance,
            anchor_distance,
            tie_breaker,
        )
    return (
        -minimum_unrelated_clearance,
        anchor_distance,
        0.0,
        tie_breaker,
    )


@lru_cache(maxsize=32768)
def _peak_candidate_tie_breaker(
    identifier: str, x: float, y: float
) -> str:
    return sha256_bytes(
        f"peak-layout-v1:{identifier}:{x:.12f}:{y:.12f}".encode("utf-8")
    )


def _peak_fallback_positions(
    specs: Sequence[_PeakLayoutSpec],
    close_allowed: frozenset[tuple[str, str]],
    locked_specs: Mapping[str, _PeakLayoutSpec],
    locked_positions: Mapping[str, tuple[float, float]],
) -> dict[str, tuple[float, float]] | None:
    def footprint(spec: _PeakLayoutSpec) -> float:
        radius_x, radius_y = _peak_hit_radii(spec)
        return radius_x * radius_y

    ordered = sorted(
        specs,
        key=lambda spec: (-footprint(spec), spec.identifier),
    )
    remaining_search_steps = PEAK_LAYOUT_FALLBACK_SEARCH_LIMIT
    for template in _peak_layout_fallback_templates(len(ordered)):
        placed_specs = dict(locked_specs)
        placed_positions = dict(locked_positions)
        available = list(template)
        for spec in ordered:
            valid_candidates = [
                candidate
                for candidate in available
                if _peak_position_is_safe(
                    spec,
                    candidate,
                    placed_specs,
                    placed_positions,
                    close_allowed,
                )
            ]
            if not valid_candidates:
                break
            position = min(
                valid_candidates,
                key=lambda candidate: _peak_candidate_score(
                    spec,
                    candidate,
                    placed_specs,
                    placed_positions,
                    close_allowed,
                ),
            )
            available.remove(position)
            placed_specs[spec.identifier] = spec
            placed_positions[spec.identifier] = position
        else:
            return placed_positions

        failed_states: set[tuple[tuple[str, float, float], ...]] = set()

        def search(
            remaining: tuple[_PeakLayoutSpec, ...],
            remaining_positions: tuple[tuple[float, float], ...],
        ) -> dict[str, tuple[float, float]] | None:
            nonlocal remaining_search_steps
            if not remaining:
                return dict(placed_positions)
            state = tuple(
                sorted(
                    (identifier, position[0], position[1])
                    for identifier, position in placed_positions.items()
                    if identifier not in locked_positions
                )
            )
            if state in failed_states:
                return None
            choices: list[
                tuple[
                    int,
                    float,
                    str,
                    _PeakLayoutSpec,
                    list[tuple[float, float]],
                ]
            ] = []
            for spec in remaining:
                valid_candidates = [
                    candidate
                    for candidate in remaining_positions
                    if _peak_position_is_safe(
                        spec,
                        candidate,
                        placed_specs,
                        placed_positions,
                        close_allowed,
                    )
                ]
                if not valid_candidates:
                    failed_states.add(state)
                    return None
                choices.append(
                    (
                        len(valid_candidates),
                        -footprint(spec),
                        spec.identifier,
                        spec,
                        valid_candidates,
                    )
                )
            _, _, _, selected, candidates = min(
                choices,
                key=lambda row: (row[0], row[1], row[2]),
            )
            candidates.sort(
                key=lambda candidate: _peak_candidate_score(
                    selected,
                    candidate,
                    placed_specs,
                    placed_positions,
                    close_allowed,
                )
            )
            next_remaining = tuple(
                spec for spec in remaining if spec.identifier != selected.identifier
            )
            for position in candidates:
                if remaining_search_steps <= 0:
                    return None
                remaining_search_steps -= 1
                placed_specs[selected.identifier] = selected
                placed_positions[selected.identifier] = position
                result = search(
                    next_remaining,
                    tuple(
                        candidate
                        for candidate in remaining_positions
                        if candidate != position
                    ),
                )
                if result is not None:
                    return result
                del placed_specs[selected.identifier]
                del placed_positions[selected.identifier]
            failed_states.add(state)
            return None

        placed_specs = dict(locked_specs)
        placed_positions = dict(locked_positions)
        result = search(tuple(ordered), tuple(template))
        if result is not None:
            return result
        if remaining_search_steps <= 0:
            break
    return None


def _resolve_peak_positions(
    specs: Sequence[_PeakLayoutSpec],
    close_allowed: frozenset[tuple[str, str]],
) -> dict[str, tuple[float, float]]:
    """Keep safe prior positions and deterministically reflow conflicts."""

    locked_specs: dict[str, _PeakLayoutSpec] = {}
    locked_positions: dict[str, tuple[float, float]] = {}
    movable: list[_PeakLayoutSpec] = []
    for spec in sorted(
        (row for row in specs if row.previous_position is not None),
        key=lambda row: row.identifier,
    ):
        if _peak_position_is_safe(
            spec,
            spec.previous_position,
            locked_specs,
            locked_positions,
            close_allowed,
        ):
            locked_specs[spec.identifier] = spec
            locked_positions[spec.identifier] = spec.previous_position
        else:
            movable.append(spec)
    movable.extend(
        sorted(
            (row for row in specs if row.previous_position is None),
            key=lambda row: row.identifier,
        )
    )

    placed_specs = dict(locked_specs)
    placed_positions = dict(locked_positions)
    failed = False
    for spec in movable:
        preferred = spec.previous_position or (
            _stable_axis(spec.identifier, "peak-x"),
            _stable_axis(spec.identifier, "peak-y"),
        )
        related_to_placed = any(
            _peak_pair_key(spec.identifier, identifier) in close_allowed
            for identifier in placed_positions
        )
        keep_preferred = (
            spec.previous_position is not None
            or not placed_positions
            or related_to_placed
        )
        if keep_preferred and _peak_position_is_safe(
            spec, preferred, placed_specs, placed_positions, close_allowed
        ):
            position = preferred
        else:
            valid_candidates = (
                candidate
                for candidate in _peak_layout_candidates()
                if _peak_position_is_safe(
                    spec,
                    candidate,
                    placed_specs,
                    placed_positions,
                    close_allowed,
                )
            )
            position = min(
                valid_candidates,
                key=lambda candidate: _peak_candidate_score(
                    spec,
                    candidate,
                    placed_specs,
                    placed_positions,
                    close_allowed,
                ),
                default=None,
            )
            if position is None:
                failed = True
                break
        placed_specs[spec.identifier] = spec
        placed_positions[spec.identifier] = position
    if failed:
        fallback = _peak_fallback_positions(
            movable,
            close_allowed,
            locked_specs,
            locked_positions,
        )
        if fallback is None:
            raise ContractError(
                "认知地景峰值坐标无法满足点击与关系间距合同",
                kind="evidence",
            )
        placed_positions = fallback
    return {
        identifier: placed_positions[identifier]
        for identifier in sorted(placed_positions)
    }


class _ProjectionLock:
    def __init__(self, publisher: "CognitiveProjectionPublisher") -> None:
        self.publisher = publisher
        self.descriptor: int | None = None

    def __enter__(self) -> "_ProjectionLock":
        self.publisher._ensure_layout()
        path = self.publisher.lock_path
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise ContractError("投影锁无法安全打开", kind="evidence") from exc
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.getuid()
            or details.st_nlink != 1
            or stat.S_IMODE(details.st_mode) & 0o077
        ):
            os.close(descriptor)
            raise ContractError("投影锁必须是当前用户的单链接私有文件", kind="evidence")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        self.descriptor = descriptor
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.descriptor is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


class CognitiveProjectionPublisher:
    """Build and securely publish landscape and home projections."""

    def __init__(
        self,
        vault: Path,
        *,
        record_store: RecordReader,
        action_store: ReceiptActionReader,
        bundle_store: FormalReader,
        state_root: Path | None = None,
        profile_loader: Callable[[Path], Mapping[str, Any]] = build_agent_profile,
    ) -> None:
        try:
            resolved = vault.expanduser().resolve(strict=True)
        except OSError as exc:
            raise ContractError("Vault 目录不存在", kind="not_found") from exc
        if not resolved.is_dir():
            raise ContractError("Vault 目录不存在", kind="not_found")
        root = state_root or (
            resolved / ".context-agent" / "cognitive-secretary-v1"
        )
        if not root.is_absolute():
            root = resolved / root
        candidate = root.parent.resolve() / root.name
        try:
            candidate.relative_to(resolved)
        except ValueError as exc:
            raise ContractError("state_root 必须位于 Vault 内", kind="evidence") from exc
        self.vault = resolved
        self.root = candidate
        self.record_store = record_store
        self.action_store = action_store
        self.bundle_store = bundle_store
        self.profile_loader = profile_loader
        self.snapshots_dir = self.root / "landscape-snapshots"
        self.projections_dir = self.root / "projections"
        self.home_path = self.projections_dir / "home_projection.json"
        self.landscape_head_path = self.projections_dir / "landscape-head.json"
        self.locks_dir = self.root / "locks"
        self.lock_path = self.locks_dir / "projection.lock"

    # --------------------------------------------------------------
    # Hardened projection persistence
    # --------------------------------------------------------------
    def _secure_directory(self, path: Path) -> None:
        try:
            path.relative_to(self.vault)
        except ValueError as exc:
            raise ContractError("投影路径越过 Vault 边界", kind="evidence") from exc
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ContractError(f"投影目录不安全：{path.name}", kind="evidence")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        details = path.lstat()
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) & 0o077
        ):
            raise ContractError(f"投影目录必须仅当前用户可访问：{path.name}", kind="evidence")

    def _ensure_layout(self) -> None:
        for path in (
            self.root.parent,
            self.root,
            self.snapshots_dir,
            self.projections_dir,
            self.locks_dir,
        ):
            self._secure_directory(path)

    def _safe_read_bytes(self, path: Path, *, name: str) -> bytes:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError as exc:
            raise ContractError(f"{name} 不存在", kind="not_found") from exc
        except OSError as exc:
            kind = "evidence" if exc.errno in {errno.ELOOP, errno.EISDIR} else "runtime"
            raise ContractError(f"{name} 无法安全读取", kind=kind) from exc
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) & 0o077
                or before.st_size > MAX_JSON_BYTES
            ):
                raise ContractError(f"{name} 必须是当前用户的单链接私有文件", kind="evidence")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(1024 * 1024, MAX_JSON_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_JSON_BYTES:
                    raise ContractError(f"{name} 超过限制", kind="evidence")
            after = os.fstat(descriptor)
            stable = (
                "st_dev",
                "st_ino",
                "st_uid",
                "st_mode",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            if any(getattr(before, key) != getattr(after, key) for key in stable):
                raise ContractError(f"{name} 读取期间发生变化", kind="stale")
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def _fsync_directory(self, path: Path) -> None:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags)
        try:
            with contextlib.suppress(OSError):
                os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _safe_write_immutable(self, path: Path, payload: bytes) -> None:
        self._secure_directory(path.parent)
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise ContractError("不可变地景快照已存在", kind="conflict") from exc
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("地景快照写入失败")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._fsync_directory(path.parent)

    def _safe_replace(self, path: Path, payload: bytes) -> None:
        self._secure_directory(path.parent)
        if path.is_symlink():
            raise ContractError(f"拒绝覆盖符号链接：{path.name}", kind="evidence")
        if path.exists():
            details = path.lstat()
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.getuid()
                or details.st_nlink != 1
                or stat.S_IMODE(details.st_mode) & 0o077
            ):
                raise ContractError(f"拒绝覆盖不安全文件：{path.name}", kind="evidence")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()

    # --------------------------------------------------------------
    # Verified input snapshots
    # --------------------------------------------------------------
    def _record_heads(
        self, *, local_date: str | None = None
    ) -> tuple[tuple[SourceRecordRevision, ObjectRef], ...]:
        raw_heads = self.record_store.list_heads(
            local_date=local_date, include_tombstones=True
        )
        raw_refs = self.record_store.list_head_refs(
            local_date=local_date, include_tombstones=True
        )
        refs = {_object_ref(row, "record head ref").id: _object_ref(row, "record head ref") for row in raw_refs}
        heads: list[tuple[SourceRecordRevision, ObjectRef]] = []
        for raw in raw_heads:
            head = SourceRecordRevision.from_dict(raw)
            ref = refs.get(head.record_id)
            if ref is None or ref.kind != "source_record" or ref.revision != head.revision:
                raise ContractError("record heads 与 refs 不一致", kind="stale")
            heads.append((head, ref))
        if len(heads) != len(refs):
            raise ContractError("record heads 与 refs 不一致", kind="stale")
        return tuple(sorted(heads, key=lambda row: (row[0].captured_at, row[0].record_id)))

    def _profile(self, injected: Mapping[str, Any] | None) -> dict[str, Any]:
        raw = dict(injected) if injected is not None else dict(self.profile_loader(self.vault))
        if not isinstance(raw.get("memories"), list):
            raise ContractError("Agent profile.memories 无效", kind="evidence")
        _sha(raw.get("profile_sha256"), "Agent profile.profile_sha256")
        seen: set[str] = set()
        for memory in raw["memories"]:
            if not isinstance(memory, dict):
                raise ContractError("Agent profile memory 无效", kind="evidence")
            identifier = memory.get("memory_id")
            if (
                not isinstance(identifier, str)
                or not identifier.startswith("mem_")
                or len(identifier) != 28
                or any(character not in "0123456789abcdef" for character in identifier[4:])
                or identifier in seen
            ):
                raise ContractError("Agent profile memory_id 无效", kind="evidence")
            seen.add(identifier)
            if memory.get("status") != "active":
                raise ContractError("Agent profile 只能投影 active memory", kind="evidence")
            if type(memory.get("revision")) is not int or memory["revision"] < 0:
                raise ContractError("Agent profile revision 无效", kind="evidence")
            _sha(memory.get("revision_sha256"), "Agent profile revision_sha256")
            if not isinstance(memory.get("evidence", []), list) or not isinstance(
                memory.get("counterevidence", []), list
            ):
                raise ContractError("Agent profile evidence 无效", kind="evidence")
        return raw

    def _current_receipt(
        self, source_ref: ObjectRef
    ) -> tuple[InterpretationReceiptRevision, ObjectRef] | None:
        receipt_id = make_receipt_id(source_ref.id)
        try:
            receipt = self.action_store.load_receipt_head(receipt_id)
            receipt_ref = _object_ref(
                self.action_store.load_receipt_head_ref(receipt_id), "receipt head ref"
            )
        except ContractError as exc:
            if exc.kind == "not_found":
                return None
            raise
        if (
            receipt.receipt_id != receipt_id
            or receipt_ref.kind != "interpretation_receipt"
            or receipt_ref.id != receipt_id
            or receipt_ref.revision != receipt.revision
            or receipt_ref.revision_sha256 != receipt.sha256
        ):
            raise ContractError("receipt head 与 ref 不一致", kind="evidence")
        if (
            not _same_ref(receipt.record_ref, source_ref)
            and receipt.status != "original_only"
        ):
            return None
        if receipt.status == "tombstone":
            return None
        return receipt, receipt_ref

    def _action_watermark(self) -> str:
        refs, digest = self.action_store.action_watermark()
        _sha(digest, "user action watermark")
        normalized: list[dict[str, str]] = []
        for row in refs:
            identifier = getattr(row, "action_id", None)
            row_sha = getattr(row, "sha256", None)
            if not isinstance(identifier, str) or not COGNITIVE_ACTION_RE.fullmatch(identifier):
                raise ContractError("user action ref 无效", kind="evidence")
            normalized.append({"id": identifier, "sha256": _sha(row_sha, "action ref sha256")})
        if _json_hash(normalized) != digest:
            raise ContractError("user action watermark 与 refs 不一致", kind="evidence")
        return digest

    def _terminal_source_state(
        self,
        records: Sequence[tuple[SourceRecordRevision, ObjectRef]],
    ) -> tuple[dict[str, tuple[tuple[int, int], ...]], frozenset[str]]:
        """Return current source spans the user removed from AI derivation.

        Agent V1 history is immutable and may still contain an older active
        understanding while its dependency rebuild is pending.  The public
        projection applies the current receipt head as an immediate,
        conservative overlay: any understanding citing one of these exact
        source ranges stays out of the active landscape.
        """

        ranges: dict[str, list[tuple[int, int]]] = {}
        record_ids: set[str] = set()
        for record, source_ref in records:
            receipt_id = make_receipt_id(record.record_id)
            try:
                receipt = self.action_store.load_receipt_head(receipt_id)
                receipt_ref = _object_ref(
                    self.action_store.load_receipt_head_ref(receipt_id),
                    "terminal receipt head ref",
                )
            except ContractError as exc:
                if exc.kind == "not_found":
                    continue
                raise
            if (
                receipt_ref.kind != "interpretation_receipt"
                or receipt_ref.id != receipt.receipt_id
                or receipt_ref.revision != receipt.revision
                or receipt_ref.revision_sha256 != receipt.sha256
            ):
                raise ContractError("terminal receipt head 与ref 不一致", kind="evidence")
            if receipt.status not in {"original_only", "tombstone"}:
                continue
            # User terminal receipts are record-level and intentionally
            # survive later source revisions of the same stable record id.
            # The formal overlay therefore keys on record identity, not on
            # the receipt's older source revision.
            record_ids.add(record.record_id)
            ranges.setdefault(record.source_file, []).append(
                (record.line_start, record.line_end)
            )
        return (
            {
                file: tuple(sorted(file_ranges))
                for file, file_ranges in sorted(ranges.items())
            },
            frozenset(record_ids),
        )

    @staticmethod
    def _formal_object_uses_terminal_source(
        value: ReusableMemoryRevision | RelationRevision,
        terminal_record_ids: frozenset[str],
    ) -> bool:
        return any(
            span.record_id in terminal_record_ids for span in value.source_spans
        )

    def _apply_terminal_formal_overlay(
        self,
        memories: Sequence[ReusableMemoryRevision],
        relations: Sequence[RelationRevision],
        memory_refs: Mapping[str, ObjectRef],
        relation_refs: Mapping[str, ObjectRef],
        understanding_refs: Mapping[str, ObjectRef],
        terminal_record_ids: frozenset[str],
    ) -> tuple[
        tuple[ReusableMemoryRevision, ...],
        tuple[RelationRevision, ...],
        dict[str, ObjectRef],
        dict[str, ObjectRef],
    ]:
        """Hide current formal objects that still depend on a terminal source.

        The immutable catalogue remains untouched.  This projection overlay
        closes the interval between a user's original-only/tombstone action
        and the next formal cascade revision.
        """

        visible_memories = tuple(
            memory
            for memory in memories
            if not self._formal_object_uses_terminal_source(
                memory, terminal_record_ids
            )
        )
        visible_memory_refs = {
            memory.memory_id: memory_refs[memory.memory_id]
            for memory in visible_memories
        }
        valid_endpoints = set(visible_memory_refs) | set(understanding_refs)
        visible_relations = tuple(
            relation
            for relation in relations
            if relation.from_ref.id in valid_endpoints
            and relation.to_ref.id in valid_endpoints
            and not self._formal_object_uses_terminal_source(
                relation, terminal_record_ids
            )
        )
        visible_relation_refs = {
            relation.relation_id: relation_refs[relation.relation_id]
            for relation in visible_relations
        }
        return (
            visible_memories,
            visible_relations,
            visible_memory_refs,
            visible_relation_refs,
        )

    @staticmethod
    def _understanding_uses_terminal_source(
        memory: Mapping[str, Any],
        terminal_ranges: Mapping[str, Sequence[tuple[int, int]]],
    ) -> bool:
        for evidence in list(memory.get("evidence", [])) + list(
            memory.get("counterevidence", [])
        ):
            if not isinstance(evidence, Mapping):
                raise ContractError("Agent profile evidence 无效", kind="evidence")
            file = evidence.get("file")
            line = evidence.get("line")
            if not isinstance(file, str) or type(line) is not int:
                raise ContractError("Agent profile evidence 位置无效", kind="evidence")
            if any(start <= line <= end for start, end in terminal_ranges.get(file, ())):
                return True
        return False

    def _formal_heads(
        self,
        current_sources: Mapping[str, ObjectRef],
        understandings: Mapping[str, ObjectRef],
    ) -> tuple[
        tuple[ReusableMemoryRevision, ...],
        tuple[RelationRevision, ...],
        dict[str, ObjectRef],
        dict[str, ObjectRef],
        dict[str, Any],
    ]:
        catalog = self.bundle_store.load_catalog()
        if not isinstance(catalog, dict):
            raise ContractError("formal catalog 无效", kind="evidence")
        catalog_memory_refs = {
            ref.id: ref
            for ref in (
                _object_ref(row, "catalog memory ref")
                for row in catalog.get("reusable_memories", [])
            )
        }
        catalog_relation_refs = {
            ref.id: ref
            for ref in (
                _object_ref(row, "catalog relation ref")
                for row in catalog.get("relations", [])
            )
        }
        if any(ref.kind != "reusable_memory" for ref in catalog_memory_refs.values()):
            raise ContractError("catalog memory ref kind 无效", kind="evidence")
        if any(ref.kind != "relation" for ref in catalog_relation_refs.values()):
            raise ContractError("catalog relation ref kind 无效", kind="evidence")
        active_memories: dict[str, ReusableMemoryRevision] = {}
        memory_refs: dict[str, ObjectRef] = {}
        for memory in self.bundle_store.list_active_memories():
            if memory.status != "active":
                continue
            ref = catalog_memory_refs.get(memory.memory_id)
            if ref is None or ref.revision != memory.revision or ref.revision_sha256 != memory.sha256:
                raise ContractError("formal memory head 与 catalog 不一致", kind="evidence")
            # A source edit or tombstone invalidates the entire old derived
            # memory until a new formal revision is committed.
            if any(
                current_sources.get(span.record_id)
                != ObjectRef(
                    "source_record",
                    span.record_id,
                    span.record_revision,
                    span.record_revision_sha256,
                )
                for span in memory.source_spans
            ):
                continue
            active_memories[memory.memory_id] = memory
            memory_refs[memory.memory_id] = ref
        active_relations: dict[str, RelationRevision] = {}
        relation_refs: dict[str, ObjectRef] = {}
        valid_endpoints = {**memory_refs, **understandings}
        for relation in self.bundle_store.list_active_relations():
            if relation.status != "active":
                continue
            ref = catalog_relation_refs.get(relation.relation_id)
            if ref is None or ref.revision != relation.revision or ref.revision_sha256 != relation.sha256:
                raise ContractError("formal relation head 与 catalog 不一致", kind="evidence")
            if (
                valid_endpoints.get(relation.from_ref.id) != relation.from_ref
                or valid_endpoints.get(relation.to_ref.id) != relation.to_ref
            ):
                continue
            if any(
                current_sources.get(span.record_id)
                != ObjectRef(
                    "source_record",
                    span.record_id,
                    span.record_revision,
                    span.record_revision_sha256,
                )
                for span in relation.source_spans
            ):
                continue
            active_relations[relation.relation_id] = relation
            relation_refs[relation.relation_id] = ref
        return (
            tuple(active_memories[key] for key in sorted(active_memories)),
            tuple(active_relations[key] for key in sorted(active_relations)),
            memory_refs,
            relation_refs,
            catalog,
        )

    def _understandings(
        self, profile: Mapping[str, Any]
    ) -> tuple[dict[str, Mapping[str, Any]], dict[str, ObjectRef]]:
        rows: dict[str, Mapping[str, Any]] = {}
        refs: dict[str, ObjectRef] = {}
        for memory in profile["memories"]:
            # revision 0 is the old Self Reflection projection, not an active
            # formal Agent V1 revision, so it cannot become a formal peak.
            if memory["revision"] < 1:
                continue
            ref = ObjectRef(
                "understanding",
                memory["memory_id"],
                memory["revision"],
                memory["revision_sha256"],
            )
            rows[memory["memory_id"]] = memory
            refs[memory["memory_id"]] = ref
        return rows, refs

    # --------------------------------------------------------------
    # Landscape
    # --------------------------------------------------------------
    def _load_landscape_head(self) -> tuple[LandscapeSnapshot, str] | None:
        if not self.landscape_head_path.exists() and not self.landscape_head_path.is_symlink():
            return None
        raw = self._safe_read_bytes(self.landscape_head_path, name="landscape head")
        try:
            head = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("landscape head JSON 损坏", kind="evidence") from exc
        if not isinstance(head, dict) or frozenset(head) != frozenset(
            {"schema_version", "kind", "snapshot_id", "snapshot_sha256"}
        ):
            raise ContractError("landscape head 字段无效", kind="evidence")
        if head["schema_version"] != COGNITIVE_SCHEMA_VERSION or head["kind"] != "memento_landscape_head":
            raise ContractError("landscape head 合同无效", kind="evidence")
        snapshot_path = self.snapshots_dir / f"{head['snapshot_id']}.json"
        payload = self._safe_read_bytes(snapshot_path, name="landscape snapshot")
        digest = sha256_bytes(payload)
        if digest != _sha(head["snapshot_sha256"], "landscape head snapshot_sha256"):
            raise ContractError("landscape head hash 不一致", kind="evidence")
        try:
            snapshot = LandscapeSnapshot.from_dict(json.loads(payload.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, ContractError) as exc:
            raise ContractError("landscape snapshot 损坏", kind="evidence") from exc
        if snapshot.snapshot_id != head["snapshot_id"] or payload != persisted_json_bytes(snapshot):
            raise ContractError("landscape snapshot 字节或身份无效", kind="evidence")
        return snapshot, digest

    def build_landscape(
        self,
        *,
        local_date: str,
        now: dt.datetime | None = None,
        profile: Mapping[str, Any] | None = None,
        publication_nonce: str | None = None,
    ) -> LandscapeSnapshot:
        as_of = _date(local_date)
        created_at = _now_text(now)
        profile_value = self._profile(profile)
        all_records = self._record_heads()
        terminal_ranges, terminal_record_ids = self._terminal_source_state(all_records)
        understanding_rows, understanding_refs = self._understandings(profile_value)
        understanding_rows = {
            identifier: memory
            for identifier, memory in understanding_rows.items()
            if not self._understanding_uses_terminal_source(memory, terminal_ranges)
        }
        understanding_refs = {
            identifier: ref
            for identifier, ref in understanding_refs.items()
            if identifier in understanding_rows
        }
        current_sources = {
            head.record_id: ref
            for head, ref in all_records
            if head.status == "active"
        }
        memories, relations, memory_refs, relation_refs, _ = self._formal_heads(
            current_sources, understanding_refs
        )
        memories, relations, memory_refs, relation_refs = (
            self._apply_terminal_formal_overlay(
                memories,
                relations,
                memory_refs,
                relation_refs,
                understanding_refs,
                terminal_record_ids,
            )
        )
        action_watermark = self._action_watermark()
        input_hashes = {
            "agent_profile_sha256": profile_value["profile_sha256"],
            "reusable_memory_head_sha256": _json_hash(
                [memory_refs[key].to_dict() for key in sorted(memory_refs)]
            ),
            "relation_head_sha256": _json_hash(
                [relation_refs[key].to_dict() for key in sorted(relation_refs)]
            ),
            "user_action_watermark_sha256": action_watermark,
        }
        previous = self._load_landscape_head()
        previous_snapshot = None if previous is None else previous[0]
        previous_sha = None if previous is None else previous[1]
        previous_peaks = (
            {}
            if previous_snapshot is None
            else {row["understanding_ref"]["id"]: row for row in previous_snapshot.peaks}
        )
        previous_nodes = (
            {}
            if previous_snapshot is None
            else {row["memory_ref"]["id"]: row for row in previous_snapshot.nodes}
        )

        peak_specs: list[_PeakLayoutSpec] = []
        for identifier in sorted(understanding_refs):
            memory = understanding_rows[identifier]
            old = previous_peaks.get(identifier)
            evidence_count = len(memory.get("evidence", []))
            elevation = min(1.0, 0.25 + min(evidence_count, 12) / 16.0)
            peak_specs.append(
                _PeakLayoutSpec(
                    identifier=identifier,
                    evidence_count=evidence_count,
                    elevation=elevation,
                    previous_position=None
                    if old is None
                    else (float(old["x"]), float(old["y"])),
                )
            )
        close_allowed = _peak_close_allowed_pairs(
            tuple(understanding_refs), tuple(memory_refs), relations
        )
        peak_positions = _resolve_peak_positions(peak_specs, close_allowed)

        peaks: list[dict[str, Any]] = []
        for spec in sorted(peak_specs, key=lambda row: row.identifier):
            identifier = spec.identifier
            memory = understanding_rows[identifier]
            x, y = peak_positions[identifier]
            counter_count = len(memory.get("counterevidence", []))
            lifecycle = "tension" if memory.get("insight_kind") == "tension" else "active"
            recent_change = str(memory.get("created_at", ""))[:10] == as_of
            peaks.append(
                {
                    "peak_id": make_peak_id(identifier),
                    "understanding_ref": understanding_refs[identifier].to_dict(),
                    "x": x,
                    "y": y,
                    "elevation": spec.elevation,
                    "evidence_count": spec.evidence_count,
                    "counterevidence_count": counter_count,
                    "recent_change": recent_change,
                    "lifecycle": lifecycle,
                }
            )

        connected_peaks: dict[str, set[str]] = {identifier: set() for identifier in memory_refs}
        for relation in relations:
            endpoints = (relation.from_ref.id, relation.to_ref.id)
            reusable_ids = [identifier for identifier in endpoints if identifier in memory_refs]
            understanding_ids = [identifier for identifier in endpoints if identifier in understanding_refs]
            for reusable_id in reusable_ids:
                connected_peaks[reusable_id].update(understanding_ids)

        nodes: list[dict[str, Any]] = []
        node_positions: dict[str, tuple[float, float]] = {}
        memory_by_id = {memory.memory_id: memory for memory in memories}
        for identifier in sorted(memory_refs):
            old = previous_nodes.get(identifier)
            linked = sorted(connected_peaks[identifier])
            if old is not None:
                x, y = float(old["x"]), float(old["y"])
            elif linked:
                base_x = sum(peak_positions[row][0] for row in linked) / len(linked)
                base_y = sum(peak_positions[row][1] for row in linked) / len(linked)
                x = _clamp(base_x + (_stable_axis(identifier, "node-x") - 0.5) * 0.10)
                y = _clamp(base_y + (_stable_axis(identifier, "node-y") - 0.5) * 0.10)
            else:
                x = _stable_axis(identifier, "node-x")
                y = _stable_axis(identifier, "node-y")
            node_positions[identifier] = (x, y)
            nodes.append(
                {
                    "memory_ref": memory_refs[identifier].to_dict(),
                    "x": x,
                    "y": y,
                    "state": "committed",
                    "recent": memory_by_id[identifier].created_at[:10] == as_of,
                }
            )

        edges = [
            {
                "relation_ref": relation_refs[relation.relation_id].to_dict(),
                "from_id": relation.from_ref.id,
                "to_id": relation.to_ref.id,
                "type": relation.type,
            }
            for relation in relations
        ]
        nonce = publication_nonce or secrets.token_hex(24)
        return LandscapeSnapshot(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_landscape_snapshot",
            snapshot_id=make_landscape_id(input_hashes, nonce),
            created_at=created_at,
            as_of=as_of,
            projection_version=LANDSCAPE_PROJECTION_VERSION,
            input_hashes=input_hashes,
            summary={
                "active_understandings": len(peaks),
                "recent_changes": sum(1 for row in peaks if row["recent_change"]),
                "observing_candidates": 0,
            },
            terrain={
                "algorithm_version": "stable-anchor-kde-v1",
                "grid_size": 96,
                "contour_levels": 18,
                "coordinate_space": "normalized_0_1",
            },
            peaks=tuple(peaks),
            nodes=tuple(nodes),
            edges=tuple(edges),
            previous_snapshot_sha256=previous_sha,
        )

    def publish_landscape(
        self,
        *,
        local_date: str,
        now: dt.datetime | None = None,
        profile: Mapping[str, Any] | None = None,
        publication_nonce: str | None = None,
    ) -> tuple[LandscapeSnapshot, Path]:
        with _ProjectionLock(self):
            snapshot = self.build_landscape(
                local_date=local_date,
                now=now,
                profile=profile,
                publication_nonce=publication_nonce,
            )
            path = self.snapshots_dir / f"{snapshot.snapshot_id}.json"
            payload = persisted_json_bytes(snapshot)
            self._safe_write_immutable(path, payload)
            digest = sha256_bytes(payload)
            head = {
                "schema_version": COGNITIVE_SCHEMA_VERSION,
                "kind": "memento_landscape_head",
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_sha256": digest,
            }
            self._safe_replace(self.landscape_head_path, persisted_json_bytes(head))
            return snapshot, path

    # --------------------------------------------------------------
    # Home
    # --------------------------------------------------------------
    def _validate_schedule(self, value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping) or frozenset(value) != frozenset(
            {"enabled", "hour", "minute", "next_due_at", "last_run_status"}
        ):
            raise ContractError("schedule 字段无效")
        result = dict(value)
        if (
            type(result["enabled"]) is not bool
            or type(result["hour"]) is not int
            or type(result["minute"]) is not int
            or not 0 <= result["hour"] <= 23
            or not 0 <= result["minute"] <= 59
            or result["last_run_status"] not in DAILY_RUN_STATUSES - {"running"}
        ):
            raise ContractError("schedule 值无效")
        try:
            due = dt.datetime.fromisoformat(str(result["next_due_at"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractError("schedule.next_due_at 无效") from exc
        if due.tzinfo is None:
            raise ContractError("schedule.next_due_at 必须带时区")
        return result

    def _validate_runtime_statuses(
        self,
        value: Mapping[str, Mapping[str, Any]] | None,
        active_today_ids: set[str],
    ) -> dict[str, dict[str, Any]]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ContractError("record_runtime_statuses 必须是 object")
        result: dict[str, dict[str, Any]] = {}
        for record_id, raw in value.items():
            if record_id not in active_today_ids:
                raise ContractError("runtime status 必须绑定当日 active record", kind="evidence")
            if not isinstance(raw, Mapping) or frozenset(raw) != frozenset(
                {"status", "error_kind"}
            ):
                raise ContractError("runtime status 字段无效")
            status = raw["status"]
            error_kind = raw["error_kind"]
            if status not in RECORD_RUNTIME_STATUSES:
                raise ContractError("runtime status 只允许 processing/no_candidate/failed")
            if status in {"processing", "no_candidate"} and error_kind is not None:
                raise ContractError(f"{status} 不得携带 error_kind")
            if status == "failed" and error_kind not in RECORD_RUNTIME_ERROR_KINDS:
                raise ContractError("failed.error_kind 不在 allowlist")
            result[record_id] = {"status": status, "error_kind": error_kind}
        return result

    def build_home(
        self,
        *,
        local_date: str,
        landscape: LandscapeSnapshot,
        schedule: Mapping[str, Any],
        warnings: Sequence[str] = (),
        record_runtime_statuses: Mapping[str, Mapping[str, Any]] | None = None,
        now: dt.datetime | None = None,
        profile: Mapping[str, Any] | None = None,
    ) -> HomeProjection:
        date = _date(local_date)
        generated_at = _now_text(now)
        schedule_value = self._validate_schedule(schedule)
        warning_values = tuple(warnings)
        if len(warning_values) != len(set(warning_values)) or any(
            row not in PROJECTION_WARNING_CODES for row in warning_values
        ):
            raise ContractError("warnings 含未授权值")
        profile_value = self._profile(profile)
        if landscape.input_hashes["agent_profile_sha256"] != profile_value["profile_sha256"]:
            raise ContractError("landscape 与 Agent profile 不一致", kind="stale")
        all_records = self._record_heads()
        terminal_ranges, terminal_record_ids = self._terminal_source_state(all_records)
        current_sources = {
            head.record_id: ref
            for head, ref in all_records
            if head.status == "active"
        }
        today = tuple(
            (head, ref)
            for head, ref in all_records
            if head.status == "active" and head.local_date == date
        )
        runtime_statuses = self._validate_runtime_statuses(
            record_runtime_statuses, {head.record_id for head, _ in today}
        )
        understanding_rows, understanding_refs = self._understandings(profile_value)
        understanding_rows = {
            identifier: memory
            for identifier, memory in understanding_rows.items()
            if not self._understanding_uses_terminal_source(memory, terminal_ranges)
        }
        understanding_refs = {
            identifier: ref
            for identifier, ref in understanding_refs.items()
            if identifier in understanding_rows
        }
        memories, relations, memory_refs, relation_refs, catalog = self._formal_heads(
            current_sources, understanding_refs
        )
        memories, relations, memory_refs, relation_refs = (
            self._apply_terminal_formal_overlay(
                memories,
                relations,
                memory_refs,
                relation_refs,
                understanding_refs,
                terminal_record_ids,
            )
        )
        current_action_watermark = self._action_watermark()
        expected_landscape_inputs = {
            "agent_profile_sha256": profile_value["profile_sha256"],
            "reusable_memory_head_sha256": _json_hash(
                [memory_refs[key].to_dict() for key in sorted(memory_refs)]
            ),
            "relation_head_sha256": _json_hash(
                [relation_refs[key].to_dict() for key in sorted(relation_refs)]
            ),
            "user_action_watermark_sha256": current_action_watermark,
        }
        if dict(landscape.input_hashes) != expected_landscape_inputs:
            raise ContractError("landscape 输入已过期", kind="stale")
        memory_by_record: dict[str, set[str]] = {}
        for memory in memories:
            for span in memory.source_spans:
                memory_by_record.setdefault(span.record_id, set()).add(memory.memory_id)
        understanding_by_memory: dict[str, set[str]] = {}
        for relation in relations:
            endpoints = (relation.from_ref.id, relation.to_ref.id)
            for memory_id in endpoints:
                if memory_id not in memory_refs:
                    continue
                for understanding_id in endpoints:
                    if understanding_id in understanding_refs:
                        understanding_by_memory.setdefault(memory_id, set()).add(
                            understanding_id
                        )

        manifest = self.bundle_store.load_day_manifest(date)
        bundle_ref = self.bundle_store.load_day_bundle_ref(date)
        merged_source_refs: set[tuple[Any, ...]] = set()
        merged_receipt_refs: set[tuple[Any, ...]] = set()
        candidate_status = schedule_value["last_run_status"]
        if manifest is not None:
            if bundle_ref is None:
                raise ContractError("daily manifest 缺少 catalogue bundle ref", kind="evidence")
            merged_source_refs = {
                tuple(_object_ref(row, "bundle source ref").to_dict().values())
                for row in manifest.get("source_refs", [])
            }
            merged_receipt_refs = {
                tuple(_object_ref(row, "bundle receipt ref").to_dict().values())
                for row in manifest.get("receipt_refs", [])
            }
            committed_status = (
                "committed_with_warnings"
                if manifest.get("warnings")
                else "committed"
            )
        else:
            if bundle_ref is not None:
                raise ContractError("daily bundle ref 缺少 manifest", kind="evidence")
            committed_status = "not_started"
        daily_status = (
            candidate_status
            if candidate_status
            in {
                "no_change",
                "no_candidate",
                "no_records",
                "no_receipts",
                "stale",
                "error",
                "budget_exhausted",
            }
            else committed_status
        )

        receipt_refs: list[ObjectRef] = []
        home_records: list[dict[str, Any]] = []
        interpreted = 0
        merged = 0
        needs_review = 0
        for head, source_ref in today:
            current_receipt = self._current_receipt(source_ref)
            runtime = runtime_statuses.get(head.record_id)
            if current_receipt is not None and runtime is not None:
                raise ContractError("current receipt 不得与 runtime status 并存", kind="stale")
            if current_receipt is None:
                status = "raw_saved" if runtime is None else runtime["status"]
                if status == "no_candidate":
                    interpreted += 1
                receipt_ref = None
                summary = None
                content_types: list[str] = []
                topics: list[str] = []
                purposes: list[str] = []
            else:
                receipt, exact_receipt_ref = current_receipt
                receipt_refs.append(exact_receipt_ref)
                interpreted += 1
                receipt_ref = exact_receipt_ref.to_dict()
                if receipt.status == "original_only":
                    status = "original_only"
                    summary = None
                    content_types = []
                    topics = []
                    purposes = []
                else:
                    status = receipt.status
                    summary = receipt.summary
                    content_types = list(receipt.facets["content_types"])
                    topics = list(receipt.facets["topics"])
                    purposes = list(receipt.facets["purposes"])
                    if status == "needs_review":
                        needs_review += 1
                    source_key = tuple(source_ref.to_dict().values())
                    receipt_key = tuple(exact_receipt_ref.to_dict().values())
                    if (
                        source_key in merged_source_refs
                        and receipt_key in merged_receipt_refs
                    ):
                        status = "merged"
                        merged += 1
            downstream_memories = sorted(memory_by_record.get(head.record_id, set()))
            downstream_understandings = sorted(
                {
                    understanding
                    for memory_id in downstream_memories
                    for understanding in understanding_by_memory.get(memory_id, set())
                }
            )
            if status in {"original_only", "no_candidate"}:
                downstream_memories = []
                downstream_understandings = []
            home_records.append(
                {
                    "record_ref": source_ref.to_dict(),
                    "receipt_ref": receipt_ref,
                    "captured_at": head.captured_at,
                    "source_type": head.source_type,
                    "source_app": head.source_app,
                    "status": status,
                    "summary": summary,
                    "content_types": content_types,
                    "topics": topics,
                    "purposes": purposes,
                    "memory_refs": [
                        memory_refs[identifier].to_dict()
                        for identifier in downstream_memories
                    ],
                    "understanding_refs": [
                        understanding_refs[identifier].to_dict()
                        for identifier in downstream_understandings
                    ],
                }
            )

        landscape_payload = persisted_json_bytes(landscape)
        landscape_sha = sha256_bytes(landscape_payload)
        action_watermark = current_action_watermark
        record_head_hash = _json_hash(
            [ref.to_dict() for _, ref in today]
        )
        receipt_head_hash = _json_hash(
            [ref.to_dict() for ref in sorted(receipt_refs, key=lambda row: row.id)]
        )
        daily_bundle_hash = _json_hash(
            [] if bundle_ref is None else [bundle_ref.to_dict()]
        )
        # Loading the public catalogue above verifies all formal heads; bind its
        # exact visible content so a replacement catalogue invalidates home.
        daily_bundle_hash = _json_hash(
            {"day": daily_bundle_hash, "catalog": catalog}
        )
        return HomeProjection(
            schema_version=COGNITIVE_SCHEMA_VERSION,
            kind="memento_home_projection",
            projection_version=HOME_PROJECTION_VERSION,
            generated_at=generated_at,
            local_date=date,
            input_hashes={
                "record_head_sha256": record_head_hash,
                "receipt_head_sha256": receipt_head_hash,
                "daily_bundle_head_sha256": daily_bundle_hash,
                "agent_profile_sha256": profile_value["profile_sha256"],
                "landscape_snapshot_sha256": landscape_sha,
                "user_action_watermark_sha256": action_watermark,
            },
            landscape_ref={
                "snapshot_id": landscape.snapshot_id,
                "snapshot_sha256": landscape_sha,
            },
            landscape_summary=dict(landscape.summary),
            today_status={
                "saved": len(today),
                "interpreted": interpreted,
                "merged": merged,
                "needs_review": needs_review,
                "daily_run_status": daily_status,
            },
            records=tuple(home_records),
            schedule=schedule_value,
            warnings=warning_values,
        )

    def publish_home(
        self,
        *,
        local_date: str,
        landscape: LandscapeSnapshot,
        schedule: Mapping[str, Any],
        warnings: Sequence[str] = (),
        record_runtime_statuses: Mapping[str, Mapping[str, Any]] | None = None,
        now: dt.datetime | None = None,
        profile: Mapping[str, Any] | None = None,
    ) -> tuple[HomeProjection, Path]:
        with _ProjectionLock(self):
            published = self._load_landscape_head()
            if (
                published is None
                or published[0].snapshot_id != landscape.snapshot_id
                or published[1] != landscape.sha256
            ):
                raise ContractError("home 只能引用当前已发布地景", kind="stale")
            home = self.build_home(
                local_date=local_date,
                landscape=landscape,
                schedule=schedule,
                warnings=warnings,
                record_runtime_statuses=record_runtime_statuses,
                now=now,
                profile=profile,
            )
            if self.home_path.exists() or self.home_path.is_symlink():
                current_payload = self._safe_read_bytes(
                    self.home_path, name="home projection"
                )
                try:
                    current = HomeProjection.from_dict(
                        json.loads(current_payload.decode("utf-8"))
                    )
                except (UnicodeDecodeError, json.JSONDecodeError, ContractError) as exc:
                    raise ContractError("home projection 损坏", kind="evidence") from exc
                current_dict = current.to_dict()
                candidate_dict = home.to_dict()
                current_dict.pop("generated_at")
                candidate_dict.pop("generated_at")
                if current_dict == candidate_dict:
                    return current, self.home_path
            self._safe_replace(self.home_path, persisted_json_bytes(home))
            return home, self.home_path

    def publish(
        self,
        *,
        local_date: str,
        schedule: Mapping[str, Any],
        warnings: Sequence[str] = (),
        record_runtime_statuses: Mapping[str, Mapping[str, Any]] | None = None,
        now: dt.datetime | None = None,
        profile: Mapping[str, Any] | None = None,
        publication_nonce: str | None = None,
    ) -> ProjectionPublication:
        landscape, landscape_path = self.publish_landscape(
            local_date=local_date,
            now=now,
            profile=profile,
            publication_nonce=publication_nonce,
        )
        home, home_path = self.publish_home(
            local_date=local_date,
            landscape=landscape,
            schedule=schedule,
            warnings=warnings,
            record_runtime_statuses=record_runtime_statuses,
            now=now,
            profile=profile,
        )
        return ProjectionPublication(landscape, landscape_path, home, home_path)


__all__ = [
    "CognitiveProjectionPublisher",
    "ProjectionPublication",
    "PROJECTION_WARNING_CODES",
    "RECORD_RUNTIME_ERROR_KINDS",
]
