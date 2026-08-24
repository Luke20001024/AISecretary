from __future__ import annotations

import hashlib
import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "chrome-newtab"
PUBLISHED = ROOT / "docs" / "demo"
LEGACY = ROOT / "docs" / "Memento-Cognitive-Home-Standalone.html"
GUIDE = ROOT / "docs" / "index.html"
BUILDER = ROOT / "scripts" / "build_standalone_preview.py"


class SharedDemoRuntimeTests(unittest.TestCase):
    def test_shared_runtime_is_current_and_byte_identical(self) -> None:
        subprocess.run(
            ["python3", str(BUILDER), "--check"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        contract = json.loads((PUBLISHED / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["source"], "chrome-newtab")
        self.assertEqual(contract["entrypoint"], "dashboard.html")
        self.assertEqual(contract["version"], "0.10.1")
        for name in contract["files"]:
            source = SOURCE / name
            published = PUBLISHED / name
            self.assertTrue(source.is_file(), name)
            self.assertTrue(published.is_file(), name)
            self.assertEqual(source.read_bytes(), published.read_bytes(), name)

    def test_guide_and_independent_demo_use_the_same_entrypoint(self) -> None:
        guide = GUIDE.read_text(encoding="utf-8")
        self.assertIn('<iframe src="demo/dashboard.html"', guide)
        self.assertGreaterEqual(guide.count('href="demo/dashboard.html"'), 2)
        self.assertNotIn('<iframe src="Memento-Cognitive-Home-Standalone.html"', guide)

        legacy = LEGACY.read_text(encoding="utf-8")
        self.assertIn('url=demo/dashboard.html', legacy)
        self.assertIn("location.replace('demo/dashboard.html'", legacy)
        self.assertNotIn('data-memento-source="dashboard.js"', legacy)

    def test_every_dashboard_dependency_is_in_the_shared_runtime(self) -> None:
        html = (PUBLISHED / "dashboard.html").read_text(encoding="utf-8")
        references = re.findall(r'(?:src|href)="([^"?#]+)', html)
        local_references = [
            reference for reference in references
            if not reference.startswith(("http:", "https:", "data:", "#"))
        ]
        for reference in local_references:
            self.assertTrue((PUBLISHED / reference).is_file(), reference)

    def test_runtime_contract_digest_matches_canonical_source(self) -> None:
        contract = json.loads((PUBLISHED / "runtime.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256()
        for name in contract["files"]:
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update((SOURCE / name).read_bytes())
            digest.update(b"\0")
        self.assertEqual(digest.hexdigest(), contract["source_sha256"])


if __name__ == "__main__":
    unittest.main()
