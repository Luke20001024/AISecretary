#!/bin/bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

python3 scripts/build_standalone_preview.py --check >/dev/null
VERSION=$(python3 -c 'import json; print(json.load(open("docs/demo/manifest.json", encoding="utf-8"))["version"])')
TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/memento-newtab-package.XXXXXX")
cleanup() { rm -rf -- "$TMP_ROOT"; }
trap cleanup EXIT

FIXTURE="$TMP_ROOT/repo"
mkdir -p "$FIXTURE"
tar -C "$ROOT" --exclude='./.git' --exclude='./dist' -cf - . | tar -C "$FIXTURE" -xf -
(
  cd "$FIXTURE"
  git init -q
  git config user.name 'Memento Demo Package Test'
  git config user.email 'demo-package@example.invalid'
  git add -A
  git commit -qm 'fixture'
  bash scripts/package_newtab_demo.sh "$TMP_ROOT/demo.zip" >/dev/null
)

[ -s "$TMP_ROOT/demo.zip" ]
[ -s "$TMP_ROOT/demo.zip.sha256" ]
(cd "$TMP_ROOT" && shasum -a 256 -c demo.zip.sha256 >/dev/null)

python3 - "$TMP_ROOT/demo.zip" "$VERSION" <<'PY'
import hashlib
import json
import sys
import zipfile
from pathlib import Path

archive_path, version = sys.argv[1:]
root = Path.cwd()
prefix = f'Memento-New-Tab-Demo-v{version}/Memento-Demo/'
runtime = json.loads((root / 'docs/demo/runtime.json').read_text(encoding='utf-8'))
required = [*runtime['files'], 'runtime.json']
with zipfile.ZipFile(archive_path) as archive:
    files = {name for name in archive.namelist() if not name.endswith('/')}
    expected = {prefix + name for name in required}
    if files != expected:
        raise SystemExit('Preview ZIP has unexpected content')
    for name in required:
        packaged = archive.read(prefix + name)
        published = (root / 'docs/demo' / name).read_bytes()
        if hashlib.sha256(packaged).digest() != hashlib.sha256(published).digest():
            raise SystemExit(f'Preview ZIP diverges from shared web runtime: {name}')
PY

echo 'Chrome Demo package and shared web runtime are byte-identical.'
