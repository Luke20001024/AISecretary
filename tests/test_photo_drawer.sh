#!/bin/bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

node --check chrome-newtab/dashboard.js
node --check chrome-newtab/photo-cache-library.js
node --check chrome-newtab/photo-library.js
node --check chrome-newtab/daily-summary-library.js
node --check chrome-newtab/dashboard-operations-library.js
node --check chrome-newtab/archive-sanitizer-library.js
node --check chrome-newtab/viewer.js
node tests/test_photo_library.js
node tests/test_photo_persistent_cache.js
node tests/test_photo_asset_loader.js
node tests/test_photo_viewport_loader.js
node tests/test_optional_read_pool.js
node tests/test_daily_summary_library.js
python3 -m json.tool chrome-newtab/manifest.json >/dev/null
python3 - <<'PY'
from html.parser import HTMLParser

class ContractParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.controls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if attrs.get("aria-controls"):
            self.controls.append(attrs["aria-controls"])

parser = ContractParser()
with open("chrome-newtab/dashboard.html", encoding="utf-8") as handle:
    parser.feed(handle.read())

assert len(parser.ids) == len(set(parser.ids)), "dashboard.html contains duplicate ids"
assert set(parser.controls).issubset(set(parser.ids)), "aria-controls points to a missing drawer"

with open("chrome-newtab/dashboard.css", encoding="utf-8") as handle:
    css = handle.read()
assert css.count("{") == css.count("}"), "dashboard.css has unbalanced blocks"
PY

rg -q 'id="daily-summary-tab"' chrome-newtab/dashboard.html
rg -q 'id="daily-summary-drawer"' chrome-newtab/dashboard.html
rg -q 'src="photo-cache-library.js"' chrome-newtab/dashboard.html
rg -q 'src="photo-library.js"' chrome-newtab/dashboard.html
rg -q 'src="daily-summary-library.js"' chrome-newtab/dashboard.html
rg -q 'src="dashboard-operations-library.js"' chrome-newtab/dashboard.html
rg -q 'function renderDailySummaryList' chrome-newtab/dashboard.js
rg -q '^\.day-card \{' chrome-newtab/dashboard.css
rg -q 'Reviews.*Daily' chrome-newtab/dashboard.js
rg -Fq "getDirectoryHandle('status')" chrome-newtab/dashboard.js
rg -q "text: '待总结'" chrome-newtab/dashboard.js
rg -q "text: '总结已更新'" chrome-newtab/dashboard.js
rg -q "text: '记录有更新'" chrome-newtab/dashboard.js
rg -q "text: '待重建'" chrome-newtab/dashboard.js
rg -q "text: '生成失败'" chrome-newtab/dashboard.js
rg -Fq '请在 ~/AISecretary 中为 Memento 补跑 ${dayKey} 的 Daily Review' chrome-newtab/dashboard.js
rg -q 'data-review-rerun' chrome-newtab/dashboard.js
rg -q '^\.day-review-rerun \{' chrome-newtab/dashboard.css
rg -q 'sourceHash' chrome-newtab/daily-summary-library.js
rg -q '"version": "0.9.0"' chrome-newtab/manifest.json

node <<'NODE'
const fs = require('fs');
const source = fs.readFileSync('chrome-newtab/dashboard.js', 'utf8');
const html = fs.readFileSync('chrome-newtab/dashboard.html', 'utf8');

const photoCacheScript = html.indexOf('photo-cache-library.js');
const photoLibraryScript = html.indexOf('photo-library.js');
const dashboardScript = html.indexOf('dashboard.js');
if (!(photoCacheScript >= 0
    && photoLibraryScript > photoCacheScript
    && dashboardScript > photoLibraryScript)) {
  throw new Error('持久照片缓存、照片加载层与 Dashboard 的脚本顺序错误');
}

const closeStart = source.indexOf('function closeSideDrawers');
const closeEnd = source.indexOf('// =============================================================', closeStart);
const closeDrawer = source.slice(closeStart, closeEnd);
if (!closeDrawer.includes("closingDrawerId === 'daily-summary-drawer') cancelPhotoRender()")
    || closeDrawer.includes('releasePhotoObjectUrls()')) {
  throw new Error('关闭每日总结必须只取消旧 DOM 渲染，不能清空同 Tab 照片缓存');
}

