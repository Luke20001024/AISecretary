#!/bin/bash
# 在隔离 HOME 中验证 Context Agent 安装 / 卸载不跟随符号链接。

set -euo pipefail
umask 077

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# Installer rejects symlinked HOME ancestor chains (macOS /var is commonly a
# symlink to /private/var). Keep the isolated HOME under the trusted workspace.
TMP_ROOT=$(mktemp -d "$ROOT/.memento-context-install-security.XXXXXX")
FAKE_BIN="$TMP_ROOT/bin"
LAUNCHCTL_LOG="$TMP_ROOT/launchctl.log"

cleanup() {
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/swiftc" <<'FAKE_SWIFTC'
#!/bin/bash
exit 42
FAKE_SWIFTC
cat > "$FAKE_BIN/launchctl" <<'FAKE_LAUNCHCTL'
#!/bin/bash
printf '%s\n' "$*" >> "${MEMENTO_FAKE_LAUNCHCTL_LOG:?}"
exit 0
FAKE_LAUNCHCTL
chmod 700 "$FAKE_BIN/swiftc" "$FAKE_BIN/launchctl"
: > "$LAUNCHCTL_LOG"

prepare_home() {
  local test_home="$1"
  mkdir -p \
    "$test_home/Library/Services" \
    "$test_home/Library/LaunchAgents"
}

run_install_skip_load() {
  local test_home="$1"
  local replies="$2"
  local log="$3"
  printf '%s' "$replies" | env \
    HOME="$test_home" \
    PATH="$FAKE_BIN:$PATH" \
    MEMENTO_FAKE_LAUNCHCTL_LOG="$LAUNCHCTL_LOG" \
    MEMENTO_SKIP_SERVICE_REFRESH=1 \
    MEMENTO_SKIP_LEGACY_LAUNCHAGENT_UNLOAD=1 \
    MEMENTO_SKIP_CONTEXT_WORKER_LOAD=1 \
    bash "$ROOT/install_aisecretary.sh" >"$log" 2>&1
}

run_install_with_fake_launchctl() {
  local test_home="$1"
  local replies="$2"
  local log="$3"
  printf '%s' "$replies" | env \
    HOME="$test_home" \
    PATH="$FAKE_BIN:$PATH" \
    MEMENTO_FAKE_LAUNCHCTL_LOG="$LAUNCHCTL_LOG" \
    MEMENTO_SKIP_SERVICE_REFRESH=1 \
    MEMENTO_SKIP_LEGACY_LAUNCHAGENT_UNLOAD=1 \
    bash "$ROOT/install_aisecretary.sh" >"$log" 2>&1
}

run_uninstall_with_fake_launchctl() {
  local test_home="$1"
  local replies="$2"
  local log="$3"
  printf '%s' "$replies" | env \
    HOME="$test_home" \
    PATH="$FAKE_BIN:$PATH" \
    MEMENTO_FAKE_LAUNCHCTL_LOG="$LAUNCHCTL_LOG" \
    MEMENTO_SKIP_SERVICE_REFRESH=1 \
    MEMENTO_SKIP_LEGACY_LAUNCHAGENT_UNLOAD=1 \
    bash "$ROOT/uninstall_aisecretary.sh" >"$log" 2>&1
}

file_snapshot() {
  local path="$1"
  printf '%s:%s\n' \
    "$(shasum -a 256 "$path" | awk '{print $1}')" \
    "$(stat -f %Lp "$path")"
}

write_context_plist() {
  local output="$1"
  local managed="$2"
  python3 - "$output" "$managed" <<'PY'
import plistlib
import sys
from pathlib import Path

output, managed = sys.argv[1:]
payload = {
    "Label": "com.memento.context-agent",
    "ProgramArguments": ["/bin/bash", "/tmp/run_context_workers_once.sh", "/tmp/vault"],
}
if managed == "yes":
    payload["MementoManaged"] = "com.memento.context-agent.v1"
with Path(output).open("wb") as handle:
    plistlib.dump(payload, handle, fmt=plistlib.FMT_XML, sort_keys=True)
PY
}

write_remember_agent_plist() {
  local output="$1"
  local managed="$2"
  python3 - "$output" "$managed" <<'PY'
import plistlib
import sys
from pathlib import Path

output, managed = sys.argv[1:]
payload = {
    "Label": "com.memento.remember-agent-v1",
    "ProgramArguments": ["/bin/bash", "/tmp/run_remember_agent_v1_once.sh", "/tmp/vault"],
}
if managed == "yes":
    payload["MementoManaged"] = "com.memento.remember-agent-v1.v1"
with Path(output).open("wb") as handle:
    plistlib.dump(payload, handle, fmt=plistlib.FMT_XML, sort_keys=True)
PY
}

write_remember_agent_schedule_plist() {
  local output="$1"
  local managed="$2"
  python3 - "$output" "$managed" <<'PY'
import plistlib
import sys
from pathlib import Path

output, managed = sys.argv[1:]
payload = {
    "Label": "com.memento.remember-agent-v1-schedule",
    "ProgramArguments": ["/bin/bash", "/tmp/run_remember_agent_schedule_once.sh", "/tmp/vault"],
    "StartCalendarInterval": [
        {"Hour": 21, "Minute": 0},
        {"Hour": 8, "Minute": 0},
    ],
}
if managed == "yes":
    payload["MementoManaged"] = "com.memento.remember-agent-v1-schedule.v1"
with Path(output).open("wb") as handle:
    plistlib.dump(payload, handle, fmt=plistlib.FMT_XML, sort_keys=True)
PY
}

run_directory_symlink_case() {
  local name="$1"
  local relative_path="$2"
  local also_uninstall="$3"
  local test_home="$TMP_ROOT/home-$name"
  local vault="$test_home/AISecretary"
  local external="$TMP_ROOT/external-$name"
  local sentinel="$external/sentinel.txt"
  local link_path="$vault/$relative_path"
  local before

  prepare_home "$test_home"
  mkdir -p "$vault" "$external" "$(dirname "$link_path")"
  printf '%s\n' "EXTERNAL_${name}_SENTINEL" > "$sentinel"
  before=$(file_snapshot "$sentinel")
  ln -s "$external" "$link_path"

  run_install_skip_load "$test_home" 'yn' "$TMP_ROOT/install-$name.log"
  [ -L "$link_path" ]
  [ "$before" = "$(file_snapshot "$sentinel")" ]
  [ "$(find "$external" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')" = '1' ]
  rg -q 'fail-closed|安全校验失败' "$TMP_ROOT/install-$name.log"

  if [ "$also_uninstall" != "no" ]; then
    run_uninstall_with_fake_launchctl "$test_home" 'n' "$TMP_ROOT/uninstall-$name.log"
    [ -L "$link_path" ]
    [ "$before" = "$(file_snapshot "$sentinel")" ]
    if [ "$also_uninstall" = "yes" ]; then
      rg -q '符号链接|不会访问链接目标' "$TMP_ROOT/uninstall-$name.log"
    fi
  fi
}

# 根目录、runtime 以及受管状态目录中的符号链接都必须 fail-closed。
run_directory_symlink_case context-root '.context-agent' yes
run_directory_symlink_case runtime '.context-agent/runtime' yes
run_directory_symlink_case usage '.context-agent/usage' no
run_directory_symlink_case logs '.context-agent/logs' no
run_directory_symlink_case agent-root '.context-agent/agent-v1' no
run_directory_symlink_case memory-state '.context-agent/agent-v1/memories' no
run_directory_symlink_case cognitive-root '.context-agent/cognitive-secretary-v1' preserve

# 日志文件符号链接不得被打开或截断；初始化失败时已加载的旧 owned job 必须卸载并禁用。
LOG_HOME="$TMP_ROOT/home-log-file"
LOG_VAULT="$LOG_HOME/AISecretary"
LOG_EXTERNAL="$TMP_ROOT/external-log-file.txt"
LOG_LINK="$LOG_VAULT/.context-agent/logs/context-workers.stdout.log"
LOG_PLIST="$LOG_HOME/Library/LaunchAgents/com.memento.context-agent.plist"
LOG_REMEMBER_PLIST="$LOG_HOME/Library/LaunchAgents/com.memento.remember-agent-v1.plist"
LOG_SCHEDULE_PLIST="$LOG_HOME/Library/LaunchAgents/com.memento.remember-agent-v1-schedule.plist"
prepare_home "$LOG_HOME"
mkdir -p "$LOG_VAULT/.context-agent/logs"
printf '%s\n' 'EXTERNAL_LOG_SENTINEL' > "$LOG_EXTERNAL"
LOG_BEFORE=$(file_snapshot "$LOG_EXTERNAL")
ln -s "$LOG_EXTERNAL" "$LOG_LINK"
write_context_plist "$LOG_PLIST" yes
write_remember_agent_plist "$LOG_REMEMBER_PLIST" yes
write_remember_agent_schedule_plist "$LOG_SCHEDULE_PLIST" yes
: > "$LAUNCHCTL_LOG"
run_install_with_fake_launchctl "$LOG_HOME" 'yn' "$TMP_ROOT/install-log-file.log"
[ -L "$LOG_LINK" ]
[ "$LOG_BEFORE" = "$(file_snapshot "$LOG_EXTERNAL")" ]
[ ! -e "$LOG_PLIST" ]
[ ! -e "$LOG_REMEMBER_PLIST" ]
[ ! -e "$LOG_SCHEDULE_PLIST" ]
rg -q 'bootout' "$LAUNCHCTL_LOG"
rg -q 'com.memento.remember-agent-v1.plist' "$LAUNCHCTL_LOG"
rg -q 'com.memento.remember-agent-v1-schedule.plist' "$LAUNCHCTL_LOG"
rg -q '已停止并移除旧 Context Worker' "$TMP_ROOT/install-log-file.log"

# 同样的降级不得修改无 Memento marker 的同名 foreign plist。
write_context_plist "$LOG_PLIST" no
write_remember_agent_plist "$LOG_REMEMBER_PLIST" no
write_remember_agent_schedule_plist "$LOG_SCHEDULE_PLIST" no
FOREIGN_PLIST_BEFORE=$(file_snapshot "$LOG_PLIST")
FOREIGN_REMEMBER_PLIST_BEFORE=$(file_snapshot "$LOG_REMEMBER_PLIST")
FOREIGN_SCHEDULE_PLIST_BEFORE=$(file_snapshot "$LOG_SCHEDULE_PLIST")
: > "$LAUNCHCTL_LOG"
run_install_with_fake_launchctl "$LOG_HOME" 'yn' "$TMP_ROOT/install-log-foreign.log"
[ "$FOREIGN_PLIST_BEFORE" = "$(file_snapshot "$LOG_PLIST")" ]
[ "$FOREIGN_REMEMBER_PLIST_BEFORE" = "$(file_snapshot "$LOG_REMEMBER_PLIST")" ]
[ "$FOREIGN_SCHEDULE_PLIST_BEFORE" = "$(file_snapshot "$LOG_SCHEDULE_PLIST")" ]
[ ! -s "$LAUNCHCTL_LOG" ]
[ "$LOG_BEFORE" = "$(file_snapshot "$LOG_EXTERNAL")" ]

# 当前安装版保留可导入运行时，但不创建/载入任何 Agent job、
# gate 或 schedule；日级 runner 也不进入安装目录。
LOAD_HOME="$TMP_ROOT/home-load"
LOAD_PLIST="$LOAD_HOME/Library/LaunchAgents/com.memento.context-agent.plist"
LOAD_REMEMBER_PLIST="$LOAD_HOME/Library/LaunchAgents/com.memento.remember-agent-v1.plist"
LOAD_SCHEDULE_PLIST="$LOAD_HOME/Library/LaunchAgents/com.memento.remember-agent-v1-schedule.plist"
LOAD_VAULT="$LOAD_HOME/AISecretary"
prepare_home "$LOAD_HOME"
: > "$LAUNCHCTL_LOG"
run_install_with_fake_launchctl "$LOAD_HOME" 'n' "$TMP_ROOT/install-load.log"
[ ! -e "$LOAD_PLIST" ]
[ ! -e "$LOAD_REMEMBER_PLIST" ]
[ ! -e "$LOAD_SCHEDULE_PLIST" ]
[ ! -s "$LAUNCHCTL_LOG" ]
[ ! -e "$LOAD_VAULT/.context-agent/agent-v1/enabled" ]
[ ! -e "$LOAD_VAULT/.context-agent/agent-v1/schedule.json" ]
LOAD_SELF_RUNNER="$LOAD_VAULT/.context-agent/runtime/run_self_reflection_once.sh"
LOAD_RUNNER="$LOAD_VAULT/.context-agent/runtime/run_remember_agent_v1_once.sh"
LOAD_RECORD_RUNNER="$LOAD_VAULT/.context-agent/runtime/run_cognitive_record_once.sh"
LOAD_SCHEDULE_RUNNER="$LOAD_VAULT/.context-agent/runtime/run_remember_agent_schedule_once.sh"
rg -q 'self-reflection-worker' "$LOAD_SELF_RUNNER"
rg -q 'agent-worker' "$LOAD_RUNNER"
rg -q 'cognitive-action-worker' "$LOAD_RUNNER"
! rg -q 'daily-manual-worker|daily-run|daily-schedule' "$LOAD_RUNNER"
rg -q 'record-ingest --vault' "$LOAD_RECORD_RUNNER"
rg -q 'record-worker --once --vault' "$LOAD_RECORD_RUNNER"
[ ! -e "$LOAD_SCHEDULE_RUNNER" ]
DEFAULT_RUNNER_OUTPUT=$(env -u DEEPSEEK_API_KEY bash "$LOAD_RUNNER" "$LOAD_VAULT")
[ -z "$DEFAULT_RUNNER_OUTPUT" ]
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$LOAD_VAULT/.context-agent/runtime" python3 -c \
  'import context_agent, cognitive_daily_review_v1, cognitive_day_orchestrator_v1'

# 卸载会在移除 Worker 后安全移除 enabled 叶子项，但保留所有其他 Agent 状态。
VALID_GATE_HOME="$TMP_ROOT/home-valid-gate-uninstall"
VALID_GATE_ROOT="$VALID_GATE_HOME/AISecretary/.context-agent/agent-v1"
VALID_COGNITIVE_ROOT="$VALID_GATE_HOME/AISecretary/.context-agent/cognitive-secretary-v1"
VALID_SCHEDULE_PLIST="$VALID_GATE_HOME/Library/LaunchAgents/com.memento.remember-agent-v1-schedule.plist"
prepare_home "$VALID_GATE_HOME"
mkdir -p \
  "$VALID_GATE_ROOT/memories" \
  "$VALID_GATE_ROOT/user-actions" \
  "$VALID_COGNITIVE_ROOT/receipts" \
  "$VALID_COGNITIVE_ROOT/daily-bundles/committed" \
  "$VALID_COGNITIVE_ROOT/projections" \
  "$VALID_GATE_HOME/AISecretary/.context-agent/logs"
printf '%s\n' 'MEMORY_STATE_SENTINEL' > "$VALID_GATE_ROOT/memories/state.json"
printf '%s\n' 'ACTION_STATE_SENTINEL' > "$VALID_GATE_ROOT/user-actions/state.json"
printf '%s\n' 'RECEIPT_STATE_SENTINEL' > "$VALID_COGNITIVE_ROOT/receipts/state.json"
printf '%s\n' 'BUNDLE_STATE_SENTINEL' > \
  "$VALID_COGNITIVE_ROOT/daily-bundles/committed/state.json"
printf '%s\n' 'PROJECTION_STATE_SENTINEL' > \
  "$VALID_COGNITIVE_ROOT/projections/home_projection.json"
printf '%s\n' 'COGNITIVE_LOG_SENTINEL' > \
  "$VALID_GATE_HOME/AISecretary/.context-agent/logs/cognitive.log"
printf 'enabled-v1\n' > "$VALID_GATE_ROOT/enabled"
chmod 600 "$VALID_GATE_ROOT/enabled"
cat > "$VALID_GATE_ROOT/schedule.json" <<'SCHEDULE_CONFIG'
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
write_remember_agent_schedule_plist "$VALID_SCHEDULE_PLIST" yes
: > "$LAUNCHCTL_LOG"
if ! run_uninstall_with_fake_launchctl \
  "$VALID_GATE_HOME" 'n' "$TMP_ROOT/uninstall-valid-gate.log"; then
  sed -n '1,240p' "$TMP_ROOT/uninstall-valid-gate.log" >&2
  exit 1
fi
[ ! -e "$VALID_GATE_ROOT/enabled" ]
[ ! -e "$VALID_GATE_ROOT/schedule.json" ]
[ ! -e "$VALID_SCHEDULE_PLIST" ]
[ "$(cat "$VALID_GATE_ROOT/memories/state.json")" = 'MEMORY_STATE_SENTINEL' ]
[ "$(cat "$VALID_GATE_ROOT/user-actions/state.json")" = 'ACTION_STATE_SENTINEL' ]
[ "$(cat "$VALID_COGNITIVE_ROOT/receipts/state.json")" = 'RECEIPT_STATE_SENTINEL' ]
[ "$(cat "$VALID_COGNITIVE_ROOT/daily-bundles/committed/state.json")" = \
  'BUNDLE_STATE_SENTINEL' ]
[ "$(cat "$VALID_COGNITIVE_ROOT/projections/home_projection.json")" = \
  'PROJECTION_STATE_SENTINEL' ]
[ "$(cat "$VALID_GATE_HOME/AISecretary/.context-agent/logs/cognitive.log")" = \
  'COGNITIVE_LOG_SENTINEL' ]
rg -q '已安全关闭 Agent V1 enabled gate' "$TMP_ROOT/uninstall-valid-gate.log"
rg -q '已安全关闭 Re:member 每日自动整理配置' "$TMP_ROOT/uninstall-valid-gate.log"
rg -q 'bootout.*com.memento.remember-agent-v1-schedule.plist' "$LAUNCHCTL_LOG"

# 卸载是明确关闭功能，因此即使内容偏移也要移除 enabled 叶子项。
INVALID_GATE_HOME="$TMP_ROOT/home-invalid-gate-uninstall"
INVALID_GATE_ROOT="$INVALID_GATE_HOME/AISecretary/.context-agent/agent-v1"
prepare_home "$INVALID_GATE_HOME"
mkdir -p "$INVALID_GATE_ROOT"
printf 'enabled-v1\nextra' > "$INVALID_GATE_ROOT/enabled"
chmod 600 "$INVALID_GATE_ROOT/enabled"
printf '%s\n' '{"invalid":true}' > "$INVALID_GATE_ROOT/schedule.json"
run_uninstall_with_fake_launchctl \
  "$INVALID_GATE_HOME" 'n' "$TMP_ROOT/uninstall-invalid-gate.log"
[ ! -e "$INVALID_GATE_ROOT/enabled" ]
[ ! -e "$INVALID_GATE_ROOT/schedule.json" ]
rg -q '已安全关闭 Agent V1 enabled gate' "$TMP_ROOT/uninstall-invalid-gate.log"

# 符号链接叶子会被移除，但绝不能跟随或修改外部目标。
SYMLINK_GATE_HOME="$TMP_ROOT/home-symlink-gate-uninstall"
SYMLINK_GATE_ROOT="$SYMLINK_GATE_HOME/AISecretary/.context-agent/agent-v1"
SYMLINK_GATE_TARGET="$TMP_ROOT/uninstall-symlink-gate-target"
SYMLINK_SCHEDULE_TARGET="$TMP_ROOT/uninstall-symlink-schedule-target"
prepare_home "$SYMLINK_GATE_HOME"
mkdir -p "$SYMLINK_GATE_ROOT"
printf 'enabled-v1\n' > "$SYMLINK_GATE_TARGET"
chmod 600 "$SYMLINK_GATE_TARGET"
SYMLINK_TARGET_BEFORE=$(file_snapshot "$SYMLINK_GATE_TARGET")
ln -s "$SYMLINK_GATE_TARGET" "$SYMLINK_GATE_ROOT/enabled"
printf '%s\n' '{"enabled":true}' > "$SYMLINK_SCHEDULE_TARGET"
chmod 600 "$SYMLINK_SCHEDULE_TARGET"
SYMLINK_SCHEDULE_BEFORE=$(file_snapshot "$SYMLINK_SCHEDULE_TARGET")
ln -s "$SYMLINK_SCHEDULE_TARGET" "$SYMLINK_GATE_ROOT/schedule.json"
run_uninstall_with_fake_launchctl \
  "$SYMLINK_GATE_HOME" 'n' "$TMP_ROOT/uninstall-symlink-gate.log"
[ ! -e "$SYMLINK_GATE_ROOT/enabled" ]
[ ! -e "$SYMLINK_GATE_ROOT/schedule.json" ]
[ "$SYMLINK_TARGET_BEFORE" = "$(file_snapshot "$SYMLINK_GATE_TARGET")" ]
[ "$SYMLINK_SCHEDULE_BEFORE" = "$(file_snapshot "$SYMLINK_SCHEDULE_TARGET")" ]
rg -q '已安全关闭 Agent V1 enabled gate' "$TMP_ROOT/uninstall-symlink-gate.log"

# 硬链接 gate 的叶子名会被移除，其他名字及内容保留。
HARDLINK_GATE_HOME="$TMP_ROOT/home-hardlink-gate-uninstall"
HARDLINK_GATE_ROOT="$HARDLINK_GATE_HOME/AISecretary/.context-agent/agent-v1"
HARDLINK_GATE_TARGET="$TMP_ROOT/uninstall-hardlink-gate-target"
HARDLINK_SCHEDULE_TARGET="$TMP_ROOT/uninstall-hardlink-schedule-target"
prepare_home "$HARDLINK_GATE_HOME"
mkdir -p "$HARDLINK_GATE_ROOT"
printf 'enabled-v1\n' > "$HARDLINK_GATE_TARGET"
chmod 600 "$HARDLINK_GATE_TARGET"
ln "$HARDLINK_GATE_TARGET" "$HARDLINK_GATE_ROOT/enabled"
HARDLINK_TARGET_BEFORE=$(file_snapshot "$HARDLINK_GATE_TARGET")
printf '%s\n' '{"enabled":true}' > "$HARDLINK_SCHEDULE_TARGET"
chmod 600 "$HARDLINK_SCHEDULE_TARGET"
ln "$HARDLINK_SCHEDULE_TARGET" "$HARDLINK_GATE_ROOT/schedule.json"
HARDLINK_SCHEDULE_BEFORE=$(file_snapshot "$HARDLINK_SCHEDULE_TARGET")
run_uninstall_with_fake_launchctl \
  "$HARDLINK_GATE_HOME" 'n' "$TMP_ROOT/uninstall-hardlink-gate.log"
[ "$HARDLINK_TARGET_BEFORE" = "$(file_snapshot "$HARDLINK_GATE_TARGET")" ]
[ "$HARDLINK_SCHEDULE_BEFORE" = "$(file_snapshot "$HARDLINK_SCHEDULE_TARGET")" ]
[ ! -e "$HARDLINK_GATE_ROOT/enabled" ]
[ ! -e "$HARDLINK_GATE_ROOT/schedule.json" ]
rg -q '已安全关闭 Agent V1 enabled gate' "$TMP_ROOT/uninstall-hardlink-gate.log"

# 同名目录不能使用 unlink 移除，必须保留并报警。
DIRECTORY_GATE_HOME="$TMP_ROOT/home-directory-gate-uninstall"
DIRECTORY_GATE_ROOT="$DIRECTORY_GATE_HOME/AISecretary/.context-agent/agent-v1"
prepare_home "$DIRECTORY_GATE_HOME"
mkdir -p "$DIRECTORY_GATE_ROOT/enabled"
mkdir -p "$DIRECTORY_GATE_ROOT/schedule.json"
run_uninstall_with_fake_launchctl \
  "$DIRECTORY_GATE_HOME" 'n' "$TMP_ROOT/uninstall-directory-gate.log"
[ -d "$DIRECTORY_GATE_ROOT/enabled" ]
[ -d "$DIRECTORY_GATE_ROOT/schedule.json" ]
rg -q '叶子是目录' "$TMP_ROOT/uninstall-directory-gate.log"

echo '✓ context installer security: no installed jobs, symlink-safe state, and data-preserving shutdown'
