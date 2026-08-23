"""Loopback-only monitor for the real shortcut capture dataset.

This surface visualizes captures already written by the installed Memento
Services.  It has no capture controls and no Provider integration.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence, Type, cast
from urllib.parse import urlparse

from memento_backend.domain.errors import ContractError
from memento_backend.evaluation.real_capture_dataset import RealCaptureDatasetStore


SOURCE_TYPE_LABELS = {
    "text": "文字",
    "screenshot_ocr": "截图 · OCR",
    "image_note": "图片 / 截图",
    "voice_transcript": "语音",
    "file_note": "附件",
}


def _preview(raw_block: Any, maximum: int = 220) -> str:
    if not isinstance(raw_block, str):
        return ""
    lines = raw_block.splitlines()
    body: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if index == 0 and stripped.startswith("## "):
            continue
        if stripped == "---" or not stripped:
            continue
        if stripped.startswith("> [原始录音]") or "![原截图]" in stripped:
            continue
        if stripped.startswith("![]("):
            continue
        if stripped.startswith("> 来源:") or stripped.startswith("> 时长:"):
            continue
        if stripped.startswith("> 备注:"):
            continue
        body.append(stripped)
    value = " ".join(body)
    return value if len(value) <= maximum else value[: maximum - 1] + "…"


def _source(event: Mapping[str, Any]) -> Mapping[str, Any]:
    value = event.get("source")
    if not isinstance(value, dict):
        raise ContractError("capture event source is invalid", kind="schema")
    return cast(Mapping[str, Any], value)


def _has_source_type(events: Sequence[Mapping[str, Any]], value: str) -> bool:
    return any(_source(event).get("source_type") == value for event in events)


def _requirements(events: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    external_text = any(
        _source(event).get("source_type") == "text"
        and not _source(event).get("note")
        and _source(event).get("source_app") not in {None, "", "Memento"}
        for event in events
    )
    annotated_text = any(bool(_source(event).get("note")) for event in events)
    read_later = any(_source(event).get("tag") == "下次再读" for event in events)
    definitions = [
        (
            "external_text",
            "客体原文，不加备注",
            "选择一段别人、网页或 AI 的原文，用 ⌃1 留下",
            external_text,
            "⌃1",
        ),
        (
            "annotated_text",
            "客体原文，加上我的判断",
            "选择一段原文，用 ⌃2 补一句你为何留下它",
            annotated_text,
            "⌃2",
        ),
        (
            "read_later",
            "一个稍后再看的链接",
            "选择链接或标题，用 ⌃3 标记为「下次再读」",
            read_later,
            "⌃3",
        ),
        (
            "screenshot_ocr",
            "有大量文字的网页截图",
            "用 ⌃4 截取正文区域，验证 OCR 与原图同时保留",
            _has_source_type(events, "screenshot_ocr"),
            "⌃4",
        ),
        (
            "image_context",
            "文字很少的图表或界面",
            "用 ⌃4 截取图表、产品界面或视觉线索",
            _has_source_type(events, "image_note"),
            "⌃4",
        ),
        (
            "voice",
            "一段未经整理的真实口述",
            "用 ⌃5 说 15–30 秒，尽量保留自然停顿和修正",
            _has_source_type(events, "voice_transcript"),
            "⌃5",
        ),
    ]
    return [
        {
            "requirement_id": requirement_id,
            "title": title,
            "instruction": instruction,
            "complete": complete,
            "shortcut": shortcut,
        }
        for requirement_id, title, instruction, complete, shortcut in definitions
    ]


def monitor_snapshot(store: RealCaptureDatasetStore) -> Mapping[str, Any]:
    status = store.status()
    events = store.capture_events(cast(str, status["session_id"]))
    public_events: list[Mapping[str, Any]] = []
    type_counts: dict[str, int] = {}
    for event in reversed(events):
        source = _source(event)
        source_type = str(source.get("source_type") or "text")
        type_counts[source_type] = type_counts.get(source_type, 0) + 1
        raw_attachments = event.get("attachments")
        attachments = raw_attachments if isinstance(raw_attachments, list) else []
        public_events.append(
            {
                "capture_id": event.get("capture_id"),
                "local_date": source.get("local_date"),
                "time": source.get("time"),
                "source_app": source.get("source_app"),
                "source_type": source_type,
                "source_type_label": SOURCE_TYPE_LABELS.get(source_type, source_type),
                "tag": source.get("tag"),
                "note": source.get("note"),
                "preview": _preview(event.get("raw_block")),
                "attachment_count": len(attachments),
                "entry_sha256": source.get("entry_sha256"),
            }
        )
    requirements = _requirements(events)
    return {
        "schema_version": "1.0",
        "kind": "memento_capture_dataset_monitor",
        "session_id": status.get("session_id"),
        "source_label": cast(Mapping[str, Any], status.get("source", {})).get("label"),
        "capture_count": len(events),
        "completed_requirement_count": sum(1 for item in requirements if item["complete"]),
        "requirement_count": len(requirements),
        "type_counts": type_counts,
        "last_collected_at": status.get("last_collected_at"),
        "scan_issues": status.get("last_scan_issues"),
        "provider_enabled": False,
        "formal_vault_write_enabled": False,
        "requirements": requirements,
        "content_prompts": [
            {
                "title": "一条你当前真正相信的判断",
                "instruction": "请说清楚你现在如何判断，以及它会怎样影响行动",
            },
            {
                "title": "同一判断后来为什么改变",
                "instruction": "补充新证据、旧判断和改变发生的理由，形成一组前后记录",
            },
        ],
        "captures": public_events,
    }


class CaptureDatasetMonitorApp:
    def __init__(self, store: RealCaptureDatasetStore) -> None:
        self.store = store
        self.ui_root = Path(__file__).resolve().parents[1] / "dataset_monitor_ui"

    def handler_class(self) -> Type[BaseHTTPRequestHandler]:
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "MementoCaptureDatasetMonitor/1.0"

            def log_message(self, format: str, *args: Any) -> None:
                super().log_message(format, *args)

            def _host_allowed(self) -> bool:
                return self.headers.get("Host", "").split(":", 1)[0] in {"127.0.0.1", "localhost"}

            def _origin_allowed(self) -> bool:
                origin = self.headers.get("Origin")
                if origin is None:
                    return True
                parsed = urlparse(origin)
                return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}

            def _local_request(self) -> bool:
                return self.client_address[0] in {"127.0.0.1", "::1"} and self._host_allowed()

            def _headers(self, content_type: str, length: int) -> None:
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "connect-src 'self'; img-src 'self'; object-src 'none'; "
                    "base-uri 'none'; frame-ancestors 'none'",
                )

            def _json(self, status_code: int, value: Mapping[str, Any]) -> None:
                body = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
                self.send_response(status_code)
                self._headers("application/json; charset=utf-8", len(body))
                self.end_headers()
                self.wfile.write(body)

            def _error(self, status_code: int, message: str, kind: str) -> None:
                self._json(status_code, {"ok": False, "error": {"kind": kind, "message": message}})

            def _serve_static(self, filename: str) -> None:
                allowed = {
                    "index.html": "text/html; charset=utf-8",
                    "app.js": "text/javascript; charset=utf-8",
                    "styles.css": "text/css; charset=utf-8",
                }
                content_type = allowed.get(filename)
                if content_type is None:
                    self._error(HTTPStatus.NOT_FOUND, "asset not found", "not_found")
                    return
                try:
                    body = (app.ui_root / filename).read_bytes()
                except OSError:
                    self._error(HTTPStatus.NOT_FOUND, "monitor UI is unavailable", "not_found")
                    return
                self.send_response(HTTPStatus.OK)
                self._headers(content_type, len(body))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if not self._local_request():
                    self._error(HTTPStatus.FORBIDDEN, "loopback access only", "authorization")
                    return
                path = urlparse(self.path).path
                if path == "/":
                    self._serve_static("index.html")
                elif path == "/app.js":
                    self._serve_static("app.js")
                elif path == "/styles.css":
                    self._serve_static("styles.css")
                elif path == "/v1/monitor":
                    try:
                        self._json(HTTPStatus.OK, {"ok": True, "monitor": monitor_snapshot(app.store)})
                    except ContractError as exc:
                        self._error(HTTPStatus.BAD_REQUEST, str(exc), exc.kind)
                elif path == "/health":
                    self._json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "status": "ready",
                            "provider_enabled": False,
                            "formal_vault_write_enabled": False,
                        },
                    )
                else:
                    self._error(HTTPStatus.NOT_FOUND, "route not found", "not_found")

            def do_POST(self) -> None:
                if not self._local_request() or not self._origin_allowed():
                    self._error(HTTPStatus.FORBIDDEN, "request origin is not allowed", "authorization")
                    return
                path = urlparse(self.path).path
                try:
                    if path == "/v1/collect":
                        collection = app.store.collect()
                        self._json(
                            HTTPStatus.OK,
                            {"ok": True, "collection": collection, "monitor": monitor_snapshot(app.store)},
                        )
                    elif path == "/v1/export":
                        dataset = app.store.export_dataset()
                        self._json(
                            HTTPStatus.OK,
                            {
                                "ok": True,
                                "dataset": {
                                    "dataset_id": dataset.get("dataset_id"),
                                    "case_count": dataset.get("case_count"),
                                    "model_generated_labels": False,
                                },
                            },
                        )
                    else:
                        self._error(HTTPStatus.NOT_FOUND, "route not found", "not_found")
                except ContractError as exc:
                    self._error(HTTPStatus.BAD_REQUEST, str(exc), exc.kind)

        return Handler


def make_server(
    app: CaptureDatasetMonitorApp,
    host: str,
    port: int,
) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ContractError("dataset monitor may only bind to 127.0.0.1", kind="authorization")
    server = ThreadingHTTPServer((host, port), app.handler_class())
    server.daemon_threads = True
    return server
