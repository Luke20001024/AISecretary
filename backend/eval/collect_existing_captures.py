#!/usr/bin/env python3
"""Collect real shortcut captures from the installed AISecretary Vault."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from memento_backend.domain.errors import ContractError
from memento_backend.evaluation.real_capture_dataset import RealCaptureDatasetStore, secure_workspace


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Build an unlabelled test dataset from existing Memento shortcut captures"
    )
    value.add_argument("--workspace", required=True, help="Owner-only isolated dataset workspace")
    commands = value.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start", help="Record a baseline before using the existing shortcuts")
    start.add_argument("--source", required=True, help="Installed AISecretary Vault path")
    start.add_argument("--source-label", default="Local AISecretary")
    start.add_argument(
        "--from-date",
        help="Earliest daily file to inspect; defaults to the local date when the session starts",
    )
    start.add_argument(
        "--include-date",
        action="append",
        default=[],
        help="Also import records already present on this date; repeatable",
    )

    collect = commands.add_parser("collect", help="Import records added since session start")
    collect.add_argument("--session-id")
    status = commands.add_parser("status", help="Show current collection status")
    status.add_argument("--session-id")
    export = commands.add_parser("export", help="Write an unlabelled dataset draft")
    export.add_argument("--session-id")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        workspace = secure_workspace(Path(args.workspace))
        store = RealCaptureDatasetStore(workspace)
        if args.command == "start":
            result = store.start_session(
                Path(args.source),
                source_label=args.source_label,
                include_existing_dates=args.include_date,
                capture_window_start_date=args.from_date,
            )
        elif args.command == "collect":
            result = store.collect(args.session_id)
        elif args.command == "status":
            result = store.status(args.session_id)
        else:
            result = store.export_dataset(args.session_id)
    except (OSError, ContractError) as exc:
        print(f"收集失败：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
