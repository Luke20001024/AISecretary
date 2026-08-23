#!/bin/bash

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/memento-snapshot-test.XXXXXX")
trap 'rm -rf "$TMP_ROOT"' EXIT

VAULT="$TMP_ROOT/vault"
SCRIPTS="$VAULT/.scripts"
mkdir -p "$SCRIPTS"

# 从安装器提取真正会被安装的统一环境脚本，避免测试一份复制品。
awk '
  /cat > "\$SCRIPT_DIR\/memento_env.sh" << '\''BASH_EOF'\''/ { copying = 1; next }
  copying && $0 == "BASH_EOF" { exit }
  copying { print }
' "$ROOT/install_aisecretary.sh" > "$SCRIPTS/memento_env.sh"
chmod +x "$SCRIPTS/memento_env.sh"

export MEMENTO_VAULT="$VAULT"
. "$SCRIPTS/memento_env.sh"

# 每日记录写锁：并发追加不能相互穿插或重复 frontmatter。
LOCK_DATE="2026-07-17"
for index in $(seq 1 20); do
  BLOCK="$TMP_ROOT/block-$index.md"
  printf '\n## 10:%02d · 周五\n\nentry-%02d\n\n---\n' "$index" "$index" > "$BLOCK"
  (memento_append_daily_block "$VAULT/$LOCK_DATE.md" "$LOCK_DATE" "$BLOCK") &
done
wait
[ "$(grep -c '^## ' "$VAULT/$LOCK_DATE.md")" = "20" ]
[ "$(grep -c '^type: memento-daily$' "$VAULT/$LOCK_DATE.md")" = "1" ]
for index in $(seq 1 20); do
  [ "$(grep -c "entry-$(printf '%02d' "$index")" "$VAULT/$LOCK_DATE.md")" = "1" ]
done
[ "$(stat -f %Lp "$VAULT")" = "700" ]
[ "$(stat -f %Lp "$VAULT/$LOCK_DATE.md")" = "600" ]

# stale lock 接管使用 token；旧 owner 释放时不能删除新 owner 的锁。
STALE_DATE="2026-07-18"
STALE_LOCK="$VAULT/.state/write-locks/$STALE_DATE.lock"
mkdir -p "$STALE_LOCK"
printf '%s\n' old-owner > "$STALE_LOCK/token"
printf '%s\n' 99999999 > "$STALE_LOCK/pid"
touch -t 200001010000 "$STALE_LOCK"
memento_acquire_daily_lock "$STALE_DATE"
NEW_LOCK="$MEMENTO_DAILY_LOCK_PATH"
NEW_TOKEN="$MEMENTO_DAILY_LOCK_TOKEN"
[ "$NEW_TOKEN" != "old-owner" ]
set +e
memento_release_daily_lock "$STALE_LOCK" old-owner
OLD_RELEASE_STATUS=$?
set -e
[ "$OLD_RELEASE_STATUS" -ne 0 ]
[ "$(cat "$NEW_LOCK/token")" = "$NEW_TOKEN" ]
memento_release_daily_lock "$NEW_LOCK" "$NEW_TOKEN"

# metadata 升级必须保留 mode、ACL、xattr，并拒绝 symlink。
UPGRADE_META_DATE="2026-07-19"
UPGRADE_META_FILE="$VAULT/$UPGRADE_META_DATE.md"
printf '%s\n' '---' "date: $UPGRADE_META_DATE" '---' '' 'legacy-body' > "$UPGRADE_META_FILE"
chmod 640 "$UPGRADE_META_FILE"
xattr -w com.memento.qa preserved "$UPGRADE_META_FILE"
chmod +a 'everyone allow read' "$UPGRADE_META_FILE"
ACL_BEFORE=$(ls -led "$UPGRADE_META_FILE" | tail -n +2)
memento_upgrade_daily_note "$UPGRADE_META_FILE" "$UPGRADE_META_DATE"
[ "$(stat -f %Lp "$UPGRADE_META_FILE")" = "640" ]
[ "$(xattr -p com.memento.qa "$UPGRADE_META_FILE")" = "preserved" ]
[ "$(ls -led "$UPGRADE_META_FILE" | tail -n +2)" = "$ACL_BEFORE" ]
[ "$(grep -c '^type: memento-daily$' "$UPGRADE_META_FILE")" = "1" ]

