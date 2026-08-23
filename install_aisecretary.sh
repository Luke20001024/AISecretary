#!/bin/bash
# ============================================================
# Memento 安装脚本
# 文件名沿用旧产品名 AISecretary,内部路径同
# ============================================================
# 安装内容:
#   - Obsidian Vault ~/AISecretary/ 及其子结构
#   - 核心脚本到 ~/AISecretary/.scripts/ (含统一存储边界、编码兜底、TAG/NOTE 支持)
#   - Context Agent 运行时到 ~/AISecretary/.context-agent/runtime/ (保留未来开发的可导入模块)
#   - 当前版不安装、不唤醒 Context/Re:member/Cognitive Worker 或定时 job
#   - 旧版的每日拍照、天气与独立 Daily Review 在升级时停用；既有数据保留
#   - 5 个 macOS 服务 (Quick Actions / Services):
#       1. 存入 AI 秘书           (选中文字 → 直接存入)
#       2. 存入 AI 秘书 (选标签)   (选中文字 → 选标签 → 存入)
#       3. 存入 AI 秘书 (加备注)   (选中文字 → 输入备注 → 存入)
#       4. 存入 AI 秘书 (截图)     (调系统截图 → OCR → 存入)
#       5. 存入 AI 秘书 (语音)     (本地录音 → Apple 转写 → 存入)
# 不再安装截图监听 LaunchAgent (由 "截图" 服务替代)
# ============================================================

set -e
set -o pipefail
umask 077

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║       Memento 安装程序                 ║${NC}"
echo -e "${BLUE}║       记录 · 回看 · 长期理解       ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""

SECRETARY_DIR="$HOME/AISecretary"
SCRIPT_DIR="$SECRETARY_DIR/.scripts"
CONTEXT_ROOT="$SECRETARY_DIR/.context-agent"
CONTEXT_RUNTIME_DEST="$CONTEXT_ROOT/runtime"
SERVICES_DIR="$HOME/Library/Services"
INSTALLER_DIR=$(cd "$(dirname "$0")" && pwd)
INSTALL_LOCK="$HOME/.memento-install.lock"
INSTALL_LOCK_TOKEN="$$-${RANDOM}-$(date +%s)"
INSTALL_BACKGROUND_AUTOMATION=0
SCRIPT_STAGE=""
SELECTION_COPY_BUILD_ROOT=""
VOICE_BUILD_ROOT=""
NEWTAB_STAGE=""
CONTEXT_STAGE=""
CONTEXT_WORKER_PLIST_STAGE=""
REMEMBER_AGENT_PLIST_STAGE=""
OCR_STAGE=""

cleanup_install_staging() {
  local path
  for path in \
    "${SCRIPT_STAGE:-}" \
    "${SELECTION_COPY_BUILD_ROOT:-}" \
    "${VOICE_BUILD_ROOT:-}" \
    "${NEWTAB_STAGE:-}" \
    "${CONTEXT_STAGE:-}"; do
    [ -z "$path" ] || rm -rf "$path" 2>/dev/null || true
  done
  for path in "${OCR_STAGE:-}"; do
    [ -z "$path" ] || rm -f "$path" 2>/dev/null || true
  done
  [ -z "${CONTEXT_WORKER_PLIST_STAGE:-}" ] \
    || rm -f "$CONTEXT_WORKER_PLIST_STAGE" 2>/dev/null \
    || true
  [ -z "${REMEMBER_AGENT_PLIST_STAGE:-}" ] \
    || rm -f "$REMEMBER_AGENT_PLIST_STAGE" 2>/dev/null \
    || true
}

release_install_lock() {
  local current_token
  local quarantine
  cleanup_install_staging
  [ -d "$INSTALL_LOCK" ] || return 0
  current_token=$(cat "$INSTALL_LOCK/token" 2>/dev/null || true)
  [ "$current_token" = "$INSTALL_LOCK_TOKEN" ] || return 0
  quarantine="$INSTALL_LOCK.released.$$.$RANDOM"
  mv "$INSTALL_LOCK" "$quarantine" 2>/dev/null || return 0
  rm -rf "$quarantine" 2>/dev/null || true
}

acquire_install_lock() {
  local owner_pid
  local lock_mtime
  local now
  local quarantine

  for _ in $(seq 1 60); do
    if mkdir "$INSTALL_LOCK" 2>/dev/null; then
      printf '%s\n' "$INSTALL_LOCK_TOKEN" > "$INSTALL_LOCK/token" || {
        rmdir "$INSTALL_LOCK" 2>/dev/null || true
        exit 1
      }
      trap release_install_lock EXIT
      trap 'exit 130' INT
      trap 'exit 143' TERM
      printf '%s\n' "$$" > "$INSTALL_LOCK/pid" || exit 1
      return 0
    fi

    owner_pid=$(cat "$INSTALL_LOCK/pid" 2>/dev/null || true)
    if [ -n "$owner_pid" ] && kill -0 "$owner_pid" 2>/dev/null; then
      echo -e "${RED}另一个 Memento 安装或卸载进程正在运行 (PID $owner_pid)。${NC}" >&2
      exit 1
    fi

    lock_mtime=$(stat -f %m "$INSTALL_LOCK" 2>/dev/null || echo 0)
    now=$(date +%s)
    if { [ -n "$owner_pid" ] || { [ "$lock_mtime" -gt 0 ] && [ $((now - lock_mtime)) -gt 30 ]; }; }; then
      quarantine="$INSTALL_LOCK.abandoned.$$.$RANDOM"
      if mv "$INSTALL_LOCK" "$quarantine" 2>/dev/null; then
        rm -rf "$quarantine"
        continue
      fi
    fi
    sleep 0.05
  done
  echo -e "${RED}无法获取 Memento 安装锁，请稍后重试。${NC}" >&2
  exit 1
}

atomic_replace_directory() {
  local staged="$1"
  local destination="$2"
  local backup="${destination}.memento-backup.$$.$RANDOM"
  local had_destination=0
  local interrupted=0
  local status=1

  [ -d "$staged" ] || return 1
  # 延迟处理中断直到替换完成或回滚，避免信号恰好落在两次 rename 之间。
  trap 'interrupted=130' INT
  trap 'interrupted=143' TERM
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    if ! mv "$destination" "$backup"; then
      trap 'exit 130' INT
      trap 'exit 143' TERM
      [ "$interrupted" = "0" ] || exit "$interrupted"
      return 1
    fi
    had_destination=1
  fi

  if mv "$staged" "$destination"; then
    [ "$had_destination" = "0" ] || rm -rf "$backup"
    status=0
  else
    [ "$had_destination" = "0" ] || mv "$backup" "$destination" 2>/dev/null || true
  fi

  trap 'exit 130' INT
  trap 'exit 143' TERM
  [ "$interrupted" = "0" ] || exit "$interrupted"
  return "$status"
}

tighten_vault_permissions() {
  [ -d "$SECRETARY_DIR" ] || return 0
  # POSIX mode 不会覆盖 macOS 扩展 ACL。Vault 的隐私合同是仅当前用户可访问，
  # 因此先移除 Vault 树上的额外 ACL；-R 默认不跟随树内符号链接。
  chmod -RN "$SECRETARY_DIR"
  # 只收紧 Memento 自有 Vault，且不跟随其中的符号链接。
  find "$SECRETARY_DIR" -type d -exec chmod go-rwx {} +
  find "$SECRETARY_DIR" -type f -exec chmod go-rwx {} +
}

# 组合源码 hash 只取文件内容摘要，不把安装器所在绝对路径写进结果。
# 否则同一份源码从另一个目录运行时会误判变化并重建 App，打断 TCC 权限连续性。
memento_content_set_hash() {
  [ "$#" -gt 0 ] || return 1
  local file
  for file in "$@"; do
    [ -f "$file" ] || return 1
    shasum -a 256 "$file" | awk '{print $1}'
  done | shasum -a 256 | awk '{print $1}'
}

memento_legacy_launchagent_owned() {
  local plist="$1"
  local label
  [ -f "$plist" ] || return 1
  label=$(plutil -extract Label raw -o - "$plist" 2>/dev/null || true)
  [ "$label" = 'com.aisecretary.screenshot' ] || return 1
  # 旧监听器的可执行文件或参数必须同时指向本产品目录；同路径的陌生 plist 不删。
  plutil -convert xml1 -o - "$plist" 2>/dev/null \
    | grep -Eq '<string>[^<]*/AISecretary/'
}

memento_context_worker_owned() {
  local plist="$1"
  local label
  local marker
  [ ! -L "$plist" ] || return 1
  [ -f "$plist" ] || return 1
  [ "$(stat -f %u "$plist" 2>/dev/null || echo -1)" = "$(id -u)" ] || return 1
  label=$(plutil -extract Label raw -o - "$plist" 2>/dev/null || true)
  marker=$(plutil -extract MementoManaged raw -o - "$plist" 2>/dev/null || true)
  [ "$label" = 'com.memento.context-agent' ] || return 1
  [ "$marker" = 'com.memento.context-agent.v1' ] || return 1
  plutil -convert xml1 -o - "$plist" 2>/dev/null \
    | grep -Eq 'self-reflection-worker|run_(context_workers|self_reflection)_once\.sh'
}

memento_remember_agent_worker_owned() {
  local plist="$1"
  local label
  local marker
  [ ! -L "$plist" ] || return 1
  [ -f "$plist" ] || return 1
  [ "$(stat -f %u "$plist" 2>/dev/null || echo -1)" = "$(id -u)" ] || return 1
  label=$(plutil -extract Label raw -o - "$plist" 2>/dev/null || true)
  marker=$(plutil -extract MementoManaged raw -o - "$plist" 2>/dev/null || true)
  [ "$label" = 'com.memento.remember-agent-v1' ] || return 1
  [ "$marker" = 'com.memento.remember-agent-v1.v1' ] || return 1
  plutil -convert xml1 -o - "$plist" 2>/dev/null \
    | grep -Eq 'run_remember_agent_v1_once\.sh'
}

memento_remember_agent_schedule_owned() {
  local plist="$1"
  local label
  local marker
  [ ! -L "$plist" ] || return 1
  [ -f "$plist" ] || return 1
  [ "$(stat -f %u "$plist" 2>/dev/null || echo -1)" = "$(id -u)" ] || return 1
  label=$(plutil -extract Label raw -o - "$plist" 2>/dev/null || true)
  marker=$(plutil -extract MementoManaged raw -o - "$plist" 2>/dev/null || true)
  [ "$label" = 'com.memento.remember-agent-v1-schedule' ] || return 1
  [ "$marker" = 'com.memento.remember-agent-v1-schedule.v1' ] || return 1
  plutil -convert xml1 -o - "$plist" 2>/dev/null \
    | grep -Eq 'run_remember_agent_schedule_once\.sh'
}

memento_plain_owned_directory() {
  local path="$1"
  [ ! -L "$path" ] || return 1
  [ -d "$path" ] || return 1
  [ "$(stat -f %u "$path" 2>/dev/null || echo -1)" = "$(id -u)" ]
}

memento_context_ensure_directory() {
  local path="$1"
  if [ -L "$path" ] || { [ -e "$path" ] && [ ! -d "$path" ]; }; then
    echo -e "${YELLOW}  ⚠ Context Agent 路径不是安全的普通目录：$path${NC}" >&2
    return 1
  fi
  if [ ! -e "$path" ]; then
    mkdir "$path" || return 1
  fi
  if ! memento_plain_owned_directory "$path"; then
    echo -e "${YELLOW}  ⚠ Context Agent 路径不属于当前用户：$path${NC}" >&2
    return 1
  fi
}

memento_context_optional_directory_safe() {
  local path="$1"
  if [ -L "$path" ] || { [ -e "$path" ] && [ ! -d "$path" ]; }; then
    echo -e "${YELLOW}  ⚠ Context Agent 路径不是安全的普通目录：$path${NC}" >&2
    return 1
  fi
  [ ! -e "$path" ] || memento_plain_owned_directory "$path"
}

memento_prepare_context_tree() {
  local path
  memento_plain_owned_directory "$HOME" || return 1
  memento_plain_owned_directory "$SECRETARY_DIR" || return 1
  for path in \
    "$CONTEXT_ROOT" \
    "$CONTEXT_ROOT/candidates" \
    "$CONTEXT_ROOT/decisions" \
    "$CONTEXT_ROOT/usage" \
    "$CONTEXT_ROOT/reflections" \
    "$CONTEXT_ROOT/self-queries" \
    "$CONTEXT_ROOT/self-queries/requests" \
    "$CONTEXT_ROOT/self-queries/responses" \
    "$CONTEXT_ROOT/self-queries/feedback" \
    "$CONTEXT_ROOT/agent-v1" \
    "$CONTEXT_ROOT/agent-v1/requests" \
    "$CONTEXT_ROOT/agent-v1/responses" \
    "$CONTEXT_ROOT/agent-v1/runs" \
    "$CONTEXT_ROOT/agent-v1/memories" \
    "$CONTEXT_ROOT/agent-v1/user-actions" \
    "$CONTEXT_ROOT/agent-v1/locks" \
    "$CONTEXT_ROOT/cognitive-secretary-v1" \
    "$CONTEXT_ROOT/cognitive-secretary-v1/user-actions" \
    "$CONTEXT_ROOT/cognitive-secretary-v1/manual-day-requests" \
    "$CONTEXT_ROOT/cognitive-secretary-v1/manual-day-results" \
    "$CONTEXT_ROOT/cognitive-secretary-v1/locks" \
    "$CONTEXT_ROOT/logs"; do
    memento_context_ensure_directory "$path" || return 1
  done
  memento_context_optional_directory_safe "$CONTEXT_RUNTIME_DEST"
}

memento_validate_context_tree() {
  local path
  memento_plain_owned_directory "$HOME" || return 1
  memento_plain_owned_directory "$SECRETARY_DIR" || return 1
  for path in \
    "$CONTEXT_ROOT" \
    "$CONTEXT_ROOT/candidates" \
    "$CONTEXT_ROOT/decisions" \
    "$CONTEXT_ROOT/usage" \
    "$CONTEXT_ROOT/reflections" \
    "$CONTEXT_ROOT/self-queries" \
    "$CONTEXT_ROOT/self-queries/requests" \
    "$CONTEXT_ROOT/self-queries/responses" \
    "$CONTEXT_ROOT/self-queries/feedback" \
    "$CONTEXT_ROOT/agent-v1" \
    "$CONTEXT_ROOT/agent-v1/requests" \
    "$CONTEXT_ROOT/agent-v1/responses" \
    "$CONTEXT_ROOT/agent-v1/runs" \
    "$CONTEXT_ROOT/agent-v1/memories" \
    "$CONTEXT_ROOT/agent-v1/user-actions" \
    "$CONTEXT_ROOT/agent-v1/locks" \
    "$CONTEXT_ROOT/cognitive-secretary-v1" \
    "$CONTEXT_ROOT/cognitive-secretary-v1/user-actions" \
    "$CONTEXT_ROOT/cognitive-secretary-v1/manual-day-requests" \
    "$CONTEXT_ROOT/cognitive-secretary-v1/manual-day-results" \
    "$CONTEXT_ROOT/cognitive-secretary-v1/locks" \
    "$CONTEXT_ROOT/logs"; do
    memento_plain_owned_directory "$path" || return 1
  done
  memento_context_optional_directory_safe "$CONTEXT_RUNTIME_DEST"
}

memento_context_regular_file_safe() {
  local path="$1"
  if [ -L "$path" ] || { [ -e "$path" ] && [ ! -f "$path" ]; }; then
    return 1
  fi
  [ ! -e "$path" ] \
    || [ "$(stat -f %u "$path" 2>/dev/null || echo -1)" = "$(id -u)" ]
}

memento_context_safe_log_file() {
  local path="$1"
  "$PYTHON3_RUNTIME" - "$path" "$(id -u)" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
uid = int(sys.argv[2])
try:
    current = os.lstat(path)
except FileNotFoundError:
    current = None
if current is not None:
    if not stat.S_ISREG(current.st_mode) or current.st_uid != uid:
        raise SystemExit(1)
flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
try:
    descriptor = os.open(path, flags, 0o600)
except OSError:
    raise SystemExit(1)
try:
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode) or opened.st_uid != uid:
        raise SystemExit(1)
    os.fchmod(descriptor, 0o600)
finally:
    os.close(descriptor)
PY
}

memento_context_runner_uses_python() {
  local runner="$1"
  "$PYTHON3_RUNTIME" - "$runner" "$PYTHON3_RUNTIME" <<'PY'
import shlex
import sys
from pathlib import Path

runner = Path(sys.argv[1])
expected = "MEMENTO_PYTHON=" + shlex.quote(sys.argv[2])
try:
    lines = runner.read_text(encoding="utf-8").splitlines()
except (OSError, UnicodeError):
    raise SystemExit(1)
if lines.count(expected) != 1:
    raise SystemExit(1)
PY
}