const renderStart = source.indexOf('async function renderDailySummaryList');
const renderEnd = source.indexOf('function openDailySummaryDrawer', renderStart);
const render = source.slice(renderStart, renderEnd);
if (!render.includes('cancelPhotoRender()')
    || !render.includes('startDailySummaryPhotoViewport(days, generation)')
    || render.includes('releasePhotoObjectUrls()')
    || render.includes('photoAssetLoader.loadBatch(daysWithPhotos')
    || /for\s*\([^)]*daysWithPhotos[\s\S]*await\s+readDayPhotoFile/.test(render)) {
  throw new Error('照片侧栏没有使用近屏调度、渐进渲染和暖缓存');
}
const viewportStart = source.indexOf('function startDailySummaryPhotoViewport');
const viewportEnd = source.indexOf('async function renderDailySummaryList', viewportStart);
const viewport = source.slice(viewportStart, viewportEnd);
if (!viewport.includes('window.MementoPhotos.createViewportLoader({')
    || !viewport.includes('root: list')
    || !viewport.includes('rootMargin: PHOTO_VIEWPORT_ROOT_MARGIN')
    || !viewport.includes('photoAssetLoader.loadBatch([item.day.photo]')
    || !viewport.includes('list.contains(figure)')
    || !viewport.includes('viewportIsCurrent')) {
  throw new Error('每日总结照片没有按滚动容器近屏加载，或旧 DOM 仍可能接收迟到结果');
}
if (!source.includes('const PHOTO_LOAD_CONCURRENCY = 3;')
    || !source.includes('const PHOTO_THUMBNAIL_MAX_WIDTH = 480;')
    || !source.includes("type: 'image/webp'")
    || !source.includes('prepareFile: preparePhotoForDisplay')
    || !source.includes('let photoPermissionLost = false;')
    || !source.includes('if (result && result.permissionLost) photoPermissionLost = true;')
    || !source.includes('viewportIsCurrent')
    || !source.includes("window.addEventListener('pagehide', releasePhotoObjectUrls)")) {
  throw new Error('照片并发、轻量缩略图或页面退出清理合同缺失');
}
if (!source.includes('const photoThumbnailCacheRepository = window.MementoPhotoCache')
    || !source.includes("const PHOTO_THUMBNAIL_VARIANT = 'w480-webp-q72-v1';")
    || !source.includes('loadPersistent: loadPersistentPhoto')
    || !source.includes('storePersistent: storePersistentPhoto')
    || !source.includes('context.binding.token')) {
  throw new Error('跨 Tab 缩略图缓存没有绑定目录身份、变体版本或照片加载器');
}
const persistentReadStart = source.indexOf('async function loadPersistentPhoto');
const persistentReadEnd = source.indexOf('async function storePersistentPhoto', persistentReadStart);
const persistentRead = source.slice(persistentReadStart, persistentReadEnd);
if (!persistentRead.includes('photoThumbnailCacheRepository.get(')
    || !persistentRead.includes('access.withTimeout(')
    || !persistentRead.includes('PHOTO_PERSISTENT_DECISION_MS')) {
  throw new Error('持久缩略图读取没有短超时，缓存故障可能阻塞实时照片回退');
}
const releaseStart = source.indexOf('function releasePhotoObjectUrls');
const releaseEnd = source.indexOf('function markDailySummaryDataChanged', releaseStart);
const release = source.slice(releaseStart, releaseEnd);
if (!release.includes('photoPermissionLost = false;')
    || !release.includes('photoPersistentReadDisabled = false;')
    || !release.includes('photoPersistentWriteDisabled = false;')) {
  throw new Error('新授权或切换目录后没有解除旧照片权限熔断');
}
if (!release.includes('photoThumbnailCacheRepository.clearMemory()')
    || release.includes('deleteBinding(')
    || release.includes('photoThumbnailCacheRepository.delete(')) {
  throw new Error('页面退出必须只释放照片内存，不能删除跨 Tab 持久缩略图');
}