SYMLINK_DATE="2026-07-20"
SYMLINK_TARGET="$TMP_ROOT/symlink-target.md"
SYMLINK_DAILY="$VAULT/$SYMLINK_DATE.md"
printf '%s\n' '---' "date: $SYMLINK_DATE" '---' 'symlink-body' > "$SYMLINK_TARGET"
ln -s "$SYMLINK_TARGET" "$SYMLINK_DAILY"
set +e
memento_upgrade_daily_note "$SYMLINK_DAILY" "$SYMLINK_DATE"
SYMLINK_STATUS=$?
set -e
[ "$SYMLINK_STATUS" -ne 0 ]
[ -L "$SYMLINK_DAILY" ]
[ "$(grep -c '^type: memento-daily$' "$SYMLINK_TARGET" || true)" = "0" ]

# 升级和追加共用同一把锁：即使升级读完旧文件后暂停，并发条目也不能被 mv 覆盖。
UPGRADE_RACE_ROOT="$TMP_ROOT/upgrade-race"
UPGRADE_RACE_VAULT="$UPGRADE_RACE_ROOT/vault"
UPGRADE_RACE_BIN="$UPGRADE_RACE_ROOT/bin"
UPGRADE_SIGNAL="$UPGRADE_RACE_ROOT/awk-read"
mkdir -p "$UPGRADE_RACE_VAULT/.scripts" "$UPGRADE_RACE_BIN"
cp "$SCRIPTS/memento_env.sh" "$UPGRADE_RACE_VAULT/.scripts/memento_env.sh"
cat > "$UPGRADE_RACE_BIN/awk" <<'FAKE_AWK'
#!/bin/bash
out=$(mktemp)
/usr/bin/awk "$@" > "$out"
touch "${MEMENTO_TEST_AWK_SIGNAL:?}"
sleep 1
cat "$out"
rm -f "$out"
FAKE_AWK
chmod +x "$UPGRADE_RACE_BIN/awk"
RACE_DATE="2026-07-21"
RACE_FILE="$UPGRADE_RACE_VAULT/$RACE_DATE.md"
RACE_BLOCK="$UPGRADE_RACE_ROOT/block.md"
printf '%s\n' '---' "date: $RACE_DATE" '---' '' 'old-entry' > "$RACE_FILE"
printf '\n## 09:00 · 周二\n\nconcurrent-entry\n\n---\n' > "$RACE_BLOCK"
MEMENTO_VAULT="$UPGRADE_RACE_VAULT" MEMENTO_TEST_AWK_SIGNAL="$UPGRADE_SIGNAL" \
PATH="$UPGRADE_RACE_BIN:$PATH" bash -c \
  '. "$1"; memento_upgrade_daily_note "$2" "$3"' _ \
  "$UPGRADE_RACE_VAULT/.scripts/memento_env.sh" "$RACE_FILE" "$RACE_DATE" &
UPGRADE_PID=$!
for _ in $(seq 1 100); do
  [ -e "$UPGRADE_SIGNAL" ] && break
  sleep 0.01
done
MEMENTO_VAULT="$UPGRADE_RACE_VAULT" bash -c \
  '. "$1"; memento_append_daily_block "$2" "$3" "$4"' _ \
  "$UPGRADE_RACE_VAULT/.scripts/memento_env.sh" "$RACE_FILE" "$RACE_DATE" "$RACE_BLOCK" &
APPEND_PID=$!
wait "$UPGRADE_PID"
wait "$APPEND_PID"
grep -q '^type: memento-daily$' "$RACE_FILE"
grep -q 'concurrent-entry' "$RACE_FILE"