memento_disable_owned_context_workers() {
  local plist="$HOME/Library/LaunchAgents/com.memento.context-agent.plist"
  local remember_plist="$HOME/Library/LaunchAgents/com.memento.remember-agent-v1.plist"
  local schedule_plist="$HOME/Library/LaunchAgents/com.memento.remember-agent-v1-schedule.plist"
  if memento_context_worker_owned "$plist"; then
    if [ "${MEMENTO_SKIP_CONTEXT_WORKER_LOAD:-0}" != "1" ]; then
      launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null \
        || launchctl unload "$plist" 2>/dev/null \
        || true
    fi
    rm -f "$plist"
    echo -e "${YELLOW}  ⚠ Context Agent 本轮初始化或校验失败；旧 Memento Worker 已停止并禁用${NC}"
  elif [ -e "$plist" ] || [ -L "$plist" ]; then
    echo -e "${YELLOW}  ⚠ 保留无法确认归属的同名 Context Worker: $plist${NC}"
  fi
  if memento_remember_agent_worker_owned "$remember_plist"; then
    if [ "${MEMENTO_SKIP_CONTEXT_WORKER_LOAD:-0}" != "1" ] \
      && [ "${MEMENTO_SKIP_REMEMBER_AGENT_LOAD:-0}" != "1" ]; then
      launchctl bootout "gui/$(id -u)" "$remember_plist" 2>/dev/null \
        || launchctl unload "$remember_plist" 2>/dev/null \
        || true
    fi
    rm -f "$remember_plist"
    echo -e "${YELLOW}  ⚠ Re:member Agent V1 本轮初始化或校验失败；已停止并禁用事件 Worker${NC}"
  elif [ -e "$remember_plist" ] || [ -L "$remember_plist" ]; then
    echo -e "${YELLOW}  ⚠ 保留无法确认归属的同名 Re:member Agent Worker: $remember_plist${NC}"
  fi
  if memento_remember_agent_schedule_owned "$schedule_plist"; then
    if [ "${MEMENTO_SKIP_CONTEXT_WORKER_LOAD:-0}" != "1" ] \
      && [ "${MEMENTO_SKIP_REMEMBER_AGENT_SCHEDULE_LOAD:-0}" != "1" ]; then
      launchctl bootout "gui/$(id -u)" "$schedule_plist" 2>/dev/null \
        || launchctl unload "$schedule_plist" 2>/dev/null \
        || true
    fi
    rm -f "$schedule_plist"
    echo -e "${YELLOW}  ⚠ Re:member 每日调度本轮初始化或校验失败；已停止并禁用${NC}"
  elif [ -e "$schedule_plist" ] || [ -L "$schedule_plist" ]; then
    echo -e "${YELLOW}  ⚠ 保留无法确认归属的同名 Re:member 每日调度: $schedule_plist${NC}"
  fi
}

remove_owned_legacy_launchagent() {
  local plist="$HOME/Library/LaunchAgents/com.aisecretary.screenshot.plist"
  [ -f "$plist" ] || return 0
  if ! memento_legacy_launchagent_owned "$plist"; then
    echo -e "${YELLOW}  ⚠ 保留无法确认归属的旧截图 LaunchAgent: $plist${NC}"
    return 0
  fi

  echo -e "${BLUE}  → 停止并移除旧截图后台监听器${NC}"
  if [ "${MEMENTO_SKIP_LEGACY_LAUNCHAGENT_UNLOAD:-0}" != '1' ]; then
    launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null \
      || launchctl unload "$plist" 2>/dev/null \
      || true
  fi
  rm -f "$plist"
  echo -e "${GREEN}  ✓ 旧截图后台监听器已移除${NC}"
}

memento_owned_regular_file() {
  local path="$1"
  [ ! -L "$path" ] \
    && [ -f "$path" ] \
    && [ "$(stat -f %u "$path" 2>/dev/null || echo -1)" = "$(id -u)" ] \
    && [ "$(stat -f %l "$path" 2>/dev/null || echo 0)" = "1" ]
}

memento_vault_root_safe() {
  "$PYTHON3_RUNTIME" - "$HOME" "$SECRETARY_DIR" "$(id -u)" <<'PY'
import os
import stat
import sys

home = os.path.abspath(sys.argv[1])
vault = os.path.abspath(sys.argv[2])
uid = int(sys.argv[3])
if os.path.dirname(vault) != home:
    raise SystemExit(1)

# Reject every symlink in the HOME parent chain. Ancestors may be owned by
# root or the current user; HOME and an existing Vault must belong to the user.
current = os.path.sep
for component in [part for part in home.split(os.path.sep) if part]:
    current = os.path.join(current, component)
    try:
        metadata = os.lstat(current)
    except OSError:
        raise SystemExit(1)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in (0, uid):
        raise SystemExit(1)
try:
    home_metadata = os.lstat(home)
except OSError:
    raise SystemExit(1)
if not stat.S_ISDIR(home_metadata.st_mode) or home_metadata.st_uid != uid:
    raise SystemExit(1)

if os.path.lexists(vault):
    try:
        vault_metadata = os.lstat(vault)
    except OSError:
        raise SystemExit(1)
    if not stat.S_ISDIR(vault_metadata.st_mode) or vault_metadata.st_uid != uid:
        raise SystemExit(1)
PY
}

memento_snapshot_app_owned() {
  local app="$1"
  local info="$app/Contents/Info.plist"
  local executable="$app/Contents/MacOS/MementoDailySnapshot"

  memento_plain_owned_directory "$SECRETARY_DIR/.apps" \
    && memento_plain_owned_directory "$app" \
    && memento_owned_regular_file "$info" \
    && memento_owned_regular_file "$executable" \
    && [ "$(plutil -extract CFBundleIdentifier raw -o - "$info" 2>/dev/null || true)" \
      = "com.memento.daily-snapshot" ] \
    && [ "$(plutil -extract CFBundleExecutable raw -o - "$info" 2>/dev/null || true)" \
      = "MementoDailySnapshot" ]
}

memento_review_protocol_file_owned() {
  local path="$1"
  local name=${path##*/}
  local expected_sha=""
  local actual_sha
  memento_plain_owned_directory "$SECRETARY_DIR/.review" \
    && memento_owned_regular_file "$path" \
    || return 1
  case "$name" in
    DAILY_REVIEW.md)
      expected_sha='6d6f103a03c0aff3f6fb671206648ef89e61cbc01934f372bc5dfcbe9f4aeca0'
      ;;
    README.md)
      expected_sha='7e813eae5d03c64a0758fb58201f0ab6f4678266a77991a1329e8e7f0f126f85'
      ;;
    commit_review.sh)
      expected_sha='bdfd4c43900a1513ccc4f7d9e80652de0ddeb8e74d34081af5a9f35f0b1700e8'
      ;;
    commit_review_atomic.py)
      expected_sha='d6bfaaa74f0cb462a4963cd652ae857cb077ea31d05fc285a44d621c82027202'
      ;;
    review_cycle.sh)
      expected_sha='4b9334e3264bc036f7c44c2a1893c20785177bbc6ed57306a5624b380930b877'
      ;;
    review_state.sh)
      expected_sha='4342549ca7d6d6b4bb7c689fbe5c5124e48467df7e6d2a91f9e6e57cd984a3ff'
      ;;
    review_status.sh)
      expected_sha='5b7ccf0f4e52a85e8931a8e6d2a23062f82c12c014ebe89ed4f92cdf61ed0e2f'
      ;;
    verify_review.sh)
      expected_sha='b587fd6f67edffe51da49ae9e2dee29d78ab03c08869e94aaf4c0b4cd588463e'
      ;;
    *)
      return 1
      ;;
  esac
  actual_sha=$(shasum -a 256 "$path" 2>/dev/null | awk '{print $1}')
  [ "$actual_sha" = "$expected_sha" ]
}

remove_deprecated_daily_capture_and_review() {
  local app="$SECRETARY_DIR/.apps/Memento Daily Snapshot.app"
  local quarantine="$SECRETARY_DIR/.apps/.memento-retired-snapshot.$$.$RANDOM"
  local path

  if ! memento_vault_root_safe; then
    echo -e "${YELLOW}  ⚠ Vault 根路径不安全；已保留照片与 Review 文件${NC}"
    return 0
  fi

  if [ -e "$app" ] || [ -L "$app" ]; then
    if memento_snapshot_app_owned "$app" \
      && mv "$app" "$quarantine" 2>/dev/null; then
      rm -rf "$quarantine"
      echo -e "${GREEN}  ✓ 已移除 Memento 管理的每日拍照 App${NC}"
    else
      echo -e "${YELLOW}  ⚠ 保留无法确认归属的同名拍照 App: $app${NC}"
    fi
  fi

  for path in \
    "$SECRETARY_DIR/.review/DAILY_REVIEW.md" \
    "$SECRETARY_DIR/.review/README.md" \
    "$SECRETARY_DIR/.review/commit_review.sh" \
    "$SECRETARY_DIR/.review/commit_review_atomic.py" \
    "$SECRETARY_DIR/.review/review_cycle.sh" \
    "$SECRETARY_DIR/.review/review_state.sh" \
    "$SECRETARY_DIR/.review/review_status.sh" \
    "$SECRETARY_DIR/.review/verify_review.sh"; do
    [ -e "$path" ] || [ -L "$path" ] || continue
    if memento_review_protocol_file_owned "$path"; then
      rm -f "$path"
    else
      echo -e "${YELLOW}  ⚠ 保留无法确认归属的 Review 协议文件: $path${NC}"
    fi
  done
}

remove_owned_daily_schedule() {
  local plist="$HOME/Library/LaunchAgents/com.memento.remember-agent-v1-schedule.plist"
  if memento_remember_agent_schedule_owned "$plist"; then
    if [ "${MEMENTO_SKIP_CONTEXT_WORKER_LOAD:-0}" != "1" ] \
      && [ "${MEMENTO_SKIP_REMEMBER_AGENT_SCHEDULE_LOAD:-0}" != "1" ]; then
      launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null \
        || launchctl unload "$plist" 2>/dev/null \
        || true
    fi
    rm -f "$plist"
    echo -e "${GREEN}  ✓ 已停止并移除旧每日调度${NC}"
  elif [ -e "$plist" ] || [ -L "$plist" ]; then
    echo -e "${YELLOW}  ⚠ 保留无法确认归属的同名每日调度: $plist${NC}"
  fi
}

remove_owned_context_automation_jobs() {
  local context_plist="$HOME/Library/LaunchAgents/com.memento.context-agent.plist"
  local remember_plist="$HOME/Library/LaunchAgents/com.memento.remember-agent-v1.plist"

  if memento_context_worker_owned "$context_plist"; then
    if [ "${MEMENTO_SKIP_CONTEXT_WORKER_LOAD:-0}" != "1" ]; then
      launchctl bootout "gui/$(id -u)" "$context_plist" 2>/dev/null \
        || launchctl unload "$context_plist" 2>/dev/null \
        || true
    fi
    rm -f "$context_plist"
    echo -e "${GREEN}  ✓ 已停止并移除旧 Context Worker${NC}"
  elif [ -e "$context_plist" ] || [ -L "$context_plist" ]; then
    echo -e "${YELLOW}  ⚠ 保留无法确认归属的同名 Context Worker: $context_plist${NC}"
  fi

  if memento_remember_agent_worker_owned "$remember_plist"; then
    if [ "${MEMENTO_SKIP_CONTEXT_WORKER_LOAD:-0}" != "1" ] \
      && [ "${MEMENTO_SKIP_REMEMBER_AGENT_LOAD:-0}" != "1" ]; then
      launchctl bootout "gui/$(id -u)" "$remember_plist" 2>/dev/null \
        || launchctl unload "$remember_plist" 2>/dev/null \
        || true
    fi
    rm -f "$remember_plist"
    echo -e "${GREEN}  ✓ 已停止并移除旧 Re:member Worker${NC}"
  elif [ -e "$remember_plist" ] || [ -L "$remember_plist" ]; then
    echo -e "${YELLOW}  ⚠ 保留无法确认归属的同名 Re:member Worker: $remember_plist${NC}"
  fi
}

disable_owned_agent_automation_controls() {
  local gate="$CONTEXT_ROOT/agent-v1/enabled"
  local schedule="$CONTEXT_ROOT/agent-v1/schedule.json"

  if ! memento_vault_root_safe; then
    echo -e "${YELLOW}  ⚠ Vault 根路径不安全；已保留 Agent gate 与 schedule.json${NC}"
    return 0
  fi

  if [ -e "$gate" ] || [ -L "$gate" ]; then
    if memento_validate_context_tree \
      && memento_owned_regular_file "$gate" \
      && [ "$(cat "$gate" 2>/dev/null || true)" = "enabled-v1" ]; then
      rm -f "$gate"
      echo -e "${GREEN}  ✓ 已关闭旧 Agent 启用 gate${NC}"
    else
      echo -e "${YELLOW}  ⚠ 保留无法确认归属的 Agent gate；后台 Worker 仍不会安装${NC}"
    fi
  fi

  if [ -e "$schedule" ] || [ -L "$schedule" ]; then
    if memento_validate_context_tree \
      && memento_owned_regular_file "$schedule" \
      && "$PYTHON3_RUNTIME" - "$schedule" <<'PY'
import json
import pathlib
import sys

try:
    value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError):
    raise SystemExit(1)
raise SystemExit(0 if value.get("kind") == "remember_agent_schedule" else 1)
PY
    then
      rm -f "$schedule"
      echo -e "${GREEN}  ✓ 已关闭旧每日调度配置${NC}"
    else
      echo -e "${YELLOW}  ⚠ 保留无法确认归属的 schedule.json；日级 job 仍不会安装${NC}"
    fi
  fi
}

memento_migrate_homepage_remove_review() {
  local path="$SECRETARY_DIR/Memento.md"

  [ -e "$path" ] || [ -L "$path" ] || return 0
  if ! memento_vault_root_safe || ! memento_owned_regular_file "$path"; then
    echo -e "${YELLOW}  ⚠ Memento.md 不是安全的当前用户单链接普通文件；已保留且未跟随${NC}"
    return 0
  fi

  if ! "$PYTHON3_RUNTIME" - "$SECRETARY_DIR" "$(id -u)" <<'PY'
import os
import secrets
import stat
import sys

parent = sys.argv[1]
uid = int(sys.argv[2])
name = "Memento.md"
directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
directory_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
directory = os.open(parent, directory_flags)
source = None
temporary = None
temporary_name = None
try:
    read_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    source = os.open(name, read_flags, dir_fd=directory)
    metadata = os.fstat(source)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_nlink != 1
    ):
        raise OSError("unsafe Memento.md")
    chunks = []
    while True:
        chunk = os.read(source, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    original = b"".join(chunks)
    text = original.decode("utf-8")
    migrated = text.replace(
        "搜索 `tag:#TODO` 查看待办碎片",
        "搜索 `tag:#TODO` 查看行动类记录(仅作标签,没有完成态)",
    )
    migrated = migrated.replace("\n## AI Daily Review\n\n![[Reviews.base]]\n", "")
    migrated = migrated.replace("- [[Reviews.base|AI Daily Review 索引]]\n", "")
    if migrated == text:
        raise SystemExit(0)

    temporary_name = f".Memento.md.migrate.{os.getpid()}.{secrets.token_hex(8)}"
    write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    write_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    temporary = os.open(
        temporary_name,
        write_flags,
        stat.S_IMODE(metadata.st_mode),
        dir_fd=directory,
    )
    os.fchmod(temporary, stat.S_IMODE(metadata.st_mode))
    payload = migrated.encode("utf-8")
    offset = 0
    while offset < len(payload):
        offset += os.write(temporary, payload[offset:])
    os.fsync(temporary)

    current = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_uid != uid
        or current.st_nlink != 1
        or (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino)
    ):
        raise OSError("Memento.md changed during migration")
    os.close(temporary)
    temporary = None
    os.replace(
        temporary_name,
        name,
        src_dir_fd=directory,
        dst_dir_fd=directory,
    )
    temporary_name = None
    os.fsync(directory)
finally:
    if source is not None:
        os.close(source)
    if temporary is not None:
        os.close(temporary)
    if temporary_name is not None:
        try:
            os.unlink(temporary_name, dir_fd=directory)
        except FileNotFoundError:
            pass
    os.close(directory)
PY
  then
    echo -e "${YELLOW}  ⚠ Memento.md 旧 Review 入口迁移失败；原文件已保留${NC}"
  fi
}

if ! command -v python3 >/dev/null 2>&1; then
  echo -e "${RED}缺少 python3，无法安全生成 macOS Workflow。请先安装 Xcode Command Line Tools。${NC}" >&2
  exit 1
