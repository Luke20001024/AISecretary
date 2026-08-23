#!/bin/bash
# ============================================================
# Memento 卸载脚本 (v7 · 每日第一帧)
# 作用: 移除所有由 install_aisecretary.sh v7 安装的组件
#       同时兼容清理 v1 残留 (单 workflow + LaunchAgent)
# 注意: 默认保留你的 Obsidian Vault (~/AISecretary),除非明确选择删除
# ============================================================

set -e
set -o pipefail
umask 077

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

INSTALL_LOCK="$HOME/.memento-install.lock"
INSTALL_LOCK_TOKEN="$$-${RANDOM}-$(date +%s)"

release_install_lock() {
  local current_token
  local quarantine
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

memento_workflow_owned() {
  local workflow="$1"
  local marker="$workflow/Contents/.memento-managed"
  local document="$workflow/Contents/document.wflow"
  [ -f "$marker" ] && grep -q '^com.memento.workflow.v1$' "$marker" && return 0
  [ -f "$document" ] && grep -Eq 'AISecretary/\.(scripts|apps)/|Memento Voice Capture\.app' "$document"
}

memento_legacy_launchagent_owned() {
  local plist="$1"
  local label
  [ -f "$plist" ] || return 1
  label=$(plutil -extract Label raw -o - "$plist" 2>/dev/null || true)
  [ "$label" = 'com.aisecretary.screenshot' ] || return 1
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

memento_remove_agent_control_leaf() {
  local leaf_name="$1"
  local control_label="$2"
  local success_message="$3"
  local python_runtime='/usr/bin/python3'
  local result
  local status

  case "$leaf_name" in
    enabled|schedule.json) ;;
    *) return 1 ;;
  esac

  # 卸载执行组件后必须同步关闭启用状态。父链逐级用
  # O_NOFOLLOW 打开并核对当前 uid；随后只 unlink agent-v1 目录中
  # 明确命名的控制叶子，不跟随符号链接或破坏硬链接的其他名字。
  if [ ! -x "$python_runtime" ]; then
    echo -e "${YELLOW}  ⚠ 缺少可信的 /usr/bin/python3；无法安全移除 $control_label，已保留${NC}"
    return 0
  fi

  set +e
  result=$(
    "$python_runtime" - "$HOME" "$(id -u)" "$leaf_name" <<'PY'
import os
import stat
import sys

home = sys.argv[1]
uid = int(sys.argv[2])
leaf_name = sys.argv[3]
directory_flags = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
try:
    home_metadata = os.lstat(home)
    if not stat.S_ISDIR(home_metadata.st_mode) or home_metadata.st_uid != uid:
        print("unsafe-parent")
        raise SystemExit(4)
    current_fd = os.open(home, directory_flags)
except OSError:
    print("unsafe-parent")
    raise SystemExit(4)

try:
    for component in ("AISecretary", ".context-agent", "agent-v1"):
        try:
            child_fd = os.open(component, directory_flags, dir_fd=current_fd)
        except FileNotFoundError:
            print("absent")
            raise SystemExit(0)
        except OSError:
            print("unsafe-parent")
            raise SystemExit(4)
        child_metadata = os.fstat(child_fd)
        if not stat.S_ISDIR(child_metadata.st_mode) or child_metadata.st_uid != uid:
            os.close(child_fd)
            print("unsafe-parent")
            raise SystemExit(4)
        os.close(current_fd)
        current_fd = child_fd

    try:
        leaf = os.stat(leaf_name, dir_fd=current_fd, follow_symlinks=False)
    except FileNotFoundError:
        print("absent")
        raise SystemExit(0)
    if stat.S_ISDIR(leaf.st_mode):
        print("unsafe-leaf")
        raise SystemExit(3)
    try:
        # unlinkat 删除的是目录项本身：符号链接不会被跟随，硬链接的
        # 其他名字和内容也不会被删除。
        os.unlink(leaf_name, dir_fd=current_fd)
    except (FileNotFoundError, IsADirectoryError):
        print("unsafe-leaf")
        raise SystemExit(3)
    print("removed")
finally:
    try:
        os.close(current_fd)
    except OSError:
        pass
PY
  )
  status=$?
  set -e

  case "$result:$status" in
    removed:0)
      echo -e "${GREEN}  ✓ $success_message${NC}"
      ;;
    absent:0)
      ;;
    *)
      echo -e "${YELLOW}  ⚠ $control_label 父路径不安全或叶子是目录；已保留且不会跟随链接${NC}"
      ;;
  esac
}