const quarantineStart = source.indexOf('function quarantineDirectoryActions');
const quarantineEnd = source.indexOf('function showGrantUI', quarantineStart);
if (!source.slice(quarantineStart, quarantineEnd).includes('releasePhotoObjectUrls()')) {
  throw new Error('撤权或切目录时没有清除旧目录照片缓存');
}

const optionalStart = source.indexOf('async function readOptionalDashboardData');
const optionalEnd = source.indexOf('function fileReadIssue', optionalStart);
const optional = source.slice(optionalStart, optionalEnd);
if (!optional.includes('Promise.allSettled([')
    || !optional.includes('listDailyReviewFiles(handle, coordinatedOptions)')
    || !optional.includes('listDailyReviewStateFiles(handle, coordinatedOptions)')
    || !optional.includes('readDailyReviewPrompt(handle, coordinatedOptions)')) {
  throw new Error('Review、状态和 Prompt 没有安全并发并等待全部物理请求收敛');
}
if (!source.includes('const OPTIONAL_FILE_READ_CONCURRENCY = 3;')
    || !source.includes('while (optionalReadActive < OPTIONAL_FILE_READ_CONCURRENCY')
    || !source.includes('const coordinator = createOptionalReadCoordinator(options);')
    || !source.includes('coordinator.schedule(async () =>')
    || !source.includes('options.coordinator?.fail(error)')) {
  throw new Error('Review 与状态文件没有共用受控并发池或统一权限熔断');
}

const hydrateStart = source.indexOf('async function hydrateOptionalDashboardData');
const hydrateEnd = source.indexOf('function cacheContextForHandle', hydrateStart);
const hydrate = source.slice(hydrateStart, hydrateEnd);
const reviewCommitStart = source.indexOf('function commitReviewDataToVisibleState');
const reviewCommitEnd = source.indexOf('function commitCoreRecordView', reviewCommitStart);
const reviewCommit = source.slice(reviewCommitStart, reviewCommitEnd);
if (!hydrate.includes('isCurrent: () => directoryLoadGate.isCurrent(generation)')
    || !hydrate.includes('commitReviewDataToVisibleState(handle, generation')
    || !reviewCommit.includes('refreshDailySummaryOptionalView()')
    || hydrate.includes('void renderDailySummaryList()')) {
  throw new Error('可选数据补齐没有绑定目录代次，或仍会无条件重建照片列表');
}

const optionalViewStart = source.indexOf('function refreshDailySummaryOptionalView');
const optionalViewEnd = source.indexOf('async function renderDailySummaryList', optionalViewStart);
const optionalView = source.slice(optionalViewStart, optionalViewEnd);
if (!optionalView.includes('current.replaceWith(updated)')
    || !optionalView.includes('dailySummaryRenderedLayout === layout')) {
  throw new Error('Daily Review 补齐没有在照片布局稳定时局部更新文字区域');
}

const openStart = source.indexOf('function openDailySummaryDrawer');
const openEnd = source.indexOf('function initDailySummaries', openStart);
if (!source.includes('function canReuseRenderedDailySummary')
    || !source.slice(openStart, openEnd).includes('void renderDailySummaryList()')) {
  throw new Error('已完成的每日总结 DOM 无法在同 Tab 关闭重开时复用');
}

const commitStart = source.indexOf('function commitCoreRecordView');
const commitEnd = source.indexOf('async function hydrateOptionalDashboardData', commitStart);
if (!source.slice(commitStart, commitEnd)
    .includes("activeDrawerId === 'daily-summary-drawer') void renderDailySummaryList({ force: true })")) {
  throw new Error('核心记录完整读取后没有立即刷新已打开的每日总结');
}

if (!viewport.includes('readDayPhotoFile(record, resolveAssetsDir, isDirectoryCurrent)')) {
  throw new Error('照片多阶段文件读取没有在目录切换后停止后续 FSA 请求');
}

const coreStart = source.indexOf('async function produceCoreRecords');
const coreEnd = source.indexOf('function scheduleOptionalHydration', coreStart);
const core = source.slice(coreStart, coreEnd);
if (!core.includes('if (!detail.isToday) return;') || core.includes('partialFiles')) {
  throw new Error('无缓存首屏仍可能为每个历史文件重复解析和重绘');
}
NODE

echo "✓ daily summary drawer: photo + review pairing, HTML, CSS and extension contract"
