#!/usr/bin/env python3
"""Build the current Memento cognitive-home preview as one offline HTML file."""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_HTML = ROOT / "chrome-newtab-demo" / "dashboard.html"
DEFAULT_OUTPUT = ROOT / "docs" / "Memento-Cognitive-Home-Standalone.html"

STYLESHEET_RE = re.compile(
    r'<link\s+rel="stylesheet"\s+href="(?P<src>[^"]+)"\s*>', re.IGNORECASE
)
SCRIPT_RE = re.compile(
    r'<script\s+src="(?P<src>[^"]+)"\s*>\s*</script>', re.IGNORECASE
)


def source_path(reference: str) -> Path:
    relative = reference.split("?", 1)[0]
    path = (SOURCE_HTML.parent / relative).resolve()
    path.relative_to(SOURCE_HTML.parent.resolve())
    if not path.is_file():
        raise FileNotFoundError(f"missing preview dependency: {relative}")
    return path


def safe_inline_style(text: str) -> str:
    return text.replace("</style", "<\\/style")


def safe_inline_script(text: str) -> str:
    return text.replace("</script", "<\\/script")


def build() -> bytes:
    html = SOURCE_HTML.read_text(encoding="utf-8")
    dependencies: list[Path] = [SOURCE_HTML]
    script_hashes: list[str] = []

    def replace_stylesheet(match: re.Match[str]) -> str:
        path = source_path(match.group("src"))
        dependencies.append(path)
        css = safe_inline_style(path.read_text(encoding="utf-8"))
        return f'<style data-memento-source="{path.name}">\n{css}\n</style>'

    def replace_script(match: re.Match[str]) -> str:
        path = source_path(match.group("src"))
        dependencies.append(path)
        script = safe_inline_script(path.read_text(encoding="utf-8"))
        inline_text = f"\n{script}\n"
        encoded_hash = base64.b64encode(
            hashlib.sha256(inline_text.encode("utf-8")).digest()
        ).decode("ascii")
        script_hashes.append(f"'sha256-{encoded_hash}'")
        return f'<script data-memento-source="{path.name}">{inline_text}</script>'

    html = STYLESHEET_RE.sub(replace_stylesheet, html)
    html = SCRIPT_RE.sub(replace_script, html)

    digest = hashlib.sha256()
    for path in dependencies:
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    source_sha256 = digest.hexdigest()

    content_security_policy = "; ".join((
        "default-src 'none'",
        f"script-src {' '.join(script_hashes)}",
        "style-src 'unsafe-inline'",
        "img-src data: blob:",
        "media-src data: blob:",
        "font-src data:",
        "connect-src 'none'",
        "object-src 'none'",
        "frame-src 'none'",
        "worker-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
    ))
    metadata = (
        '<meta name="memento-standalone-preview" content="2026-08-24">\n'
        f'  <meta name="memento-source-sha256" content="{source_sha256}">\n'
        '  <meta http-equiv="Content-Security-Policy" '
        f'content="{content_security_policy}">'
    )
    html = html.replace('<meta name="color-scheme" content="light">', (
        '<meta name="color-scheme" content="light">\n  ' + metadata
    ), 1)
    html = html.replace(
        "<title>Memento</title>",
        "<title>Memento · 认知地景离线预览</title>",
        1,
    )
    banner = (
        "<!-- Standalone preview: all runtime assets and the 20-day fixture are "
        "embedded below. No local server is required. -->"
    )
    html = html.replace("<body>", f"<body>\n  {banner}", 1)
    return (html.rstrip() + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    output = args.output.resolve()
    expected = build()
    if args.check:
        if not output.is_file() or output.read_bytes() != expected:
            print(f"standalone preview is stale: {output}")
            return 1
        print(f"standalone preview is current: {output}")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(expected)
    print(f"wrote {output} ({len(expected)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