memento_remove_agent_gate_leaf() {
  memento_remove_agent_control_leaf \
    'enabled' \
    'Agent V1 enabled gate' \
    '已安全关闭 Agent V1 enabled gate'
}

memento_remove_agent_schedule_leaf() {
  memento_remove_agent_control_leaf \
    'schedule.json' \
    'Re:member 每日自动整理配置' \
    '已安全关闭 Re:member 每日自动整理配置'
}

acquire_install_lock

echo ""
echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║       Memento 卸载程序                 ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""

# 1. 卸载截图监听 LaunchAgent (v1 残留)
PLIST="$HOME/Library/LaunchAgents/com.aisecretary.screenshot.plist"
if [ -f "$PLIST" ]; then
  if memento_legacy_launchagent_owned "$PLIST"; then
    echo -e "${BLUE}→ 停止截图监听服务 (v1 残留)...${NC}"
    if [ "${MEMENTO_SKIP_LEGACY_LAUNCHAGENT_UNLOAD:-0}" != '1' ]; then
      launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null \
        || launchctl unload "$PLIST" 2>/dev/null \
        || true
    fi
    rm -f "$PLIST"
    echo -e "${GREEN}  ✓ 已停止${NC}"
  else
    echo -e "${YELLOW}  ⚠ 已保留无法确认归属的同名 LaunchAgent: $PLIST${NC}"
  fi
fi

# 2. 移除 Workflow
echo -e "${BLUE}→ 移除右键菜单服务...${NC}"
REMOVED=0
SERVICES_DIR="$HOME/Library/Services"
for NAME in \
  "存入 AI 秘书" \
  "存入 AI 秘书 (选标签)" \
  "存入 AI 秘书 (加备注)" \
  "存入 AI 秘书 (截图)" \
  "存入 AI 秘书 (语音)" \
  "存入 AI 秘书 (语音+截图)"; do
  WF="$SERVICES_DIR/$NAME.workflow"
  if [ -d "$WF" ]; then
    if memento_workflow_owned "$WF"; then
      rm -rf "$WF"
      echo -e "${GREEN}  ✓ $NAME${NC}"
      REMOVED=$((REMOVED+1))
    else
      echo -e "${YELLOW}  ⚠ 已保留非 Memento 管理的同名 Service: $NAME${NC}"
    fi
  fi
done

# 同时清理早期版本使用的「AI秘书·速记 / 标签 / 备注 / 截图」等入口。
# 这里只删除 Workflow；~/AISecretary 中的历史记录仍按下方默认策略保留。
shopt -s nullglob
for WF in "$SERVICES_DIR/AI秘书.workflow" "$SERVICES_DIR"/AI秘书·*.workflow; do
  [ -d "$WF" ] || continue
  NAME=$(basename "$WF" .workflow)
  if memento_workflow_owned "$WF"; then
    rm -rf "$WF"
    echo -e "${GREEN}  ✓ $NAME (旧版入口)${NC}"
    REMOVED=$((REMOVED+1))
  else
    echo -e "${YELLOW}  ⚠ 已保留非 Memento 管理的旧前缀 Service: $NAME${NC}"
  fi
done
shopt -u nullglob

if [ "$REMOVED" -gt 0 ]; then
  echo -e "${YELLOW}  注意: 系统设置里的快捷键绑定可能需要手动清理${NC}"
  echo -e "${YELLOW}        (位置: 系统设置 → 键盘 → 键盘快捷键 → 服务)${NC}"
  if [ "${MEMENTO_SKIP_SERVICE_REFRESH:-0}" != "1" ]; then
    /System/Library/CoreServices/pbs -update 2>/dev/null || true
  fi
