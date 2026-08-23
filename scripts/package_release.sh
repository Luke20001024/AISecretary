#!/bin/bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MANIFEST="$ROOT/chrome-newtab/manifest.json"
OUTPUT=${1:-"$ROOT/dist/Memento-macOS.zip"}

echo '当前工作树只发布 Memento 4.0 Preview 的固定数据 Chrome Demo。'
echo '历史 macOS 完整安装包仅保留在 v0.8.9 Release；请使用 scripts/package_newtab_demo.sh 构建当前发行。' >&2
exit 2

# These are the minimum Agent V1 sources and release claims that must come from
# the exact commit being archived.  A worktree-only implementation must never
# make a release check pass while being absent from the ZIP produced from HEAD.
REQUIRED_AGENT_FILES=(
  chrome-newtab/manifest.json
  chrome-newtab/dashboard.html
  chrome-newtab/dashboard.js
  chrome-newtab/cognitive-demo-fixture.js
  chrome-newtab/cognitive-home-library.js
  chrome-newtab/context-agent-library.js
  chrome-newtab/remember-agent-v1-library.js
  context-agent/README.md
  context-agent/agent_v1.py
  context-agent/cognitive_actions_v1.py
  context-agent/cognitive_agent_adapter_v1.py
  context-agent/cognitive_bundle_store_v1.py
  context-agent/cognitive_daily_review_v1.py
  context-agent/cognitive_day_orchestrator_v1.py
  context-agent/cognitive_migration_v1.py
  context-agent/cognitive_manual_request_v1.py
  context-agent/cognitive_pipeline_v1.py
  context-agent/cognitive_projection_v1.py
  context-agent/cognitive_prompts_v1.py
  context-agent/cognitive_record_worker_v1.py
  context-agent/cognitive_runtime_v1.py
  context-agent/cognitive_schedule_v1.py
  context-agent/cognitive_store_v1.py
  context-agent/cognitive_v1.py
  context-agent/context_agent.py
  context-agent/core.py
  context-agent/deepseek_provider.py
  context-agent/reflection.py
  context-agent/schemas/model-response.schema.json
  context-agent/schemas/stored-candidate.schema.json
  context-agent/schemas/record_interpreter_action_v1.json
  context-agent/schemas/daily_integrator_action_v1.json
  context-agent/eval/agent-v1/README.md
  context-agent/eval/agent-v1/cases.json
  context-agent/eval/agent-v1/run_offline_eval.py
  context-agent/eval/agent-v1/run_live_pairing.py
  context-agent/eval/agent-v1/run_live_preflight.py
  context-agent/eval/agent-v1/run_live_e2.py
  context-agent/eval/agent-v1/run_live_manual_gate.py
  context-agent/eval/agent-v1/run_live_workflow_mvp.py
  context-agent/eval/cognitive-v1/README.md
  context-agent/eval/cognitive-v1/run_live_acceptance.py
  docs/REMEMBER_AGENT_V1_PRD.html
  docs/REMEMBER_AGENT_V1_TECHNICAL_DESIGN.md
  docs/REMEMBER_AGENT_V1_EVALUATION.md
  docs/REMEMBER_AGENT_V1_EVALUATION_RESULT_2026-08-12.md
  docs/cognitive-secretary-mvp/PRD.md
  docs/cognitive-secretary-mvp/TECHNICAL_DESIGN.md
  docs/cognitive-secretary-mvp/DATA_CONTRACT.md
  docs/cognitive-secretary-mvp/USER_GUIDE.md
  docs/cognitive-secretary-mvp/RELEASE_CHECKLIST.md
)

if ! command -v python3 >/dev/null 2>&1; then
  echo '缺少 python3，无法读取发布版本。' >&2
  exit 1
fi
for REQUIRED_COMMAND in unzip node git tar; do
  if ! command -v "$REQUIRED_COMMAND" >/dev/null 2>&1; then
    echo "缺少 ${REQUIRED_COMMAND}，无法完成发布校验。" >&2
    exit 1
  fi
done

cd "$ROOT"
if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
  echo '发布校验失败：当前仓库没有可归档的 HEAD。' >&2
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo '存在未提交的已跟踪改动；请先提交，再从确定的 HEAD 打包。' >&2
  exit 1
