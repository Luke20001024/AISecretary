#!/bin/bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

node --check chrome-newtab/dashboard.js
node --check chrome-newtab/directory-access-library.js
node --check chrome-newtab/dashboard-cache-library.js
node --check chrome-newtab/dashboard-operations-library.js
node --check chrome-newtab/archive-sanitizer-library.js
node --check chrome-newtab/viewer.js
node --check chrome-newtab/prompts.js
node tests/test_directory_access_library.js
node tests/test_dashboard_cache_library.js
node tests/test_dashboard_operations_library.js
node tests/test_today_first_refresh.js
node tests/test_heatmap_navigation.js
node tests/test_archive_security.js
node tests/test_archive_read_pool.js
node tests/test_archive_fast_path.js
node tests/test_optional_read_pool.js
node tests/test_daily_summary_fast_path.js
node tests/test_html_demo_summary_cache.js

rg -q 'id="record-summary"' chrome-newtab/dashboard.html
rg -q "currentFilter: 'all'" chrome-newtab/dashboard.js
rg -q '今天留下了' chrome-newtab/dashboard.js
rg -q '全部记录' chrome-newtab/dashboard.js
rg -q "KNOWN_TAGS = new Set\(\['TODO', '灵感', '下次再读'\]\)" chrome-newtab/dashboard.js
rg -q 'rememberedDirectoryHandle' chrome-newtab/dashboard.js
rg -qF 'enterCognitiveDemo();' chrome-newtab/dashboard.js
rg -qF 'directory-access-library.js' chrome-newtab/dashboard.html
rg -qF 'photo-cache-library.js' chrome-newtab/dashboard.html
rg -qF 'dashboard-cache-library.js' chrome-newtab/dashboard.html
rg -qF 'dashboard-operations-library.js' chrome-newtab/dashboard.html
rg -qF 'coordinateCoreRefresh' chrome-newtab/dashboard.js
rg -qF 'window.MementoDashboardOperations.filterEntriesForDate' chrome-newtab/dashboard.js
rg -qF 'recordSource' chrome-newtab/dashboard.js
rg -qF 'copyModeForRecordState' chrome-newtab/dashboard.js
rg -qF '复制当前显示的' chrome-newtab/dashboard.js
rg -qF '【数据状态：当前显示的是上次完整记录；今天最新内容仍在后台核对】' chrome-newtab/dashboard.js
rg -qF 'produceCoordinatedCoreRecords' chrome-newtab/dashboard.js
rg -qF 'await context.handle.isSameEntry(storedHandle)' chrome-newtab/dashboard.js
rg -qF "type: 'selection-changed'" chrome-newtab/dashboard.js
rg -qF 'publication.scanDate === session.today' chrome-newtab/dashboard.js
rg -qF 'quarantineDirectoryActions();' chrome-newtab/dashboard.js
rg -qF 'archiveMutationStillCurrent(context)' chrome-newtab/dashboard.js
node <<'NODE'
const fs = require('fs');
const source = fs.readFileSync('chrome-newtab/dashboard.js', 'utf8');
const loadStart = source.indexOf('async function loadHandle()');
const loadEnd = source.indexOf('const dashboardCacheRepository', loadStart);
if (loadStart < 0 || loadEnd < 0) throw new Error('无法定位 loadHandle');
if (source.slice(loadStart, loadEnd).includes('readBootstrap')) {
  throw new Error('handle 恢复被完整快照读取阻塞');
}
const html = fs.readFileSync('chrome-newtab/dashboard.html', 'utf8');
const photoCacheScript = html.indexOf('photo-cache-library.js');
const photoLibraryScript = html.indexOf('photo-library.js');
const cacheScript = html.indexOf('dashboard-cache-library.js');
const operationsScript = html.indexOf('dashboard-operations-library.js');
const dashboardScript = html.indexOf('dashboard.js');
if (!(photoCacheScript >= 0
    && photoLibraryScript > photoCacheScript
    && cacheScript >= 0
    && operationsScript > cacheScript
    && dashboardScript > photoLibraryScript
    && dashboardScript > operationsScript)) {
  throw new Error('Dashboard 模块加载顺序错误');
}
const coordinatedStart = source.indexOf('async function produceCoordinatedCoreRecords');
const coordinatedEnd = source.indexOf('function scheduleCoreRefresh', coordinatedStart);
const coordinated = source.slice(coordinatedStart, coordinatedEnd);
if (!coordinated.includes('await persistCompleteSnapshot(session, recordResult)')) {
  throw new Error('leader 在共享快照提交前释放了核心刷新锁');
}
if (source.includes('scheduleFollowerRetry')
    || source.includes('followerRetryTimer')
    || source.includes('core-refresh-settled')
    || source.includes('refreshRound')
    || source.includes('refreshChainId')) {
  throw new Error('follower 仍包含定时自动接管，可能重复扫描');
}
const scheduleStart = source.indexOf('function scheduleCoreRefresh');
const scheduleEnd = source.indexOf('function loadAndRenderLocked', scheduleStart);
const schedule = source.slice(scheduleStart, scheduleEnd);
const todayFirst = schedule.indexOf('operations.startTodayFirstRefresh({');
const todayRead = schedule.indexOf('readTodayRecord(session)', todayFirst);
const historyStart = schedule.indexOf('startHistory:', todayRead);
const historyLock = schedule.indexOf('operations.coordinateCoreRefresh(', historyStart);
if (!(todayFirst >= 0
    && todayRead > todayFirst
    && historyStart > todayRead
    && historyLock > historyStart)) {
  throw new Error('今日单文件点读必须位于完整历史刷新锁之外并先于历史扫描');
}
if (!schedule.includes("result.role === 'follower'")
    || schedule.includes('reloadSharedSnapshot(session)')
    || schedule.includes('scheduleCoreStandby')
    || schedule.includes('coordinateCoreStandby')) {
  throw new Error('follower 不应立即重读同一缓存或自动排队补开扫描');
}
const hydrateStart = source.indexOf('async function startCacheHydration');
const hydrateEnd = source.indexOf('async function permissionStillGranted', hydrateStart);
const hydrate = source.slice(hydrateStart, hydrateEnd);
if (hydrateStart < 0
    || !hydrate.includes('const context = await session.contextPromise')
    || !hydrate.includes('session.todayFile')
    || !hydrate.includes('session.cacheDecisionExpired')
    || !hydrate.includes('(context.cache || context.archiveIndex)')
    || !hydrate.includes('!await permissionStillGranted(session)')
    || hydrate.includes('session.liveShown ||')) {
  throw new Error('缓存 hydration 没有仅对超时迟到结果复核权限，或 today-first 会丢失历史快照');
}
const startupStart = source.indexOf('async function loadAndRenderLocked');
const startupEnd = source.indexOf('function loadAndRender(', startupStart);
const startup = source.slice(startupStart, startupEnd);
if (startupStart < 0
    || !startup.includes('operations.startCacheFirstRefresh({')
    || !startup.includes('hydrateCache: () => startCacheHydration(session)')
    || !startup.includes('waitForCache: hydrationPromise => waitForStartupCache(session, hydrationPromise)')
    || !startup.includes('hasVisibleContent: () => state.dirHandle === handle')
    || !startup.includes('shouldRefresh: () => !(state.dirHandle === handle')
    || !startup.includes('afterFirstPaint: afterFirstDashboardPaint')
    || !startup.includes('startRefresh: () => scheduleCoreRefresh(session)')) {
  throw new Error('根目录扫描没有置于缓存提交和首帧绘制之后');
}
const persistenceStart = source.indexOf('async function persistSelectedDirectoryHandle');
const persistenceEnd = source.indexOf('async function listMarkdownFiles', persistenceStart);
const persistence = source.slice(persistenceStart, persistenceEnd);
if (!persistence.includes('onEventuallyPersisted')) {
  throw new Error('目录保存超时后晚到成功不会补发切换通知');
}
if (!persistence.includes('preparedSelection.startPersistence()')
    || !persistence.includes('operations.withArchiveMutationLock(navigator.locks, startPersistence)')
    || persistence.includes('preparedSelection.persistence')) {
  throw new Error('目录选择没有在共享写锁内惰性启动持久化');
}
if (!source.includes('let selectionFlowId = 0')
    || !source.includes('function selectionFlowStillCurrent(flowId)')) {
  throw new Error('目录恢复流程缺少统一代次');
}
const autoStart = source.indexOf('async function tryAutoLoad');
const autoEnd = source.indexOf('async function loadSelectedDirectory', autoStart);
const auto = source.slice(autoStart, autoEnd);
if (!auto.includes('const flowId = ++selectionFlowId')
    || !auto.includes('loadDirectory: handle =>')
    || !auto.includes('const bootstrapPromise = dashboardCacheRepository')
    || !auto.includes('restoredContextPromise = bootstrapPromise.then')
    || !auto.includes('loadAndRender(handle, generation, restoredContextPromise)')
    || !auto.includes('onStage: stage =>')
    || !auto.includes('selectionFlowStillCurrent(flowId)')
    || !auto.includes('if (selectionFlowStillCurrent(flowId)) setGrantBusy(false)')) {
  throw new Error('自动恢复可能在晚到后覆盖新目录或新 UI');
}
const reloadStart = source.indexOf('async function reloadPersistedSelectionAfterBroadcast');
const reloadEnd = source.indexOf("window.addEventListener('pagehide'", reloadStart);
const reload = source.slice(reloadStart, reloadEnd);
if (!reload.includes('const flowId = ++selectionFlowId')
    || !reload.includes('directoryLoadGate.begin()')
    || !reload.includes('selectionFlowStillCurrent(flowId)')
    || !reload.includes('loadSelectedDirectory(storedHandle, null, flowId)')) {
  throw new Error('跨标签页目录同步不能淘汰更早的恢复流程');
}
const selectedStart = source.indexOf('async function loadSelectedDirectory');
const selectedEnd = source.indexOf("grantBtn.addEventListener('click'", selectedStart);
const selected = source.slice(selectedStart, selectedEnd);
if (!selected.includes('flowId = selectionFlowId')
    || (selected.match(/selectionFlowStillCurrent\(flowId\)/g) || []).length < 3) {
  throw new Error('选定目录的加载边界没有在前后核验流程代次');
}
const clickStart = source.indexOf("grantBtn.addEventListener('click'");
const clickEnd = source.indexOf('enterCognitiveDemo();', clickStart);
const click = source.slice(clickStart, clickEnd);
if (!click.includes('const flowId = ++selectionFlowId')
    || !click.includes('directoryLoadGate.begin()')
    || !click.includes('loadSelectedDirectory(rememberedDirectoryHandle, null, flowId)')
    || !click.includes('if (selectionFlowStillCurrent(flowId)) setGrantBusy(false)')) {
  throw new Error('手动授权/选目录不能淘汰更早的恢复流程');
}
const notifyStart = click.indexOf('const notifySelectionPersisted');
const notifyEnd = click.indexOf('const selection = await', notifyStart);
const notify = click.slice(notifyStart, notifyEnd);
if (!notify.includes("type: 'selection-changed'")
    || !notify.includes('if (!selectionFlowStillCurrent(flowId))')
    || !notify.includes('reloadPersistedSelectionAfterBroadcast()')) {
  throw new Error('晚到的目录持久化没有同时通知其他页面并协调当前页面');
}
if (!click.includes('{ cacheFirst: false }')) {
  throw new Error('新选目录被旧缓存上下文阻塞');
}
const archiveMatchStart = source.indexOf('async function archiveContextMatchesPersisted');
const archiveMatchEnd = source.indexOf('function reconcileArchiveSelectionMismatch', archiveMatchStart);
const archiveMatch = source.slice(archiveMatchStart, archiveMatchEnd);
if (!archiveMatch.includes('loadHandle')
    || !archiveMatch.includes('await context.handle.isSameEntry(storedHandle)')
    || (archiveMatch.match(/archiveMutationStillCurrent\(context\)/g) || []).length < 3) {
  throw new Error('归档写入前没有在 await 边界核验当前持久化目录');
}
const archiveReadStart = source.indexOf('const archiveReadQueue = []');
const archiveReadEnd = source.indexOf('function extractTitle', archiveReadStart);
const archiveRead = source.slice(archiveReadStart, archiveReadEnd);
if (!source.includes('const ARCHIVE_READ_CONCURRENCY = 3;')
    || !source.includes('const ARCHIVE_TITLE_SCAN_BYTES = 256 * 1024;')
    || !archiveRead.includes('scheduleArchiveRead(async () =>')
    || !source.includes('while (archiveReadActive < ARCHIVE_READ_CONCURRENCY')
    || !archiveRead.includes('file.slice(0, ARCHIVE_TITLE_SCAN_BYTES)')
    || !archiveRead.includes('cached.mtime === mtime')
    || !archiveRead.includes('notifyArchiveItem(options.onItem, resolved)')
    || source.includes('hydrateArchiveTitles')) {
  throw new Error('归档列表没有使用缓存标题、渐进更新与共享三路并发');
}
const closeDrawerStart = source.indexOf('function closeSideDrawers');
const closeDrawerEnd = source.indexOf('// =============================================================', closeDrawerStart);
const closeDrawer = source.slice(closeDrawerStart, closeDrawerEnd);
if (!closeDrawer.includes("closingDrawerId === 'archive-drawer') archiveRenderGeneration++")
    || closeDrawer.includes('resetArchiveIndexState()')) {
  throw new Error('关闭归档侧栏应停止旧 UI 更新，但必须保留同标签页索引');
}
const coreCommitStartForArchiveBadge = source.indexOf('function commitCoreRecordView');
const coreCommitEndForArchiveBadge = source.indexOf('async function hydrateOptionalDashboardData', coreCommitStartForArchiveBadge);
const coreCommitForArchiveBadge = source.slice(coreCommitStartForArchiveBadge, coreCommitEndForArchiveBadge);
if (!source.includes('bootstrapArchiveIndex: null')
    || !source.includes('session.bootstrapArchiveIndex = context.archiveIndex || null')
    || !source.includes('session.bootstrapArchiveIndex = cacheContext.archiveIndex || null')
    || source.includes('dashboardCacheRepository.readArchiveIndex(cacheContext.binding.token)')
    || !(coreCommitForArchiveBadge.indexOf('primeArchiveIndexFromActiveSession()')
      < coreCommitForArchiveBadge.indexOf('initArchives()'))) {
  throw new Error('归档数量没有复用启动事务并在侧栏首次绘制前同步恢复');
}
const archiveSaveStartForRefresh = source.indexOf('async function saveArchiveFiles');
const archiveRenderStartForRefresh = source.indexOf('async function renderArchives', archiveSaveStartForRefresh);
const archiveSaveForRefresh = source.slice(archiveSaveStartForRefresh, archiveRenderStartForRefresh);
if (!archiveSaveForRefresh.includes('applyArchiveIndexMutation(directoryContext')
    || (archiveSaveForRefresh.match(/activeDrawerId === 'archive-drawer'/g) || []).length < 2
    || !archiveSaveForRefresh.includes('void startArchiveIndexRefresh(directoryContext, { force: true })')
    || !archiveSaveForRefresh.includes('void renderArchives({ forceRefresh: true })')) {
  throw new Error('归档保存没有先更新可见索引，或在侧栏关闭时仍可能启动隐藏刷新');
}
const archiveSaveStart = source.indexOf('async function saveArchiveFiles');
const archiveSaveEnd = source.indexOf('async function renderArchives', archiveSaveStart);
const archiveSave = source.slice(archiveSaveStart, archiveSaveEnd);
const saveLock = archiveSave.indexOf('await withArchiveMutationLock');
const saveMatch = archiveSave.indexOf('await archiveContextMatchesPersisted', saveLock);
const saveDir = archiveSave.indexOf('getArchiveDir(true', saveMatch);
if (!(saveLock >= 0 && saveMatch > saveLock && saveDir > saveMatch)) {
  throw new Error('归档保存没有在共享写锁内先核验持久化目录');
}
const archiveDeleteStart = source.indexOf("list.querySelectorAll('.ai-del')");
const archiveDeleteEnd = source.indexOf('// 点击归档', archiveDeleteStart);
const archiveDelete = source.slice(archiveDeleteStart, archiveDeleteEnd);
const deleteLock = archiveDelete.indexOf('await withArchiveMutationLock');
const deleteMatch = archiveDelete.indexOf('await archiveContextMatchesPersisted', deleteLock);
const deleteDir = archiveDelete.indexOf('getArchiveDir(false', deleteMatch);
const removeEntry = archiveDelete.indexOf('removeEntry(name)', deleteDir);
if (!(deleteLock >= 0 && deleteMatch > deleteLock && deleteDir > deleteMatch && removeEntry > deleteDir)
    || !archiveDelete.includes("activeDrawerId === 'archive-drawer'")) {
  throw new Error('归档删除没有在共享写锁内先核验持久化目录');
}
const grantStart = source.indexOf('function showGrantUI');
const grantEnd = source.indexOf('function shortError', grantStart);
const grant = source.slice(grantStart, grantEnd);
if (!grant.includes('retireActiveCoreLoad()') || !grant.includes('quarantineDirectoryActions()')) {
  throw new Error('授权/切目录界面没有先隔离旧目录操作');
}
const commitStart = source.indexOf('function commitCoreRecordView');
const commitEnd = source.indexOf('async function hydrateOptionalDashboardData', commitStart);
if (!source.slice(commitStart, commitEnd).includes('options.today || getLocalDate()')) {
  throw new Error('页面日期没有固定到本轮扫描日期');
}
NODE
rg -qF 'loadWhilePersisting' chrome-newtab/dashboard.js
rg -qF 'directoryLoadGate.commit' chrome-newtab/dashboard.js
rg -qF 'recordReadIssues' chrome-newtab/dashboard.js
rg -qF 'readDayPhotoFile' chrome-newtab/dashboard.js
rg -qF 'runArchiveMutation' chrome-newtab/dashboard.js
rg -qF 'MementoDashboardOperations.withArchiveMutationLock' chrome-newtab/dashboard.js
rg -qF 'permission = await requestRead(rememberedDirectoryHandle);' chrome-newtab/dashboard.js
rg -qF 'event.source !== window.opener' chrome-newtab/viewer.js
rg -qF "connect-src 'none'" chrome-newtab/manifest.json
if rg -qF 'loadHandle().catch' chrome-newtab/dashboard.js; then
  echo '目录授权读取错误仍被静默吞掉' >&2
  exit 1