else
  echo -e "${BLUE}  (没有找到任何已安装的 workflow)${NC}"
fi

# 3. 清理通用选区助手
SELECTION_COPY_APP="$HOME/AISecretary/.apps/Memento Selection Copy.app"

if [ -d "$SELECTION_COPY_APP" ]; then
  echo ""
  echo -e "${BLUE}→ 清理通用选区助手...${NC}"
  pkill -f "$SELECTION_COPY_APP/Contents/MacOS/MementoSelectionCopy" 2>/dev/null || true
  rm -rf "$SELECTION_COPY_APP"
  tccutil reset Accessibility com.memento.selection-copy >/dev/null 2>&1 || true
  echo -e "${GREEN}  ✓ 已删除通用选区助手${NC}"
fi

# 4. 清理本地语音捕获应用
VOICE_APP="$HOME/AISecretary/.apps/Memento Voice Capture.app"

if [ -d "$VOICE_APP" ]; then
  echo ""
  echo -e "${BLUE}→ 清理本地语音捕获应用...${NC}"
  pkill -f "$VOICE_APP/Contents/MacOS/MementoVoiceCapture" 2>/dev/null || true
  rm -rf "$VOICE_APP"
  echo -e "${GREEN}  ✓ 已删除语音捕获应用${NC}"
fi

# 4. 清理每日第一帧应用 (已拍照片和每日状态随 Vault 默认保留)
SNAPSHOT_APP="$HOME/AISecretary/.apps/Memento Daily Snapshot.app"

if [ -d "$SNAPSHOT_APP" ]; then
  echo ""
  echo -e "${BLUE}→ 清理每日第一帧应用...${NC}"
  pkill -f "$SNAPSHOT_APP/Contents/MacOS/MementoDailySnapshot" 2>/dev/null || true
  rm -rf "$SNAPSHOT_APP"
  echo -e "${GREEN}  ✓ 已删除每日第一帧应用${NC}"
fi

# 5. 清理 Chrome 新标签页 Dashboard 资源
NEWTAB_DIR="$HOME/AISecretary/.chrome-newtab"
if [ -d "$NEWTAB_DIR" ]; then
  echo ""
  echo -e "${BLUE}→ 清理 Chrome dashboard 资源...${NC}"
  rm -rf "$NEWTAB_DIR"
  echo -e "${GREEN}  ✓ 已删除 $NEWTAB_DIR${NC}"
  echo -e "${YELLOW}  ⚠ 别忘了去 chrome://extensions 手动移除 'Memento' 扩展${NC}"
  echo -e "${YELLOW}    (Chrome 不允许脚本卸载扩展,只能你点 [移除] 按钮)${NC}"
fi

# 6. 停止两个按需 Worker 与独立每日调度；只删除明确由 Memento 管理的同名 LaunchAgent。
CONTEXT_WORKER_PLIST="$HOME/Library/LaunchAgents/com.memento.context-agent.plist"
if [ -e "$CONTEXT_WORKER_PLIST" ] || [ -L "$CONTEXT_WORKER_PLIST" ]; then
  if memento_context_worker_owned "$CONTEXT_WORKER_PLIST"; then
    echo ""
    echo -e "${BLUE}→ 停止按需理解 Worker...${NC}"
    if [ "${MEMENTO_SKIP_CONTEXT_WORKER_UNLOAD:-0}" != '1' ]; then
      launchctl bootout "gui/$(id -u)" "$CONTEXT_WORKER_PLIST" 2>/dev/null \
        || launchctl unload "$CONTEXT_WORKER_PLIST" 2>/dev/null \
        || true
    fi
    rm -f "$CONTEXT_WORKER_PLIST"
    echo -e "${GREEN}  ✓ 已停止并移除${NC}"
  else
    echo -e "${YELLOW}  ⚠ 已保留无法确认归属的同名 Context Worker: $CONTEXT_WORKER_PLIST${NC}"
  fi