fi

for REQUIRED_FILE in "${REQUIRED_AGENT_FILES[@]}"; do
  if ! git cat-file -e "HEAD:${REQUIRED_FILE}" 2>/dev/null \
    || ! git ls-files --error-unmatch -- "$REQUIRED_FILE" >/dev/null 2>&1; then
    echo "发布校验失败：Agent V1 必需文件未由 HEAD 跟踪：${REQUIRED_FILE}" >&2
    exit 1
  fi
  if [ ! -f "$ROOT/$REQUIRED_FILE" ] || [ -L "$ROOT/$REQUIRED_FILE" ] \
    || ! git diff --quiet HEAD -- "$REQUIRED_FILE"; then
    echo "发布校验失败：Agent V1 必需文件与 HEAD 不一致：${REQUIRED_FILE}" >&2
    exit 1
  fi
done

# Refuse untracked implementation files under the two shipped runtime trees.
# Ignored caches/results are excluded by Git and do not affect the archive.
UNTRACKED_RUNTIME_FILES=$(git ls-files --others --exclude-standard -- \
  chrome-newtab context-agent)
if [ -n "$UNTRACKED_RUNTIME_FILES" ]; then
  echo '发布校验失败：待发布运行时中存在未跟踪文件，不能依赖工作树打包：' >&2
  printf '%s\n' "$UNTRACKED_RUNTIME_FILES" >&2
  exit 1
fi

VERSION=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["version"])' "$MANIFEST")
PREFIX="Memento-v${VERSION}/"
OUTPUT_DIR=$(dirname "$OUTPUT")
OUTPUT_NAME=$(basename "$OUTPUT")

if [ "${MEMENTO_REQUIRE_RELEASE_TAG:-0}" = "1" ]; then
  RELEASE_TAG="v${VERSION}"
  TAG_COMMIT=$(git rev-list -n 1 "$RELEASE_TAG" 2>/dev/null || true)
  HEAD_COMMIT=$(git rev-parse HEAD)
  if [ -z "$TAG_COMMIT" ] || [ "$TAG_COMMIT" != "$HEAD_COMMIT" ]; then
    echo "发布校验失败：${RELEASE_TAG} 必须存在并精确指向 HEAD ${HEAD_COMMIT}。" >&2
    exit 1
  fi
fi

mkdir -p "$OUTPUT_DIR"
rm -f "$OUTPUT" "$OUTPUT.sha256"

RELEASE_PATHS=(
  README.md \
  INSTALL_WITH_AI.md \
  install_aisecretary.sh \
  uninstall_aisecretary.sh \
  chrome-newtab \
  daily-review \
  context-agent \
  docs/CONTEXT_AGENT_MVP_PRD.md \
  docs/CONTEXT_AGENT_TECHNICAL_DESIGN.md \
  docs/CONTEXT_AGENT_EVALUATION.md \
  docs/CONTEXT_AGENT_EVALUATION_RESULT_2026-08-10.md \
  docs/REMEMBER_AGENT_V1_PRD.html \
  docs/REMEMBER_AGENT_V1_TECHNICAL_DESIGN.md \
  docs/REMEMBER_AGENT_V1_EVALUATION.md \
  docs/REMEMBER_AGENT_V1_EVALUATION_RESULT_2026-08-12.md \
  docs/Memento-cache-acceleration-design.md \
  docs/cognitive-secretary-mvp \
  obsidian-vault \
  snapshot-capture \
  voice-capture
)

TEMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/memento-release.XXXXXX")
STAGING_DIR="$TEMP_ROOT/staging"
VERIFY_DIR="$TEMP_ROOT/verify"
mkdir -p "$STAGING_DIR" "$VERIFY_DIR"
cleanup_release_temp() {
  rm -rf -- "$TEMP_ROOT"
}
trap cleanup_release_temp EXIT

# Materialize the exact HEAD first. Privacy checks run on this staging tree
# before any distributable ZIP is produced.
git archive --format=tar HEAD -- "${RELEASE_PATHS[@]}" \
  | tar -xf - -C "$STAGING_DIR"

python3 - "$STAGING_DIR" <<'PY'
import json
import os
import pathlib
import re
import stat
import sys