# temp append 中途失败时，正式每日文件必须保持字节级不变。
ROLLBACK_ROOT="$TMP_ROOT/append-rollback"
ROLLBACK_VAULT="$ROLLBACK_ROOT/vault"
ROLLBACK_BIN="$ROLLBACK_ROOT/bin"
mkdir -p "$ROLLBACK_VAULT/.scripts" "$ROLLBACK_BIN"
cp "$SCRIPTS/memento_env.sh" "$ROLLBACK_VAULT/.scripts/memento_env.sh"
ROLLBACK_DATE="2026-07-22"
ROLLBACK_FILE="$ROLLBACK_VAULT/$ROLLBACK_DATE.md"
ROLLBACK_BLOCK="$ROLLBACK_ROOT/failing-block.md"
printf '%s\n' '---' "date: $ROLLBACK_DATE" 'type: memento-daily' '---' 'stable-entry' > "$ROLLBACK_FILE"
printf '\n## 10:00 · 周三\n\nshould-not-commit\n\n---\n' > "$ROLLBACK_BLOCK"
ROLLBACK_HASH=$(shasum -a 256 "$ROLLBACK_FILE" | awk '{print $1}')
cat > "$ROLLBACK_BIN/cat" <<'FAKE_CAT'
#!/bin/bash
if [ "${1:-}" = "${MEMENTO_TEST_FAIL_BLOCK:-}" ]; then
  /usr/bin/head -c 8 "$1"
  exit 23
fi
/bin/cat "$@"
FAKE_CAT
chmod +x "$ROLLBACK_BIN/cat"
set +e
MEMENTO_VAULT="$ROLLBACK_VAULT" MEMENTO_TEST_FAIL_BLOCK="$ROLLBACK_BLOCK" \
PATH="$ROLLBACK_BIN:$PATH" bash -c \
  '. "$1"; memento_append_daily_block "$2" "$3" "$4"' _ \
  "$ROLLBACK_VAULT/.scripts/memento_env.sh" "$ROLLBACK_FILE" "$ROLLBACK_DATE" "$ROLLBACK_BLOCK"
ROLLBACK_STATUS=$?
set -e
[ "$ROLLBACK_STATUS" -ne 0 ]
[ "$(shasum -a 256 "$ROLLBACK_FILE" | awk '{print $1}')" = "$ROLLBACK_HASH" ]
[ "$(grep -c 'should-not-commit' "$ROLLBACK_FILE" || true)" = "0" ]

# append_text 必须先确认真实写入成功，之后才能通知并触发每日第一帧。
# 用测试版 memento_env 注入成功/失败，测试安装器实际生成的 append_text，而非复制品。
TEXT_ROOT="$TMP_ROOT/text-integrity"
TEXT_SCRIPTS="$TEXT_ROOT/.scripts"
TEXT_TMP="$TEXT_ROOT/tmp"
TEXT_BIN="$TEXT_ROOT/bin"
NOTIFY_LOG="$TEXT_ROOT/notify.log"
TEXT_TODAY=$(TZ=Asia/Shanghai date +%Y-%m-%d)
mkdir -p "$TEXT_SCRIPTS" "$TEXT_TMP" "$TEXT_BIN"

awk '
  /cat > "\$SCRIPT_DIR\/append_text.sh" << '\''BASH_EOF'\''/ { copying = 1; next }
  copying && $0 == "BASH_EOF" { exit }
  copying { print }
' "$ROOT/install_aisecretary.sh" > "$TEXT_SCRIPTS/append_text.sh"
chmod +x "$TEXT_SCRIPTS/append_text.sh"

cat > "$TEXT_SCRIPTS/memento_env.sh" <<'FAKE_ENV'
#!/bin/bash
MEMENTO_VAULT="${MEMENTO_VAULT:?}"

memento_append_daily_block() {
  [ "${MEMENTO_TEST_WRITE_FAIL:-0}" = "1" ] && return 23
  mkdir -p "$MEMENTO_VAULT"
  cp "$3" "$MEMENTO_VAULT/written-block.md"
}

FAKE_ENV

cat > "$TEXT_BIN/osascript" <<'FAKE_OSASCRIPT'
#!/bin/bash
printf '%s\n' "$*" >> "${MEMENTO_TEST_NOTIFY_LOG:?}"
FAKE_OSASCRIPT
chmod +x "$TEXT_BIN/osascript"