fi

REMEMBER_AGENT_PLIST="$HOME/Library/LaunchAgents/com.memento.remember-agent-v1.plist"
if [ -e "$REMEMBER_AGENT_PLIST" ] || [ -L "$REMEMBER_AGENT_PLIST" ]; then
  if memento_remember_agent_worker_owned "$REMEMBER_AGENT_PLIST"; then
    echo ""
    echo -e "${BLUE}→ 停止 Re:member Agent V1 事件 Worker...${NC}"
    if [ "${MEMENTO_SKIP_CONTEXT_WORKER_UNLOAD:-0}" != '1' ] \
      && [ "${MEMENTO_SKIP_REMEMBER_AGENT_UNLOAD:-0}" != '1' ]; then
      launchctl bootout "gui/$(id -u)" "$REMEMBER_AGENT_PLIST" 2>/dev/null \
        || launchctl unload "$REMEMBER_AGENT_PLIST" 2>/dev/null \
        || true
    fi
    rm -f "$REMEMBER_AGENT_PLIST"
    echo -e "${GREEN}  ✓ 已停止并移除${NC}"
  else
    echo -e "${YELLOW}  ⚠ 已保留无法确认归属的同名 Re:member Agent Worker: $REMEMBER_AGENT_PLIST${NC}"
  fi
fi

REMEMBER_AGENT_SCHEDULE_PLIST="$HOME/Library/LaunchAgents/com.memento.remember-agent-v1-schedule.plist"
if [ -e "$REMEMBER_AGENT_SCHEDULE_PLIST" ] || [ -L "$REMEMBER_AGENT_SCHEDULE_PLIST" ]; then
  if memento_remember_agent_schedule_owned "$REMEMBER_AGENT_SCHEDULE_PLIST"; then
    echo ""
    echo -e "${BLUE}→ 停止 Re:member 每日调度...${NC}"
    if [ "${MEMENTO_SKIP_CONTEXT_WORKER_UNLOAD:-0}" != '1' ] \
      && [ "${MEMENTO_SKIP_REMEMBER_AGENT_SCHEDULE_UNLOAD:-0}" != '1' ]; then
      launchctl bootout "gui/$(id -u)" "$REMEMBER_AGENT_SCHEDULE_PLIST" 2>/dev/null \
        || launchctl unload "$REMEMBER_AGENT_SCHEDULE_PLIST" 2>/dev/null \
        || true
    fi
    rm -f "$REMEMBER_AGENT_SCHEDULE_PLIST"
    echo -e "${GREEN}  ✓ 已停止并移除${NC}"
  else
    echo -e "${YELLOW}  ⚠ 已保留无法确认归属的同名 Re:member 每日调度: $REMEMBER_AGENT_SCHEDULE_PLIST${NC}"
  fi
fi

# 执行组件不再存在时，总 gate 与自动整理配置也必须被安全移除，
# 避免重装后意外继承过期的启用状态。请求、记忆、用户操作、
# receipt、bundle、projection 与 profile 仍保留。
memento_remove_agent_gate_leaf
memento_remove_agent_schedule_leaf

# 7. 清理 Context Agent 运行时（只删除可执行代码，保留所有认知数据与日志）
CONTEXT_VAULT="$HOME/AISecretary"
CONTEXT_ROOT="$CONTEXT_VAULT/.context-agent"
CONTEXT_RUNTIME="$CONTEXT_ROOT/runtime"
CONTEXT_RUNTIME_SAFE=0
if [ -L "$CONTEXT_VAULT" ] \
  || { [ -e "$CONTEXT_VAULT" ] && ! memento_plain_owned_directory "$CONTEXT_VAULT"; }; then
  echo -e "${YELLOW}  ⚠ AISecretary 不是当前用户拥有的安全普通目录；Context Agent 运行时已保留${NC}"
