from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "chrome-newtab"
PUBLISHED = ROOT / "docs" / "demo"
LEGACY = ROOT / "docs" / "Memento-Cognitive-Home-Standalone.html"
GUIDE = ROOT / "docs" / "index.html"
BUILDER = ROOT / "scripts" / "build_standalone_preview.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("shared_demo_builder", BUILDER)
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    return builder


class SharedDemoRuntimeTests(unittest.TestCase):
    def test_public_build_rejects_installed_config_without_exposing_tokens(self) -> None:
        builder = load_builder()
        safe = (SOURCE / "cognitive-runtime-config.js").read_text(encoding="utf-8")
        builder.validate_public_runtime_config(safe)
        for config in (
            "{mode:'fixture', token:'', publicPreview:true}",
            "{mode:'v2_live', token:'private-test-token', publicPreview:true}",
            "{mode:'fixture', token:'private-test-token', publicPreview:true}",
            "{mode:'fixture', token:'', publicPreview:false}",
            "{mode:'fixture', token:''}",
            "{mode:'fixture', token:'', token:'private-test-token', publicPreview:true}",
            safe.replace("mode: 'fixture'", "mode: 'v2_live'"),
            safe.replace("token: ''", "token: 'private-test-token'"),
            safe.replace("publicPreview: true", "publicPreview: true, \"publicPreview\": false"),
            safe.replace("token: ''", "token: '', \"token\": 'private-test-token'"),
            safe.replace("baseUrl: ''", "baseUrl: 'http://127.0.0.1:4318'"),
            safe + "\nwindow.MementoRuntimeConfig = {mode:'v2_live'};",
            "/*" + safe + "*/\nwindow.MementoRuntimeConfig = {mode:'v2_live'};",
        ):
            with self.subTest(config=config), self.assertRaises(ValueError) as error:
                builder.validate_public_runtime_config(config)
            self.assertNotIn("private-test-token", str(error.exception))

    def test_output_gate_rejects_unknown_entries_without_writing(self) -> None:
        builder = load_builder()
        for kind in ("directory", "file", "link", "allowed-name-link"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                target = base / "demo"
                target.mkdir()
                entry = target / "dashboard.html"
                external = base / "private.txt"
                external.write_bytes(b"private-marker")
                if kind == "allowed-name-link":
                    entry.symlink_to(external)
                else:
                    entry.write_bytes(b"previous publication")
                    unexpected = target / "unexpected"
                    if kind == "directory":
                        unexpected.mkdir()
                        (unexpected / "private.txt").write_bytes(b"private-marker")
                    elif kind == "link":
                        unexpected.symlink_to(external)
                    else:
                        unexpected.write_bytes(b"private-marker")
                with mock.patch.object(builder, "OUTPUT_DIR", target):
                    outputs = {entry: b"new publication"}
                    with redirect_stdout(io.StringIO()):
                        self.assertEqual(builder.check(outputs), 1)
                    with self.assertRaises(ValueError):
                        builder.write(outputs)
                self.assertEqual(external.read_bytes(), b"private-marker")
                if kind == "allowed-name-link":
                    self.assertTrue(entry.is_symlink())
                else:
                    self.assertEqual(entry.read_bytes(), b"previous publication")
                    self.assertTrue(unexpected.exists())

    def test_output_gate_rejects_linked_root_and_legacy_redirect(self) -> None:
        builder = load_builder()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            external = base / "external"
            external.mkdir()
            linked_root = base / "demo"
            linked_root.symlink_to(external, target_is_directory=True)
            with mock.patch.object(builder, "OUTPUT_DIR", linked_root):
                outputs = {linked_root / "dashboard.html": b"new publication"}
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(builder.check(outputs), 1)
                with self.assertRaises(ValueError):
                    builder.write(outputs)
            self.assertEqual(list(external.iterdir()), [])
            target = base / "ordinary-demo"
            legacy = base / "legacy.html"
            original = external / "private.txt"
            original.write_bytes(b"private-marker")
            legacy.symlink_to(original)
            with mock.patch.object(builder, "OUTPUT_DIR", target):
                outputs = {target / "dashboard.html": b"new publication", legacy: b"redirect"}
                with self.assertRaises(ValueError):
                    builder.write(outputs)
            self.assertFalse(target.exists())
            self.assertEqual(original.read_bytes(), b"private-marker")

    def test_canonical_sources_must_not_be_symbolic_links(self) -> None:
        builder = load_builder()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            private = source / "private.txt"
            private.write_bytes(b"private-marker")
            (source / "dashboard.js").symlink_to(private)
            with mock.patch.object(builder, "SOURCE_DIR", source), mock.patch.object(
                builder, "RUNTIME_FILES", ("dashboard.js",)
            ):
                with self.assertRaises(FileNotFoundError):
                    builder.source_digest()

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
