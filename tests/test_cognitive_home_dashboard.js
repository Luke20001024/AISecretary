'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'chrome-newtab', 'dashboard.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'chrome-newtab', 'dashboard.css'), 'utf8');
const js = fs.readFileSync(path.join(root, 'chrome-newtab', 'dashboard.js'), 'utf8');
const evidenceRiverCss = css.slice(css.indexOf('Cognitive home · evidence-first time river'));
const historyAction = html.match(/<button id="cognitive-history-action"[^>]*>[\s\S]*?<\/button>/)?.[0] || '';

assert.match(html, /<meta name="color-scheme" content="light">/,
  '纸张型认知主页应固定浅色配色，不随浏览器自动反色');
assert.match(css, /color-scheme:\s*only light/,
  '样式层应声明仅使用浅色配色');

for (const id of [
  'cognitive-home-shell', 'cognitive-landscape-map', 'cognitive-list-region',
  'cognitive-record-list', 'cognitive-chain-drawer', 'cognitive-drawer-scrim',
  'cognitive-portrait-section', 'cognitive-portrait-feature', 'cognitive-portrait-list',
  'cognitive-portrait-kicker', 'cognitive-portrait-title', 'cognitive-portrait-count',
  'cognitive-portrait-auto-update', 'cognitive-portrait-update-at',
  'cognitive-portrait-open', 'cognitive-history-action', 'cognitive-today-title',
  'cognitive-home-view', 'cognitive-records-view', 'cognitive-record-results',
  'cognitive-record-tags-panel', 'cognitive-record-range',
  'legacy-dashboard-shell',
]) {
  assert.match(html, new RegExp(`id="${id}"`), `${id} 必须存在`);
}

assert.ok(
  html.indexOf('cognitive-home-library.js') < html.indexOf('dashboard.js'),
  '严格投影合同必须先于页面入口加载'
);
assert.ok(
  html.indexOf('cognitive-demo-fixture.js') < html.indexOf('dashboard.js'),
  '固定认知数据必须先于页面入口加载'
);
assert.equal((html.match(/cognitive-demo-fixture\.js/g) || []).length, 1,
  '唯一版本只能加载一份固定认知数据');
assert.ok(html.indexOf('cognitive-record-browser.js') >= 0
  && html.indexOf('cognitive-record-browser.js') < html.indexOf('dashboard.js'),
  '记录浏览的数据模块必须先于页面入口加载');
assert.equal((html.match(/cognitive-record-browser\.js/g) || []).length, 1,
  '记录浏览模块只能加载一次');
assert.match(html, /data-cognitive-secondary="context"/);
assert.match(html, /data-cognitive-page="records"/);
assert.doesNotMatch(html, /data-cognitive-secondary="daily"/,
  '认知主页不得保留独立每日评价入口');
assert.doesNotMatch(html, /data-cognitive-secondary="output"/,
  '认知主链不得继续分叉到旧输出页');
assert.match(html, /id="cognitive-portrait-kicker">内在图志<\/p>/);
assert.match(html, /id="cognitive-portrait-title">她理解的我<\/h2>/);
assert.match(html,
  /id="cognitive-portrait-count"[^>]*>4 条 · 6 主题<\/output>/,
  '理解数量与主题数量应压缩在同一行');
assert.match(html,
  /id="cognitive-portrait-auto-update" type="checkbox" aria-label="自动更新理解"/,
  '理解栏应提供自动更新的界面开关');
assert.match(html,
  /id="cognitive-portrait-update-at" type="time" value="21:00" disabled/,
  '更新时间初始为 21:00，并在自动更新关闭时不可编辑');