set +e
MEMENTO_VAULT="$TEXT_ROOT/vault" \
MEMENTO_TEST_WRITE_FAIL=1 \
MEMENTO_TEST_NOTIFY_LOG="$NOTIFY_LOG" \
TMPDIR="$TEXT_TMP" \
PATH="$TEXT_BIN:$PATH" \
  "$TEXT_SCRIPTS/append_text.sh" "不会落档的内容" >/dev/null 2>&1
WRITE_STATUS=$?
set -e

[ "$WRITE_STATUS" -ne 0 ]
[ ! -e "$NOTIFY_LOG" ]
[ -z "$(find "$TEXT_TMP" -maxdepth 1 -name 'memento-text.*' -print -quit)" ]

MEMENTO_VAULT="$TEXT_ROOT/vault" \
MEMENTO_TEST_NOTIFY_LOG="$NOTIFY_LOG" \
SOURCE_APP="Codex" TAG="TODO" NOTE="测试备注" \
TMPDIR="$TEXT_TMP" \
PATH="$TEXT_BIN:$PATH" \
  "$TEXT_SCRIPTS/append_text.sh" "真实落档的内容"

grep -q '真实落档的内容' "$TEXT_ROOT/vault/written-block.md"
grep -q '#TODO' "$TEXT_ROOT/vault/written-block.md"
grep -q '测试备注' "$TEXT_ROOT/vault/written-block.md"
for _ in $(seq 1 100); do
  [ -f "$NOTIFY_LOG" ] && [ "$(wc -l < "$NOTIFY_LOG" | tr -d ' ')" -ge 1 ] && break
  sleep 0.01
done
[ "$(wc -l < "$NOTIFY_LOG" | tr -d ' ')" = "1" ]
[ "$(grep -c '行动线索' "$NOTIFY_LOG")" = "1" ]
if grep -q '#TODO' "$NOTIFY_LOG"; then
  echo "成功通知仍暴露 TODO 任务语义" >&2
  exit 1
fi
[ -z "$(find "$TEXT_TMP" -maxdepth 1 -name 'memento-text.*' -print -quit)" ]

# stdin 是主传输路径；以 echo 选项开头及末尾空行都不能丢失。
printf '%s' $'-n\n保留正文\n\n' | \
  MEMENTO_VAULT="$TEXT_ROOT/vault" \
  MEMENTO_TEST_NOTIFY_LOG="$NOTIFY_LOG" \
  TMPDIR="$TEXT_TMP" PATH="$TEXT_BIN:$PATH" \
  "$TEXT_SCRIPTS/append_text.sh"
grep -q '^-n$' "$TEXT_ROOT/vault/written-block.md"
grep -q '^保留正文$' "$TEXT_ROOT/vault/written-block.md"

# 图片、语音和截图资产必须在同一秒并发时仍一条对应一个唯一原件。
CAPTURE_ROOT="$TMP_ROOT/capture-integrity"
CAPTURE_VAULT="$CAPTURE_ROOT/vault"
CAPTURE_SCRIPTS="$CAPTURE_ROOT/scripts"
CAPTURE_BIN="$CAPTURE_ROOT/bin"
CAPTURE_NOTIFY="$CAPTURE_ROOT/notify.log"
mkdir -p "$CAPTURE_SCRIPTS" "$CAPTURE_BIN" "$CAPTURE_VAULT"
cp "$SCRIPTS/memento_env.sh" "$CAPTURE_SCRIPTS/memento_env.sh"
for SCRIPT_NAME in append_image append_voice capture_screenshot; do
  awk -v script_name="$SCRIPT_NAME" '
    $0 ~ "cat > \\\"\\$SCRIPT_DIR/" script_name ".sh\\\" << '\''BASH_EOF'\''" { copying = 1; next }
    copying && $0 == "BASH_EOF" { exit }
    copying { print }
  ' "$ROOT/install_aisecretary.sh" > "$CAPTURE_SCRIPTS/$SCRIPT_NAME.sh"
  chmod +x "$CAPTURE_SCRIPTS/$SCRIPT_NAME.sh"