fi
if rg -U -q 'Promise\.all\(\[\s*listMarkdownFiles\([\s\S]*listDailyReviewFiles' chrome-newtab/dashboard.js; then
  echo '主记录仍与可选 Daily Review 并发绑定' >&2
  exit 1
fi
for TERM in \
  'withDirectoryReadLock' \
  'withDashboardDirectoryReadLock' \
  'FILE_SYSTEM_IDLE_TIMEOUT_MS' \
  'loadTimeoutMs' \
  'MementoDashboardOperations.withIdleTimeout'; do
  if rg -qF "$TERM" chrome-newtab/dashboard.js; then
    echo "普通文件读取仍包含旧硬超时或旧式排队读锁: $TERM" >&2
    exit 1
  fi
done
rg -q '## 行动线索' daily-review/DAILY_REVIEW.md
rg -q '目标是帮助回看和理解,不是督促清理任务' chrome-newtab/prompts.js

for TERM in \
  'todo-check' \
  'aisec.done' \
  '未完成 TODO' \
  '所有 TODO 已清空' \
  '标记完成/撤销' \
  'TODO · Memento' \
  'renderTodoBanner' \
  'updateFavicon' \
  'entry-body.is-done' \
  'chip.is-todo'; do
  if rg -qF "$TERM" chrome-newtab/dashboard.html chrome-newtab/dashboard.js chrome-newtab/dashboard.css; then
    echo "记录面板仍包含任务完成语义: $TERM" >&2
    exit 1
  fi