root = pathlib.Path(sys.argv[1]).resolve()
token_pattern = re.compile(rb"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}")
private_key_pattern = re.compile(
    rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
)
live_schema_prefixes = (
    "memento_cognitive_v1_live_",
    "remember_agent_live_",
    "remember_agent_workflow_mvp_live.",
)
violations: list[tuple[str, str]] = []


def add_violation(category: str, path: pathlib.Path) -> None:
    relative = path.relative_to(root).as_posix()
    item = (category, relative)
    if item not in violations:
        violations.append(item)


def is_live_report_payload(raw: bytes) -> bool:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    schema = payload.get("schema_version")
    if not isinstance(schema, str) or not schema.startswith(live_schema_prefixes):
        return False
    return (
        "executed" in payload
        and "status" in payload
        and any(key in payload for key in ("usage", "cases", "runs", "summary"))
    )


for directory, directory_names, file_names in os.walk(root, followlinks=False):
    directory_names.sort()
    file_names.sort()
    parent = pathlib.Path(directory)
    for file_name in file_names:
        path = parent / file_name
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            continue
        lower_name = file_name.lower()
        if (
            lower_name == ".env" or lower_name.startswith(".env.")
        ) and file_name != ".env.example":
            add_violation("environment file", path)
        relative_parts = tuple(part.lower() for part in path.relative_to(root).parts)
        if (
            ("live" in lower_name and "report" in lower_name)
            or ("eval" in relative_parts and "results" in relative_parts)
        ):
            add_violation("live report filename", path)
        raw = path.read_bytes()
        if private_key_pattern.search(raw):
            add_violation("private key header", path)
        if token_pattern.search(raw):
            add_violation("sk token", path)
        if is_live_report_payload(raw):
            add_violation("live report content", path)

if violations:
    print("发布隐私校验失败：staging 中存在不可分发内容：", file=sys.stderr)
    for category, relative in sorted(violations):
        print(f"- {category}: {relative}", file=sys.stderr)
    raise SystemExit(1)
PY

git archive \
  --format=zip \
  --prefix="$PREFIX" \
  --output="$OUTPUT" \
  HEAD -- "${RELEASE_PATHS[@]}"

python3 - "$OUTPUT" "$PREFIX" "${REQUIRED_AGENT_FILES[@]}" <<'PY'
import pathlib
import sys
import zipfile

archive_path, prefix, *required = sys.argv[1:]
with zipfile.ZipFile(archive_path) as archive:
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise SystemExit("发布包校验失败：ZIP 含有重复路径")
    unsafe = [
        name
        for name in names
        if not name.startswith(prefix)
        or pathlib.PurePosixPath(name).is_absolute()
        or ".." in pathlib.PurePosixPath(name).parts
    ]
    if unsafe:
        raise SystemExit("发布包校验失败：ZIP 路径越界")
    missing = [path for path in required if prefix + path not in names]
    if missing:
        raise SystemExit(
            "发布包校验失败：缺少 Agent V1 必需文件："
            + ", ".join(missing)
        )
    corrupt = archive.testzip()
    if corrupt is not None:
        raise SystemExit(f"发布包校验失败：ZIP 条目损坏：{corrupt}")
PY

unzip -qq "$OUTPUT" -d "$VERIFY_DIR"
PACKAGED_ROOT="$VERIFY_DIR/${PREFIX%/}"

for PACKAGED_JS in \
  "$PACKAGED_ROOT/chrome-newtab/dashboard.js" \
  "$PACKAGED_ROOT/chrome-newtab/cognitive-demo-fixture.js" \
  "$PACKAGED_ROOT/chrome-newtab/cognitive-home-library.js" \
  "$PACKAGED_ROOT/chrome-newtab/context-agent-library.js" \
  "$PACKAGED_ROOT/chrome-newtab/remember-agent-v1-library.js"; do
  if [ ! -f "$PACKAGED_JS" ] || [ -L "$PACKAGED_JS" ] \
    || ! node --check "$PACKAGED_JS" >/dev/null; then
    echo "发布包校验失败：Agent V1 JavaScript 运行时缺失或语法错误：$(basename "$PACKAGED_JS")" >&2
    exit 1
  fi
done

