#!/bin/bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

for required_command in git python3 node shasum; do
  command -v "$required_command" >/dev/null
done

python3 scripts/build_standalone_preview.py --check >/dev/null
VERSION=$(python3 -c 'import json; print(json.load(open("docs/demo/manifest.json", encoding="utf-8"))["version"])')
[ "$VERSION" = "0.10.1" ]

[ -f docs/index.html ]
[ -f docs/demo/dashboard.html ]
[ -f docs/demo/runtime.json ]
[ -f docs/Memento-Cognitive-Home-Standalone.html ]

/usr/bin/grep -qF '<iframe src="demo/dashboard.html"' docs/index.html
/usr/bin/grep -qF 'href="demo/dashboard.html"' docs/index.html
! /usr/bin/grep -qF '<iframe src="Memento-Cognitive-Home-Standalone.html"' docs/index.html
/usr/bin/grep -qF 'url=demo/dashboard.html' docs/Memento-Cognitive-Home-Standalone.html

/usr/bin/grep -qF 'v0.10.1 Preview' README.md INSTALL_WITH_AI.md docs/MEMENTO_DEMO_INSTALL.md
/usr/bin/grep -qF 'Memento-Demo' README.md INSTALL_WITH_AI.md docs/MEMENTO_DEMO_INSTALL.md
/usr/bin/grep -qF 'docs/demo/' README.md
/usr/bin/grep -qF 'enterCognitiveDemo();' docs/demo/dashboard.js

for source in docs/demo/*.js; do
  node --check "$source" >/dev/null
done
node tests/test_preview_demo_fixture.js >/dev/null
node tests/test_public_demo_runtime.js >/dev/null
node tests/test_product_guide_install_cta.js >/dev/null
node tests/test_product_guide_fullscreen.js >/dev/null
node tests/test_record_browser.js >/dev/null
node tests/test_record_browser_dashboard.js >/dev/null
node tests/test_ui_home_clarity.js >/dev/null
node tests/test_ui_detail_clarity.js >/dev/null

python3 - <<'PY'
import json
from pathlib import Path

root = Path.cwd()
runtime = json.loads((root / 'docs/demo/runtime.json').read_text(encoding='utf-8'))
manifest = json.loads((root / 'docs/demo/manifest.json').read_text(encoding='utf-8'))
assert runtime['version'] == manifest['version'] == '0.10.1'
assert runtime['source'] == 'chrome-newtab'
for name in runtime['files']:
    assert (root / 'chrome-newtab' / name).read_bytes() == (root / 'docs/demo' / name).read_bytes(), name
for key in ('permissions', 'background', 'content_scripts', 'externally_connectable'):
    assert not manifest.get(key), key
assert manifest.get('host_permissions') == ['http://127.0.0.1:4318/*']
PY

bash tests/test_newtab_demo_package.sh >/dev/null

echo 'Memento v0.10.1 shared-runtime release contract passed.'
