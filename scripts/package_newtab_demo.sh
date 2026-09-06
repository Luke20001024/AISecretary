#!/bin/bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
WEB_RUNTIME="docs/demo"
MANIFEST="$ROOT/$WEB_RUNTIME/manifest.json"

for command_name in python3 node git shasum; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "缺少 ${command_name}，无法构建 Chrome Demo。" >&2
    exit 1
  }
done

cd "$ROOT"
python3 scripts/build_standalone_preview.py --check >/dev/null
VERSION=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["version"])' "$MANIFEST")
OUTPUT=${1:-"$ROOT/dist/Memento-New-Tab-Demo-v${VERSION}.zip"}
PREFIX="Memento-New-Tab-Demo-v${VERSION}/Memento-Demo/"

git rev-parse --verify HEAD >/dev/null 2>&1 || {
  echo '当前仓库没有可归档的 HEAD。' >&2
  exit 1
}

RUNTIME_FILES=()
while IFS= read -r file; do
  RUNTIME_FILES+=("$file")
done < <(python3 - "$WEB_RUNTIME/runtime.json" <<'PY'
import json
import sys

runtime = json.load(open(sys.argv[1], encoding='utf-8'))
for name in runtime['files']:
    print(name)
print('runtime.json')
PY
)

for name in "${RUNTIME_FILES[@]}"; do
  path="$WEB_RUNTIME/$name"
  if [ ! -f "$path" ] || ! git cat-file -e "HEAD:$path" 2>/dev/null; then
    echo "共享 Demo 缺少已提交文件：${path}" >&2
    exit 1
  fi
  if ! git diff --quiet HEAD -- "$path"; then
    echo "共享 Demo 文件尚未提交：${path}" >&2
    exit 1
  fi
done

for source in "$WEB_RUNTIME"/*.js; do
  node --check "$source" >/dev/null
done

python3 - "$MANIFEST" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding='utf-8'))
assert manifest['manifest_version'] == 3
assert manifest['chrome_url_overrides']['newtab'] == 'dashboard.html'
assert manifest['version'].count('.') >= 1
for key in ('permissions', 'background', 'content_scripts', 'externally_connectable'):
    if key in manifest and manifest[key]:
        raise AssertionError(f'Preview manifest must not declare {key}')
if manifest.get('host_permissions') != ['http://127.0.0.1:4318/*']:
    raise AssertionError('Preview manifest may only access the Memento loopback runtime')
PY

if [ "${MEMENTO_REQUIRE_RELEASE_TAG:-0}" = "1" ]; then
  TAG="v${VERSION}"
  [ "$(git rev-list -n 1 "$TAG")" = "$(git rev-parse HEAD)" ] || {
    echo "发布标签 ${TAG} 必须精确指向当前 HEAD。" >&2
    exit 1
  }
fi

mkdir -p "$(dirname "$OUTPUT")"
python3 - "$OUTPUT" "$PREFIX" "${RUNTIME_FILES[@]}" <<'PY'
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

output, prefix, *names = sys.argv[1:]
target = Path(output)
temporary = target.with_suffix(target.suffix + '.tmp')
if temporary.exists():
    temporary.unlink()
with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for name in names:
        repo_path = f'docs/demo/{name}'
        content = subprocess.run(
            ['git', 'show', f'HEAD:{repo_path}'],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        info = zipfile.ZipInfo(prefix + name, date_time=(2026, 8, 24, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, content)
temporary.replace(target)
PY

python3 - "$OUTPUT" "$PREFIX" "${RUNTIME_FILES[@]}" <<'PY'
import sys
import zipfile

archive_path, prefix, *required = sys.argv[1:]
with zipfile.ZipFile(archive_path) as archive:
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise SystemExit('Demo ZIP contains duplicate paths')
    files = {name for name in names if not name.endswith('/')}
    expected = {prefix + name for name in required}
    if files != expected:
        raise SystemExit('Demo ZIP contract mismatch')
PY

(cd "$(dirname "$OUTPUT")" && shasum -a 256 "$(basename "$OUTPUT")" > "$(basename "$OUTPUT").sha256")
echo "已从 $WEB_RUNTIME 构建 $OUTPUT"
