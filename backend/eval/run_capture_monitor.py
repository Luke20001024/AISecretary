#!/usr/bin/env python3
"""Run the local read-only monitor for real shortcut captures."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path
from typing import Optional, Sequence

from memento_backend.domain.errors import ContractError
from memento_backend.evaluation.real_capture_dataset import RealCaptureDatasetStore, secure_workspace
from memento_backend.interfaces.capture_dataset_monitor import CaptureDatasetMonitorApp, make_server


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Run Memento's real shortcut dataset monitor")
    value.add_argument("--workspace", required=True, help="Existing isolated capture dataset workspace")
    value.add_argument("--port", type=int, default=4317)
    value.add_argument("--open", action="store_true")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    if args.port < 0 or args.port > 65535:
        parser().error("--port must be between 0 and 65535")
    try:
        workspace = secure_workspace(Path(args.workspace))
        app = CaptureDatasetMonitorApp(RealCaptureDatasetStore(workspace))
        server = make_server(app, "127.0.0.1", args.port)
    except (OSError, ContractError) as exc:
        print(f"无法启动：{exc}", file=sys.stderr)
        return 2
    raw_host, port = server.server_address
    host = raw_host.decode("ascii") if isinstance(raw_host, bytes) else raw_host
    url = f"http://{host}:{port}/"
    print("Memento 真实样本监看页已启动")
    print(f"打开地址：{url}")
    print("继续使用现有快捷键，页面每 5 秒收取一次新增记录")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\n监看页已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
