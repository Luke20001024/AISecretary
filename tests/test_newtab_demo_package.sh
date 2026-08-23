#!/bin/bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

VERSION=$(python3 -c 'import json; print(json.load(open("chrome-newtab-demo/manifest.json", encoding="utf-8"))["version"])')
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
import sys
import zipfile

archive_path, version = sys.argv[1:]
prefix = f'Memento-New-Tab-Demo-v{version}/chrome-newtab-demo/'
required = {'manifest.json', 'dashboard.html', 'dashboard.js', 'dashboard.css',
            'cognitive-demo-fixture.js', 'README-DEMO.md'}
with zipfile.ZipFile(archive_path) as archive:
    names = set(archive.namelist())
missing = [item for item in required if prefix + item not in names]
if missing:
    raise SystemExit('missing package content: ' + ', '.join(missing))
files = {name for name in names if not name.endswith('/')}
expected = {prefix + item for item in required}
if files != expected:
    raise SystemExit('unexpected package content: ' + ', '.join(sorted(files - expected)))
PY

echo 'Chrome Demo package contract passed.'