done
cat > "$CAPTURE_BIN/osascript" <<'FAKE_CAPTURE_OSASCRIPT'
#!/bin/bash
printf '%s\n' "$*" >> "${MEMENTO_TEST_CAPTURE_NOTIFY:?}"
FAKE_CAPTURE_OSASCRIPT
cat > "$CAPTURE_BIN/screencapture" <<'FAKE_SCREENCAPTURE'
#!/bin/bash
printf 'screenshot-%s' "$$" > "$2"
FAKE_SCREENCAPTURE
cat > "$CAPTURE_SCRIPTS/ocr_image" <<'FAKE_OCR'
#!/bin/bash
exit 0
FAKE_OCR
chmod +x "$CAPTURE_BIN/osascript" "$CAPTURE_BIN/screencapture" "$CAPTURE_SCRIPTS/ocr_image"

for index in $(seq 1 20); do
  printf 'image-%02d' "$index" > "$CAPTURE_ROOT/image-$index.png"
  MEMENTO_VAULT="$CAPTURE_VAULT" MEMENTO_DAILY_SNAPSHOT_DISABLED=1 \
  MEMENTO_TEST_CAPTURE_NOTIFY="$CAPTURE_NOTIFY" PATH="$CAPTURE_BIN:$PATH" \
    "$CAPTURE_SCRIPTS/append_image.sh" "$CAPTURE_ROOT/image-$index.png" &
done
wait
TODAY=$(date +%Y-%m-%d)
[ "$(find "$CAPTURE_VAULT/assets" -type f -name '*-image-*.png' | wc -l | tr -d ' ')" = "20" ]
[ "$(find "$CAPTURE_VAULT/assets" -type f -name '*-image-*.png' -exec shasum -a 256 {} \; | awk '{print $1}' | sort -u | wc -l | tr -d ' ')" = "20" ]
[ "$(grep -o './assets/[^)]*-image-[^)]*\.png' "$CAPTURE_VAULT/$TODAY.md" | sort -u | wc -l | tr -d ' ')" = "20" ]

: > "$CAPTURE_ROOT/empty-transcript.txt"
for index in $(seq 1 20); do
  printf 'audio-%02d' "$index" > "$CAPTURE_ROOT/audio-$index.m4a"
  MEMENTO_VAULT="$CAPTURE_VAULT" MEMENTO_DAILY_SNAPSHOT_DISABLED=1 \
  MEMENTO_TEST_CAPTURE_NOTIFY="$CAPTURE_NOTIFY" PATH="$CAPTURE_BIN:$PATH" \
    "$CAPTURE_SCRIPTS/append_voice.sh" "$CAPTURE_ROOT/audio-$index.m4a" \
      "$CAPTURE_ROOT/empty-transcript.txt" 1.0 QA &
done
wait
[ "$(find "$CAPTURE_VAULT/assets" -type f -name '*-voice-*.m4a' | wc -l | tr -d ' ')" = "20" ]
[ "$(find "$CAPTURE_VAULT/assets" -type f -name '*-voice-*.m4a' -exec shasum -a 256 {} \; | awk '{print $1}' | sort -u | wc -l | tr -d ' ')" = "20" ]
[ "$(grep -o './assets/[^)]*-voice-[^)]*\.m4a' "$CAPTURE_VAULT/$TODAY.md" | sort -u | wc -l | tr -d ' ')" = "20" ]
for _ in $(seq 1 100); do
  [ -f "$CAPTURE_NOTIFY" ] && grep -q '未生成转写' "$CAPTURE_NOTIFY" && break
  sleep 0.01
done
grep -q '未生成转写' "$CAPTURE_NOTIFY"

for _ in $(seq 1 12); do
  MEMENTO_VAULT="$CAPTURE_VAULT" MEMENTO_DAILY_SNAPSHOT_DISABLED=1 \
  MEMENTO_TEST_CAPTURE_NOTIFY="$CAPTURE_NOTIFY" PATH="$CAPTURE_BIN:$PATH" \
    "$CAPTURE_SCRIPTS/capture_screenshot.sh" &
done
wait
[ "$(find "$CAPTURE_VAULT/assets" -type f -name '*-screenshot-*.png' | wc -l | tr -d ' ')" = "12" ]
[ "$(grep -o './assets/[^)]*-screenshot-[^)]*\.png' "$CAPTURE_VAULT/$TODAY.md" | sort -u | wc -l | tr -d ' ')" = "12" ]
[ "$(stat -f %Lp "$CAPTURE_VAULT/assets")" = "700" ]
find "$CAPTURE_VAULT/assets" -type f -exec sh -c \
  '[ "$(stat -f %Lp "$1")" = "600" ]' _ {} \;