done

for TERM in '必须处理的 TODO' 'TODO 清单:' 'TODO 漂移' '还在拖的事' '反复出现但没推进'; do
  if rg -qF "$TERM" chrome-newtab/prompts.js; then
    echo "Prompt 仍包含催办语义: $TERM" >&2
    exit 1
  fi
done

# 新 Review 用记录优先章节；历史章节的展示兼容由 JS 数据层测试覆盖。
TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT
DATE=2026-07-13
mkdir -p "$TMP_ROOT/Reviews/Daily" "$TMP_ROOT/.chrome-newtab"
cp chrome-newtab/prompts.js "$TMP_ROOT/.chrome-newtab/prompts.js"
printf '%s\n' '# test record' > "$TMP_ROOT/$DATE.md"
HASH=$(shasum -a 256 "$TMP_ROOT/$DATE.md" | awk '{print $1}')
PROMPT_HASH=$(shasum -a 256 "$TMP_ROOT/.chrome-newtab/prompts.js" | awk '{print $1}')

write_review() {
  printf '%s\n' \
    '---' \
    "date: $DATE" \
    'type: memento-review' \
    'period: daily' \
    'source: "[[2026-07-13]]"' \
    "source_hash: \"$HASH\"" \
    'source_mock: false' \
    'prompt: memento-comprehensive' \
    "prompt_hash: \"$PROMPT_HASH\"" \
    'generated_at: 2026-07-13T21:00:00+08:00' \
    '---' \
    '' \
    '# Daily Review · 2026-07-13' \
    '' \
    '## 工作与生活现场' \
    '无' \
    '' \
    '## 行动线索' \
    '无' \
    '' \
    '## 灵感与想法' \
    '无' \
    '' \
    '## 个人记录/情绪' \
    '无' \
    '' \
    '## 已忽略' \
    '无' \
    '' \
    '## 来源索引' \
    '' \
    '- [[2026-07-13]]' \
    '' \
    '## 我的补充' \
    '无' > "$TMP_ROOT/Reviews/Daily/$DATE.md"
}

