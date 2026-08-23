#!/bin/bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

VERSION=$(python3 -c 'import json; print(json.load(open("chrome-newtab-demo/manifest.json", encoding="utf-8"))["version"])')

for REQUIRED_COMMAND in git python3 unzip node tar shasum; do
  command -v "$REQUIRED_COMMAND" >/dev/null
done

[ "$VERSION" = "0.10.0" ]
[ -f docs/index.html ]
[ -f docs/Memento-3.0.html ]
[ -f docs/Memento-Cognitive-Home-Standalone.html ]
[ -f chrome-newtab-demo/manifest.json ]
node --check chrome-newtab-demo/dashboard.js >/dev/null
node --check chrome-newtab-demo/cognitive-demo-fixture.js >/dev/null
node tests/test_preview_demo_fixture.js >/dev/null

/usr/bin/grep -qF "Memento 4.0 Preview" README.md INSTALL_WITH_AI.md docs/MEMENTO_DEMO_INSTALL.md
/usr/bin/grep -qF "chrome-newtab-demo" README.md INSTALL_WITH_AI.md docs/MEMENTO_DEMO_INSTALL.md
/usr/bin/grep -qF "固定数据" README.md INSTALL_WITH_AI.md docs/MEMENTO_DEMO_INSTALL.md
/usr/bin/grep -qF "v0.8.9" README.md INSTALL_WITH_AI.md docs/MEMENTO_DEMO_INSTALL.md
/usr/bin/grep -qF "不读取真实目录" INSTALL_WITH_AI.md docs/MEMENTO_DEMO_INSTALL.md

if /usr/bin/grep -R -E -n '(showDirectoryPicker|indexedDB|localStorage|sessionStorage|CacheStorage|caches\.|document\.cookie|navigator\.storage|openDatabase|FileSystem|createWritable|requestPermission|fetch[[:space:]]*\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon|chrome\.|browser\.)' chrome-newtab-demo; then
  echo 'Preview runtime contains prohibited access capability.' >&2
  exit 1
fi

TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/memento-preview-release.XXXXXX")
cleanup() { rm -rf -- "$TMP_ROOT"; }
trap cleanup EXIT

FIXTURE="$TMP_ROOT/repo"
mkdir -p "$FIXTURE"
tar -C "$ROOT" --exclude='./.git' --exclude='./dist' -cf - . | tar -C "$FIXTURE" -xf -
(
  cd "$FIXTURE"
  git init -q
  git config user.name 'Memento Preview Release Test'
  git config user.email 'preview-release@example.invalid'
  git add -A
  git commit -qm 'release fixture'
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
required = {'manifest.json', 'dashboard.html', 'dashboard.js', 'dashboard.css', 'cognitive-demo-fixture.js', 'README-DEMO.md'}
with zipfile.ZipFile(archive_path) as archive:
    files = {name for name in archive.namelist() if not name.endswith('/')}
expected = {prefix + item for item in required}
if files != expected:
    raise SystemExit('Preview ZIP has unexpected content: ' + ', '.join(sorted(files - expected)))
PY

echo 'Memento 4.0 Preview release contract passed.'
