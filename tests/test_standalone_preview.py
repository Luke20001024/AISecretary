from __future__ import annotations

import base64
import hashlib
import re
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREVIEW = ROOT / "docs" / "Memento-Cognitive-Home-Standalone.html"
BUILDER = ROOT / "scripts" / "build_standalone_preview.py"


class _AssetTagParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str | None]] = []
        self.links: list[dict[str, str | None]] = []
        self.images: list[dict[str, str | None]] = []
        self.frames: list[dict[str, str | None]] = []
        self.metas: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script":
            self.scripts.append(attributes)
        if tag == "link":
            self.links.append(attributes)
        if tag == "img":
            self.images.append(attributes)
        if tag in {"iframe", "frame"}:
            self.frames.append(attributes)
        if tag == "meta":
            self.metas.append(attributes)


class StandalonePreviewTests(unittest.TestCase):
    def test_generated_preview_is_current_and_self_contained(self) -> None:
        subprocess.run(
            ["python3", str(BUILDER), "--check"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        html = PREVIEW.read_text(encoding="utf-8")
        self.assertIn('name="memento-standalone-preview"', html)
        self.assertIn('name="memento-source-sha256"', html)
        self.assertIn('data-memento-source="dashboard.css"', html)
        self.assertIn('data-memento-source="cognitive-demo-fixture.js"', html)
        self.assertIn('data-memento-source="dashboard.js"', html)
        parser = _AssetTagParser()
        parser.feed(html)
        self.assertEqual(len(parser.scripts), 2)
        self.assertTrue(all("src" not in script for script in parser.scripts))
        self.assertFalse(any(link.get("rel") == "stylesheet" for link in parser.links))
        self.assertFalse(any((link.get("href") or "").startswith(("http:", "https:")) for link in parser.links))
        self.assertFalse(any((image.get("src") or "").startswith(("http:", "https:")) for image in parser.images))
        self.assertFalse(parser.frames)

        csp_meta = next(
            meta for meta in parser.metas
            if (meta.get("http-equiv") or "").lower() == "content-security-policy"
        )
        csp = csp_meta.get("content") or ""
        self.assertIn("default-src 'none'", csp)
        self.assertIn("connect-src 'none'", csp)
        self.assertNotIn("'unsafe-eval'", csp)
        self.assertNotIn("script-src 'unsafe-inline'", csp)
        inline_scripts = re.findall(
            r'<script data-memento-source="[^"]+">(.*?)</script>',
            html,
            flags=re.DOTALL,
        )
        self.assertEqual(len(inline_scripts), 2)
        for script in inline_scripts:
            digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")
            self.assertIn(f"'sha256-{digest}'", csp)
            subprocess.run(
                ["node", "--check"],
                input=script,
                text=True,
                capture_output=True,
                check=True,
            )


if __name__ == "__main__":
    unittest.main()