elif [ -L "$CONTEXT_ROOT" ] \
  || { [ -e "$CONTEXT_ROOT" ] && ! memento_plain_owned_directory "$CONTEXT_ROOT"; }; then
  echo -e "${YELLOW}  ⚠ .context-agent 是符号链接、非目录或不属于当前用户；已保留且不会访问链接目标${NC}"
elif memento_plain_owned_directory "$CONTEXT_ROOT"; then
  if [ -L "$CONTEXT_RUNTIME" ] \
    || { [ -e "$CONTEXT_RUNTIME" ] && ! memento_plain_owned_directory "$CONTEXT_RUNTIME"; }; then
    echo -e "${YELLOW}  ⚠ Context Agent runtime 是符号链接、非目录或不属于当前用户；已保留且不会访问链接目标${NC}"
  elif memento_plain_owned_directory "$CONTEXT_RUNTIME"; then
    CONTEXT_RUNTIME_SAFE=1
  fi
fi
if [ "$CONTEXT_RUNTIME_SAFE" = "1" ]; then
  echo ""
  echo -e "${BLUE}→ 清理 Context Agent 运行时...${NC}"
  rm -rf "$CONTEXT_RUNTIME"
  echo -e "${GREEN}  ✓ 已删除 $CONTEXT_RUNTIME${NC}"
  echo -e "${BLUE}  → 认知 receipt/bundle/projection、Agent 请求/记忆/操作、Context 与日志已保留${NC}"
fi

# 8. 清理 Daily Review 执行协议 (保留已生成的 Reviews)
REVIEW_WORKER="$HOME/AISecretary/.review"
if [ -d "$REVIEW_WORKER" ]; then
  echo ""
  echo -e "${BLUE}→ 清理 Daily Review 执行协议...${NC}"
  rm -rf "$REVIEW_WORKER"
  echo -e "${GREEN}  ✓ 已删除 $REVIEW_WORKER${NC}"
  echo -e "${BLUE}  → 已生成的 ~/AISecretary/Reviews 已保留${NC}"
fi

# 9. 删除产品执行脚本；事实文件、资产和已生成 Review 仍保留。
SCRIPT_DIR="$HOME/AISecretary/.scripts"
if [ -d "$SCRIPT_DIR" ]; then
  echo ""
  echo -e "${BLUE}→ 清理 Memento 执行脚本...${NC}"
  rm -rf "$SCRIPT_DIR"
  echo -e "${GREEN}  ✓ 已删除 $SCRIPT_DIR${NC}"
fi

APPS_DIR="$HOME/AISecretary/.apps"
if [ -d "$APPS_DIR" ]; then
  rmdir "$APPS_DIR" 2>/dev/null || true
fi

# 10. Obsidian Vault 的处理 (默认保留)
SECRETARY_DIR="$HOME/AISecretary"
if [ -d "$SECRETARY_DIR" ]; then
  echo ""
  echo -e "${YELLOW}━━━ 你的 Obsidian Vault ━━━${NC}"
  echo "位置: $SECRETARY_DIR"
  COUNT=$(find "$SECRETARY_DIR" -name "*.md" -type f 2>/dev/null | wc -l | tr -d ' ')
  IMG=$(find "$SECRETARY_DIR/assets" -type f 2>/dev/null | wc -l | tr -d ' ' || true)
  echo "包含 $COUNT 个 Markdown 文件 + $IMG 张图片/截图"
  echo ""
  read -p "是否一并删除你的数据? [y/N] " -n 1 -r
  echo ""
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    read -p "再次确认: 删除后无法恢复,确定吗? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
      rm -rf "$SECRETARY_DIR"
      echo -e "${GREEN}  ✓ 数据已删除${NC}"
      echo -e "${YELLOW}  ⚠ Obsidian 若仍显示 Memento Vault,请在 Vault 列表中手动移除入口${NC}"
    else
      echo -e "${BLUE}  → 数据已保留: $SECRETARY_DIR${NC}"
    fi
  else
    echo -e "${BLUE}  → 数据已保留: $SECRETARY_DIR${NC}"
  fi
fi

echo ""
echo -e "${GREEN}✓ 卸载完成${NC}"
echo ""