PACKAGED_PYTHON_FILES=(
  "$PACKAGED_ROOT/context-agent/agent_v1.py"
  "$PACKAGED_ROOT/context-agent/cognitive_actions_v1.py"
  "$PACKAGED_ROOT/context-agent/cognitive_agent_adapter_v1.py"
  "$PACKAGED_ROOT/context-agent/cognitive_bundle_store_v1.py"
  "$PACKAGED_ROOT/context-agent/cognitive_daily_review_v1.py"
  "$PACKAGED_ROOT/context-agent/cognitive_day_orchestrator_v1.py"
  "$PACKAGED_ROOT/context-agent/cognitive_migration_v1.py"
  "$PACKAGED_ROOT/context-agent/cognitive_manual_request_v1.py"
  "$PACKAGED_ROOT/context-agent/cognitive_pipeline_v1.py"
  "$PACKAGED_ROOT/context-agent/cognitive_projection_v1.py"
  "$PACKAGED_ROOT/context-agent/cognitive_prompts_v1.py"
  "$PACKAGED_ROOT/context-agent/cognitive_record_worker_v1.py"
  "$PACKAGED_ROOT/context-agent/cognitive_runtime_v1.py"
  "$PACKAGED_ROOT/context-agent/cognitive_schedule_v1.py"
  "$PACKAGED_ROOT/context-agent/cognitive_store_v1.py"
  "$PACKAGED_ROOT/context-agent/cognitive_v1.py"
  "$PACKAGED_ROOT/context-agent/context_agent.py"
  "$PACKAGED_ROOT/context-agent/core.py"
  "$PACKAGED_ROOT/context-agent/deepseek_provider.py"
  "$PACKAGED_ROOT/context-agent/reflection.py"
  "$PACKAGED_ROOT/context-agent/eval/agent-v1/run_live_manual_gate.py"
  "$PACKAGED_ROOT/context-agent/eval/agent-v1/run_live_workflow_mvp.py"
  "$PACKAGED_ROOT/context-agent/eval/cognitive-v1/run_live_acceptance.py"
)
if ! PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile \
  "${PACKAGED_PYTHON_FILES[@]}"; then
  echo '发布包校验失败：Agent V1 Python 运行时语法错误。' >&2
  exit 1
fi
if ! PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PACKAGED_ROOT/context-agent" \
  python3 -c 'import agent_v1, context_agent, core, deepseek_provider, reflection, cognitive_actions_v1, cognitive_agent_adapter_v1, cognitive_bundle_store_v1, cognitive_daily_review_v1, cognitive_day_orchestrator_v1, cognitive_migration_v1, cognitive_manual_request_v1, cognitive_pipeline_v1, cognitive_projection_v1, cognitive_prompts_v1, cognitive_record_worker_v1, cognitive_runtime_v1, cognitive_schedule_v1, cognitive_store_v1, cognitive_v1'; then
  echo '发布包校验失败：Cognitive Secretary Python 运行时无法从 ZIP 解包后导入。' >&2
  exit 1
fi
for PACKAGED_SCHEMA in \
  "$PACKAGED_ROOT/context-agent/schemas/record_interpreter_action_v1.json" \
  "$PACKAGED_ROOT/context-agent/schemas/daily_integrator_action_v1.json"; do
  if [ ! -f "$PACKAGED_SCHEMA" ] || [ -L "$PACKAGED_SCHEMA" ] \
    || ! python3 -m json.tool "$PACKAGED_SCHEMA" >/dev/null; then
    echo "发布包校验失败：Cognitive Secretary schema 缺失或不合法：$(basename "$PACKAGED_SCHEMA")" >&2
    exit 1
  fi
done

PACKAGED_VERSION=$(unzip -p "$OUTPUT" "${PREFIX}chrome-newtab/manifest.json" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')
if [ "$PACKAGED_VERSION" != "$VERSION" ]; then
  echo "发布包版本校验失败：期望 ${VERSION}，实际 ${PACKAGED_VERSION}" >&2
  exit 1
fi

(
  cd "$OUTPUT_DIR"
  shasum -a 256 "$OUTPUT_NAME" > "$OUTPUT_NAME.sha256"
)

echo "已生成 $OUTPUT"
echo "版本 v${VERSION}，目录前缀 ${PREFIX}"
cat "$OUTPUT.sha256"