fi
PYTHON3_COMMAND=$(command -v python3)
case "$PYTHON3_COMMAND" in
  /*) ;;
  *)
    echo -e "${RED}python3 必须解析为绝对路径，当前为: $PYTHON3_COMMAND${NC}" >&2
    exit 1
    ;;
esac
PYTHON3_RUNTIME=$(
  "$PYTHON3_COMMAND" - "$PYTHON3_COMMAND" "$(id -u)" <<'PY'
import os
import stat
import sys

candidate = sys.argv[1]
uid = int(sys.argv[2])
resolved = os.path.realpath(candidate)
if not os.path.isabs(resolved) or "\n" in resolved or "\r" in resolved:
    raise SystemExit(1)
try:
    executable = os.stat(resolved)
except OSError:
    raise SystemExit(1)
if (not stat.S_ISREG(executable.st_mode)
        or executable.st_uid not in (0, uid)
        or executable.st_mode & 0o022
        or not os.access(resolved, os.X_OK)):
    raise SystemExit(1)
parent = os.path.dirname(resolved)
while True:
    try:
        current = os.lstat(parent)
    except OSError:
        raise SystemExit(1)
    if (not stat.S_ISDIR(current.st_mode)
            or current.st_uid not in (0, uid)
            or current.st_mode & 0o022):
        raise SystemExit(1)
    if parent == os.path.dirname(parent):
        break
    parent = os.path.dirname(parent)
print(resolved)
PY
) || {
  echo -e "${RED}python3 不是当前用户可信的绝对可执行普通文件，已停止安装。${NC}" >&2
  exit 1
}
if ! command -v plutil >/dev/null 2>&1; then
  echo -e "${RED}缺少 plutil，无法校验安装产物。${NC}" >&2
  exit 1
fi

HAS_CODESIGN=0
if command -v codesign >/dev/null 2>&1; then
  HAS_CODESIGN=1
else
  echo -e "${YELLOW}⚠ 缺少 codesign，本次会保留已有采集 App，不会安装新版本。${NC}"
fi

acquire_install_lock

if [ -d "$SECRETARY_DIR" ]; then
  echo -e "${YELLOW}⚠ 发现已存在的 ~/AISecretary 文件夹${NC}"
  read -p "继续会保留你的 md 数据,但会覆盖脚本和服务。继续吗? [y/N] " -n 1 -r
  echo ""
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "已取消"
    exit 0
  fi
fi

# v1 曾安装常驻截图监听器；升级必须主动清理，否则仍会在后台弹出旧通知。
remove_owned_legacy_launchagent
remove_owned_daily_schedule
remove_owned_context_automation_jobs

if ! memento_vault_root_safe; then
  echo -e "${RED}~/AISecretary 或其 HOME 父链不是当前用户拥有的安全普通目录；已停止安装且未访问该 Vault。${NC}" >&2
  exit 1
fi

# ============================================================
# Step 1: 文件夹
# ============================================================
echo -e "${BLUE}[1/6] 创建文件夹...${NC}"
mkdir -p "$SECRETARY_DIR/assets"
mkdir -p "$SECRETARY_DIR/Context/Confirmed"
mkdir -p "$SECRETARY_DIR/.obsidian"
mkdir -p "$SCRIPT_DIR"
mkdir -p "$SECRETARY_DIR/.apps"
mkdir -p "$SERVICES_DIR"
remove_deprecated_daily_capture_and_review
CONTEXT_PATHS_SAFE=0
if memento_prepare_context_tree; then
  CONTEXT_PATHS_SAFE=1
  disable_owned_agent_automation_controls
else
  echo -e "${YELLOW}  ⚠ Context Agent 已 fail-closed：本轮不会创建运行时、日志或 Worker${NC}"
fi
tighten_vault_permissions

# ============================================================
# Step 2: 配置 Obsidian Vault + 写 README
# ============================================================
echo -e "${BLUE}[2/6] 配置 Obsidian Vault 和 README...${NC}"

OBSIDIAN_SRC="$INSTALLER_DIR/obsidian-vault"
if [ -d "$OBSIDIAN_SRC" ]; then
  for CONFIG_FILE in app.json core-plugins.json daily-notes.json; do
    if [ ! -f "$SECRETARY_DIR/.obsidian/$CONFIG_FILE" ]; then
      cp "$OBSIDIAN_SRC/.obsidian/$CONFIG_FILE" "$SECRETARY_DIR/.obsidian/$CONFIG_FILE"
    fi
  done

  for VAULT_FILE in Memento.md Memento.base; do
    if [ ! -e "$SECRETARY_DIR/$VAULT_FILE" ] \
      && [ ! -L "$SECRETARY_DIR/$VAULT_FILE" ]; then
      cp "$OBSIDIAN_SRC/$VAULT_FILE" "$SECRETARY_DIR/$VAULT_FILE"
    elif [ -L "$SECRETARY_DIR/$VAULT_FILE" ]; then
      echo -e "${YELLOW}  ⚠ 保留且未跟随同名 symlink: $SECRETARY_DIR/$VAULT_FILE${NC}"
    fi
  done

  # 只迁移旧模板中的精确文案，使用安全单链接文件和同目录原子替换。
  memento_migrate_homepage_remove_review
  echo -e "${GREEN}  ✓ Obsidian Vault 配置已就绪${NC}"
else
  echo -e "${YELLOW}  ⚠ 安装包内未找到 obsidian-vault/,仅保留 Markdown 写入${NC}"
fi

if [ ! -e "$SECRETARY_DIR/README.md" ]; then
cat > "$SECRETARY_DIR/README.md" << 'README_EOF'
# 我的碎片记录库

这是 Memento 的 Obsidian Vault,也是一个"承接器"——我在各种 App 里写下的零散想法,会按天整理在这里。所有文件统一 UTF-8 编码,Obsidian 无需运行也能继续记录。

## 文件结构

- 每天一个 `.md` 文件,文件名: `YYYY-MM-DD.md`
- 每日文件的 properties 包含 `date` 和 `type: memento-daily`
- 每条记录用 `---` 分隔
- 图片/截图统一放在 `assets/`,文件名: `YYYY-MM-DD-HHMMSS.png`
- 原始录音放在 `assets/`,文件名包含 `-voice.m4a`
- `Memento.md` 是 Vault 首页,`Memento.base` 是每日记录索引

## 数据边界

- 原始 Markdown、截图、录音、Apple 转写和 Chrome Dashboard 都保存在本机
- 基础记录不会调用每日拍照、位置、天气或独立 Daily Review

## 条目格式

每条记录的 heading 把所有元信息塑在一行:

`## HH:MM · 周X [· 来源App] [· #标签]`

举例:

- `## 11:30 · 周三` — 直接存入,无来源无标签
- `## 11:30 · 周三 · WeChat` — 从微信存入
- `## 15:57 · 周日 · Feishu · #灵感` — 飞书来源,标记为灵感
- `## 11:30 · 周三 · 截图·OCR` — 截图条目,OCR 文字作为正文
- `## 11:30 · 周三 · 截图` — 纯截图,正文是图片引用
- `## 11:30 · 周三 · 语音` — Apple 本地转写 + 原始录音

heading 下方是正文。可选的备注用 blockquote:

`> 备注: ...`

截图条目正文之后,会附原图引用:

`> ![原截图](./assets/2026-05-13-150000.png)`

## 标签体系

只用 3 个固定标签:

- `#TODO` — 行动类记录;只作标签,不表示必须完成
- `#下次再读` — 暂存,稍后再看
- `#灵感` — 想法/创意

## 给 AI 的说明

阅读这个文件夹帮我处理内容时:

1. **跨日期检索**: 按文件名 (YYYY-MM-DD) 定位
2. **每条记录独立**: 不要假设上下文连续——它们是不同时刻的不同想法
3. **元信息在 heading**: 按 ` · ` 分割 `## HH:MM · 周X · 来源 · #标签`,即可拿到全部 metadata
4. **标签筛选**: 标签只用于检索语境,不代表完成状态或优先级
5. **截图条目**: heading 含 `截图·OCR` 的,正文就是图中文字
6. **语音条目**: 转写用于快速理解,原始录音是事实源;语音模块不会主动截屏
7. **总结请求**: "今天" → 最新日期的文件;"本周" → 过去 7 天

## 我的常见诉求

- 把今天的碎片归类成几个主题
- 找出最近一周反复出现的关键词
- 把某天的想法整理成一篇文章草稿
- 找出最近反复出现的主题和行动线索
- 列出所有 `#下次再读` 的内容,准备开始读
README_EOF
  chmod 600 "$SECRETARY_DIR/README.md"
else
  echo -e "${BLUE}  → 保留已有 README.md（不覆盖用户编辑）${NC}"
fi

# ============================================================
# Step 3: 核心脚本
# ============================================================
echo -e "${BLUE}[3/6] 创建核心脚本...${NC}"

# 全套脚本先在 Vault 内同文件系统 staging 目录生成并校验，再整体替换。
# 即使安装被中断，上一版 .scripts 仍保持完整可用。
SCRIPT_DEST="$SCRIPT_DIR"
SCRIPT_STAGE=$(mktemp -d "$SECRETARY_DIR/.scripts-stage.XXXXXX")
SCRIPT_DIR="$SCRIPT_STAGE"

cat > "$SCRIPT_DIR/memento_env.sh" << 'BASH_EOF'
#!/bin/bash
# Memento 的统一记录层。Obsidian 直接监听这些 Markdown 文件的外部变化。

umask 077

MEMENTO_VAULT="${MEMENTO_VAULT:-$HOME/AISecretary}"
MEMENTO_ASSETS_DIR="$MEMENTO_VAULT/assets"
MEMENTO_DAILY_APPEND_COMMITTED=0
export MEMENTO_VAULT MEMENTO_ASSETS_DIR

memento_validate_date() {
  [[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]
}

memento_current_pid() {
  /bin/sh -c 'printf "%s" "$PPID"'
}

# 获取按日写锁。锁由 PID + 随机 token 标识；失效接管先原子隔离旧目录，
# 因而旧 owner 即使稍后恢复，也无法删除新 owner 的锁。
memento_acquire_daily_lock() {
  local date="$1"
  local lock_root="$MEMENTO_VAULT/.state/write-locks"
  local lock="$lock_root/${date}.lock"
  local token="$(memento_current_pid)-${RANDOM}-$(date +%s)"
  local observed_token
  local owner_pid
  local lock_mtime
  local now
  local quarantine

  memento_validate_date "$date" || return 2
  mkdir -p "$lock_root" || return 1
  chmod 700 "$MEMENTO_VAULT/.state" "$lock_root" 2>/dev/null || true

  for _ in $(seq 1 200); do
    if mkdir "$lock" 2>/dev/null; then
      printf '%s\n' "$token" > "$lock/token" || {
        rmdir "$lock" 2>/dev/null || true
        return 1
      }
      memento_current_pid > "$lock/pid" || {
        rm -f "$lock/token"
        rmdir "$lock" 2>/dev/null || true
        return 1
      }
      MEMENTO_DAILY_LOCK_PATH="$lock"
      MEMENTO_DAILY_LOCK_TOKEN="$token"
      return 0
    fi

    observed_token=$(cat "$lock/token" 2>/dev/null || printf '%s' '__missing__')
    owner_pid=$(cat "$lock/pid" 2>/dev/null || true)
    lock_mtime=$(stat -f %m "$lock" 2>/dev/null || echo 0)
    now=$(date +%s)

    # 只有 owner 已不存在且锁超过 30 秒，才允许接管。先重读 token，
    # 再原子移动旧目录；并发接管者中只有一个能成功。
    if { [ -z "$owner_pid" ] || ! kill -0 "$owner_pid" 2>/dev/null; } \
      && [ "$lock_mtime" -gt 0 ] \
      && [ $((now - lock_mtime)) -gt 30 ] \
      && [ "$(cat "$lock/token" 2>/dev/null || printf '%s' '__missing__')" = "$observed_token" ]; then
      quarantine="$lock_root/.abandoned.${date}.$$.$RANDOM"
      if mv "$lock" "$quarantine" 2>/dev/null; then
        rm -rf "$quarantine"
        continue
      fi
    fi
    sleep 0.05
  done
  return 1
}

memento_release_daily_lock() {
  local lock="$1"
  local token="$2"
  local current_token
  local lock_root
  local quarantine

  [ -d "$lock" ] || return 0
  current_token=$(cat "$lock/token" 2>/dev/null || true)
  [ -n "$token" ] && [ "$current_token" = "$token" ] || return 2
  lock_root=$(dirname "$lock")
  quarantine="$lock_root/.released.$(basename "$lock").$$.$RANDOM"
  # 先原子移走固定锁路径，再 best-effort 清理；其他 writer 可立即继续，
  # 且旧 owner 永远不会按路径误删后来者的新锁。
  mv "$lock" "$quarantine" 2>/dev/null || return 1
  rm -rf "$quarantine" 2>/dev/null || true
  return 0
}

memento_ensure_daily_note() {
  local file="$1"
  local date="$2"
  local tmp

  memento_validate_date "$date" || return 2
  [ -L "$file" ] && return 3
  mkdir -p "$MEMENTO_VAULT" "$MEMENTO_ASSETS_DIR" || return 1
  chmod 700 "$MEMENTO_VAULT" "$MEMENTO_ASSETS_DIR" 2>/dev/null || true
  if [ -e "$file" ]; then
    [ -f "$file" ] || return 3
    return 0
  fi

  tmp=$(mktemp "${file}.new.XXXXXX") || return 1
  if ! printf '%s\n' '---' "date: $date" 'type: memento-daily' '---' > "$tmp"; then
    rm -f "$tmp"
    return 1
  fi
  chmod 600 "$tmp" || {
    rm -f "$tmp"
    return 1
  }
  if [ -e "$file" ]; then
    rm -f "$tmp"
  else
    mv "$tmp" "$file" || {
      rm -f "$tmp"
      return 1
    }
  fi
}

# 把预先生成好的完整条目安全追加到每日文件。
# 文本、图片、截图与语音入口共用同一把按日写锁。
memento_append_daily_block() {
  local file="$1"
  local date="$2"
  local block="$3"
  local lock
  local token
  local replacement=""
  local status=0
  local committed=0
  local append_interrupted=0
  local deferred_signals=0
  local previous_int
  local previous_term

  MEMENTO_DAILY_APPEND_COMMITTED=0
  [ -f "$block" ] && [ ! -L "$block" ] || return 1
  memento_acquire_daily_lock "$date" || return 1
  lock="$MEMENTO_DAILY_LOCK_PATH"
  token="$MEMENTO_DAILY_LOCK_TOKEN"
  memento_ensure_daily_note "$file" "$date" || status=$?
  if [ "$status" = "0" ]; then
    replacement=$(mktemp "${file}.append.XXXXXX") || status=1
  fi
  if [ "$status" = "0" ] && ! cp -p "$file" "$replacement"; then
    status=1
  fi
  if [ "$status" = "0" ] && ! cat "$block" >> "$replacement"; then
    status=1
  fi
  if [ "$status" = "0" ]; then
    chmod 600 "$replacement" 2>/dev/null || true
    # rename 与“资产 ownership 已转移”必须处在同一个不可中断临界区。
    # TERM/INT 会延迟到 committed flag、锁释放都完成后再交还调用方处理。
    previous_int=$(trap -p INT)
    previous_term=$(trap -p TERM)
    trap 'append_interrupted=130' INT
    trap 'append_interrupted=143' TERM
    deferred_signals=1
    if mv "$replacement" "$file"; then
      committed=1
    elif [ ! -e "$replacement" ] && [ -f "$file" ]; then
      # mv 可能在 rename 成功后才被信号打断；源路径消失代表 commit 已发生。
      committed=1
    else
      status=1
    fi
    if [ "$committed" = "1" ]; then
      MEMENTO_DAILY_APPEND_COMMITTED=1
      status=0
    fi
  fi
  [ -z "$replacement" ] || rm -f "$replacement"

  if ! memento_release_daily_lock "$lock" "$token"; then
    # Markdown rename 已成功就是 durable commit。锁回收失败只能告警，绝不能让
    # 调用方按“写入失败”删除已经被 Markdown 引用的图片/录音。
    if [ "$committed" = "1" ]; then
      echo "Memento: 每日写锁回收失败，记录本身已安全落档" >&2
    else
      echo "Memento: 每日写锁回收失败" >&2
    fi
  fi

  if [ "$deferred_signals" = "1" ]; then
    if [ -n "${previous_int:-}" ]; then eval "$previous_int"; else trap - INT; fi
    if [ -n "${previous_term:-}" ]; then eval "$previous_term"; else trap - TERM; fi
    if [ "$append_interrupted" = "130" ]; then
      kill -INT "${BASHPID:-$$}"
    elif [ "$append_interrupted" = "143" ]; then
      kill -TERM "${BASHPID:-$$}"
    fi
  fi
  return "$status"
}

# 把资产复制到 Vault 内的同文件系统临时目录，再使用由 mktemp 目录派生的
# 唯一 token 原子落位。成功时通过 MEMENTO_COPIED_ASSET_* 返回结果；
# 调用方可在信号 trap 中清理该路径，避免复制后、Markdown commit 前留下孤儿。
memento_copy_asset() {
  local source="$1"
  local prefix="$2"
  local extension="$3"
  local reservation
  local token
  local staged
  local basename
  local destination

  MEMENTO_COPIED_ASSET_BASENAME=""
  MEMENTO_COPIED_ASSET_PATH=""
  MEMENTO_ASSET_RESERVATION=""

  [ -f "$source" ] && [ ! -L "$source" ] && [ -s "$source" ] || return 1
  [[ "$prefix" =~ ^[A-Za-z0-9._-]+$ ]] || return 2
  [[ "$extension" =~ ^[A-Za-z0-9]+$ ]] || return 2
  mkdir -p "$MEMENTO_ASSETS_DIR" || return 1
  chmod 700 "$MEMENTO_ASSETS_DIR" 2>/dev/null || true

  reservation=$(mktemp -d "$MEMENTO_ASSETS_DIR/.memento-asset.XXXXXX") || return 1
  MEMENTO_ASSET_RESERVATION="$reservation"
  token=${reservation##*.memento-asset.}
  staged="$reservation/payload"
  basename="${prefix}-${token}.${extension}"
  destination="$MEMENTO_ASSETS_DIR/$basename"

  if ! cp "$source" "$staged" || [ ! -s "$staged" ]; then
    rm -rf "$reservation"
    MEMENTO_ASSET_RESERVATION=""
    return 1
  fi
  chmod 600 "$staged" || {
    rm -rf "$reservation"
    MEMENTO_ASSET_RESERVATION=""
    return 1
  }
  MEMENTO_COPIED_ASSET_BASENAME="$basename"
  MEMENTO_COPIED_ASSET_PATH="$destination"
  if [ -e "$destination" ] || ! mv "$staged" "$destination"; then
    rm -rf "$reservation"
    MEMENTO_COPIED_ASSET_BASENAME=""
    MEMENTO_COPIED_ASSET_PATH=""
    MEMENTO_ASSET_RESERVATION=""
    return 1
  fi
  rmdir "$reservation" 2>/dev/null || true
  MEMENTO_ASSET_RESERVATION=""
  return 0
}

memento_upgrade_daily_note() {
  local file="$1"
  local date="${2:-$(basename "$file" .md)}"
  local lock
  local token
  local content_tmp
  local replacement
  local status=0

  memento_validate_date "$date" || return 2
  [ -L "$file" ] && return 3
  [ -f "$file" ] || return 0
  memento_acquire_daily_lock "$date" || return 1
  lock="$MEMENTO_DAILY_LOCK_PATH"
  token="$MEMENTO_DAILY_LOCK_TOKEN"

  if [ "$(sed -n '1p' "$file")" != "---" ]; then
    status=2
  elif sed -n '2,/^---$/p' "$file" | grep -q '^type: memento-daily$'; then
    status=0
  else
    content_tmp=$(mktemp "${file}.content.XXXXXX") || status=1
    if [ "$status" = "0" ]; then
      replacement=$(mktemp "${file}.upgrade.XXXXXX") || status=1
    fi
    if [ "$status" = "0" ] && ! awk '
      NR == 1 { print; next }
      !added && $0 == "---" { print "type: memento-daily"; added = 1 }
      { print }
      END { if (!added) exit 42 }
    ' "$file" > "$content_tmp"; then
      status=2
    fi

    # cp -p 在 macOS 同时保留模式、ACL 和扩展属性；随后只替换内容。
    if [ "$status" = "0" ] && ! cp -p "$file" "$replacement"; then
      status=1
    fi
    if [ "$status" = "0" ] && ! cat "$content_tmp" > "$replacement"; then
      status=1
    fi
    if [ "$status" = "0" ]; then
      touch -r "$file" "$replacement" 2>/dev/null || true
      mv "$replacement" "$file" || status=1
    fi
    [ -z "$content_tmp" ] || rm -f "$content_tmp"
    [ -z "$replacement" ] || rm -f "$replacement"
  fi

  memento_release_daily_lock "$lock" "$token" || [ "$status" != "0" ] || status=1
  return "$status"
}

BASH_EOF
chmod +x "$SCRIPT_DIR/memento_env.sh"

# 给已有每日记录补上 Obsidian 可查询的类型属性,正文保持原样。
. "$SCRIPT_DIR/memento_env.sh"
UPGRADED_NOTES=0
PRESERVED_NOTES=0
shopt -s nullglob
for DAILY_FILE in "$SECRETARY_DIR"/????-??-??.md; do
  DAILY_DATE=$(basename "$DAILY_FILE" .md)
  if ! [[ "$DAILY_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || [ -L "$DAILY_FILE" ]; then
    PRESERVED_NOTES=$((PRESERVED_NOTES + 1))
    continue
  fi
  if sed -n '2,/^---$/p' "$DAILY_FILE" | grep -q '^type: memento-daily$'; then
    continue
  fi
  if memento_upgrade_daily_note "$DAILY_FILE" "$DAILY_DATE"; then
    UPGRADED_NOTES=$((UPGRADED_NOTES + 1))
  else
    PRESERVED_NOTES=$((PRESERVED_NOTES + 1))
  fi
done
shopt -u nullglob

if [ "$UPGRADED_NOTES" -gt 0 ]; then
  echo -e "${GREEN}  ✓ 已迁移 $UPGRADED_NOTES 个每日记录到 Obsidian properties${NC}"
fi
if [ "$PRESERVED_NOTES" -gt 0 ]; then
  echo -e "${YELLOW}  ⚠ $PRESERVED_NOTES 个非标准文件未改动,请手动检查${NC}"
fi

cat > "$SCRIPT_DIR/append_text.sh" << 'BASH_EOF'
#!/bin/bash
# 追加文本到今天的 Markdown (AI 友好格式 · 元信息全塑到 heading)
# 用法: printf '内容' | append_text.sh；仍兼容 append_text.sh "内容"
# 环境变量:
#   SOURCE_APP — 来源 App 名 (可选)
#   TAG        — 标签名,heading 里显示为 #TAG (可选)
#   NOTE       — 备注文本,放在内容下方的引用块 (可选)
#
# 输出示例:
#   ## 15:57 · 周日 · Feishu · #灵感
#
#   正文内容
#
#   > 备注: 在路上想到的
#
#   ---

set -e

SCRIPT_HOME=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$SCRIPT_HOME/memento_env.sh"

INPUT_FILE=$(mktemp "${TMPDIR:-/tmp}/memento-text-input.XXXXXX") || exit 1
CONVERTED_FILE=$(mktemp "${TMPDIR:-/tmp}/memento-text-utf8.XXXXXX") || {
  rm -f "$INPUT_FILE"
  exit 1
}
BLOCK=""
cleanup_text_append() {
  [ -z "$INPUT_FILE" ] || rm -f "$INPUT_FILE"
  [ -z "$CONVERTED_FILE" ] || rm -f "$CONVERTED_FILE"
  [ -z "$BLOCK" ] || rm -f "$BLOCK"
}
trap cleanup_text_append EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$#" -gt 0 ]; then
  printf '%s' "$1" > "$INPUT_FILE"
else
  cat > "$INPUT_FILE"
fi
[ -s "$INPUT_FILE" ] || exit 0

# 编码兜底: 某些 App (如飞书) 会以 GBK/GB18030 编码递给 Service,
# 若不转换就写入,文件会混编码,Trae/Obsidian 等编辑器按 UTF-8 读会乱码。
if ! iconv -f utf-8 -t utf-8 "$INPUT_FILE" >/dev/null 2>&1; then
  CONVERTED=0
  for ENC in gbk gb18030 big5; do
    if iconv -f "$ENC" -t utf-8 "$INPUT_FILE" > "$CONVERTED_FILE" 2>/dev/null; then
      mv "$CONVERTED_FILE" "$INPUT_FILE"
      CONVERTED_FILE=$(mktemp "${TMPDIR:-/tmp}/memento-text-utf8.XXXXXX")
      CONVERTED=1
      break
    fi
  done
  if [ "$CONVERTED" != "1" ]; then
    echo "Memento: 文本不是有效 UTF-8，且无法安全转换编码" >&2
    exit 1
  fi
fi

DIR="$MEMENTO_VAULT"
TODAY=$(date +%Y-%m-%d)
FILE="$DIR/$TODAY.md"
TIME=$(date +%H:%M)
WEEKDAY=$(date +%u)
WEEKDAYS=("一" "二" "三" "四" "五" "六" "日")
WD="周${WEEKDAYS[$((WEEKDAY-1))]}"

# 组装 heading: ## 时间 · 周X [· 来源] [· #标签]
HEADING="## $TIME · $WD"
[ -n "${SOURCE_APP:-}" ] && HEADING="$HEADING · $SOURCE_APP"
[ -n "${TAG:-}" ]        && HEADING="$HEADING · #$TAG"

BLOCK=$(mktemp "${TMPDIR:-/tmp}/memento-text.XXXXXX") || {
  echo "Memento: 无法创建临时记录块" >&2
  exit 1
}
if ! {
  printf '\n%s\n\n' "$HEADING"
  cat "$INPUT_FILE"
  printf '\n'
  if [ -n "${NOTE:-}" ]; then
    printf '\n'
    printf '%s\n' "$NOTE" | sed '1s/^/> 备注: /; 2,$s/^/> /'
  fi
  printf '\n---\n'
} > "$BLOCK"; then
  echo "Memento: 无法生成记录内容" >&2
  exit 1
fi

if ! memento_append_daily_block "$FILE" "$TODAY" "$BLOCK"; then
  echo "Memento: 写入 $TODAY.md 失败" >&2
  exit 1
fi

NOTIFY_MSG="已存入 $TODAY.md"
if [ "${TAG:-}" = "TODO" ]; then
  NOTIFY_MSG="已存入 [行动线索]"
elif [ -n "${TAG:-}" ]; then
  NOTIFY_MSG="已存入 [#$TAG]"
fi
osascript \
  -e 'on run argv' \
  -e 'display notification (item 1 of argv) with title "Memento"' \
  -e 'end run' \
  "$NOTIFY_MSG" >/dev/null 2>&1 &
BASH_EOF
chmod +x "$SCRIPT_DIR/append_text.sh"

cat > "$SCRIPT_DIR/append_image.sh" << 'BASH_EOF'
#!/bin/bash
# 追加图片到今天的 Markdown
# 用法: append_image.sh /path/to/image.png

set -e

SRC="$1"
[ -f "$SRC" ] && [ ! -L "$SRC" ] && [ -s "$SRC" ] || exit 1

SCRIPT_HOME=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$SCRIPT_HOME/memento_env.sh"

BLOCK=""
PENDING_ASSET=""
cleanup_image_capture() {
  [ -z "$BLOCK" ] || rm -f "$BLOCK"
  if [ "${MEMENTO_DAILY_APPEND_COMMITTED:-0}" != "1" ]; then
    [ -z "$PENDING_ASSET" ] || rm -f "$PENDING_ASSET"
    [ -z "${MEMENTO_COPIED_ASSET_PATH:-}" ] || rm -f "$MEMENTO_COPIED_ASSET_PATH"
  fi
  [ -z "${MEMENTO_ASSET_RESERVATION:-}" ] || rm -rf "$MEMENTO_ASSET_RESERVATION"
}
trap cleanup_image_capture EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

DIR="$MEMENTO_VAULT"
TODAY=$(date +%Y-%m-%d)
FILE="$DIR/$TODAY.md"
TIME=$(date +%H:%M)
TIMESTAMP=$(date +%H%M%S)
WEEKDAY=$(date +%u)
WEEKDAYS=("一" "二" "三" "四" "五" "六" "日")
WD="周${WEEKDAYS[$((WEEKDAY-1))]}"
SOURCE_NAME=${SRC##*/}
case "$SOURCE_NAME" in
  *.*) EXT=$(printf '%s' "${SOURCE_NAME##*.}" | tr '[:upper:]' '[:lower:]') ;;
  *) echo "Memento: 图片文件缺少可识别的扩展名" >&2; exit 1 ;;