write_review
MEMENTO_VAULT="$TMP_ROOT" bash daily-review/verify_review.sh "$DATE" >/dev/null

# 本机存在已安装目录时,顺便防止“源码已改、Chrome 仍运行旧版”。
INSTALLED_ROOT="${MEMENTO_INSTALLED_ROOT:-$HOME/AISecretary}"
if [ -d "$INSTALLED_ROOT/.chrome-newtab" ]; then
  for FILE in README.md archive-sanitizer-library.js cognitive-demo-fixture.js cognitive-home-library.js daily-summary-library.js dashboard-cache-library.js dashboard.css dashboard.html dashboard.js dashboard-operations-library.js directory-access-library.js manifest.json photo-cache-library.js photo-library.js prompts.js remember-agent-v1-library.js viewer.html viewer.js; do
    cmp -s "chrome-newtab/$FILE" "$INSTALLED_ROOT/.chrome-newtab/$FILE" || {
      echo "已安装扩展未同步: $FILE" >&2
      exit 1
    }
  done
fi
if [ -d "$INSTALLED_ROOT/.review" ] && find "$INSTALLED_ROOT/.review" -type f -print -quit | grep -q .; then
  echo "基础记录版不应保留可执行的 Daily Review 协议" >&2
  exit 1
fi

echo "✓ record-first dashboard: fixed cognitive view, basic capture, and no installed Daily Review protocol"