# 资产复制失败不得写 Markdown、不得通知成功。
FAIL_ROOT="$TMP_ROOT/asset-copy-failure"
FAIL_VAULT="$FAIL_ROOT/vault"
FAIL_BIN="$FAIL_ROOT/bin"
mkdir -p "$FAIL_VAULT" "$FAIL_BIN"
printf 'cannot-copy' > "$FAIL_ROOT/source.png"
cat > "$FAIL_BIN/cp" <<'FAKE_FAIL_CP'
#!/bin/bash
exit 23
FAKE_FAIL_CP
chmod +x "$FAIL_BIN/cp"
set +e
MEMENTO_VAULT="$FAIL_VAULT" MEMENTO_DAILY_SNAPSHOT_DISABLED=1 \
MEMENTO_TEST_CAPTURE_NOTIFY="$FAIL_ROOT/notify.log" PATH="$FAIL_BIN:$CAPTURE_BIN:$PATH" \
  "$CAPTURE_SCRIPTS/append_image.sh" "$FAIL_ROOT/source.png" >/dev/null 2>&1
FAIL_STATUS=$?
set -e
[ "$FAIL_STATUS" -ne 0 ]
[ ! -e "$FAIL_VAULT/$TODAY.md" ]
[ ! -e "$FAIL_ROOT/notify.log" ]
[ -z "$(find "$FAIL_VAULT/assets" -type f 2>/dev/null -print -quit)" ]

# Markdown 已 commit 后，即使写锁隔离失败，API 也必须返回成功并保留被引用资产。
RELEASE_ROOT="$TMP_ROOT/release-after-commit"
RELEASE_VAULT="$RELEASE_ROOT/vault"
RELEASE_BIN="$RELEASE_ROOT/bin"
mkdir -p "$RELEASE_VAULT" "$RELEASE_BIN"
printf 'release-failure-asset' > "$RELEASE_ROOT/source.png"
cat > "$RELEASE_BIN/mv" <<'FAKE_RELEASE_MV'
#!/bin/bash
if [[ "${1:-}" == *.lock ]] && [[ "${2:-}" == *.released.* ]]; then
  exit 91
fi
exec /bin/mv "$@"
FAKE_RELEASE_MV
chmod +x "$RELEASE_BIN/mv"
MEMENTO_VAULT="$RELEASE_VAULT" MEMENTO_DAILY_SNAPSHOT_DISABLED=1 \
MEMENTO_TEST_CAPTURE_NOTIFY="$RELEASE_ROOT/notify.log" \
PATH="$RELEASE_BIN:$CAPTURE_BIN:$PATH" \
  "$CAPTURE_SCRIPTS/append_image.sh" "$RELEASE_ROOT/source.png" \
  >"$RELEASE_ROOT/stdout.log" 2>"$RELEASE_ROOT/stderr.log"
RELEASE_ASSET=$(find "$RELEASE_VAULT/assets" -type f -name '*-image-*.png' -print -quit)
[ -n "$RELEASE_ASSET" ]
[ -f "$RELEASE_ASSET" ]
grep -qF "./assets/$(basename "$RELEASE_ASSET")" "$RELEASE_VAULT/$TODAY.md"
grep -q '记录本身已安全落档' "$RELEASE_ROOT/stderr.log"
rm -rf "$RELEASE_VAULT/.state/write-locks/$TODAY.lock"

# TERM 精确落在 daily rename 与 caller 清空 PENDING_ASSET 之间时，
# 进程应退出，但 committed flag 必须让 EXIT trap 保留已被 Markdown 引用的资产。
TERM_ROOT="$TMP_ROOT/term-commit-window"
TERM_VAULT="$TERM_ROOT/vault"
TERM_BIN="$TERM_ROOT/bin"
mkdir -p "$TERM_VAULT" "$TERM_BIN"
printf 'term-window-asset' > "$TERM_ROOT/source.png"
cat > "$TERM_BIN/mv" <<'FAKE_TERM_MV'
#!/bin/bash
if [[ "${1:-}" == *.append.* ]] && [[ "${2:-}" == *.md ]]; then
  /bin/mv "$@" || exit $?
  kill -TERM "$PPID"
  sleep 0.1
  exit 0