esac
case "$EXT" in
  png|jpg|jpeg|gif|webp|heic|tif|tiff|bmp) ;;
  *) echo "Memento: 不支持的图片扩展名 .$EXT" >&2; exit 1 ;;
esac

if ! memento_copy_asset "$SRC" "${TODAY}-${TIMESTAMP}-image" "$EXT"; then
  echo "Memento: 图片复制失败" >&2
  exit 1
fi
BASENAME="$MEMENTO_COPIED_ASSET_BASENAME"
DEST="$MEMENTO_COPIED_ASSET_PATH"
PENDING_ASSET="$DEST"

BLOCK=$(mktemp "${TMPDIR:-/tmp}/memento-image.XXXXXX") || {
  rm -f "$DEST"
  exit 1
}
if ! printf '\n## %s · %s\n\n![](./assets/%s)\n\n---\n' \
  "$TIME" "$WD" "$BASENAME" > "$BLOCK"; then
  rm -f "$DEST"
  exit 1
fi

if ! memento_append_daily_block "$FILE" "$TODAY" "$BLOCK"; then
  rm -f "$DEST"
  exit 1
fi
PENDING_ASSET=""
MEMENTO_COPIED_ASSET_PATH=""

osascript -e 'display notification "图片已存入" with title "Memento"' >/dev/null 2>&1 &
BASH_EOF
chmod +x "$SCRIPT_DIR/append_image.sh"

cat > "$SCRIPT_DIR/append_voice.sh" << 'BASH_EOF'
#!/bin/bash
# 把本地录音和 Apple 转写作为一个语义包写入当天记录。
# 用法: append_voice.sh AUDIO TRANSCRIPT_FILE DURATION SOURCE_APP

set -e

SCRIPT_HOME=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$SCRIPT_HOME/memento_env.sh"

BLOCK=""
PENDING_ASSET=""
cleanup_voice_capture() {
  [ -z "$BLOCK" ] || rm -f "$BLOCK"
  if [ "${MEMENTO_DAILY_APPEND_COMMITTED:-0}" != "1" ]; then
    [ -z "$PENDING_ASSET" ] || rm -f "$PENDING_ASSET"
    [ -z "${MEMENTO_COPIED_ASSET_PATH:-}" ] || rm -f "$MEMENTO_COPIED_ASSET_PATH"
  fi
  [ -z "${MEMENTO_ASSET_RESERVATION:-}" ] || rm -rf "$MEMENTO_ASSET_RESERVATION"
}
trap cleanup_voice_capture EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

AUDIO="$1"
TRANSCRIPT_FILE="$2"
DURATION="$3"
SOURCE_APP="$4"

[ -f "$AUDIO" ] || exit 1

TODAY=$(date +%Y-%m-%d)
TIME=$(date +%H:%M)
TIMESTAMP=$(date +%H%M%S)
WEEKDAY=$(date +%u)
WEEKDAYS=("一" "二" "三" "四" "五" "六" "日")
WD="周${WEEKDAYS[$((WEEKDAY-1))]}"
FILE="$MEMENTO_VAULT/$TODAY.md"

mkdir -p "$MEMENTO_ASSETS_DIR"

CAPTURE_ID="${TODAY}-${TIMESTAMP}-voice"
if ! memento_copy_asset "$AUDIO" "$CAPTURE_ID" "m4a"; then
  echo "Memento: 录音复制失败" >&2
  exit 1
fi
AUDIO_NAME="$MEMENTO_COPIED_ASSET_BASENAME"
AUDIO_DEST="$MEMENTO_COPIED_ASSET_PATH"
PENDING_ASSET="$AUDIO_DEST"

HAS_TRANSCRIPT=0
if [ -f "$TRANSCRIPT_FILE" ] && [ -s "$TRANSCRIPT_FILE" ] \
  && LC_ALL=C grep -q '[^[:space:]]' "$TRANSCRIPT_FILE"; then
  HAS_TRANSCRIPT=1
fi

BLOCK=$(mktemp "${TMPDIR:-/tmp}/memento-voice.XXXXXX") || {
  rm -f "$AUDIO_DEST"
  exit 1
}
if ! {
  printf '\n## %s · %s · 语音\n\n' "$TIME" "$WD"

  if [ "$HAS_TRANSCRIPT" = "1" ]; then
    cat "$TRANSCRIPT_FILE"
  else
    printf '%s\n' "（Apple 本地语音识别未生成文字，原始录音已保留。）"
  fi

  printf '\n'
  [ -n "$SOURCE_APP" ] && printf '> 来源: %s\n' "$SOURCE_APP"
  [ -n "$DURATION" ] && printf '> 时长: %s 秒\n' "$DURATION"
  printf '> [原始录音](./assets/%s)\n' "$AUDIO_NAME"

  printf '\n---\n'
} > "$BLOCK"; then
  rm -f "$AUDIO_DEST"
  exit 1
fi

if ! memento_append_daily_block "$FILE" "$TODAY" "$BLOCK"; then
  rm -f "$AUDIO_DEST"
  exit 1
fi
PENDING_ASSET=""
MEMENTO_COPIED_ASSET_PATH=""

if [ "$HAS_TRANSCRIPT" = "1" ]; then
  NOTIFY_MSG="语音和本地转写已存入 $TODAY.md"
else
  NOTIFY_MSG="语音已存入 $TODAY.md（未生成转写）"
fi
osascript \
  -e 'on run argv' \
  -e 'display notification (item 1 of argv) with title "Memento"' \
  -e 'end run' \
  "$NOTIFY_MSG" >/dev/null 2>&1 &
BASH_EOF
chmod +x "$SCRIPT_DIR/append_voice.sh"

cat > "$SCRIPT_DIR/capture_screenshot.sh" << 'BASH_EOF'
#!/bin/bash
# 截图 → OCR → 存入今天的 Markdown (AI 友好格式 · 元信息全塑到 heading)
# 行为:
#   - 弹出 macOS 系统选区截图
#   - OCR 提取文字 (Vision 框架,支持中英文)
#   - OCR 文字 > 20 字符时,写为正文 + 原图引用
#   - OCR 文字过短或失败时,只写图片
set -e

SCRIPT_HOME=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$SCRIPT_HOME/memento_env.sh"

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/memento-screenshot.XXXXXX") || exit 1
TMP="$TMP_DIR/capture.png"
BLOCK=""
PENDING_ASSET=""
cleanup_screenshot_capture() {
  rm -rf "$TMP_DIR"
  [ -z "$BLOCK" ] || rm -f "$BLOCK"
  if [ "${MEMENTO_DAILY_APPEND_COMMITTED:-0}" != "1" ]; then
    [ -z "$PENDING_ASSET" ] || rm -f "$PENDING_ASSET"
    [ -z "${MEMENTO_COPIED_ASSET_PATH:-}" ] || rm -f "$MEMENTO_COPIED_ASSET_PATH"
  fi
  [ -z "${MEMENTO_ASSET_RESERVATION:-}" ] || rm -rf "$MEMENTO_ASSET_RESERVATION"
}
trap cleanup_screenshot_capture EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if ! screencapture -i "$TMP"; then
  exit 0
fi
[ -f "$TMP" ] && [ -s "$TMP" ] || exit 0

TODAY=$(date +%Y-%m-%d)
TIMESTAMP=$(date +%H%M%S)
if ! memento_copy_asset "$TMP" "${TODAY}-${TIMESTAMP}-screenshot" "png"; then
  echo "Memento: 截图复制失败" >&2
  exit 1
fi
BASENAME="$MEMENTO_COPIED_ASSET_BASENAME"
DEST="$MEMENTO_COPIED_ASSET_PATH"
PENDING_ASSET="$DEST"

DIR="$MEMENTO_VAULT"
FILE="$DIR/$TODAY.md"
TIME=$(date +%H:%M)
WEEKDAY=$(date +%u)
WEEKDAYS=("一" "二" "三" "四" "五" "六" "日")
WD="周${WEEKDAYS[$((WEEKDAY-1))]}"

OCR_TEXT=$("$SCRIPT_HOME/ocr_image" "$DEST" 2>/dev/null || true)
BLOCK=$(mktemp "${TMPDIR:-/tmp}/memento-screenshot-block.XXXXXX") || {
  rm -f "$DEST"
  exit 1
}