assert.match(css,
  /\.cognitive-portrait-section \{[\s\S]*?container-name:\s*cognitive-portrait;[\s\S]*?container-type:\s*inline-size;/,
  '她理解的我必须按分栏自身宽度响应，不能只依赖浏览器视口断点');
assert.match(css,
  /@container cognitive-portrait \(max-width: 560px\) \{[\s\S]*?\.cognitive-portrait-head \{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\);[\s\S]*?grid-template-rows:\s*auto auto;/,
  '窄分栏下标题与更新控件必须分行，避免标题、开关和时间重叠');
assert.match(html, /id="cognitive-today-title">今天的时间河<\/h2>/);
assert.match(html,
  /data-cognitive-timeline-now[^>]*aria-controls="cognitive-record-list"[^>]*hidden>回到现在<\/button>/,
  '时间河必须提供一个只在离开最新记录后出现的回到现在入口');
assert.match(html,
  /id="cognitive-record-list"[^>]*aria-label="今日记录，最新在左，向右浏览更早记录"/,
  '时间河必须明确最新记录在左侧，并向右进入历史');
assert.ok(
  html.indexOf('class="cognitive-topbar"') < html.indexOf('class="cognitive-today-section"')
    && html.indexOf('class="cognitive-today-section"') < html.indexOf('class="cognitive-overview-grid"'),
  '认知主页必须按顶栏、今天的时间河、地景与深层理解排列'
);
for (const icon of ['river', 'terrain', 'eye']) {
  assert.match(html,
    new RegExp(`class="cognitive-module-icon cognitive-module-icon--${icon}" data-cognitive-section-icon="${icon}"[\\s\\S]*?stroke-width="1\\.25"[\\s\\S]*?aria-hidden="true" focusable="false"`),
    `${icon} 模块必须使用统一的无障碍隐藏线性图标`);
}
assert.equal((html.match(/class="cognitive-module-icon cognitive-module-icon--/g) || []).length, 3,
  '主页只使用河流、地形与眼睛三枚模块识别图标');
assert.equal((html.match(/class="cognitive-module-icon-accent"/g) || []).length, 3,
  '三枚模块图标都应保留一个克制的动态焦点');
assert.match(css, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.cognitive-module-icon-accent[\s\S]*animation: none !important/,
  '模块图标动画必须尊重减少动态效果偏好');
assert.match(css,
  /\.cognitive-module-icon \{[\s\S]*?transform: translateY\(2px\);/,
  '三枚模块图标必须使用统一的光学基线修正');
assert.match(css,
  /\.cognitive-module-icon--river \{[\s\S]*?color: var\(--cog-blue-deep\);/,
  '时间河图标必须使用地景蓝色');
assert.match(historyAction, /<span>记录轨迹<\/span>/);
assert.match(html,
  /id="cognitive-portrait-open"[^>]*data-cognitive-secondary="context"[^>]*aria-controls="cognitive-chain-drawer"/,
  '她理解的我应使用认知主页的统一详情抽屉');
assert.match(historyAction,
  /data-cognitive-page="records"[^>]*aria-controls="cognitive-records-view"[^>]*aria-pressed="false"/,
  '记录轨迹应通过主导航进入独立记录浏览页，初始保持认知首页选中');
assert.doesNotMatch(historyAction, /data-cognitive-secondary="archive"|aria-controls="cognitive-chain-drawer"/,
  '记录浏览入口不得继续打开旧轨迹抽屉');
assert.match(html, /id="cognitive-records-view"[^>]*hidden/,
  '记录浏览页初始应隐藏，切换主导航后显示');
assert.doesNotMatch(html, /class="cognitive-secondary-nav"/,
  '深层理解已成为常驻内容，不再使用边缘导轨');
assert.equal((html.match(/data-cognitive-manual-day/g) || []).length, 1,
  '主页只能有一个“积累今天”入口');
assert.match(html, /id="cognitive-manual-day-status"[^>]*role="status"[^>]*aria-live="polite"/);

for (const state of [
  'raw_saved', 'processing', 'ready', 'needs_review', 'no_candidate', 'failed',
  'original_only', 'merged',
]) {
  assert.match(js, new RegExp(`${state}:`), `${state} 必须拥有用户可读状态`);
}
for (const state of [
  'candidate', 'waiting_evidence', 'waiting_confirmation', 'promoted',
  'not_candidate', 'rejected',
]) {
  assert.match(js, new RegExp(`${state}:`), `${state} 必须拥有用户可读的记忆升级状态`);
}
assert.match(js, /function cognitiveAttachBridgeRecordMetadata/,
  '页面必须将 V2\.1 的关系判断与记忆状态附加到冻结的 V1 主页投影');
assert.match(js, /function cognitiveTodayView/,
  '页面必须把今日时间河与上一份认知快照分开');
assert.match(js, /runtimeLocalDate: bootstrap\.runtime_local_date/,
  '真实运行时必须使用后端返回的权威本地日期');
assert.match(js, /cognitiveHomeState\.todayHome \|\| cognitiveHomeState\.home/,
  '今日时间河必须读取经过日期隔离的今日视图');
assert.match(js, /认知快照更新至/,
  '跨日时必须告知认知地景的快照日期');
assert.match(js, /Agent 如何理解这条记录/,
  '记录详情必须解释 Agent 对作者、与用户关系和认知信号的判断');
for (const status of ['no_candidate', 'no_records', 'no_receipts']) {
  assert.match(js, new RegExp(`${status}:`), `${status} 必须拥有用户可读日级状态`);
}

assert.match(js, /COGNITIVE_HOME_PROJECTION_PATH/);
assert.match(js, /COGNITIVE_LANDSCAPE_PATH/);
assert.match(js, /home_projection\.json/);
assert.match(js, /validateHomeProjection\(homeResult\.value\)/);
assert.match(js, /validateLandscapeSnapshot\(landscapeResult\.value\)/);
assert.match(js, /validateProjectionPair\(home, landscape, landscapeResult\.sha256\)/);
assert.match(js, /readCognitiveProjectionAuthorityBase/);
assert.match(js, /validateProjectionAuthority/);
assert.match(js, /formal-head-index\.json/);
assert.match(js, /record-index\.json/);
assert.match(js, /readCognitiveActionWatermark/);
assert.match(js, /cognitiveHomeState\.status = 'authorizing'/);
assert.match(js, /profile\.memories\.find/,
  '正式地景只能连接已校验 profile 的当前理解，不得提前应用待提交用户修改');
assert.match(js, /cognitiveHomeState\.status === 'ready'/);
assert.match(js, /本地认知主页没有通过合同与来源映射校验/);
assert.match(js, /本地认知主页版本与当前页面不兼容/);
assert.match(js, /认知主页尚未生成/);
assert.match(js,
  /title: '无法载入本机个人认知'[\s\S]*?status: `连接失败：\$\{shortError\(error\)\}`[\s\S]*?action: 'runtime'/,
  'Runtime 或快照合同校验失败必须退出无限加载并显示实际错误');
assert.match(js,
  /if \(grantAction === 'runtime'\) \{[\s\S]*?connectCognitiveRuntimeFromDirectory\(\{ requestPermission: true \}\)/,
  '载入失败画面上的重新连接必须真正重试 Backend V2');

const cognitiveStart = js.indexOf('function cognitiveHomeLibrary()');
const cognitiveEnd = js.indexOf('function renderRecordSummary', cognitiveStart);
assert.ok(cognitiveStart >= 0 && cognitiveEnd > cognitiveStart, '认知主页实现区必须可定位');
const cognitiveSource = js.slice(cognitiveStart, cognitiveEnd);
assert.doesNotMatch(cognitiveSource, /record\.raw|record\.body|todayFileText|renderMarkdown\(/,
  '认知主页不能读取或渲染原始记录正文');
assert.doesNotMatch(cognitiveSource, /cmem_|candidate[_-]memory|candidate[_-]relation/,
  '认知地景不能把候选对象当作正式峰或关系');
assert.match(cognitiveSource, /landscape\.peaks\.map/);
assert.match(cognitiveSource, /landscape\.nodes\.map/);
assert.match(cognitiveSource, /landscape\.edges\.map/);
assert.match(cognitiveSource, /function cognitiveTerrainValue/);
assert.match(cognitiveSource, /function cognitiveTerrainSubpeakItems/,
  '固定地景必须把次级倾向作为纯视觉峰参与地形');
assert.match(cognitiveSource, /function cognitiveTerrainRidgeContribution/,
  '主峰与次峰之间必须生成连续山脊');
assert.match(cognitiveSource, /function cognitiveMapSubpeakMarkup/,
  '放大后必须能显示次级倾向短标签');
assert.match(cognitiveSource, /class="cognitive-subpeak[^\"]*" aria-hidden="true"/,
  '次峰只是第一层主题的视觉展开，不得伪装成正式可操作理解');
assert.match(cognitiveSource, /class="cognitive-map-screen-space" data-cognitive-screen-space>[\s\S]*cognitive-subpeak-core/,
  '次峰圆点和短标签必须共享定屏幕容器');
assert.match(cognitiveSource, /const nodeById = new Map\(landscape\.nodes/,
  '运行时必须再次确认每个次峰仍由正式 memory node 支撑');
assert.match(cognitiveSource, /class="cognitive-map-screen-space" data-cognitive-screen-space>[\s\S]*cognitive-subpeak-core/,
  '次峰圆点和短标签必须共享定屏幕容器');
assert.match(cognitiveSource, /const nodeById = new Map\(landscape\.nodes/,
  '运行时必须再次确认每个次峰仍由正式 memory node 支撑');
assert.match(cognitiveSource, /cognitiveDistanceToSegmentSquared/,
  '正式关系必须参与连续地形的桥接高度');
assert.match(cognitiveSource, /item\.point\.x/,
  '已归并记忆点必须参与连续地形');
assert.match(cognitiveSource, /cognitiveTerrainSegments/);
assert.match(cognitiveSource, /cognitiveStitchTerrainSegments/);
assert.doesNotMatch(cognitiveSource, /const contourMarkup = landscape\.peaks\.map/,
  '不得回退为每座峰独立的装饰同心圈');
assert.match(cognitiveSource, /applyCognitiveMapHover/);
assert.match(cognitiveSource, /cognitive-elevation-band/,
  '静态地形必须由同一高度场生成可叠加的高程色带');
assert.match(cognitiveSource, /function cognitiveAtlasBackdropMarkup/,
  '地图必须提供坐标网格、定位十字与图框');
assert.match(js, /function cognitiveUpdateAtlasHud/,
  '指北针、比例尺、图例和坐标必须以视口 HUD 跟随地图相机更新');
assert.match(html, /class="cognitive-atlas-hud"[\s\S]*id="cognitive-atlas-scale-label"[\s\S]*class="cognitive-atlas-north"/,
  '地图家具必须悬浮在视口层，不得烙进 SVG 图幅');
assert.doesNotMatch(cognitiveSource, /function cognitiveAtlasFurnitureMarkup/,
  '地图家具不得继续作为 SVG 世界坐标内容随缩放漂移');
assert.match(html, /id="cognitive-map-region"[^>]*tabindex="0"/,
  '可探索地图必须提供独立键盘焦点');
assert.match(html, /id="cognitive-landscape-map"[^>]*preserveAspectRatio="xMidYMid slice"/,
  '地图在窄视口必须裁切世界范围，100% 时才能继续平移查看图外内容');
for (const action of ['fullscreen', 'zoom-out', 'zoom-in', 'reset']) {
  assert.match(html, new RegExp(`data-cognitive-map-action="${action}"`),
    `可探索地图必须提供 ${action} 控件`);
}
assert.match(html, /id="cognitive-map-zoom"[^>]*aria-live="polite"/,
  '缩放层级必须以有限状态向辅助技术反馈');
assert.match(js, /const COGNITIVE_MAP_MAX_ZOOM = 3\.2/);
assert.match(js, /function cognitiveZoomMapAt/);
assert.match(js, /function cognitivePanMap/);
assert.match(js, /function cognitiveSetMapFullscreen/);
assert.match(js, /function cognitiveMapDefaultZoomAnchor/,
  '缩放按钮必须朝最近主题推进，避免高倍视图落在空白地形');
assert.match(js, /getScreenCTM\?\.\(\)/,
  '围绕指针缩放必须使用当前 SVG 屏幕矩阵');
assert.match(js, /function cognitiveMapViewportScale/,
  '地图必须测量当前 viewBox 到屏幕的实际缩放比例');
assert.match(js, /function cognitiveMapVisibleWorldSize/,
  '相机边界必须按 slice 后的真实可见窗口计算');
assert.match(js, /screenAspect < worldAspect[\s\S]*?width: viewportHeight \* screenAspect/,
  '100% 嵌入态被横向裁切时必须保留左右平移空间');
assert.match(js, /cognitiveClampMapCamera\(svg\)/,
  '相机约束必须绑定当前地图画布尺寸');
assert.match(js, /baseScreenScale\s*\/\s*viewportScale/,
  '主页缩放与全屏切换必须反向补偿屏幕空间内容，保持字号和圆点大小稳定');
assert.match(js, /Math\.max\(\.01, Math\.min\(/,
  '高分辨率全屏的反向补偿不得被过大的最小比例截断');
assert.match(js, /querySelectorAll\('\[data-cognitive-screen-space\]'\)/,
  '主题文字、圆点和证据标记必须统一使用屏幕空间补偿');
assert.match(cognitiveSource, /class="cognitive-map-screen-space" data-cognitive-screen-space/,
  '主题和记忆点必须生成屏幕空间容器');
assert.match(cognitiveSource, /transform="translate\(\$\{point\.x\.toFixed\(2\)\}/,
  '主题间空间位置仍须由地图坐标控制');
const mapWheelSource = js.slice(
  js.indexOf("region.addEventListener('wheel', event => {"),
  js.indexOf("region.addEventListener('dblclick', event => {", js.indexOf("region.addEventListener('wheel', event => {"))
);
assert.match(mapWheelSource, /event\.preventDefault\(\)/,
  '主页地图必须直接支持滚轮缩放');
assert.doesNotMatch(mapWheelSource, /cognitiveMapCameraState\.fullscreen/,
  '缩放能力不得依赖全屏状态');
const mapPointerSource = js.slice(
  js.indexOf("region.addEventListener('pointerdown', event => {"),
  js.indexOf("region.addEventListener('pointermove', event => {", js.indexOf("region.addEventListener('pointerdown', event => {"))
);
assert.doesNotMatch(mapPointerSource, /cognitiveMapCameraState\.fullscreen/,
  '100% 主页地图也必须允许拖动查看被裁切内容');
assert.doesNotMatch(mapPointerSource, /setPointerCapture/,
  '普通单击不得提前捕获指针，否则主题点击会被拖拽层截走');
const mapPointerMoveSource = js.slice(
  js.indexOf("region.addEventListener('pointermove', event => {"),
  js.indexOf('const finishPointer = event => {', js.indexOf("region.addEventListener('pointermove', event => {"))
);
assert.match(mapPointerMoveSource, /Math\.hypot[\s\S]*?> 3[\s\S]*?setPointerCapture/,
  '只有移动超过阈值后才可捕获指针并进入拖拽');
assert.match(js, /suppressClickUntil = performance\.now\(\) \+ 320/,
  '拖拽结束必须阻止一次误点击');
assert.match(js, /if \(key === 'Escape' && cognitiveMapCameraState\.fullscreen\)/,
  'Escape 必须退出全屏');
const mapKeydownSource = js.slice(
  js.indexOf("region.addEventListener('keydown', event => {"),
  js.indexOf("if (typeof cognitiveReducedMotionMedia?.addEventListener", js.indexOf("region.addEventListener('keydown', event => {"))
);
assert.ok(
  mapKeydownSource.indexOf("key === 'Escape'")
    < mapKeydownSource.indexOf("event.target.closest?.('.cognitive-map-controls')"),
  '即使焦点停在缩放控件，Escape 也必须优先退出全屏'
);
const mapFullscreenSource = js.slice(
  js.indexOf('function cognitiveSetMapFullscreen'),
  js.indexOf('function cognitiveMapSvgPoint', js.indexOf('function cognitiveSetMapFullscreen'))
);
assert.match(mapFullscreenSource, /region\.getBoundingClientRect\(\);[\s\S]*?cognitiveApplyMapCamera\(\{ clamp: false \}\);/,
  '全屏几何变化必须在同一帧内完成字号与点径补偿');
assert.match(js, /new ResizeObserver\(\(\) => \([\s\S]*?cognitiveApplyMapCamera\(\{ clamp: false \}\)/,
  '画布改尺寸只能重算屏幕补偿，不得悄悄移动用户相机');
assert.doesNotMatch(mapFullscreenSource, /cognitiveResetMapCamera\(/,
  '全屏切换必须保留当前缩放与视图中心');
assert.match(mapFullscreenSource, /document\.documentElement\.classList\.toggle\('cognitive-map-fullscreen', fullscreen\)/,
  '全屏必须同步根节点状态，释放浏览器预留的滚动条槽');
assert.doesNotMatch(html, />探索地图<|进入探索|退出探索/,
  '产品中不应再存在独立探索模式；按钮只切换全屏');
assert.match(cognitiveSource, /function cognitiveMapPeakDetailMarkup/);
assert.match(cognitiveSource, /function cognitiveMapRecordConstellation/);
assert.doesNotMatch(cognitiveSource, /cognitive-peak-detail-card/,
  '高倍证据层不得生成遮挡地形的矩形详情卡');
assert.match(js, /if \(zoom >= 2\.05\) return 'evidence'/,
  '放大后必须出现证据层细节');

const homeSummarySource = cognitiveSource.slice(
  cognitiveSource.indexOf("const homeSummary = document.getElementById('cognitive-home-summary')"),
  cognitiveSource.indexOf("const notice = document.getElementById('cognitive-projection-notice')")
);
const fixedSummarySource = homeSummarySource.slice(
  homeSummarySource.indexOf('if (cognitiveDemoState.active)'),
  homeSummarySource.indexOf('} else {')
);
for (const dynamicFixtureValue of [
  /fixture\?\.themes\?\.length/,
  /fixture\?\.changes\?\.length/,
  /fixture\?\.stats\?\.totalRecords/,
]) {
  assert.match(fixedSummarySource, dynamicFixtureValue,
    '固定版顶栏统计必须取自 fixture，不能硬编码');
}
for (const label of ['个聚合主题', '项近期变化', '条记录']) {
  assert.ok(fixedSummarySource.includes(label), `固定版顶栏必须显示 ${label}`);
}
assert.doesNotMatch(fixedSummarySource, /可用记忆点/,
  '固定版顶栏不得暴露技术词“可用记忆点”');

const portraitRenderSource = cognitiveSource.slice(
  cognitiveSource.indexOf('function cognitiveDemoPortraitItems'),
  cognitiveSource.indexOf('function renderCognitiveHome')
);
assert.match(portraitRenderSource, /cognitiveDemoState\.fixture\?\.portrait/,
  '常驻深层理解必须来自 fixture portrait');
assert.match(portraitRenderSource, /const \[primary, \.\.\.secondary\] = items/,
  '四条理解必须继续对应现有主容器与列表容器');
assert.match(portraitRenderSource, /list\.innerHTML = secondary\.map/,
  '三条次级理解必须使用同一组固定数据渲染');
assert.match(portraitRenderSource, /data-cognitive-portrait-id=/,
  '每条深层理解必须拥有稳定交互身份');
assert.match(portraitRenderSource, /forming: '形成中'[^]*stable: '已稳定'/,
  '深层理解必须使用统一的成熟度状态语言');
assert.match(portraitRenderSource, /class="cognitive-portrait-orbit" aria-hidden="true"/,
  '动态轨道应作为隐藏于辅助技术的装饰系统');
assert.match(portraitRenderSource, /data-cognitive-orbit-portrait-id=/,
  '每圈轨道必须保留独立的理解映射身份');
assert.match(portraitRenderSource,
  /function cognitivePortraitOrbitMarkup\(item\)[\s\S]*?\[1, 2, 3\]\.map\(index =>/,
  '每条理解必须独立渲染三层轨道');
assert.match(portraitRenderSource,
  /feature\.innerHTML =[\s\S]*?cognitivePortraitOrbitMarkup\(primary\)/,
  '第一条理解必须使用通用轨道组件');
assert.match(portraitRenderSource,
  /list\.innerHTML = secondary\.map[\s\S]*?cognitivePortraitOrbitMarkup\(item\)/,
  '其余理解必须使用同一轨道组件');
assert.match(portraitRenderSource, /data-portrait-maturity=/,
  '主理解和次级理解必须显式输出成熟度状态');
assert.match(portraitRenderSource,
  /if \(!feature\.querySelector\('\.cognitive-portrait-orbit'\)[^]*feature\.dataset\.cognitiveOrbitSignature !== orbitSignature\)[^]*feature\.innerHTML =/,
  '轨道 DOM 必须仅在首次创建或数据身份改变时重建');
assert.match(portraitRenderSource, /feature\.dataset\.cognitiveOrbitSignature = orbitSignature/,
  '轨道 DOM 必须记录稳定签名，避免重复渲染重启动画');
assert.match(portraitRenderSource,
  /list\.dataset\.cognitivePortraitSignature !== listSignature[\s\S]*?list\.dataset\.cognitivePortraitSignature = listSignature/,
  '列表轨道也必须保持稳定 DOM，避免动画重启');
const portraitFeatureMarkup = portraitRenderSource.slice(
  portraitRenderSource.indexOf('feature.innerHTML ='),
  portraitRenderSource.indexOf('list.innerHTML = secondary.map')
);
assert.doesNotMatch(portraitFeatureMarkup, /primary\.statement|cognitive-portrait-eyebrow|cognitive-portrait-meta/,
  '主页理解只保留动态轨道、成熟度、标题和来源主题');
assert.match(portraitRenderSource, /cognitivePortraitThemeTitles/,
  '深层理解的来源主题必须由 themeIds 实时解析');
assert.match(portraitRenderSource, /new Set\(items\.flatMap\(item => item\.themeIds \|\| \[\]\)\)\.size/,
  '常驻深层理解计数必须同时呈现去重后的来源主题数');
assert.match(portraitRenderSource,
  /`\$\{items\.length\} 条\$\{linkedThemeCount \? ` · \$\{linkedThemeCount\} 主题` : ''\}`/,
  '理解栏计数必须使用紧凑的一行表达');
assert.match(cognitiveSource, /renderCognitivePortrait\(\);[\s\S]*renderCognitiveLandscape\(\);/,
  '常驻深层理解必须随主页投影同步渲染');
assert.match(css,
  /\.cognitive-portrait-orbit-ring--2 \{[\s\S]*?inset: 14%;[\s\S]*?\.cognitive-portrait-orbit-ring--3 \{[\s\S]*?inset: 26%;/,
  '三层轨道必须使用互不重叠的相对半径');
assert.match(css,
  /\.cognitive-portrait-feature \{[\s\S]*?grid-template-columns: 52px minmax\(0, 1fr\) auto;[\s\S]*?min-height: 78px;[\s\S]*?padding: 14px 2px;/,
  '第一条理解必须与后续理解使用同一行高和栅格');

const portraitLinkSource = cognitiveSource.slice(
  cognitiveSource.indexOf('function cognitiveMergeMapHoverContexts'),
  cognitiveSource.indexOf('function renderCognitiveUnderstandingList')
);
assert.match(portraitLinkSource, /function cognitivePortraitIdsForPeak/);
assert.match(portraitLinkSource, /function cognitivePeakIdsForPortrait/);
assert.match(portraitLinkSource,
  /filter\(item => \(item\.themeIds \|\| \[\]\)\.includes\(theme\.id\)\)/,
  '主题到深层理解的关系必须以 portrait.themeIds 为唯一来源');
assert.match(portraitLinkSource, /applyCognitivePortraitLinkFocus\(cognitivePortraitIdsForPeak\(identifier\)\)/,
  '聚焦地图主题时必须同步聚焦关联深层理解');
assert.match(portraitLinkSource, /function applyCognitiveInsightMapFocus/,
  '点击深层理解后必须投影显式关联的来源主题');
assert.match(portraitLinkSource,
  /const peakIds = new Set\(cognitivePeakIdsForPortrait\(identifier\)\)/,
  '第三层投影必须只读取 portrait.themeIds 解析出的主题峰');
assert.match(portraitLinkSource, /peakIds\.size < 2[\s\S]*?clearCognitiveInsightMapFocus\(\)/,
  '不足两个有效主题时不得绘制第三层山系');
assert.match(portraitLinkSource, /function toggleCognitiveInsightMapFocus/,
  '深层理解必须拥有可再次点击退出的固定状态');
assert.doesNotMatch(cognitiveSource, /function applyCognitivePortraitMapFocus/,
  '深层理解悬停不得改变地图，只有主动点击才显影山系');
assert.match(portraitLinkSource,
  /\[data-cognitive-orbit-portrait-id\][^]*element\.dataset\.cognitiveOrbitPortraitId/,
  '轨道圈必须响应精确的理解联动状态');
assert.doesNotMatch(cognitiveSource,
  /closest\('\[data-cognitive-orbit-portrait-id\]'\)/,
  '装饰轨道不得拥有独立的指针事件入口');
assert.match(cognitiveSource,
  /event\.target\.closest\('\[data-cognitive-portrait-id\]'\)[\s\S]*toggleCognitiveInsightMapFocus\([\s\S]*portrait\.dataset\.cognitivePortraitId[\s\S]*openCognitiveChainDrawer\('library', portrait\.dataset\.cognitivePortraitId, portrait\)/,
  '点击常驻深层理解应先显影来源山系，再携带该条身份进入统一详情抽屉');

const recordRenderSource = cognitiveSource.slice(
  cognitiveSource.indexOf('function renderCognitiveRecords'),
  cognitiveSource.indexOf('function applyCognitiveView')
);
const newestFirstSource = cognitiveSource.slice(
  cognitiveSource.indexOf('function cognitiveRecordsNewestFirst'),
  cognitiveSource.indexOf('const cognitiveTimelineState')
);
assert.match(newestFirstSource,
  /right\.captured_at\.localeCompare\(left\.captured_at\)/,
  '时间河必须按记录发生时间降序排列，让最新记录稳定处于左侧');
assert.match(recordRenderSource,
  /const recordsNewestFirst = cognitiveRecordsNewestFirst\(home\.records\)[\s\S]*recordsNewestFirst\.map\(\(record, index\) =>/,
  '时间河必须把当前主页投影按最新在左的顺序逐条渲染');
assert.match(recordRenderSource, /cognitiveTimeLabel\(record\.captured_at\)/,
  '时间河必须保留记录发生时间');
assert.match(recordRenderSource, /data-cognitive-entity="record"/,
  '时间河中的每条记录必须保持可追溯入口');
assert.match(recordRenderSource, /data-cognitive-timeline-index="\$\{index\}"/,
  '每条记录必须拥有稳定的时间河顺序');
assert.match(cognitiveSource,
  /const COGNITIVE_RIVER_PROFILES = \[[\s\S]*?function cognitiveRiverSegmentMarkup\(recordId, index, total\)/,
  '时间河必须由细流、平缓、小弯、大弯与宽河湾等稳定河形生成');
assert.match(cognitiveSource,
  /function cognitiveRiverSeed\(value\)[\s\S]*?function cognitiveRiverRandom\(seed\)[\s\S]*?function cognitiveRiverProfile\(recordId, index\)/,
  '河形随机性必须由记录身份稳定生成，刷新后不得跳变');
assert.match(recordRenderSource,
  /\$\{cognitiveRiverSegmentMarkup\(record\.record_ref\.id, index, recordsNewestFirst\.length\)\}/,
  '每条记录必须以自身身份在时间节点后方绘制稳定河道');
assert.match(css,
  /\.cognitive-river-water \{[\s\S]*?fill-opacity:[\s\S]*?\.cognitive-river-bank/,
  '时间河必须包含水面与双岸线视觉层次');
assert.match(cognitiveSource,
  /hasGlint = index === 0 \|\| index % 4 === 1[\s\S]*?class="cognitive-river-glint"/,
  '水光只能稀疏地出现在部分河段与最新记录');
assert.match(css,
  /\.cognitive-river-glint \{[\s\S]*?animation: cognitive-river-glint[\s\S]*?@keyframes cognitive-river-glint/,
  '时间河必须使用克制的变换与透明度水光表达流动');
assert.match(cognitiveSource,
  /const waterOpacity = \.036 \+ recency \* \.022;[\s\S]*?const bankOpacity = \.15 \+ recency \* \.045;/,
  '河面与岸线必须保持低对比的浅色层级');
assert.match(cognitiveSource,
  /--cognitive-river-breathe-delay:[\s\S]*?--cognitive-river-breathe-duration:/,
  '每段河道必须拥有稳定但不同步的呼吸节奏');
assert.match(css,
  /\.cognitive-river-water \{[\s\S]*?animation: cognitive-river-breathe[\s\S]*?@keyframes cognitive-river-breathe/,
  '河面呼吸只能使用透明度与缩放表达');
assert.match(recordRenderSource, /data-cognitive-timeline-state="\$\{timelineState\}"/,
  '每条记录必须暴露普通、进入地图或含不确定三种视觉状态');
const timelineStateSource = cognitiveSource.slice(
  cognitiveSource.indexOf('function cognitiveRecordTimelineState'),
  cognitiveSource.indexOf('const cognitiveTimelineState')
);
for (const [state, rule] of [
  ['uncertain', /record\.status === 'needs_review'/],
  ['map', /record\.status === 'merged' \|\| record\.understanding_refs\.length/],
  ['ordinary', /return 'ordinary'/],
]) {
  assert.match(timelineStateSource, rule, `时间河 ${state} 状态必须由有限规则产生`);
}
assert.match(recordRenderSource,
  /class="cognitive-record-axis"[\s\S]*?class="cognitive-record-time"[\s\S]*?class="cognitive-record-node"[\s\S]*?class="cognitive-record-stem"/,
  '每条记录必须生成时间、节点与连接茎组成的轴线结构');
assert.match(recordRenderSource,
  /class="cognitive-record-body"[\s\S]*?class="cognitive-record-meta"[\s\S]*?record\.source_app[\s\S]*?class="cognitive-record-state/,
  '记录正文必须从来源与状态开始，时间仅保留在轴线上');
const recordBodyMarkup = recordRenderSource.slice(
  recordRenderSource.indexOf('<span class="cognitive-record-body">'),
  recordRenderSource.indexOf('</button>`;')
);
assert.doesNotMatch(recordBodyMarkup, /<time\b|cognitiveTimeLabel/,
  '记录正文不得重复显示已嵌入轴线的发生时间');
assert.match(cognitiveSource,
  /if \(kind === 'record'\)[\s\S]*cognitiveRecordById\(identifier\)[\s\S]*loadCognitiveDrawerOriginal\(record\)/,
  '点击时间河记录后必须继续在统一抽屉中读取经校验的原文');

const timelineBehaviorSource = cognitiveSource.slice(
  cognitiveSource.indexOf('const cognitiveTimelineState'),
  cognitiveSource.indexOf('function applyCognitiveView')
);
assert.match(timelineBehaviorSource, /initialDemoRender[\s\S]*!cognitiveTimelineState\.initialized/,
  '固定内容版只能在首次渲染时定位到最新记录');
assert.match(timelineBehaviorSource,
  /options\.initialDemoRender \|\| \(options\.recordCountChanged && options\.wasAtNow\)/,
  '仅当首次进入或用户仍停在左侧最新记录时，新增记录才可跟随到现在');
assert.match(timelineBehaviorSource,
  /scrollWidthDelta[\s\S]*options\.previousScrollLeft \+ scrollWidthDelta[\s\S]*list\.scrollLeft = Math\.min\(preservedScrollLeft/,
  '用户向右阅读历史时，新增记录后必须继续锚定原浏览内容');
assert.match(timelineBehaviorSource, /\[data-cognitive-timeline-now\]/,
  '时间河必须支持可选的“回到现在”入口');
assert.match(timelineBehaviorSource, /list\.scrollTo\(\{ left, behavior \}\)/,
  '回到现在只应移动时间河自身，不得滚动整张页面');
assert.match(timelineBehaviorSource,
  /const latest = records\.at\(0\)[\s\S]*const left = 0/,
  '回到现在必须定位到左侧的第一条最新记录');
assert.match(timelineBehaviorSource,
  /cognitiveReducedMotionMedia\?\.matches \? 'auto' : 'smooth'/,
  '减少动态偏好必须把回到现在的平滑滚动降为即时定位');

const timelineInteractionStart = cognitiveSource.indexOf('function initCognitiveHomeInteractions');
const timelineInteractionSource = cognitiveSource.slice(
  timelineInteractionStart,
  cognitiveSource.indexOf("document.getElementById('cognitive-drawer-close')", timelineInteractionStart)
);
assert.match(timelineInteractionSource,
  /timeline\.addEventListener\('scroll'[\s\S]*syncCognitiveTimelineNowControl/,
  '时间河必须持续判断用户是否离开最新记录');
assert.match(timelineInteractionSource,
  /\['ArrowLeft', 'ArrowRight', 'Home', 'End'\][\s\S]*data-cognitive-timeline-index/,
  '记录必须支持左右方向键及首尾键浏览');
assert.match(timelineInteractionSource,
  /next\.focus\(\{ preventScroll: true \}\)[\s\S]*next\.scrollIntoView/,
  '键盘移动必须同步焦点和时间河内的可见位置');

const themeCandidateSource = cognitiveSource.slice(
  cognitiveSource.indexOf('function cognitiveThemeCandidate'),
  cognitiveSource.indexOf('function cognitivePeakForUnderstanding')
);
assert.match(themeCandidateSource, /Array\.from\(theme\)\.length > maximum/,
  '短主题的长度限制必须按 Unicode 字符计算');
assert.match(themeCandidateSource,
  /cognitiveThemeCandidate\(memory\?\.title, 18\)[\s\S]*`长期理解 \$\{String\(index \+ 1\)/,
  '地景只允许已校验短 title；不合格时必须使用中性稳定名称');
const peakTitleSource = themeCandidateSource.slice(
  themeCandidateSource.indexOf('function cognitivePeakTitle'),
  themeCandidateSource.indexOf('function cognitivePeakStatement')
);
assert.doesNotMatch(peakTitleSource, /statement|slice\(|substring\(|substr\(/,
  '地景标签不得从长理解正文猜测或截断');
assert.doesNotMatch(peakTitleSource, /scope/,
  '适用范围不得被冒充为聚合主题');
assert.match(themeCandidateSource,
  /function cognitivePeakStatement\(peak\)[\s\S]*memory\?\.statement \|\| memory\?\.title/,
  '完整理解应保持为单独文本，不与地景短主题混用');
const understandingListSource = cognitiveSource.slice(
  cognitiveSource.indexOf('function renderCognitiveUnderstandingList'),
  cognitiveSource.indexOf('function cognitiveRecordDestination')
);
assert.match(understandingListSource, /const statement = cognitivePeakStatement\(peak\)/);
assert.match(understandingListSource,
  /class="cognitive-understanding-copy"[\s\S]*cognitivePeakTitle\(peak, index\)[\s\S]*escapeHtml\(statement/,
  '列表视图应同时呈现短主题与完整理解');
assert.match(cognitiveSource, /readCognitiveAuthorizedRawRecord/);
assert.match(cognitiveSource, /library\.sha256Hex\(block\) !== locator\.entrySha256/,
  '原文只能在抽屉中经过记录块哈希校验后显示');
assert.match(cognitiveSource, /原文不进入主页投影/);
assert.match(cognitiveSource, /SourceRecord/);
assert.match(cognitiveSource, /data-cognitive-action="confirm_receipt"[^>]*>正确</);
assert.match(cognitiveSource, /data-cognitive-edit-form="edit_receipt"/);
assert.match(cognitiveSource, /data-cognitive-edit-form="edit_reusable_memory"/);
assert.match(cognitiveSource, /data-cognitive-edit-form="edit_relation"/);
assert.match(cognitiveSource, /data-cognitive-terminal-action="original_only"/);
assert.match(cognitiveSource, /data-cognitive-terminal-action="delete_reusable_memory"/);
assert.match(cognitiveSource, /data-cognitive-terminal-action="delete_relation"/);
assert.match(cognitiveSource, /MVP 中无法恢复/,
  'original_only 必须告知不可恢复');
assert.match(cognitiveSource, /terminal\.dataset\.confirmArmed !== 'true'/,
  '终态动作必须二次确认');

const secondaryRouteSource = cognitiveSource.slice(
  cognitiveSource.indexOf('function setCognitiveSecondaryExpanded'),
  cognitiveSource.indexOf('function openCognitiveOutputPopover')
);
const contextRouteSource = secondaryRouteSource.slice(
  secondaryRouteSource.indexOf("if (name === 'context')"),
  secondaryRouteSource.indexOf('const target =')
);
assert.match(secondaryRouteSource,
  /if \(name === 'context'\)[\s\S]*openCognitiveChainDrawer\('library', 'current', trigger\)/,
  '她理解的我必须路由到统一 cognitive chain drawer');
assert.doesNotMatch(contextRouteSource,
  /getElementById\(['"]context-tab['"]\)|getElementById\(['"]context-drawer['"]\)|\.click\(\)/,
  '她理解的我不得再绕行隐藏的旧 Context 入口');
assert.match(cognitiveSource,
  /if \(kind === 'library'\)[\s\S]*title: '她理解的我'[\s\S]*contextInsightMarkup\(\)/,
  '统一抽屉应在 library 模式内渲染第三层理解内容');

assert.match(js, /!\['synthetic', 'v1_adapter', 'v2_shadow', 'v2_live'\]\.includes\(fixture\.mode\)/,
  '每种认知数据都必须经过显式模式边界');
assert.match(js, /window\.MementoCognitiveDemoFixture \|\| null/,
  '页面入口必须读取 fixture 脚本实际暴露的浏览器全局');
assert.match(js,
  /function cognitiveDemoRawText\(recordId\)[\s\S]*typeof entry\?\.text === 'string' \? entry\.text : ''/,
  '形成链与原文抽屉必须从 fixture record 对象中取 text，不能显示 object 字符串');
assert.match(js,
  /const value = item\?\.value[\s\S]*const id = item\?\.ref\?\.id[\s\S]*return \[id, value\]/,
  'fixture 的 revision envelope 必须解包到 ref id 与 value');
assert.match(js, /当前版本只保留基础记录；新的记录暂时不会重算地景与长期理解/,
  '固定认知数据不得伪装为已经运行自动积累');
assert.match(js, /function cognitiveDemoPeakDrawer/);
assert.match(js, /function cognitiveDemoPortraitMarkup/);
assert.match(js, /function cognitiveDemoHistoryMarkup/);
assert.match(cognitiveSource,
  /function cognitiveDemoPeakDrawer[\s\S]*theme\.boundary[\s\S]*适用边界/,
  '第二层形成链必须展示主题适用边界');
assert.match(cognitiveSource,
  /function cognitiveDemoEvidenceMarkup[\s\S]*previewLimit = 5[\s\S]*查看其余/,
  '主题抽屉应先展示代表性依据，其余记录按需展开');
assert.match(cognitiveSource,
  /第三层[\s\S]*data-demo-open-library>查看「她理解的我」/,
  '主题概览首屏应给出通向第三层的明确入口');
assert.match(cognitiveSource, /function cognitiveDemoDayMarkup/);
assert.match(cognitiveSource, /data-demo-open-day=/,
  '记录轨迹必须能从日期进入当日全部记录');
assert.match(cognitiveSource, /data-demo-open-record=/,
  '当日列表必须为每条记录提供可达入口');
assert.match(cognitiveSource,
  /demoRecord \? '原始记录已保存在本地' : `SourceRecord/,
  '固定内容版记录抽屉不应把底层 SourceRecord 术语暴露给用户');
assert.match(cognitiveSource,
  /demoRecord \? '正在读取本地原文…' : '正在根据当前 SourceRecord/,
  '固定内容版的原文读取提示应使用产品语言');
assert.match(cognitiveSource, /这一天没有记录/,
  '自然空日必须使用明确空状态文案');
assert.match(cognitiveSource,
  /const dayCount = cognitiveDemoState\.fixture\?\.window\?\.days[\s\S]*title: `最近 \$\{dayCount\} 天`/,
  '记录轨迹抽屉标题必须从 fixture 显示完整时间范围');
assert.match(cognitiveSource,
  /function cognitiveDemoHistoryMarkup[\s\S]*const totalRecords = cognitiveDemoState\.fixture\?\.stats\?\.totalRecords[\s\S]*\$\{totalRecords\} 条记录/,
  '记录轨迹正文必须显示完整记录数');
const portraitDrawerSource = cognitiveSource.slice(
  cognitiveSource.indexOf('function cognitiveDemoPortraitMarkup'),
  cognitiveSource.indexOf('function cognitiveDemoHistoryMarkup')
);
assert.match(portraitDrawerSource, /data-cognitive-portrait-drawer-id=/,
  '第三层抽屉中的每条理解必须拥有可定位身份');
assert.match(portraitDrawerSource, /tabindex="-1"/,
  '目标理解必须允许程序化聚焦，同时不增加 Tab 顺序噪声');
assert.match(portraitDrawerSource, /item\.id === selectedIdentifier \? ' aria-current="true"'/,
  '从单条理解进入时，抽屉必须标记对应当前项');
assert.match(portraitDrawerSource,
  /class="cognitive-portrait-maturity" data-portrait-maturity=/,
  '第三层抽屉必须显示与首页一致的成熟度标签');
const drawerOpenSource = cognitiveSource.slice(
  cognitiveSource.indexOf('function openCognitiveChainDrawer'),
  cognitiveSource.indexOf('function closeCognitiveChainDrawer')
);
assert.match(drawerOpenSource, /drawerBody\.scrollTop = 0/,
  '从“展开全部”进入时必须从列表起始位置阅读');
assert.match(drawerOpenSource,
  /identifier !== 'current'[\s\S]*data-cognitive-portrait-drawer-id[\s\S]*portraitTarget\.focus\(\{ preventScroll: true \}\)[\s\S]*portraitTarget\.scrollIntoView\(\{ block: 'center' \}\)[\s\S]*cueCognitiveDrawerPortraitTarget\(portraitTarget\)/,
  '从单条理解进入时必须聚焦、滚动并提示对应抽屉条目');
assert.match(cognitiveSource,
  /function cueCognitiveDrawerPortraitTarget[\s\S]*is-cognitive-entry-cued[\s\S]*animationend[\s\S]*setTimeout\(clearCue, 1500\)/,
  '抽屉定位提示必须播放一次并在有限时间内自动清理');
assert.match(css,
  /\.cognitive-demo-portrait-card\.is-cognitive-entry-cued::before[\s\S]*animation: cognitive-drawer-entry-cue/,
  '被定位的长期理解必须拥有克制的一次性视觉提示');
assert.match(css,
  /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.cognitive-demo-portrait-card\.is-cognitive-entry-cued::before[\s\S]*animation: none/,
  '抽屉定位提示必须尊重减少动态效果偏好');
assert.match(js, /enterCognitiveDemo\(\);/,
  '唯一版本启动时应直接加载固定认知数据');
assert.doesNotMatch(js,
  /cognitiveDemoRequested|COGNITIVE_DEMO_MODE_KEY|COGNITIVE_DEMO_RECORDS_KEY|installed-demo-v1/,
  '唯一版本不得再保留独立 Demo 模式或本地模式开关');
assert.doesNotMatch(html,
  /id="cognitive-demo-(?:enter|exit|connect|capture|controls)"/,
  '唯一版本不得暴露 Demo 进入、退出或连接控件');

const landscapeRenderSource = cognitiveSource.slice(
  cognitiveSource.indexOf('function renderCognitiveLandscape'),
  cognitiveSource.indexOf('function cognitiveMapHoverContext')
);
const fixedEdgeMarkup = landscapeRenderSource.slice(
  landscapeRenderSource.indexOf('if (cognitiveDemoState.active)', landscapeRenderSource.indexOf('const edgeMarkup')),
  landscapeRenderSource.indexOf('\n    return `<g class="cognitive-edge',
    landscapeRenderSource.indexOf('if (cognitiveDemoState.active)', landscapeRenderSource.indexOf('const edgeMarkup')))
);
assert.match(fixedEdgeMarkup, /aria-hidden="true"/);
assert.doesNotMatch(fixedEdgeMarkup, /role="button"|tabindex="0"|data-cognitive-entity=|cognitive-edge-hit/,
  '关系线只作为主题阅读反馈，不独立成为按钮或抽屉支路');
assert.match(fixedEdgeMarkup, /is-default-relation[^:]*: 'is-evidence-relation'/,
  '地图必须区分默认主题关系与悬停后展示的证据支线');
const fixedNodeStart = landscapeRenderSource.indexOf(
  'if (cognitiveDemoState.active)', landscapeRenderSource.indexOf('const nodeMarkup')
);
const fixedNodeMarkup = landscapeRenderSource.slice(
  fixedNodeStart,
  landscapeRenderSource.indexOf('\n    return `<g class="cognitive-node', fixedNodeStart)
);
assert.match(fixedNodeMarkup, /return ''/,
  '固定版的正式记忆点已由次峰承载，不得再画一层重复点云');
assert.match(landscapeRenderSource,
  /role="button" tabindex="0" aria-pressed="false"[\s\S]*data-cognitive-entity="peak"[\s\S]*cognitive-peak-hit[\s\S]*cognitive-peak-summit/,
  '聚合主题必须可聚焦、可固定，并拥有不改变图面的点击热区');
assert.match(landscapeRenderSource,
  /const displayEdges = landscape\.edges;/,
  '地图必须保留主题到记忆点的支持关系，以便在悬停时显示');
assert.match(landscapeRenderSource,
  /const insightRangeMarkup = cognitiveInsightRangeMarkup\(positions\);/,
  '地图必须从当前主题坐标生成第三层山系投影');
assert.match(landscapeRenderSource,
  /class="cognitive-map-insight-ranges">\$\{insightRangeMarkup\}<\/g>/,
  '第三层山系必须位于地形与关系线之间，且不覆盖主题交互');
const insightRangeSource = cognitiveSource.slice(
  cognitiveSource.indexOf('function cognitiveInsightConvexHull'),
  cognitiveSource.indexOf('function renderCognitiveLandscape')
);
assert.match(insightRangeSource, /data-cognitive-insight-range=/,
  '每条深层理解必须拥有独立的无文字山系图层');
assert.match(insightRangeSource, /peaks\.length < 2/,
  '山系几何必须遵守至少两个主题的材料门');
assert.doesNotMatch(insightRangeSource, /<text|item\.title|item\.statement/,
  '第三层山系不得把理解标题或陈述塞进地图');
assert.match(cognitiveSource,
  /cognitiveDemoState\.active[\s\S]*entity\.closest\('#cognitive-landscape-map'\)[\s\S]*entity\.dataset\.cognitiveEntity !== 'peak'/,
  '地图第一轮交互只开放主题山峰，记忆点和关系线继续作为证据视觉层');
assert.match(js,
  /const cognitiveMapInteractionState = \{[\s\S]*pinnedKind:[\s\S]*pinnedId:[\s\S]*insightId:/,
  '地图必须分别保存主题固定与深层理解固定状态');
assert.match(cognitiveSource,
  /function toggleCognitiveMapPin[\s\S]*clearCognitiveMapPin\(\)[\s\S]*applyCognitiveMapPinnedContext\(\)/,
  '再次点击同一主题必须退出固定，其他主题则更新固定上下文');
assert.match(cognitiveSource,
  /event\.target\.closest\('#cognitive-map-region'\)[\s\S]*clearCognitiveMapPin\(\)[\s\S]*clearCognitiveInsightMapFocus\(\)/,
  '点击地图空白处必须退出主题或深层理解固定');
assert.match(cognitiveSource,
  /event\.key === 'Escape' && cognitiveMapInteractionState\.pinnedId[\s\S]*clearCognitiveMapPin\(\)/,
  'Esc 必须能退出主题固定');
assert.match(cognitiveSource,
  /event\.key === 'Escape' && cognitiveMapInteractionState\.insightId[\s\S]*clearCognitiveInsightMapFocus\(\)/,
  'Esc 必须能退出深层理解山系');

for (const [field, pattern] of [
  ['content_types', /cognitiveCheckboxes\('content_types'/],
  ['topics', /name="topics"|elements\.topics/],
  ['objects', /name="objects"|elements\.objects/],
  ['stance', /name="stance"|elements\.stance/],
  ['cognitive_state', /name="cognitive_state"|elements\.cognitive_state/],
  ['purposes', /cognitiveCheckboxes\('purposes'|input\[name="purposes"\]/],
  ['statement', /name="statement"|elements\.statement/],
  ['type', /name="type"|elements\.type/],
]) {
  assert.match(cognitiveSource, pattern, `编辑表单必须保留 ${field}`);
}

const submitStart = cognitiveSource.indexOf('async function submitCognitiveUserAction');
const submitEnd = cognitiveSource.indexOf('function setCognitiveSecondaryExpanded', submitStart);
assert.ok(submitStart >= 0 && submitEnd > submitStart, 'cognitive action 提交边界必须可定位');
const submitSource = cognitiveSource.slice(submitStart, submitEnd);
for (const gate of [
  'ensureWritePermission(context.handle)',
  'archiveContextMatchesPersisted(context)',
  'readCognitiveActionWatermark(context.handle, library)',
  'cognitiveActionTargetStillCurrent(context.handle, target.ref, library)',
]) {
  assert.match(submitSource, new RegExp(gate.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')),
    `写入前必须通过 ${gate}`);
}
const createInboxAt = submitSource.indexOf('nestedDirectory(context.handle, COGNITIVE_ACTION_PATH, true)');
assert.ok(createInboxAt > submitSource.indexOf('ensureWritePermission(context.handle)'),
  '未授权时不得创建 user-actions 目录');
assert.ok(createInboxAt > submitSource.indexOf('cognitiveActionTargetStillCurrent'),
  'target CAS 复核前不得创建 user-actions 目录');
assert.match(submitSource, /cognitiveActionFileName\(userAction\.id\)/);
assert.doesNotMatch(submitSource, /COGNITIVE_(?:RECEIPT|MEMORY|RELATION)_REVISION_PATH\s*,\s*true/,
  '主页只能追加 action，不得直接创建正式 revision');
assert.match(cognitiveSource, /cognitiveActionResultFileName\(pending\.action\.id\)/);
assert.match(cognitiveSource, /result\.action_sha256 !== pending\.actionSha256/);
assert.match(cognitiveSource, /当前内容仍是上一个已校验版本/,
  '动作只写入时不得伪称已应用');
assert.match(cognitiveSource, /result\.status === 'applied'/);
assert.match(cognitiveSource, /result\.status === 'conflict'/);
assert.match(cognitiveSource, /function submitCognitiveManualDayRequest/);
assert.match(cognitiveSource, /buildManualDayRequest\(\{/);
assert.match(cognitiveSource, /newSelfReflectionId\('cman'\)/);
assert.match(cognitiveSource, /COGNITIVE_MANUAL_DAY_REQUEST_PATH/);
assert.match(cognitiveSource, /COGNITIVE_MANUAL_DAY_RESULT_PATH/);
assert.match(cognitiveSource, /validateManualDayResult\(resultFile\.value\)/);
assert.match(cognitiveSource, /result\.request_sha256 !== pending\.requestSha256/);
assert.match(cognitiveSource, /result\.request_id === pending\.request\.id/);
assert.match(cognitiveSource, /result\.local_date !== pending\.request\.local_date/);
for (const status of [
  'completed', 'master_gate_disabled', 'rejected_date', 'runner_failed',
  'committed_with_warnings', 'no_change', 'no_candidate', 'no_records',
  'no_receipts', 'stale', 'error', 'budget_exhausted',
]) {
  assert.ok(cognitiveSource.includes(status), `manual day UI 必须覆盖 ${status}`);
}
assert.match(cognitiveSource, /当前地景仍是上一份已校验结果/,
  'pending 请求不得误报为已应用');

const manualSubmitStart = cognitiveSource.indexOf('async function submitCognitiveManualDayRequest');
const manualSubmitEnd = cognitiveSource.indexOf('function setCognitiveSecondaryExpanded', manualSubmitStart);
assert.ok(manualSubmitStart >= 0 && manualSubmitEnd > manualSubmitStart,
  'manual day 提交边界必须可定位');
const manualSubmitSource = cognitiveSource.slice(manualSubmitStart, manualSubmitEnd);
for (const gate of [
  'ensureWritePermission(context.handle)',
  'archiveContextMatchesPersisted(context)',
  'request.local_date !== getLocalDate()',
]) {
  assert.match(manualSubmitSource, new RegExp(gate.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')),
    `manual day 写入前必须通过 ${gate}`);
}
const createManualAt = manualSubmitSource.indexOf(
  'nestedDirectory(\n        context.handle, COGNITIVE_MANUAL_DAY_REQUEST_PATH, true'
);
assert.ok(createManualAt > manualSubmitSource.indexOf('ensureWritePermission(context.handle)'),
  '未授权时不得创建 manual-day-requests 目录');
assert.ok(createManualAt > manualSubmitSource.indexOf('archiveContextMatchesPersisted(context)'),
  '目录身份校验前不得创建 manual-day-requests 目录');
assert.match(manualSubmitSource, /manualDayRequestFileName\(request\.id\)/);
assert.match(manualSubmitSource, /if \(writeResult\.unchanged\) throw new Error/,
  'manual day request 必须是新的不可变文件');
assert.doesNotMatch(manualSubmitSource, /fetch\(|DeepSeek|provider|exec|spawn|CLI/,
  '归并入口只能写本地请求，不得直接调用 provider 或 CLI');
assert.match(cognitiveSource, /form\.querySelector\('textarea, input, select'\)\?\.focus\(\)/,
  '打开编辑表单后必须移入焦点');
assert.match(cognitiveSource, /toggleButton\?\.focus\(\)/,
  '取消编辑后必须恢复焦点');
assert.doesNotMatch(cognitiveSource, /\[猜测\]/,
  '生产 UI 不得出现内部证据标签');

for (const keyboard of ["event.key === 'Enter'", "event.key === ' '", "event.key === 'Escape'", "event.key !== 'Tab'"]) {
  assert.ok(cognitiveSource.includes(keyboard), `必须覆盖键盘路径 ${keyboard}`);
}
assert.match(cognitiveSource, /document\.getElementById\('app'\)\.inert = true/);
assert.match(cognitiveSource, /drawer\.setAttribute\('aria-hidden', 'false'\)/);
assert.match(cognitiveSource, /previousTrigger instanceof Element && previousTrigger\.isConnected/,
  '仍连接的 HTML 或 SVG 原触发器都必须可恢复焦点');
assert.match(cognitiveSource, /liveTrigger instanceof Element[\s\S]*typeof liveTrigger\.focus === 'function'/,
  'SVG 山峰必须纳入焦点恢复');
assert.match(cognitiveSource, /data-cognitive-entity\]\[data-cognitive-id/,
  '抽屉关闭时必须能按对象身份重新找到实时节点');
assert.match(cognitiveSource,
  /button:not\(\[disabled\]\), input:not\(\[disabled\]\), select:not\(\[disabled\]\), textarea:not\(\[disabled\]\), summary, \[href\]/,
  '统一抽屉的焦点循环必须覆盖第三层理解中的表单、详情与链接');
assert.doesNotMatch(html, /<title id="cognitive-map-title"/,
  'SVG title 会被 Chrome 显示为原生黑色悬浮框，不得保留');
assert.doesNotMatch(cognitiveSource, /<title id="cognitive-map-title"/);
assert.doesNotMatch(cognitiveSource, /<title(?:\s|>)/,
  '动态 SVG 不得生成 Chrome 原生黑色悬浮框');
assert.match(html, /aria-labelledby="cognitive-landscape-title cognitive-map-desc"/);
assert.ok(
  cognitiveSource.includes('位置仅用于排版；只有连线表示已确认关系；高度只表示证据积累，不表示重要程度或真实性。'),
  '地景边界必须保留在 SVG 无障碍描述中，不应作为常驻说明覆盖地图'
);
assert.doesNotMatch(html, /class="cognitive-map-boundary"|id="cognitive-map-help"|class="cognitive-map-legend"/,
  '地图内不得保留解释性说明、操作教程或常驻图例');
assert.doesNotMatch(js, /主题地图保持当前版本/,
  '固定版今日标题只保留事实数量，不显示版本解释');
assert.match(js, /if \(cognitiveDemoState\.active && !cognitiveUsingLiveBackend\(\)\) \{\s*return \{ text: '', tone: '' \};\s*\}/,
  '固定版不显示常驻投影说明条，真实运行时仍需提示快照日期');
assert.doesNotMatch(html, /位置只表达已经建立的关系/,
  '不得把稳定排版坐标误写成关系语义');
assert.doesNotMatch(css, /\.cognitive-peak(?::[^,{ ]+)?\s*\{[^}]*transform\s*:/s,
  '峰组不得用 CSS transform 覆盖 SVG 坐标');

for (const selector of [
  '.cognitive-landscape-section', '.cognitive-record-row', '.cognitive-chain-drawer',
  '.cognitive-contour', '.cognitive-edge-visible', '.cognitive-node-visible',
  '.cognitive-elevation-band', '.cognitive-atlas-grid', '.cognitive-peak-summit',
  '.cognitive-atlas-north', '.cognitive-atlas-scale', '.cognitive-atlas-legend',
  '.cognitive-drawer-original',
  '.cognitive-action-row', '.cognitive-edit-form', '.cognitive-action-status',
  '.cognitive-manual-day-status',
  '.cognitive-demo-day-record', '.cognitive-demo-portrait-card small',
  '.cognitive-map-stage', '.cognitive-map-controls', '.cognitive-map-record-star',
]) {
  assert.ok(css.includes(selector), `${selector} 必须有生产样式`);
}
assert.doesNotMatch(css, /\.cognitive-terrain-shadow|\.cognitive-terrain-ridge\s*\{/,
  '旧的整块偏移影和宽山脊涂抹层必须删除');
assert.doesNotMatch(css, /\.cognitive-map-shadows/,
  '旧的独立地形阴影组样式必须删除');
assert.doesNotMatch(cognitiveSource, /mask-type:luminance|cognitive-map-shadows/,
  'AO 不得使用独立填充遮罩或独立地形影层');
assert.doesNotMatch(cognitiveSource, /cognitive-contour-ao|--cog-contour-ao/,
  '旧的粗线位移伴影必须完整移除');
assert.doesNotMatch(cognitiveSource,
  /cognitiveTerrainLightState|function cognitiveTerrainSample|function cognitiveRenderTerrainLight|cognitive-terrain-relief-canvas/,
  '静态 MVP 不得保留鼠标光照画布和 AO 计算');
assert.match(cognitiveSource,
  /const bands = \[\][\s\S]*?fill-rule="evenodd"[\s\S]*?bands: bands\.join/,
  '高程色带必须由闭合等高线逐层叠加生成');
assert.match(cognitiveSource, /const contourCount = Math\.max\(20, Math\.min\(24,/,
  '二轮地图必须把等高线密度提升到至少 20 层');
assert.match(js, /function cognitiveAtlasGridStep[\s\S]*return 25;[\s\S]*return 50;[\s\S]*return 100;/,
  '网格间距必须随缩放层级切换');
assert.match(css, /--cog-blue-soft: #e7eef2/);
assert.match(css,
  /\.cognitive-overview-grid \{[\s\S]*?grid-template-columns: minmax\(0, 1fr\) clamp\(440px, 30vw, 620px\);[\s\S]*?height: clamp\(480px, calc\(100svh - 318px\), 620px\);/,
  '桌面首屏必须为地形保留伸展空间，并给深层理解稳定的主角宽度');
assert.match(css,
  /\.cognitive-portrait-section \{[\s\S]*?grid-template-rows: auto minmax\(0, 1fr\) auto;[\s\S]*?border-left: 1px solid/,
  '第三层理解必须作为常驻右栏主角，与地景共用首屏高度');
assert.match(evidenceRiverCss,
  /\.cognitive-today-section \{[\s\S]*?grid-template-rows: 58px auto;[\s\S]*?min-height: 230px;[\s\S]*?overflow: visible;/,
  '时间河必须容纳完整证据内容，不得使用会裁掉末行的硬高度');
assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
assert.match(css,
  /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.cognitive-map-stage \{ transform: none; \}/,
  '减少动态偏好必须关闭 2.5D 倾斜');
assert.match(css,
  /\.cognitive-map-region\.is-cognitive-map-fullscreen \{[\s\S]*?position: fixed;[\s\S]*?inset: 0;/,
  '全屏必须用同一张地图覆盖视口，不得露出主页中间层');
assert.match(css,
  /\.cognitive-map-region\.is-cognitive-map-fullscreen \{[\s\S]*?background-color: var\(--cog-atlas-paper\);/,
  '全屏画布自身必须是不透明底层');
assert.match(css,
  /html\.cognitive-map-fullscreen \{[\s\S]*?overflow: hidden;[\s\S]*?scrollbar-gutter: auto;/,
  '全屏根节点必须释放稳定滚动条槽，地图才能覆盖最右像素');
assert.match(css,
  /\.cognitive-map-region \{[\s\S]*?user-select: none;/,
  '拖动地图不得产生浏览器文字选区或蓝色选择框');
assert.match(css,
  /\.cognitive-map-camera-controls \{[\s\S]*?display: flex;/,
  '缩放控件在主页和全屏都必须可用');
assert.match(css,
  /\.cognitive-map-stage \{[\s\S]*?transform: none;/,
  '鼠标景深不得移动主题命中层，避免悬停时反复闪动');
assert.match(css,
  /\.cognitive-map-edges,[\s\S]*?\.cognitive-map-peaks,[\s\S]*?\.cognitive-map-nodes \{[\s\S]*?transform: none;/,
  '关系、主题与点击点必须固定，仅地形底层允许视差');
assert.match(css,
  /\.cognitive-landscape-map \[tabindex="0"\]:focus,[\s\S]*?outline: none !important;/,
  'SVG 可聚焦组不得显示覆盖多个主题的浏览器矩形焦点框');
assert.match(evidenceRiverCss,
  /\.cognitive-today-section \.cognitive-record-list \{[\s\S]*?grid-auto-columns: minmax\(330px, 380px\);/,
  '今日记录必须使用更宽的单行卡片，保留正文呼吸空间');
assert.match(evidenceRiverCss,
  /\.cognitive-record-axis \{[\s\S]*?display: block;[\s\S]*?width: calc\(100% \+ 32px\);[\s\S]*?height: 42px;/,
  '时间轴头必须以紧凑块级高度隔开正文');
assert.match(cognitiveSource,
  /const COGNITIVE_RIVER_WIDTH_SCALE = \.58;[\s\S]*?edge \* point\[2\] \* COGNITIVE_RIVER_WIDTH_SCALE/,
  '河道必须整体变细，同时保留中心线的蜿蜒幅度');
assert.match(css,
  /\.cognitive-portrait-head \{[\s\S]*?align-items: center;[\s\S]*?min-height: 72px;/,
  '她理解的我、计数与更新设置必须压缩为同一行');
assert.match(css,
  /\.cognitive-portrait-section::before \{[\s\S]*?top: 96px;[\s\S]*?right: -36px;/,
  '正文水印必须离开标题栏的文字安全区');
assert.match(css,
  /\.cognitive-portrait-head \{[\s\S]*?background:[\s\S]*?#f8f9f6;/,
  '她理解的我标题栏必须使用完整表面遮住正文水印');
assert.match(css,
  /\.cognitive-portrait-head > \* \{[\s\S]*?z-index: 2;/,
  '标题、计数与更新控件必须稳定处于装饰层之上');
assert.match(css,
  /\.cognitive-portrait-update-switch input:checked \+ \.cognitive-portrait-update-track/,
  '自动更新开关必须拥有清晰但克制的开启状态');
assert.match(css,
  /\.cognitive-portrait-head::before \{[\s\S]*?repeating-radial-gradient[\s\S]*?animation: cognitive-portrait-head-breathe/,
  '理解栏应使用低对比的动态轨道场');
assert.match(css,
  /\.cognitive-portrait-head::after \{[\s\S]*?animation: cognitive-portrait-head-sweep/,
  '理解栏应具有缓慢的时间扫过效果');
assert.match(css,
  /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.cognitive-portrait-head::before,[\s\S]*?\.cognitive-portrait-head::after \{[\s\S]*?animation: none !important;/,
  '标题栏动效必须遵守 reduced-motion');
assert.doesNotMatch(css,
  /\.cognitive-map-region \{[\s\S]*?background-size: 36px 36px/,
  '认知地图不得回退为工程化方格纸背景');
assert.match(css, /\.cognitive-map-empty\[hidden\]\s*\{[^}]*display:\s*none;/s,
  '隐藏的地景空态不得以透明覆盖层拦截主题点击');
assert.match(css, /has-cognitive-hover \.cognitive-edge\.is-hover-related/);
assert.match(css,
  /\.cognitive-edge\.is-evidence-relation \.cognitive-edge-visible \{[\s\S]*?stroke-opacity:\s*0;/,
  '证据支线必须默认隐藏，保持地形图的初始纯净度');
assert.match(css,
  /has-cognitive-hover[\s\S]*is-evidence-relation\.is-hover-related[\s\S]*has-cognitive-pin[\s\S]*is-evidence-relation\.is-pinned-related/,
  '证据支线必须同时支持悬停预览和点击固定');
assert.match(css,
  /has-cognitive-pin \.cognitive-peak\.is-pinned \.cognitive-peak-summit/,
  '已固定的主题必须在山峰图例上给出明确反馈');
assert.match(css,
  /\.cognitive-insight-range \{[\s\S]*?opacity:\s*0;[\s\S]*?has-cognitive-insight[\s\S]*?is-cognitive-insight-active[\s\S]*?opacity:\s*1;/,
  '第三层山系必须默认隐藏，并仅在主动选择后显影');
assert.match(css,
  /has-cognitive-insight[\s\S]*?is-evidence-relation \.cognitive-edge-visible \{[\s\S]*?stroke-opacity:\s*0;/,
  '第三层阅读态不得顺带展开第二层证据支线');
assert.match(css,
  /\.cognitive-portrait-feature\.is-cognitive-selected,[\s\S]*?\.cognitive-portrait-item\.is-cognitive-selected/,
  '被选中的深层理解必须保留克制的来源锚点');
assert.doesNotMatch(css,
  /has-cognitive-hover \.cognitive-peak,[\s\S]{0,180}?opacity:\s*\.28/,
  '悬停主题不得把整张地图的其他主题统一压暗');
assert.match(css,
  /\.cognitive-topbar-actions button \{[\s\S]*?min-height: 36px;/,
  '记录轨迹必须保留足够的顶栏点击高度');
assert.match(css,
  /\.cognitive-portrait-section\.has-cognitive-link-focus[\s\S]*?\.is-cognitive-related/,
  '地图主题与第三层理解的双向聚焦必须拥有明确视觉反馈');
assert.match(css,
  /@media \(max-width: 900px\)[\s\S]*?\.cognitive-overview-grid \{[\s\S]*?display: block;[\s\S]*?\.cognitive-portrait-section \{[\s\S]*?min-height: 430px;/,
  '中窄屏必须将地景与第三层理解顺序展开，保持各自可读高度');
assert.match(evidenceRiverCss,
  /@media \(max-width: 600px\)[\s\S]*?\.cognitive-today-section \{[\s\S]*?grid-template-rows: auto 42px auto;/,
  '手机宽度的记录区必须重排为标题、状态、横向记录三层');
assert.match(evidenceRiverCss,
  /@media \(max-width: 600px\)[\s\S]*?\.cognitive-today-section \.cognitive-record-list \{[\s\S]*?grid-auto-columns: minmax\(280px, 85vw\);/,
  '手机宽度必须保留横向记录浏览，并限制单条宽度');

const portraitUpdateSource = cognitiveSource.slice(
  cognitiveSource.indexOf('function syncCognitivePortraitUpdateControls('),
  cognitiveSource.indexOf('function initCognitiveHomeInteractions()')
);
assert.match(portraitUpdateSource,
  /autoUpdate\.checked = Boolean\(hasSchedule && schedule\.enabled\)/,
  '自动更新开关必须显示 Backend V2 投影中的真实计划状态');
assert.match(portraitUpdateSource,
  /schedule\.hour[\s\S]*schedule\.minute[\s\S]*updateAt\.value/,
  '更新时间必须来自同一份后端计划投影');
assert.match(portraitUpdateSource,
  /cognitiveBackendState\.runtimeSettings\?\.schedule \|\| home\?\.schedule/,
  '在线页面必须优先复读本地 Runtime 的持久化计划设置');
assert.match(portraitUpdateSource,
  /runtimeTransport\?\.updateRuntimeSettings[\s\S]*autoUpdate\.disabled = !writable/,
  '只有已连接的可写 Backend V2 才能启用计划开关');
assert.match(portraitUpdateSource,
  /addEventListener\('change'[\s\S]*updateRuntimeSettings\(\{[\s\S]*schedule: \{ enabled: requested \}/,
  '计划开关必须通过后端设置接口持久化');
assert.doesNotMatch(portraitUpdateSource,
  /localStorage|indexedDB|writeContext|Provider|DeepSeek/,
  '计划设置不得建立浏览器私有真相或触发模型');
assert.match(cognitiveSource,
  /const home = cognitiveHomeState\.home;[\s\S]{0,160}?syncCognitivePortraitUpdateControls\(home\)/,
  '每次真实投影重绘都必须恢复后端日级计划状态');
assert.match(js, /matchMedia\('\(max-width: 600px\)'\)/,
  '页面必须监听手机宽度断点');
assert.match(cognitiveSource,
  /const mapVisible = cognitiveHomeState\.activeView === 'map' && !compact/,
  '手机宽度必须切换到可读列表，避免缩小地图文字');

const dailyInitSource = js.slice(
  js.indexOf('function initDailySummaries()'),
  js.indexOf('// =============================================================', js.indexOf('function initDailySummaries()'))
);
assert.match(dailyInitSource,
  /if \(cognitiveDemoState\.active\) \{[\s\S]*daily-summary-tab'\)\.hidden = true;[\s\S]*return;/,
  '固定版不得重新初始化旧每日总结与照片视图');
const broadcastSource = js.slice(
  js.indexOf('if (coreRefreshChannel) {\n  coreRefreshChannel.onmessage'),
  js.indexOf('async function reloadPersistedSelectionAfterBroadcast')
);
assert.match(broadcastSource,
  /coreRefreshChannel\.onmessage = event => \{\s*if \(cognitiveDemoState\.active\) return;/,
  '固定版必须忽略 live BroadcastChannel 回流');

console.log('cognitive home dashboard wiring tests passed');