fi
exec /bin/mv "$@"
FAKE_TERM_MV
chmod +x "$TERM_BIN/mv"
set +e
MEMENTO_VAULT="$TERM_VAULT" MEMENTO_DAILY_SNAPSHOT_DISABLED=1 \
MEMENTO_TEST_CAPTURE_NOTIFY="$TERM_ROOT/notify.log" \
PATH="$TERM_BIN:$CAPTURE_BIN:$PATH" \
  "$CAPTURE_SCRIPTS/append_image.sh" "$TERM_ROOT/source.png" \
  >"$TERM_ROOT/stdout.log" 2>"$TERM_ROOT/stderr.log"
TERM_STATUS=$?
set -e
[ "$TERM_STATUS" -ne 0 ]
TERM_ASSET=$(find "$TERM_VAULT/assets" -type f -name '*-image-*.png' -print -quit)
[ -n "$TERM_ASSET" ]
[ -f "$TERM_ASSET" ]
grep -qF "./assets/$(basename "$TERM_ASSET")" "$TERM_VAULT/$TODAY.md"
[ ! -d "$TERM_VAULT/.state/write-locks/$TODAY.lock" ]
[ -z "$(find "$TERM_VAULT" -maxdepth 1 -name '*.append.*' -print -quit)" ]

# 所有带资产的 caller 都必须尊重 committed ownership。
for COMMITTED_SCRIPT in \
  "$CAPTURE_SCRIPTS/append_image.sh" \
  "$CAPTURE_SCRIPTS/append_voice.sh" \
  "$CAPTURE_SCRIPTS/capture_screenshot.sh"; do
  rg -q 'MEMENTO_DAILY_APPEND_COMMITTED' "$COMMITTED_SCRIPT"
done

# commit 后通知必须是后台 best-effort，不能阻塞 Swift helper 判定成功。
for NOTIFY_SCRIPT in \
  "$TEXT_SCRIPTS/append_text.sh" \
  "$CAPTURE_SCRIPTS/append_image.sh" \
  "$CAPTURE_SCRIPTS/append_voice.sh" \
  "$CAPTURE_SCRIPTS/capture_screenshot.sh"; do
  if ! rg -q '>/dev/null 2>&1 &$' "$NOTIFY_SCRIPT"; then
    echo "采集成功通知没有后台执行: $NOTIFY_SCRIPT" >&2
    exit 1
  fi
done

# 标签选择不再默认 TODO；用户看到的是记录语义，底层仍映射到兼容标签。
rg -qF 'set labels to {"灵感", "行动线索", "下次再读"}' "$ROOT/install_aisecretary.sh"
rg -qF '只用于回看，不表示待办或优先级' "$ROOT/install_aisecretary.sh"
rg -qF 'if chosenLabel is "行动线索" then return "TODO"' "$ROOT/install_aisecretary.sh"
rg -qF 'NOTIFY_MSG="已存入 [行动线索]"' "$ROOT/install_aisecretary.sh"
if rg -qF 'default items {"TODO"}' "$ROOT/install_aisecretary.sh"; then
  echo "标签入口仍默认选中 TODO" >&2
  exit 1
fi

# 安装和卸载都覆盖旧版 AI秘书·* 入口，且不会借此删除 Vault 数据。
rg -qF '"$SERVICES_DIR"/AI秘书·*.workflow' "$ROOT/install_aisecretary.sh"
rg -qF '"$SERVICES_DIR"/AI秘书·*.workflow' "$ROOT/uninstall_aisecretary.sh"

echo "✓ daily note writer: concurrent blocks remain complete"
echo "✓ daily lock: token-safe stale takeover and migration serialization"
echo "✓ daily note writer: metadata preservation and failure rollback"
echo "✓ text writer: notify only after durable append"
echo "✓ capture assets: unique, private and failure-safe under concurrency"
echo "✓ installer semantics: record-first tags and legacy Service cleanup"