if [ ${#OCR_TEXT} -gt 20 ]; then
    if ! {
        printf '\n## %s · %s · 截图·OCR\n\n' "$TIME" "$WD"
        printf '%s\n' "$OCR_TEXT"
        printf '\n> ![原截图](./assets/%s)\n\n---\n' "$BASENAME"
    } > "$BLOCK"; then
      rm -f "$DEST"
      exit 1
    fi
    NOTIFY_MSG="文字已提取存入 $TODAY.md"
else
    if ! printf '\n## %s · %s · 截图\n\n![](./assets/%s)\n\n---\n' \
      "$TIME" "$WD" "$BASENAME" > "$BLOCK"; then
      rm -f "$DEST"
      exit 1
    fi
    NOTIFY_MSG="截图已存入"
fi

if ! memento_append_daily_block "$FILE" "$TODAY" "$BLOCK"; then
  rm -f "$DEST"
  exit 1
fi
PENDING_ASSET=""
MEMENTO_COPIED_ASSET_PATH=""

osascript \
  -e 'on run argv' \
  -e 'display notification (item 1 of argv) with title "Memento"' \
  -e 'end run' \
  "$NOTIFY_MSG" >/dev/null 2>&1 &
BASH_EOF
chmod +x "$SCRIPT_DIR/capture_screenshot.sh"

cat > "$SCRIPT_DIR/copy_today.sh" << 'BASH_EOF'
#!/bin/bash
# 把今天的 Markdown 复制到剪贴板,方便粘贴给 AI
SCRIPT_HOME=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$SCRIPT_HOME/memento_env.sh"

TODAY=$(date +%Y-%m-%d)
FILE="$MEMENTO_VAULT/$TODAY.md"

if [ ! -f "$FILE" ]; then
  osascript -e "display notification \"今天还没有任何记录\" with title \"AISecretary\""
  exit 0
fi

pbcopy < "$FILE"
LINES=$(wc -l < "$FILE" | tr -d ' ')
osascript -e "display notification \"今天的 $LINES 行已复制到剪贴板\" with title \"AISecretary\""
BASH_EOF
chmod +x "$SCRIPT_DIR/copy_today.sh"

cat > "$SCRIPT_DIR/copy_week.sh" << 'BASH_EOF'
#!/bin/bash
# 把过去 7 天的 Markdown 拼起来复制到剪贴板
SCRIPT_HOME=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$SCRIPT_HOME/memento_env.sh"

DIR="$MEMENTO_VAULT"
TMP=$(mktemp)

for i in 6 5 4 3 2 1 0; do
  DATE=$(date -v-${i}d +%Y-%m-%d 2>/dev/null || date -d "$i days ago" +%Y-%m-%d)
  FILE="$DIR/$DATE.md"
  if [ -f "$FILE" ]; then
    echo "" >> "$TMP"
    echo "# === $DATE ===" >> "$TMP"
    echo "" >> "$TMP"
    cat "$FILE" >> "$TMP"
  fi
done

if [ ! -s "$TMP" ]; then
  osascript -e "display notification \"过去 7 天没有任何记录\" with title \"AISecretary\""
  rm "$TMP"
  exit 0
fi

pbcopy < "$TMP"
rm "$TMP"
osascript -e "display notification \"本周记录已复制到剪贴板\" with title \"AISecretary\""
BASH_EOF
chmod +x "$SCRIPT_DIR/copy_week.sh"

cat > "$SCRIPT_DIR/stats.sh" << 'BASH_EOF'
#!/bin/bash
# AISecretary 自我对账
# 回答 README 里"我真的会持续用吗 / 攒了多少条"的问题

SCRIPT_HOME=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$SCRIPT_HOME/memento_env.sh"

DIR="$MEMENTO_VAULT"

if [ ! -d "$DIR" ]; then
  echo "AISecretary 文件夹不存在: $DIR"
  exit 1
fi

PATTERN='[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md'

TOTAL_DAYS=$(find "$DIR" -maxdepth 1 -type f -name "$PATTERN" | wc -l | tr -d ' ')

if [ "$TOTAL_DAYS" -eq 0 ]; then
  echo "还没有任何记录"
  exit 0
fi

TOTAL_ENTRIES=$(find "$DIR" -maxdepth 1 -type f -name "$PATTERN" -exec grep -h "^## " {} + 2>/dev/null | wc -l | tr -d ' ')
FIRST=$(find "$DIR" -maxdepth 1 -type f -name "$PATTERN" | sort | head -1 | xargs basename | sed 's/\.md$//')
LAST=$(find "$DIR" -maxdepth 1 -type f -name "$PATTERN" | sort | tail -1 | xargs basename | sed 's/\.md$//')

# 最近 7 天活跃
ACTIVE_7=0
for i in 0 1 2 3 4 5 6; do
  d=$(date -v-${i}d +%Y-%m-%d 2>/dev/null)
  [ -f "$DIR/$d.md" ] && ACTIVE_7=$((ACTIVE_7+1))
done

echo ""
echo "━━━ AISecretary 对账 ━━━"
echo ""
echo "总条数:           $TOTAL_ENTRIES"
echo "有记录的天数:     $TOTAL_DAYS"
echo "跨度:             $FIRST → $LAST"
echo "最近 7 天活跃:    $ACTIVE_7 / 7"

if [ "$ACTIVE_7" -lt 2 ]; then
  echo ""
  echo "→ 最近一周几乎没用,是真的不需要,还是忘了?"
fi
echo ""
BASH_EOF
chmod +x "$SCRIPT_DIR/stats.sh"

cat > "$SCRIPT_DIR/ocr_image.swift" << 'SWIFT_EOF'
import Vision
import Foundation

guard CommandLine.arguments.count > 1 else { exit(1) }
let imageURL = URL(fileURLWithPath: CommandLine.arguments[1])

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
request.usesLanguageCorrection = true

let handler = VNImageRequestHandler(url: imageURL)
try? handler.perform([request])

let text = (request.results ?? [])
    .compactMap { $0.topCandidates(1).first?.string }
    .joined(separator: "\n")

print(text)
SWIFT_EOF

if command -v swiftc >/dev/null 2>&1; then
  OCR_STAGE=$(mktemp "$SCRIPT_DIR/.ocr-image.XXXXXX")
  rm -f "$OCR_STAGE"
  swiftc -O "$SCRIPT_DIR/ocr_image.swift" -o "$OCR_STAGE" 2>/dev/null \
    && chmod 700 "$OCR_STAGE" \
    && mv "$OCR_STAGE" "$SCRIPT_DIR/ocr_image" && {
    echo -e "${GREEN}  ✓ OCR 二进制已编译${NC}"
  } || {
    rm -f "$OCR_STAGE"
    echo -e "${YELLOW}  ⚠ OCR 编译失败，保留已有 OCR；截图仍可落档${NC}"
    if [ -x "$SCRIPT_DEST/ocr_image" ]; then
      cp -p "$SCRIPT_DEST/ocr_image" "$SCRIPT_DIR/ocr_image"
    fi
  }
else
  echo -e "${YELLOW}  ⚠ 未找到 swiftc,跳过 OCR 编译${NC}"
  echo -e "${YELLOW}    安装 Xcode Command Line Tools 后,重跑本脚本即可获得 OCR${NC}"
  if [ -x "$SCRIPT_DEST/ocr_image" ]; then
    cp -p "$SCRIPT_DEST/ocr_image" "$SCRIPT_DIR/ocr_image"
  fi
fi

SCRIPT_VALID=1
for GENERATED_SCRIPT in "$SCRIPT_DIR"/*.sh; do
  if ! bash -n "$GENERATED_SCRIPT"; then
    SCRIPT_VALID=0
    break
  fi
done
if [ "$SCRIPT_VALID" != "1" ] \
  || ! atomic_replace_directory "$SCRIPT_STAGE" "$SCRIPT_DEST"; then
  echo -e "${RED}核心脚本校验或安装失败，上一版已保留。${NC}" >&2
  exit 1
fi
SCRIPT_STAGE=""
SCRIPT_DIR="$SCRIPT_DEST"

# 编译通用直接记录入口使用的最小复制助手。它只发送一次 Command-C，
# 不读取用户数据，也不负责写入 Vault；剪贴板读取仍由受管 Workflow 完成。
SELECTION_COPY_SRC="$INSTALLER_DIR/selection-copy-helper"
SELECTION_COPY_DEST="$SECRETARY_DIR/.apps/Memento Selection Copy.app"
SELECTION_COPY_READY=0

if [ "$HAS_CODESIGN" = "1" ] \
  && command -v swiftc >/dev/null 2>&1 \
  && [ -f "$SELECTION_COPY_SRC/MementoSelectionCopy.swift" ] \
  && [ -f "$SELECTION_COPY_SRC/Info.plist" ]; then
  SELECTION_COPY_SOURCE_HASH=$(memento_content_set_hash \
    "$SELECTION_COPY_SRC/MementoSelectionCopy.swift" \
    "$SELECTION_COPY_SRC/Info.plist")
  SELECTION_COPY_INSTALLED_HASH=$(cat "$SELECTION_COPY_DEST/Contents/Resources/source.sha256" 2>/dev/null || true)
  if [ -x "$SELECTION_COPY_DEST/Contents/MacOS/MementoSelectionCopy" ] \
    && [ "$SELECTION_COPY_INSTALLED_HASH" = "$SELECTION_COPY_SOURCE_HASH" ]; then
    SELECTION_COPY_READY=1
    echo -e "${GREEN}  ✓ 通用选区助手未变化，保留现有权限${NC}"
  else
    SELECTION_COPY_BUILD_ROOT=$(mktemp -d "$SECRETARY_DIR/.apps/.memento-selection-copy-build.XXXXXX")
    SELECTION_COPY_BUILD_APP="$SELECTION_COPY_BUILD_ROOT/Memento Selection Copy.app"
    mkdir -p "$SELECTION_COPY_BUILD_APP/Contents/MacOS" "$SELECTION_COPY_BUILD_APP/Contents/Resources"
    cp "$SELECTION_COPY_SRC/Info.plist" "$SELECTION_COPY_BUILD_APP/Contents/Info.plist"
    if swiftc -O -parse-as-library \
      "$SELECTION_COPY_SRC/MementoSelectionCopy.swift" \
      -o "$SELECTION_COPY_BUILD_APP/Contents/MacOS/MementoSelectionCopy" \
      -framework AppKit -framework ApplicationServices 2>/dev/null \
      && chmod 700 "$SELECTION_COPY_BUILD_APP/Contents/MacOS/MementoSelectionCopy" \
      && printf '%s\n' "$SELECTION_COPY_SOURCE_HASH" > "$SELECTION_COPY_BUILD_APP/Contents/Resources/source.sha256" \
      && codesign --force --sign - "$SELECTION_COPY_BUILD_APP" >/dev/null 2>&1 \
      && codesign --verify --strict "$SELECTION_COPY_BUILD_APP" >/dev/null 2>&1 \
      && plutil -lint "$SELECTION_COPY_BUILD_APP/Contents/Info.plist" >/dev/null \
      && atomic_replace_directory "$SELECTION_COPY_BUILD_APP" "$SELECTION_COPY_DEST"; then
      SELECTION_COPY_READY=1
      echo -e "${GREEN}  ✓ 通用选区助手已编译${NC}"
    else
      echo -e "${YELLOW}  ⚠ 通用选区助手构建失败，直接记录退回标准文字 Service${NC}"
      [ -x "$SELECTION_COPY_DEST/Contents/MacOS/MementoSelectionCopy" ] \
        && SELECTION_COPY_READY=1
    fi
    rm -rf "$SELECTION_COPY_BUILD_ROOT"
    SELECTION_COPY_BUILD_ROOT=""
  fi
else
  echo -e "${YELLOW}  ⚠ 缺少 swiftc、codesign 或选区助手源码，直接记录退回标准文字 Service${NC}"
  [ -x "$SELECTION_COPY_DEST/Contents/MacOS/MementoSelectionCopy" ] \
    && SELECTION_COPY_READY=1
fi

# 编译短生命周期的本地语音捕获应用。它只在第 5 个 Service 被调用时运行。
VOICE_APP_SRC="$INSTALLER_DIR/voice-capture"
VOICE_APP_DEST="$SECRETARY_DIR/.apps/Memento Voice Capture.app"
VOICE_APP_READY=0
MACOS_MAJOR=$(sw_vers -productVersion | cut -d. -f1)

if [ "$MACOS_MAJOR" -ge 26 ] && [ "$HAS_CODESIGN" = "1" ] \
  && command -v swiftc >/dev/null 2>&1 \
  && [ -f "$VOICE_APP_SRC/MementoVoiceCapture.swift" ] \
  && [ -f "$VOICE_APP_SRC/Info.plist" ]; then
  VOICE_SOURCE_HASH=$(memento_content_set_hash \
    "$VOICE_APP_SRC/MementoVoiceCapture.swift" \
    "$VOICE_APP_SRC/Info.plist")
  VOICE_INSTALLED_HASH=$(cat "$VOICE_APP_DEST/Contents/Resources/source.sha256" 2>/dev/null || true)
  if [ -x "$VOICE_APP_DEST/Contents/MacOS/MementoVoiceCapture" ] \
    && [ "$VOICE_INSTALLED_HASH" = "$VOICE_SOURCE_HASH" ]; then
    VOICE_APP_READY=1
    echo -e "${GREEN}  ✓ 语音捕获器未变化，保留现有权限${NC}"
  else
    VOICE_BUILD_ROOT=$(mktemp -d "$SECRETARY_DIR/.apps/.memento-voice-build.XXXXXX")
    VOICE_BUILD_APP="$VOICE_BUILD_ROOT/Memento Voice Capture.app"
    mkdir -p "$VOICE_BUILD_APP/Contents/MacOS" "$VOICE_BUILD_APP/Contents/Resources"
    cp "$VOICE_APP_SRC/Info.plist" "$VOICE_BUILD_APP/Contents/Info.plist"
    if swiftc -O -parse-as-library \
      "$VOICE_APP_SRC/MementoVoiceCapture.swift" \
      -o "$VOICE_BUILD_APP/Contents/MacOS/MementoVoiceCapture" \
      -framework AppKit -framework AVFoundation -framework Speech 2>/dev/null \
      && chmod 700 "$VOICE_BUILD_APP/Contents/MacOS/MementoVoiceCapture" \
      && printf '%s\n' "$VOICE_SOURCE_HASH" > "$VOICE_BUILD_APP/Contents/Resources/source.sha256" \
      && codesign --force --sign - "$VOICE_BUILD_APP" >/dev/null 2>&1 \
      && codesign --verify --strict "$VOICE_BUILD_APP" >/dev/null 2>&1 \
      && plutil -lint "$VOICE_BUILD_APP/Contents/Info.plist" >/dev/null \
      && atomic_replace_directory "$VOICE_BUILD_APP" "$VOICE_APP_DEST"; then
      VOICE_APP_READY=1
      echo -e "${GREEN}  ✓ Apple 本地语音捕获器已编译${NC}"
    else
      echo -e "${YELLOW}  ⚠ 语音捕获器构建失败，保留已有版本${NC}"
      [ -x "$VOICE_APP_DEST/Contents/MacOS/MementoVoiceCapture" ] && VOICE_APP_READY=1
    fi
    rm -rf "$VOICE_BUILD_ROOT"
  fi
else
  echo -e "${YELLOW}  ⚠ 本地语音需要 macOS 26、swiftc、codesign 和 voice-capture 源码${NC}"
  [ -x "$VOICE_APP_DEST/Contents/MacOS/MementoVoiceCapture" ] && VOICE_APP_READY=1
fi

# ============================================================
# Step 4: 安装 macOS Workflow
# ============================================================
echo -e "${BLUE}[4/6] 安装右键菜单服务...${NC}"

memento_workflow_owned() {
  local workflow="$1"
  local marker="$workflow/Contents/.memento-managed"
  local document="$workflow/Contents/document.wflow"

  [ -f "$marker" ] && grep -q '^com.memento.workflow.v1$' "$marker" && return 0
  # 兼容接管本项目早期没有 marker 的 Workflow；必须同时匹配 Memento 专属路径。
  [ -f "$document" ] && grep -Eq 'AISecretary/\.(scripts|apps)/|Memento Voice Capture\.app' "$document"
}

remove_owned_workflow() {
  local workflow="$1"
  [ -d "$workflow" ] || return 0
  if memento_workflow_owned "$workflow"; then
    rm -rf "$workflow"
    return 0
  fi
  echo -e "${YELLOW}  ⚠ 保留非 Memento 管理的同名 Service: $(basename "$workflow" .workflow)${NC}"
  return 1
}

write_workflow() {
  local NAME="$1"
  local INPUT_TYPE="$2"
  local CMD_STRING="$3"
  local FINAL_WF="$SERVICES_DIR/$NAME.workflow"
  local STAGING_ROOT
  local STAGED_WF
  local WF_DIR

  if [ -d "$FINAL_WF" ] && ! memento_workflow_owned "$FINAL_WF"; then
    echo -e "${YELLOW}  ⚠ 同名 Service 不属于 Memento，已跳过: $NAME${NC}"
    return 0
  fi

  STAGING_ROOT=$(mktemp -d "$SERVICES_DIR/.memento-workflow.XXXXXX") || return 1
  STAGED_WF="$STAGING_ROOT/$NAME.workflow"
  WF_DIR="$STAGED_WF/Contents"
  mkdir -p "$WF_DIR"

  # Info.plist: 选中文字触发的 service 需 NSSendTypes; 无输入的不需要
  if [ "$INPUT_TYPE" = "text" ]; then
    cat > "$WF_DIR/Info.plist" << INFO_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>NSServices</key>
    <array>
        <dict>
            <key>NSMenuItem</key>
            <dict>
                <key>default</key>
                <string>$NAME</string>
            </dict>
            <key>NSMessage</key>
            <string>runWorkflowAsService</string>
            <key>NSSendTypes</key>
            <array>
                <string>NSStringPboardType</string>
                <string>public.plain-text</string>
            </array>
        </dict>
    </array>
</dict>
</plist>
INFO_EOF
  else
    cat > "$WF_DIR/Info.plist" << INFO_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>NSServices</key>
    <array>
        <dict>
            <key>NSMenuItem</key>
            <dict>
                <key>default</key>
                <string>$NAME</string>
            </dict>
            <key>NSMessage</key>
            <string>runWorkflowAsService</string>
        </dict>
    </array>
</dict>
</plist>
INFO_EOF
  fi

  # COMMAND_STRING 需要 XML 转义 (& < >)
  local CMD_ESC
  CMD_ESC=$(printf '%s' "$CMD_STRING" | python3 -c 'import sys, html; sys.stdout.write(html.escape(sys.stdin.read()))')
  local INPUT_ID="com.apple.Automator.$INPUT_TYPE"

  cat > "$WF_DIR/document.wflow" << WFLOW_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>AMApplicationBuild</key>
	<string>512</string>
	<key>AMApplicationVersion</key>
	<string>2.10</string>
	<key>AMDocumentVersion</key>
	<string>2</string>
	<key>actions</key>
	<array>
		<dict>
			<key>action</key>
			<dict>
				<key>AMAccepts</key>
				<dict>
					<key>Container</key>
					<string>List</string>
					<key>Optional</key>
					<true/>
					<key>Types</key>
					<array>
						<string>com.apple.cocoa.string</string>
					</array>
				</dict>
				<key>AMActionVersion</key>
				<string>2.0.3</string>
				<key>AMParameterProperties</key>
				<dict>
					<key>COMMAND_STRING</key>
					<dict/>
					<key>CheckedForUserDefaultShell</key>
					<dict/>
					<key>inputMethod</key>
					<dict/>
					<key>shell</key>
					<dict/>
					<key>source</key>
					<dict/>
				</dict>
				<key>AMProvides</key>
				<dict>
					<key>Container</key>
					<string>List</string>
					<key>Types</key>
					<array>
						<string>com.apple.cocoa.string</string>
					</array>
				</dict>
				<key>ActionBundlePath</key>
				<string>/System/Library/Automator/Run Shell Script.action</string>
				<key>ActionName</key>
				<string>Run Shell Script</string>
				<key>ActionParameters</key>
				<dict>
					<key>COMMAND_STRING</key>
					<string>$CMD_ESC</string>
					<key>CheckedForUserDefaultShell</key>
					<true/>
					<key>inputMethod</key>
					<integer>0</integer>
					<key>shell</key>
					<string>/bin/bash</string>
					<key>source</key>
					<string></string>
				</dict>
				<key>BundleIdentifier</key>
				<string>com.apple.RunShellScript</string>
				<key>CFBundleVersion</key>
				<string>2.0.3</string>
				<key>CanShowSelectedItemsWhenRun</key>
				<false/>
				<key>CanShowWhenRun</key>
				<true/>
				<key>Category</key>
				<array>
					<string>AMCategoryUtilities</string>
				</array>
				<key>Class Name</key>
				<string>RunShellScriptAction</string>
				<key>InputUUID</key>
				<string>96F7841E-ADE8-4A09-9B67-038E1F11DB06</string>
				<key>Keywords</key>
				<array>
					<string>Shell</string>
					<string>Script</string>
					<string>Command</string>
					<string>Run</string>
					<string>Unix</string>
				</array>
				<key>OutputUUID</key>
				<string>9FCFE354-D7F1-414E-B3B4-3DBAD39E897E</string>
				<key>UUID</key>
				<string>C1DD8E96-6D6B-4162-BDFA-6D13A77F6405</string>
				<key>UnlocalizedApplications</key>
				<array>
					<string>Automator</string>
				</array>
				<key>arguments</key>
				<dict>
					<key>0</key>
					<dict>
						<key>default value</key>
						<integer>0</integer>
						<key>name</key>
						<string>inputMethod</string>
						<key>required</key>
						<string>0</string>
						<key>type</key>
						<string>0</string>
						<key>uuid</key>
						<string>0</string>
					</dict>
					<key>1</key>
					<dict>
						<key>default value</key>
						<false/>
						<key>name</key>
						<string>CheckedForUserDefaultShell</string>
						<key>required</key>
						<string>0</string>
						<key>type</key>
						<string>0</string>
						<key>uuid</key>
						<string>1</string>
					</dict>
					<key>2</key>
					<dict>
						<key>default value</key>
						<string></string>
						<key>name</key>
						<string>source</string>
						<key>required</key>
						<string>0</string>
						<key>type</key>
						<string>0</string>
						<key>uuid</key>
						<string>2</string>
					</dict>
					<key>3</key>
					<dict>
						<key>default value</key>
						<string></string>
						<key>name</key>
						<string>COMMAND_STRING</string>
						<key>required</key>
						<string>0</string>
						<key>type</key>
						<string>0</string>
						<key>uuid</key>
						<string>3</string>
					</dict>
					<key>4</key>
					<dict>
						<key>default value</key>
						<string>/bin/sh</string>
						<key>name</key>
						<string>shell</string>
						<key>required</key>
						<string>0</string>
						<key>type</key>
						<string>0</string>
						<key>uuid</key>
						<string>4</string>
					</dict>
				</dict>
				<key>conversionLabel</key>
				<integer>0</integer>
				<key>isViewVisible</key>
				<integer>1</integer>
				<key>location</key>
				<string>309.000000:316.000000</string>
				<key>nibPath</key>
				<string>/System/Library/Automator/Run Shell Script.action/Contents/Resources/Base.lproj/main.nib</string>
			</dict>
			<key>isViewVisible</key>
			<integer>1</integer>
		</dict>
	</array>
	<key>connectors</key>
	<dict/>
	<key>workflowMetaData</key>
	<dict>
		<key>serviceApplicationBundleID</key>
		<string></string>
		<key>serviceApplicationPath</key>
		<string></string>
		<key>serviceInputTypeIdentifier</key>
		<string>$INPUT_ID</string>
		<key>serviceOutputTypeIdentifier</key>
		<string>com.apple.Automator.nothing</string>
		<key>serviceProcessesInput</key>
		<integer>0</integer>
		<key>useAutomaticInputType</key>
		<integer>0</integer>
		<key>workflowTypeIdentifier</key>
		<string>com.apple.Automator.servicesMenu</string>
	</dict>
</dict>
</plist>
WFLOW_EOF
  printf '%s\n' 'com.memento.workflow.v1' > "$WF_DIR/.memento-managed"
  chmod 600 "$WF_DIR/.memento-managed"

  if ! plutil -lint "$WF_DIR/Info.plist" >/dev/null \
    || ! plutil -lint "$WF_DIR/document.wflow" >/dev/null \
    || ! atomic_replace_directory "$STAGED_WF" "$FINAL_WF"; then
    rm -rf "$STAGING_ROOT"
    echo -e "${YELLOW}  ⚠ $NAME 安装失败，已保留原版本${NC}"
    return 1
  fi
  rm -rf "$STAGING_ROOT"
  echo -e "${GREEN}  ✓ $NAME${NC}"
}

# 把 COMMAND_STRING 用单引号 HEREDOC 读进变量(避免转义噩梦)
if [ "$SELECTION_COPY_READY" = "1" ]; then
CMD_DIRECT=$(cat << 'CMD_EOF'
export SOURCE_APP=$(osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true' 2>/dev/null)
SOURCE_PID=$(osascript -e 'tell application "System Events" to get unix id of first application process whose frontmost is true' 2>/dev/null)
HELPER="$HOME/AISecretary/.apps/Memento Selection Copy.app/Contents/MacOS/MementoSelectionCopy"
LOG_DIR="$HOME/AISecretary/.logs"
LOG_FILE="$LOG_DIR/selection-shortcut.log"
mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR" 2>/dev/null || true
printf '%s shortcut=1 phase=start source_app=%s source_pid=%s\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$SOURCE_APP" "$SOURCE_PID" >> "$LOG_FILE"

"$HELPER" --check
HELPER_STATUS=$?
if [ "$HELPER_STATUS" -eq 77 ]; then
  open 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'
  osascript -e 'display notification "请允许 Memento 选区助手，然后回到原应用重试" with title "Memento"' 2>/dev/null || true
  exit 0
fi
[ "$HELPER_STATUS" -eq 0 ] || exit 0

printf '' | pbcopy
COPY_RESULT=$("$HELPER" --pid "$SOURCE_PID" 2>&1)
HELPER_STATUS=$?
printf '%s shortcut=1 phase=copy helper_status=%s result=%s\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$HELPER_STATUS" "$COPY_RESULT" >> "$LOG_FILE"
if [ "$HELPER_STATUS" -ne 0 ]; then
  osascript -e 'display notification "复制助手未能发送 Command-C，请检查辅助功能授权" with title "Memento"' 2>/dev/null || true
  exit 0
fi

CAPTURE_FILE=$(mktemp "${TMPDIR:-/tmp}/memento-selection-text.XXXXXX") || exit 0
trap 'rm -f "$CAPTURE_FILE"' EXIT
CAPTURE_READY=0
ATTEMPT=0
while [ "$ATTEMPT" -lt 30 ]; do
  pbpaste -Prefer txt > "$CAPTURE_FILE" 2>/dev/null || true
  if [ -s "$CAPTURE_FILE" ] \
    && iconv -f utf-8 -t utf-8 "$CAPTURE_FILE" >/dev/null 2>&1; then
    CAPTURE_READY=1
    break
  fi
  sleep 0.05
  ATTEMPT=$((ATTEMPT + 1))
done

if [ "$CAPTURE_READY" != "1" ]; then
  printf '%s shortcut=1 phase=capture ready=0 bytes=0\n' \
    "$(date '+%Y-%m-%dT%H:%M:%S%z')" >> "$LOG_FILE"
  osascript -e 'display notification "没有取得有效文字，请保持文字处于选中状态后重试" with title "Memento"' 2>/dev/null || true
  exit 0
fi

CAPTURE_BYTES=$(wc -c < "$CAPTURE_FILE" | tr -d ' ')
printf '%s shortcut=1 phase=capture ready=1 bytes=%s\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$CAPTURE_BYTES" >> "$LOG_FILE"
"$HOME/AISecretary/.scripts/append_text.sh" < "$CAPTURE_FILE"
CMD_EOF
)

CMD_TAG=$(cat << 'CMD_EOF'
export SOURCE_APP=$(osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true' 2>/dev/null)
SOURCE_PID=$(osascript -e 'tell application "System Events" to get unix id of first application process whose frontmost is true' 2>/dev/null)
HELPER="$HOME/AISecretary/.apps/Memento Selection Copy.app/Contents/MacOS/MementoSelectionCopy"
LOG_DIR="$HOME/AISecretary/.logs"
LOG_FILE="$LOG_DIR/selection-shortcut.log"
mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR" 2>/dev/null || true
printf '%s shortcut=3 phase=start source_app=%s source_pid=%s\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$SOURCE_APP" "$SOURCE_PID" >> "$LOG_FILE"

"$HELPER" --check
HELPER_STATUS=$?
if [ "$HELPER_STATUS" -eq 77 ]; then
  open 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'
  osascript -e 'display notification "请允许 Memento 选区助手，然后回到原应用重试" with title "Memento"' 2>/dev/null || true
  exit 0
fi
[ "$HELPER_STATUS" -eq 0 ] || exit 0

printf '' | pbcopy
COPY_RESULT=$("$HELPER" --pid "$SOURCE_PID" 2>&1)
HELPER_STATUS=$?
printf '%s shortcut=3 phase=copy helper_status=%s result=%s\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$HELPER_STATUS" "$COPY_RESULT" >> "$LOG_FILE"
if [ "$HELPER_STATUS" -ne 0 ]; then
  osascript -e 'display notification "复制助手未能发送 Command-C，请检查辅助功能授权" with title "Memento"' 2>/dev/null || true
  exit 0
fi

CAPTURE_FILE=$(mktemp "${TMPDIR:-/tmp}/memento-selection-text.XXXXXX") || exit 0
trap 'rm -f "$CAPTURE_FILE"' EXIT
CAPTURE_READY=0
ATTEMPT=0
while [ "$ATTEMPT" -lt 30 ]; do
  pbpaste -Prefer txt > "$CAPTURE_FILE" 2>/dev/null || true
  if [ -s "$CAPTURE_FILE" ] \
    && iconv -f utf-8 -t utf-8 "$CAPTURE_FILE" >/dev/null 2>&1; then
    CAPTURE_READY=1
    break
  fi
  sleep 0.05
  ATTEMPT=$((ATTEMPT + 1))
done

if [ "$CAPTURE_READY" != "1" ]; then
  printf '%s shortcut=3 phase=capture ready=0 bytes=0\n' \
    "$(date '+%Y-%m-%dT%H:%M:%S%z')" >> "$LOG_FILE"
  osascript -e 'display notification "没有取得有效文字，请保持文字处于选中状态后重试" with title "Memento"' 2>/dev/null || true
  exit 0
fi

CAPTURE_BYTES=$(wc -c < "$CAPTURE_FILE" | tr -d ' ')
printf '%s shortcut=3 phase=capture ready=1 bytes=%s\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$CAPTURE_BYTES" >> "$LOG_FILE"
TAG=$(osascript \
  -e 'set labels to {"灵感", "行动线索", "下次再读"}' \
  -e 'set c to choose from list labels with prompt "选择记录标签（只用于回看，不表示待办或优先级）"' \
  -e 'if c is false then return ""' \
  -e 'set chosenLabel to item 1 of c' \
  -e 'if chosenLabel is "行动线索" then return "TODO"' \
  -e 'return chosenLabel' 2>/dev/null)
[ -z "$TAG" ] && exit 0
export TAG
"$HOME/AISecretary/.scripts/append_text.sh" < "$CAPTURE_FILE"
CMD_EOF
)

CMD_NOTE=$(cat << 'CMD_EOF'
export SOURCE_APP=$(osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true' 2>/dev/null)
SOURCE_PID=$(osascript -e 'tell application "System Events" to get unix id of first application process whose frontmost is true' 2>/dev/null)
HELPER="$HOME/AISecretary/.apps/Memento Selection Copy.app/Contents/MacOS/MementoSelectionCopy"
LOG_DIR="$HOME/AISecretary/.logs"
LOG_FILE="$LOG_DIR/selection-shortcut.log"
mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR" 2>/dev/null || true
printf '%s shortcut=2 phase=start source_app=%s source_pid=%s\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$SOURCE_APP" "$SOURCE_PID" >> "$LOG_FILE"

"$HELPER" --check
HELPER_STATUS=$?
if [ "$HELPER_STATUS" -eq 77 ]; then
  open 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'
  osascript -e 'display notification "请允许 Memento 选区助手，然后回到原应用重试" with title "Memento"' 2>/dev/null || true
  exit 0
fi
[ "$HELPER_STATUS" -eq 0 ] || exit 0

printf '' | pbcopy
COPY_RESULT=$("$HELPER" --pid "$SOURCE_PID" 2>&1)
HELPER_STATUS=$?
printf '%s shortcut=2 phase=copy helper_status=%s result=%s\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$HELPER_STATUS" "$COPY_RESULT" >> "$LOG_FILE"
if [ "$HELPER_STATUS" -ne 0 ]; then
  osascript -e 'display notification "复制助手未能发送 Command-C，请检查辅助功能授权" with title "Memento"' 2>/dev/null || true
  exit 0
fi

CAPTURE_FILE=$(mktemp "${TMPDIR:-/tmp}/memento-selection-text.XXXXXX") || exit 0
trap 'rm -f "$CAPTURE_FILE"' EXIT
CAPTURE_READY=0
ATTEMPT=0
while [ "$ATTEMPT" -lt 30 ]; do
  pbpaste -Prefer txt > "$CAPTURE_FILE" 2>/dev/null || true
  if [ -s "$CAPTURE_FILE" ] \
    && iconv -f utf-8 -t utf-8 "$CAPTURE_FILE" >/dev/null 2>&1; then
    CAPTURE_READY=1
    break
  fi
  sleep 0.05
  ATTEMPT=$((ATTEMPT + 1))
done

if [ "$CAPTURE_READY" != "1" ]; then
  printf '%s shortcut=2 phase=capture ready=0 bytes=0\n' \
    "$(date '+%Y-%m-%dT%H:%M:%S%z')" >> "$LOG_FILE"
  osascript -e 'display notification "没有取得有效文字，请保持文字处于选中状态后重试" with title "Memento"' 2>/dev/null || true
  exit 0
fi

CAPTURE_BYTES=$(wc -c < "$CAPTURE_FILE" | tr -d ' ')
printf '%s shortcut=2 phase=capture ready=1 bytes=%s\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$CAPTURE_BYTES" >> "$LOG_FILE"
NOTE=$(osascript -e 'set d to display dialog "备注 (可留空):" default answer "" buttons {"取消","存入"} default button "存入"' -e 'if button returned of d is "取消" then return "__CANCEL__"' -e 'return text returned of d' 2>/dev/null) || exit 0
[ "$NOTE" = "__CANCEL__" ] && exit 0
export NOTE
"$HOME/AISecretary/.scripts/append_text.sh" < "$CAPTURE_FILE"
CMD_EOF
)

TEXT_WORKFLOW_INPUT="nothing"
else
CMD_DIRECT=$(cat << 'CMD_EOF'
export SOURCE_APP=$(osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true' 2>/dev/null)
"$HOME/AISecretary/.scripts/append_text.sh"
CMD_EOF
)

CMD_TAG=$(cat << 'CMD_EOF'
export SOURCE_APP=$(osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true' 2>/dev/null)
TAG=$(osascript \
  -e 'set labels to {"灵感", "行动线索", "下次再读"}' \
  -e 'set c to choose from list labels with prompt "选择记录标签（只用于回看，不表示待办或优先级）"' \
  -e 'if c is false then return ""' \
  -e 'set chosenLabel to item 1 of c' \
  -e 'if chosenLabel is "行动线索" then return "TODO"' \
  -e 'return chosenLabel' 2>/dev/null)
[ -z "$TAG" ] && exit 0
export TAG
"$HOME/AISecretary/.scripts/append_text.sh"
CMD_EOF
)

CMD_NOTE=$(cat << 'CMD_EOF'
export SOURCE_APP=$(osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true' 2>/dev/null)
NOTE=$(osascript -e 'set d to display dialog "备注 (可留空):" default answer "" buttons {"取消","存入"} default button "存入"' -e 'if button returned of d is "取消" then return "__CANCEL__"' -e 'return text returned of d' 2>/dev/null) || exit 0
[ "$NOTE" = "__CANCEL__" ] && exit 0
export NOTE
"$HOME/AISecretary/.scripts/append_text.sh"
CMD_EOF
)

TEXT_WORKFLOW_INPUT="text"
fi

CMD_SHOT=$(cat << 'CMD_EOF'
export SOURCE_APP=$(osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true' 2>/dev/null)
"$HOME/AISecretary/.scripts/capture_screenshot.sh"
CMD_EOF
)

CMD_VOICE=$(cat << 'CMD_EOF'
SOURCE_APP=$(osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true' 2>/dev/null)
open "$HOME/AISecretary/.apps/Memento Voice Capture.app" --args \
  --source-app "$SOURCE_APP"
CMD_EOF
)

# 旧版「AI秘书·*」与 v4 的语音+截图入口已废弃。升级时只清理
# Workflow 本身，不触碰 Vault、历史记录或用户设置里的其他服务。
shopt -s nullglob
for LEGACY_WF in "$SERVICES_DIR/AI秘书.workflow" "$SERVICES_DIR"/AI秘书·*.workflow; do
  [ -d "$LEGACY_WF" ] || continue
  if remove_owned_workflow "$LEGACY_WF"; then
    echo -e "${BLUE}  → 清理旧入口: $(basename "$LEGACY_WF" .workflow)${NC}"
  fi
done
shopt -u nullglob
remove_owned_workflow "$SERVICES_DIR/存入 AI 秘书 (语音+截图).workflow" || true

write_workflow "存入 AI 秘书"           "$TEXT_WORKFLOW_INPUT" "$CMD_DIRECT"
write_workflow "存入 AI 秘书 (选标签)"  "$TEXT_WORKFLOW_INPUT" "$CMD_TAG"
write_workflow "存入 AI 秘书 (加备注)"  "$TEXT_WORKFLOW_INPUT" "$CMD_NOTE"
write_workflow "存入 AI 秘书 (截图)"    "nothing" "$CMD_SHOT"
remove_owned_workflow "$SERVICES_DIR/存入 AI 秘书 (微信测试).workflow" || true
if [ "$VOICE_APP_READY" = "1" ]; then
  write_workflow "存入 AI 秘书 (语音)" "nothing" "$CMD_VOICE"
else
  remove_owned_workflow "$SERVICES_DIR/存入 AI 秘书 (语音).workflow" || true
fi

# ============================================================
# Step 5: 刷新 Services 注册
# ============================================================
echo -e "${BLUE}[5/6] 刷新 Services 注册...${NC}"
if [ "${MEMENTO_SKIP_SERVICE_REFRESH:-0}" != "1" ]; then
  /System/Library/CoreServices/pbs -update 2>/dev/null || true
fi

# ============================================================
# Step 6: 安装 Dashboard 和 Context Agent 运行时
# ============================================================
echo -e "${BLUE}[6/6] 安装 Dashboard 和 Context Agent 运行时...${NC}"
NEWTAB_SRC="$INSTALLER_DIR/chrome-newtab"
NEWTAB_DEST="$SECRETARY_DIR/.chrome-newtab"
HAS_NEWTAB=0

memento_dashboard_runtime_valid() {
  local directory="$1"
  [ -f "$directory/manifest.json" ] \
    && [ -f "$directory/dashboard.html" ] \
    && [ -f "$directory/dashboard.js" ] \
    && [ -f "$directory/cognitive-demo-fixture.js" ] \
    && [ -f "$directory/cognitive-home-library.js" ] \
    && [ -f "$directory/context-agent-library.js" ] \
    && [ -f "$directory/remember-agent-v1-library.js" ] \
    && [ -f "$directory/dashboard-cache-library.js" ] \
    && [ -f "$directory/dashboard-operations-library.js" ] \
    && python3 -m json.tool "$directory/manifest.json" >/dev/null \
    || return 1
  # Node 是开发 / 发布校验依赖，不是用户基础安装的前置。
  # 安装机已有 Node 时同步做语法校验；发布合同则强制执行。
  if command -v node >/dev/null 2>&1; then
    node --check "$directory/dashboard.js" >/dev/null \
      && node --check "$directory/cognitive-demo-fixture.js" >/dev/null \
      && node --check "$directory/cognitive-home-library.js" >/dev/null \
      && node --check "$directory/context-agent-library.js" >/dev/null \
      && node --check "$directory/dashboard-cache-library.js" >/dev/null \
      && node --check "$directory/dashboard-operations-library.js" >/dev/null \
      && node --check "$directory/photo-cache-library.js" >/dev/null \
      && node --check "$directory/remember-agent-v1-library.js" >/dev/null
  fi
}

if [ -d "$NEWTAB_SRC" ]; then
  NEWTAB_STAGE=$(mktemp -d "$SECRETARY_DIR/.chrome-newtab-stage.XXXXXX")
  if cp -R "$NEWTAB_SRC/." "$NEWTAB_STAGE/" \
    && memento_dashboard_runtime_valid "$NEWTAB_STAGE" \
    && atomic_replace_directory "$NEWTAB_STAGE" "$NEWTAB_DEST"; then
    echo -e "${GREEN}  ✓ 已复制到 $NEWTAB_DEST${NC}"
    HAS_NEWTAB=1
  else
    rm -rf "$NEWTAB_STAGE"
    echo -e "${YELLOW}  ⚠ Dashboard 校验或安装失败，已保留原版本${NC}"
    if memento_dashboard_runtime_valid "$NEWTAB_DEST"; then
      HAS_NEWTAB=1
    fi
  fi
else
  echo -e "${YELLOW}  ⚠ 安装包内未找到 chrome-newtab/,跳过 dashboard 安装${NC}"
fi

tighten_vault_permissions

CONTEXT_SRC="$INSTALLER_DIR/context-agent"
HAS_CONTEXT_AGENT=0

if [ "$CONTEXT_PATHS_SAFE" = "1" ] \
  && memento_validate_context_tree \
  && [ -d "$CONTEXT_SRC" ]; then
  CONTEXT_STAGE=$(mktemp -d "$SECRETARY_DIR/.context-agent-runtime-stage.XXXXXX")
  CONTEXT_STAGE_READY=0
  if cp -R "$CONTEXT_SRC/." "$CONTEXT_STAGE/"; then
    cat > "$CONTEXT_STAGE/run_self_reflection_once.sh" <<'SELF_REFLECTION_RUNNER'
#!/bin/bash
set -u

if [ "$#" -ne 1 ]; then
  echo "usage: run_self_reflection_once.sh VAULT" >&2
  exit 2
fi

RUNTIME_DIR=$(cd "$(dirname "$0")" && pwd)
VAULT=$1
MEMENTO_PYTHON=__MEMENTO_PYTHON_PATH__
"$MEMENTO_PYTHON" "$RUNTIME_DIR/context_agent.py" \
  self-reflection-worker --vault "$VAULT" --once
SELF_REFLECTION_RUNNER

    cat > "$CONTEXT_STAGE/run_remember_agent_v1_once.sh" <<'REMEMBER_AGENT_RUNNER'
#!/bin/bash
set -u

if [ "$#" -ne 1 ]; then
  echo "usage: run_remember_agent_v1_once.sh VAULT" >&2
  exit 2
fi

RUNTIME_DIR=$(cd "$(dirname "$0")" && pwd)
VAULT=$1
MEMENTO_PYTHON=__MEMENTO_PYTHON_PATH__
AGENT_V1_GATE="$VAULT/.context-agent/agent-v1/enabled"

# Re:member Agent V1 ships as a disabled RC.  Only one exact, private,
# current-user-owned regular file may opt this event runner in.  The
# check and read share one O_NOFOLLOW descriptor so symlinks, hard links,
# permission drift and content drift all fail closed.
if ! PYTHONDONTWRITEBYTECODE=1 "$MEMENTO_PYTHON" - "$AGENT_V1_GATE" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
try:
    descriptor = os.open(path, flags)
except OSError:
    raise SystemExit(1)
try:
    metadata = os.fstat(descriptor)
    valid = (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_nlink == 1
        and os.read(descriptor, 12) == b"enabled-v1\n"
    )
finally:
    os.close(descriptor)
raise SystemExit(0 if valid else 1)
PY
then
  # 未启用、损坏或不安全的 gate 都是正常的 fail-closed 结果。
  exit 0
fi

# 先物化用户反馈并重投影，再处理旧 Agent inbox。
# 当前版本不消费手动日级请求，避免生成独立日评价。
"$MEMENTO_PYTHON" "$RUNTIME_DIR/context_agent.py" \
  cognitive-action-worker --vault "$VAULT" --once || exit $?
"$MEMENTO_PYTHON" "$RUNTIME_DIR/context_agent.py" \
  agent-worker --vault "$VAULT" --once
REMEMBER_AGENT_RUNNER

    cat > "$CONTEXT_STAGE/run_cognitive_record_once.sh" <<'COGNITIVE_RECORD_RUNNER'
#!/bin/bash
set -u

if [ "$#" -ne 2 ]; then
  echo "usage: run_cognitive_record_once.sh VAULT YYYY-MM-DD" >&2
  exit 2
fi

RUNTIME_DIR=$(cd "$(dirname "$0")" && pwd)
VAULT=$1
LOCAL_DATE=$2
MEMENTO_PYTHON=__MEMENTO_PYTHON_PATH__

case "$LOCAL_DATE" in
  ????-??-??) ;;
  *) exit 2 ;;
esac

# Ingest 只建立本地索引与幂等请求；worker 会再校验用户 gate，
# 新安装未启用时不构造 Provider。
"$MEMENTO_PYTHON" "$RUNTIME_DIR/context_agent.py" \
  record-ingest --vault "$VAULT" --source "$LOCAL_DATE.md" >/dev/null || exit $?
"$MEMENTO_PYTHON" "$RUNTIME_DIR/context_agent.py" \
  record-worker --once --vault "$VAULT" --date "$LOCAL_DATE" --limit 16
COGNITIVE_RECORD_RUNNER

    "$PYTHON3_RUNTIME" - \
      "$CONTEXT_STAGE/run_self_reflection_once.sh" \
      "$CONTEXT_STAGE/run_remember_agent_v1_once.sh" \
      "$CONTEXT_STAGE/run_cognitive_record_once.sh" \
      "$PYTHON3_RUNTIME" <<'PY'
import shlex
import sys
from pathlib import Path

paths = [Path(value) for value in sys.argv[1:-1]]
python_path = sys.argv[-1]
placeholder = "__MEMENTO_PYTHON_PATH__"
for path in paths:
    text = path.read_text(encoding="utf-8")
    if text.count(placeholder) != 1:
        raise SystemExit(1)
    path.write_text(text.replace(placeholder, shlex.quote(python_path)), encoding="utf-8")
PY
    chmod 700 \
      "$CONTEXT_STAGE/run_self_reflection_once.sh" \
      "$CONTEXT_STAGE/run_remember_agent_v1_once.sh" \
      "$CONTEXT_STAGE/run_cognitive_record_once.sh"
  fi
  COGNITIVE_RUNTIME_FILES=(
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
  COGNITIVE_RUNTIME_READY=1
  for COGNITIVE_RUNTIME_FILE in "${COGNITIVE_RUNTIME_FILES[@]}"; do
    if [ ! -f "$CONTEXT_STAGE/$COGNITIVE_RUNTIME_FILE" ] \
      || [ -L "$CONTEXT_STAGE/$COGNITIVE_RUNTIME_FILE" ] \
      || ! PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" -c \
        'import ast,pathlib,sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))' \
        "$CONTEXT_STAGE/$COGNITIVE_RUNTIME_FILE"; then
      COGNITIVE_RUNTIME_READY=0
    fi
  done
  if [ "$COGNITIVE_RUNTIME_READY" = "1" ] \
    && [ -f "$CONTEXT_STAGE/context_agent.py" ] \
    && [ -f "$CONTEXT_STAGE/core.py" ] \
    && [ -f "$CONTEXT_STAGE/deepseek_provider.py" ] \
    && [ -f "$CONTEXT_STAGE/reflection.py" ] \
    && [ -f "$CONTEXT_STAGE/agent_v1.py" ] \
    && [ -f "$CONTEXT_STAGE/schemas/record_interpreter_action_v1.json" ] \
    && [ ! -L "$CONTEXT_STAGE/schemas/record_interpreter_action_v1.json" ] \
    && [ -f "$CONTEXT_STAGE/schemas/daily_integrator_action_v1.json" ] \
    && [ ! -L "$CONTEXT_STAGE/schemas/daily_integrator_action_v1.json" ] \
    && "$PYTHON3_RUNTIME" -m json.tool \
      "$CONTEXT_STAGE/schemas/record_interpreter_action_v1.json" >/dev/null \
    && "$PYTHON3_RUNTIME" -m json.tool \
      "$CONTEXT_STAGE/schemas/daily_integrator_action_v1.json" >/dev/null \
    && [ -x "$CONTEXT_STAGE/run_self_reflection_once.sh" ] \
    && [ -x "$CONTEXT_STAGE/run_remember_agent_v1_once.sh" ] \
    && [ -x "$CONTEXT_STAGE/run_cognitive_record_once.sh" ] \
    && bash -n "$CONTEXT_STAGE/run_self_reflection_once.sh" \
    && bash -n "$CONTEXT_STAGE/run_remember_agent_v1_once.sh" \
    && bash -n "$CONTEXT_STAGE/run_cognitive_record_once.sh" \
    && memento_context_runner_uses_python "$CONTEXT_STAGE/run_self_reflection_once.sh" \
    && memento_context_runner_uses_python "$CONTEXT_STAGE/run_remember_agent_v1_once.sh" \
    && memento_context_runner_uses_python "$CONTEXT_STAGE/run_cognitive_record_once.sh" \
    && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$CONTEXT_STAGE" "$PYTHON3_RUNTIME" -c \
      'import agent_v1, context_agent, cognitive_actions_v1, cognitive_agent_adapter_v1, cognitive_bundle_store_v1, cognitive_daily_review_v1, cognitive_day_orchestrator_v1, cognitive_migration_v1, cognitive_manual_request_v1, cognitive_pipeline_v1, cognitive_projection_v1, cognitive_prompts_v1, cognitive_record_worker_v1, cognitive_runtime_v1, cognitive_schedule_v1, cognitive_store_v1, cognitive_v1' \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      self-reflection-worker --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      agent-status --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      agent-enable --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      agent-disable --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      agent-request --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      agent-run --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      agent-worker --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      cognitive-action-worker --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      daily-manual-worker --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      daily-schedule-tick --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      daily-schedule-status --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      daily-schedule-enable --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      daily-schedule-disable --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      record-ingest --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      record-worker --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      daily-run --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      projection-rebuild --help >/dev/null \
    && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_STAGE/context_agent.py" \
      agent-profile --help >/dev/null; then
    CONTEXT_STAGE_READY=1
  fi
  if [ "$CONTEXT_STAGE_READY" = "1" ] \
    && memento_validate_context_tree \
    && atomic_replace_directory "$CONTEXT_STAGE" "$CONTEXT_RUNTIME_DEST"; then
    if ! memento_plain_owned_directory "$CONTEXT_RUNTIME_DEST"; then
      echo -e "${YELLOW}  ⚠ Context Agent 运行时替换后安全校验失败${NC}"
      HAS_CONTEXT_AGENT=0
    else
    chmod 700 \
      "$CONTEXT_RUNTIME_DEST/context_agent.py" \
      "$CONTEXT_RUNTIME_DEST/run_self_reflection_once.sh" \
      "$CONTEXT_RUNTIME_DEST/run_remember_agent_v1_once.sh" \
      "$CONTEXT_RUNTIME_DEST/run_cognitive_record_once.sh"
    echo -e "${GREEN}  ✓ Context Agent 运行时已复制到 $CONTEXT_RUNTIME_DEST${NC}"
    HAS_CONTEXT_AGENT=1
    fi
  else
    rm -rf "$CONTEXT_STAGE"
    echo -e "${YELLOW}  ⚠ Context Agent 校验或安装失败，已保留原版本与用户状态${NC}"
    COGNITIVE_RUNTIME_DEST_READY=1
    for COGNITIVE_RUNTIME_FILE in "${COGNITIVE_RUNTIME_FILES[@]}"; do
      if [ ! -f "$CONTEXT_RUNTIME_DEST/$COGNITIVE_RUNTIME_FILE" ] \
        || [ -L "$CONTEXT_RUNTIME_DEST/$COGNITIVE_RUNTIME_FILE" ] \
        || ! PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" -c \
          'import ast,pathlib,sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))' \
          "$CONTEXT_RUNTIME_DEST/$COGNITIVE_RUNTIME_FILE"; then
        COGNITIVE_RUNTIME_DEST_READY=0
      fi
    done
    if [ "$COGNITIVE_RUNTIME_DEST_READY" = "1" ] \
      && memento_validate_context_tree \
      && memento_plain_owned_directory "$CONTEXT_RUNTIME_DEST" \
      && [ -f "$CONTEXT_RUNTIME_DEST/context_agent.py" ] \
      && [ -f "$CONTEXT_RUNTIME_DEST/agent_v1.py" ] \
      && [ -f "$CONTEXT_RUNTIME_DEST/schemas/record_interpreter_action_v1.json" ] \
      && [ ! -L "$CONTEXT_RUNTIME_DEST/schemas/record_interpreter_action_v1.json" ] \
      && [ -f "$CONTEXT_RUNTIME_DEST/schemas/daily_integrator_action_v1.json" ] \
      && [ ! -L "$CONTEXT_RUNTIME_DEST/schemas/daily_integrator_action_v1.json" ] \
      && "$PYTHON3_RUNTIME" -m json.tool \
        "$CONTEXT_RUNTIME_DEST/schemas/record_interpreter_action_v1.json" >/dev/null 2>&1 \
      && "$PYTHON3_RUNTIME" -m json.tool \
        "$CONTEXT_RUNTIME_DEST/schemas/daily_integrator_action_v1.json" >/dev/null 2>&1 \
      && [ -x "$CONTEXT_RUNTIME_DEST/run_self_reflection_once.sh" ] \
      && [ -x "$CONTEXT_RUNTIME_DEST/run_remember_agent_v1_once.sh" ] \
      && [ -x "$CONTEXT_RUNTIME_DEST/run_cognitive_record_once.sh" ] \
      && memento_context_runner_uses_python "$CONTEXT_RUNTIME_DEST/run_self_reflection_once.sh" \
      && memento_context_runner_uses_python "$CONTEXT_RUNTIME_DEST/run_remember_agent_v1_once.sh" \
      && memento_context_runner_uses_python "$CONTEXT_RUNTIME_DEST/run_cognitive_record_once.sh" \
      && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$CONTEXT_RUNTIME_DEST" "$PYTHON3_RUNTIME" -c \
        'import agent_v1, context_agent, cognitive_actions_v1, cognitive_agent_adapter_v1, cognitive_bundle_store_v1, cognitive_daily_review_v1, cognitive_day_orchestrator_v1, cognitive_migration_v1, cognitive_manual_request_v1, cognitive_pipeline_v1, cognitive_projection_v1, cognitive_prompts_v1, cognitive_record_worker_v1, cognitive_runtime_v1, cognitive_schedule_v1, cognitive_store_v1, cognitive_v1' \
      && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_RUNTIME_DEST/context_agent.py" \
        agent-worker --help >/dev/null 2>&1 \
      && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_RUNTIME_DEST/context_agent.py" \
        cognitive-action-worker --help >/dev/null 2>&1 \
      && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_RUNTIME_DEST/context_agent.py" \
        daily-manual-worker --help >/dev/null 2>&1 \
      && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_RUNTIME_DEST/context_agent.py" \
        daily-schedule-tick --help >/dev/null 2>&1 \
      && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_RUNTIME_DEST/context_agent.py" \
        daily-schedule-status --help >/dev/null 2>&1 \
      && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_RUNTIME_DEST/context_agent.py" \
        daily-schedule-enable --help >/dev/null 2>&1 \
      && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_RUNTIME_DEST/context_agent.py" \
        daily-schedule-disable --help >/dev/null 2>&1 \
      && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_RUNTIME_DEST/context_agent.py" \
        record-ingest --help >/dev/null 2>&1 \
      && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_RUNTIME_DEST/context_agent.py" \
        record-worker --help >/dev/null 2>&1 \
      && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_RUNTIME_DEST/context_agent.py" \
        daily-run --help >/dev/null 2>&1 \
      && PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_RUNTIME_DEST/context_agent.py" \
        projection-rebuild --help >/dev/null 2>&1; then
      HAS_CONTEXT_AGENT=1
    fi
  fi
elif [ ! -d "$CONTEXT_SRC" ]; then
  echo -e "${YELLOW}  ⚠ 安装包内未找到 context-agent/,跳过 Context Agent${NC}"
else
  echo -e "${YELLOW}  ⚠ Context Agent 路径安全校验失败，已跳过运行时安装${NC}"
fi

AGENT_V1_PROFILE="$CONTEXT_ROOT/agent-v1/profile.json"
if [ "$HAS_CONTEXT_AGENT" = "1" ] \
  && [ "$INSTALL_BACKGROUND_AUTOMATION" = "1" ]; then
  if ! memento_validate_context_tree \
    || ! memento_context_regular_file_safe "$AGENT_V1_PROFILE"; then
    echo -e "${YELLOW}  ⚠ Agent V1 profile.json 不是当前用户拥有的安全普通文件，已保留且不会启动 Worker${NC}"
    HAS_CONTEXT_AGENT=0
  elif [ ! -e "$AGENT_V1_PROFILE" ]; then
    if ! PYTHONDONTWRITEBYTECODE=1 "$PYTHON3_RUNTIME" "$CONTEXT_RUNTIME_DEST/context_agent.py" \
      agent-profile --vault "$SECRETARY_DIR" >/dev/null; then
      echo -e "${YELLOW}  ⚠ Agent V1 初始 profile 创建失败，运行时已安装但不会启动 Worker${NC}"
      HAS_CONTEXT_AGENT=0
    fi
  fi
  if [ "$HAS_CONTEXT_AGENT" = "1" ] \
    && { ! memento_context_regular_file_safe "$AGENT_V1_PROFILE" \
      || ! python3 -m json.tool "$AGENT_V1_PROFILE" >/dev/null; }; then
    echo -e "${YELLOW}  ⚠ Agent V1 profile.json 无法校验，已保留且不会启动 Worker${NC}"
    HAS_CONTEXT_AGENT=0
  fi
fi

CONTEXT_WORKER_PLIST="$HOME/Library/LaunchAgents/com.memento.context-agent.plist"
REMEMBER_AGENT_PLIST="$HOME/Library/LaunchAgents/com.memento.remember-agent-v1.plist"
CONTEXT_STDOUT_LOG="$CONTEXT_ROOT/logs/context-workers.stdout.log"
CONTEXT_STDERR_LOG="$CONTEXT_ROOT/logs/context-workers.stderr.log"
REMEMBER_AGENT_STDOUT_LOG="$CONTEXT_ROOT/logs/remember-agent-v1.stdout.log"
REMEMBER_AGENT_STDERR_LOG="$CONTEXT_ROOT/logs/remember-agent-v1.stderr.log"
HAS_CONTEXT_WORKER=0
HAS_REMEMBER_AGENT_WORKER=0
if [ "$HAS_CONTEXT_AGENT" = "1" ] \
  && [ "$INSTALL_BACKGROUND_AUTOMATION" = "1" ]; then
  if ! memento_validate_context_tree \
    || ! memento_context_safe_log_file "$CONTEXT_STDOUT_LOG" \
    || ! memento_context_safe_log_file "$CONTEXT_STDERR_LOG" \
    || ! memento_context_safe_log_file "$REMEMBER_AGENT_STDOUT_LOG" \
    || ! memento_context_safe_log_file "$REMEMBER_AGENT_STDERR_LOG"; then
    echo -e "${YELLOW}  ⚠ Context Agent 日志路径不安全，已拒绝启动 Worker${NC}"
    HAS_CONTEXT_AGENT=0
  fi
fi

if [ "$HAS_CONTEXT_AGENT" != "1" ]; then
  memento_disable_owned_context_workers
fi

if [ "$HAS_CONTEXT_AGENT" = "1" ] \
  && [ "$INSTALL_BACKGROUND_AUTOMATION" = "1" ]; then
  mkdir -p "$HOME/Library/LaunchAgents"
  if { [ -e "$CONTEXT_WORKER_PLIST" ] || [ -L "$CONTEXT_WORKER_PLIST" ]; } \
    && ! memento_context_worker_owned "$CONTEXT_WORKER_PLIST"; then
    echo -e "${YELLOW}  ⚠ 保留无法确认归属的同名 Context Worker: $CONTEXT_WORKER_PLIST${NC}"
  else
    CONTEXT_WORKER_PLIST_STAGE=$(mktemp \
      "$HOME/Library/LaunchAgents/.com.memento.context-agent.plist.XXXXXX")
    if "$PYTHON3_RUNTIME" - \
      "$CONTEXT_WORKER_PLIST_STAGE" \
      "$CONTEXT_RUNTIME_DEST/run_self_reflection_once.sh" \
      "$SECRETARY_DIR" \
      "$CONTEXT_ROOT/self-queries/requests" \
      "$CONTEXT_STDOUT_LOG" \
      "$CONTEXT_STDERR_LOG" <<'PY' &&
import plistlib
import sys
from pathlib import Path

output, runner, vault, self_watch, stdout_path, stderr_path = sys.argv[1:]
payload = {
    "Label": "com.memento.context-agent",
    "MementoManaged": "com.memento.context-agent.v1",
    "ProgramArguments": [
        "/bin/bash",
        runner,
        vault,
    ],
    "WatchPaths": [self_watch],
    "RunAtLoad": True,
    "KeepAlive": {"SuccessfulExit": False},
    "ProcessType": "Background",
    "ThrottleInterval": 1,
    "StandardOutPath": stdout_path,
    "StandardErrorPath": stderr_path,
}
with Path(output).open("wb") as handle:
    plistlib.dump(payload, handle, fmt=plistlib.FMT_XML, sort_keys=True)
PY
      plutil -lint "$CONTEXT_WORKER_PLIST_STAGE" >/dev/null \
      && chmod 600 "$CONTEXT_WORKER_PLIST_STAGE" \
      && memento_validate_context_tree \
      && memento_context_safe_log_file "$CONTEXT_STDOUT_LOG" \
      && memento_context_safe_log_file "$CONTEXT_STDERR_LOG"; then
      if [ -f "$CONTEXT_WORKER_PLIST" ] \
        && [ "${MEMENTO_SKIP_CONTEXT_WORKER_LOAD:-0}" != "1" ]; then
        launchctl bootout "gui/$(id -u)" "$CONTEXT_WORKER_PLIST" 2>/dev/null \
          || launchctl unload "$CONTEXT_WORKER_PLIST" 2>/dev/null \
          || true
      fi
      mv "$CONTEXT_WORKER_PLIST_STAGE" "$CONTEXT_WORKER_PLIST"
      CONTEXT_WORKER_PLIST_STAGE=""
      if find \
        "$CONTEXT_ROOT/self-queries/requests" \
        -maxdepth 1 -type f -name '*.json' -print -quit 2>/dev/null | grep -q .; then
        echo -e "${YELLOW}  ⚠ 检测到已有旧 Self Reflection 请求；Worker 载入后会扫描并只继续尚未完成项${NC}"
      fi
      if [ "${MEMENTO_SKIP_CONTEXT_WORKER_LOAD:-0}" = "1" ]; then
        HAS_CONTEXT_WORKER=1
      elif launchctl bootstrap "gui/$(id -u)" "$CONTEXT_WORKER_PLIST" 2>/dev/null \
        || launchctl load "$CONTEXT_WORKER_PLIST" 2>/dev/null; then
        HAS_CONTEXT_WORKER=1
      else
        echo -e "${YELLOW}  ⚠ Context Worker 文件已安装，但本次未能载入；可重新登录后再试${NC}"
      fi
      if [ "$HAS_CONTEXT_WORKER" = "1" ]; then
        echo -e "${GREEN}  ✓ 旧 Self Reflection Worker 已安装（Agent V1 RC 默认关闭）${NC}"
      fi
    else
      rm -f "$CONTEXT_WORKER_PLIST_STAGE"
      CONTEXT_WORKER_PLIST_STAGE=""
      echo -e "${YELLOW}  ⚠ Context Worker 安装失败；仍可用 CLI 手动运行${NC}"
    fi
  fi
fi

# Re:member Agent V1 与旧 Self Reflection 使用独立 job。它只监听明确的
# Agent 请求/用户动作目录，不在安装、登录、间隔或异常退出时自启。
# 即使事件到达，runner 也会再独立校验 enabled gate 并 fail closed。
if [ "$HAS_CONTEXT_AGENT" = "1" ] \
  && [ "$INSTALL_BACKGROUND_AUTOMATION" = "1" ]; then
  if { [ -e "$REMEMBER_AGENT_PLIST" ] || [ -L "$REMEMBER_AGENT_PLIST" ]; } \
    && ! memento_remember_agent_worker_owned "$REMEMBER_AGENT_PLIST"; then
    echo -e "${YELLOW}  ⚠ 保留无法确认归属的同名 Re:member Agent Worker: $REMEMBER_AGENT_PLIST${NC}"
  else
    REMEMBER_AGENT_PLIST_STAGE=$(mktemp \
      "$HOME/Library/LaunchAgents/.com.memento.remember-agent-v1.plist.XXXXXX")
    if "$PYTHON3_RUNTIME" - \
      "$REMEMBER_AGENT_PLIST_STAGE" \
      "$CONTEXT_RUNTIME_DEST/run_remember_agent_v1_once.sh" \
      "$SECRETARY_DIR" \
      "$CONTEXT_ROOT/agent-v1/requests" \
      "$CONTEXT_ROOT/agent-v1/user-actions" \
      "$CONTEXT_ROOT/cognitive-secretary-v1/user-actions" \
      "$REMEMBER_AGENT_STDOUT_LOG" \
      "$REMEMBER_AGENT_STDERR_LOG" <<'PY' &&
import plistlib
import sys
from pathlib import Path

(
    output,
    runner,
    vault,
    request_watch,
    action_watch,
    cognitive_action_watch,
    stdout_path,
    stderr_path,
) = sys.argv[1:]
payload = {
    "Label": "com.memento.remember-agent-v1",
    "MementoManaged": "com.memento.remember-agent-v1.v1",
    "ProgramArguments": [
        "/bin/bash",
        runner,
        vault,
    ],
    "WatchPaths": [
        request_watch,
        action_watch,
        cognitive_action_watch,
    ],
    "ProcessType": "Background",
    "ThrottleInterval": 1,
    "StandardOutPath": stdout_path,
    "StandardErrorPath": stderr_path,
}
with Path(output).open("wb") as handle:
    plistlib.dump(payload, handle, fmt=plistlib.FMT_XML, sort_keys=True)
PY
      plutil -lint "$REMEMBER_AGENT_PLIST_STAGE" >/dev/null \
      && chmod 600 "$REMEMBER_AGENT_PLIST_STAGE" \
      && memento_validate_context_tree \
      && memento_context_safe_log_file "$REMEMBER_AGENT_STDOUT_LOG" \
      && memento_context_safe_log_file "$REMEMBER_AGENT_STDERR_LOG"; then
      if [ -f "$REMEMBER_AGENT_PLIST" ] \
        && [ "${MEMENTO_SKIP_CONTEXT_WORKER_LOAD:-0}" != "1" ] \
        && [ "${MEMENTO_SKIP_REMEMBER_AGENT_LOAD:-0}" != "1" ]; then
        launchctl bootout "gui/$(id -u)" "$REMEMBER_AGENT_PLIST" 2>/dev/null \
          || launchctl unload "$REMEMBER_AGENT_PLIST" 2>/dev/null \
          || true
      fi
      mv "$REMEMBER_AGENT_PLIST_STAGE" "$REMEMBER_AGENT_PLIST"
      REMEMBER_AGENT_PLIST_STAGE=""
      if [ "${MEMENTO_SKIP_CONTEXT_WORKER_LOAD:-0}" = "1" ] \
        || [ "${MEMENTO_SKIP_REMEMBER_AGENT_LOAD:-0}" = "1" ]; then
        HAS_REMEMBER_AGENT_WORKER=1
      elif launchctl bootstrap "gui/$(id -u)" "$REMEMBER_AGENT_PLIST" 2>/dev/null \
        || launchctl load "$REMEMBER_AGENT_PLIST" 2>/dev/null; then
        HAS_REMEMBER_AGENT_WORKER=1
      else
        echo -e "${YELLOW}  ⚠ Re:member Agent 事件 Worker 文件已安装，但本次未能载入；可重新登录后再试${NC}"
      fi
      if [ "$HAS_REMEMBER_AGENT_WORKER" = "1" ]; then
        echo -e "${GREEN}  ✓ Re:member Agent V1 事件 Worker 已安装（无 RunAtLoad / KeepAlive / 定时，gate 默认不存在）${NC}"
      fi
    else
      rm -f "$REMEMBER_AGENT_PLIST_STAGE"
      REMEMBER_AGENT_PLIST_STAGE=""
      echo -e "${YELLOW}  ⚠ Re:member Agent 事件 Worker 安装失败；未修改用户的 Agent 状态或启用 gate${NC}"
    fi
  fi
fi

tighten_vault_permissions

echo ""
echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         ✓ 安装完成!                    ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BLUE}Obsidian Vault:${NC} ~/AISecretary"
echo -e "${BLUE}脚本目录:${NC} ~/AISecretary/.scripts"
echo -e "${BLUE}Vault 首页:${NC} ~/AISecretary/Memento.md"
if [ "$HAS_CONTEXT_AGENT" = "1" ]; then
  echo -e "${BLUE}Context Agent:${NC} ~/AISecretary/.context-agent/runtime"
fi
if [ "$HAS_CONTEXT_WORKER" = "1" ]; then
  echo -e "${BLUE}Self Reflection:${NC} 独立 Worker 继续按需处理"
fi
if [ "$HAS_REMEMBER_AGENT_WORKER" = "1" ]; then
  echo -e "${BLUE}Re:member:${NC} Agent V1 RC 事件 Worker 已安装；新安装默认未启用"
fi
echo -e "${BLUE}已装服务:${NC}"
echo "  - 存入 AI 秘书           (选中文字 → 直接存入)"
echo "  - 存入 AI 秘书 (选标签)   (选中文字 → 三选一标签 → 存入)"
echo "  - 存入 AI 秘书 (加备注)   (选中文字 → 输入备注 → 存入)"
echo "  - 存入 AI 秘书 (截图)     (调系统截图 → OCR → 存入)"
if [ "$VOICE_APP_READY" = "1" ]; then
  echo "  - 存入 AI 秘书 (语音)     (本地录音 → Apple 转写 → 存入)"
fi
echo ""
echo -e "${YELLOW}━━━ 首次安装: 确认文本/截图快捷键 ━━━${NC}"
echo "  打开: 系统设置 → 键盘 → 键盘快捷键 → 服务"
echo "  在「常规」分类下:  存入 AI 秘书 / (选标签) / (加备注) / (截图)"
echo "  推荐组合: ⌃1 / ⌃2 / ⌃3 / ⌃4"
echo "  语音不默认绑定快捷键;可从 Services 菜单调用,部分非原生 App 不支持该入口。"
echo ""
echo -e "${BLUE}━━━ 测试 ━━━${NC}"
echo "  ~/AISecretary/.scripts/append_text.sh \"hello Memento\""
echo ""
echo -e "${BLUE}━━━ 在 Obsidian 中打开 ━━━${NC}"
echo "  open -a Obsidian ~/AISecretary"
echo "  Obsidian 不运行时也能正常记录;下次打开会自动同步文件变化。"
echo ""
echo -e "${BLUE}━━━ 喂给 AI ━━━${NC}"
echo "  方式 1 (推荐): 把整个 ~/AISecretary 文件夹拖给 Claude/ChatGPT"
echo "  方式 2:        ~/AISecretary/.scripts/copy_today.sh 后 ⌘V 粘贴"
echo "  方式 3:        在 Obsidian 打开 Memento.md / Memento.base"
echo ""

if [ "$HAS_NEWTAB" = "1" ]; then
  echo -e "${YELLOW}━━━ 可选: 启用 Chrome 新标签页 Dashboard ━━━${NC}"
  echo "  让 Chrome 新标签变成 Memento 记录面板,回看今天与过去 90 天留下的内容。"
  echo ""
  echo "  1. Chrome 地址栏访问 chrome://extensions"
  echo "  2. 右上角打开 [开发者模式]"
  echo "  3. [加载已解压的扩展程序],选择目录:"
  echo -e "       ${BLUE}$NEWTAB_DEST${NC}"
  echo "  4. 开新标签 → 点 [授权数据目录]"
  echo ""
  read -p "现在帮你打开 chrome://extensions 吗? [y/N] " -n 1 -r
  echo ""
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    open -a "Google Chrome" "chrome://extensions" 2>/dev/null \
      || echo -e "${YELLOW}  ⚠ Chrome 没装或打开失败,请手动访问 chrome://extensions${NC}"
  fi
  echo ""
fi

echo -e "${YELLOW}如需卸载: ./uninstall_aisecretary.sh${NC}"
echo ""
