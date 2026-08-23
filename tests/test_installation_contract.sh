#!/bin/bash
# 在隔离 HOME 中验证完整安装、升级幂等与默认卸载的数据边界。

set -e
set -o pipefail
umask 077

ROOT=$(cd "$(dirname "$0")/.." && pwd)
rg -qF '[ -f "$directory/cognitive-demo-fixture.js" ]' \
  "$ROOT/install_aisecretary.sh"
rg -qF 'node --check "$directory/cognitive-demo-fixture.js"' \
  "$ROOT/install_aisecretary.sh"
# The installer intentionally rejects executable paths beneath group/world-
# writable ancestors such as /private/tmp.  Keep this fake runtime beneath the
# current-owner workspace so the test exercises the trusted-Python contract.
TMP_ROOT=$(mktemp -d "$ROOT/.memento-install-contract.XXXXXX")
TEST_HOME="$TMP_ROOT/home"
FAKE_BIN="$TMP_ROOT/bin"
LOG_DIR="$TMP_ROOT/logs"

cleanup() {
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p "$TEST_HOME/Library/Services" "$TEST_HOME/Library/LaunchAgents" "$FAKE_BIN" "$LOG_DIR"

MEMENTO_TEST_REAL_PYTHON3=$(command -v python3)
export MEMENTO_TEST_REAL_PYTHON3
cat > "$FAKE_BIN/python3" <<'FAKE_PYTHON3'
#!/bin/bash
exec "${MEMENTO_TEST_REAL_PYTHON3:?}" "$@"
FAKE_PYTHON3

# 强制 Swift 构建失败，验证升级时已有可用 App 不会被半成品覆盖。
cat > "$FAKE_BIN/swiftc" <<'FAKE_SWIFTC'
#!/bin/bash
exit 42
FAKE_SWIFTC
chmod 700 "$FAKE_BIN/python3" "$FAKE_BIN/swiftc"
FAKE_PYTHON_RUNTIME=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' \
  "$FAKE_BIN/python3")
FAKE_PYTHON_ASSIGNMENT=$(python3 -c \
  'import shlex,sys; print("MEMENTO_PYTHON=" + shlex.quote(sys.argv[1]))' \
  "$FAKE_PYTHON_RUNTIME")

run_install_for_home() {
  local selected_home="$1"
  local replies="$2"
  local log="$3"
  printf '%s' "$replies" | env \
    HOME="$selected_home" \
    PATH="$FAKE_BIN:$PATH" \
    MEMENTO_SKIP_SERVICE_REFRESH=1 \
    MEMENTO_SKIP_LEGACY_LAUNCHAGENT_UNLOAD=1 \
    MEMENTO_SKIP_CONTEXT_WORKER_LOAD=1 \
    bash "$ROOT/install_aisecretary.sh" >"$log" 2>&1
}

run_install() {
  run_install_for_home "$TEST_HOME" "$1" "$2"
}

run_uninstall() {
  local replies="$1"
  local log="$2"
  printf '%s' "$replies" | env \
    HOME="$TEST_HOME" \
    PATH="$FAKE_BIN:$PATH" \
    MEMENTO_SKIP_SERVICE_REFRESH=1 \
    MEMENTO_SKIP_LEGACY_LAUNCHAGENT_UNLOAD=1 \
    MEMENTO_SKIP_CONTEXT_WORKER_UNLOAD=1 \
    bash "$ROOT/uninstall_aisecretary.sh" >"$log" 2>&1
}

# v1 的 owned 常驻截图监听器必须在升级时清掉，避免旧通知继续后台出现。
LEGACY_PLIST="$TEST_HOME/Library/LaunchAgents/com.aisecretary.screenshot.plist"
cat > "$LEGACY_PLIST" <<LEGACY_PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.aisecretary.screenshot</string>
  <key>ProgramArguments</key><array><string>$TEST_HOME/AISecretary/.scripts/watch_screenshots.sh</string></array>
</dict></plist>
LEGACY_PLIST_EOF

# 同名但无 Memento marker/专属路径的用户 Workflow 必须全程保留。
USER_WF="$TEST_HOME/Library/Services/存入 AI 秘书.workflow"
mkdir -p "$USER_WF/Contents"
printf '%s\n' 'USER_WORKFLOW_SENTINEL' > "$USER_WF/Contents/document.wflow"

# 首次安装只有 Chrome 可选提示；回答 n，避免触碰真实 Chrome。
run_install 'n' "$LOG_DIR/install-first.log"

# 语音 App 仍使用内容 hash 稳定复用已有 TCC 身份。
rg -q 'VOICE_SOURCE_HASH=\$\(memento_content_set_hash' "$ROOT/install_aisecretary.sh"
# 通用选区助手使用固定身份与内容 hash，避免无变化升级重置辅助功能授权。
rg -q 'SELECTION_COPY_SOURCE_HASH=\$\(memento_content_set_hash' "$ROOT/install_aisecretary.sh"

VAULT="$TEST_HOME/AISecretary"
AGENT_V1_ROOT="$VAULT/.context-agent/agent-v1"
COGNITIVE_ROOT="$VAULT/.context-agent/cognitive-secretary-v1"
COGNITIVE_MODULES=(
  cognitive_actions_v1.py
  cognitive_agent_adapter_v1.py
  cognitive_bundle_store_v1.py
  cognitive_daily_review_v1.py
  cognitive_day_orchestrator_v1.py
  cognitive_migration_v1.py
  cognitive_manual_request_v1.py
  cognitive_pipeline_v1.py
  cognitive_projection_v1.py
  cognitive_prompts_v1.py
  cognitive_record_worker_v1.py
  cognitive_runtime_v1.py
  cognitive_schedule_v1.py
  cognitive_store_v1.py
  cognitive_v1.py
)
[ -x "$VAULT/.scripts/append_text.sh" ]
[ ! -e "$VAULT/.scripts/append_daily_snapshot.sh" ]
AUTOMATION_WAKE_DENYLIST='memento_(wake|trigger)|MEMENTO_DAILY_SNAPSHOT|append_daily_snapshot|run_cognitive_record_once|record-(ingest|worker)|daily-(run|manual|schedule)|review_cycle|commit_review|Memento Daily Snapshot'
for CAPTURE_SCRIPT in \
  append_text.sh append_image.sh append_voice.sh capture_screenshot.sh; do
  ! rg -q "$AUTOMATION_WAKE_DENYLIST" "$VAULT/.scripts/$CAPTURE_SCRIPT"
done
! rg -q "$AUTOMATION_WAKE_DENYLIST" "$VAULT/.scripts/memento_env.sh"
[ -f "$VAULT/.chrome-newtab/manifest.json" ]
[ -f "$VAULT/.chrome-newtab/cognitive-demo-fixture.js" ]
[ -f "$VAULT/.chrome-newtab/context-agent-library.js" ]
[ -f "$VAULT/.chrome-newtab/cognitive-home-library.js" ]
[ -f "$VAULT/.chrome-newtab/remember-agent-v1-library.js" ]
[ -f "$VAULT/.chrome-newtab/dashboard-cache-library.js" ]
[ -f "$VAULT/.chrome-newtab/dashboard-operations-library.js" ]
[ -f "$VAULT/.chrome-newtab/photo-cache-library.js" ]
[ ! -e "$VAULT/.review" ]
[ ! -e "$VAULT/Reviews/Daily" ]
[ ! -e "$VAULT/.apps/Memento Daily Snapshot.app" ]
[ -x "$VAULT/.context-agent/runtime/context_agent.py" ]
[ -f "$VAULT/.context-agent/runtime/reflection.py" ]
[ -f "$VAULT/.context-agent/runtime/agent_v1.py" ]
for MODULE in "${COGNITIVE_MODULES[@]}"; do
  [ -f "$VAULT/.context-agent/runtime/$MODULE" ]
done
for SCHEMA in record_interpreter_action_v1.json daily_integrator_action_v1.json; do
  [ -f "$VAULT/.context-agent/runtime/schemas/$SCHEMA" ]
  python3 -m json.tool "$VAULT/.context-agent/runtime/schemas/$SCHEMA" >/dev/null
done
[ -x "$VAULT/.context-agent/runtime/run_self_reflection_once.sh" ]
[ -x "$VAULT/.context-agent/runtime/run_remember_agent_v1_once.sh" ]
[ -x "$VAULT/.context-agent/runtime/run_cognitive_record_once.sh" ]
[ -f "$VAULT/.context-agent/runtime/cognitive_daily_review_v1.py" ]
[ ! -e "$VAULT/.context-agent/runtime/run_remember_agent_schedule_once.sh" ]
for RUNNER in \
  "$VAULT/.context-agent/runtime/run_self_reflection_once.sh" \
  "$VAULT/.context-agent/runtime/run_remember_agent_v1_once.sh" \
  "$VAULT/.context-agent/runtime/run_cognitive_record_once.sh"; do
  rg -qF "$FAKE_PYTHON_ASSIGNMENT" "$RUNNER"
done
[ -d "$VAULT/.context-agent/candidates" ]
[ -d "$VAULT/.context-agent/decisions" ]
[ -d "$VAULT/.context-agent/usage" ]
[ -d "$VAULT/.context-agent/self-queries/requests" ]
[ -d "$VAULT/.context-agent/self-queries/responses" ]
[ -d "$VAULT/.context-agent/self-queries/feedback" ]
[ -d "$VAULT/.context-agent/agent-v1/requests" ]
[ -d "$VAULT/.context-agent/agent-v1/responses" ]
[ -d "$VAULT/.context-agent/agent-v1/runs" ]
[ -d "$VAULT/.context-agent/agent-v1/memories" ]
[ -d "$VAULT/.context-agent/agent-v1/user-actions" ]
[ -d "$VAULT/.context-agent/agent-v1/locks" ]
[ ! -e "$VAULT/.context-agent/agent-v1/profile.json" ]
[ ! -e "$VAULT/.context-agent/agent-v1/enabled" ]
[ ! -e "$VAULT/.context-agent/agent-v1/schedule.json" ]
[ -d "$COGNITIVE_ROOT" ]
[ -d "$COGNITIVE_ROOT/user-actions" ]
[ -d "$COGNITIVE_ROOT/manual-day-requests" ]
[ -d "$COGNITIVE_ROOT/manual-day-results" ]
[ -d "$COGNITIVE_ROOT/locks" ]
[ -d "$VAULT/.context-agent/logs" ]
[ -d "$VAULT/Context/Confirmed" ]
[ "$(cat "$USER_WF/Contents/document.wflow")" = 'USER_WORKFLOW_SENTINEL' ]
[ ! -d "$TEST_HOME/.memento-install.lock" ]
[ ! -e "$LEGACY_PLIST" ]

node --check "$VAULT/.chrome-newtab/dashboard.js" >/dev/null
node --check "$VAULT/.chrome-newtab/cognitive-demo-fixture.js" >/dev/null
node --check "$VAULT/.chrome-newtab/cognitive-home-library.js" >/dev/null
node --check "$VAULT/.chrome-newtab/context-agent-library.js" >/dev/null
node --check "$VAULT/.chrome-newtab/remember-agent-v1-library.js" >/dev/null
node --check "$VAULT/.chrome-newtab/dashboard-cache-library.js" >/dev/null
node --check "$VAULT/.chrome-newtab/dashboard-operations-library.js" >/dev/null
node --check "$VAULT/.chrome-newtab/photo-cache-library.js" >/dev/null
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$VAULT/.context-agent/runtime" python3 -c \
  'import agent_v1, context_agent, cognitive_actions_v1, cognitive_agent_adapter_v1, cognitive_bundle_store_v1, cognitive_daily_review_v1, cognitive_day_orchestrator_v1, cognitive_migration_v1, cognitive_manual_request_v1, cognitive_pipeline_v1, cognitive_projection_v1, cognitive_prompts_v1, cognitive_record_worker_v1, cognitive_runtime_v1, cognitive_schedule_v1, cognitive_store_v1, cognitive_v1'
for COMMAND in \
  agent-status agent-enable agent-disable \
  agent-request agent-run agent-worker agent-schedule-tick agent-profile \
  record-ingest record-worker daily-run \
  cognitive-action-worker daily-manual-worker \
  daily-schedule-status daily-schedule-enable daily-schedule-disable \
  daily-schedule-tick projection-rebuild; do
  PYTHONDONTWRITEBYTECODE=1 python3 "$VAULT/.context-agent/runtime/context_agent.py" \
    "$COMMAND" --help >/dev/null
done
SELF_WORKER_OUTPUT=$(bash \
  "$VAULT/.context-agent/runtime/run_self_reflection_once.sh" "$VAULT")
printf '%s' "$SELF_WORKER_OUTPUT" | rg -q 'self_reflection_worker_run'
! printf '%s' "$SELF_WORKER_OUTPUT" | rg -q 'remember_agent_worker_run'
rg -q 'self-reflection-worker' "$VAULT/.context-agent/runtime/run_self_reflection_once.sh"
! rg -q 'agent-worker|agent-v1/enabled' \
  "$VAULT/.context-agent/runtime/run_self_reflection_once.sh"

DEFAULT_AGENT_OUTPUT=$(env -u DEEPSEEK_API_KEY bash \
  "$VAULT/.context-agent/runtime/run_remember_agent_v1_once.sh" "$VAULT")
[ -z "$DEFAULT_AGENT_OUTPUT" ]
rg -q 'agent-worker' "$VAULT/.context-agent/runtime/run_remember_agent_v1_once.sh"
rg -q 'cognitive-action-worker' "$VAULT/.context-agent/runtime/run_remember_agent_v1_once.sh"
! rg -q 'daily-manual-worker|daily-run|daily-schedule' \
  "$VAULT/.context-agent/runtime/run_remember_agent_v1_once.sh"
! rg -q 'self-reflection-worker' \
  "$VAULT/.context-agent/runtime/run_remember_agent_v1_once.sh"

CONTEXT_AGENT_RUNTIME="$VAULT/.context-agent/runtime/context_agent.py"
CONTEXT_AGENT_BACKUP="$VAULT/.context-agent/runtime/context_agent.py.test-backup"
# 逐条 runner 先建立本地 ingest，再执行受 gate 约束的 worker；日期与调用顺序固定。
RECORD_RUNNER="$VAULT/.context-agent/runtime/run_cognitive_record_once.sh"
RECORD_CALL_LOG="$TMP_ROOT/record-runner-calls.jsonl"
cp "$CONTEXT_AGENT_RUNTIME" "$CONTEXT_AGENT_BACKUP"
cat > "$CONTEXT_AGENT_RUNTIME" <<'FAKE_RECORD_CONTEXT_AGENT'
import json
import os
import sys

arguments = sys.argv[1:]
with open(os.environ["MEMENTO_TEST_RECORD_CALL_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(arguments, ensure_ascii=False) + "\n")
if arguments and arguments[0] == "record-ingest":
    print('{"kind":"memento_record_ingest","status":"queued"}')
elif arguments and arguments[0] == "record-worker":
    print('{"kind":"memento_record_worker_run","status":"completed"}')
else:
    raise SystemExit(3)
FAKE_RECORD_CONTEXT_AGENT
: > "$RECORD_CALL_LOG"
RECORD_OUTPUT=$(env MEMENTO_TEST_RECORD_CALL_LOG="$RECORD_CALL_LOG" \
  bash "$RECORD_RUNNER" "$VAULT" 2026-08-18)
printf '%s' "$RECORD_OUTPUT" | python3 -c \
  'import json,sys; value=json.load(sys.stdin); assert value["kind"] == "memento_record_worker_run"'
python3 - "$RECORD_CALL_LOG" "$VAULT" <<'PY'
import json
import sys
from pathlib import Path

calls = [json.loads(line) for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()]
vault = sys.argv[2]
assert calls == [
    ["record-ingest", "--vault", vault, "--source", "2026-08-18.md"],
    ["record-worker", "--once", "--vault", vault, "--date", "2026-08-18", "--limit", "16"],
]
PY
if env MEMENTO_TEST_RECORD_CALL_LOG="$RECORD_CALL_LOG" \
  bash "$RECORD_RUNNER" "$VAULT" invalid-date >/dev/null 2>&1; then
  echo '逐条 runner 接受了非法日期' >&2
  exit 1
fi
mv "$CONTEXT_AGENT_BACKUP" "$CONTEXT_AGENT_RUNTIME"

# 当前安装版只落原始记录；append 不得唤醒逐条解释或任何 Provider。
RECORD_RUNNER_BACKUP="$RECORD_RUNNER.test-backup"
RECORD_WAKE_LOG="$TMP_ROOT/record-wake.log"
cp "$RECORD_RUNNER" "$RECORD_RUNNER_BACKUP"
cat > "$RECORD_RUNNER" <<'FAKE_RECORD_RUNNER'
#!/bin/bash
printf '%s\t%s\n' "$1" "$2" > "${MEMENTO_TEST_RECORD_WAKE_LOG:?}"
exit 37
FAKE_RECORD_RUNNER
chmod 700 "$RECORD_RUNNER"
CAPTURE_SENTINEL='COGNITIVE_WAKE_RAW_SAVE_SENTINEL'
env \
  HOME="$TEST_HOME" \
  MEMENTO_TEST_RECORD_WAKE_LOG="$RECORD_WAKE_LOG" \
  "$VAULT/.scripts/append_text.sh" "$CAPTURE_SENTINEL"
TODAY=$(date +%Y-%m-%d)
rg -qF "$CAPTURE_SENTINEL" "$VAULT/$TODAY.md"
[ ! -e "$RECORD_WAKE_LOG" ]
mv "$RECORD_RUNNER_BACKUP" "$RECORD_RUNNER"

CONTEXT_WORKER_PLIST="$TEST_HOME/Library/LaunchAgents/com.memento.context-agent.plist"
REMEMBER_AGENT_PLIST="$TEST_HOME/Library/LaunchAgents/com.memento.remember-agent-v1.plist"
REMEMBER_AGENT_SCHEDULE_PLIST="$TEST_HOME/Library/LaunchAgents/com.memento.remember-agent-v1-schedule.plist"
[ ! -e "$CONTEXT_WORKER_PLIST" ]
[ ! -e "$REMEMBER_AGENT_PLIST" ]
[ ! -e "$REMEMBER_AGENT_SCHEDULE_PLIST" ]

for NAME in \
  '存入 AI 秘书 (选标签)' \
  '存入 AI 秘书 (加备注)' \
  '存入 AI 秘书 (截图)'; do
  MARKER="$TEST_HOME/Library/Services/$NAME.workflow/Contents/.memento-managed"
  [ "$(cat "$MARKER")" = 'com.memento.workflow.v1' ]
done
for NAME in '存入 AI 秘书 (选标签)' '存入 AI 秘书 (加备注)'; do
  WORKFLOW="$TEST_HOME/Library/Services/$NAME.workflow/Contents"
  rg -q 'NSSendTypes' "$WORKFLOW/Info.plist"
  rg -q 'NSStringPboardType' "$WORKFLOW/Info.plist"
  rg -q 'public\.plain-text' "$WORKFLOW/Info.plist"
  rg -q 'com\.apple\.Automator\.text' "$WORKFLOW/document.wflow"
  ! rg -q 'capture_selection\.sh|pbpaste|keystroke' "$WORKFLOW/document.wflow"
done
[ ! -d "$TEST_HOME/Library/Services/存入 AI 秘书 (语音).workflow" ]

# 模拟用户数据、运行状态和上一版组件。二次安装只能移除可验证
# 归属的拍照/Review/自动化执行件，不得删用户事实、资产或认知状态。
printf '%s\n' 'USER_README_SENTINEL' >> "$VAULT/README.md"
mkdir -p "$VAULT/.review" "$VAULT/Reviews/Daily"
cp -R "$ROOT/daily-review/." "$VAULT/.review/"
# 同名但字节已被用户改写的协议文件不属于精确旧发行版，必须保留。
printf '%s\n' 'USER_CUSTOM_REVIEW_PROTOCOL_SENTINEL' > "$VAULT/.review/README.md"
mkdir -p "$VAULT/.review/status"
printf '%s\n' 'USER_REVIEW_STATUS_SENTINEL' > "$VAULT/.review/status/state.txt"
cat > "$VAULT/2026-07-16.md" <<'DAILY_NOTE'
---
date: 2026-07-16
type: memento-daily
---

## 09:00 · 周四

USER_DAILY_SENTINEL

---
DAILY_NOTE
printf '%s\n' 'USER_ASSET_SENTINEL' > "$VAULT/assets/user-asset.txt"
printf '%s\n' 'USER_REVIEW_SENTINEL' > "$VAULT/Reviews/Daily/2026-07-16.md"
printf '%s\n' 'USER_CONTEXT_DECISION_SENTINEL' > "$VAULT/.context-agent/decisions/user-decision.json"
printf '%s\n' 'USER_CONFIRMED_CONTEXT_SENTINEL' > "$VAULT/Context/Confirmed/user-context.json"
printf '%s\n' 'USER_SELF_REQUEST_SENTINEL' > "$VAULT/.context-agent/self-queries/requests/user-request.json"
printf '%s\n' 'USER_SELF_RESPONSE_SENTINEL' > "$VAULT/.context-agent/self-queries/responses/user-response.json"
printf '%s\n' 'USER_SELF_FEEDBACK_SENTINEL' > "$VAULT/.context-agent/self-queries/feedback/user-feedback.json"
printf '%s\n' 'USER_AGENT_REQUEST_SENTINEL' > "$AGENT_V1_ROOT/requests/user-request-state.json"
printf '%s\n' 'USER_AGENT_RESPONSE_SENTINEL' > "$AGENT_V1_ROOT/responses/user-response-state.json"
printf '%s\n' 'USER_AGENT_RUN_SENTINEL' > "$AGENT_V1_ROOT/runs/user-run-state.json"
printf '%s\n' 'USER_AGENT_MEMORY_SENTINEL' > "$AGENT_V1_ROOT/memories/user-memory-state.json"
printf '%s\n' 'USER_AGENT_ACTION_SENTINEL' > "$AGENT_V1_ROOT/user-actions/user-action-state.json"
printf '%s\n' 'USER_AGENT_LOCK_SENTINEL' > "$AGENT_V1_ROOT/locks/user-lock-state.txt"
mkdir -p \
  "$COGNITIVE_ROOT/receipts" \
  "$COGNITIVE_ROOT/daily-bundles/committed" \
  "$COGNITIVE_ROOT/projections"
printf '%s\n' 'USER_COGNITIVE_RECEIPT_SENTINEL' > \
  "$COGNITIVE_ROOT/receipts/user-receipt.json"
printf '%s\n' 'USER_COGNITIVE_BUNDLE_SENTINEL' > \
  "$COGNITIVE_ROOT/daily-bundles/committed/user-bundle.json"
printf '%s\n' 'USER_COGNITIVE_PROJECTION_SENTINEL' > \
  "$COGNITIVE_ROOT/projections/home_projection.json"
printf '%s\n' 'USER_COGNITIVE_ACTION_SENTINEL' > \
  "$COGNITIVE_ROOT/user-actions/user-action.json"
printf '%s\n' 'USER_MANUAL_REQUEST_SENTINEL' > \
  "$COGNITIVE_ROOT/manual-day-requests/manual-request.json"
printf '%s\n' 'USER_MANUAL_RESULT_SENTINEL' > \
  "$COGNITIVE_ROOT/manual-day-results/manual-result.json"
printf '%s\n' 'USER_COGNITIVE_LOG_SENTINEL' > \
  "$VAULT/.context-agent/logs/cognitive-user.log"
# 用户的 profile 是认知状态，升级时保留。enabled/schedule 是旧自动化控制，
# 安装器只在能验证格式与归属时关闭。
cat > "$AGENT_V1_ROOT/profile.json" <<'AGENT_PROFILE'
{
  "schema_version": "1.0",
  "kind": "remember_agent_profile",
  "sentinel": "USER_AGENT_PROFILE_SENTINEL"
}
AGENT_PROFILE
printf 'enabled-v1\n' > "$AGENT_V1_ROOT/enabled"
chmod 600 "$AGENT_V1_ROOT/enabled"
cat > "$AGENT_V1_ROOT/schedule.json" <<'SCHEDULE_CONFIG'
{
  "schema_version": "1.0",
  "kind": "remember_agent_schedule",
  "enabled": true,
  "cadence": "daily",
  "hour": 21,
  "minute": 0,
  "updated_at": "2026-08-16T19:00:00+08:00"
}
SCHEDULE_CONFIG

# 安全普通首页只迁移精确旧模板文案，其他用户内容必须保留。
cat >> "$VAULT/Memento.md" <<'OLD_HOME_PAGE_BLOCK'

# USER_HOME_PAGE_SENTINEL
- [[Reviews.base|AI Daily Review 索引]]

## AI Daily Review

![[Reviews.base]]
OLD_HOME_PAGE_BLOCK
HOME_PAGE_BEFORE_INODE=$(stat -f %i "$VAULT/Memento.md")

# POSIX mode 之外的显式 ACL 也必须在完整安装的隐私收紧阶段移除。
# 单文件 metadata 迁移函数仍单独验证原子替换会保留 ACL/xattr；最终安装边界更严格。
chmod +a 'everyone allow read' "$VAULT"
chmod +a 'everyone allow read' "$VAULT/README.md"

SNAPSHOT_EXEC="$VAULT/.apps/Memento Daily Snapshot.app/Contents/MacOS/MementoDailySnapshot"
VOICE_EXEC="$VAULT/.apps/Memento Voice Capture.app/Contents/MacOS/MementoVoiceCapture"
SELECTION_COPY_EXEC="$VAULT/.apps/Memento Selection Copy.app/Contents/MacOS/MementoSelectionCopy"
mkdir -p \
  "$(dirname "$SNAPSHOT_EXEC")" \
  "$(dirname "$VOICE_EXEC")" \
  "$(dirname "$SELECTION_COPY_EXEC")"
cp "$ROOT/snapshot-capture/Info.plist" \
  "$VAULT/.apps/Memento Daily Snapshot.app/Contents/Info.plist"
printf '%s\n' 'OLD_SNAPSHOT_APP_SENTINEL' > "$SNAPSHOT_EXEC"
printf '%s\n' 'OLD_VOICE_APP_SENTINEL' > "$VOICE_EXEC"
printf '%s\n' '#!/bin/bash' 'exit 0' > "$SELECTION_COPY_EXEC"
chmod 700 "$SNAPSHOT_EXEC" "$VOICE_EXEC" "$SELECTION_COPY_EXEC"

# 上一版由 Memento 拥有的 3 个 job 必须在升级时停止并移除。
cat > "$CONTEXT_WORKER_PLIST" <<OWNED_CONTEXT_WORKER_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.memento.context-agent</string>
  <key>MementoManaged</key><string>com.memento.context-agent.v1</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string>
    <string>$VAULT/.context-agent/runtime/run_self_reflection_once.sh</string>
    <string>self-reflection-worker</string>
  </array>
</dict></plist>
OWNED_CONTEXT_WORKER_EOF
cat > "$REMEMBER_AGENT_PLIST" <<OWNED_REMEMBER_AGENT_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.memento.remember-agent-v1</string>
  <key>MementoManaged</key><string>com.memento.remember-agent-v1.v1</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string>
    <string>$VAULT/.context-agent/runtime/run_remember_agent_v1_once.sh</string>
  </array>
</dict></plist>
OWNED_REMEMBER_AGENT_EOF
cat > "$REMEMBER_AGENT_SCHEDULE_PLIST" <<OWNED_REMEMBER_AGENT_SCHEDULE_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.memento.remember-agent-v1-schedule</string>
  <key>MementoManaged</key><string>com.memento.remember-agent-v1-schedule.v1</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string>
    <string>$VAULT/.context-agent/runtime/run_remember_agent_schedule_once.sh</string>
  </array>
</dict></plist>
OWNED_REMEMBER_AGENT_SCHEDULE_EOF
chmod 600 \
  "$CONTEXT_WORKER_PLIST" \
  "$REMEMBER_AGENT_PLIST" \
  "$REMEMBER_AGENT_SCHEDULE_PLIST"

README_HASH=$(shasum -a 256 "$VAULT/README.md" | awk '{print $1}')
STATUS_HASH=$(shasum -a 256 "$VAULT/.review/status/state.txt" | awk '{print $1}')
CONTEXT_DECISION_HASH=$(shasum -a 256 "$VAULT/.context-agent/decisions/user-decision.json" | awk '{print $1}')
CONFIRMED_CONTEXT_HASH=$(shasum -a 256 "$VAULT/Context/Confirmed/user-context.json" | awk '{print $1}')
SELF_REQUEST_HASH=$(shasum -a 256 "$VAULT/.context-agent/self-queries/requests/user-request.json" | awk '{print $1}')
SELF_RESPONSE_HASH=$(shasum -a 256 "$VAULT/.context-agent/self-queries/responses/user-response.json" | awk '{print $1}')
SELF_FEEDBACK_HASH=$(shasum -a 256 "$VAULT/.context-agent/self-queries/feedback/user-feedback.json" | awk '{print $1}')
AGENT_PROFILE_HASH=$(shasum -a 256 "$AGENT_V1_ROOT/profile.json" | awk '{print $1}')
COGNITIVE_RECEIPT_HASH=$(shasum -a 256 \
  "$COGNITIVE_ROOT/receipts/user-receipt.json" | awk '{print $1}')
COGNITIVE_BUNDLE_HASH=$(shasum -a 256 \
  "$COGNITIVE_ROOT/daily-bundles/committed/user-bundle.json" | awk '{print $1}')
COGNITIVE_PROJECTION_HASH=$(shasum -a 256 \
  "$COGNITIVE_ROOT/projections/home_projection.json" | awk '{print $1}')
COGNITIVE_LOG_HASH=$(shasum -a 256 \
  "$VAULT/.context-agent/logs/cognitive-user.log" | awk '{print $1}')
SCRIPT_HASH=$(shasum -a 256 "$VAULT/.scripts/append_text.sh" | awk '{print $1}')
VOICE_HASH=$(shasum -a 256 "$VOICE_EXEC" | awk '{print $1}')

# 已有 Vault 提示回答 y，Chrome 提示回答 n。
run_install 'yn' "$LOG_DIR/install-second.log"

[ "$README_HASH" = "$(shasum -a 256 "$VAULT/README.md" | awk '{print $1}')" ]
[ ! -L "$VAULT/Memento.md" ]
[ "$HOME_PAGE_BEFORE_INODE" != "$(stat -f %i "$VAULT/Memento.md")" ]
rg -qF '# USER_HOME_PAGE_SENTINEL' "$VAULT/Memento.md"
! rg -qF '## AI Daily Review' "$VAULT/Memento.md"
! rg -qF '[[Reviews.base|AI Daily Review 索引]]' "$VAULT/Memento.md"
[ "$STATUS_HASH" = "$(shasum -a 256 "$VAULT/.review/status/state.txt" | awk '{print $1}')" ]
[ "$CONTEXT_DECISION_HASH" = "$(shasum -a 256 "$VAULT/.context-agent/decisions/user-decision.json" | awk '{print $1}')" ]
[ "$CONFIRMED_CONTEXT_HASH" = "$(shasum -a 256 "$VAULT/Context/Confirmed/user-context.json" | awk '{print $1}')" ]
[ "$SELF_REQUEST_HASH" = "$(shasum -a 256 "$VAULT/.context-agent/self-queries/requests/user-request.json" | awk '{print $1}')" ]
[ "$SELF_RESPONSE_HASH" = "$(shasum -a 256 "$VAULT/.context-agent/self-queries/responses/user-response.json" | awk '{print $1}')" ]
[ "$SELF_FEEDBACK_HASH" = "$(shasum -a 256 "$VAULT/.context-agent/self-queries/feedback/user-feedback.json" | awk '{print $1}')" ]
[ "$AGENT_PROFILE_HASH" = "$(shasum -a 256 "$AGENT_V1_ROOT/profile.json" | awk '{print $1}')" ]
[ "$(cat "$AGENT_V1_ROOT/requests/user-request-state.json")" = 'USER_AGENT_REQUEST_SENTINEL' ]
[ "$(cat "$AGENT_V1_ROOT/responses/user-response-state.json")" = 'USER_AGENT_RESPONSE_SENTINEL' ]
[ "$(cat "$AGENT_V1_ROOT/runs/user-run-state.json")" = 'USER_AGENT_RUN_SENTINEL' ]
[ "$(cat "$AGENT_V1_ROOT/memories/user-memory-state.json")" = 'USER_AGENT_MEMORY_SENTINEL' ]
[ "$(cat "$AGENT_V1_ROOT/user-actions/user-action-state.json")" = 'USER_AGENT_ACTION_SENTINEL' ]
[ "$(cat "$AGENT_V1_ROOT/locks/user-lock-state.txt")" = 'USER_AGENT_LOCK_SENTINEL' ]
[ "$COGNITIVE_RECEIPT_HASH" = "$(shasum -a 256 \
  "$COGNITIVE_ROOT/receipts/user-receipt.json" | awk '{print $1}')" ]
[ "$COGNITIVE_BUNDLE_HASH" = "$(shasum -a 256 \
  "$COGNITIVE_ROOT/daily-bundles/committed/user-bundle.json" | awk '{print $1}')" ]
[ "$COGNITIVE_PROJECTION_HASH" = "$(shasum -a 256 \
  "$COGNITIVE_ROOT/projections/home_projection.json" | awk '{print $1}')" ]
[ "$(cat "$COGNITIVE_ROOT/user-actions/user-action.json")" = 'USER_COGNITIVE_ACTION_SENTINEL' ]
[ "$(cat "$COGNITIVE_ROOT/manual-day-requests/manual-request.json")" = 'USER_MANUAL_REQUEST_SENTINEL' ]
[ "$(cat "$COGNITIVE_ROOT/manual-day-results/manual-result.json")" = 'USER_MANUAL_RESULT_SENTINEL' ]
[ "$COGNITIVE_LOG_HASH" = "$(shasum -a 256 \
  "$VAULT/.context-agent/logs/cognitive-user.log" | awk '{print $1}')" ]
[ "$SCRIPT_HASH" = "$(shasum -a 256 "$VAULT/.scripts/append_text.sh" | awk '{print $1}')" ]
[ ! -e "$VAULT/.apps/Memento Daily Snapshot.app" ]
[ "$VOICE_HASH" = "$(shasum -a 256 "$VOICE_EXEC" | awk '{print $1}')" ]
[ "$(cat "$USER_WF/Contents/document.wflow")" = 'USER_WORKFLOW_SENTINEL' ]
[ -f "$VAULT/.chrome-newtab/manifest.json" ]
[ -f "$VAULT/.chrome-newtab/cognitive-demo-fixture.js" ]
[ -f "$VAULT/.chrome-newtab/context-agent-library.js" ]
[ -f "$VAULT/.chrome-newtab/remember-agent-v1-library.js" ]
[ -f "$VAULT/.chrome-newtab/dashboard-cache-library.js" ]
[ -f "$VAULT/.chrome-newtab/photo-cache-library.js" ]
for REMOVED_REVIEW_FILE in \
  DAILY_REVIEW.md commit_review.sh commit_review_atomic.py \
  review_cycle.sh review_state.sh review_status.sh verify_review.sh; do
  [ ! -e "$VAULT/.review/$REMOVED_REVIEW_FILE" ]
done
[ "$(cat "$VAULT/.review/README.md")" = 'USER_CUSTOM_REVIEW_PROTOCOL_SENTINEL' ]
[ "$(cat "$VAULT/.review/status/state.txt")" = 'USER_REVIEW_STATUS_SENTINEL' ]
[ -x "$VAULT/.context-agent/runtime/context_agent.py" ]
[ -f "$VAULT/.context-agent/runtime/agent_v1.py" ]
[ -f "$VAULT/.context-agent/runtime/cognitive_v1.py" ]
[ -f "$VAULT/.context-agent/runtime/cognitive_daily_review_v1.py" ]
[ -x "$VAULT/.context-agent/runtime/run_self_reflection_once.sh" ]
[ -x "$VAULT/.context-agent/runtime/run_remember_agent_v1_once.sh" ]
[ -x "$VAULT/.context-agent/runtime/run_cognitive_record_once.sh" ]
[ ! -e "$VAULT/.context-agent/runtime/run_remember_agent_schedule_once.sh" ]
[ ! -e "$CONTEXT_WORKER_PLIST" ]
[ ! -e "$REMEMBER_AGENT_PLIST" ]
[ ! -e "$REMEMBER_AGENT_SCHEDULE_PLIST" ]
[ ! -e "$AGENT_V1_ROOT/enabled" ]
[ ! -e "$AGENT_V1_ROOT/schedule.json" ]
[ "$(cat "$TEST_HOME/Library/Services/存入 AI 秘书 (语音).workflow/Contents/.memento-managed")" = 'com.memento.workflow.v1' ]
for NAME in '存入 AI 秘书 (选标签)' '存入 AI 秘书 (加备注)'; do
  WORKFLOW="$TEST_HOME/Library/Services/$NAME.workflow/Contents"
  rg -q 'com\.apple\.Automator\.nothing' "$WORKFLOW/document.wflow"
  rg -q 'MementoSelectionCopy' "$WORKFLOW/document.wflow"
  rg -q 'pbpaste -Prefer txt' "$WORKFLOW/document.wflow"
  rg -q 'selection-shortcut\.log' "$WORKFLOW/document.wflow"
  rg -q 'phase=capture ready=1 bytes=' "$WORKFLOW/document.wflow"
  rg -q 'append_text\.sh.*CAPTURE_FILE' "$WORKFLOW/document.wflow"
  ! rg -q 'capture_selected_text\.sh' "$WORKFLOW/document.wflow"
  ! rg -q 'NSSendTypes|NSStringPboardType|public\.plain-text' "$WORKFLOW/Info.plist"
done
[ ! -e "$VAULT/.scripts/capture_selected_text.sh" ]
rg -q 'shortcut=1 phase=start' "$ROOT/install_aisecretary.sh"
rg -q 'shortcut=1 phase=capture ready=1 bytes=' "$ROOT/install_aisecretary.sh"
rg -q 'accessibilitySelection = "selected-text"' \
  "$ROOT/selection-copy-helper/MementoSelectionCopy.swift"
rg -q 'pressCopyMenuItem' "$ROOT/selection-copy-helper/MementoSelectionCopy.swift"
rg -q 'postCommandCopy' "$ROOT/selection-copy-helper/MementoSelectionCopy.swift"

# 内部 daily bundle/renderer 仍保持可导入，但当前安装态没有执行入口、job 或 gate。
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$VAULT/.context-agent/runtime" python3 -c \
  'import context_agent, cognitive_daily_review_v1, cognitive_day_orchestrator_v1'

# 下一次升级中，同名首页 symlink 不得被复制或迁移跟随到 Vault 之外。
EXTERNAL_HOME_PAGE="$TMP_ROOT/external-memento-home.md"
cat > "$EXTERNAL_HOME_PAGE" <<'EXTERNAL_HOME_PAGE_EOF'
# EXTERNAL_HOME_PAGE_SENTINEL

- [[Reviews.base|AI Daily Review 索引]]

## AI Daily Review

![[Reviews.base]]
EXTERNAL_HOME_PAGE_EOF
mv "$VAULT/Memento.md" "$VAULT/Memento.user-preserved.md"
ln -s "$EXTERNAL_HOME_PAGE" "$VAULT/Memento.md"
EXTERNAL_HOME_PAGE_HASH=$(shasum -a 256 "$EXTERNAL_HOME_PAGE" | awk '{print $1}')

# 同名但没有 Memento marker/专属 runner 的用户 job 在再次升级时也必须保留。
cat > "$CONTEXT_WORKER_PLIST" <<'FOREIGN_INSTALL_CONTEXT_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.example.foreign-context-install</string>
  <key>ProgramArguments</key><array><string>/bin/true</string></array>
</dict></plist>
FOREIGN_INSTALL_CONTEXT_EOF
cat > "$REMEMBER_AGENT_PLIST" <<'FOREIGN_INSTALL_REMEMBER_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.example.foreign-remember-install</string>
  <key>ProgramArguments</key><array><string>/bin/true</string></array>
</dict></plist>
FOREIGN_INSTALL_REMEMBER_EOF
cat > "$REMEMBER_AGENT_SCHEDULE_PLIST" <<'FOREIGN_INSTALL_SCHEDULE_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.example.foreign-schedule-install</string>
  <key>ProgramArguments</key><array><string>/bin/true</string></array>
</dict></plist>
FOREIGN_INSTALL_SCHEDULE_EOF
FOREIGN_CONTEXT_HASH=$(shasum -a 256 "$CONTEXT_WORKER_PLIST" | awk '{print $1}')
FOREIGN_REMEMBER_HASH=$(shasum -a 256 "$REMEMBER_AGENT_PLIST" | awk '{print $1}')
FOREIGN_SCHEDULE_HASH=$(shasum -a 256 "$REMEMBER_AGENT_SCHEDULE_PLIST" | awk '{print $1}')
run_install 'yn' "$LOG_DIR/install-third-foreign.log"
[ "$FOREIGN_CONTEXT_HASH" = "$(shasum -a 256 "$CONTEXT_WORKER_PLIST" | awk '{print $1}')" ]
[ "$FOREIGN_REMEMBER_HASH" = "$(shasum -a 256 "$REMEMBER_AGENT_PLIST" | awk '{print $1}')" ]
[ "$FOREIGN_SCHEDULE_HASH" = "$(shasum -a 256 "$REMEMBER_AGENT_SCHEDULE_PLIST" | awk '{print $1}')" ]
[ -L "$VAULT/Memento.md" ]
[ "$EXTERNAL_HOME_PAGE_HASH" = "$(shasum -a 256 "$EXTERNAL_HOME_PAGE" | awk '{print $1}')" ]
[ "$(cat "$VAULT/.review/README.md")" = 'USER_CUSTOM_REVIEW_PROTOCOL_SENTINEL' ]
[ ! -e "$VAULT/.apps/Memento Daily Snapshot.app" ]
[ ! -e "$AGENT_V1_ROOT/enabled" ]
[ ! -e "$AGENT_V1_ROOT/schedule.json" ]

# Vault 与事实文件默认仅当前用户可读；升级不能留下 staging/backup/lock。
[ "$(stat -f %Lp "$VAULT")" = '700' ]
for PRIVATE_FILE in \
  "$VAULT/README.md" \
  "$VAULT/2026-07-16.md" \
  "$VAULT/assets/user-asset.txt" \
  "$VAULT/Reviews/Daily/2026-07-16.md" \
  "$VAULT/.review/README.md" \
  "$VAULT/.review/status/state.txt" \
  "$AGENT_V1_ROOT/profile.json" \
  "$AGENT_V1_ROOT/memories/user-memory-state.json" \
  "$AGENT_V1_ROOT/user-actions/user-action-state.json" \
  "$COGNITIVE_ROOT/receipts/user-receipt.json" \
  "$COGNITIVE_ROOT/daily-bundles/committed/user-bundle.json" \
  "$COGNITIVE_ROOT/projections/home_projection.json" \
  "$VAULT/.context-agent/logs/cognitive-user.log"; do
  [ "$(stat -f %Lp "$PRIVATE_FILE")" = '600' ]
done
for ACL_TARGET in "$VAULT" "$VAULT/README.md"; do
  if ls -led "$ACL_TARGET" | tail -n +2 | rg -q '^[[:space:]]*[0-9]+:'; then
    echo "安装后仍残留可绕过 0700/0600 的扩展 ACL: $ACL_TARGET" >&2
    exit 1
  fi
done

if find "$TEST_HOME" \
  \( -name '.memento-install.lock' \
     -o -name '.memento-workflow.*' \
     -o -name '.memento-*-build.*' \
     -o -name '.memento-backup.*' \
     -o -name '.scripts-stage.*' \
     -o -name '.chrome-newtab-stage.*' \
     -o -name '.review-stage.*' \
     -o -name '.context-agent-runtime-stage.*' \
     -o -name '.ocr-image.*' \
     -o -name '.append-daily-snapshot.*' \) \
  -print -quit | rg -q .; then
  echo '安装后遗留 staging、backup 或 lock' >&2
  exit 1
fi

# 默认卸载回答 n：只移除执行组件，保留事实、资产、Reviews 和用户 Workflow。
# 无法确认归属的同名 LaunchAgent 也必须保留。
cat > "$LEGACY_PLIST" <<'FOREIGN_PLIST_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.example.foreign</string>
  <key>ProgramArguments</key><array><string>/bin/true</string></array>
</dict></plist>
FOREIGN_PLIST_EOF
run_uninstall 'n' "$LOG_DIR/uninstall-first.log"

[ ! -d "$VAULT/.scripts" ]
[ ! -d "$VAULT/.apps" ]
[ ! -d "$VAULT/.chrome-newtab" ]
[ ! -d "$VAULT/.review" ]
[ ! -d "$VAULT/.context-agent/runtime" ]
[ "$FOREIGN_CONTEXT_HASH" = "$(shasum -a 256 "$CONTEXT_WORKER_PLIST" | awk '{print $1}')" ]
[ "$FOREIGN_REMEMBER_HASH" = "$(shasum -a 256 "$REMEMBER_AGENT_PLIST" | awk '{print $1}')" ]
[ "$FOREIGN_SCHEDULE_HASH" = "$(shasum -a 256 "$REMEMBER_AGENT_SCHEDULE_PLIST" | awk '{print $1}')" ]
[ "$(cat "$VAULT/.context-agent/decisions/user-decision.json")" = 'USER_CONTEXT_DECISION_SENTINEL' ]
[ "$(cat "$VAULT/Context/Confirmed/user-context.json")" = 'USER_CONFIRMED_CONTEXT_SENTINEL' ]
[ "$(cat "$VAULT/.context-agent/self-queries/requests/user-request.json")" = 'USER_SELF_REQUEST_SENTINEL' ]
[ "$(cat "$VAULT/.context-agent/self-queries/responses/user-response.json")" = 'USER_SELF_RESPONSE_SENTINEL' ]
[ "$(cat "$VAULT/.context-agent/self-queries/feedback/user-feedback.json")" = 'USER_SELF_FEEDBACK_SENTINEL' ]
[ "$AGENT_PROFILE_HASH" = "$(shasum -a 256 "$AGENT_V1_ROOT/profile.json" | awk '{print $1}')" ]
[ "$(cat "$AGENT_V1_ROOT/requests/user-request-state.json")" = 'USER_AGENT_REQUEST_SENTINEL' ]
[ "$(cat "$AGENT_V1_ROOT/responses/user-response-state.json")" = 'USER_AGENT_RESPONSE_SENTINEL' ]
[ "$(cat "$AGENT_V1_ROOT/runs/user-run-state.json")" = 'USER_AGENT_RUN_SENTINEL' ]
[ "$(cat "$AGENT_V1_ROOT/memories/user-memory-state.json")" = 'USER_AGENT_MEMORY_SENTINEL' ]
[ "$(cat "$AGENT_V1_ROOT/user-actions/user-action-state.json")" = 'USER_AGENT_ACTION_SENTINEL' ]
[ "$(cat "$AGENT_V1_ROOT/locks/user-lock-state.txt")" = 'USER_AGENT_LOCK_SENTINEL' ]
[ "$COGNITIVE_RECEIPT_HASH" = "$(shasum -a 256 \
  "$COGNITIVE_ROOT/receipts/user-receipt.json" | awk '{print $1}')" ]
[ "$COGNITIVE_BUNDLE_HASH" = "$(shasum -a 256 \
  "$COGNITIVE_ROOT/daily-bundles/committed/user-bundle.json" | awk '{print $1}')" ]
[ "$COGNITIVE_PROJECTION_HASH" = "$(shasum -a 256 \
  "$COGNITIVE_ROOT/projections/home_projection.json" | awk '{print $1}')" ]
[ "$(cat "$COGNITIVE_ROOT/user-actions/user-action.json")" = 'USER_COGNITIVE_ACTION_SENTINEL' ]
[ "$(cat "$COGNITIVE_ROOT/manual-day-requests/manual-request.json")" = 'USER_MANUAL_REQUEST_SENTINEL' ]
[ "$(cat "$COGNITIVE_ROOT/manual-day-results/manual-result.json")" = 'USER_MANUAL_RESULT_SENTINEL' ]
[ "$COGNITIVE_LOG_HASH" = "$(shasum -a 256 \
  "$VAULT/.context-agent/logs/cognitive-user.log" | awk '{print $1}')" ]
[ ! -e "$AGENT_V1_ROOT/enabled" ]
[ ! -e "$AGENT_V1_ROOT/schedule.json" ]
[ -f "$LEGACY_PLIST" ]
[ -f "$VAULT/2026-07-16.md" ]
[ "$(cat "$VAULT/assets/user-asset.txt")" = 'USER_ASSET_SENTINEL' ]
[ "$(cat "$VAULT/Reviews/Daily/2026-07-16.md")" = 'USER_REVIEW_SENTINEL' ]
rg -qF 'USER_README_SENTINEL' "$VAULT/README.md"
[ "$(cat "$USER_WF/Contents/document.wflow")" = 'USER_WORKFLOW_SENTINEL' ]
[ ! -d "$TEST_HOME/.memento-install.lock" ]
rg -q 'receipt/bundle/projection' "$LOG_DIR/uninstall-first.log"

for NAME in \
  '存入 AI 秘书 (选标签)' \
  '存入 AI 秘书 (加备注)' \
  '存入 AI 秘书 (截图)' \
  '存入 AI 秘书 (语音)'; do
  [ ! -d "$TEST_HOME/Library/Services/$NAME.workflow" ]
done

# 卸载幂等：第二次运行仍不删除默认保留的数据和未托管 Workflow/LaunchAgent。
cat > "$CONTEXT_WORKER_PLIST" <<'FOREIGN_CONTEXT_WORKER_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.example.foreign-context-worker</string>
  <key>ProgramArguments</key><array><string>/bin/true</string></array>
</dict></plist>
FOREIGN_CONTEXT_WORKER_EOF
cat > "$REMEMBER_AGENT_PLIST" <<'FOREIGN_REMEMBER_AGENT_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.example.foreign-remember-agent</string>
  <key>ProgramArguments</key><array><string>/bin/true</string></array>
</dict></plist>
FOREIGN_REMEMBER_AGENT_EOF
cat > "$REMEMBER_AGENT_SCHEDULE_PLIST" <<'FOREIGN_REMEMBER_AGENT_SCHEDULE_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.example.foreign-remember-agent-schedule</string>
  <key>ProgramArguments</key><array><string>/bin/true</string></array>
</dict></plist>
FOREIGN_REMEMBER_AGENT_SCHEDULE_EOF
run_uninstall 'n' "$LOG_DIR/uninstall-second.log"
[ -f "$VAULT/2026-07-16.md" ]
[ -f "$VAULT/assets/user-asset.txt" ]
[ -f "$VAULT/Reviews/Daily/2026-07-16.md" ]
[ "$(cat "$USER_WF/Contents/document.wflow")" = 'USER_WORKFLOW_SENTINEL' ]
[ -f "$CONTEXT_WORKER_PLIST" ]
[ -f "$REMEMBER_AGENT_PLIST" ]
[ -f "$REMEMBER_AGENT_SCHEDULE_PLIST" ]
[ ! -e "$AGENT_V1_ROOT/enabled" ]
[ ! -e "$AGENT_V1_ROOT/schedule.json" ]

# 活跃安装锁必须 fail closed，且不能在退出时删除别人的锁或开始写入。
mkdir "$TEST_HOME/.memento-install.lock"
printf '%s\n' 'foreign-token' > "$TEST_HOME/.memento-install.lock/token"
printf '%s\n' "$$" > "$TEST_HOME/.memento-install.lock/pid"
set +e
run_install 'yn' "$LOG_DIR/install-locked.log"
LOCKED_STATUS=$?
set -e
[ "$LOCKED_STATUS" -ne 0 ]
[ "$(cat "$TEST_HOME/.memento-install.lock/token")" = 'foreign-token' ]
[ ! -d "$VAULT/.scripts" ]
rm -rf "$TEST_HOME/.memento-install.lock"

# Vault 根本身是 symlink 时必须在任何 Vault 内写入/删除前停止。
SYMLINK_HOME="$TMP_ROOT/symlink-home"
EXTERNAL_VAULT="$TMP_ROOT/external-vault"
mkdir -p \
  "$SYMLINK_HOME/Library/Services" \
  "$SYMLINK_HOME/Library/LaunchAgents" \
  "$EXTERNAL_VAULT/.review" \
  "$EXTERNAL_VAULT/.apps/Memento Daily Snapshot.app/Contents/MacOS"
printf '%s\n' 'EXTERNAL_VAULT_SENTINEL' > "$EXTERNAL_VAULT/user-data.md"
cp "$ROOT/daily-review/DAILY_REVIEW.md" "$EXTERNAL_VAULT/.review/DAILY_REVIEW.md"
cp "$ROOT/snapshot-capture/Info.plist" \
  "$EXTERNAL_VAULT/.apps/Memento Daily Snapshot.app/Contents/Info.plist"
printf '%s\n' 'EXTERNAL_SNAPSHOT_SENTINEL' > \
  "$EXTERNAL_VAULT/.apps/Memento Daily Snapshot.app/Contents/MacOS/MementoDailySnapshot"
chmod 700 \
  "$EXTERNAL_VAULT/.apps/Memento Daily Snapshot.app/Contents/MacOS/MementoDailySnapshot"
ln -s "$EXTERNAL_VAULT" "$SYMLINK_HOME/AISecretary"
EXTERNAL_REVIEW_HASH=$(shasum -a 256 \
  "$EXTERNAL_VAULT/.review/DAILY_REVIEW.md" | awk '{print $1}')
set +e
run_install_for_home "$SYMLINK_HOME" 'y' "$LOG_DIR/install-symlink-vault.log"
SYMLINK_VAULT_STATUS=$?
set -e
[ "$SYMLINK_VAULT_STATUS" -ne 0 ]
[ -L "$SYMLINK_HOME/AISecretary" ]
[ "$(cat "$EXTERNAL_VAULT/user-data.md")" = 'EXTERNAL_VAULT_SENTINEL' ]
[ "$EXTERNAL_REVIEW_HASH" = "$(shasum -a 256 \
  "$EXTERNAL_VAULT/.review/DAILY_REVIEW.md" | awk '{print $1}')" ]
[ -e "$EXTERNAL_VAULT/.apps/Memento Daily Snapshot.app" ]
[ ! -e "$EXTERNAL_VAULT/.scripts" ]
[ ! -e "$SYMLINK_HOME/.memento-install.lock" ]

echo '✓ installer contract: isolated install, idempotent upgrade and data-preserving uninstall'
