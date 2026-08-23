#!/bin/bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DEMO_DIR="chrome-newtab-demo"
MANIFEST="$ROOT/$DEMO_DIR/manifest.json"

if ! command -v python3 >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1 \
  || ! command -v git >/dev/null 2>&1 || ! command -v shasum >/dev/null 2>&1; then
  echo '缺少 python3、node、git 或 shasum，无法构建 Chrome Demo。' >&2
  exit 1
fi

cd "$ROOT"
VERSION=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["version"])' "$MANIFEST")
OUTPUT=${1:-"$ROOT/dist/Memento-New-Tab-Demo-v${VERSION}.zip"}
PREFIX="Memento-New-Tab-Demo-v${VERSION}/"
REQUIRED_FILES=(
  chrome-newtab-demo/manifest.json
  chrome-newtab-demo/dashboard.html
  chrome-newtab-demo/dashboard.css
  chrome-newtab-demo/dashboard.js
  chrome-newtab-demo/cognitive-demo-fixture.js
  chrome-newtab-demo/README-DEMO.md
)

if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
  echo '当前仓库没有可归档的 HEAD。' >&2
  exit 1
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo 'Chrome Demo 必须从已提交的确定性 HEAD 构建。' >&2
  exit 1
fi

for file in "${REQUIRED_FILES[@]}"; do
  if ! git cat-file -e "HEAD:${file}" 2>/dev/null || [ ! -f "$ROOT/$file" ]; then
    echo "Demo 缺少必需文件：${file}" >&2
    exit 1
  fi
done

UNTRACKED_RUNTIME=$(git ls-files --others --exclude-standard -- "$DEMO_DIR")
if [ -n "$UNTRACKED_RUNTIME" ]; then
  echo 'Chrome Demo 运行时存在未跟踪文件，拒绝从工作树发布：' >&2
  printf '%s\n' "$UNTRACKED_RUNTIME" >&2
  exit 1
fi

for source in "$DEMO_DIR"/*.js; do
  node --check "$source" >/dev/null
done

if /usr/bin/grep -R -E -n '(showDirectoryPicker|indexedDB|localStorage|sessionStorage|CacheStorage|caches\.|document\.cookie|navigator\.storage|openDatabase|FileSystem|createWritable|requestPermission|fetch[[:space:]]*\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon|chrome\.|browser\.)' "$DEMO_DIR" >/dev/null; then
  echo '独立 Demo 包不得包含目录、存储、网络、权限或浏览器运行时。' >&2
  exit 1
fi

python3 - "$MANIFEST" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding='utf-8'))
assert manifest['manifest_version'] == 3
assert manifest['chrome_url_overrides']['newtab'] == 'dashboard.html'
assert manifest['version'].count('.') >= 1
for key in ('permissions', 'host_permissions', 'background', 'content_scripts', 'externally_connectable'):
    if key in manifest and manifest[key]:
        raise AssertionError(f'Preview manifest must not declare {key}')
PY

if [ "${MEMENTO_REQUIRE_RELEASE_TAG:-0}" = "1" ]; then
  TAG="v${VERSION}"
  [ "$(git rev-list -n 1 "$TAG")" = "$(git rev-parse HEAD)" ] || {
    echo "发布标签 ${TAG} 必须精确指向当前 HEAD。" >&2
    exit 1
  }
fi

mkdir -p "$(dirname "$OUTPUT")"
rm -f "$OUTPUT" "$OUTPUT.sha256"
git archive --format=zip --prefix="$PREFIX" --output="$OUTPUT" HEAD -- "${REQUIRED_FILES[@]}"

python3 - "$OUTPUT" "$PREFIX" "${REQUIRED_FILES[@]}" <<'PY'
import sys
import zipfile

archive_path, prefix, *required = sys.argv[1:]
with zipfile.ZipFile(archive_path) as archive:
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise SystemExit('Demo ZIP contains duplicate paths')
    files = {name for name in names if not name.endswith('/')}
    expected = {prefix + path for path in required}
    if files != expected:
        unexpected = sorted(files - expected)
        missing = sorted(expected - files)
        raise SystemExit('Demo ZIP contract mismatch; missing=' + ', '.join(missing) + '; unexpected=' + ', '.join(unexpected))
PY

(cd "$(dirname "$OUTPUT")" && shasum -a 256 "$(basename "$OUTPUT")" > "$(basename "$OUTPUT").sha256")
echo "已构建 $OUTPUT"
