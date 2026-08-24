// Memento · Chrome 新标签页 Dashboard
// - 今日记录摘要 (不设置完成态,不催促清理)
// - 大号"复制今天 → AI"按钮 (clipboard API)
// - Entry 列表 (默认展示全部记录,chip 切换)
// - 统计 + 90 天记录热力图
// - 每日总结 (当天第一帧 + Daily Review + 运行状态)
//
// 本文件不依赖任何外部库,内含一个极简 markdown 渲染器 (paragraphs + code + list)。
// 注: 内部技术目录名仍为 AISecretary (沿用旧名),Memento 是后改的产品名。

// =============================================================
// 0. IndexedDB · 存放 directoryHandle
// =============================================================

const DB_NAME = 'aisecretary';
const STORE = 'handles';
const HANDLE_KEY = 'dir';
const STORAGE_OPERATION_TIMEOUT_MS = 8000;
const CACHE_FIRST_DECISION_MS = 250;
const CACHE_CONTEXT_GRACE_MS = 250;

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => {
      const db = req.result;
      db.onversionchange = () => db.close();
      resolve(db);
    };
    req.onerror = () => reject(req.error);
  });
}

async function saveHandle(handle) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).put(handle, HANDLE_KEY);
    tx.oncomplete = () => {
      db.close();
      resolve();
    };
    const fail = () => {
      db.close();
      reject(tx.error || new Error('无法保存目录授权记录'));
    };
    tx.onerror = fail;
    tx.onabort = fail;
  });
}

async function loadHandle() {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly');
    const req = tx.objectStore(STORE).get(HANDLE_KEY);
    let handle = null;
    req.onsuccess = () => { handle = req.result || null; };
    tx.oncomplete = () => {
      db.close();
      resolve(handle);
    };
    const fail = () => {
      db.close();
      const requestError = req.readyState === 'done' ? req.error : null;
      reject(requestError || tx.error || new Error('无法读取目录授权记录'));
    };
    req.onerror = fail;
    tx.onerror = fail;
    tx.onabort = fail;
  });
}

const dashboardCacheRepository = window.MementoDashboardCache
  ? window.MementoDashboardCache.createRepository({ openDB })
  : null;
const photoThumbnailCacheRepository = window.MementoPhotoCache
  ? window.MementoPhotoCache.createRepository()
  : null;
const CORE_REFRESH_CHANNEL_NAME = 'memento.dashboard.core-refresh.events.v1';

async function invalidateFastStartCache(handle, suppliedContextPromise = null) {
  if (!dashboardCacheRepository || !handle) {
    return { invalidated: false, reason: 'missing-context' };
  }

  let context = null;
  if (suppliedContextPromise) {
    const suppliedContext = await suppliedContextPromise;
    if (suppliedContext && suppliedContext.handle) {
      try {
        if (await handle.isSameEntry(suppliedContext.handle)) context = suppliedContext;
      } catch {
        // Fall through to a fresh, handle-checked lookup below.
      }
    }
  }
  if (!context) {
    const bootstrap = await dashboardCacheRepository.readBootstrap();
    context = await dashboardCacheRepository.resolveBootstrap(handle, bootstrap);
  }
  if (!context || !context.binding) {
    return { invalidated: false, reason: context?.reason || 'missing-binding' };
  }
  return dashboardCacheRepository.invalidateCurrent(context.binding.token);
}

async function persistBrowserStorage() {
  try {
    if (navigator.storage && navigator.storage.persist) await navigator.storage.persist();
  } catch (error) {
    // 目录句柄已经写入 IndexedDB;持久化存储申请失败不应阻止本次使用。
    console.warn('无法申请持久化浏览器存储', error);
  }
}

// =============================================================
// 1. File System Access · 授权 + 读文件
// =============================================================

async function queryRead(handle) {
  return handle.queryPermission({ mode: 'read' });
}
async function requestRead(handle) {
  return handle.requestPermission({ mode: 'read' });
}
async function pickFolder() {
  return window.showDirectoryPicker({ mode: 'read' });
}
async function persistSelectedDirectoryHandle(handle, preparedSelection = null, onEventuallyPersisted = null) {
  const startPersistence = () => preparedSelection
    ? preparedSelection.startPersistence()
    : saveHandle(handle);
  const operations = window.MementoDashboardOperations;
  // Directory selection and archive writes change the meaning or contents of
  // the same user-owned directory. Serialize their commit boundaries so an
  // archive mutation can verify the persisted selection without a TOCTOU gap.
  const persistence = navigator.locks
      && typeof navigator.locks.request === 'function'
      && operations
      && typeof operations.withArchiveMutationLock === 'function'
    ? operations.withArchiveMutationLock(navigator.locks, startPersistence)
    : startPersistence();
  if (typeof onEventuallyPersisted === 'function') {
    void Promise.resolve(persistence).then(onEventuallyPersisted, () => undefined);
  }
  const access = window.MementoDirectoryAccess;
  if (access && access.withTimeout) {
    await access.withTimeout(
      () => persistence,
      STORAGE_OPERATION_TIMEOUT_MS,
      '保存浏览器授权记录'
    );
  } else {
    await persistence;
  }
  void persistBrowserStorage();
}
async function listMarkdownFiles(dirHandle, options = {}) {
  if (!window.MementoDashboardOperations) throw new Error('Dashboard 文件操作模块未加载');
  return window.MementoDashboardOperations.readMarkdownFiles(dirHandle, {
    ...options,
    todayDate: options.todayDate || getLocalDate(),
    onProgress: detail => {
      if (!options.isCurrent || options.isCurrent()) {
        const total = detail.discoveredCount ? ` / ${detail.discoveredCount}` : '';
        setStatus(`正在并行读取每日记录…已完成 ${detail.count}${total} 个文件`);
      }
      if (typeof options.onProgress === 'function') options.onProgress(detail);
    },
  });
}

const OPTIONAL_FILE_READ_CONCURRENCY = 3;
const optionalReadQueue = [];
let optionalReadActive = 0;

function pumpOptionalReadQueue() {
  while (optionalReadActive < OPTIONAL_FILE_READ_CONCURRENCY && optionalReadQueue.length) {
    const queued = optionalReadQueue.shift();
    if (!queued.shouldStart()) {
      queued.resolve({ skipped: true });
      continue;
    }
    optionalReadActive++;
    Promise.resolve()
      .then(queued.task)
      .then(queued.resolve, error => queued.resolve({ error }))
      .finally(() => {
        optionalReadActive--;
        pumpOptionalReadQueue();
      });
  }
}

function scheduleOptionalRead(task, shouldStart) {
  return new Promise(resolve => {
    optionalReadQueue.push({ task, shouldStart, resolve });
    pumpOptionalReadQueue();
  });
}

function optionalReadCurrent(options = {}) {
  const generationCurrent = typeof options.isCurrent !== 'function' || options.isCurrent();
  return generationCurrent
    && (!options.coordinator || options.coordinator.canContinue());
}

function staleOptionalFiles(files = []) {
  return { files, issue: '', stale: true };
}

function createOptionalReadCoordinator(options = {}) {
  let fatalError = null;
  const generationCurrent = () => typeof options.isCurrent !== 'function' || options.isCurrent();

  return {
    canContinue: () => !fatalError && generationCurrent(),
    fatalError: () => fatalError,
    fail(error) {
      if (!fatalError) fatalError = error;
      pumpOptionalReadQueue();
    },
    schedule(task) {
      return scheduleOptionalRead(async () => {
        try {
          return await task();
        } catch (error) {
          if (error && (error.name === 'NotAllowedError' || error.name === 'SecurityError')) {
            fatalError = error;
          }
          throw error;
        }
      }, () => !fatalError && generationCurrent());
    },
  };
}

async function listOptionalTextFiles(dirHandle, options) {
  const entries = [];
  let scanIssue = '';

  try {
    const iterator = dirHandle.entries()[Symbol.asyncIterator]();
    while (optionalReadCurrent(options)) {
      const next = await iterator.next();
      if (!optionalReadCurrent(options)) return staleOptionalFiles();
      if (next.done) break;
      const [name, entry] = next.value;
      if (entry.kind !== 'file' || !options.namePattern.test(name)) continue;
      const date = name.replace(options.extensionPattern, '');
      if (options.datePrefix && !date.startsWith(options.datePrefix)) continue;
      entries.push({ name, date, entry });
    }
  } catch (error) {
    if (error && (error.name === 'NotAllowedError' || error.name === 'SecurityError')) {
      options.coordinator?.fail(error);
      throw error;
    }
    scanIssue = fileReadIssue(error, options.scanIssue);
  }

  if (!optionalReadCurrent(options)) return staleOptionalFiles();

  const coordinator = options.coordinator || createOptionalReadCoordinator(options);
  const taskOptions = options.coordinator ? options : { ...options, coordinator };
  const results = await Promise.all(entries.map(({ name, date, entry }) =>
    coordinator.schedule(async () => {
      if (!optionalReadCurrent(taskOptions)) return { skipped: true };
      try {
        const file = await entry.getFile();
        if (!optionalReadCurrent(taskOptions)) return { skipped: true };
        const text = await file.text();
        if (!optionalReadCurrent(taskOptions)) return { skipped: true };
        const record = { name, date, mtime: file.lastModified, text, readIssue: '' };
        if (typeof options.onFile === 'function') {
          try {
            options.onFile(record);
          } catch (error) {
            console.warn('无法渐进更新每日总结文件', error);
          }
        }
        return { file: record };
      } catch (error) {
        if (error && (error.name === 'NotAllowedError' || error.name === 'SecurityError')) {
          throw error;
        }
        return {
          file: {
            name,
            date,
            mtime: 0,
            text: '',
            readIssue: fileReadIssue(error, options.fileIssue),
          },
        };
      }
    })
  ));
  if (coordinator.fatalError()) throw coordinator.fatalError();
  const completed = results.map(result => result.file).filter(Boolean)
    .sort((a, b) => b.date.localeCompare(a.date));
  if (!optionalReadCurrent(taskOptions)) return staleOptionalFiles(completed);
  return { files: completed, issue: scanIssue };
}

async function readOptionalDashboardData(handle, options = {}) {
  // These are optional enhancements. Read them after the main records, but
  // start all three together and still wait for every physical request to
  // settle. File System Access promises cannot be cancelled, so a bare
  // Promise.all rejection would leave hidden work running behind a retry.
  const coordinator = createOptionalReadCoordinator(options);
  const coordinatedOptions = { ...options, coordinator };
  const settled = await Promise.allSettled([
    listDailyReviewFiles(handle, coordinatedOptions),
    listDailyReviewStateFiles(handle, coordinatedOptions),
    readDailyReviewPrompt(handle, coordinatedOptions),
  ]);
  const permissionFailure = settled.find(result => result.status === 'rejected'
    && result.reason
    && (result.reason.name === 'NotAllowedError' || result.reason.name === 'SecurityError'));
  if (permissionFailure) throw permissionFailure.reason;
  const fallbacks = [
    { files: [], issue: '每日总结暂时无法读取。' },
    { files: [], issue: '总结运行状态暂时无法读取。' },
    { hash: '', issue: '当前总结 Prompt 暂时无法读取，现有总结不能判定为已更新。' },
  ];
  const [reviewResult, reviewStateResult, promptResult] = settled.map((result, index) =>
    result.status === 'fulfilled' ? result.value : fallbacks[index]
  );
  return { reviewResult, reviewStateResult, promptResult };
}

function fileReadIssue(error, fallback) {
  if (error && (error.name === 'NotAllowedError' || error.name === 'SecurityError')) return '访问总结目录的权限已失效';
  return fallback;
}

async function listDailyReviewFiles(rootHandle, options = {}) {
  if (!optionalReadCurrent(options)) return staleOptionalFiles();
  let dailyDir;
  try {
    const reviewsDir = await rootHandle.getDirectoryHandle('Reviews');
    if (!optionalReadCurrent(options)) return staleOptionalFiles();
    dailyDir = await reviewsDir.getDirectoryHandle('Daily');
  } catch (error) {
    if (error && error.name === 'NotFoundError') return { files: [], issue: '' };
    if (error && (error.name === 'NotAllowedError' || error.name === 'SecurityError')) {
      options.coordinator?.fail(error);
      throw error;
    }
    return {
      files: [],
      issue: fileReadIssue(error, '无法读取 Daily Review 目录'),
    };
  }
  if (!optionalReadCurrent(options)) return staleOptionalFiles();
  return listOptionalTextFiles(dailyDir, {
    ...options,
    namePattern: /^\d{4}-\d{2}-\d{2}\.md$/,
    extensionPattern: /\.md$/,
    fileIssue: '总结文件暂时无法读取',
    scanIssue: '无法继续读取 Daily Review 目录',
    onFile: options.onReviewFile,
  });
}

async function listDailyReviewStateFiles(rootHandle, options = {}) {
  if (!optionalReadCurrent(options)) return staleOptionalFiles();
  let statusDir;
  try {
    const reviewDir = await rootHandle.getDirectoryHandle('.review');
    if (!optionalReadCurrent(options)) return staleOptionalFiles();
    statusDir = await reviewDir.getDirectoryHandle('status');
  } catch (error) {
    if (error && error.name === 'NotFoundError') return { files: [], issue: '' };
    if (error && (error.name === 'NotAllowedError' || error.name === 'SecurityError')) {
      options.coordinator?.fail(error);
      throw error;
    }
    return {
      files: [],
      issue: fileReadIssue(error, '无法读取总结运行状态'),
    };
  }
  if (!optionalReadCurrent(options)) return staleOptionalFiles();
  return listOptionalTextFiles(statusDir, {
    ...options,
    namePattern: /^\d{4}-\d{2}-\d{2}\.json$/,
    extensionPattern: /\.json$/,
    fileIssue: '总结运行状态暂时无法读取',
    scanIssue: '无法继续读取总结运行状态',
    onFile: options.onReviewStateFile,
  });
}

async function readDailyReviewPrompt(rootHandle, options = {}) {
  const staleResult = { hash: '', issue: '', stale: true };
  if (!optionalReadCurrent(options)) return staleResult;
  try {
    const promptDir = await rootHandle.getDirectoryHandle('.chrome-newtab');
    if (!optionalReadCurrent(options)) return staleResult;
    const promptHandle = await promptDir.getFileHandle('prompts.js');
    if (!optionalReadCurrent(options)) return staleResult;
    const file = await promptHandle.getFile();
    if (!optionalReadCurrent(options)) return staleResult;
    const bytes = await file.arrayBuffer();
    if (!optionalReadCurrent(options)) return staleResult;
    const text = new TextDecoder().decode(bytes);
    // 与 daily-review/review_status.sh 的可用性检查保持一致；hash 覆盖整个文件。
    if (!text.includes("id: 'comprehensive'")) {
      return {
        hash: '',
        issue: '当前 .chrome-newtab/prompts.js 缺少 comprehensive Prompt，现有总结不能判定为已更新。',
      };
    }
    return { hash: await sha256Hex(bytes), issue: '' };
  } catch (error) {
    const missing = error && error.name === 'NotFoundError';
    const permission = error && (error.name === 'NotAllowedError' || error.name === 'SecurityError');
    if (permission) {
      options.coordinator?.fail(error);
      throw error;
    }
    return {
      hash: '',
      issue: missing
        ? '缺少 .chrome-newtab/prompts.js，现有总结不能判定为已更新。'
        : permission
          ? '当前总结 Prompt 因目录权限失效而无法读取，现有总结需要重新校验。'
          : '当前总结 Prompt 暂时无法读取，现有总结不能判定为已更新。',
    };
  }
}

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('');
}

async function buildSourceHashes(files) {
  // review_status.sh 以 `-s` 要求源记录非空；空文件不能拥有可用 freshness hash。
  const readableSources = (files || []).filter(file => file.bytes && file.bytes.byteLength > 0);
  const pairs = await Promise.all(readableSources.map(async file => [file.date, await sha256Hex(file.bytes)]));
  return Object.fromEntries(pairs);
}

function buildSourceDaySkeleton(files) {
  return Object.fromEntries((files || [])
    .filter(file => file && file.date && file.bytes && file.bytes.byteLength > 0)
    .map(file => [file.date, '']));
}

function sourceMockFromText(text) {
  // review_status.sh 比较源文件的原始行；CRLF 不应被浏览器悄悄解释成 mock。
  const lines = String(text || '').split('\n');
  if (lines[0] !== '---') return false;
  for (let index = 1; index < lines.length; index++) {
    if (lines[index] === '---') break;
    if (lines[index] === 'mock: true') return true;
  }
  return false;
}

function buildSourceMocks(files) {
  return Object.fromEntries((files || [])
    .filter(file => file.bytes && file.bytes.byteLength > 0)
    .map(file => [file.date, sourceMockFromText(file.text)]));
}

// =============================================================
// 2. Markdown parser
// =============================================================

const KNOWN_TAGS = new Set(['TODO', '灵感', '下次再读']);
const WEEKDAY_RE = /^周[一二三四五六日]$/;
const FRONTMATTER_RE = /^---\s*\n[\s\S]*?\n---\s*\n/;
const ENTRY_SPLIT_RE = /\n---\s*\n/;

function parseFile(text, date) {
  const body = text.replace(FRONTMATTER_RE, '');
  const blocks = body.split(ENTRY_SPLIT_RE).map(b => b.trim()).filter(Boolean);
  return blocks.map((block, idx) => parseEntry(block, date, idx)).filter(Boolean);
}

function parseEntry(block, date, index) {
  const lines = block.split('\n');
  const headingLineIdx = lines.findIndex(l => l.startsWith('## '));
  if (headingLineIdx < 0) return null;

  const heading = lines[headingLineIdx].replace(/^##\s+/, '').trim();
  const parts = heading.split(' · ').map(s => s.trim()).filter(Boolean);

  const time = parts[0] || '';
  let weekday = null, source = null, tag = null;

  for (let i = 1; i < parts.length; i++) {
    const p = parts[i];
    if (WEEKDAY_RE.test(p)) weekday = p;
    else if (p.startsWith('#')) tag = p.slice(1);
    else source = source ?? p;
  }

  let bodyLines = lines.slice(headingLineIdx + 1);
  while (bodyLines.length && !bodyLines[0].trim()) bodyLines.shift();
  while (bodyLines.length && !bodyLines[bodyLines.length - 1].trim()) bodyLines.pop();

  if (!source && bodyLines.length) {
    const last = bodyLines[bodyLines.length - 1];
    if (/^—\s+\S/.test(last)) {
      source = last.replace(/^—\s+/, '').trim();
      bodyLines.pop();
      while (bodyLines.length && !bodyLines[bodyLines.length - 1].trim()) bodyLines.pop();
    }
  }

  let note = null;
  for (let i = 0; i < bodyLines.length; i++) {
    if (/^>\s*备注[:：]/.test(bodyLines[i])) {
      note = bodyLines[i].replace(/^>\s*备注[:：]\s*/, '').trim();
      bodyLines.splice(i, 1);
      if (bodyLines[i] !== undefined && !bodyLines[i].trim()) bodyLines.splice(i, 1);
      break;
    }
  }

  let screenshot = null;
  for (let i = 0; i < bodyLines.length; i++) {
    if (/^>\s*!\[/.test(bodyLines[i])) {
      const m = bodyLines[i].match(/!\[[^\]]*\]\(([^)]+)\)/);
      if (m) screenshot = m[1];
      bodyLines.splice(i, 1);
      break;
    }
  }

  if (!tag) {
    for (let i = 0; i < bodyLines.length; i++) {
      const m = bodyLines[i].match(/(?:^|\s)#(TODO|灵感|下次再读)(?:\s|$)/);
      if (m && KNOWN_TAGS.has(m[1])) {
        tag = m[1];
        if (bodyLines[i].trim() === `#${m[1]}`) {
          bodyLines.splice(i, 1);
          if (bodyLines[i] !== undefined && !bodyLines[i].trim()) bodyLines.splice(i, 1);
        }
        break;
      }
    }
  }

  return {
    id: `${date}#${index}`,
    date, time, weekday, source, tag, note, screenshot,
    sourceIndex: index,
    body: bodyLines.join('\n').trim(),
    raw: block,
  };
}

// =============================================================
// 3. 极简 Markdown 渲染 (paragraphs / inline code / list)
//    先 escape 所有 HTML,再补回安全标签,避免 XSS
// =============================================================

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function renderMarkdown(text) {
  if (!text) return '';
  const escaped = escapeHtml(text);
  const paragraphs = escaped.split(/\n\s*\n/);
  return paragraphs.map(p => {
    p = p.trim();
    if (!p) return '';
    const lines = p.split('\n');
    // 纯列表段
    if (lines.every(l => /^-\s+/.test(l))) {
      return '<ul>' + lines.map(l =>
        `<li>${inline(l.replace(/^-\s+/, ''))}</li>`
      ).join('') + '</ul>';
    }
    return `<p>${inline(p).replace(/\n/g, '<br>')}</p>`;
  }).filter(Boolean).join('');
}

function inline(s) {
  // 行内 code
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  // 加粗 (用得不多,但便宜)
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  return s;
}

// =============================================================
// 4. localStorage · Prompt 选择
// =============================================================

const RANGE_KEY = 'aisec.range';   // A · 时间段 (today/week/month)
const STYLE_KEY = 'aisec.style';   // B · 风格 (prompt id, null=不附)

function getSavedRange() {
  try {
    return localStorage.getItem(RANGE_KEY) || 'today';
  } catch (_) {
    return 'today';
  }
}
function setSavedRange(id) {
  if (!id) return;
  try {
    localStorage.setItem(RANGE_KEY, id);
  } catch (_) {
    // A standalone file can run in a browser profile that blocks local storage.
  }
}
function getSavedStyle() {
  try {
    return localStorage.getItem(STYLE_KEY) || null;
  } catch (_) {
    return null;
  }
}
function setSavedStyle(id) {
  try {
    if (id) localStorage.setItem(STYLE_KEY, id);
    else localStorage.removeItem(STYLE_KEY);
  } catch (_) {
    // The UI remains usable; only this optional preference is not persisted.
  }
}
function findStyle(id) {
  if (!id || !window.MEMENTO_STYLES) return null;
  return window.MEMENTO_STYLES.find(p => p.id === id) || null;
}
function findRange(id) {
  const ranges = window.MEMENTO_RANGES || [];
  return ranges.find(r => r.id === id) || ranges[0] || { id: 'today', label: '今天', days: 1 };
}

// =============================================================
// 5. State + 渲染
// =============================================================

const state = {
  files: [],
  allEntries: [],
  todayDate: null,
  todayFileText: null,
  todayEntries: [],
  selectedDate: null,     // 热力条当前浏览日期；同一 Tab 后台刷新时保持
  currentFilter: 'all', // 记录优先:默认看见完整的一天
  selectedRange: 'today', // A · 时间段 (today/week/month)
  selectedStyle: null,    // B · 风格 prompt id (null = 不附)
  dirHandle: null,        // ~/AISecretary 目录 handle (照片和总结只读;写归档时懒升级)
  snapshots: [],          // 从每日 Markdown 解析出的“每日第一帧”
  reviewFiles: [],        // Daily Review 原始文本;可由持久快照立即恢复
  reviewStateFiles: [],   // Review 运行状态原始文本
  reviews: [],            // 从 Reviews/Daily 解析出的晚间总结
  reviewStates: {},       // 从 .review/status 读取的真实生成状态
  reviewSourceHashes: {}, // 上次完成核对的日报哈希;内容可先显示，状态后台更新
  reviewSourceMocks: {},
  reviewPromptHash: '',
  reviewCacheSource: 'none', // none / cache / partial / fresh
  dayCards: [],           // 按日期配对后的照片 + 总结
  reviewReadIssue: '',    // 总结目录级读取异常;不影响主记录和照片
  reviewStatusReadIssue: '', // 状态目录级读取异常;不存在时保持安静
  reviewPromptReadIssue: '', // 当前 Prompt 读取失败时 Review 不能标记为 current
  recordReadIssues: [],   // 单个根 Markdown 读取失败;其他文件继续加载
  recordScanIssue: '',    // 根目录扫描中断时显示已读取的部分结果
  persistenceIssue: '',   // 当前 handle 可用，但 IndexedDB 未确认持久化
  recordSource: 'none',   // none / waiting / cache / partial / fresh / shared
  recordRefreshMessage: '', // 缓存、跨标签页和后台核对状态
  todayResolved: false,   // 今天文件已成功读取，或完整扫描已确认不存在
};

const COGNITIVE_HOME_ROOT_PATH = ['.context-agent', 'cognitive-secretary-v1'];
const COGNITIVE_HOME_PROJECTION_PATH = [...COGNITIVE_HOME_ROOT_PATH, 'projections'];
const COGNITIVE_LANDSCAPE_PATH = [...COGNITIVE_HOME_ROOT_PATH, 'landscape-snapshots'];
const COGNITIVE_ACTION_PATH = [...COGNITIVE_HOME_ROOT_PATH, 'user-actions'];
const COGNITIVE_ACTION_RESULT_PATH = [...COGNITIVE_HOME_ROOT_PATH, 'action-results'];
const COGNITIVE_MANUAL_DAY_REQUEST_PATH = [...COGNITIVE_HOME_ROOT_PATH, 'manual-day-requests'];
const COGNITIVE_MANUAL_DAY_RESULT_PATH = [...COGNITIVE_HOME_ROOT_PATH, 'manual-day-results'];
const COGNITIVE_RECORD_REVISION_PATH = [...COGNITIVE_HOME_ROOT_PATH, 'records'];
const COGNITIVE_RECEIPT_REVISION_PATH = [...COGNITIVE_HOME_ROOT_PATH, 'receipts'];
const COGNITIVE_MEMORY_REVISION_PATH = [...COGNITIVE_HOME_ROOT_PATH, 'memory-revisions'];
const COGNITIVE_RELATION_REVISION_PATH = [...COGNITIVE_HOME_ROOT_PATH, 'relation-revisions'];
const COGNITIVE_BUNDLE_COMMITTED_PATH = [...COGNITIVE_HOME_ROOT_PATH, 'daily-bundles', 'committed'];
const COGNITIVE_HOME_MAX_BYTES = 8 * 1024 * 1024;
const COGNITIVE_SOURCE_MAX_BYTES = 32 * 1024 * 1024;
const COGNITIVE_DAILY_SOURCE_RE = /^\d{4}-\d{2}-\d{2}\.md$/;
const COGNITIVE_ACTION_FILE_RE = /^(cact_[0-9a-f]{24})\.json$/;
const COGNITIVE_MANUAL_DAY_RESULT_FILE_RE = /^(cmanr_[0-9a-f]{24})\.json$/;
const COGNITIVE_REVISION_FILE_RE = /^(rec_[0-9a-f]{24})\.r([0-9]{6})\.json$/;
const COGNITIVE_RECEIPT_FILE_RE = /^(rcp_[0-9a-f]{24})\.r([0-9]{6})\.json$/;
const COGNITIVE_INDEX_FIELDS = new Set([
  'schema_version', 'kind', 'index_revision', 'generated_at', 'records',
]);
const COGNITIVE_INDEX_ENTRY_FIELDS = new Set([
  'record_id', 'status', 'current_revision', 'revision_sha256', 'source_file',
  'locator_version', 'original_occurrence_ordinal', 'line_start', 'line_end',
  'byte_start', 'byte_end', 'entry_sha256', 'source_snapshot_sha256',
  'heading_sha256', 'time', 'weekday', 'source_app', 'tag', 'note_sha256',
  'attachment_paths',
]);
const COGNITIVE_SOURCE_REVISION_FIELDS = new Set([
  'schema_version', 'kind', 'record_id', 'revision', 'status', 'operation',
  'created_at', 'captured_at', 'local_date', 'source_type', 'source_app',
  'source_file', 'line_start', 'line_end', 'entry_sha256',
  'source_snapshot_sha256', 'attachments', 'ingest_origin',
  'previous_revision_sha256',
]);
const COGNITIVE_RECEIPT_REVISION_FIELDS = new Set([
  'schema_version', 'kind', 'receipt_id', 'revision', 'status', 'operation',
  'created_at', 'request_id', 'run_id', 'record_ref', 'user_action_id',
  'summary', 'facets', 'memory_candidates', 'relation_candidates',
  'source_spans', 'contract_version', 'feedback_watermark_sha256',
  'previous_revision_sha256',
]);
const COGNITIVE_MEMORY_REVISION_FIELDS = new Set([
  'schema_version', 'kind', 'memory_id', 'revision', 'status', 'operation',
  'created_at', 'statement', 'memory_kind', 'topics', 'purposes', 'uncertainty',
  'source_spans', 'origin_receipt_refs', 'provenance', 'previous_revision_sha256',
]);
const COGNITIVE_RELATION_REVISION_FIELDS = new Set([
  'schema_version', 'kind', 'relation_id', 'revision', 'status', 'operation',
  'created_at', 'type', 'from_ref', 'to_ref', 'direction', 'statement',
  'uncertainty', 'source_spans', 'valid_from', 'provenance',
  'previous_revision_sha256',
]);
const COGNITIVE_SOURCE_SPAN_FIELDS = new Set([
  'record_id', 'record_revision', 'record_revision_sha256', 'source_file',
  'line_start', 'line_end', 'quote', 'quote_sha256',
]);
const COGNITIVE_BUNDLE_MANIFEST_FIELDS = new Set([
  'schema_version', 'kind', 'bundle_id', 'revision', 'status', 'operation',
  'created_at', 'committed_at', 'local_date', 'request_id', 'run_id',
  'input_hashes', 'source_refs', 'receipt_refs', 'memory_refs',
  'relation_refs', 'summary_ref', 'candidate_materializations',
  'long_term_result_ref', 'warnings', 'previous_revision_sha256',
]);
const cognitiveHomeState = {
  readId: 0,
  status: 'idle', // idle / loading / authorizing / ready / missing / legacy / invalid
  home: null,
  landscape: null,
  landscapeSha256: '',
  recordLocators: new Map(),
  candidate: null,
  stale: false,
  issue: '',
  activeView: 'map',
  selected: null,
  drawerTrigger: null,
  verifiedReceipts: new Map(),
  verifiedMemories: new Map(),
  verifiedRelations: new Map(),
  actionMutating: false,
  actionNotice: '',
  actionNoticeTone: '',
  pendingAction: null,
  manualDayMutating: false,
  manualDayNotice: '',
  manualDayNoticeTone: '',
  pendingManualDay: null,
  outputOpen: false,
  outputTrigger: null,
};

const cognitiveDemoState = {
  active: false,
  fixture: null,
  rawRecordsById: new Map(),
};
const COGNITIVE_MAP_BOUNDS = Object.freeze({ x: 0, y: 0, width: 1100, height: 520 });
const COGNITIVE_MAP_MIN_ZOOM = 1;
const COGNITIVE_MAP_MAX_ZOOM = 3.2;
const cognitiveMapCameraState = {
  zoom: 1,
  centerX: COGNITIVE_MAP_BOUNDS.width / 2,
  centerY: COGNITIVE_MAP_BOUNDS.height / 2,
  pointers: new Map(),
  dragPointerId: null,
  dragOrigin: null,
  pinchOrigin: null,
  moved: false,
  fullscreen: false,
  scrollY: 0,
  suppressClickUntil: 0,
  tiltFrame: 0,
  baseScreenScale: 0,
  hoverArmed: true,
  hoverGateOrigin: null,
  lastPointerClientX: null,
  lastPointerClientY: null,
  resizeObserver: null,
  interactionsInited: false,
};
const cognitiveMapInteractionState = {
  hoverKind: '',
  hoverId: '',
  pinnedKind: '',
  pinnedId: '',
  pinnedTrigger: null,
  insightId: '',
  insightTrigger: null,
};
const cognitiveCompactLandscapeMedia = typeof window.matchMedia === 'function'
  ? window.matchMedia('(max-width: 600px)')
  : null;
const cognitiveReducedMotionMedia = typeof window.matchMedia === 'function'
  ? window.matchMedia('(prefers-reduced-motion: reduce)')
  : null;

function cognitiveMapDepth(zoom = cognitiveMapCameraState.zoom) {
  if (zoom >= 2.05) return 'evidence';
  if (zoom >= 1.38) return 'theme';
  return 'overview';
}

function cognitiveMapDepthLabel(zoom = cognitiveMapCameraState.zoom) {
  const label = cognitiveMapDepth(zoom) === 'evidence' ? '证据层'
    : cognitiveMapDepth(zoom) === 'theme' ? '主题层' : '全景';
  return `${label} · ${Math.round(zoom * 100)}%`;
}

function cognitiveMapVisibleWorldSize(svg) {
  const viewportWidth = COGNITIVE_MAP_BOUNDS.width / cognitiveMapCameraState.zoom;
  const viewportHeight = COGNITIVE_MAP_BOUNDS.height / cognitiveMapCameraState.zoom;
  const rect = svg?.getBoundingClientRect?.();
  if (!rect?.width || !rect?.height) {
    return { width: viewportWidth, height: viewportHeight };
  }
  const screenAspect = rect.width / rect.height;
  const worldAspect = viewportWidth / viewportHeight;
  // preserveAspectRatio="xMidYMid slice" crops one world axis. Camera bounds
  // must use the part that is actually visible, otherwise 100% looks locked
  // even though the embedded map has content beyond its left and right edges.
  if (screenAspect < worldAspect) {
    return { width: viewportHeight * screenAspect, height: viewportHeight };
  }
  return { width: viewportWidth, height: viewportWidth / screenAspect };
}

function cognitiveClampMapCamera(svg) {
  const visible = cognitiveMapVisibleWorldSize(svg);
  const viewportWidth = visible.width;
  const viewportHeight = visible.height;
  const halfWidth = viewportWidth / 2;
  const halfHeight = viewportHeight / 2;
  cognitiveMapCameraState.centerX = Math.max(
    COGNITIVE_MAP_BOUNDS.x + halfWidth,
    Math.min(COGNITIVE_MAP_BOUNDS.x + COGNITIVE_MAP_BOUNDS.width - halfWidth,
      cognitiveMapCameraState.centerX)
  );
  cognitiveMapCameraState.centerY = Math.max(
    COGNITIVE_MAP_BOUNDS.y + halfHeight,
    Math.min(COGNITIVE_MAP_BOUNDS.y + COGNITIVE_MAP_BOUNDS.height - halfHeight,
      cognitiveMapCameraState.centerY)
  );
}

function cognitiveMapViewportScale(svg) {
  const rect = svg?.getBoundingClientRect?.();
  const viewBox = String(svg?.getAttribute?.('viewBox') || '')
    .trim().split(/\s+/).map(Number);
  if (!rect?.width || !rect?.height || viewBox.length !== 4
      || !Number.isFinite(viewBox[2]) || !Number.isFinite(viewBox[3])
      || viewBox[2] <= 0 || viewBox[3] <= 0) return 0;
  // The map uses xMidYMid slice, so the larger axis scale is the actual
  // user-unit to screen-pixel ratio.
  return Math.max(rect.width / viewBox[2], rect.height / viewBox[3]);
}

function cognitiveApplyMapScreenSpaceScale(svg) {
  const viewportScale = cognitiveMapViewportScale(svg);
  if (!viewportScale) return;
  if (!cognitiveMapCameraState.baseScreenScale) {
    cognitiveMapCameraState.baseScreenScale = viewportScale;
  }
  const scale = Math.max(.01, Math.min(
    4,
    cognitiveMapCameraState.baseScreenScale / viewportScale
  ));
  const transform = `scale(${scale.toFixed(5)})`;
  svg.querySelectorAll('[data-cognitive-screen-space]').forEach(element => {
    element.setAttribute('transform', transform);
  });
  svg.dataset.cognitiveScreenSpaceScale = scale.toFixed(5);
}

function cognitiveAtlasGridStep(zoom = cognitiveMapCameraState.zoom) {
  if (zoom >= 2.55) return 25;
  if (zoom >= 1.55) return 50;
  return 100;
}

function cognitiveAtlasNiceScale(worldWidth) {
  const target = Math.max(1, worldWidth * .16);
  const magnitude = 10 ** Math.floor(Math.log10(target));
  return [5, 2, 1]
    .map(multiplier => multiplier * magnitude)
    .find(value => value <= target) || magnitude;
}

function cognitiveAtlasAxisValues(minimum, size, interval) {
  const start = Math.ceil(minimum / interval) * interval;
  const maximum = minimum + size;
  const values = [];
  for (let value = start; value <= maximum + .001; value += interval) {
    values.push(value);
  }
  return values;
}

function cognitiveSetAtlasAxisLabels(identifier, values, minimum, size, axis) {
  const container = document.getElementById(identifier);
  if (!container) return;
  const labels = values.map(value => {
    const label = document.createElement('span');
    const normalized = Math.max(0, Math.min(1, (value - minimum) / size));
    label.style.setProperty('--atlas-position', `${(normalized * 100).toFixed(3)}%`);
    label.textContent = `${axis} ${String(Math.round(value)).padStart(4, '0')}`;
    return label;
  });
  container.replaceChildren(...labels);
}

function cognitiveUpdateAtlasHud(svg) {
  const viewBox = String(svg?.getAttribute?.('viewBox') || '')
    .trim().split(/\s+/).map(Number);
  if (viewBox.length !== 4 || viewBox.some(value => !Number.isFinite(value))) return;
  const visible = cognitiveMapVisibleWorldSize(svg);
  const viewWidth = visible.width;
  const viewHeight = visible.height;
  const viewX = cognitiveMapCameraState.centerX - viewWidth / 2;
  const viewY = cognitiveMapCameraState.centerY - viewHeight / 2;
  const gridStep = cognitiveAtlasGridStep();
  const minorStep = gridStep / 5;
  const majorPattern = svg.querySelector('#cognitive-atlas-major-grid');
  const minorPattern = svg.querySelector('#cognitive-atlas-minor-grid');
  const majorLine = svg.querySelector('#cognitive-atlas-major-grid-line');
  const minorLine = svg.querySelector('#cognitive-atlas-minor-grid-line');
  const cross = svg.querySelector('#cognitive-atlas-cross-mark');
  if (majorPattern && majorLine && cross) {
    majorPattern.setAttribute('width', gridStep);
    majorPattern.setAttribute('height', gridStep);
    majorLine.setAttribute('d', `M ${gridStep} 0 H 0 V ${gridStep}`);
    const middle = gridStep / 2;
    cross.setAttribute('d', `M ${middle - 3} ${middle} H ${middle + 3} M ${middle} ${middle - 3} V ${middle + 3}`);
  }
  if (minorPattern && minorLine) {
    minorPattern.setAttribute('width', minorStep);
    minorPattern.setAttribute('height', minorStep);
    minorLine.setAttribute('d', `M ${minorStep} 0 H 0 V ${minorStep}`);
  }
  svg.dataset.cognitiveGridStep = String(gridStep);

  const labelStep = gridStep * 2;
  const xValues = cognitiveAtlasAxisValues(viewX, viewWidth, labelStep);
  const yValues = cognitiveAtlasAxisValues(viewY, viewHeight, labelStep);
  cognitiveSetAtlasAxisLabels('cognitive-atlas-x-top', xValues, viewX, viewWidth, 'X');
  cognitiveSetAtlasAxisLabels('cognitive-atlas-x-bottom', xValues, viewX, viewWidth, 'X');
  cognitiveSetAtlasAxisLabels('cognitive-atlas-y-left', yValues, viewY, viewHeight, 'Y');
  cognitiveSetAtlasAxisLabels('cognitive-atlas-y-right', yValues, viewY, viewHeight, 'Y');

  const sheet = document.getElementById('cognitive-atlas-sheet-code');
  if (sheet) {
    sheet.textContent = `SHEET 01 · X${String(Math.round(cognitiveMapCameraState.centerX)).padStart(4, '0')} Y${String(Math.round(cognitiveMapCameraState.centerY)).padStart(4, '0')}`;
  }
  const scaleWorld = cognitiveAtlasNiceScale(viewWidth);
  const scaleLabel = document.getElementById('cognitive-atlas-scale-label');
  const scaleBar = document.getElementById('cognitive-atlas-scale-bar');
  if (scaleLabel) scaleLabel.textContent = `${scaleWorld} u`;
  if (scaleBar) {
    const rect = svg.getBoundingClientRect?.();
    const scaleWidth = rect?.width ? Math.max(54, Math.min(170, rect.width * scaleWorld / viewWidth)) : 96;
    scaleBar.style.width = `${scaleWidth.toFixed(1)}px`;
  }
}

function cognitiveApplyMapCamera({ clamp = true } = {}) {
  const svg = document.getElementById('cognitive-landscape-map');
  if (!svg) return;
  if (clamp) cognitiveClampMapCamera(svg);
  const width = COGNITIVE_MAP_BOUNDS.width / cognitiveMapCameraState.zoom;
  const height = COGNITIVE_MAP_BOUNDS.height / cognitiveMapCameraState.zoom;
  svg.setAttribute('viewBox', [
    cognitiveMapCameraState.centerX - width / 2,
    cognitiveMapCameraState.centerY - height / 2,
    width,
    height,
  ].map(value => value.toFixed(2)).join(' '));
  cognitiveUpdateAtlasHud(svg);
  cognitiveApplyMapScreenSpaceScale(svg);
  const depth = cognitiveMapDepth();
  svg.dataset.cognitiveMapDepth = depth;
  const region = document.getElementById('cognitive-map-region');
  if (region) {
    region.dataset.cognitiveMapDepth = depth;
    region.dataset.cognitiveMapDepthLabel = depth === 'evidence' ? '证据层'
      : depth === 'theme' ? '主题层' : '全景';
  }
  const output = document.getElementById('cognitive-map-zoom');
  if (output) output.textContent = cognitiveMapDepthLabel();
  document.querySelectorAll('[data-cognitive-map-action="zoom-out"]').forEach(button => {
    button.disabled = cognitiveMapCameraState.zoom <= COGNITIVE_MAP_MIN_ZOOM + .001;
  });
  document.querySelectorAll('[data-cognitive-map-action="zoom-in"]').forEach(button => {
    button.disabled = cognitiveMapCameraState.zoom >= COGNITIVE_MAP_MAX_ZOOM - .001;
  });
}

function cognitiveResetMapCamera() {
  cognitiveMapCameraState.zoom = 1;
  cognitiveMapCameraState.centerX = COGNITIVE_MAP_BOUNDS.width / 2;
  cognitiveMapCameraState.centerY = COGNITIVE_MAP_BOUNDS.height / 2;
  cognitiveApplyMapCamera();
  cognitiveSetMapTilt(0, 0, 50, 50);
}

function cognitiveSetMapFullscreen(fullscreen, { restoreFocus = true } = {}) {
  const region = document.getElementById('cognitive-map-region');
  const trigger = region?.querySelector('[data-cognitive-map-action="fullscreen"]');
  if (!region || !trigger || cognitiveMapCameraState.fullscreen === fullscreen) return;
  cancelAnimationFrame(cognitiveMapCameraState.tiltFrame);
  clearCognitiveMapHover();
  cognitiveSetMapTilt(0, 0, 50, 50);
  if (fullscreen) {
    cognitiveMapCameraState.scrollY = window.scrollY;
  }
  cognitiveMapCameraState.fullscreen = fullscreen;
  document.documentElement.classList.toggle('cognitive-map-fullscreen', fullscreen);
  document.body.classList.toggle('cognitive-map-fullscreen', fullscreen);
  region.classList.toggle('is-cognitive-map-fullscreen', fullscreen);
  // A layout change can move a peak under a stationary pointer. Ignore that
  // synthetic hover until the user actually moves the pointer again.
  cognitiveMapCameraState.hoverArmed = false;
  cognitiveMapCameraState.hoverGateOrigin = Number.isFinite(cognitiveMapCameraState.lastPointerClientX)
    && Number.isFinite(cognitiveMapCameraState.lastPointerClientY)
    ? {
        clientX: cognitiveMapCameraState.lastPointerClientX,
        clientY: cognitiveMapCameraState.lastPointerClientY,
      }
    : null;
  trigger.setAttribute('aria-pressed', fullscreen ? 'true' : 'false');
  trigger.setAttribute('aria-label', fullscreen ? '退出认知地图全屏' : '全屏查看认知地图');
  trigger.textContent = fullscreen ? '退出全屏' : '全屏查看';
  const hint = document.getElementById('cognitive-map-help');
  if (hint) hint.textContent = fullscreen
    ? '滚轮缩放，拖拽移动，双击靠近。Esc 退出全屏。'
    : '滚轮缩放，拖拽移动；全屏只扩展画布。';
  if (!fullscreen) {
    window.scrollTo({ top: cognitiveMapCameraState.scrollY, behavior: 'auto' });
  }
  window.getSelection?.()?.removeAllRanges?.();
  // Force the new geometry now, then compensate labels and dots before the
  // browser gets a chance to paint an intermediate oversized frame.
  region.getBoundingClientRect();
  cognitiveApplyMapCamera({ clamp: false });
  requestAnimationFrame(() => {
    if (fullscreen) region.focus({ preventScroll: true });
    else if (restoreFocus) trigger.focus({ preventScroll: true });
  });
}

function cognitiveMapSvgPoint(clientX, clientY) {
  const svg = document.getElementById('cognitive-landscape-map');
  if (!svg || typeof svg.createSVGPoint !== 'function') return null;
  const matrix = svg.getScreenCTM?.();
  if (!matrix) return null;
  const point = svg.createSVGPoint();
  point.x = clientX;
  point.y = clientY;
  return point.matrixTransform(matrix.inverse());
}

function cognitiveMapDefaultZoomAnchor() {
  const peaks = cognitiveHomeState.landscape?.peaks || [];
  if (!peaks.length) return null;
  return peaks
    .map(peak => cognitiveMapPoint(peak.x, peak.y))
    .reduce((nearest, point) => {
      const distance = Math.hypot(
        point.x - cognitiveMapCameraState.centerX,
        point.y - cognitiveMapCameraState.centerY
      );
      return !nearest || distance < nearest.distance ? { ...point, distance } : nearest;
    }, null);
}

function cognitiveZoomMapAt(nextZoom, clientX = null, clientY = null) {
  const previousZoom = cognitiveMapCameraState.zoom;
  const boundedZoom = Math.max(COGNITIVE_MAP_MIN_ZOOM, Math.min(COGNITIVE_MAP_MAX_ZOOM, nextZoom));
  if (Math.abs(previousZoom - boundedZoom) < .001) return;
  const pointerAnchor = Number.isFinite(clientX) && Number.isFinite(clientY)
    ? cognitiveMapSvgPoint(clientX, clientY) : null;
  const anchor = pointerAnchor || (boundedZoom > previousZoom
    ? cognitiveMapDefaultZoomAnchor() : null);
  cognitiveMapCameraState.zoom = boundedZoom;
  if (anchor) {
    const ratio = previousZoom / boundedZoom;
    cognitiveMapCameraState.centerX = anchor.x
      + (cognitiveMapCameraState.centerX - anchor.x) * ratio;
    cognitiveMapCameraState.centerY = anchor.y
      + (cognitiveMapCameraState.centerY - anchor.y) * ratio;
  }
  cognitiveApplyMapCamera();
}

function cognitivePanMap(deltaX, deltaY) {
  cognitiveMapCameraState.centerX += deltaX;
  cognitiveMapCameraState.centerY += deltaY;
  cognitiveApplyMapCamera();
}

function cognitiveSetMapTilt() {
  // The cartographic MVP is intentionally static. Camera pan and zoom remain,
  // while pointer-driven lighting and parallax are reserved for a later study.
}

function cognitiveScheduleMapTilt() {}

function cognitiveDemoLibrary() {
  return window.MementoCognitiveDemoFixture || null;
}

function cognitiveDemoRevisionMap(items, idKeys) {
  return new Map((items || []).map(item => {
    const value = item?.value && typeof item.value === 'object' ? item.value : item;
    const id = item?.ref?.id || idKeys.map(key => value?.[key]).find(Boolean);
    return [id, value];
  }).filter(([id]) => Boolean(id)));
}

function cognitiveDemoRawText(recordId) {
  const entry = cognitiveDemoState.rawRecordsById.get(recordId);
  if (typeof entry === 'string') return entry;
  return typeof entry?.text === 'string' ? entry.text : '';
}

function cognitiveDemoSyncTodayCounts() {
  const home = cognitiveDemoState.fixture?.home;
  if (!home) return;
  home.today_status.saved = home.records.length;
  home.today_status.interpreted = home.records.filter(record => (
    record.receipt_ref || record.status === 'no_candidate'
  )).length;
  home.today_status.merged = home.records.filter(record => record.status === 'merged').length;
  home.today_status.needs_review = home.records.filter(record => record.status === 'needs_review').length;
  if (home.records.some(record => record.status === 'raw_saved')) {
    home.today_status.daily_run_status = 'no_receipts';
  }
}

const directoryLoadGate = window.MementoDirectoryAccess.createGenerationGate();
let selectionEpoch = 0;

function renderDashboard() {
  renderDashboardNotice();
  renderRecordSummary(state.todayEntries.length);
  renderStats();
  renderHeatmap();
  renderSelectedDateSection();
  bindCopyButton();
  renderCognitiveHome();
}

function renderDashboardNotice() {
  const notice = document.getElementById('dashboard-notice');
  const messages = [];
  const errorMessages = [];
  if (state.recordRefreshMessage) messages.push(state.recordRefreshMessage);
  if (state.recordReadIssues.length) {
    const names = state.recordReadIssues.slice(0, 3).map(issue => issue.name).join('、');
    const more = state.recordReadIssues.length > 3 ? ` 等 ${state.recordReadIssues.length} 个文件` : '';
    errorMessages.push(`有 ${state.recordReadIssues.length} 个每日记录文件暂时无法读取(${names}${more})，其余记录已正常加载。`);
  }
  if (state.recordScanIssue) {
    errorMessages.push(`${state.recordScanIssue} 当前已显示 ${state.files.length} 个已读取的文件；请检查数据目录后刷新重试。`);
  }
  if (state.persistenceIssue) errorMessages.push(state.persistenceIssue);
  if (['loading', 'authorizing'].includes(cognitiveHomeState.status)) {
    messages.push('正在核对认知主页投影；核对完成前继续显示记录主页。');
  } else if (cognitiveHomeState.status === 'missing') {
    messages.push('认知主页尚未生成，当前继续使用记录主页。');
  } else if (cognitiveHomeState.status === 'legacy') {
    errorMessages.push('本地认知主页版本与当前页面不兼容，已停止读取并继续使用记录主页。');
  } else if (cognitiveHomeState.status === 'invalid') {
    errorMessages.push('本地认知主页没有通过合同与来源映射校验，已停止读取并继续使用记录主页。');
  }
  messages.push(...errorMessages);
  notice.textContent = messages.join(' ');
  notice.hidden = messages.length === 0;
  notice.classList.toggle('is-neutral', errorMessages.length === 0);
}

function cognitiveHomeLibrary() {
  const library = window.MementoCognitiveHome;
  if (!library) throw new Error('认知主页数据模块未加载');
  return library;
}

function resetCognitiveHomeState() {
  cognitiveHomeState.readId += 1;
  cognitiveHomeState.status = 'idle';
  cognitiveHomeState.home = null;
  cognitiveHomeState.landscape = null;
  cognitiveHomeState.landscapeSha256 = '';
  cognitiveHomeState.recordLocators = new Map();
  cognitiveHomeState.verifiedReceipts = new Map();
  cognitiveHomeState.verifiedMemories = new Map();
  cognitiveHomeState.verifiedRelations = new Map();
  cognitiveHomeState.actionMutating = false;
  cognitiveHomeState.actionNotice = '';
  cognitiveHomeState.actionNoticeTone = '';
  cognitiveHomeState.pendingAction = null;
  cognitiveHomeState.manualDayMutating = false;
  cognitiveHomeState.manualDayNotice = '';
  cognitiveHomeState.manualDayNoticeTone = '';
  cognitiveHomeState.pendingManualDay = null;
  cognitiveHomeState.candidate = null;
  cognitiveHomeState.stale = false;
  cognitiveHomeState.issue = '';
  cognitiveHomeState.activeView = 'map';
  cognitiveHomeState.selected = null;
  clearCognitiveInsightMapFocus();
  clearCognitiveMapPin();
  closeCognitiveChainDrawer(false);
  closeCognitiveOutputPopover(false);
}

async function readCognitiveJsonFile(root, path, name) {
  const directory = await nestedDirectory(root, path, false);
  if (!directory) return { exists: false, value: null, sha256: '' };
  try {
    const handle = await directory.getFileHandle(name);
    const file = await handle.getFile();
    if (file.size > COGNITIVE_HOME_MAX_BYTES) throw new Error('认知主页投影文件过大');
    const bytes = await file.arrayBuffer();
    const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    return {
      exists: true,
      value: JSON.parse(text),
      sha256: await sha256Hex(bytes),
    };
  } catch (error) {
    if (error && error.name === 'NotFoundError') {
      return { exists: false, value: null, sha256: '' };
    }
    throw error;
  }
}

function cognitiveExactObject(value, fields, name) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${name} 必须是 JSON object`);
  }
  const keys = Object.keys(value);
  if (keys.length !== fields.size || keys.some(key => !fields.has(key))) {
    throw new Error(`${name} 字段不符合合同`);
  }
  return value;
}

function cognitiveSameObjectRef(left, right) {
  return Boolean(left && right
    && left.kind === right.kind
    && left.id === right.id
    && left.revision === right.revision
    && left.revision_sha256 === right.revision_sha256);
}

function cognitiveRefMap(refs) {
  return new Map(refs.map(ref => [ref.id, ref]));
}

function cognitiveRevisionFileName(ref) {
  return `${ref.id}.r${String(ref.revision).padStart(6, '0')}.json`;
}

async function readCognitiveDirectoryRows(root, path) {
  const directory = await nestedDirectory(root, path, false);
  if (!directory) return [];
  const rows = [];
  for await (const [name, handle] of directory.entries()) rows.push({ name, handle });
  return rows.sort((left, right) => left.name.localeCompare(right.name));
}

async function readCognitiveActionWatermark(root, library) {
  const rows = await readCognitiveDirectoryRows(root, COGNITIVE_ACTION_PATH);
  const refs = [];
  for (const row of rows) {
    if (row.name.startsWith('.')) continue;
    const match = COGNITIVE_ACTION_FILE_RE.exec(row.name);
    if (!match || row.handle.kind !== 'file') throw new Error('cognitive user-actions 包含非法文件');
    const file = await row.handle.getFile();
    if (file.size > COGNITIVE_HOME_MAX_BYTES) throw new Error('cognitive user action 文件过大');
    const bytes = await file.arrayBuffer();
    refs.push({ id: match[1], sha256: library.sha256Hex(bytes) });
  }
  return library.sha256Hex(library.canonicalJson(refs));
}

async function readCognitiveSourceFileBytes(root, sourceFile) {
  if (!COGNITIVE_DAILY_SOURCE_RE.test(sourceFile)) {
    throw new Error('source_file 不是 Vault 根目录日级 Markdown');
  }
  const handle = await root.getFileHandle(sourceFile);
  const file = await handle.getFile();
  if (file.size > COGNITIVE_SOURCE_MAX_BYTES) throw new Error('日级 Markdown 超过读取上限');
  return file.arrayBuffer();
}

async function readCognitiveRecordAuthority(root, localDate, library) {
  const indexResult = await readCognitiveJsonFile(
    root, COGNITIVE_HOME_ROOT_PATH, 'record-index.json'
  );
  const index = indexResult.exists ? cognitiveExactObject(
    indexResult.value, COGNITIVE_INDEX_FIELDS, 'record index'
  ) : {
    schema_version: '1.0',
    kind: 'memento_source_record_index',
    index_revision: 0,
    generated_at: '1970-01-01T00:00:00+00:00',
    records: [],
  };
  if (index.schema_version !== '1.0'
      || index.kind !== 'memento_source_record_index'
      || !Number.isSafeInteger(index.index_revision)
      || index.index_revision < 0
      || !Array.isArray(index.records)) {
    throw new Error('record index 合同无效');
  }

  const revisionRows = await readCognitiveDirectoryRows(root, COGNITIVE_RECORD_REVISION_PATH);
  const latestRevision = new Map();
  for (const row of revisionRows) {
    if (row.name.startsWith('.')) continue;
    const match = COGNITIVE_REVISION_FILE_RE.exec(row.name);
    if (!match || row.handle.kind !== 'file') throw new Error('records 目录包含非法文件');
    const revision = Number(match[2]);
    latestRevision.set(match[1], Math.max(latestRevision.get(match[1]) || 0, revision));
  }

  const identifiers = [];
  const activeRefs = new Map();
  const today = [];
  const recordLocators = new Map();
  const sourceSnapshots = new Map();
  for (const rawEntry of index.records) {
    const entry = cognitiveExactObject(rawEntry, COGNITIVE_INDEX_ENTRY_FIELDS, 'record index entry');
    if (!COGNITIVE_DAILY_SOURCE_RE.test(entry.source_file)
        || !Number.isSafeInteger(entry.byte_start)
        || !Number.isSafeInteger(entry.byte_end)
        || entry.byte_start < 0
        || entry.byte_end <= entry.byte_start
        || !/^[0-9a-f]{64}$/.test(entry.entry_sha256)
        || !/^[0-9a-f]{64}$/.test(entry.source_snapshot_sha256)) {
      throw new Error('record index source locator 无效');
    }
    const ref = library.validateObjectRef({
      kind: 'source_record',
      id: entry.record_id,
      revision: entry.current_revision,
      revision_sha256: entry.revision_sha256,
    });
    identifiers.push(ref.id);
    if (!['active', 'tombstone'].includes(entry.status)
        || latestRevision.get(ref.id) !== ref.revision) {
      throw new Error('record index 没有绑定当前 revision head');
    }
    const revisionResult = await readCognitiveJsonFile(
      root, COGNITIVE_RECORD_REVISION_PATH, cognitiveRevisionFileName(ref)
    );
    if (!revisionResult.exists || revisionResult.sha256 !== ref.revision_sha256) {
      throw new Error('source record revision hash 不一致');
    }
    const revision = cognitiveExactObject(
      revisionResult.value, COGNITIVE_SOURCE_REVISION_FIELDS, 'source record revision'
    );
    if (revision.schema_version !== '1.0'
        || revision.kind !== 'memento_source_record_revision'
        || revision.record_id !== ref.id
        || revision.revision !== ref.revision
        || revision.status !== entry.status
        || revision.entry_sha256 !== entry.entry_sha256
        || revision.source_file !== entry.source_file
        || revision.source_snapshot_sha256 !== entry.source_snapshot_sha256
        || revision.line_start !== entry.line_start
        || revision.line_end !== entry.line_end
        || typeof revision.captured_at !== 'string'
        || typeof revision.local_date !== 'string') {
      throw new Error('source record revision 与 index 不一致');
    }
    if (revision.status === 'active') {
      const existingSnapshot = sourceSnapshots.get(entry.source_file);
      if (existingSnapshot && existingSnapshot !== entry.source_snapshot_sha256) {
        throw new Error('同一日级 Markdown 出现不一致快照');
      }
      sourceSnapshots.set(entry.source_file, entry.source_snapshot_sha256);
      activeRefs.set(ref.id, ref);
      recordLocators.set(ref.id, {
        recordRef: ref,
        sourceFile: entry.source_file,
        byteStart: entry.byte_start,
        byteEnd: entry.byte_end,
        entrySha256: entry.entry_sha256,
        sourceSnapshotSha256: entry.source_snapshot_sha256,
      });
      if (revision.local_date === localDate) today.push({ capturedAt: revision.captured_at, ref });
    }
  }
  if (identifiers.some((id, indexValue) => indexValue > 0 && identifiers[indexValue - 1] >= id)
      || latestRevision.size !== index.records.length) {
    throw new Error('record index 必须完整、唯一且有序');
  }
  await Promise.all([...sourceSnapshots].map(async ([sourceFile, expectedSha256]) => {
    const bytes = await readCognitiveSourceFileBytes(root, sourceFile);
    if (library.sha256Hex(bytes) !== expectedSha256) {
      throw new Error('日级 Markdown 已变化，cognitive head 尚未同步');
    }
  }));
  today.sort((left, right) => left.capturedAt.localeCompare(right.capturedAt)
    || left.ref.id.localeCompare(right.ref.id));
  return { activeRefs, todayRefs: today.map(row => row.ref), recordLocators };
}

function cognitiveValidateSourceSpans(spans, activeRecordRefs, library, name) {
  if (!Array.isArray(spans) || !spans.length) throw new Error(`${name}.source_spans 无效`);
  for (const rawSpan of spans) {
    const span = cognitiveExactObject(rawSpan, COGNITIVE_SOURCE_SPAN_FIELDS, `${name}.source_span`);
    const current = activeRecordRefs.get(span.record_id);
    if (!current
        || current.revision !== span.record_revision
        || current.revision_sha256 !== span.record_revision_sha256
        || typeof span.quote !== 'string'
        || library.sha256Hex(span.quote) !== span.quote_sha256) {
      throw new Error(`${name} 没有绑定当前 source record`);
    }
  }
}

async function readCognitiveReceiptAuthority(root, recordRefs, library) {
  const rows = await readCognitiveDirectoryRows(root, COGNITIVE_RECEIPT_REVISION_PATH);
  const byId = new Map();
  for (const row of rows) {
    const match = COGNITIVE_RECEIPT_FILE_RE.exec(row.name);
    if (!match || row.handle.kind !== 'file') continue;
    if (!byId.has(match[1])) byId.set(match[1], []);
    byId.get(match[1]).push({ revision: Number(match[2]), name: row.name });
  }
  const receipts = [];
  const revisions = new Map();
  for (const recordRef of recordRefs) {
    const receiptId = library.makeReceiptId(recordRef.id);
    const chain = (byId.get(receiptId) || []).sort((left, right) => left.revision - right.revision);
    if (!chain.length) continue;
    if (chain.some((row, index) => row.revision !== index + 1)) {
      throw new Error('interpretation receipt revision 链不连续');
    }
    let previousSha = null;
    let head = null;
    let headSha = '';
    for (const row of chain) {
      const result = await readCognitiveJsonFile(root, COGNITIVE_RECEIPT_REVISION_PATH, row.name);
      const revision = cognitiveExactObject(
        result.value, COGNITIVE_RECEIPT_REVISION_FIELDS, 'interpretation receipt revision'
      );
      if (!result.exists
          || revision.schema_version !== '1.0'
          || revision.kind !== 'memento_interpretation_receipt_revision'
          || revision.receipt_id !== receiptId
          || revision.revision !== row.revision
          || revision.previous_revision_sha256 !== previousSha
          || !['ready', 'needs_review', 'original_only', 'tombstone'].includes(revision.status)) {
        throw new Error('interpretation receipt revision 合同无效');
      }
      library.validateObjectRef(revision.record_ref);
      const active = ['ready', 'needs_review'].includes(revision.status);
      if (active) library.validateReceiptFacets(revision.facets);
      if (active !== (typeof revision.summary === 'string' && Boolean(revision.summary.trim()))) {
        throw new Error('interpretation receipt 整理内容与状态不一致');
      }
      previousSha = result.sha256;
      head = revision;
      headSha = result.sha256;
    }
    if (head.status !== 'tombstone' && cognitiveSameObjectRef(head.record_ref, recordRef)) {
      const ref = library.validateObjectRef({
        kind: 'interpretation_receipt', id: receiptId,
        revision: head.revision, revision_sha256: headSha,
      });
      receipts.push(ref);
      revisions.set(receiptId, { ref, value: head });
    }
  }
  return {
    refs: receipts.sort((left, right) => left.id.localeCompare(right.id)),
    revisions,
  };
}

async function cognitiveValidateFormalRef(
  root, ref, path, fields, kind, idField, activeRecordRefs, entityRefs, library
) {
  const result = await readCognitiveJsonFile(root, path, cognitiveRevisionFileName(ref));
  if (!result.exists || result.sha256 !== ref.revision_sha256) {
    throw new Error(`${kind} revision hash 不一致`);
  }
  const revision = cognitiveExactObject(result.value, fields, `${kind} revision`);
  if (revision.schema_version !== '1.0'
      || revision.kind !== kind
      || revision[idField] !== ref.id
      || revision.revision !== ref.revision
      || revision.status !== 'active') {
    throw new Error(`${kind} revision 不再是当前 active 对象`);
  }
  cognitiveValidateSourceSpans(revision.source_spans, activeRecordRefs, library, kind);
  if (kind === 'memento_relation_revision') {
    const fromRef = library.validateObjectRef(revision.from_ref);
    const toRef = library.validateObjectRef(revision.to_ref);
    if (!cognitiveSameObjectRef(entityRefs.get(fromRef.id), fromRef)
        || !cognitiveSameObjectRef(entityRefs.get(toRef.id), toRef)) {
      throw new Error('relation revision 端点没有绑定当前地景对象');
    }
    library.validateCognitiveActionPayload('edit_relation', {
      type: revision.type,
      statement: revision.statement,
    });
    if ((revision.type === 'same_topic') !== (revision.direction === 'undirected')) {
      throw new Error('relation revision 编辑字段无效');
    }
  } else if (kind === 'memento_reusable_memory_revision') {
    library.validateCognitiveActionPayload('edit_reusable_memory', {
      statement: revision.statement,
      topics: revision.topics,
      purposes: revision.purposes,
    });
  }
  return revision;
}

async function readCognitiveFormalAuthority(root, localDate, landscape, recordAuthority, library) {
  const catalogResult = await readCognitiveJsonFile(
    root, COGNITIVE_HOME_ROOT_PATH, 'formal-head-index.json'
  );
  const catalog = library.validateFormalHeadIndex(catalogResult.exists
    ? catalogResult.value
    : {
      schema_version: '1.0',
      kind: 'memento_cognitive_formal_head_index',
      revision: 0,
      generated_at: '1970-01-01T00:00:00+00:00',
      daily_bundles: [], daily_summaries: [], reusable_memories: [], relations: [],
    });
  const catalogMemories = cognitiveRefMap(catalog.reusable_memories);
  const catalogRelations = cognitiveRefMap(catalog.relations);
  const entityRefs = new Map([
    ...landscape.nodes.map(node => [node.memory_ref.id, node.memory_ref]),
    ...landscape.peaks.map(peak => [peak.understanding_ref.id, peak.understanding_ref]),
  ]);
  const memoryRevisions = new Map();
  const relationRevisions = new Map();
  for (const node of landscape.nodes) {
    if (!cognitiveSameObjectRef(catalogMemories.get(node.memory_ref.id), node.memory_ref)) {
      throw new Error('landscape memory 不是当前 formal head');
    }
    const revision = await cognitiveValidateFormalRef(
      root, node.memory_ref, COGNITIVE_MEMORY_REVISION_PATH,
      COGNITIVE_MEMORY_REVISION_FIELDS, 'memento_reusable_memory_revision',
      'memory_id', recordAuthority.activeRefs, entityRefs, library
    );
    memoryRevisions.set(node.memory_ref.id, { ref: node.memory_ref, value: revision });
  }
  for (const edge of landscape.edges) {
    if (!cognitiveSameObjectRef(catalogRelations.get(edge.relation_ref.id), edge.relation_ref)) {
      throw new Error('landscape relation 不是当前 formal head');
    }
    const revision = await cognitiveValidateFormalRef(
      root, edge.relation_ref, COGNITIVE_RELATION_REVISION_PATH,
      COGNITIVE_RELATION_REVISION_FIELDS, 'memento_relation_revision',
      'relation_id', recordAuthority.activeRefs, entityRefs, library
    );
    relationRevisions.set(edge.relation_ref.id, { ref: edge.relation_ref, value: revision });
  }

  const bundleId = `db_${localDate.replaceAll('-', '')}`;
  const bundleRef = catalog.daily_bundles.find(ref => ref.id === bundleId) || null;
  if (bundleRef) {
    const directoryName = `day_${bundleId.slice(3)}.r${String(bundleRef.revision).padStart(6, '0')}`;
    const manifestResult = await readCognitiveJsonFile(
      root, [...COGNITIVE_BUNDLE_COMMITTED_PATH, directoryName], 'manifest.json'
    );
    const manifest = cognitiveExactObject(
      manifestResult.value, COGNITIVE_BUNDLE_MANIFEST_FIELDS, 'daily bundle manifest'
    );
    if (!manifestResult.exists
        || manifestResult.sha256 !== bundleRef.revision_sha256
        || manifest.schema_version !== '1.0'
        || manifest.kind !== 'memento_daily_bundle_revision'
        || manifest.bundle_id !== bundleRef.id
        || manifest.revision !== bundleRef.revision
        || manifest.local_date !== localDate
        || manifest.status !== 'committed') {
      throw new Error('daily bundle ref 没有绑定已提交 manifest');
    }
  }
  const dayHash = library.sha256Hex(library.canonicalJson(bundleRef ? [bundleRef] : []));
  return {
    currentMemoryRefs: landscape.nodes.map(node => node.memory_ref)
      .sort((left, right) => left.id.localeCompare(right.id)),
    currentRelationRefs: landscape.edges.map(edge => edge.relation_ref)
      .sort((left, right) => left.id.localeCompare(right.id)),
    dailyBundleHeadSha256: library.sha256Hex(library.canonicalJson({ day: dayHash, catalog })),
    memoryRevisions,
    relationRevisions,
  };
}

async function readCognitiveProjectionAuthorityBase(root, home, landscape, library) {
  const recordAuthority = await readCognitiveRecordAuthority(root, home.local_date, library);
  const [userActionWatermarkSha256, receiptAuthority, formal] = await Promise.all([
    readCognitiveActionWatermark(root, library),
    readCognitiveReceiptAuthority(root, recordAuthority.todayRefs, library),
    readCognitiveFormalAuthority(root, home.local_date, landscape, recordAuthority, library),
  ]);
  return {
    userActionWatermarkSha256,
    todayRecordRefs: recordAuthority.todayRefs,
    todayReceiptRefs: receiptAuthority.refs,
    receiptRevisions: receiptAuthority.revisions,
    recordLocators: recordAuthority.recordLocators,
    ...formal,
  };
}

function failCognitiveHomeAuthority(error) {
  console.warn('认知主页投影未通过当前 head 授权，继续使用记录主页', error);
  cognitiveHomeState.status = 'invalid';
  cognitiveHomeState.home = null;
  cognitiveHomeState.landscape = null;
  cognitiveHomeState.landscapeSha256 = '';
  cognitiveHomeState.recordLocators = new Map();
  cognitiveHomeState.verifiedReceipts = new Map();
  cognitiveHomeState.verifiedMemories = new Map();
  cognitiveHomeState.verifiedRelations = new Map();
  cognitiveHomeState.candidate = null;
  cognitiveHomeState.stale = false;
  cognitiveHomeState.issue = shortError(error);
  renderDashboard();
}

function finalizeCognitiveHomeAuthority() {
  const candidate = cognitiveHomeState.candidate;
  if (!candidate || !['authorizing', 'ready'].includes(cognitiveHomeState.status)) return;
  if (!contextAgentState.loaded) return;
  try {
    const profile = contextAgentState.agentProfile;
    if (!profile || !contextAgentState.agentProfileAuthoritative) {
      throw new Error('当前 Agent profile 未通过权威校验');
    }
    const activeUnderstandingRefs = profile.memories
      .filter(memory => memory.status === 'active' && memory.revision >= 1)
      .map(memory => cognitiveHomeLibrary().validateObjectRef({
        kind: 'understanding', id: memory.memoryId, revision: memory.revision,
        revision_sha256: memory.revisionSha256,
      }))
      .sort((left, right) => left.id.localeCompare(right.id));
    const authority = {
      agent_profile_sha256: profile.profileSha256,
      active_understanding_refs: activeUnderstandingRefs,
      current_memory_refs: candidate.authorityBase.currentMemoryRefs,
      current_relation_refs: candidate.authorityBase.currentRelationRefs,
      user_action_watermark_sha256: candidate.authorityBase.userActionWatermarkSha256,
      today_record_refs: candidate.authorityBase.todayRecordRefs,
      today_receipt_refs: candidate.authorityBase.todayReceiptRefs,
      daily_bundle_head_sha256: candidate.authorityBase.dailyBundleHeadSha256,
    };
    cognitiveHomeLibrary().validateProjectionAuthority(
      candidate.home, candidate.landscape, authority
    );
    cognitiveHomeState.status = 'ready';
    cognitiveHomeState.home = candidate.home;
    cognitiveHomeState.landscape = candidate.landscape;
    cognitiveHomeState.landscapeSha256 = candidate.landscapeSha256;
    cognitiveHomeState.recordLocators = candidate.authorityBase.recordLocators;
    cognitiveHomeState.verifiedReceipts = candidate.authorityBase.receiptRevisions;
    cognitiveHomeState.verifiedMemories = candidate.authorityBase.memoryRevisions;
    cognitiveHomeState.verifiedRelations = candidate.authorityBase.relationRevisions;
    cognitiveHomeState.stale = candidate.home.local_date !== getLocalDate();
    cognitiveHomeState.issue = '';
    renderDashboard();
  } catch (error) {
    failCognitiveHomeAuthority(error);
  }
}

function cognitiveProjectionIsLegacy(value) {
  return Boolean(value && typeof value === 'object'
    && value.kind === 'memento_home_projection'
    && value.projection_version !== 'cognitive-secretary-home-v1');
}

async function refreshCognitiveHomeProjection(handle, generation) {
  const readId = ++cognitiveHomeState.readId;
  if (cognitiveHomeState.status !== 'ready') {
    cognitiveHomeState.status = 'loading';
    cognitiveHomeState.issue = '';
    renderDashboard();
  }

  try {
    const library = cognitiveHomeLibrary();
    const homeResult = await readCognitiveJsonFile(
      handle,
      COGNITIVE_HOME_PROJECTION_PATH,
      'home_projection.json'
    );
    if (readId !== cognitiveHomeState.readId
        || state.dirHandle !== handle
        || !directoryLoadGate.isCurrent(generation)) return;
    if (!homeResult.exists) {
      cognitiveHomeState.status = 'missing';
      cognitiveHomeState.home = null;
      cognitiveHomeState.landscape = null;
      cognitiveHomeState.recordLocators = new Map();
      cognitiveHomeState.verifiedReceipts = new Map();
      cognitiveHomeState.verifiedMemories = new Map();
      cognitiveHomeState.verifiedRelations = new Map();
      cognitiveHomeState.candidate = null;
      renderDashboard();
      return;
    }
    if (cognitiveProjectionIsLegacy(homeResult.value)) {
      cognitiveHomeState.status = 'legacy';
      cognitiveHomeState.home = null;
      cognitiveHomeState.landscape = null;
      cognitiveHomeState.recordLocators = new Map();
      cognitiveHomeState.verifiedReceipts = new Map();
      cognitiveHomeState.verifiedMemories = new Map();
      cognitiveHomeState.verifiedRelations = new Map();
      cognitiveHomeState.candidate = null;
      renderDashboard();
      return;
    }

    const home = library.validateHomeProjection(homeResult.value);
    const landscapeResult = await readCognitiveJsonFile(
      handle,
      COGNITIVE_LANDSCAPE_PATH,
      `${home.landscape_ref.snapshot_id}.json`
    );
    if (readId !== cognitiveHomeState.readId
        || state.dirHandle !== handle
        || !directoryLoadGate.isCurrent(generation)) return;
    if (!landscapeResult.exists) throw new Error('主页引用的认知地景不存在');
    const landscape = library.validateLandscapeSnapshot(landscapeResult.value);
    library.validateProjectionPair(home, landscape, landscapeResult.sha256);
    const authorityBase = await readCognitiveProjectionAuthorityBase(
      handle, home, landscape, library
    );
    if (readId !== cognitiveHomeState.readId
        || state.dirHandle !== handle
        || !directoryLoadGate.isCurrent(generation)) return;
    cognitiveHomeState.status = 'authorizing';
    cognitiveHomeState.home = null;
    cognitiveHomeState.landscape = null;
    cognitiveHomeState.landscapeSha256 = '';
    cognitiveHomeState.recordLocators = new Map();
    cognitiveHomeState.verifiedReceipts = new Map();
    cognitiveHomeState.verifiedMemories = new Map();
    cognitiveHomeState.verifiedRelations = new Map();
    cognitiveHomeState.candidate = {
      home, landscape, landscapeSha256: landscapeResult.sha256, authorityBase,
    };
    cognitiveHomeState.stale = false;
    cognitiveHomeState.issue = '';
    renderDashboard();
    finalizeCognitiveHomeAuthority();
  } catch (error) {
    if (readId !== cognitiveHomeState.readId
        || state.dirHandle !== handle
        || !directoryLoadGate.isCurrent(generation)) return;
    console.warn('认知主页投影未通过校验，继续使用记录主页', error);
    cognitiveHomeState.status = 'invalid';
    cognitiveHomeState.home = null;
    cognitiveHomeState.landscape = null;
    cognitiveHomeState.landscapeSha256 = '';
    cognitiveHomeState.recordLocators = new Map();
    cognitiveHomeState.verifiedReceipts = new Map();
    cognitiveHomeState.verifiedMemories = new Map();
    cognitiveHomeState.verifiedRelations = new Map();
    cognitiveHomeState.candidate = null;
    cognitiveHomeState.stale = false;
    cognitiveHomeState.issue = shortError(error);
    renderDashboard();
  }
}

const COGNITIVE_RECORD_STATUS_LABELS = Object.freeze({
  raw_saved: '原文已保存',
  processing: '正在整理这一条',
  ready: '已初步整理，等待今日归并',
  needs_review: '有一处需要你确认',
  no_candidate: '已检查，本条没有形成可归并内容',
  failed: '原文已保存，整理尚未完成',
  original_only: '仅保留原文',
  merged: '已进入今日归并',
});

const COGNITIVE_DAILY_STATUS_LABELS = Object.freeze({
  not_started: '今日尚未归并',
  running: '正在归并今天的内容',
  committed: '今日归并已完成',
  committed_with_warnings: '今日归并已完成，部分内容待核对',
  no_change: '本次归并没有形成新的长期变化',
  no_candidate: '今日记录已检查，没有形成可归并内容',
  no_records: '今天还没有可归并的记录',
  no_receipts: '今天的记录尚未完成逐条整理',
  stale: '今日归并需要重新核对',
  error: '今日归并尚未完成',
  budget_exhausted: '今日归并已暂停',
});

const COGNITIVE_CONTENT_LABELS = Object.freeze({
  quote: '引用', own_idea: '我的想法', observation: '观察', question: '问题',
  decision: '决定', action: '行动', experience: '经历', fact: '事实', learning: '学习',
});

const COGNITIVE_PURPOSE_LABELS = Object.freeze({
  find_later: '以后查找', continue_thinking: '继续思考', create: '用于创作',
  future_decision: '未来决策', action_clue: '行动线索', preserve_only: '只想保存',
});

const COGNITIVE_RELATION_LABELS = Object.freeze({
  supports: '支持', counterexample: '反例', revises: '修订',
  scope_boundary: '适用边界', same_topic: '同一主题',
});

const COGNITIVE_SOURCE_LABELS = Object.freeze({
  text: '文字', screenshot_ocr: '截图 OCR', voice_transcript: '语音转写',
  image_note: '图片记录', file_note: '文件记录',
});

const COGNITIVE_STANCE_LABELS = Object.freeze({
  agree: '赞同', doubt: '怀疑', reject: '反对', inspired: '被打动',
  self_observation: '自我观察', unresolved: '尚未理解', unknown: '未知',
});

const COGNITIVE_STATE_LABELS = Object.freeze({
  first_seen: '第一次出现', repeated: '反复出现', supports_existing: '支持旧观点',
  conflicts_existing: '形成冲突', revises_existing: '修订旧观点', verified: '已验证',
  unknown: '未知',
});

const COGNITIVE_WARNING_LABELS = Object.freeze({
  long_term_failed: '长期理解本次没有更新',
  landscape_failed: '认知地景本次没有更新',
  partial_source_unavailable: '部分来源暂时无法读取',
});

function cognitiveSameRef(left, right) {
  return Boolean(left && right
    && left.kind === right.kind
    && left.id === right.id
    && left.revision === right.revision
    && left.revision_sha256 === right.revision_sha256);
}

function cognitiveVerifiedUnderstanding(understandingRef) {
  const home = cognitiveHomeState.home;
  const profile = contextAgentState.agentProfile;
  if (!home || !profile || !contextAgentState.agentProfileAuthoritative
      || profile.profileSha256 !== home.input_hashes.agent_profile_sha256) return null;
  const memory = profile.memories.find(item => item.memoryId === understandingRef.id);
  if (!memory
      || memory.revision !== understandingRef.revision
      || memory.revisionSha256 !== understandingRef.revision_sha256) return null;
  return memory;
}

function cognitiveThemeCandidate(value, maximum) {
  const raw = typeof value === 'string' ? value.trim() : '';
  if (!raw || /[\r\n]/u.test(raw)) return '';
  const theme = raw.replace(/[\t ]+/gu, ' ');
  if (Array.from(theme).length > maximum) return '';
  return theme;
}

function cognitivePeakScope(peak, index) {
  const memory = cognitiveVerifiedUnderstanding(peak.understanding_ref);
  return memory?.scope?.trim() || `长期理解 ${String(index + 1).padStart(2, '0')}`;
}

function cognitivePeakTitle(peak, index) {
  const memory = cognitiveVerifiedUnderstanding(peak.understanding_ref);
  return cognitiveThemeCandidate(memory?.title, 18)
    || `长期理解 ${String(index + 1).padStart(2, '0')}`;
}

function cognitivePeakStatement(peak) {
  const memory = cognitiveVerifiedUnderstanding(peak.understanding_ref);
  return memory?.statement || memory?.title || '';
}

function cognitivePeakForUnderstanding(ref) {
  return cognitiveHomeState.landscape?.peaks.find(peak => cognitiveSameRef(peak.understanding_ref, ref)) || null;
}

function cognitivePeakIndex(peak) {
  return cognitiveHomeState.landscape?.peaks.indexOf(peak) ?? -1;
}

function cognitivePeakTitleById(identifier) {
  const peak = cognitiveHomeState.landscape?.peaks.find(item => item.understanding_ref.id === identifier);
  return peak ? cognitivePeakTitle(peak, cognitivePeakIndex(peak)) : '长期理解';
}

function cognitiveEntityTitle(identifier) {
  const peak = cognitiveHomeState.landscape?.peaks.find(item => item.understanding_ref.id === identifier);
  if (peak) return cognitivePeakTitle(peak, cognitivePeakIndex(peak));
  const nodeIndex = cognitiveHomeState.landscape?.nodes.findIndex(item => item.memory_ref.id === identifier) ?? -1;
  return nodeIndex >= 0 ? `可用记忆 ${String(nodeIndex + 1).padStart(2, '0')}` : '已提交对象';
}

function cognitiveTimeLabel(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '';
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date);
}

function cognitiveDateTimeLabel(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date);
}

function cognitiveTodayHeadline(home) {
  if (cognitiveDemoState.active) {
    return `<strong>今天 ${home.today_status.saved} 条记录</strong>`;
  }
  const label = COGNITIVE_DAILY_STATUS_LABELS[home.today_status.daily_run_status] || '今日状态未知';
  const next = home.schedule.enabled
    ? `下一次本地计划 ${String(home.schedule.hour).padStart(2, '0')}:${String(home.schedule.minute).padStart(2, '0')}`
    : '自动计划未开启';
  return `<strong>${escapeHtml(label)}</strong><span>${escapeHtml(next)}</span>`;
}

function cognitiveProjectionNotice(home) {
  if (cognitiveDemoState.active) {
    return { text: '', tone: '' };
  }
  const messages = [];
  let tone = '';
  if (cognitiveHomeState.actionNotice) {
    messages.push(cognitiveHomeState.actionNotice);
    tone = cognitiveHomeState.actionNoticeTone;
  }
  if (cognitiveHomeState.stale) {
    messages.push(`当前显示 ${home.local_date} 的上一份已校验投影；今天的投影尚未可用。`);
    tone = 'is-warning';
  }
  for (const warning of home.warnings) {
    if (warning === 'review_failed') continue;
    messages.push(COGNITIVE_WARNING_LABELS[warning] || '有一项本地结果未进入本次投影。');
    tone = 'is-warning';
  }
  if (home.today_status.daily_run_status === 'running') {
    messages.push('今天的归并仍在进行；地景继续显示上一份已提交结果。');
  }
  if (home.today_status.daily_run_status === 'no_receipts') {
    messages.push('今天有记录尚未完成逐条整理；地景继续显示上一份已提交结果。');
    tone = 'is-warning';
  }
  if (home.today_status.daily_run_status === 'no_candidate') {
    messages.push('今日记录已检查，本次没有形成可归并内容。');
  }
  if (['error', 'budget_exhausted'].includes(home.today_status.daily_run_status)) {
    messages.push('今天的归并尚未形成新的提交；原始记录仍已保留。');
    tone = 'is-error';
  }
  return { text: messages.join(' '), tone };
}

function cognitiveDemoPortraitItems() {
  return cognitiveDemoState.active && Array.isArray(cognitiveDemoState.fixture?.portrait)
    ? cognitiveDemoState.fixture.portrait : [];
}

function cognitiveDemoThemeById(identifier) {
  return cognitiveDemoState.fixture?.themes?.find(theme => (
    theme.id === identifier || theme.understandingId === identifier
  )) || null;
}

function cognitivePortraitThemeTitles(item) {
  return (item?.themeIds || []).map(cognitiveDemoThemeById).filter(Boolean)
    .map(theme => theme.title);
}

const COGNITIVE_PORTRAIT_MATURITY = Object.freeze({
  forming: '形成中',
  stable: '已稳定',
});

function cognitivePortraitMaturity(item) {
  return Object.prototype.hasOwnProperty.call(COGNITIVE_PORTRAIT_MATURITY, item?.maturity)
    ? item.maturity : 'forming';
}

function cognitivePortraitMaturityLabel(item) {
  return COGNITIVE_PORTRAIT_MATURITY[cognitivePortraitMaturity(item)];
}

function cognitivePortraitEntryAttributes(item) {
  const themeIds = (item?.themeIds || []).filter(Boolean).join(' ');
  return `data-cognitive-portrait-id="${escapeHtml(item.id)}" data-cognitive-portrait-theme-ids="${escapeHtml(themeIds)}" data-portrait-maturity="${escapeHtml(cognitivePortraitMaturity(item))}" aria-pressed="false"`;
}

function cognitivePortraitOrbitMarkup(item) {
  const maturity = cognitivePortraitMaturity(item);
  return `<span class="cognitive-portrait-orbit" aria-hidden="true">
    ${[1, 2, 3].map(index => `<span class="cognitive-portrait-orbit-ring cognitive-portrait-orbit-ring--${index}"
      data-cognitive-orbit-portrait-id="${escapeHtml(item.id)}"
      data-portrait-maturity="${escapeHtml(maturity)}"></span>`).join('')}
    <span class="cognitive-portrait-orbit-core"></span>
  </span>`;
}

function renderCognitivePortrait() {
  const section = document.getElementById('cognitive-portrait-section');
  const feature = document.getElementById('cognitive-portrait-feature');
  const list = document.getElementById('cognitive-portrait-list');
  const empty = document.getElementById('cognitive-portrait-empty');
  const count = document.getElementById('cognitive-portrait-count');
  if (!section || !feature || !list || !empty || !count) return;

  clearCognitivePortraitLinkFocus();
  const items = cognitiveDemoPortraitItems();
  const linkedThemeCount = new Set(items.flatMap(item => item.themeIds || [])).size;
  count.textContent = items.length
    ? `${items.length} 条${linkedThemeCount ? ` · ${linkedThemeCount} 主题` : ''}`
    : '';
  if (!items.length) {
    feature.hidden = true;
    feature.removeAttribute('role');
    feature.removeAttribute('tabindex');
    delete feature.dataset.cognitivePortraitId;
    delete feature.dataset.cognitivePortraitThemeIds;
    delete feature.dataset.cognitivePortraitMaturity;
    delete feature.dataset.cognitiveOrbitSignature;
    delete list.dataset.cognitivePortraitSignature;
    feature.replaceChildren();
    list.replaceChildren();
    empty.hidden = false;
    return;
  }

  const [primary, ...secondary] = items;
  const primaryMaturity = cognitivePortraitMaturity(primary);
  const primaryMaturityLabel = cognitivePortraitMaturityLabel(primary);
  const primaryThemes = cognitivePortraitThemeTitles(primary);
  const orbitSignature = `${primary.id}:${primaryMaturity}:${primary.title}:${primaryThemes.join('|')}`;
  const listSignature = secondary.map(item => (
    `${item.id}:${cognitivePortraitMaturity(item)}:${item.title}:${cognitivePortraitThemeTitles(item).join('|')}`
  )).join('||');
  feature.hidden = false;
  feature.setAttribute('role', 'button');
  feature.setAttribute('tabindex', '0');
  feature.setAttribute('aria-pressed', 'false');
  feature.dataset.cognitivePortraitId = primary.id;
  feature.dataset.cognitivePortraitThemeIds = (primary.themeIds || []).join(' ');
  feature.dataset.cognitivePortraitMaturity = primaryMaturity;
  feature.setAttribute('aria-label', `查看深层理解：${primary.title}，状态：${primaryMaturityLabel}`);
  if (!feature.querySelector('.cognitive-portrait-orbit')
      || feature.dataset.cognitiveOrbitSignature !== orbitSignature) {
    feature.innerHTML = `
      ${cognitivePortraitOrbitMarkup(primary)}
      <span class="cognitive-portrait-feature-copy">
        <h3>${escapeHtml(primary.title)}</h3>
        <small>${escapeHtml(primaryThemes.join(' · '))}</small>
      </span>
      <span class="cognitive-portrait-maturity" data-portrait-maturity="${escapeHtml(primaryMaturity)}">${escapeHtml(primaryMaturityLabel)}</span>`;
    feature.dataset.cognitiveOrbitSignature = orbitSignature;
  } else {
    const maturity = feature.querySelector('.cognitive-portrait-maturity');
    const title = feature.querySelector('h3');
    const themes = feature.querySelector('small');
    if (maturity) {
      maturity.dataset.portraitMaturity = primaryMaturity;
      maturity.textContent = primaryMaturityLabel;
    }
    if (title) title.textContent = primary.title;
    if (themes) themes.textContent = primaryThemes.join(' · ');
  }

  if (list.dataset.cognitivePortraitSignature !== listSignature) {
    list.innerHTML = secondary.map(item => {
      const themes = cognitivePortraitThemeTitles(item);
      const maturity = cognitivePortraitMaturity(item);
      const maturityLabel = cognitivePortraitMaturityLabel(item);
      return `<button type="button" class="cognitive-portrait-item" ${cognitivePortraitEntryAttributes(item)}
        aria-label="查看深层理解：${escapeHtml(item.title)}，状态：${escapeHtml(maturityLabel)}">
        ${cognitivePortraitOrbitMarkup(item)}
        <span class="cognitive-portrait-item-copy"><strong>${escapeHtml(item.title)}</strong>
          <small>${escapeHtml(themes.join(' · '))}</small></span>
        <span class="cognitive-portrait-maturity" data-portrait-maturity="${escapeHtml(maturity)}">${escapeHtml(maturityLabel)}</span>
      </button>`;
    }).join('');
    list.dataset.cognitivePortraitSignature = listSignature;
  }
  empty.hidden = true;
}

function renderCognitiveHome() {
  const shell = document.getElementById('cognitive-home-shell');
  const legacy = document.getElementById('legacy-dashboard-shell');
  if (!shell || !legacy) return;
  const ready = cognitiveHomeState.status === 'ready'
    && cognitiveHomeState.home
    && cognitiveHomeState.landscape;
  shell.hidden = !ready;
  legacy.hidden = Boolean(ready && !cognitiveHomeState.outputOpen);
  document.body.classList.toggle('cognitive-home-active', Boolean(ready));
  if (!ready) return;

  const home = cognitiveHomeState.home;
  const landscape = cognitiveHomeState.landscape;
  const homeSummary = document.getElementById('cognitive-home-summary');
  if (cognitiveDemoState.active) {
    const fixture = cognitiveDemoState.fixture;
    const themeCount = fixture?.themes?.length || landscape.peaks.length;
    const changeCount = fixture?.changes?.length || landscape.summary.recent_changes;
    const recordCount = fixture?.stats?.totalRecords || fixture?.records?.length || 0;
    homeSummary.innerHTML = `
      <span><strong>${themeCount}</strong> 个聚合主题</span>
      <span><strong>${changeCount}</strong> 项近期变化</span>
      <span><strong>${recordCount}</strong> 条记录</span>`;
  } else {
    homeSummary.innerHTML = `
      <span><strong>${landscape.summary.active_understandings}</strong> 项当前理解</span>
      <span><strong>${landscape.summary.recent_changes}</strong> 项近期变化</span>
      <span><strong>${landscape.nodes.length}</strong> 个可用记忆点</span>
      ${landscape.summary.observing_candidates
        ? `<span><strong>${landscape.summary.observing_candidates}</strong> 项仍在观察</span>` : ''}`;
  }

  const notice = document.getElementById('cognitive-projection-notice');
  const noticeValue = cognitiveProjectionNotice(home);
  notice.textContent = noticeValue.text;
  notice.hidden = !noticeValue.text;
  notice.className = `cognitive-projection-notice${noticeValue.tone ? ` ${noticeValue.tone}` : ''}`;

  document.getElementById('cognitive-landscape-caption').textContent = cognitiveDemoState.active
    ? ''
    : landscape.peaks.length
      ? '山峰来自当前 active 的长期理解；点和连线只来自已提交对象。'
      : '还没有足够证据形成长期理解；今天的记录仍会继续被保留和整理。';
  document.getElementById('cognitive-today-caption').textContent = cognitiveDemoState.active
    ? ''
    : cognitiveHomeState.stale
      ? `${home.local_date} 的逐条整理回执。主页没有把它误标成今天。`
      : '原文先保存；主页展示逐条整理后的结果和去向。';
  document.getElementById('cognitive-today-status-copy').innerHTML = cognitiveTodayHeadline(home);
  const manualDayButton = document.getElementById('cognitive-manual-day-button');
  const manualDayRunning = home.today_status.daily_run_status === 'running';
  manualDayButton.hidden = cognitiveDemoState.active;
  manualDayButton.disabled = cognitiveHomeState.manualDayMutating || manualDayRunning;
  manualDayButton.textContent = manualDayRunning || cognitiveHomeState.manualDayMutating
      ? '正在积累'
      : '积累今天';
  const manualDayStatus = document.getElementById('cognitive-manual-day-status');
  manualDayStatus.textContent = cognitiveHomeState.manualDayNotice;
  manualDayStatus.hidden = !cognitiveHomeState.manualDayNotice;
  manualDayStatus.className = `cognitive-manual-day-status${cognitiveHomeState.manualDayNoticeTone
    ? ` ${cognitiveHomeState.manualDayNoticeTone}` : ''}`;
  document.getElementById('cognitive-saved-count').textContent = String(home.today_status.saved);
  document.getElementById('cognitive-interpreted-count').textContent = String(home.today_status.interpreted);
  document.getElementById('cognitive-merged-count').textContent = String(home.today_status.merged);
  document.getElementById('cognitive-review-count').textContent = String(home.today_status.needs_review);

  renderCognitivePortrait();
  renderCognitiveLandscape();
  renderCognitiveUnderstandingList();
  renderCognitiveRecords();
  applyCognitiveView();
  initCognitiveHomeInteractions();
}

function cognitiveMapPoint(x, y) {
  return { x: 72 + Number(x) * 956, y: 50 + Number(y) * 390 };
}

function cognitiveContourPath(cx, cy, rx, ry, seed, level) {
  const library = cognitiveHomeLibrary();
  if (typeof library.organicContourPath !== 'function') {
    throw new Error('认知地景等高线模块未加载');
  }
  return library.organicContourPath(cx, cy, rx, ry, seed, level);
}

function cognitiveDistanceToSegmentSquared(x, y, from, to) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const lengthSquared = dx * dx + dy * dy || 1;
  const ratio = Math.max(0, Math.min(1, ((x - from.x) * dx + (y - from.y) * dy) / lengthSquared));
  const offsetX = x - (from.x + ratio * dx);
  const offsetY = y - (from.y + ratio * dy);
  return offsetX * offsetX + offsetY * offsetY;
}

function cognitiveTerrainPrimaryItem(peak, point) {
  const theme = cognitiveDemoState.active
    ? cognitiveDemoThemeByUnderstandingId(peak.understanding_ref.id) : null;
  return {
    peak,
    point,
    kind: 'primary',
    visualId: peak.understanding_ref.id,
    parentId: peak.understanding_ref.id,
    shape: theme?.terrain || {},
  };
}

function cognitiveTerrainSubpeakItems(landscape) {
  if (!cognitiveDemoState.active) return [];
  const formalIds = new Set(landscape.peaks.map(peak => peak.understanding_ref.id));
  const nodeById = new Map(landscape.nodes.map(node => [node.memory_ref.id, node]));
  return (cognitiveDemoState.fixture?.themes || []).flatMap(theme => {
    if (!formalIds.has(theme.understandingId)) return [];
    return (theme.subpeaks || []).flatMap((subpeak, index) => {
      const node = nodeById.get(subpeak.memoryId);
      if (!node) return [];
      return [{
        peak: {
        peak_id: `subpeak_${theme.id}_${subpeak.id || index}`,
        elevation: Number(subpeak.elevation || .45),
        evidence_count: Number(subpeak.evidenceCount || 8),
        recent_change: Boolean(subpeak.recent),
        },
        point: cognitiveMapPoint(node.x, node.y),
        kind: 'subpeak',
        visualId: subpeak.memoryId,
        parentId: theme.understandingId,
        title: String(subpeak.title || ''),
        shape: {
          spreadX: Number(subpeak.spreadX || 42),
          spreadY: Number(subpeak.spreadY || 24),
          angle: Number(subpeak.angle || 0),
        },
      }];
    });
  });
}

function cognitiveTerrainRidgeControl(from, to, seed) {
  const hash = Array.from(String(seed || '')).reduce(
    (value, character) => (value * 33 + character.charCodeAt(0)) % 997, 17
  );
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.max(1, Math.hypot(dx, dy));
  const bend = ((hash % 19) - 9) * Math.min(1.8, length / 90);
  return {
    x: (from.x + to.x) / 2 - (dy / length) * bend,
    y: (from.y + to.y) / 2 + (dx / length) * bend,
  };
}

function cognitiveTerrainContext(landscape, positions) {
  const primaryPeaks = landscape.peaks.map(peak => (
    cognitiveTerrainPrimaryItem(peak, positions.get(peak.understanding_ref.id))
  ));
  const subpeaks = cognitiveTerrainSubpeakItems(landscape);
  const primaryById = new Map(primaryPeaks.map(item => [item.parentId, item]));
  const ridges = subpeaks.map(item => {
    const parent = primaryById.get(item.parentId);
    return {
      id: `${item.visualId}:ridge`,
      parentId: item.parentId,
      from: parent.point,
      to: item.point,
      control: cognitiveTerrainRidgeControl(parent.point, item.point, item.visualId),
      width: item.kind === 'subpeak' ? 19 : 23,
      strength: .078 + Math.min(item.peak.evidence_count, 18) * .0015,
    };
  });
  const mappedNodeIds = new Set(subpeaks.map(item => item.visualId));
  const localRidgePairs = new Set(subpeaks.map(item => (
    [item.visualId, item.parentId].sort().join(':')
  )));
  return {
    peaks: [...primaryPeaks, ...subpeaks],
    primaryPeaks,
    nodes: landscape.nodes.filter(node => !mappedNodeIds.has(node.memory_ref.id))
      .map(node => ({ node, point: positions.get(node.memory_ref.id) })),
    bridges: landscape.edges.filter(edge => !localRidgePairs.has(
      [edge.from_id, edge.to_id].sort().join(':')
    )).map(edge => ({
      edge,
      from: positions.get(edge.from_id),
      to: positions.get(edge.to_id),
      width: 24,
      strength: .052,
    })),
    ridges,
  };
}

function cognitivePeakTerrainContribution(item, x, y) {
  const evidenceCount = Number(item.peak.evidence_count || 0);
  const defaultWidth = item.kind === 'primary'
    ? 70 + Math.min(evidenceCount, 18) * 1.1 : 42;
  const defaultHeight = item.kind === 'primary'
    ? 35 + Math.min(evidenceCount, 18) * .7 : 24;
  const width = Number(item.shape?.spreadX || defaultWidth);
  const height = Number(item.shape?.spreadY || defaultHeight);
  const radians = Number(item.shape?.angle || 0) * Math.PI / 180;
  const cosine = Math.cos(radians);
  const sine = Math.sin(radians);
  const offsetX = x - item.point.x;
  const offsetY = y - item.point.y;
  const dx = (offsetX * cosine + offsetY * sine) / width;
  const dy = (-offsetX * sine + offsetY * cosine) / height;
  const amplitude = (item.kind === 'primary' ? .20 : .13)
    + Number(item.peak.elevation || .5) * (item.kind === 'primary' ? .34 : .31)
    + Math.log1p(evidenceCount) * (item.kind === 'primary' ? .018 : .012);
  return amplitude * Math.exp(-.5 * (dx * dx + dy * dy));
}

function cognitiveTerrainRidgeContribution(ridge, x, y) {
  const distanceSquared = Math.min(
    cognitiveDistanceToSegmentSquared(x, y, ridge.from, ridge.control || ridge.to),
    cognitiveDistanceToSegmentSquared(x, y, ridge.control || ridge.from, ridge.to)
  );
  return Number(ridge.strength || .052)
    * Math.exp(-distanceSquared / (2 * Number(ridge.width || 24) ** 2));
}

function cognitiveTerrainValue(x, y, context) {
  let value = 0;
  for (const item of context.peaks) {
    value += cognitivePeakTerrainContribution(item, x, y);
  }
  for (const item of context.nodes) {
    const dx = (x - item.point.x) / 38;
    const dy = (y - item.point.y) / 27;
    value += .064 * Math.exp(-.5 * (dx * dx + dy * dy));
  }
  for (const bridge of context.bridges) {
    value += cognitiveTerrainRidgeContribution(bridge, x, y);
  }
  for (const ridge of context.ridges) {
    value += cognitiveTerrainRidgeContribution(ridge, x, y);
  }
  return value;
}

function cognitiveInterpolateTerrainPoint(x1, y1, value1, x2, y2, value2, threshold) {
  const denominator = value2 - value1;
  const ratio = Math.abs(denominator) < .00001 ? .5 : (threshold - value1) / denominator;
  return { x: x1 + (x2 - x1) * ratio, y: y1 + (y2 - y1) * ratio };
}

function cognitiveTerrainSegments(values, columns, rows, step, originX, originY, threshold) {
  const segments = [];
  const edgePoint = (edge, column, row, corners) => {
    const x = originX + column * step;
    const y = originY + row * step;
    if (edge === 'top') return cognitiveInterpolateTerrainPoint(x, y, corners.tl, x + step, y, corners.tr, threshold);
    if (edge === 'right') return cognitiveInterpolateTerrainPoint(x + step, y, corners.tr, x + step, y + step, corners.br, threshold);
    if (edge === 'bottom') return cognitiveInterpolateTerrainPoint(x, y + step, corners.bl, x + step, y + step, corners.br, threshold);
    return cognitiveInterpolateTerrainPoint(x, y, corners.tl, x, y + step, corners.bl, threshold);
  };
  for (let row = 0; row < rows - 1; row += 1) {
    for (let column = 0; column < columns - 1; column += 1) {
      const index = row * columns + column;
      const corners = {
        tl: values[index], tr: values[index + 1],
        bl: values[index + columns], br: values[index + columns + 1],
      };
      const states = {
        tl: corners.tl >= threshold, tr: corners.tr >= threshold,
        br: corners.br >= threshold, bl: corners.bl >= threshold,
      };
      const crossings = [];
      if (states.tl !== states.tr) crossings.push('top');
      if (states.tr !== states.br) crossings.push('right');
      if (states.bl !== states.br) crossings.push('bottom');
      if (states.tl !== states.bl) crossings.push('left');
      if (crossings.length === 2) {
        segments.push(crossings.map(edge => edgePoint(edge, column, row, corners)));
      } else if (crossings.length === 4) {
        const centerHigh = (corners.tl + corners.tr + corners.br + corners.bl) / 4 >= threshold;
        const pairs = states.tl === centerHigh
          ? [['top', 'right'], ['bottom', 'left']]
          : [['top', 'left'], ['right', 'bottom']];
        pairs.forEach(pair => segments.push(pair.map(edge => edgePoint(edge, column, row, corners))));
      }
    }
  }
  return segments;
}

function cognitiveTerrainPointKey(point) {
  return `${Math.round(point.x * 2)},${Math.round(point.y * 2)}`;
}

function cognitiveStitchTerrainSegments(segments) {
  const edges = segments.map(([from, to]) => ({ from, to }));
  const adjacency = new Map();
  edges.forEach((edge, edgeIndex) => {
    [edge.from, edge.to].forEach(point => {
      const key = cognitiveTerrainPointKey(point);
      if (!adjacency.has(key)) adjacency.set(key, []);
      adjacency.get(key).push(edgeIndex);
    });
  });
  const visited = new Set();
  const lines = [];
  const walk = (firstEdgeIndex, firstKey) => {
    const points = [];
    let edgeIndex = firstEdgeIndex;
    let currentKey = firstKey;
    let guard = 0;
    while (!visited.has(edgeIndex) && guard <= edges.length) {
      guard += 1;
      visited.add(edgeIndex);
      const edge = edges[edgeIndex];
      const forward = cognitiveTerrainPointKey(edge.from) === currentKey;
      const start = forward ? edge.from : edge.to;
      const end = forward ? edge.to : edge.from;
      if (!points.length) points.push(start);
      points.push(end);
      currentKey = cognitiveTerrainPointKey(end);
      const next = (adjacency.get(currentKey) || []).find(candidate => !visited.has(candidate));
      if (next === undefined) break;
      edgeIndex = next;
    }
    return points;
  };
  adjacency.forEach((edgeIndices, key) => {
    if (edgeIndices.length !== 1 || visited.has(edgeIndices[0])) return;
    const points = walk(edgeIndices[0], key);
    if (points.length > 1) lines.push(points);
  });
  edges.forEach((edge, edgeIndex) => {
    if (visited.has(edgeIndex)) return;
    const points = walk(edgeIndex, cognitiveTerrainPointKey(edge.from));
    if (points.length > 1) lines.push(points);
  });
  return lines;
}

function cognitiveTerrainLinePath(points) {
  if (points.length < 2) return '';
  const first = points[0];
  const last = points[points.length - 1];
  const closed = cognitiveTerrainPointKey(first) === cognitiveTerrainPointKey(last) && points.length > 4;
  const line = closed ? points.slice(0, -1) : points;
  const midpoint = (left, right) => ({ x: (left.x + right.x) / 2, y: (left.y + right.y) / 2 });
  if (closed) {
    const start = midpoint(line[line.length - 1], line[0]);
    const parts = [`M ${start.x.toFixed(1)} ${start.y.toFixed(1)}`];
    line.forEach((point, index) => {
      const middle = midpoint(point, line[(index + 1) % line.length]);
      parts.push(`Q ${point.x.toFixed(1)} ${point.y.toFixed(1)} ${middle.x.toFixed(1)} ${middle.y.toFixed(1)}`);
    });
    return `${parts.join(' ')} Z`;
  }
  const parts = [`M ${line[0].x.toFixed(1)} ${line[0].y.toFixed(1)}`];
  for (let index = 1; index < line.length - 1; index += 1) {
    const middle = midpoint(line[index], line[index + 1]);
    parts.push(`Q ${line[index].x.toFixed(1)} ${line[index].y.toFixed(1)} ${middle.x.toFixed(1)} ${middle.y.toFixed(1)}`);
  }
  parts.push(`L ${line[line.length - 1].x.toFixed(1)} ${line[line.length - 1].y.toFixed(1)}`);
  return parts.join(' ');
}

function cognitiveTerrainLineIsClosed(points) {
  if (!Array.isArray(points) || points.length <= 4) return false;
  return cognitiveTerrainPointKey(points[0]) === cognitiveTerrainPointKey(points[points.length - 1]);
}

function cognitiveTerrainMarkup(landscape, positions) {
  const step = 8;
  const originX = 6;
  const originY = 6;
  const columns = 137;
  const rows = 64;
  const context = cognitiveTerrainContext(landscape, positions);
  const values = [];
  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      values.push(cognitiveTerrainValue(
        originX + column * step, originY + row * step, context
      ));
    }
  }
  const maximum = Math.max(...values);
  const requestedContours = Number(landscape.terrain.contour_levels || 20);
  const contourCount = Math.max(20, Math.min(24, requestedContours + 8));
  const bands = [];
  const contours = [];
  for (let index = 0; index < contourCount; index += 1) {
    const ratio = index / Math.max(1, contourCount - 1);
    const threshold = Math.max(.018, maximum * .055)
      + (maximum * .94 - Math.max(.018, maximum * .055)) * Math.pow(ratio, .74);
    const lines = cognitiveStitchTerrainSegments(cognitiveTerrainSegments(
      values, columns, rows, step, originX, originY, threshold
    ));
    const path = lines.map(cognitiveTerrainLinePath).filter(Boolean).join(' ');
    const closedPath = lines.filter(cognitiveTerrainLineIsClosed)
      .map(cognitiveTerrainLinePath).filter(Boolean).join(' ');
    if (closedPath && (index % 2 === 0 || index === contourCount - 1)) {
      bands.push(`<path class="cognitive-elevation-band cognitive-elevation-band-${index + 1}"
        data-cognitive-elevation="${index + 1}" fill-rule="evenodd" d="${closedPath}"></path>`);
    }
    if (!path) continue;
    contours.push(`<path class="cognitive-contour${index % 4 === 0 ? ' cognitive-contour-major' : ''}"
        data-cognitive-contour-level="${index + 1}"
        style="stroke-opacity:${(.16 + index * .019).toFixed(3)}" d="${path}"></path>`);
  }
  return {
    bands: bands.join(''),
    contours: contours.join(''),
  };
}

function cognitiveMapShortText(value, limit = 20) {
  const clean = String(value || '').replace(/\s+/g, ' ').trim();
  const characters = Array.from(clean);
  return characters.length > limit ? `${characters.slice(0, limit).join('')}…` : clean;
}

function cognitiveMapRecordConstellation(theme, peakIndex) {
  if (!cognitiveDemoState.active || !theme) return '';
  const recordIds = theme.evidenceRecordIds || [];
  const selectedIndexes = [0, Math.floor(recordIds.length / 2), Math.max(0, recordIds.length - 1)];
  const selected = [...new Set(selectedIndexes.map(index => recordIds[index]).filter(Boolean))];
  const baseAngle = (peakIndex * 47 - 32) * Math.PI / 180;
  return selected.map((recordId, index) => {
    const record = cognitiveDemoRecordMeta(recordId);
    const angle = baseAngle + index * (Math.PI * .78);
    const radiusX = 82 + index * 16;
    const radiusY = 58 + index * 12;
    const x = Math.cos(angle) * radiusX;
    const y = Math.sin(angle) * radiusY;
    const date = record?.date ? record.date.slice(5).replace('-', '.') : '';
    return `<g class="cognitive-map-record-star" aria-hidden="true">
      <path d="M 0 0 L ${x.toFixed(1)} ${y.toFixed(1)}"></path>
      <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${index === 0 ? 3.2 : 2.5}"></circle>
      <text x="${(x + 6).toFixed(1)}" y="${(y - 5).toFixed(1)}">${escapeHtml(date)}</text>
    </g>`;
  }).join('');
}

function cognitiveMapPeakDetailMarkup(peak, peakIndex) {
  if (!cognitiveDemoState.active) return '';
  const theme = cognitiveDemoThemeByUnderstandingId(peak.understanding_ref.id);
  if (!theme) return '';
  // 高倍层只展开记录星点。完整倾向与边界继续由主题抽屉承载，
  // 避免 SVG 里的浮动矩形遮住地形，或扩大可聚焦组的边界框。
  return cognitiveMapRecordConstellation(theme, peakIndex);
}

function cognitiveMapSubpeakMarkup(landscape) {
  if (!cognitiveDemoState.active) return '';
  const formalIds = new Set(landscape.peaks.map(peak => peak.understanding_ref.id));
  const nodeById = new Map(landscape.nodes.map(node => [node.memory_ref.id, node]));
  return (cognitiveDemoState.fixture?.themes || []).flatMap(theme => {
    if (!formalIds.has(theme.understandingId)) return [];
    return (theme.subpeaks || []).flatMap(subpeak => {
      const node = nodeById.get(subpeak.memoryId);
      if (!node) return [];
      const point = cognitiveMapPoint(node.x, node.y);
      const radius = 2.5 + Number(subpeak.elevation || .45) * 2.2;
      const recent = Boolean(subpeak.recent);
      return [`<g class="cognitive-subpeak${recent ? ' is-recent' : ''}" aria-hidden="true"
        data-cognitive-visual-entity="node" data-cognitive-id="${subpeak.memoryId || ''}"
        data-terrain-peak="${theme.understandingId}"
        transform="translate(${point.x.toFixed(2)} ${point.y.toFixed(2)})">
        <g class="cognitive-map-screen-space" data-cognitive-screen-space>
          <path class="cognitive-subpeak-summit" d="M 0 -${(radius + 8).toFixed(1)} L -3.1 -${(radius + 2.6).toFixed(1)} L 3.1 -${(radius + 2.6).toFixed(1)} Z"></path>
          <circle class="cognitive-subpeak-core" cx="0" cy="0" r="${radius.toFixed(1)}"></circle>
          <text class="cognitive-subpeak-label" x="0" y="-11.5">${escapeHtml(subpeak.title)}</text>
        </g>
      </g>`];
    });
  }).join('');
}

function cognitiveMapMemoryLabel(node, index) {
  const memory = cognitiveHomeState.verifiedMemories.get(node.memory_ref.id);
  const topic = Array.isArray(memory?.topics) ? memory.topics.find(Boolean) : '';
  return cognitiveMapShortText(topic || memory?.statement || `线索 ${String(index + 1).padStart(2, '0')}`, 12);
}

function cognitiveAtlasBackdropMarkup() {
  return `<defs>
      <pattern id="cognitive-atlas-minor-grid" width="20" height="20" patternUnits="userSpaceOnUse">
        <path id="cognitive-atlas-minor-grid-line" d="M 20 0 H 0 V 20" class="cognitive-atlas-minor-grid-line"></path>
      </pattern>
      <pattern id="cognitive-atlas-major-grid" width="100" height="100" patternUnits="userSpaceOnUse">
        <path id="cognitive-atlas-major-grid-line" d="M 100 0 H 0 V 100" class="cognitive-atlas-grid-line"></path>
        <path id="cognitive-atlas-cross-mark" d="M 47 50 H 53 M 50 47 V 53" class="cognitive-atlas-cross-mark"></path>
      </pattern>
    </defs>
    <rect class="cognitive-atlas-paper" x="0" y="0" width="1100" height="520"></rect>
    <rect class="cognitive-atlas-minor-grid" x="0" y="0" width="1100" height="520"></rect>
    <rect class="cognitive-atlas-grid" x="0" y="0" width="1100" height="520"></rect>
    <rect class="cognitive-atlas-frame" x="1" y="1" width="1098" height="518"></rect>`;
}

function cognitiveInsightConvexHull(points) {
  const sorted = [...points].sort((left, right) => left.x - right.x || left.y - right.y);
  if (sorted.length <= 2) return sorted;
  const cross = (origin, left, right) => (
    (left.x - origin.x) * (right.y - origin.y)
      - (left.y - origin.y) * (right.x - origin.x)
  );
  const half = source => {
    const result = [];
    for (const point of source) {
      while (result.length >= 2
          && cross(result[result.length - 2], result[result.length - 1], point) <= 0) {
        result.pop();
      }
      result.push(point);
    }
    return result;
  };
  const lower = half(sorted);
  const upper = half([...sorted].reverse());
  return [...lower.slice(0, -1), ...upper.slice(0, -1)];
}

function cognitiveInsightScaledHull(points, scale) {
  const center = points.reduce((total, point) => ({
    x: total.x + point.x / points.length,
    y: total.y + point.y / points.length,
  }), { x: 0, y: 0 });
  return points.map(point => ({
    x: center.x + (point.x - center.x) * scale,
    y: center.y + (point.y - center.y) * scale,
  }));
}

function cognitiveInsightSmoothPath(points) {
  if (points.length < 3) return '';
  const midpoint = (left, right) => ({
    x: (left.x + right.x) / 2,
    y: (left.y + right.y) / 2,
  });
  const start = midpoint(points[points.length - 1], points[0]);
  const commands = points.map((point, index) => {
    const next = points[(index + 1) % points.length];
    const end = midpoint(point, next);
    return `Q ${point.x.toFixed(2)} ${point.y.toFixed(2)} ${end.x.toFixed(2)} ${end.y.toFixed(2)}`;
  });
  return `M ${start.x.toFixed(2)} ${start.y.toFixed(2)} ${commands.join(' ')} Z`;
}

function cognitiveInsightRangeMarkup(positions) {
  if (!cognitiveDemoState.active) return '';
  return cognitiveDemoPortraitItems().map((item, itemIndex) => {
    const peaks = (item.themeIds || []).map(cognitiveDemoThemeById).filter(Boolean)
      .map(theme => positions.get(theme.understandingId)).filter(Boolean);
    if (peaks.length < 2) return '';
    const supportPoints = peaks.flatMap((peak, peakIndex) => {
      const radiusX = 56 + ((itemIndex + peakIndex) % 3) * 5;
      const radiusY = 34 + ((itemIndex * 2 + peakIndex) % 3) * 4;
      return Array.from({ length: 10 }, (_, angleIndex) => {
        const angle = Math.PI * 2 * angleIndex / 10;
        return {
          x: peak.x + Math.cos(angle) * radiusX,
          y: peak.y + Math.sin(angle) * radiusY,
        };
      });
    });
    const hull = cognitiveInsightConvexHull(supportPoints);
    const outerPath = cognitiveInsightSmoothPath(hull);
    const middlePath = cognitiveInsightSmoothPath(cognitiveInsightScaledHull(hull, .9));
    const innerPath = cognitiveInsightSmoothPath(cognitiveInsightScaledHull(hull, .8));
    return `<g class="cognitive-insight-range" data-cognitive-insight-range="${escapeHtml(item.id)}" aria-hidden="true">
      <path class="cognitive-insight-range-fill" d="${outerPath}"></path>
      <path class="cognitive-insight-range-contour cognitive-insight-range-contour--outer" d="${outerPath}"></path>
      <path class="cognitive-insight-range-contour cognitive-insight-range-contour--middle" d="${middlePath}"></path>
      <path class="cognitive-insight-range-contour cognitive-insight-range-contour--inner" d="${innerPath}"></path>
    </g>`;
  }).join('');
}

function renderCognitiveLandscape() {
  const svg = document.getElementById('cognitive-landscape-map');
  const empty = document.getElementById('cognitive-map-empty');
  const landscape = cognitiveHomeState.landscape;
  if (!svg || !empty || !landscape) return;

  if (!landscape.peaks.length) {
    svg.replaceChildren();
    svg.hidden = true;
    empty.hidden = false;
    empty.innerHTML = '<strong>地景仍在形成</strong><p>目前没有通过长期证据门的理解。Memento 会保留今天的逐条整理，不会先画出候选山峰。</p>';
    return;
  }
  svg.hidden = false;
  empty.hidden = true;

  const positions = new Map();
  landscape.peaks.forEach(peak => positions.set(peak.understanding_ref.id, cognitiveMapPoint(peak.x, peak.y)));
  landscape.nodes.forEach(node => positions.set(node.memory_ref.id, cognitiveMapPoint(node.x, node.y)));
  const terrainMarkup = cognitiveTerrainMarkup(landscape, positions);
  const insightRangeMarkup = cognitiveInsightRangeMarkup(positions);
  const subpeakMarkup = cognitiveMapSubpeakMarkup(landscape);

  const themeIds = new Set(landscape.peaks.map(peak => peak.understanding_ref.id));
  const displayEdges = landscape.edges;
  const edgeMarkup = displayEdges.map(edge => {
    const from = positions.get(edge.from_id);
    const to = positions.get(edge.to_id);
    const bend = ((parseInt(edge.relation_ref.id.slice(-2), 16) % 31) - 15) * .7;
    const mx = (from.x + to.x) / 2 + (to.y - from.y) * .06;
    const my = (from.y + to.y) / 2 - (to.x - from.x) * .025 + bend;
    const d = `M ${from.x.toFixed(2)} ${from.y.toFixed(2)} Q ${mx.toFixed(2)} ${my.toFixed(2)} ${to.x.toFixed(2)} ${to.y.toFixed(2)}`;
    const label = COGNITIVE_RELATION_LABELS[edge.type] || '正式关系';
    if (cognitiveDemoState.active) {
      const isThemeRelation = themeIds.has(edge.from_id) && themeIds.has(edge.to_id);
      return `<g class="cognitive-edge is-${edge.type} is-static-visual ${isThemeRelation ? 'is-default-relation' : 'is-evidence-relation'}" aria-hidden="true"
        data-cognitive-visual-entity="edge" data-cognitive-id="${edge.relation_ref.id}">
        <path class="cognitive-edge-visible" d="${d}"></path>
      </g>`;
    }
    return `<g class="cognitive-edge is-${edge.type}" role="button" tabindex="0"
      aria-label="${escapeHtml(label)}：${escapeHtml(cognitiveEntityTitle(edge.from_id))} 到 ${escapeHtml(cognitiveEntityTitle(edge.to_id))}"
      data-cognitive-entity="edge" data-cognitive-id="${edge.relation_ref.id}">
      <path class="cognitive-edge-visible" d="${d}"></path>
      <path class="cognitive-edge-hit" d="${d}"></path>
    </g>`;
  }).join('');

  const peakMarkup = landscape.peaks.map((peak, index) => {
    const point = positions.get(peak.understanding_ref.id);
    const outerX = 74 + peak.elevation * 42 + Math.min(peak.evidence_count, 12) * 2.4;
    const outerY = 48 + peak.elevation * 31 + Math.min(peak.evidence_count, 12) * 1.7;
    const scope = cognitivePeakScope(peak, index);
    const interactionAttributes = `role="button" tabindex="0" aria-pressed="false"
      aria-label="${escapeHtml(scope)}，${peak.evidence_count} 条依据，点击查看形成依据"
      data-cognitive-entity="peak" data-cognitive-id="${peak.understanding_ref.id}"`;
    return `<g class="cognitive-peak is-${peak.lifecycle}${peak.recent_change ? ' is-recent' : ''}"
      ${interactionAttributes}
      transform="translate(${point.x.toFixed(2)} ${point.y.toFixed(2)})">
      <g class="cognitive-map-screen-space" data-cognitive-screen-space>
        <ellipse class="cognitive-peak-hit" cx="0" cy="0" rx="${Math.max(66, outerX * .72)}" ry="${Math.max(42, outerY * .7)}"></ellipse>
        <path class="cognitive-peak-summit" d="M 0 -33 L -6 -22 L 6 -22 Z"></path>
        <text class="cognitive-peak-label" x="0" y="5">${escapeHtml(cognitivePeakTitle(peak, index))}</text>
        <text class="cognitive-peak-meta" x="0" y="23">${peak.evidence_count} 依据 · ${peak.counterevidence_count} 边界</text>
        ${peak.recent_change ? '<circle class="cognitive-peak-change-mark" cx="0" cy="34" r="2.1"></circle>' : ''}
        ${cognitiveDemoState.active ? '' : cognitiveMapPeakDetailMarkup(peak, index)}
      </g>
    </g>`;
  }).join('');

  const nodeMarkup = landscape.nodes.map((node, index) => {
    const point = positions.get(node.memory_ref.id);
    if (cognitiveDemoState.active) {
      // The twelve committed memories are already rendered as the twelve
      // visual-only subpeaks. Rendering the generic node glyph again would
      // double every summit and return the map to a diagram-like dot cloud.
      return '';
    }
    return `<g class="cognitive-node${node.recent ? ' is-recent' : ''}" role="button" tabindex="0"
      aria-label="可用记忆 ${String(index + 1).padStart(2, '0')}${node.recent ? '，近期归并' : ''}"
      data-cognitive-entity="node" data-cognitive-id="${node.memory_ref.id}"
      transform="translate(${point.x.toFixed(2)} ${point.y.toFixed(2)})">
      <g class="cognitive-map-screen-space" data-cognitive-screen-space>
        <circle class="cognitive-node-hit" cx="0" cy="0" r="15"></circle>
        <circle class="cognitive-node-visible" cx="0" cy="0" r="4.5"></circle>
      </g>
    </g>`;
  }).join('');

  svg.innerHTML = `
    <desc id="cognitive-map-desc">${cognitiveDemoState.active
      ? `${landscape.peaks.length} 个聚合主题，${landscape.nodes.length} 个可用记忆点，${displayEdges.length} 条已确认主题关系。位置仅用于排版；只有连线表示已确认关系；高度只表示证据积累，不表示重要程度或真实性。`
      : `${landscape.peaks.length} 座长期理解山峰，${landscape.nodes.length} 个可用记忆点，${landscape.edges.length} 条正式关系。`}</desc>
    <g class="cognitive-map-atlas">${cognitiveAtlasBackdropMarkup()}</g>
    <g class="cognitive-map-terrain"><g class="cognitive-elevation-bands">${terrainMarkup.bands}</g>${terrainMarkup.contours}</g>
    <g class="cognitive-map-insight-ranges">${insightRangeMarkup}</g>
    <g class="cognitive-map-edges">${edgeMarkup}</g>
    <g class="cognitive-map-subpeaks">${subpeakMarkup}</g>
    <g class="cognitive-map-peaks">${peakMarkup}</g>
    <g class="cognitive-map-nodes">${nodeMarkup}</g>`;
  cognitiveApplyMapCamera();
  restoreCognitiveMapInteractionState();
}

function cognitiveMapHoverContext(kind, identifier) {
  const landscape = cognitiveHomeState.landscape;
  const context = { peaks: new Set(), nodes: new Set(), edges: new Set() };
  if (!landscape) return context;
  if (kind === 'peak') context.peaks.add(identifier);
  if (kind === 'node') context.nodes.add(identifier);
  if (kind === 'edge') context.edges.add(identifier);
  for (const edge of landscape.edges) {
    const selected = edge.relation_ref.id === identifier
      || edge.from_id === identifier
      || edge.to_id === identifier;
    if (!selected) continue;
    context.edges.add(edge.relation_ref.id);
    for (const endpoint of [edge.from_id, edge.to_id]) {
      if (landscape.peaks.some(peak => peak.understanding_ref.id === endpoint)) {
        context.peaks.add(endpoint);
      }
      if (landscape.nodes.some(node => node.memory_ref.id === endpoint)) {
        context.nodes.add(endpoint);
      }
    }
  }
  return context;
}

function cognitiveMergeMapHoverContexts(contexts) {
  const merged = { peaks: new Set(), nodes: new Set(), edges: new Set() };
  for (const context of contexts) {
    for (const key of ['peaks', 'nodes', 'edges']) {
      for (const identifier of context?.[key] || []) merged[key].add(identifier);
    }
  }
  return merged;
}

function clearCognitivePortraitLinkFocus() {
  const section = document.getElementById('cognitive-portrait-section');
  if (!section) return;
  section.classList.remove('has-cognitive-link-focus');
  section.querySelectorAll('[data-cognitive-portrait-id]').forEach(element => {
    element.classList.remove('is-cognitive-related');
  });
  section.querySelectorAll('[data-cognitive-orbit-portrait-id]').forEach(element => {
    element.classList.remove('is-cognitive-related');
  });
}

function applyCognitivePortraitLinkFocus(portraitIds) {
  const section = document.getElementById('cognitive-portrait-section');
  if (!section) return;
  const related = portraitIds instanceof Set ? portraitIds : new Set(portraitIds || []);
  section.classList.toggle('has-cognitive-link-focus', related.size > 0);
  section.querySelectorAll('[data-cognitive-portrait-id]').forEach(element => {
    element.classList.toggle('is-cognitive-related', related.has(element.dataset.cognitivePortraitId));
  });
  section.querySelectorAll('[data-cognitive-orbit-portrait-id]').forEach(element => {
    element.classList.toggle('is-cognitive-related', related.has(element.dataset.cognitiveOrbitPortraitId));
  });
}

function cognitivePortraitIdsForPeak(identifier) {
  const theme = cognitiveDemoThemeByUnderstandingId(identifier);
  if (!theme) return new Set();
  return new Set(cognitiveDemoPortraitItems()
    .filter(item => (item.themeIds || []).includes(theme.id))
    .map(item => item.id));
}

function cognitivePeakIdsForPortrait(identifier) {
  const item = cognitiveDemoPortraitItems().find(portrait => portrait.id === identifier);
  if (!item) return [];
  return (item.themeIds || []).map(cognitiveDemoThemeById).filter(Boolean)
    .map(theme => theme.understandingId).filter(Boolean);
}

function cognitivePortraitItemById(identifier) {
  return cognitiveDemoPortraitItems().find(item => item.id === identifier) || null;
}

function clearCognitiveInsightMapFocus({ restoreFocus = false } = {}) {
  const trigger = cognitiveMapInteractionState.insightTrigger;
  cognitiveMapInteractionState.insightId = '';
  cognitiveMapInteractionState.insightTrigger = null;
  const svg = document.getElementById('cognitive-landscape-map');
  if (svg) {
    svg.classList.remove('has-cognitive-insight');
    svg.querySelectorAll('.is-cognitive-insight-active, .is-insight-related').forEach(element => {
      element.classList.remove('is-cognitive-insight-active', 'is-insight-related');
    });
  }
  const section = document.getElementById('cognitive-portrait-section');
  if (section) {
    section.querySelectorAll('[data-cognitive-portrait-id]').forEach(element => {
      element.classList.remove('is-cognitive-selected');
      element.setAttribute('aria-pressed', 'false');
    });
  }
  clearCognitivePortraitLinkFocus();
  if (restoreFocus && trigger instanceof HTMLElement && trigger.isConnected && !trigger.closest('[inert]')) {
    trigger.focus({ preventScroll: true });
  }
}

function applyCognitiveInsightMapFocus() {
  const identifier = cognitiveMapInteractionState.insightId;
  const item = cognitivePortraitItemById(identifier);
  const svg = document.getElementById('cognitive-landscape-map');
  const landscape = cognitiveHomeState.landscape;
  if (!item || !svg || !landscape) return;
  const peakIds = new Set(cognitivePeakIdsForPortrait(identifier));
  if (peakIds.size < 2) {
    clearCognitiveInsightMapFocus();
    return;
  }
  const relationIds = new Set(landscape.edges.filter(edge => (
    peakIds.has(edge.from_id) && peakIds.has(edge.to_id)
  )).map(edge => edge.relation_ref.id));
  svg.classList.remove('has-cognitive-hover', 'has-cognitive-pin');
  svg.classList.add('has-cognitive-insight');
  svg.querySelectorAll('.is-hover-related, .is-pinned-related, .is-pinned').forEach(element => {
    element.classList.remove('is-hover-related', 'is-pinned-related', 'is-pinned');
    if (element.hasAttribute('aria-pressed')) element.setAttribute('aria-pressed', 'false');
  });
  svg.querySelectorAll('[data-cognitive-insight-range]').forEach(element => {
    element.classList.toggle(
      'is-cognitive-insight-active',
      element.dataset.cognitiveInsightRange === identifier
    );
  });
  svg.querySelectorAll('[data-cognitive-entity="peak"]').forEach(element => {
    element.classList.toggle('is-insight-related', peakIds.has(element.dataset.cognitiveId));
  });
  svg.querySelectorAll('[data-terrain-peak]').forEach(element => {
    element.classList.toggle('is-insight-related', peakIds.has(element.dataset.terrainPeak));
  });
  svg.querySelectorAll('[data-cognitive-visual-entity="edge"]').forEach(element => {
    element.classList.toggle('is-insight-related', relationIds.has(element.dataset.cognitiveId));
  });
  applyCognitivePortraitLinkFocus(new Set([identifier]));
  const section = document.getElementById('cognitive-portrait-section');
  section?.querySelectorAll('[data-cognitive-portrait-id]').forEach(element => {
    const selected = element.dataset.cognitivePortraitId === identifier;
    element.classList.toggle('is-cognitive-selected', selected);
    element.setAttribute('aria-pressed', String(selected));
  });
}

function toggleCognitiveInsightMapFocus(identifier, trigger) {
  if (!identifier || !cognitivePortraitItemById(identifier)) return false;
  if (cognitiveMapInteractionState.insightId === identifier) {
    clearCognitiveInsightMapFocus();
    return false;
  }
  clearCognitiveInsightMapFocus();
  clearCognitiveMapPin();
  cognitiveMapInteractionState.hoverKind = '';
  cognitiveMapInteractionState.hoverId = '';
  cognitiveMapInteractionState.insightId = identifier;
  cognitiveMapInteractionState.insightTrigger = trigger || null;
  applyCognitiveInsightMapFocus();
  return Boolean(cognitiveMapInteractionState.insightId);
}

function clearCognitiveMapHover() {
  cognitiveMapInteractionState.hoverKind = '';
  cognitiveMapInteractionState.hoverId = '';
  if (cognitiveMapInteractionState.pinnedId) {
    applyCognitiveMapPinnedContext();
    return;
  }
  if (cognitiveMapInteractionState.insightId) {
    applyCognitiveInsightMapFocus();
    return;
  }
  const svg = document.getElementById('cognitive-landscape-map');
  if (svg) {
    svg.classList.remove('has-cognitive-hover');
    svg.classList.remove('has-cognitive-pin');
    svg.querySelectorAll('.is-hover-related').forEach(element => element.classList.remove('is-hover-related'));
    svg.querySelectorAll('.is-pinned-related, .is-pinned').forEach(element => {
      element.classList.remove('is-pinned-related', 'is-pinned');
      if (element.hasAttribute('aria-pressed')) element.setAttribute('aria-pressed', 'false');
    });
  }
  clearCognitivePortraitLinkFocus();
}

function applyCognitiveMapHoverContext(context) {
  const svg = document.getElementById('cognitive-landscape-map');
  if (!svg) return;
  svg.classList.remove('has-cognitive-hover');
  svg.querySelectorAll('.is-hover-related').forEach(element => element.classList.remove('is-hover-related'));
  svg.classList.add('has-cognitive-hover');
  svg.querySelectorAll('[data-cognitive-entity], [data-cognitive-visual-entity]').forEach(element => {
    const entityKind = element.dataset.cognitiveEntity || element.dataset.cognitiveVisualEntity;
    const entityId = element.dataset.cognitiveId;
    const related = entityKind === 'peak' ? context.peaks.has(entityId)
      : entityKind === 'node' ? context.nodes.has(entityId)
        : context.edges.has(entityId);
    element.classList.toggle('is-hover-related', related);
  });
  svg.querySelectorAll('[data-terrain-peak]').forEach(element => {
    element.classList.toggle('is-hover-related', context.peaks.has(element.dataset.terrainPeak));
  });
}

function applyCognitiveMapHover(kind, identifier) {
  if (!['peak', 'node', 'edge'].includes(kind)) return;
  if (cognitiveMapInteractionState.pinnedId || cognitiveMapInteractionState.insightId) return;
  cognitiveMapInteractionState.hoverKind = kind;
  cognitiveMapInteractionState.hoverId = identifier;
  applyCognitiveMapHoverContext(cognitiveMapHoverContext(kind, identifier));
  if (kind === 'peak') applyCognitivePortraitLinkFocus(cognitivePortraitIdsForPeak(identifier));
  else clearCognitivePortraitLinkFocus();
}

function applyCognitiveMapPinnedContext() {
  const { pinnedKind, pinnedId } = cognitiveMapInteractionState;
  const svg = document.getElementById('cognitive-landscape-map');
  if (!svg || !pinnedId) return;
  const context = cognitiveMapHoverContext(pinnedKind, pinnedId);
  svg.classList.remove('has-cognitive-hover');
  svg.classList.add('has-cognitive-pin');
  svg.querySelectorAll('.is-hover-related').forEach(element => element.classList.remove('is-hover-related'));
  svg.querySelectorAll('[data-cognitive-entity], [data-cognitive-visual-entity]').forEach(element => {
    const entityKind = element.dataset.cognitiveEntity || element.dataset.cognitiveVisualEntity;
    const entityId = element.dataset.cognitiveId;
    const related = entityKind === 'peak' ? context.peaks.has(entityId)
      : entityKind === 'node' ? context.nodes.has(entityId)
        : context.edges.has(entityId);
    element.classList.toggle('is-pinned-related', related);
    const selected = entityKind === pinnedKind && entityId === pinnedId;
    element.classList.toggle('is-pinned', selected);
    if (element.hasAttribute('aria-pressed')) element.setAttribute('aria-pressed', String(selected));
  });
  svg.querySelectorAll('[data-terrain-peak]').forEach(element => {
    element.classList.toggle('is-pinned-related', context.peaks.has(element.dataset.terrainPeak));
  });
  if (pinnedKind === 'peak') applyCognitivePortraitLinkFocus(cognitivePortraitIdsForPeak(pinnedId));
  else clearCognitivePortraitLinkFocus();
}

function clearCognitiveMapPin({ restoreFocus = false } = {}) {
  const trigger = cognitiveMapInteractionState.pinnedTrigger;
  cognitiveMapInteractionState.pinnedKind = '';
  cognitiveMapInteractionState.pinnedId = '';
  cognitiveMapInteractionState.pinnedTrigger = null;
  const svg = document.getElementById('cognitive-landscape-map');
  if (svg) {
    svg.classList.remove('has-cognitive-pin', 'has-cognitive-hover');
    svg.querySelectorAll('.is-pinned-related, .is-pinned, .is-hover-related').forEach(element => {
      element.classList.remove('is-pinned-related', 'is-pinned', 'is-hover-related');
      if (element.hasAttribute('aria-pressed')) element.setAttribute('aria-pressed', 'false');
    });
  }
  clearCognitivePortraitLinkFocus();
  if (restoreFocus && trigger instanceof HTMLElement && trigger.isConnected && !trigger.closest('[inert]')) {
    trigger.focus({ preventScroll: true });
  }
}

function toggleCognitiveMapPin(kind, identifier, trigger) {
  if (!['peak', 'node', 'edge'].includes(kind) || !identifier) return false;
  if (cognitiveMapInteractionState.insightId) clearCognitiveInsightMapFocus();
  if (cognitiveMapInteractionState.pinnedKind === kind
      && cognitiveMapInteractionState.pinnedId === identifier) {
    clearCognitiveMapPin();
    return false;
  }
  cognitiveMapInteractionState.hoverKind = '';
  cognitiveMapInteractionState.hoverId = '';
  cognitiveMapInteractionState.pinnedKind = kind;
  cognitiveMapInteractionState.pinnedId = identifier;
  cognitiveMapInteractionState.pinnedTrigger = trigger || null;
  applyCognitiveMapPinnedContext();
  return true;
}

function restoreCognitiveMapInteractionState() {
  if (cognitiveMapInteractionState.pinnedId) {
    applyCognitiveMapPinnedContext();
    return;
  }
  if (cognitiveMapInteractionState.insightId) {
    applyCognitiveInsightMapFocus();
    return;
  }
  if (cognitiveMapInteractionState.hoverId) {
    applyCognitiveMapHover(
      cognitiveMapInteractionState.hoverKind,
      cognitiveMapInteractionState.hoverId
    );
  }
}

function renderCognitiveUnderstandingList() {
  const container = document.getElementById('cognitive-list-region');
  const landscape = cognitiveHomeState.landscape;
  if (!container || !landscape) return;
  if (!landscape.peaks.length) {
    container.innerHTML = cognitiveDemoState.active
      ? '<div class="cognitive-record-empty"><strong>还没有形成聚合主题</strong><p>记录会继续保留，主题需要更多依据。</p></div>'
      : '<div class="cognitive-record-empty"><strong>还没有已提交的长期理解</strong><p>候选内容不会出现在这份列表中。</p></div>';
    return;
  }
  container.innerHTML = `<div class="cognitive-understanding-list">${landscape.peaks.map((peak, index) => {
    const linked = landscape.edges.filter(edge => edge.from_id === peak.understanding_ref.id
      || edge.to_id === peak.understanding_ref.id).length;
    const statement = cognitivePeakStatement(peak);
    return `<button type="button" class="cognitive-understanding-row"
      data-cognitive-entity="peak" data-cognitive-id="${peak.understanding_ref.id}">
      <span class="row-index">${String(index + 1).padStart(2, '0')}</span>
      <span class="cognitive-understanding-copy"><strong>${escapeHtml(cognitivePeakTitle(peak, index))}</strong>
        <small>${escapeHtml(statement || '完整理解正在等待当前版本的本地索引')}</small></span>
      <span>${peak.evidence_count} 条依据 · ${peak.counterevidence_count} 条${cognitiveDemoState.active ? '边界记录' : '反例'}</span>
      <span>${linked} 条正式关系${peak.recent_change ? ' · 近期变化' : ''}</span>
    </button>`;
  }).join('')}</div>`;
}

function cognitiveRecordDestination(record) {
  const titles = record.understanding_refs.map(ref => {
    const peak = cognitivePeakForUnderstanding(ref);
    return peak ? cognitivePeakTitle(peak, cognitivePeakIndex(peak)) : '';
  }).filter(Boolean);
  if (titles.length) return {
    title: titles.slice(0, 2).join('、'),
    detail: `${record.memory_refs.length} 个可用记忆 · ${titles.length} 个${cognitiveDemoState.active ? '聚合主题' : '长期理解'}`,
  };
  if (record.memory_refs.length) return {
    title: `${record.memory_refs.length} 个可用记忆`,
    detail: cognitiveDemoState.active ? '尚未关联聚合主题' : '尚未关联长期理解',
  };
  if (['ready', 'needs_review'].includes(record.status)) return {
    title: cognitiveDemoState.active ? '线索已保留' : '等待今日归并',
    detail: cognitiveDemoState.active ? '尚未进入主题地图' : '尚未进入正式地景',
  };
  return {
    title: cognitiveDemoState.active ? '尚未进入主题积累' : '尚未进入长期沉淀',
    detail: '原文仍已保留',
  };
}

function cognitiveRecordStatusLabel(record) {
  if (!cognitiveDemoState.active) return COGNITIVE_RECORD_STATUS_LABELS[record.status] || record.status;
  const labels = {
    merged: '已形成主题',
    ready: '已形成线索',
    needs_review: '含不确定',
    raw_saved: '仅保留原文',
    processing: '仅保留原文',
    failed: '仅保留原文',
    original_only: '仅保留原文',
    no_candidate: '尚未关联主题',
  };
  return labels[record.status] || '已保存';
}

function cognitiveRecordTimelineState(record) {
  if (record.status === 'needs_review') return 'uncertain';
  if (record.status === 'merged' || record.understanding_refs.length) return 'map';
  return 'ordinary';
}

function cognitiveRecordsNewestFirst(records) {
  return [...records].sort((left, right) => (
    right.captured_at.localeCompare(left.captured_at)
  ));
}

const cognitiveTimelineState = {
  initialized: false,
  recordCount: 0,
  atNow: true,
};

const COGNITIVE_RIVER_PROFILES = [
  {
    id: 'fine-stream',
    start: [0, 24, 4],
    curves: [
      [[48, 24, 4], [82, 21, 2.8], [142, 21, 2.8]],
      [[208, 21, 2.8], [274, 24, 4], [320, 24, 4]],
    ],
  },
  {
    id: 'calm-water',
    start: [0, 24, 4],
    curves: [
      [[72, 24, 4], [116, 22, 4.8], [180, 22, 4.8]],
      [[238, 22, 4.8], [282, 24, 4], [320, 24, 4]],
    ],
  },
  {
    id: 'small-bends',
    start: [0, 24, 4],
    curves: [
      [[38, 24, 4], [62, 17, 5.4], [116, 17, 5.4]],
      [[162, 17, 5.4], [176, 31, 6.2], [226, 31, 6.2]],
      [[272, 31, 5.2], [294, 24, 4], [320, 24, 4]],
    ],
  },
  {
    id: 'large-bend',
    start: [0, 24, 4],
    curves: [
      [[34, 24, 4], [58, 11, 7.2], [120, 11, 7.2]],
      [[178, 11, 7.2], [182, 37, 8.4], [242, 37, 8.4]],
      [[286, 37, 7], [300, 24, 4], [320, 24, 4]],
    ],
  },
  {
    id: 'wide-bay',
    start: [0, 24, 4],
    curves: [
      [[40, 24, 4], [70, 14, 7.6], [136, 14, 7.6]],
      [[214, 14, 8.6], [238, 31, 8.6], [278, 31, 7]],
      [[302, 31, 5.2], [310, 24, 4], [320, 24, 4]],
    ],
  },
];

const COGNITIVE_RIVER_WIDTH_SCALE = .58;

function cognitiveRiverSeed(value) {
  let hash = 2166136261;
  for (const character of String(value)) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function cognitiveRiverRandom(seed) {
  let state = seed || 1;
  return () => {
    state += 0x6D2B79F5;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function cognitiveRiverProfile(recordId, index) {
  const seed = cognitiveRiverSeed(`${recordId}:${index}`);
  const base = COGNITIVE_RIVER_PROFILES[seed % COGNITIVE_RIVER_PROFILES.length];
  const random = cognitiveRiverRandom(seed);
  const lastCurveIndex = base.curves.length - 1;
  const varyPoint = (point, fixed = false) => {
    if (fixed) return [...point];
    return [
      point[0] + (random() - .5) * 10,
      Math.max(9, Math.min(39, point[1] + (random() - .5) * 3.6)),
      Math.max(2.5, Math.min(9, point[2] + (random() - .5) * 1.5)),
    ];
  };
  return {
    id: base.id,
    start: [...base.start],
    curves: base.curves.map((curve, curveIndex) => [
      varyPoint(curve[0], curveIndex === 0),
      varyPoint(curve[1], curveIndex === lastCurveIndex),
      varyPoint(curve[2], curveIndex === lastCurveIndex),
    ]),
  };
}

function cognitiveRiverPoint(point, edge = 0) {
  return `${point[0].toFixed(1)} ${(point[1] + edge * point[2] * COGNITIVE_RIVER_WIDTH_SCALE).toFixed(1)}`;
}

function cognitiveRiverPath(profile, edge = 0) {
  return `M${cognitiveRiverPoint(profile.start, edge)}${profile.curves.map((curve) => (
    `C${cognitiveRiverPoint(curve[0], edge)} ${cognitiveRiverPoint(curve[1], edge)} ${cognitiveRiverPoint(curve[2], edge)}`
  )).join('')}`;
}

function cognitiveRiverWaterPath(profile) {
  const lastPoint = profile.curves[profile.curves.length - 1][2];
  const reverseCurves = profile.curves.map((curve, curveIndex) => {
    const previousPoint = curveIndex === 0 ? profile.start : profile.curves[curveIndex - 1][2];
    return { curve, previousPoint };
  }).reverse();
  return `${cognitiveRiverPath(profile, -1)}L${cognitiveRiverPoint(lastPoint, 1)}${reverseCurves.map(({ curve, previousPoint }) => (
    `C${cognitiveRiverPoint(curve[1], 1)} ${cognitiveRiverPoint(curve[0], 1)} ${cognitiveRiverPoint(previousPoint, 1)}`
  )).join('')}Z`;
}

function cognitiveRiverSegmentMarkup(recordId, index, total) {
  const profile = cognitiveRiverProfile(recordId, index);
  const recency = total > 1 ? 1 - index / (total - 1) : 1;
  const waterOpacity = .036 + recency * .022;
  const bankOpacity = .15 + recency * .045;
  const hasGlint = index === 0 || index % 4 === 1;
  const motionSeed = cognitiveRiverSeed(`${recordId}:river-motion`);
  const glintDelay = -((motionSeed % 90) / 10);
  const breatheDelay = -((motionSeed % 140) / 10);
  const breatheDuration = 13 + ((motionSeed >>> 8) % 36) / 10;
  return `<svg class="cognitive-river-channel" data-cognitive-river-profile="${profile.id}"
    viewBox="0 0 320 52" preserveAspectRatio="none" aria-hidden="true" focusable="false"
    style="--cognitive-river-water-opacity:${waterOpacity.toFixed(3)};--cognitive-river-bank-opacity:${bankOpacity.toFixed(3)};--cognitive-river-glint-delay:${glintDelay.toFixed(1)}s;--cognitive-river-breathe-delay:${breatheDelay.toFixed(1)}s;--cognitive-river-breathe-duration:${breatheDuration.toFixed(1)}s">
    <path class="cognitive-river-water" d="${cognitiveRiverWaterPath(profile)}"/>
    <path class="cognitive-river-bank" d="${cognitiveRiverPath(profile, -1)}"/>
    <path class="cognitive-river-bank cognitive-river-bank--far" d="${cognitiveRiverPath(profile, 1)}"/>
    <path class="cognitive-river-current-line" d="${cognitiveRiverPath(profile)}"/>
    ${hasGlint ? '<path class="cognitive-river-glint" d="M8 24C18 24 28 24 38 24"/>' : ''}
  </svg>`;
}

function cognitiveTimelineAtNow(list) {
  return list.scrollLeft <= 32;
}

function cognitiveTimelineNowControl() {
  return document.querySelector('[data-cognitive-timeline-now]');
}

function syncCognitiveTimelineNowControl(list) {
  const control = cognitiveTimelineNowControl();
  cognitiveTimelineState.atNow = cognitiveTimelineAtNow(list);
  if (!control) return;
  control.hidden = cognitiveTimelineState.atNow;
  control.setAttribute('aria-hidden', cognitiveTimelineState.atNow ? 'true' : 'false');
}

function scrollCognitiveTimelineToNow(options = {}) {
  const list = document.getElementById('cognitive-record-list');
  if (!list) return;
  const records = [...list.querySelectorAll('[data-cognitive-timeline-index]')];
  const latest = records.at(0);
  if (!latest) return;
  const left = 0;
  const behavior = options.behavior
    || (cognitiveReducedMotionMedia?.matches ? 'auto' : 'smooth');
  if (typeof list.scrollTo === 'function') {
    list.scrollTo({ left, behavior });
  } else {
    list.scrollLeft = left;
  }
  if (options.focus) latest.focus({ preventScroll: true });
  cognitiveTimelineState.atNow = true;
  const control = cognitiveTimelineNowControl();
  if (control) {
    control.hidden = true;
    control.setAttribute('aria-hidden', 'true');
  }
}

function settleCognitiveTimelineAfterRender(list, options) {
  requestAnimationFrame(() => {
    if (!list.isConnected) return;
    if (options.initialDemoRender || (options.recordCountChanged && options.wasAtNow)) {
      scrollCognitiveTimelineToNow({ behavior: 'auto' });
    } else {
      const scrollWidthDelta = options.recordCountChanged
        ? list.scrollWidth - options.previousScrollWidth
        : 0;
      const preservedScrollLeft = Math.max(0, options.previousScrollLeft + scrollWidthDelta);
      list.scrollLeft = Math.min(preservedScrollLeft, Math.max(0, list.scrollWidth - list.clientWidth));
      syncCognitiveTimelineNowControl(list);
    }
  });
}

function renderCognitiveRecords() {
  const list = document.getElementById('cognitive-record-list');
  const empty = document.getElementById('cognitive-record-empty');
  const home = cognitiveHomeState.home;
  if (!list || !empty || !home) return;
  if (!home.records.length) {
    list.replaceChildren();
    empty.hidden = false;
    empty.innerHTML = cognitiveHomeState.stale
      ? '<strong>这份投影没有记录</strong><p>当前展示的是上一份已校验结果；今天的新记录尚未进入主页投影。</p>'
      : '<strong>今天还没有留下记录</strong><p>Memento 会安静等待。下一条内容出现后，先确认原文已保存，再展示逐条整理状态。</p>';
    return;
  }
  empty.hidden = true;
  const previousScrollLeft = list.scrollLeft;
  const previousScrollWidth = list.scrollWidth;
  const wasAtNow = cognitiveTimelineState.initialized
    ? cognitiveTimelineAtNow(list) : true;
  const initialDemoRender = cognitiveDemoState.active && !cognitiveTimelineState.initialized;
  const recordCountChanged = cognitiveTimelineState.recordCount !== home.records.length;
  const recordsNewestFirst = cognitiveRecordsNewestFirst(home.records);
  list.dataset.cognitiveTimeline = 'today';
  list.innerHTML = recordsNewestFirst.map((record, index) => {
    const status = cognitiveRecordStatusLabel(record);
    const timelineState = cognitiveRecordTimelineState(record);
    const destination = cognitiveRecordDestination(record);
    const content = record.content_types.map(value => COGNITIVE_CONTENT_LABELS[value] || value);
    const purposes = record.purposes.map(value => COGNITIVE_PURPOSE_LABELS[value] || value);
    const raw = cognitiveDemoState.active ? cognitiveDemoRawText(record.record_ref.id) : '';
    const rawOnly = cognitiveDemoState.active
      && ['raw_saved', 'processing', 'failed', 'original_only', 'no_candidate'].includes(record.status);
    const summary = rawOnly && raw ? raw : record.summary || (['raw_saved', 'processing'].includes(record.status)
      ? '这条记录暂未关联主题' : '本次没有形成可展示的摘要');
    const facets = [...content.slice(0, 2), ...record.topics.slice(0, 2)].join(' · ');
    const timeLabel = cognitiveTimeLabel(record.captured_at);
    return `<button type="button" class="cognitive-record-row" data-cognitive-entity="record"
      data-cognitive-id="${record.record_ref.id}"
      data-cognitive-timeline-index="${index}"
      data-cognitive-timeline-state="${timelineState}"
      aria-label="${escapeHtml(timeLabel)}，${escapeHtml(status)}：${escapeHtml(summary)}">
      <span class="cognitive-record-axis">
        ${cognitiveRiverSegmentMarkup(record.record_ref.id, index, recordsNewestFirst.length)}
        <time class="cognitive-record-time" datetime="${escapeHtml(record.captured_at)}">${escapeHtml(timeLabel)}</time>
        <span class="cognitive-record-node" aria-hidden="true"></span>
        <span class="cognitive-record-stem" aria-hidden="true"></span>
      </span>
      <span class="cognitive-record-body">
        <span class="cognitive-record-meta">
          <span>${escapeHtml(record.source_app || COGNITIVE_SOURCE_LABELS[record.source_type] || '本地记录')}</span>
        </span>
        <span class="cognitive-record-summary">
          <strong>${escapeHtml(summary)}</strong>
          <span>${escapeHtml(purposes.slice(0, 2).join(' · ') || status)}</span>
        </span>
        <span class="cognitive-record-facets">
          <span>${escapeHtml(facets || '尚未形成内容标签')}</span>
        </span>
        <span class="cognitive-record-destination">
          <strong>${escapeHtml(destination.title)}</strong>
          <span>${escapeHtml(destination.detail)}</span>
        </span>
        <span class="cognitive-record-state is-${record.status}">${escapeHtml(status)}</span>
      </span>
    </button>`;
  }).join('');
  settleCognitiveTimelineAfterRender(list, {
    initialDemoRender,
    recordCountChanged,
    previousScrollLeft,
    previousScrollWidth,
    wasAtNow,
  });
  cognitiveTimelineState.initialized = true;
  cognitiveTimelineState.recordCount = home.records.length;
}

function applyCognitiveView() {
  const map = document.getElementById('cognitive-map-region');
  const list = document.getElementById('cognitive-list-region');
  const compact = Boolean(cognitiveCompactLandscapeMedia?.matches);
  const mapVisible = cognitiveHomeState.activeView === 'map' && !compact;
  map.hidden = !mapVisible;
  list.hidden = mapVisible;
  document.querySelectorAll('[data-cognitive-view]').forEach(button => {
    const active = button.dataset.cognitiveView === cognitiveHomeState.activeView;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}

function cognitivePeakById(identifier) {
  return cognitiveHomeState.landscape?.peaks.find(peak => peak.understanding_ref.id === identifier) || null;
}

function cognitiveNodeById(identifier) {
  return cognitiveHomeState.landscape?.nodes.find(node => node.memory_ref.id === identifier) || null;
}

function cognitiveEdgeById(identifier) {
  return cognitiveHomeState.landscape?.edges.find(edge => edge.relation_ref.id === identifier) || null;
}

function cognitiveRecordById(identifier) {
  return cognitiveHomeState.home?.records.find(record => record.record_ref.id === identifier)
    || cognitiveDemoState.fixture?.records?.find(record => (
      record.record_ref?.id === identifier || record.id === identifier || record.recordId === identifier
    ))
    || null;
}

function cognitiveRelatedRecords(kind, identifier) {
  const records = cognitiveHomeState.home?.records || [];
  if (kind === 'peak') return records.filter(record => record.understanding_refs.some(ref => ref.id === identifier));
  if (kind === 'node') return records.filter(record => record.memory_refs.some(ref => ref.id === identifier));
  return [];
}

function cognitiveRecordSummaryList(records) {
  if (!records.length) return '<p class="cognitive-drawer-muted">今天的主页投影中没有直接关联记录。</p>';
  return `<ul class="cognitive-reference-list">${records.map(record => `
    <li>${escapeHtml(record.summary || COGNITIVE_RECORD_STATUS_LABELS[record.status])}
      <span class="cognitive-drawer-muted"> · ${escapeHtml(cognitiveTimeLabel(record.captured_at))}</span></li>`).join('')}</ul>`;
}

function cognitiveRefVersionLabel(ref) {
  if (!ref) return '尚未形成版本';
  return `v${ref.revision} · ${ref.revision_sha256.slice(0, 8)}`;
}

function cognitiveVerifiedRevision(map, ref) {
  const item = ref ? map.get(ref.id) : null;
  return item && cognitiveSameObjectRef(item.ref, ref) ? item.value : null;
}

function cognitiveListInput(values) {
  return escapeHtml((values || []).join('、'));
}

function cognitiveChoiceOptions(labels, selected) {
  return Object.entries(labels).map(([value, label]) => `
    <option value="${escapeHtml(value)}"${value === selected ? ' selected' : ''}>${escapeHtml(label)}</option>`).join('');
}

function cognitiveCheckboxes(name, labels, selectedValues) {
  const selected = new Set(selectedValues || []);
  return Object.entries(labels).map(([value, label]) => `
    <label class="cognitive-check-option">
      <input type="checkbox" name="${escapeHtml(name)}" value="${escapeHtml(value)}"${selected.has(value) ? ' checked' : ''}>
      <span>${escapeHtml(label)}</span>
    </label>`).join('');
}

function cognitiveActionStatusRegion() {
  const message = cognitiveHomeState.actionNotice;
  const tone = cognitiveHomeState.actionNoticeTone;
  return `<div class="cognitive-action-status${tone ? ` ${escapeHtml(tone)}` : ''}"
    data-cognitive-action-status role="status" aria-live="polite"${message ? '' : ' hidden'}>${escapeHtml(message)}</div>`;
}

function cognitiveReceiptActions(record, receipt) {
  if (cognitiveDemoState.active) return '';
  if (!record.receipt_ref || !receipt || !['ready', 'needs_review'].includes(receipt.status)) return '';
  const facets = receipt.facets;
  return `
    <section class="cognitive-drawer-section cognitive-calibration-section" aria-labelledby="cognitive-receipt-calibration-title">
      <h3 id="cognitive-receipt-calibration-title">这条理解对吗？</h3>
      <div class="cognitive-action-row" aria-label="校正这条整理回执">
        <button type="button" class="is-primary" data-cognitive-action="confirm_receipt">正确</button>
        <button type="button" data-cognitive-form-toggle="receipt-edit" aria-expanded="false" aria-controls="cognitive-receipt-edit-form">改一下</button>
        <button type="button" class="is-negative" data-cognitive-terminal-action="original_only" data-confirm-label="再点一次：仅保留原文">仅保留原文</button>
      </div>
      <p class="cognitive-action-help">“仅保留原文”会让这条停止进入后续自动整理，MVP 中无法恢复。</p>
      <form id="cognitive-receipt-edit-form" class="cognitive-edit-form" data-cognitive-edit-form="edit_receipt" hidden>
        <label><span>整理结果</span><textarea name="summary" maxlength="600" required>${escapeHtml(receipt.summary)}</textarea></label>
        <fieldset><legend>内容类型</legend><div class="cognitive-check-grid">${cognitiveCheckboxes('content_types', COGNITIVE_CONTENT_LABELS, facets.content_types)}</div></fieldset>
        <label><span>主题，用顿号分隔</span><input name="topics" value="${cognitiveListInput(facets.topics)}"></label>
        <label><span>对象，用顿号分隔</span><input name="objects" value="${cognitiveListInput(facets.objects)}"></label>
        <div class="cognitive-form-pair">
          <label><span>我与它的关系</span><select name="stance">${cognitiveChoiceOptions(COGNITIVE_STANCE_LABELS, facets.stance)}</select></label>
          <label><span>认知状态</span><select name="cognitive_state">${cognitiveChoiceOptions(COGNITIVE_STATE_LABELS, facets.cognitive_state)}</select></label>
        </div>
        <fieldset><legend>后续用途</legend><div class="cognitive-check-grid">${cognitiveCheckboxes('purposes', COGNITIVE_PURPOSE_LABELS, facets.purposes)}</div></fieldset>
        <div class="cognitive-form-actions">
          <button type="submit" class="is-primary">保存修改</button>
          <button type="button" data-cognitive-form-cancel="receipt-edit">取消</button>
        </div>
      </form>
    </section>`;
}

function cognitiveMemoryActions(node, memory) {
  if (cognitiveDemoState.active) return '';
  if (!memory || memory.status !== 'active') return '';
  return `
    <section class="cognitive-drawer-section cognitive-calibration-section" aria-labelledby="cognitive-memory-calibration-title">
      <h3 id="cognitive-memory-calibration-title">调整这个记忆点</h3>
      <div class="cognitive-action-row">
        <button type="button" data-cognitive-form-toggle="memory-edit" aria-expanded="false" aria-controls="cognitive-memory-edit-form">编辑</button>
        <button type="button" class="is-negative" data-cognitive-terminal-action="delete_reusable_memory" data-confirm-label="再点一次：删除记忆点">删除</button>
      </div>
      <form id="cognitive-memory-edit-form" class="cognitive-edit-form" data-cognitive-edit-form="edit_reusable_memory" hidden>
        <label><span>记忆表述</span><textarea name="statement" maxlength="1000" required>${escapeHtml(memory.statement)}</textarea></label>
        <label><span>主题，用顿号分隔</span><input name="topics" value="${cognitiveListInput(memory.topics)}"></label>
        <fieldset><legend>后续用途</legend><div class="cognitive-check-grid">${cognitiveCheckboxes('purposes', COGNITIVE_PURPOSE_LABELS, memory.purposes)}</div></fieldset>
        <p class="cognitive-action-help">内容类型、原文依据和其他未编辑字段会沿用当前版本。</p>
        <div class="cognitive-form-actions">
          <button type="submit" class="is-primary">保存修改</button>
          <button type="button" data-cognitive-form-cancel="memory-edit">取消</button>
        </div>
      </form>
    </section>`;
}

function cognitiveRelationActions(edge, relation) {
  if (cognitiveDemoState.active) return '';
  if (!relation || relation.status !== 'active') return '';
  return `
    <section class="cognitive-drawer-section cognitive-calibration-section" aria-labelledby="cognitive-relation-calibration-title">
      <h3 id="cognitive-relation-calibration-title">调整这条关系</h3>
      <div class="cognitive-action-row">
        <button type="button" data-cognitive-form-toggle="relation-edit" aria-expanded="false" aria-controls="cognitive-relation-edit-form">编辑</button>
        <button type="button" class="is-negative" data-cognitive-terminal-action="delete_relation" data-confirm-label="再点一次：删除关系">删除</button>
      </div>
      <form id="cognitive-relation-edit-form" class="cognitive-edit-form" data-cognitive-edit-form="edit_relation" hidden>
        <label><span>关系类型</span><select name="type">${cognitiveChoiceOptions(COGNITIVE_RELATION_LABELS, relation.type)}</select></label>
        <label><span>关系说明</span><textarea name="statement" maxlength="1000" required>${escapeHtml(relation.statement)}</textarea></label>
        <p class="cognitive-action-help">改为“同一主题”时关系会转为无向；其他类型保持有向。</p>
        <div class="cognitive-form-actions">
          <button type="submit" class="is-primary">保存修改</button>
          <button type="button" data-cognitive-form-cancel="relation-edit">取消</button>
        </div>
      </form>
    </section>`;
}

async function readCognitiveAuthorizedRawRecord(record) {
  const locator = cognitiveHomeState.recordLocators.get(record.record_ref.id);
  if (!state.dirHandle
      || !locator
      || !cognitiveSameObjectRef(locator.recordRef, record.record_ref)) {
    throw new Error('当前投影没有通过原文定位校验');
  }
  const bytes = await readCognitiveSourceFileBytes(state.dirHandle, locator.sourceFile);
  const library = cognitiveHomeLibrary();
  if (library.sha256Hex(bytes) !== locator.sourceSnapshotSha256
      || locator.byteEnd > bytes.byteLength) {
    throw new Error('本地原文已变化，请等待记录索引同步');
  }
  const block = bytes.slice(locator.byteStart, locator.byteEnd);
  if (library.sha256Hex(block) !== locator.entrySha256) {
    throw new Error('本地原文片段未通过哈希校验');
  }
  return new TextDecoder('utf-8', { fatal: true }).decode(block);
}

async function loadCognitiveDrawerOriginal(record) {
  const container = document.querySelector(`[data-cognitive-original="${record.record_ref.id}"]`);
  if (!container) return;
  try {
    const raw = cognitiveDemoState.active
      ? cognitiveDemoRawText(record.record_ref.id)
      : await readCognitiveAuthorizedRawRecord(record);
    if (typeof raw !== 'string') throw new Error('原文不存在');
    if (cognitiveHomeState.selected?.kind !== 'record'
        || cognitiveHomeState.selected.identifier !== record.record_ref.id
        || !container.isConnected) return;
    const pre = document.createElement('pre');
    pre.className = 'cognitive-drawer-original';
    pre.textContent = raw;
    container.replaceChildren(pre);
  } catch (error) {
    if (!container.isConnected) return;
    const message = document.createElement('p');
    message.className = 'cognitive-drawer-muted';
    message.textContent = `原文当前无法安全展示：${shortError(error)}`;
    container.replaceChildren(message);
  }
}

function cognitiveEndpointLabel(edge, id) {
  return `${cognitiveEntityTitle(id)}${id === edge.from_id ? '（起点）' : '（终点）'}`;
}

function cognitiveDemoThemeByUnderstandingId(identifier) {
  return cognitiveDemoState.fixture?.themes?.find(theme => (
    theme.understandingId === identifier || theme.id === identifier
  )) || null;
}

function cognitiveDemoRecordMeta(recordId) {
  const fixture = cognitiveDemoState.fixture;
  if (!fixture) return null;
  const direct = fixture.records?.find(record => record.id === recordId || record.recordId === recordId);
  if (direct) return direct;
  for (const day of fixture.history || []) {
    const item = (day.records || []).find(record => (
      typeof record === 'string' ? record === recordId : record.id === recordId || record.recordId === recordId
    ));
    if (item) return typeof item === 'string'
      ? { id: item, date: day.date, text: cognitiveDemoRawText(item) }
      : { date: day.date, ...item };
  }
  return null;
}

function cognitiveDemoEvidenceRows(records) {
  return records.map(record => {
    const id = record.id || record.recordId;
    const raw = cognitiveDemoRawText(id) || record.text || record.summary || '';
    return `<li><button type="button" class="cognitive-demo-evidence-row" data-demo-open-record="${escapeHtml(id)}">
      <time>${escapeHtml(record.date || '')}</time><span>${escapeHtml(raw)}</span></button></li>`;
  }).join('');
}

function cognitiveDemoEvidenceMarkup(recordIds, emptyCopy, previewLimit = 5) {
  const records = (recordIds || []).map(cognitiveDemoRecordMeta).filter(Boolean);
  if (!records.length) return `<p class="cognitive-drawer-muted">${escapeHtml(emptyCopy)}</p>`;
  if (!Number.isFinite(previewLimit) || records.length <= previewLimit) {
    return `<ol class="cognitive-chain-list cognitive-demo-evidence-list">${cognitiveDemoEvidenceRows(records)}</ol>`;
  }
  const previewIndexes = new Set(Array.from({ length: previewLimit }, (_, index) => (
    Math.round(index * (records.length - 1) / Math.max(1, previewLimit - 1))
  )));
  const preview = records.filter((_, index) => previewIndexes.has(index));
  const remaining = records.filter((_, index) => !previewIndexes.has(index));
  return `<ol class="cognitive-chain-list cognitive-demo-evidence-list">${cognitiveDemoEvidenceRows(preview)}</ol>
    <details class="cognitive-demo-evidence-more">
      <summary>查看其余 ${remaining.length} 条依据</summary>
      <ol class="cognitive-chain-list cognitive-demo-evidence-list">${cognitiveDemoEvidenceRows(remaining)}</ol>
    </details>`;
}

function cognitiveDemoPeakDrawer(peak) {
  const theme = cognitiveDemoThemeByUnderstandingId(peak.understanding_ref.id);
  if (!theme) return null;
  const dayCount = cognitiveDemoState.fixture?.window?.days
    || cognitiveDemoState.fixture?.history?.length || 0;
  const portraitItems = (cognitiveDemoState.fixture?.portrait || [])
    .filter(item => (item.themeIds || []).includes(theme.id));
  const evidenceRecords = (theme.evidenceRecordIds || []).map(cognitiveDemoRecordMeta).filter(Boolean);
  const evidenceDates = new Set(evidenceRecords.map(record => record.date).filter(Boolean));
  return {
    eyebrow: '第二层 · 形成依据',
    title: theme.title,
    body: `
      <section class="cognitive-drawer-section cognitive-demo-theme-overview">
        <h3>当前理解</h3>
        <p class="cognitive-drawer-lead">${escapeHtml(theme.tendency || theme.statement || '')}</p>
        ${theme.boundary ? `<p class="cognitive-demo-theme-boundary"><strong>适用边界</strong>${escapeHtml(theme.boundary)}</p>` : ''}
        <div class="cognitive-demo-theme-metrics" aria-label="主题形成概览">
          <span><strong>${evidenceRecords.length}</strong>条支持</span>
          <span><strong>${evidenceDates.size}</strong>个记录日</span>
          <span><strong>${(theme.counterRecordIds || []).length}</strong>条边界记录</span>
        </div>
        <div class="cognitive-demo-layer-next">
          <div>
            <span>第三层</span>
            <strong>${portraitItems.length ? `关联 ${portraitItems.length} 条长期理解` : '尚未收束为长期理解'}</strong>
          </div>
          <button type="button" data-demo-open-library>查看「她理解的我」</button>
        </div>
      </section>
      <section class="cognitive-drawer-section">
        <h3>形成依据 · ${dayCount} 天</h3>
        <p class="cognitive-drawer-muted">先展示时间跨度上的 5 条代表记录，其余依据可按需展开。</p>
        ${cognitiveDemoEvidenceMarkup(theme.evidenceRecordIds, '这个主题尚无支持记录。', 5)}
      </section>
      <section class="cognitive-drawer-section">
        <h3>边界记录</h3>
        ${cognitiveDemoEvidenceMarkup(theme.counterRecordIds, '当前记录中还没有需要单独保留的边界情况。', Number.POSITIVE_INFINITY)}
      </section>`,
    foot: '来源、边界和形成过程都可回到记录。',
  };
}

function cognitiveDemoPortraitMarkup(selectedIdentifier = '') {
  const fixture = cognitiveDemoState.fixture;
  const themes = new Map((fixture?.themes || []).map(theme => [theme.id, theme]));
  return `<section class="cognitive-demo-portrait">
    <header class="cognitive-demo-portrait-intro">
      <p>由多个可追溯主题收束而来；每句话都保留来源和适用边界。</p>
    </header>
    <div class="cognitive-demo-portrait-list">${(fixture?.portrait || []).map((item, index) => `
      <article class="cognitive-demo-portrait-card" tabindex="-1"
        data-cognitive-portrait-drawer-id="${escapeHtml(item.id)}"${item.id === selectedIdentifier ? ' aria-current="true"' : ''}>
        <span>${String(index + 1).padStart(2, '0')}</span>
        <div>
          <div class="cognitive-demo-portrait-card-heading">
            <h3>${escapeHtml(item.title)}</h3>
            <span class="cognitive-portrait-maturity" data-portrait-maturity="${escapeHtml(cognitivePortraitMaturity(item))}">${escapeHtml(cognitivePortraitMaturityLabel(item))}</span>
          </div>
          <p>${escapeHtml(item.statement)}</p>
          ${item.boundary ? `<small>${escapeHtml(item.boundary)}</small>` : ''}
          <div class="cognitive-demo-portrait-links">${(item.themeIds || []).map(themeId => {
            const theme = themes.get(themeId);
            return theme ? `<button type="button" data-demo-open-peak="${escapeHtml(theme.understandingId || theme.id)}">${escapeHtml(theme.title)}</button>` : '';
          }).join('')}</div>
        </div>
      </article>`).join('')}</div>
  </section>`;
}

function cognitiveDemoHistoryMarkup() {
  const allRecords = new Map((cognitiveDemoState.fixture?.records || []).map(record => [record.id, record]));
  const history = cognitiveDemoState.fixture?.history || [];
  const dayCount = cognitiveDemoState.fixture?.window?.days || history.length;
  const totalRecords = cognitiveDemoState.fixture?.stats?.totalRecords
    || history.reduce((total, day) => total + (day.records || []).length, 0);
  return `<section class="cognitive-demo-history">
    <header class="cognitive-demo-portrait-intro">
      <p>${totalRecords} 条记录，按日期回看主题去向。</p>
    </header>
    <div class="cognitive-demo-history-grid">${history.map(day => {
      const records = day.records || [];
      const topics = day.topics || day.themeTitles || [...new Set(records.flatMap(record => {
        const id = typeof record === 'string' ? record : record?.id || record?.recordId;
        return allRecords.get(id)?.topics || [];
      }))].slice(0, 3);
      const summary = records.length
        ? topics.join(' · ') || day.note || '仅保留原始记录'
        : '这一天没有记录';
      return `<article class="cognitive-demo-history-day">
        <time>${escapeHtml(day.date || '')} · ${escapeHtml(day.weekday || '')}</time>
        <strong>${records.length} 条</strong>
        <p>${escapeHtml(summary)}</p>
        ${records.length ? `<button type="button" data-demo-open-day="${escapeHtml(day.date || '')}">查看当日 ${records.length} 条</button>` : ''}
      </article>`;
    }).join('')}</div>
  </section>`;
}

function cognitiveDemoDayMarkup(date) {
  const fixture = cognitiveDemoState.fixture;
  const day = (fixture?.history || []).find(item => item.date === date);
  if (!day) return '';
  const records = (day.records || []).map(cognitiveDemoRecordMeta).filter(Boolean);
  return `<section class="cognitive-demo-day">
    <header class="cognitive-demo-portrait-intro">
      <strong>${escapeHtml(day.date)} · ${escapeHtml(day.weekday || '')}</strong>
      <p>${records.length} 条原始记录；点开任意一条可查看它的整理去向与原文。</p>
    </header>
    <div class="cognitive-demo-day-list">${records.map(record => {
      const id = record.id || record.recordId;
      const raw = cognitiveDemoRawText(id) || record.text || record.summary || '';
      const status = COGNITIVE_RECORD_STATUS_LABELS[record.status] || record.status || '已保存';
      const topics = (record.topics || []).slice(0, 2).join(' · ');
      return `<button type="button" class="cognitive-demo-day-record" data-demo-open-record="${escapeHtml(id)}"
        aria-label="${escapeHtml(record.time || '')} ${escapeHtml(status)}：${escapeHtml(raw)}">
        <span><time>${escapeHtml(record.time || '')}</time><small>${escapeHtml(record.source_app || '本地记录')}</small></span>
        <span><strong>${escapeHtml(raw)}</strong><small>${escapeHtml(topics || status)}</small></span>
      </button>`;
    }).join('')}</div>
  </section>`;
}

function cognitivePeakDrawer(peak) {
  if (cognitiveDemoState.active) return cognitiveDemoPeakDrawer(peak);
  const index = cognitivePeakIndex(peak);
  const memory = cognitiveVerifiedUnderstanding(peak.understanding_ref);
  const edges = cognitiveHomeState.landscape.edges.filter(edge => edge.from_id === peak.understanding_ref.id
    || edge.to_id === peak.understanding_ref.id);
  const connectedNodes = new Set(edges.flatMap(edge => [edge.from_id, edge.to_id])
    .filter(id => cognitiveNodeById(id)));
  const crossPeakEdges = edges.filter(edge => {
    const other = edge.from_id === peak.understanding_ref.id ? edge.to_id : edge.from_id;
    return Boolean(cognitivePeakById(other));
  });
  const relatedRecords = cognitiveRelatedRecords('peak', peak.understanding_ref.id);
  const relationMarkup = edges.length
    ? `<ul class="cognitive-reference-list">${edges.map(edge => {
      const other = edge.from_id === peak.understanding_ref.id ? edge.to_id : edge.from_id;
      return `<li>${escapeHtml(COGNITIVE_RELATION_LABELS[edge.type])} · ${escapeHtml(cognitiveEntityTitle(other))}</li>`;
    }).join('')}</ul>`
    : '<p class="cognitive-drawer-muted">目前没有进入投影的正式关系。</p>';
  return {
    eyebrow: peak.recent_change ? '长期理解 · 近期有变化' : '长期理解 · 当前版本',
    title: cognitivePeakTitle(peak, index),
    body: `
      <section class="cognitive-drawer-section">
        <h3>完整理解</h3>
        <p class="cognitive-drawer-lead">${memory
          ? escapeHtml(memory.statement)
          : '理解正文正在等待同版本的本地理解索引；主页没有使用未校验文字补全。'}</p>
        ${memory ? `<p class="cognitive-drawer-muted">地景主题：${escapeHtml(memory.scope)}</p>` : ''}
      </section>
      <section class="cognitive-drawer-section">
        <h3>怎样形成</h3>
        <ol class="cognitive-chain-list">
          <li>SourceRecord：${peak.evidence_count} 条依据与 ${peak.counterevidence_count} 条反例已绑定当前记录版本</li>
          <li>逐条拆解：先形成内容类型、主题、用途和需核对项</li>
          <li>可用记忆：${connectedNodes.size} 个已归并记忆进入当前地景</li>
          <li>正式关系：${edges.length} 条关系，其中 ${crossPeakEdges.length} 条连向其他认知峰</li>
          <li>当前理解：${cognitiveRefVersionLabel(peak.understanding_ref)}，${peak.recent_change ? '本次包含近期变化' : '当前没有新的实质变化'}</li>
        </ol>
      </section>
      <section class="cognitive-drawer-section">
        <h3>正式关系</h3>${relationMarkup}
      </section>
      <section class="cognitive-drawer-section">
        <h3>今天的关联回执</h3>${cognitiveRecordSummaryList(relatedRecords)}
      </section>
      <section class="cognitive-drawer-section">
        <div class="cognitive-drawer-meta">
          <div><span>生命周期</span><strong>${escapeHtml(peak.lifecycle === 'tension' ? '存在张力' : peak.lifecycle === 'dormant' ? '暂时休眠' : '当前有效')}</strong></div>
          <div><span>版本</span><strong>${escapeHtml(cognitiveRefVersionLabel(peak.understanding_ref))}</strong></div>
          <div><span>地景高度</span><strong>由证据积累确定</strong></div>
        </div>
      </section>`,
    foot: '地景只显示短主题；完整表述、形成依据与版本边界保留在这里。更深的收束可从“她理解的我”进入。',
  };
}

function cognitiveNodeDrawer(node) {
  const index = cognitiveHomeState.landscape.nodes.indexOf(node);
  const memory = cognitiveVerifiedRevision(cognitiveHomeState.verifiedMemories, node.memory_ref);
  const edges = cognitiveHomeState.landscape.edges.filter(edge => edge.from_id === node.memory_ref.id
    || edge.to_id === node.memory_ref.id);
  const destinations = edges.map(edge => edge.from_id === node.memory_ref.id ? edge.to_id : edge.from_id);
  const understandingDestinations = destinations.filter(id => cognitivePeakById(id));
  const records = cognitiveRelatedRecords('node', node.memory_ref.id);
  return {
    eyebrow: node.recent ? '可用记忆 · 近期归并' : '可用记忆 · 已提交',
    title: memory?.statement || `可用记忆 ${String(index + 1).padStart(2, '0')}`,
    body: `
      ${memory ? `<section class="cognitive-drawer-section"><h3>当前记忆</h3><p class="cognitive-drawer-lead">${escapeHtml(memory.statement)}</p><p class="cognitive-drawer-muted">${escapeHtml(memory.topics.join(' · ') || '尚未标记主题')}</p></section>` : ''}
      <section class="cognitive-drawer-section">
        <h3>在形成链路中的位置</h3>
        <ol class="cognitive-chain-list">
          <li>SourceRecord 先被保存，逐条整理回执再形成拆解结果</li>
          <li>每日归并将通过校验的内容提交为可用记忆</li>
          <li>${escapeHtml(destinations.length
            ? `通过 ${edges.length} 条正式关系连接 ${destinations.map(cognitiveEntityTitle).join('、')}`
            : '目前没有进入地景的正式关系')}</li>
          <li>${understandingDestinations.length
            ? `进入 ${understandingDestinations.length} 项当前理解或跨峰分支`
            : '尚未进入当前长期理解'}</li>
          <li>可用记忆版本：${cognitiveRefVersionLabel(node.memory_ref)}</li>
        </ol>
      </section>
      <section class="cognitive-drawer-section">
        <h3>今天的来源回执</h3>${cognitiveRecordSummaryList(records)}
      </section>
      <section class="cognitive-drawer-section">
        <p class="cognitive-drawer-boundary">主页投影只携带对象引用与整理摘要，不携带原文或附件。</p>
      </section>
      ${cognitiveMemoryActions(node, memory)}`,
    foot: '实心点只表示已提交的可用记忆；未归并候选不会在默认地景中出现。',
  };
}

function cognitiveEdgeDrawer(edge) {
  const label = COGNITIVE_RELATION_LABELS[edge.type] || '正式关系';
  const relation = cognitiveVerifiedRevision(cognitiveHomeState.verifiedRelations, edge.relation_ref);
  const fromPeak = cognitivePeakById(edge.from_id);
  const toPeak = cognitivePeakById(edge.to_id);
  const branch = fromPeak && toPeak ? '跨峰分支'
    : fromPeak || toPeak ? '可用记忆到当前理解'
      : '可用记忆之间';
  return {
    eyebrow: '正式关系 · 已提交',
    title: label,
    body: `
      <section class="cognitive-drawer-section">
        <h3>关系两端</h3>
        <ol class="cognitive-chain-list">
          <li>${escapeHtml(cognitiveEndpointLabel(edge, edge.from_id))}</li>
          <li>${escapeHtml(cognitiveEndpointLabel(edge, edge.to_id))}</li>
        </ol>
      </section>
      <section class="cognitive-drawer-section">
        <h3>形成链路</h3>
        <ol class="cognitive-chain-list">
          <li>SourceRecord 来源片段已绑定当前记录版本</li>
          <li>逐条拆解和每日归并形成两端对象</li>
          <li>当前关系：${escapeHtml(label)} · ${escapeHtml(branch)}</li>
          <li>关系版本：${cognitiveRefVersionLabel(edge.relation_ref)}</li>
        </ol>
      </section>
      <section class="cognitive-drawer-section">
        <h3>如何阅读</h3>
        <p class="cognitive-drawer-lead">${escapeHtml(relation?.statement || `这条线表示已经提交的“${label}”关系。`)}</p>
        <p class="cognitive-drawer-muted">线的长度和弯曲程度不产生额外人物判断。</p>
      </section>
      ${cognitiveRelationActions(edge, relation)}`,
    foot: '只有正式关系进入地景；候选关系不在主页中显示。',
  };
}

function cognitiveRecordDrawer(record) {
  const demoRecord = cognitiveDemoState.active;
  const status = COGNITIVE_RECORD_STATUS_LABELS[record.status] || record.status;
  const destination = cognitiveRecordDestination(record);
  const content = record.content_types.map(value => COGNITIVE_CONTENT_LABELS[value] || value);
  const purposes = record.purposes.map(value => COGNITIVE_PURPOSE_LABELS[value] || value);
  const understandingItems = record.understanding_refs.map(ref => {
    const peak = cognitivePeakForUnderstanding(ref);
    return peak ? `<li>${escapeHtml(cognitivePeakTitle(peak, cognitivePeakIndex(peak)))}</li>` : '';
  }).filter(Boolean).join('');
  const receiptStep = demoRecord
    ? (record.receipt_ref ? '这条记录已经完成逐条整理' : status)
    : (record.receipt_ref
      ? `逐条整理回执已绑定当前原始记录版本（${cognitiveRefVersionLabel(record.receipt_ref)}）`
      : status);
  const receipt = cognitiveVerifiedRevision(cognitiveHomeState.verifiedReceipts, record.receipt_ref);
  const understandingLabel = cognitiveDemoState.active ? '聚合主题' : '长期理解';
  const downstreamSteps = record.status === 'original_only'
    ? `<li>你选择“仅保留原文”，本条没有下游可用记忆或${understandingLabel}</li>`
    : record.status === 'no_candidate'
      ? `<li>本条已完成检查，没有形成可归并回执、可用记忆或${understandingLabel}</li>`
    : `<li>${record.memory_refs.length
      ? (demoRecord ? `形成 ${record.memory_refs.length} 条可用线索` : `拆解并归并为 ${record.memory_refs.length} 个可用记忆`)
      : (demoRecord ? '目前只保留整理结果，尚未形成主题线索' : '尚未形成已提交的可用记忆')}</li>
      <li>${escapeHtml(destination.title)} · ${escapeHtml(destination.detail)}</li>`;
  return {
    eyebrow: demoRecord
      ? `本条记录 · ${cognitiveTimeLabel(record.captured_at)}`
      : `${record.receipt_ref ? '逐条整理回执' : '逐条整理状态'} · ${cognitiveTimeLabel(record.captured_at)}`,
    title: record.summary || status,
    body: `
      <section class="cognitive-drawer-section">
        <h3>${demoRecord ? '这条记录表达了什么' : '本条整理结果'}</h3>
        <p class="cognitive-drawer-lead">${escapeHtml(record.summary || '当前没有形成可展示的整理摘要。')}</p>
        <p class="cognitive-drawer-muted">${escapeHtml([...content, ...record.topics].slice(0, 5).join(' · ') || '尚未形成内容标签')}</p>
      </section>
      <section class="cognitive-drawer-section">
        <h3>${demoRecord ? '这条记录如何进入主题' : '形成链路'}</h3>
        <ol class="cognitive-chain-list">
          <li>${demoRecord ? '原始记录已保存在本地' : `SourceRecord 已保存（${cognitiveRefVersionLabel(record.record_ref)}）`}</li>
          <li>${escapeHtml(receiptStep)}</li>
          ${downstreamSteps}
        </ol>
      </section>
      <section class="cognitive-drawer-section">
        <h3>后续用途</h3>
        <p class="cognitive-drawer-muted">${escapeHtml(purposes.join(' · ') || '尚未标记后续用途')}</p>
      </section>
      ${understandingItems ? `<section class="cognitive-drawer-section"><h3>关联的${understandingLabel}</h3><ul class="cognitive-reference-list">${understandingItems}</ul></section>` : ''}
      <section class="cognitive-drawer-section">
        <h3>本地原文</h3>
        <div class="cognitive-drawer-original-region" data-cognitive-original="${record.record_ref.id}" role="status">
          <p class="cognitive-drawer-muted">${demoRecord ? '正在读取本地原文…' : '正在根据当前 SourceRecord 版本校验本地原文…'}</p>
        </div>
      </section>
      <section class="cognitive-drawer-section">
        <div class="cognitive-drawer-meta">
          <div><span>当前状态</span><strong>${escapeHtml(status)}</strong></div>
          <div><span>来源</span><strong>${escapeHtml(record.source_app || COGNITIVE_SOURCE_LABELS[record.source_type] || '本地记录')}</strong></div>
          <div><span>保存时间</span><strong>${escapeHtml(cognitiveDateTimeLabel(record.captured_at))}</strong></div>
          <div><span>原文</span><strong>仅在本抽屉中读取</strong></div>
        </div>
      </section>
      ${cognitiveReceiptActions(record, receipt)}`,
    foot: demoRecord
      ? '原文保存在本地；关联主题可继续追溯这条记录如何进入当前理解。'
      : '原文不进入主页投影；抽屉只会显示通过当前文件与记录块哈希校验的本地内容。',
  };
}

function cognitiveDrawerContent(kind, identifier) {
  if (kind === 'library') {
    if (cognitiveDemoState.active) {
      return {
        eyebrow: '第三层 · 长期理解',
        title: '她理解的我',
        body: cognitiveDemoPortraitMarkup(identifier),
        foot: '这些理解会随新记录修订，不作为固定人格结论。',
      };
    }
    return {
      eyebrow: '认知秘书 · 当前版本',
      title: '她理解的我',
      body: contextAgentState.loaded
        ? contextInsightMarkup()
        : '<div class="context-empty">正在核对本地长期理解…</div>',
      foot: '这里只显示通过当前 profile 与来源绑定校验的长期理解；打开和阅读不会调用模型。',
    };
  }
  if (kind === 'demo_history' && cognitiveDemoState.active) {
    const dayCount = cognitiveDemoState.fixture?.window?.days
      || cognitiveDemoState.fixture?.history?.length || 0;
    return {
      eyebrow: '记录轨迹',
      title: `最近 ${dayCount} 天`,
      body: cognitiveDemoHistoryMarkup(),
      foot: '点击日期可查看全部记录及其主题去向。',
    };
  }
  if (kind === 'demo_day' && cognitiveDemoState.active) {
    const day = cognitiveDemoState.fixture?.history?.find(item => item.date === identifier);
    if (!day) return null;
    return {
      eyebrow: '记录轨迹 · 当日全部',
      title: `${day.date} · ${day.weekday}`,
      body: cognitiveDemoDayMarkup(day.date),
      foot: '点击记录可回看原文与主题去向。',
    };
  }
  if (kind === 'peak') {
    const peak = cognitivePeakById(identifier);
    return peak ? cognitivePeakDrawer(peak) : null;
  }
  if (kind === 'node') {
    const node = cognitiveNodeById(identifier);
    return node ? cognitiveNodeDrawer(node) : null;
  }
  if (kind === 'edge') {
    const edge = cognitiveEdgeById(identifier);
    return edge ? cognitiveEdgeDrawer(edge) : null;
  }
  if (kind === 'record') {
    const record = cognitiveRecordById(identifier);
    return record ? cognitiveRecordDrawer(record) : null;
  }
  return null;
}

function cueCognitiveDrawerPortraitTarget(target) {
  if (!(target instanceof HTMLElement)) return;
  target.classList.remove('is-cognitive-entry-cued');
  void target.offsetWidth;
  target.classList.add('is-cognitive-entry-cued');
  let cleared = false;
  const clearCue = () => {
    if (cleared) return;
    cleared = true;
    target.classList.remove('is-cognitive-entry-cued');
  };
  target.addEventListener('animationend', clearCue, { once: true });
  window.setTimeout(clearCue, 1500);
}

function openCognitiveChainDrawer(kind, identifier, trigger) {
  const content = cognitiveDrawerContent(kind, identifier);
  if (!content) return;
  const drawer = document.getElementById('cognitive-chain-drawer');
  const drawerWasOpen = drawer.getAttribute('aria-hidden') === 'false';
  clearCognitiveMapHover();
  cognitiveSetMapTilt(0, 0, 50, 50);
  closeCognitiveOutputPopover();
  closeSideDrawers(false);
  cognitiveHomeState.selected = { kind, identifier };
  if (!drawerWasOpen || !(cognitiveHomeState.drawerTrigger instanceof Element)) {
    cognitiveHomeState.drawerTrigger = trigger || document.activeElement;
  }
  document.getElementById('cognitive-drawer-eyebrow').textContent = content.eyebrow;
  document.getElementById('cognitive-drawer-title').textContent = content.title;
  const drawerBody = document.getElementById('cognitive-drawer-body');
  drawerBody.innerHTML = `${content.body}${kind === 'library' ? '' : cognitiveActionStatusRegion()}`;
  drawerBody.scrollTop = 0;
  const portraitTarget = kind === 'library' && identifier !== 'current'
    ? [...drawerBody.querySelectorAll('[data-cognitive-portrait-drawer-id]')]
      .find(item => item.dataset.cognitivePortraitDrawerId === identifier) || null
    : null;
  document.getElementById('cognitive-drawer-foot').textContent = content.foot;
  const scrim = document.getElementById('cognitive-drawer-scrim');
  drawer.setAttribute('aria-hidden', 'false');
  scrim.hidden = false;
  document.getElementById('app').inert = true;
  document.body.classList.add('cognitive-chain-open');
  requestAnimationFrame(() => {
    drawer.classList.add('is-open');
    scrim.classList.add('is-open');
    if (portraitTarget) {
      portraitTarget.focus({ preventScroll: true });
      portraitTarget.scrollIntoView({ block: 'center' });
      cueCognitiveDrawerPortraitTarget(portraitTarget);
    } else {
      document.getElementById('cognitive-drawer-close').focus();
    }
  });
  if (kind === 'library' && contextAgentState.loaded && !cognitiveDemoState.active) bindContextAgentView();
  if (kind === 'record') {
    const record = cognitiveRecordById(identifier);
    if (record) void loadCognitiveDrawerOriginal(record);
  }
}

function closeCognitiveChainDrawer(restoreFocus = true) {
  const drawer = document.getElementById('cognitive-chain-drawer');
  const scrim = document.getElementById('cognitive-drawer-scrim');
  if (!drawer || !scrim) return;
  const wasOpen = drawer.classList.contains('is-open') || drawer.getAttribute('aria-hidden') === 'false';
  const selected = cognitiveHomeState.selected;
  const previousTrigger = cognitiveHomeState.drawerTrigger;
  drawer.classList.remove('is-open');
  drawer.setAttribute('aria-hidden', 'true');
  scrim.classList.remove('is-open');
  scrim.hidden = true;
  document.body.classList.remove('cognitive-chain-open');
  if (!activeDrawerId) document.getElementById('app').inert = false;
  cognitiveHomeState.selected = null;
  if (cognitiveDemoState.active) setCognitiveSecondaryExpanded();
  else if (selected?.kind === 'library') setCognitiveSecondaryExpanded();
  if (wasOpen && restoreFocus) {
    const liveTrigger = previousTrigger instanceof Element && previousTrigger.isConnected
      ? previousTrigger
      : [...document.querySelectorAll('[data-cognitive-entity][data-cognitive-id]')]
        .find(element => element.dataset.cognitiveEntity === selected?.kind
          && element.dataset.cognitiveId === selected?.identifier
          && !element.closest('[hidden]'));
    if (liveTrigger instanceof Element
        && typeof liveTrigger.focus === 'function'
        && !liveTrigger.closest('[inert]')) {
      liveTrigger.focus({ preventScroll: true });
    }
  }
  cognitiveHomeState.drawerTrigger = null;
}

function setCognitiveActionStatus(message = '', tone = '') {
  cognitiveHomeState.actionNotice = message;
  cognitiveHomeState.actionNoticeTone = tone;
  const status = document.querySelector('[data-cognitive-action-status]');
  if (status) {
    status.textContent = message;
    status.hidden = !message;
    status.className = `cognitive-action-status${tone ? ` ${tone}` : ''}`;
  }
  const drawer = document.getElementById('cognitive-chain-drawer');
  if (drawer) {
    drawer.querySelectorAll('[data-cognitive-action], [data-cognitive-terminal-action], [data-cognitive-form-toggle], .cognitive-edit-form input, .cognitive-edit-form textarea, .cognitive-edit-form select, .cognitive-edit-form button')
      .forEach(control => { control.disabled = cognitiveHomeState.actionMutating; });
  }
  if (cognitiveHomeState.status === 'ready') renderCognitiveHome();
}

function cognitiveSelectedActionTarget(action) {
  const selected = cognitiveHomeState.selected;
  if (!selected) return null;
  if (['confirm_receipt', 'edit_receipt', 'original_only'].includes(action)
      && selected.kind === 'record') {
    const record = cognitiveRecordById(selected.identifier);
    const verified = record?.receipt_ref
      ? cognitiveHomeState.verifiedReceipts.get(record.receipt_ref.id) : null;
    return verified && cognitiveSameObjectRef(verified.ref, record.receipt_ref) ? verified : null;
  }
  if (['edit_reusable_memory', 'delete_reusable_memory'].includes(action)
      && selected.kind === 'node') {
    const node = cognitiveNodeById(selected.identifier);
    const verified = node ? cognitiveHomeState.verifiedMemories.get(node.memory_ref.id) : null;
    return verified && cognitiveSameObjectRef(verified.ref, node.memory_ref) ? verified : null;
  }
  if (['edit_relation', 'delete_relation'].includes(action)
      && selected.kind === 'edge') {
    const edge = cognitiveEdgeById(selected.identifier);
    const verified = edge ? cognitiveHomeState.verifiedRelations.get(edge.relation_ref.id) : null;
    return verified && cognitiveSameObjectRef(verified.ref, edge.relation_ref) ? verified : null;
  }
  return null;
}

function cognitiveCommaList(value) {
  const items = String(value || '').split(/[,，\n]+/u).map(item => item.trim()).filter(Boolean);
  return [...new Set(items)];
}

function cognitiveCheckedValues(form, name) {
  return [...form.querySelectorAll(`input[name="${name}"]:checked`)].map(input => input.value);
}

function cognitivePayloadFromForm(form, action) {
  if (action === 'edit_receipt') {
    return {
      summary: form.elements.summary.value.trim(),
      facets: {
        content_types: cognitiveCheckedValues(form, 'content_types'),
        topics: cognitiveCommaList(form.elements.topics.value),
        objects: cognitiveCommaList(form.elements.objects.value),
        stance: form.elements.stance.value,
        cognitive_state: form.elements.cognitive_state.value,
        purposes: cognitiveCheckedValues(form, 'purposes'),
      },
    };
  }
  if (action === 'edit_reusable_memory') {
    return {
      statement: form.elements.statement.value.trim(),
      topics: cognitiveCommaList(form.elements.topics.value),
      purposes: cognitiveCheckedValues(form, 'purposes'),
    };
  }
  if (action === 'edit_relation') {
    return {
      type: form.elements.type.value,
      statement: form.elements.statement.value.trim(),
    };
  }
  return null;
}

async function cognitiveActionTargetStillCurrent(root, targetRef, library) {
  if (targetRef.kind === 'interpretation_receipt') {
    const rows = await readCognitiveDirectoryRows(root, COGNITIVE_RECEIPT_REVISION_PATH);
    const revisions = rows.map(row => COGNITIVE_RECEIPT_FILE_RE.exec(row.name))
      .filter(match => match && match[1] === targetRef.id)
      .map(match => Number(match[2]));
    if (!revisions.length || Math.max(...revisions) !== targetRef.revision) return false;
    const result = await readCognitiveJsonFile(
      root, COGNITIVE_RECEIPT_REVISION_PATH, cognitiveRevisionFileName(targetRef)
    );
    if (!result.exists || result.sha256 !== targetRef.revision_sha256) return false;
    const value = cognitiveExactObject(
      result.value, COGNITIVE_RECEIPT_REVISION_FIELDS, 'interpretation receipt revision'
    );
    return value.receipt_id === targetRef.id
      && value.revision === targetRef.revision
      && ['ready', 'needs_review'].includes(value.status);
  }

  const catalogResult = await readCognitiveJsonFile(
    root, COGNITIVE_HOME_ROOT_PATH, 'formal-head-index.json'
  );
  if (!catalogResult.exists) return false;
  const catalog = library.validateFormalHeadIndex(catalogResult.value);
  const memory = targetRef.kind === 'reusable_memory';
  const refs = memory ? catalog.reusable_memories : catalog.relations;
  const current = refs.find(ref => ref.id === targetRef.id);
  if (!cognitiveSameObjectRef(current, targetRef)) return false;
  const path = memory ? COGNITIVE_MEMORY_REVISION_PATH : COGNITIVE_RELATION_REVISION_PATH;
  const fields = memory ? COGNITIVE_MEMORY_REVISION_FIELDS : COGNITIVE_RELATION_REVISION_FIELDS;
  const idField = memory ? 'memory_id' : 'relation_id';
  const result = await readCognitiveJsonFile(root, path, cognitiveRevisionFileName(targetRef));
  if (!result.exists || result.sha256 !== targetRef.revision_sha256) return false;
  const value = cognitiveExactObject(result.value, fields, `${targetRef.kind} revision`);
  return value[idField] === targetRef.id
    && value.revision === targetRef.revision
    && value.status === 'active';
}

async function cognitiveProjectionHasCurrentActionWatermark(context) {
  const library = cognitiveHomeLibrary();
  const currentWatermark = await readCognitiveActionWatermark(context.handle, library);
  const homeResult = await readCognitiveJsonFile(
    context.handle, COGNITIVE_HOME_PROJECTION_PATH, 'home_projection.json'
  );
  if (!homeResult.exists) return false;
  const home = library.validateHomeProjection(homeResult.value);
  return home.input_hashes.user_action_watermark_sha256 === currentWatermark;
}

function scheduleCognitiveProjectionRefresh(context, attempt = 0) {
  if (!directoryContextStillCurrent(context) || attempt >= 300) {
    cognitiveHomeState.actionMutating = false;
    setCognitiveActionStatus('校正结果已保存；主页仍在等待本地重建，稍后刷新即可。', 'is-warning');
    return;
  }
  setTimeout(async () => {
    try {
      if (!directoryContextStillCurrent(context)) return;
      if (await cognitiveProjectionHasCurrentActionWatermark(context)) {
        cognitiveHomeState.actionMutating = false;
        cognitiveHomeState.pendingAction = null;
        cognitiveHomeState.actionNotice = '校正已应用，主页已更新。';
        cognitiveHomeState.actionNoticeTone = 'is-success';
        await refreshCognitiveHomeProjection(context.handle, context.generation);
        return;
      }
    } catch (error) {
      console.warn('等待 cognitive 主页重建失败，继续轮询', error);
    }
    scheduleCognitiveProjectionRefresh(context, attempt + 1);
  }, 2000);
}

function scheduleCognitiveActionResultPoll(pending, attempt = 0) {
  if (cognitiveHomeState.pendingAction !== pending || !directoryContextStillCurrent(pending.context)) return;
  if (attempt >= 300) {
    setCognitiveActionStatus('校正动作已保存，仍在等待本地 Worker 处理。', 'is-warning');
    return;
  }
  setTimeout(async () => {
    if (cognitiveHomeState.pendingAction !== pending || !directoryContextStillCurrent(pending.context)) return;
    try {
      const library = cognitiveHomeLibrary();
      const resultFile = await readCognitiveJsonFile(
        pending.context.handle,
        COGNITIVE_ACTION_RESULT_PATH,
        library.cognitiveActionResultFileName(pending.action.id)
      );
      if (resultFile.exists) {
        const result = library.validateCognitiveActionResult(resultFile.value);
        if (result.action_id !== pending.action.id || result.action_sha256 !== pending.actionSha256) {
          throw new Error('action result 与已保存动作不一致');
        }
        cognitiveHomeState.actionNotice = result.status === 'applied'
          ? '校正已应用，正在重建主页。'
          : result.status === 'conflict'
            ? '对象版本已变化，本次没有套用到新版本。'
            : '本次校正未通过本地合同校验。';
        cognitiveHomeState.actionNoticeTone = result.status === 'applied' ? 'is-success' : 'is-error';
        closeCognitiveChainDrawer(true);
        scheduleCognitiveProjectionRefresh(pending.context);
        return;
      }
    } catch (error) {
      cognitiveHomeState.actionMutating = false;
      cognitiveHomeState.pendingAction = null;
      setCognitiveActionStatus(`无法安全核对校正结果：${shortError(error)}`, 'is-error');
      return;
    }
    scheduleCognitiveActionResultPoll(pending, attempt + 1);
  }, 2000);
}

async function submitCognitiveUserAction(action, payload = null) {
  if (cognitiveHomeState.actionMutating) return;
  if (cognitiveHomeState.status !== 'ready' || !cognitiveHomeState.home) {
    setCognitiveActionStatus('当前只能读取上一版内容；完整校验通过前不会写入校正。', 'is-error');
    return;
  }
  const target = cognitiveSelectedActionTarget(action);
  if (!target) {
    setCognitiveActionStatus('这个对象已变化，请关闭后重新打开。', 'is-error');
    return;
  }
  const context = captureActiveDirectoryContext();
  if (!context) {
    setCognitiveActionStatus('当前数据目录已变化，本次没有写入。', 'is-error');
    return;
  }
  let userAction;
  try {
    userAction = cognitiveHomeLibrary().buildCognitiveUserAction({
      id: newSelfReflectionId('cact'),
      action,
      targetRef: target.ref,
      payload,
    });
  } catch (error) {
    setCognitiveActionStatus(`修改内容不符合本地合同：${shortError(error)}`, 'is-error');
    return;
  }

  cognitiveHomeState.actionMutating = true;
  setCognitiveActionStatus(action === 'original_only'
    ? '正在保存“仅保留原文”终态动作…'
    : '正在保存这次校正…');
  try {
    if (!(await ensureWritePermission(context.handle))) {
      cognitiveHomeState.actionMutating = false;
      setCognitiveActionStatus('未获得读写授权，本次校正没有保存。', 'is-error');
      return;
    }
    if (!directoryContextStillCurrent(context)) return;
    await enqueueContextAgentMutation(() => withArchiveMutationLock(async () => {
      if (!directoryContextStillCurrent(context)) throw new Error('数据目录已变化');
      if (!await archiveContextMatchesPersisted(context)) {
        throw new Error('数据目录已在另一页切换，本次校正已取消');
      }
      const library = cognitiveHomeLibrary();
      const watermark = await readCognitiveActionWatermark(context.handle, library);
      if (watermark !== cognitiveHomeState.home.input_hashes.user_action_watermark_sha256) {
        throw new Error('用户校正队列已变化，请刷新后再试');
      }
      if (!await cognitiveActionTargetStillCurrent(context.handle, target.ref, library)) {
        throw new Error('目标版本已变化，本次没有写入');
      }
      // 权限、目录身份、action watermark 和 target CAS 全部通过后，
      // 才创建 cognitive user-actions 目录；无权写入时不会留下空目录。
      const directory = await nestedDirectory(context.handle, COGNITIVE_ACTION_PATH, true);
      await writeContextJsonAtomically(
        directory, library.cognitiveActionFileName(userAction.id), userAction
      );
    }));
    if (!directoryContextStillCurrent(context)) return;
    const library = cognitiveHomeLibrary();
    const pending = {
      action: userAction,
      actionSha256: library.sha256Hex(library.serializeCognitiveAction(userAction)),
      context,
    };
    cognitiveHomeState.pendingAction = pending;
    setCognitiveActionStatus('校正动作已保存，正在等待本地处理；当前内容仍是上一个已校验版本。', 'is-warning');
    scheduleCognitiveActionResultPoll(pending);
  } catch (error) {
    cognitiveHomeState.actionMutating = false;
    cognitiveHomeState.pendingAction = null;
    setCognitiveActionStatus(`校正保存失败：${shortError(error)}`, 'is-error');
  }
}

function setCognitiveManualDayStatus(message = '', tone = '') {
  cognitiveHomeState.manualDayNotice = message;
  cognitiveHomeState.manualDayNoticeTone = tone;
  if (cognitiveHomeState.status === 'ready') renderCognitiveHome();
}

function cognitiveManualDayResultMessage(result) {
  if (result.status === 'master_gate_disabled') {
    return { text: '本地认知整理尚未启用，本次没有执行。', tone: 'is-error' };
  }
  if (result.status === 'rejected_date') {
    return { text: '请求日期已过期，本次没有归并。', tone: 'is-error' };
  }
  if (result.status === 'runner_failed') {
    return { text: '本地归并未完成，原始记录仍已保留。', tone: 'is-error' };
  }
  const messages = {
    completed: '今日归并已完成，正在刷新主页。',
    committed: '今日归并已提交，正在刷新主页。',
    committed_with_warnings: '今日归并已提交，有一项结果待核对。',
    no_change: '今日已归并，本次没有形成新的长期变化。',
    no_candidate: '今日已检查，还没有可归并的候选内容。',
    no_records: '今天还没有可归并的记录。',
    no_receipts: '今天的记录尚未完成逐条整理。',
    stale: '今日归并需要重新核对，本次没有提交新结果。',
    error: '今日归并未完成，原始记录仍已保留。',
    budget_exhausted: '今日归并已暂停，原始记录仍已保留。',
  };
  const errorStatuses = new Set(['stale', 'error', 'budget_exhausted']);
  const warningStatuses = new Set(['committed_with_warnings', 'no_candidate', 'no_records', 'no_receipts']);
  return {
    text: messages[result.runner_status] || '本地归并已返回结果。',
    tone: errorStatuses.has(result.runner_status)
      ? 'is-error'
      : warningStatuses.has(result.runner_status) ? 'is-warning' : 'is-success',
  };
}

async function readCognitiveManualDayResult(pending) {
  const rows = await readCognitiveDirectoryRows(
    pending.context.handle, COGNITIVE_MANUAL_DAY_RESULT_PATH
  );
  const matches = [];
  const library = cognitiveHomeLibrary();
  for (const row of rows) {
    if (row.handle.kind !== 'file' || !COGNITIVE_MANUAL_DAY_RESULT_FILE_RE.test(row.name)) continue;
    const resultFile = await readCognitiveJsonFile(
      pending.context.handle, COGNITIVE_MANUAL_DAY_RESULT_PATH, row.name
    );
    if (!resultFile.exists) continue;
    let result;
    try {
      result = library.validateManualDayResult(resultFile.value);
    } catch (error) {
      // An unrelated malformed result cannot answer this request. A file that
      // names this request must fail closed instead of being silently skipped.
      if (resultFile.value?.request_id === pending.request.id) throw error;
      continue;
    }
    if (result.id !== row.name.replace(/\.json$/, '')) {
      throw new Error('manual day result 文件名与 id 不一致');
    }
    if (result.request_id === pending.request.id) matches.push(result);
  }
  if (matches.length > 1) throw new Error('同一归并请求出现多个结果');
  if (!matches.length) return null;
  const result = matches[0];
  if (result.request_sha256 !== pending.requestSha256
      || result.local_date !== pending.request.local_date) {
    throw new Error('manual day result 没有绑定当前请求');
  }
  return result;
}

function scheduleCognitiveManualDayResultPoll(pending, attempt = 0) {
  if (cognitiveHomeState.pendingManualDay !== pending
      || !directoryContextStillCurrent(pending.context)) return;
  if (attempt >= 300) {
    cognitiveHomeState.manualDayMutating = false;
    setCognitiveManualDayStatus('归并请求已保存，仍在等待本地 Worker 处理。', 'is-warning');
    return;
  }
  setTimeout(async () => {
    if (cognitiveHomeState.pendingManualDay !== pending
        || !directoryContextStillCurrent(pending.context)) return;
    try {
      const result = await readCognitiveManualDayResult(pending);
      if (result) {
        const message = cognitiveManualDayResultMessage(result);
        cognitiveHomeState.manualDayMutating = false;
        cognitiveHomeState.pendingManualDay = null;
        setCognitiveManualDayStatus(message.text, message.tone);
        if (result.status === 'completed') {
          await refreshCognitiveHomeProjection(pending.context.handle, pending.context.generation);
        }
        return;
      }
    } catch (error) {
      cognitiveHomeState.manualDayMutating = false;
      cognitiveHomeState.pendingManualDay = null;
      setCognitiveManualDayStatus(`无法安全核对归并结果：${shortError(error)}`, 'is-error');
      return;
    }
    scheduleCognitiveManualDayResultPoll(pending, attempt + 1);
  }, 2000);
}

async function submitCognitiveManualDayRequest() {
  if (cognitiveDemoState.active) {
    setCognitiveManualDayStatus('当前版本只保留基础记录；新的记录暂时不会重算地景与长期理解。', 'is-warning');
    return;
  }
  if (cognitiveHomeState.manualDayMutating) return;
  if (cognitiveHomeState.status !== 'ready' || !cognitiveHomeState.home) {
    setCognitiveManualDayStatus('当前投影尚未通过校验，本次没有写入。', 'is-error');
    return;
  }
  const context = captureActiveDirectoryContext();
  if (!context) {
    setCognitiveManualDayStatus('当前数据目录已变化，本次没有写入。', 'is-error');
    return;
  }
  const library = cognitiveHomeLibrary();
  let request;
  try {
    request = library.buildManualDayRequest({
      id: newSelfReflectionId('cman'),
      localDate: getLocalDate(),
    });
  } catch (error) {
    setCognitiveManualDayStatus(`无法建立归并请求：${shortError(error)}`, 'is-error');
    return;
  }

  cognitiveHomeState.manualDayMutating = true;
  setCognitiveManualDayStatus('正在保存本地归并请求…');
  try {
    if (!(await ensureWritePermission(context.handle))) {
      cognitiveHomeState.manualDayMutating = false;
      setCognitiveManualDayStatus('未获得读写授权，本次请求没有保存。', 'is-error');
      return;
    }
    if (!directoryContextStillCurrent(context)) return;
    await enqueueContextAgentMutation(() => withArchiveMutationLock(async () => {
      if (!directoryContextStillCurrent(context)) throw new Error('数据目录已变化');
      if (!await archiveContextMatchesPersisted(context)) {
        throw new Error('数据目录已在另一页切换，本次请求已取消');
      }
      if (request.local_date !== getLocalDate()) {
        throw new Error('日期已变化，请重新发起今日归并');
      }
      // Only after permission, persisted-directory identity and local-date CAS
      // pass may the append-only manual request directory be created.
      const directory = await nestedDirectory(
        context.handle, COGNITIVE_MANUAL_DAY_REQUEST_PATH, true
      );
      const writeResult = await writeContextJsonAtomically(
        directory, library.manualDayRequestFileName(request.id), request
      );
      if (writeResult.unchanged) throw new Error('归并请求 id 已存在');
    }));
    if (!directoryContextStillCurrent(context)) return;
    const pending = {
      request,
      requestSha256: library.sha256Hex(library.serializeManualDayRequest(request)),
      context,
    };
    cognitiveHomeState.pendingManualDay = pending;
    setCognitiveManualDayStatus('归并请求已保存，正在等待本地处理；当前地景仍是上一份已校验结果。', 'is-warning');
    scheduleCognitiveManualDayResultPoll(pending);
  } catch (error) {
    cognitiveHomeState.manualDayMutating = false;
    cognitiveHomeState.pendingManualDay = null;
    setCognitiveManualDayStatus(`归并请求保存失败：${shortError(error)}`, 'is-error');
  }
}

function setCognitiveSecondaryExpanded(name = '') {
  document.querySelectorAll('[data-cognitive-secondary]').forEach(button => {
    button.setAttribute('aria-expanded', button.dataset.cognitiveSecondary === name ? 'true' : 'false');
  });
}

function openCognitiveSecondary(name, trigger = null) {
  closeCognitiveChainDrawer(false);
  closeCognitiveOutputPopover(false);
  if (name === 'context') {
    openCognitiveChainDrawer('library', 'current', trigger);
    setCognitiveSecondaryExpanded(name);
    if (cognitiveDemoState.active) return;
    if (!contextAgentState.loaded) renderContextAgentView();
    const context = captureActiveDirectoryContext();
    if (context) void refreshContextAgentData({ context });
    return;
  }
  if (name === 'archive' && cognitiveDemoState.active) {
    openCognitiveChainDrawer('demo_history', 'current', trigger);
    setCognitiveSecondaryExpanded(name);
    return;
  }
  const target = {
    daily: 'daily-summary-tab',
    archive: 'archive-tab',
  }[name];
  if (target) {
    document.getElementById(target)?.click();
    if (activeDrawerId) lastDrawerTrigger = trigger;
    setCognitiveSecondaryExpanded(name);
  }
  if (name === 'output') openCognitiveOutputPopover(trigger);
}

function openCognitiveOutputPopover(trigger = null) {
  const legacy = document.getElementById('legacy-dashboard-shell');
  if (!legacy) return;
  cognitiveHomeState.outputOpen = true;
  cognitiveHomeState.outputTrigger = trigger || document.activeElement;
  setCognitiveSecondaryExpanded('output');
  legacy.classList.add('is-output-popover');
  legacy.hidden = false;
  legacy.setAttribute('role', 'dialog');
  legacy.setAttribute('aria-modal', 'false');
  legacy.setAttribute('aria-label', '输出记录给 AI');
  requestAnimationFrame(() => document.getElementById('range-select')?.focus());
}

function closeCognitiveOutputPopover(restoreFocus = true) {
  const legacy = document.getElementById('legacy-dashboard-shell');
  if (!legacy || !cognitiveHomeState.outputOpen) return;
  const trigger = cognitiveHomeState.outputTrigger;
  cognitiveHomeState.outputOpen = false;
  cognitiveHomeState.outputTrigger = null;
  legacy.classList.remove('is-output-popover');
  legacy.removeAttribute('role');
  legacy.removeAttribute('aria-modal');
  legacy.setAttribute('aria-label', '记录主页');
  legacy.hidden = cognitiveHomeState.status === 'ready';
  setCognitiveSecondaryExpanded();
  if (restoreFocus && trigger instanceof HTMLElement && trigger.isConnected && !trigger.closest('[inert]')) {
    trigger.focus({ preventScroll: true });
  }
}

function cognitivePointerDistance(left, right) {
  return Math.hypot(right.clientX - left.clientX, right.clientY - left.clientY);
}

function cognitivePointerMidpoint(left, right) {
  return { clientX: (left.clientX + right.clientX) / 2, clientY: (left.clientY + right.clientY) / 2 };
}

function initCognitiveMapCameraInteractions() {
  if (cognitiveMapCameraState.interactionsInited) return;
  const region = document.getElementById('cognitive-map-region');
  if (!region) return;
  cognitiveMapCameraState.interactionsInited = true;

  region.addEventListener('click', event => {
    const action = event.target.closest?.('[data-cognitive-map-action]')?.dataset.cognitiveMapAction;
    if (!action) return;
    event.preventDefault();
    event.stopPropagation();
    if (action === 'fullscreen') {
      cognitiveMapCameraState.lastPointerClientX = event.clientX;
      cognitiveMapCameraState.lastPointerClientY = event.clientY;
      cognitiveSetMapFullscreen(!cognitiveMapCameraState.fullscreen);
      return;
    }
    if (action === 'zoom-in') cognitiveZoomMapAt(cognitiveMapCameraState.zoom * 1.32);
    if (action === 'zoom-out') cognitiveZoomMapAt(cognitiveMapCameraState.zoom / 1.32);
    if (action === 'reset') cognitiveResetMapCamera();
  });

  region.addEventListener('wheel', event => {
    if (event.target.closest?.('.cognitive-map-controls')) return;
    event.preventDefault();
    const factor = Math.exp(-event.deltaY * .00145);
    cognitiveZoomMapAt(cognitiveMapCameraState.zoom * factor, event.clientX, event.clientY);
  }, { passive: false });

  region.addEventListener('dblclick', event => {
    if (event.target.closest?.('.cognitive-map-controls')) return;
    event.preventDefault();
    cognitiveZoomMapAt(cognitiveMapCameraState.zoom * 1.55, event.clientX, event.clientY);
  });

  region.addEventListener('pointerdown', event => {
    if (event.target.closest?.('.cognitive-map-controls')) return;
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    window.getSelection?.()?.removeAllRanges?.();
    cognitiveMapCameraState.pointers.set(event.pointerId, {
      clientX: event.clientX, clientY: event.clientY,
    });
    cognitiveMapCameraState.moved = false;
    if (cognitiveMapCameraState.pointers.size === 1) {
      cognitiveMapCameraState.dragPointerId = event.pointerId;
      cognitiveMapCameraState.dragOrigin = {
        clientX: event.clientX, clientY: event.clientY,
      };
    } else if (cognitiveMapCameraState.pointers.size === 2) {
      const pointers = [...cognitiveMapCameraState.pointers.values()];
      cognitiveMapCameraState.dragPointerId = null;
      cognitiveMapCameraState.pinchOrigin = {
        distance: Math.max(1, cognitivePointerDistance(pointers[0], pointers[1])),
        zoom: cognitiveMapCameraState.zoom,
      };
    }
  });

  region.addEventListener('pointermove', event => {
    const previous = cognitiveMapCameraState.pointers.get(event.pointerId);
    if (!previous) {
      if (!cognitiveMapCameraState.hoverArmed) {
        const origin = cognitiveMapCameraState.hoverGateOrigin;
        if (origin && Math.hypot(
          event.clientX - origin.clientX,
          event.clientY - origin.clientY
        ) < 3) return;
        cognitiveMapCameraState.hoverArmed = true;
        cognitiveMapCameraState.hoverGateOrigin = null;
        const entity = event.target.closest?.('[data-cognitive-entity]');
        if (entity?.closest('#cognitive-landscape-map')) {
          applyCognitiveMapHover(entity.dataset.cognitiveEntity, entity.dataset.cognitiveId);
        }
      }
      cognitiveMapCameraState.lastPointerClientX = event.clientX;
      cognitiveMapCameraState.lastPointerClientY = event.clientY;
      if (event.pointerType === 'mouse') cognitiveScheduleMapTilt(event);
      return;
    }
    cognitiveMapCameraState.pointers.set(event.pointerId, {
      clientX: event.clientX, clientY: event.clientY,
    });
    if (cognitiveMapCameraState.pointers.size >= 2 && cognitiveMapCameraState.pinchOrigin) {
      const pointers = [...cognitiveMapCameraState.pointers.values()].slice(0, 2);
      const distance = cognitivePointerDistance(pointers[0], pointers[1]);
      const midpoint = cognitivePointerMidpoint(pointers[0], pointers[1]);
      if (!cognitiveMapCameraState.moved) {
        cognitiveMapCameraState.pointers.forEach((_, pointerId) => {
          region.setPointerCapture?.(pointerId);
        });
        region.classList.add('is-cognitive-map-dragging');
      }
      cognitiveMapCameraState.moved = true;
      cognitiveZoomMapAt(
        cognitiveMapCameraState.pinchOrigin.zoom
          * distance / cognitiveMapCameraState.pinchOrigin.distance,
        midpoint.clientX,
        midpoint.clientY
      );
      event.preventDefault();
      return;
    }
    if (cognitiveMapCameraState.dragPointerId !== event.pointerId) return;
    const origin = cognitiveMapCameraState.dragOrigin;
    if (origin && Math.hypot(event.clientX - origin.clientX, event.clientY - origin.clientY) > 3) {
      if (!cognitiveMapCameraState.moved) {
        region.setPointerCapture?.(event.pointerId);
        region.classList.add('is-cognitive-map-dragging');
      }
      cognitiveMapCameraState.moved = true;
    }
    if (cognitiveMapCameraState.moved) {
      const previousPoint = cognitiveMapSvgPoint(previous.clientX, previous.clientY);
      const nextPoint = cognitiveMapSvgPoint(event.clientX, event.clientY);
      if (previousPoint && nextPoint) {
        cognitivePanMap(previousPoint.x - nextPoint.x, previousPoint.y - nextPoint.y);
      }
      event.preventDefault();
    }
  });

  const finishPointer = event => {
    if (!cognitiveMapCameraState.pointers.has(event.pointerId)) return;
    if (cognitiveMapCameraState.moved) {
      cognitiveMapCameraState.suppressClickUntil = performance.now() + 320;
    }
    cognitiveMapCameraState.pointers.delete(event.pointerId);
    cognitiveMapCameraState.pinchOrigin = null;
    const remaining = [...cognitiveMapCameraState.pointers.entries()];
    if (remaining.length === 1) {
      cognitiveMapCameraState.dragPointerId = remaining[0][0];
      cognitiveMapCameraState.dragOrigin = { ...remaining[0][1] };
    } else {
      cognitiveMapCameraState.dragPointerId = null;
      cognitiveMapCameraState.dragOrigin = null;
      region.classList.remove('is-cognitive-map-dragging');
    }
  };
  region.addEventListener('pointerup', finishPointer);
  region.addEventListener('pointercancel', finishPointer);
  region.addEventListener('lostpointercapture', finishPointer);

  region.addEventListener('pointerleave', () => {
    if (!cognitiveMapCameraState.pointers.size) cognitiveSetMapTilt(0, 0, 50, 50);
  });

  region.addEventListener('keydown', event => {
    const key = event.key;
    if (key === 'Escape' && cognitiveMapInteractionState.pinnedId) {
      event.preventDefault();
      event.stopPropagation();
      clearCognitiveMapPin();
      return;
    }
    if (key === 'Escape' && cognitiveMapInteractionState.insightId) {
      event.preventDefault();
      event.stopPropagation();
      clearCognitiveInsightMapFocus();
      return;
    }
    if (key === 'Escape' && cognitiveMapCameraState.fullscreen) {
      event.preventDefault();
      event.stopPropagation();
      cognitiveSetMapFullscreen(false);
      return;
    }
    if (event.target.closest?.('.cognitive-map-controls')) return;
    if (!['+', '=', '-', '_', '0', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(key)) return;
    event.preventDefault();
    event.stopPropagation();
    if (key === '+' || key === '=') cognitiveZoomMapAt(cognitiveMapCameraState.zoom * 1.28);
    else if (key === '-' || key === '_') cognitiveZoomMapAt(cognitiveMapCameraState.zoom / 1.28);
    else if (key === '0') cognitiveResetMapCamera();
    else {
      const step = 46 / cognitiveMapCameraState.zoom;
      if (key === 'ArrowLeft') cognitivePanMap(-step, 0);
      if (key === 'ArrowRight') cognitivePanMap(step, 0);
      if (key === 'ArrowUp') cognitivePanMap(0, -step);
      if (key === 'ArrowDown') cognitivePanMap(0, step);
    }
  });

  if (typeof cognitiveReducedMotionMedia?.addEventListener === 'function') {
    cognitiveReducedMotionMedia.addEventListener('change', () => cognitiveSetMapTilt(0, 0, 50, 50));
  }
  if (typeof ResizeObserver === 'function') {
    cognitiveMapCameraState.resizeObserver = new ResizeObserver(() => (
      cognitiveApplyMapCamera({ clamp: false })
    ));
    cognitiveMapCameraState.resizeObserver.observe(region);
  }
  cognitiveApplyMapCamera();
}

let cognitiveHomeInteractionsInited = false;
function initCognitivePortraitUpdateControls() {
  const autoUpdate = document.getElementById('cognitive-portrait-auto-update');
  const updateAt = document.getElementById('cognitive-portrait-update-at');
  if (!autoUpdate || !updateAt) return;
  const sync = () => {
    updateAt.disabled = !autoUpdate.checked;
    updateAt.setAttribute('aria-disabled', String(updateAt.disabled));
  };
  autoUpdate.addEventListener('change', sync);
  sync();
}

function initCognitiveHomeInteractions() {
  if (cognitiveHomeInteractionsInited) return;
  cognitiveHomeInteractionsInited = true;
  const shell = document.getElementById('cognitive-home-shell');
  const timeline = document.getElementById('cognitive-record-list');
  initCognitiveMapCameraInteractions();
  initCognitivePortraitUpdateControls();
  if (timeline) {
    timeline.addEventListener('scroll', () => syncCognitiveTimelineNowControl(timeline), {
      passive: true,
    });
  }
  if (cognitiveCompactLandscapeMedia) {
    if (typeof cognitiveCompactLandscapeMedia.addEventListener === 'function') {
      cognitiveCompactLandscapeMedia.addEventListener('change', applyCognitiveView);
    } else if (typeof cognitiveCompactLandscapeMedia.addListener === 'function') {
      cognitiveCompactLandscapeMedia.addListener(applyCognitiveView);
    }
  }
  shell.addEventListener('click', event => {
    if (performance.now() < cognitiveMapCameraState.suppressClickUntil
        && event.target.closest?.('#cognitive-map-region')) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    const manualDay = event.target.closest('[data-cognitive-manual-day]');
    if (manualDay) {
      void submitCognitiveManualDayRequest();
      return;
    }
    const timelineNow = event.target.closest('[data-cognitive-timeline-now]');
    if (timelineNow) {
      scrollCognitiveTimelineToNow();
      return;
    }
    const view = event.target.closest('[data-cognitive-view]');
    if (view) {
      cognitiveHomeState.activeView = view.dataset.cognitiveView;
      applyCognitiveView();
      return;
    }
    const portrait = event.target.closest('[data-cognitive-portrait-id]');
    if (portrait) {
      const active = toggleCognitiveInsightMapFocus(
        portrait.dataset.cognitivePortraitId,
        portrait
      );
      if (!active) {
        const drawer = document.getElementById('cognitive-chain-drawer');
        if (drawer?.getAttribute('aria-hidden') === 'false') closeCognitiveChainDrawer(false);
        return;
      }
      openCognitiveChainDrawer('library', portrait.dataset.cognitivePortraitId, portrait);
      setCognitiveSecondaryExpanded('context');
      return;
    }
    const secondary = event.target.closest('[data-cognitive-secondary]');
    if (secondary) {
      openCognitiveSecondary(secondary.dataset.cognitiveSecondary, secondary);
      return;
    }
    const entity = event.target.closest('[data-cognitive-entity]');
    if (entity) {
      if (cognitiveDemoState.active
          && entity.closest('#cognitive-landscape-map')
          && entity.dataset.cognitiveEntity !== 'peak') return;
      if (cognitiveDemoState.active && entity.closest('#cognitive-landscape-map')) {
        const pinned = toggleCognitiveMapPin(
          entity.dataset.cognitiveEntity,
          entity.dataset.cognitiveId,
          entity
        );
        if (!pinned) {
          const drawer = document.getElementById('cognitive-chain-drawer');
          if (drawer?.getAttribute('aria-hidden') === 'false') closeCognitiveChainDrawer(false);
          return;
        }
      }
      openCognitiveChainDrawer(
        entity.dataset.cognitiveEntity,
        entity.dataset.cognitiveId,
        entity
      );
      return;
    }
    if (event.target.closest('#cognitive-map-region')) {
      if (cognitiveMapInteractionState.pinnedId) clearCognitiveMapPin();
      if (cognitiveMapInteractionState.insightId) clearCognitiveInsightMapFocus();
    }
  });
  shell.addEventListener('pointerover', event => {
    const portrait = event.target.closest?.('[data-cognitive-portrait-id]');
    if (portrait) return;
    const entity = event.target.closest?.('[data-cognitive-entity]');
    if (!entity || !entity.closest('#cognitive-landscape-map')) return;
    if (!cognitiveMapCameraState.hoverArmed) return;
    if (event.relatedTarget instanceof Node && entity.contains(event.relatedTarget)) return;
    applyCognitiveMapHover(entity.dataset.cognitiveEntity, entity.dataset.cognitiveId);
  });
  shell.addEventListener('pointerout', event => {
    const portrait = event.target.closest?.('[data-cognitive-portrait-id]');
    if (portrait) return;
    const entity = event.target.closest?.('[data-cognitive-entity]');
    if (!entity || !entity.closest('#cognitive-landscape-map')) return;
    if (event.relatedTarget instanceof Node && entity.contains(event.relatedTarget)) return;
    if (entity.contains(document.activeElement)) return;
    clearCognitiveMapHover();
  });
  shell.addEventListener('focusin', event => {
    const portrait = event.target.closest?.('[data-cognitive-portrait-id]');
    if (portrait) return;
    const entity = event.target.closest?.('[data-cognitive-entity]');
    if (entity?.closest('#cognitive-landscape-map')) {
      applyCognitiveMapHover(entity.dataset.cognitiveEntity, entity.dataset.cognitiveId);
    }
  });
  shell.addEventListener('focusout', event => {
    const portrait = event.target.closest?.('[data-cognitive-portrait-id]');
    if (portrait) return;
    const entity = event.target.closest?.('[data-cognitive-entity]');
    if (!entity || !entity.closest('#cognitive-landscape-map')) return;
    if (event.relatedTarget instanceof Node && entity.contains(event.relatedTarget)) return;
    clearCognitiveMapHover();
  });
  shell.addEventListener('keydown', event => {
    const portrait = event.target.closest('[data-cognitive-portrait-id]');
    if (portrait && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      const active = toggleCognitiveInsightMapFocus(
        portrait.dataset.cognitivePortraitId,
        portrait
      );
      if (!active) {
        const drawer = document.getElementById('cognitive-chain-drawer');
        if (drawer?.getAttribute('aria-hidden') === 'false') closeCognitiveChainDrawer(false);
        return;
      }
      openCognitiveChainDrawer('library', portrait.dataset.cognitivePortraitId, portrait);
      setCognitiveSecondaryExpanded('context');
      return;
    }
    const secondary = event.target.closest('[data-cognitive-secondary]');
    if (secondary && ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const buttons = [...shell.querySelectorAll('[data-cognitive-secondary]')];
      let nextIndex = buttons.indexOf(secondary);
      if (event.key === 'Home') nextIndex = 0;
      else if (event.key === 'End') nextIndex = buttons.length - 1;
      else if (['ArrowDown', 'ArrowRight'].includes(event.key)) nextIndex = (nextIndex + 1) % buttons.length;
      else nextIndex = (nextIndex - 1 + buttons.length) % buttons.length;
      buttons[nextIndex]?.focus();
      return;
    }
    const timelineRecord = event.target.closest('[data-cognitive-timeline-index]');
    if (timelineRecord && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      const records = [...timeline.querySelectorAll('[data-cognitive-timeline-index]')];
      if (!records.length) return;
      event.preventDefault();
      const currentIndex = records.indexOf(timelineRecord);
      const nextIndex = event.key === 'Home' ? 0
        : event.key === 'End' ? records.length - 1
          : Math.max(0, Math.min(
            records.length - 1,
            currentIndex + (event.key === 'ArrowRight' ? 1 : -1)
          ));
      const next = records[nextIndex];
      next.focus({ preventScroll: true });
      next.scrollIntoView({
        behavior: cognitiveReducedMotionMedia?.matches ? 'auto' : 'smooth',
        block: 'nearest',
        inline: 'nearest',
      });
      return;
    }
    const entity = event.target.closest('[data-cognitive-entity]');
    if (entity && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      if (cognitiveDemoState.active
          && entity.closest('#cognitive-landscape-map')
          && entity.dataset.cognitiveEntity !== 'peak') return;
      if (cognitiveDemoState.active && entity.closest('#cognitive-landscape-map')) {
        const pinned = toggleCognitiveMapPin(
          entity.dataset.cognitiveEntity,
          entity.dataset.cognitiveId,
          entity
        );
        if (!pinned) {
          const drawer = document.getElementById('cognitive-chain-drawer');
          if (drawer?.getAttribute('aria-hidden') === 'false') closeCognitiveChainDrawer(false);
          return;
        }
      }
      openCognitiveChainDrawer(entity.dataset.cognitiveEntity, entity.dataset.cognitiveId, entity);
      return;
    }
    const view = event.target.closest('[data-cognitive-view]');
    if (!view || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const buttons = [...shell.querySelectorAll('[data-cognitive-view]')];
    const direction = event.key === 'ArrowRight' ? 1 : -1;
    const next = buttons[(buttons.indexOf(view) + direction + buttons.length) % buttons.length];
    next.focus();
    next.click();
  });
  document.getElementById('cognitive-drawer-close').addEventListener('click', () => closeCognitiveChainDrawer());
  document.getElementById('cognitive-drawer-scrim').addEventListener('click', () => closeCognitiveChainDrawer());
  const cognitiveDrawer = document.getElementById('cognitive-chain-drawer');
  cognitiveDrawer.addEventListener('click', event => {
    const demoDay = event.target.closest('[data-demo-open-day]');
    if (demoDay && cognitiveDemoState.active) {
      openCognitiveChainDrawer('demo_day', demoDay.dataset.demoOpenDay, demoDay);
      setCognitiveSecondaryExpanded('archive');
      return;
    }
    const demoPeak = event.target.closest('[data-demo-open-peak]');
    if (demoPeak && cognitiveDemoState.active) {
      clearCognitiveInsightMapFocus();
      openCognitiveChainDrawer('peak', demoPeak.dataset.demoOpenPeak, demoPeak);
      return;
    }
    const demoRecord = event.target.closest('[data-demo-open-record]');
    if (demoRecord && cognitiveDemoState.active) {
      openCognitiveChainDrawer('record', demoRecord.dataset.demoOpenRecord, demoRecord);
      return;
    }
    const demoLibrary = event.target.closest('[data-demo-open-library]');
    if (demoLibrary && cognitiveDemoState.active) {
      openCognitiveChainDrawer('library', 'current', demoLibrary);
      setCognitiveSecondaryExpanded('context');
      return;
    }
    const toggle = event.target.closest('[data-cognitive-form-toggle]');
    if (toggle) {
      const form = cognitiveDrawer.querySelector(`#cognitive-${toggle.dataset.cognitiveFormToggle}-form`);
      if (!form) return;
      form.hidden = !form.hidden;
      toggle.setAttribute('aria-expanded', form.hidden ? 'false' : 'true');
      if (!form.hidden) form.querySelector('textarea, input, select')?.focus();
      return;
    }
    const cancel = event.target.closest('[data-cognitive-form-cancel]');
    if (cancel) {
      const form = cancel.closest('form');
      const toggleButton = cognitiveDrawer.querySelector(
        `[data-cognitive-form-toggle="${cancel.dataset.cognitiveFormCancel}"]`
      );
      form.hidden = true;
      toggleButton?.setAttribute('aria-expanded', 'false');
      toggleButton?.focus();
      return;
    }
    const direct = event.target.closest('[data-cognitive-action]');
    if (direct) {
      void submitCognitiveUserAction(direct.dataset.cognitiveAction, null);
      return;
    }
    const terminal = event.target.closest('[data-cognitive-terminal-action]');
    if (!terminal) return;
    if (terminal.dataset.confirmArmed !== 'true') {
      terminal.dataset.confirmArmed = 'true';
      terminal.dataset.originalLabel = terminal.textContent;
      terminal.textContent = terminal.dataset.confirmLabel;
      terminal.classList.add('is-armed');
      terminal.setAttribute('aria-label', terminal.dataset.confirmLabel);
      setTimeout(() => {
        if (!terminal.isConnected || terminal.dataset.confirmArmed !== 'true') return;
        terminal.dataset.confirmArmed = 'false';
        terminal.textContent = terminal.dataset.originalLabel;
        terminal.classList.remove('is-armed');
        terminal.removeAttribute('aria-label');
      }, 6000);
      return;
    }
    void submitCognitiveUserAction(terminal.dataset.cognitiveTerminalAction, null);
  });
  cognitiveDrawer.addEventListener('submit', event => {
    const form = event.target.closest('[data-cognitive-edit-form]');
    if (!form) return;
    event.preventDefault();
    if (!form.reportValidity()) return;
    const action = form.dataset.cognitiveEditForm;
    void submitCognitiveUserAction(action, cognitivePayloadFromForm(form, action));
  });
  document.addEventListener('click', event => {
    const legacy = document.getElementById('legacy-dashboard-shell');
    if (!legacy?.classList.contains('is-output-popover')) return;
    if (legacy.contains(event.target) || event.target.closest('[data-cognitive-secondary="output"]')) return;
    closeCognitiveOutputPopover();
  });
  document.addEventListener('keydown', event => {
    const drawer = document.getElementById('cognitive-chain-drawer');
    if (drawer.getAttribute('aria-hidden') === 'false') {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeCognitiveChainDrawer();
        if (cognitiveMapInteractionState.pinnedId) clearCognitiveMapPin();
        if (cognitiveMapInteractionState.insightId) clearCognitiveInsightMapFocus();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = [...drawer.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [href], [tabindex]:not([tabindex="-1"])')]
        .filter(element => !element.hidden && element.offsetParent !== null);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!drawer.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
      return;
    }
    if (event.key === 'Escape' && cognitiveMapInteractionState.pinnedId) {
      event.preventDefault();
      clearCognitiveMapPin();
      return;
    }
    if (event.key === 'Escape' && cognitiveMapInteractionState.insightId) {
      event.preventDefault();
      clearCognitiveInsightMapFocus();
      return;
    }
    if (event.key === 'Escape') closeCognitiveOutputPopover();
  });
}

function renderRecordSummary(n) {
  const summary = document.getElementById('record-summary');
  if (!state.todayResolved) {
    summary.classList.add('is-empty');
    summary.textContent = '正在确认今天的记录…';
    return;
  }
  if (n === 0) {
    summary.classList.add('is-empty');
    summary.textContent = state.recordSource === 'cache'
      ? '上次读取时，今天还没有记录'
      : '今天还没有记录';
  } else {
    summary.classList.remove('is-empty');
    const prefix = state.recordSource === 'cache' ? '上次读取：今天留下了' : '今天留下了';
    summary.innerHTML = `<span>${prefix} <strong>${n}</strong> 条记录</span>`;
  }
}

// 把所有 entries 聚合成 { 'YYYY-MM-DD': count }
function buildEntriesByDay() {
  const byDay = {};
  for (const e of state.allEntries) {
    byDay[e.date] = (byDay[e.date] || 0) + 1;
  }
  return byDay;
}

function dateOffset(baseDateStr, deltaDays) {
  const d = new Date(baseDateStr + 'T00:00:00');
  d.setDate(d.getDate() + deltaDays);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function renderStats() {
  const byDay = buildEntriesByDay();
  let weekCount = 0;
  let activeDays = 0;
  for (let i = 0; i < 7; i++) {
    const dateStr = dateOffset(state.todayDate, -i);
    const n = byDay[dateStr] || 0;
    weekCount += n;
    if (n > 0) activeDays++;
  }
  document.getElementById('stats').innerHTML =
    `本周 <strong>${weekCount}</strong> 条 · ` +
    `近 7 天有记录 <strong>${activeDays}</strong> 天`;
}

function renderHeatmap() {
  const operations = window.MementoDashboardOperations;
  const days = operations.buildHeatmapDays(
    state.allEntries,
    state.todayDate,
    state.selectedDate || state.todayDate
  );
  const heatmap = document.getElementById('heatmap');
  heatmap.innerHTML = days.map(day => {
    const labels = formatRecordDate(day.date);
    const levelClass = day.level ? ` l${day.level}` : '';
    const selectedClass = day.selected ? ' is-selected' : '';
    const countLabel = day.count ? `${day.count} 条记录` : '暂无记录';
    const ariaLabel = `${labels.fullLabel}，${labels.weekday}，${countLabel}`;
    return `
      <span class="heat-item${selectedClass}" role="listitem" data-date="${day.date}">
        <button type="button" class="heat-cell${levelClass}" data-date="${day.date}"
                aria-label="${escapeHtml(ariaLabel)}"${day.selected ? ' aria-current="date"' : ''}
                tabindex="${day.selected ? '0' : '-1'}"></button>
        <span class="heat-tooltip" role="tooltip" aria-hidden="true">
          <span>${labels.shortLabel} · ${labels.weekday}</span>
          <strong>${countLabel}</strong>
        </span>
      </span>`;
  }).join('');
  bindHeatmapInteractions(heatmap);
}

function formatRecordDate(date) {
  // JS 周一=1,周日=0;映射到中文
  const d = new Date(date + 'T00:00:00');
  const idx = (d.getDay() + 6) % 7; // 周一=0
  return {
    shortLabel: `${d.getMonth() + 1}月${d.getDate()}日`,
    fullLabel: `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`,
    weekday: `周${'一二三四五六日'[idx]}`,
  };
}

function heatmapDateIsVisible(date) {
  if (!date || !state.todayDate) return false;
  return date >= dateOffset(state.todayDate, -89) && date <= state.todayDate;
}

function selectedDateEntries(filter = 'all') {
  return window.MementoDashboardOperations.filterEntriesForDate(
    state.allEntries,
    state.selectedDate || state.todayDate,
    filter
  );
}

function updateHeatmapSelection() {
  const selectedDate = state.selectedDate || state.todayDate;
  document.querySelectorAll('#heatmap .heat-item').forEach(item => {
    const selected = item.dataset.date === selectedDate;
    const button = item.querySelector('.heat-cell');
    item.classList.toggle('is-selected', selected);
    button.tabIndex = selected ? 0 : -1;
    if (selected) button.setAttribute('aria-current', 'date');
    else button.removeAttribute('aria-current');
  });
}

function renderSectionDivider() {
  const selectedDate = state.selectedDate || state.todayDate;
  const labels = formatRecordDate(selectedDate);
  const prefix = selectedDate === state.todayDate ? '今日' : labels.shortLabel;
  document.getElementById('record-date-label').textContent =
    `${prefix} · ${selectedDate} · ${labels.weekday}`;
}

function renderSelectedDateSection() {
  renderSectionDivider();
  renderChips();
  renderEntryList();
}

function scrollToSelectedDateSection() {
  const section = document.getElementById('section-record-date');
  if (!section || typeof section.scrollIntoView !== 'function') return;
  const reduceMotion = typeof matchMedia === 'function'
    && matchMedia('(prefers-reduced-motion: reduce)').matches;
  section.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
}

function selectHeatmapDate(date, { scroll = false } = {}) {
  if (!heatmapDateIsVisible(date)) return false;
  if (state.selectedDate !== date) state.currentFilter = 'all';
  state.selectedDate = date;
  updateHeatmapSelection();
  renderSelectedDateSection();
  if (scroll) scrollToSelectedDateSection();
  return true;
}

function bindHeatmapInteractions(heatmap) {
  heatmap.onclick = event => {
    const item = event.target.closest('.heat-item');
    if (!item || !heatmap.contains(item)) return;
    const button = item.querySelector('.heat-cell');
    selectHeatmapDate(button.dataset.date, { scroll: true });
  };

  heatmap.onkeydown = event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const button = event.target.closest('.heat-cell');
    if (!button || !heatmap.contains(button)) return;
    const buttons = [...heatmap.querySelectorAll('.heat-cell')];
    const currentIndex = buttons.indexOf(button);
    if (currentIndex < 0) return;
    event.preventDefault();
    const targetIndex = event.key === 'Home' ? 0
      : event.key === 'End' ? buttons.length - 1
      : event.key === 'ArrowLeft' ? Math.max(0, currentIndex - 1)
      : Math.min(buttons.length - 1, currentIndex + 1);
    buttons.forEach((candidate, index) => { candidate.tabIndex = index === targetIndex ? 0 : -1; });
    buttons[targetIndex].focus({ preventScroll: true });
  };
}

function renderChips() {
  const chips = document.getElementById('chips');
  const entries = selectedDateEntries('all');
  const tagCounts = entries.reduce((a, e) => {
    if (e.tag) a[e.tag] = (a[e.tag] || 0) + 1;
    return a;
  }, {});
  const items = [
    { key: 'all',      label: `全部记录 · ${entries.length}` },
    { key: '灵感',     label: `#灵感 · ${tagCounts['灵感'] || 0}` },
    { key: '下次再读', label: `#下次再读 · ${tagCounts['下次再读'] || 0}` },
    { key: 'TODO',     label: `#TODO · ${tagCounts.TODO || 0}` },
  ];

  chips.innerHTML = items.map(({ key, label }) => {
    const isOn = state.currentFilter === key;
    const cls = `chip ${isOn ? 'is-on' : 'is-off'}`;
    return `<button class="${cls}" data-filter="${escapeHtml(key)}">${escapeHtml(label)}</button>`;
  }).join('');

  chips.querySelectorAll('button').forEach(b => {
    b.addEventListener('click', () => {
      state.currentFilter = b.dataset.filter;
      renderChips();
      renderEntryList();
    });
  });
}

function renderEntryList() {
  const list = document.getElementById('entry-list');
  const allForDate = selectedDateEntries('all');
  const filtered = selectedDateEntries(state.currentFilter);

  if (filtered.length === 0) {
    const selectedDate = state.selectedDate || state.todayDate;
    const viewingToday = selectedDate === state.todayDate;
    const text = viewingToday && !state.todayResolved
      ? '正在确认今天的记录…'
      : allForDate.length > 0
        ? '这个分类下还没有记录'
        : viewingToday
          ? (state.recordSource === 'cache' ? '上次读取时，今天还没有记录' : '今天还没有记录')
          : ['fresh', 'shared'].includes(state.recordSource)
            ? '这一天没有记录'
            : '当前显示中，这一天没有记录';
    list.innerHTML = `<div class="empty-state">${text}</div>`;
    return;
  }

  list.innerHTML = filtered.map(renderEntry).join('');
}

function renderEntry(e) {
  const metaParts = [`<span class="entry-time">${escapeHtml(e.time)}</span>`];
  if (e.source) metaParts.push(escapeHtml(e.source));
  if (e.tag) metaParts.push(`<span class="entry-tag">#${escapeHtml(e.tag)}</span>`);

  const noteBlock = e.note
    ? `<div class="entry-note">备注: ${escapeHtml(e.note)}</div>`
    : '';

  return `
    <article class="entry">
      <div class="entry-meta">${metaParts.join(' ')}</div>
      <div class="entry-body">${renderMarkdown(e.body)}</div>
      ${noteBlock}
    </article>
  `;
}

// ----- Prompt 双轴 (A 时间段 × B 风格) -----

// CTA 按钮文字:复制 [时间段] 的 [风格] → AI
function defaultCtaLabel() {
  const range = findRange(state.selectedRange);
  const style = findStyle(state.selectedStyle);
  return style
    ? `复制 ${range.label} 的 ${style.label} → AI`
    : `复制 ${range.label} → AI`;
}

function visibleCtaLabel() {
  const range = findRange(state.selectedRange);
  const style = findStyle(state.selectedStyle);
  return style
    ? `复制当前显示的${range.label} · ${style.label} → AI`
    : `复制当前显示的${range.label} → AI`;
}

function selectedRangeCopyMode() {
  const range = findRange(state.selectedRange);
  return window.MementoDashboardOperations.copyModeForRecordState({
    recordSource: state.recordSource,
    todayResolved: state.todayResolved,
    rangeDays: range.days,
  });
}

function updateCtaLabel() {
  const btn = document.getElementById('copy-btn');
  const label = btn.querySelector('.btn-label');
  const mode = selectedRangeCopyMode();
  btn.disabled = mode === 'blocked';
  btn.dataset.copyMode = mode;
  btn.title = mode === 'visible'
    ? '当前显示的是上次完整记录；今天最新内容仍在后台核对。'
    : mode === 'blocked'
      ? '正在读取今天的记录'
      : '';
  label.textContent = mode === 'fresh'
    ? defaultCtaLabel()
    : mode === 'visible'
      ? visibleCtaLabel()
      : '正在读取今天的记录…';
}

// 填充 A 时间段下拉
function populateRangeSelect() {
  const sel = document.getElementById('range-select');
  if (!sel || !window.MEMENTO_RANGES) return;
  sel.innerHTML = window.MEMENTO_RANGES
    .map(r => `<option value="${escapeHtml(r.id)}">${escapeHtml(r.label)}</option>`).join('');
  sel.value = state.selectedRange;
  sel.onchange = () => {
    state.selectedRange = sel.value;
    setSavedRange(state.selectedRange);
    updateCtaLabel();
  };
}

// 填充 B 风格下拉(不含彩蛋)
function populateStyleSelect() {
  const sel = document.getElementById('style-select');
  if (!sel || !window.MEMENTO_STYLES) return;
  const opts = window.MEMENTO_STYLES
    .filter(p => !p.hidden)
    .map(p => `<option value="${escapeHtml(p.id)}">${p.n} · ${escapeHtml(p.label)}</option>`);
  sel.innerHTML = '<option value="">不附</option>' + opts.join('');
  sel.value = state.selectedStyle || '';
  sel.onchange = () => {
    state.selectedStyle = sel.value || null;
    setSavedStyle(state.selectedStyle);
    updateCtaLabel();
  };
}

function populateSelectors() {
  populateRangeSelect();
  populateStyleSelect();
  updateCtaLabel();
}

// 按 A 时间段拼接 md。days=1 只取今天;多天往前回溯,带 `# === 日期 ===` 分隔。
function assembleRangeMd(days) {
  if (days <= 1) return state.todayFileText || '';
  const lines = [];
  for (let i = days - 1; i >= 0; i--) {
    const date = dateOffset(state.todayDate, -i);
    const file = state.files.find(f => f.date === date);
    if (file) {
      lines.push('', `# === ${date} ===`, '', file.text);
    }
  }
  return lines.join('\n').trim();
}

// 组装最终剪贴板内容:[风格 prompt] + 【时间范围】标注 + md。styleId 为空则只给纯 md。
function buildClipboardText(rangeId, styleId) {
  const range = findRange(rangeId);
  const style = findStyle(styleId);
  const md = assembleRangeMd(range.days);
  if (!md) return { text: null, range, style };
  const body = `【时间范围:${range.label}】\n\n${md}`;
  const text = style ? `${style.text}\n\n---\n\n${body}` : body;
  return { text, range, style };
}

function clipboardTextForCopyMode(text, mode) {
  if (mode !== 'visible') return text;
  const status = state.recordSource === 'partial' && state.todayResolved
    ? '【数据状态：今天已同步；所选范围的历史记录仍在后台核对】'
    : '【数据状态：当前显示的是上次完整记录；今天最新内容仍在后台核对】';
  return `${status}\n\n${text}`;
}

async function copyCombo() {
  const btn = document.getElementById('copy-btn');
  const label = btn.querySelector('.btn-label');
  const restore = () => updateCtaLabel();

  let copyMode = selectedRangeCopyMode();
  if (copyMode === 'blocked') {
    updateCtaLabel();
    return;
  }
  const context = captureActiveDirectoryContext();
  if (!context || !await ensureCopyPermission(context)) return;
  if (!directoryContextStillCurrent(context)) return;
  copyMode = selectedRangeCopyMode();
  if (copyMode === 'blocked') return;

  const { text, range, style } = buildClipboardText(state.selectedRange, state.selectedStyle);
  if (!text) {
    label.textContent = range.days <= 1 ? '今天还没记任何东西' : `${range.label}没有任何记录`;
    setTimeout(restore, 1800);
    return;
  }

  try {
    if (!directoryContextStillCurrent(context)) return;
    await navigator.clipboard.writeText(clipboardTextForCopyMode(text, copyMode));
    if (!directoryContextStillCurrent(context)) return;
    label.textContent = copyMode === 'visible'
      ? '✓ 已复制当前显示内容 · ⌘V 粘到 AI'
      : style
        ? `✓ ${range.label} · ${style.label} · ⌘V 粘到 AI`
        : `✓ ${range.label} · ⌘V 粘到 AI`;
    setTimeout(restore, 2200);
  } catch (err) {
    console.error(err);
    label.textContent = '复制失败,请重试';
    setTimeout(restore, 1800);
  }
}

function bindCopyButton() {
  const btn = document.getElementById('copy-btn');
  btn.onclick = copyCombo;
  updateCtaLabel();
}

function captureActiveDirectoryContext() {
  const session = activeCoreLoad;
  if (!session
      || !directoryLoadGate.isCurrent(session.generation)
      || session.selectionEpoch !== selectionEpoch
      || state.dirHandle !== session.handle) return null;
  return {
    session,
    generation: session.generation,
    selectionEpoch,
    handle: session.handle,
  };
}

function directoryContextStillCurrent(context) {
  return Boolean(context
    && activeCoreLoad === context.session
    && directoryLoadGate.isCurrent(context.generation)
    && selectionEpoch === context.selectionEpoch
    && state.dirHandle === context.handle);
}

async function ensureCopyPermission(context) {
  if (!directoryContextStillCurrent(context)) return false;
  try {
    const access = window.MementoDirectoryAccess;
    const storedHandle = access && access.withTimeout
      ? await access.withTimeout(loadHandle, STORAGE_OPERATION_TIMEOUT_MS, '确认当前数据目录')
      : await loadHandle();
    if (!directoryContextStillCurrent(context)) return false;
    const matchesCurrentSelection = Boolean(storedHandle
      && await context.handle.isSameEntry(storedHandle));
    if (!directoryContextStillCurrent(context)) return false;
    if (!matchesCurrentSelection) {
      retireActiveCoreLoad();
      if (!storedHandle) {
        showAccessResult({ kind: 'missing' });
      } else {
        showPersistedSelectionChanged(storedHandle);
      }
      return false;
    }

    const permission = await queryRead(context.handle);
    if (!directoryContextStillCurrent(context)) return false;
    if (permission === 'granted') {
      // A different tab can commit a new directory between the first identity
      // check and the permission continuation. Re-read once at the final copy
      // boundary so a not-yet-delivered BroadcastChannel task cannot leak the
      // previous directory into the clipboard.
      const latestStoredHandle = access && access.withTimeout
        ? await access.withTimeout(loadHandle, STORAGE_OPERATION_TIMEOUT_MS, '再次确认当前数据目录')
        : await loadHandle();
      if (!directoryContextStillCurrent(context)) return false;
      const stillSelected = Boolean(latestStoredHandle
        && await context.handle.isSameEntry(latestStoredHandle));
      if (!directoryContextStillCurrent(context)) return false;
      if (!stillSelected) {
        retireActiveCoreLoad();
        if (latestStoredHandle) showPersistedSelectionChanged(latestStoredHandle);
        else showAccessResult({ kind: 'missing' });
        return false;
      }
      return true;
    }
    rememberedDirectoryHandle = context.handle;
    retireActiveCoreLoad();
    setRegrantUI(permission, context.handle, context.session.contextPromise);
  } catch (error) {
    console.warn('复制前无法确认当前目录与权限', error);
    if (directoryContextStillCurrent(context)) {
      retireActiveCoreLoad();
      showAccessResult({ kind: 'permission-check-error', handle: context.handle, error });
    }
  }
  return false;
}

// ----- Easter egg (记忆卡片 · 彩蛋,也吃 A 时间段) -----

function bindEasterEgg() {
  const btn = document.getElementById('easter-egg');
  if (!btn) return;
  const style = findStyle('card');
  btn.title = style ? 'Memento 模式 · 5 张记忆卡片' : '';
  btn.onclick = copyEasterEgg;
}

async function copyEasterEgg() {
  const btn = document.getElementById('easter-egg');
  const photo = btn.querySelector('.egg-photo');
  const orig = photo.textContent;
  const reset = () => photo.textContent = orig;

  let copyMode = selectedRangeCopyMode();
  if (!findStyle('card') || copyMode === 'blocked') return;
  const context = captureActiveDirectoryContext();
  if (!context || !await ensureCopyPermission(context)) return;
  if (!directoryContextStillCurrent(context)) return;
  copyMode = selectedRangeCopyMode();
  if (copyMode === 'blocked') return;

  // 彩蛋复用当前选中的时间段(本周/本月的卡片更有回忆价值)
  const { text } = buildClipboardText(state.selectedRange, 'card');
  if (!text) {
    photo.textContent = '?';
    setTimeout(reset, 1500);
    return;
  }

  try {
    if (!directoryContextStillCurrent(context)) return;
    await navigator.clipboard.writeText(clipboardTextForCopyMode(text, copyMode));
    if (!directoryContextStillCurrent(context)) return;
    photo.textContent = '✓';
    btn.classList.add('flashed');
    setTimeout(() => { reset(); btn.classList.remove('flashed'); }, 2000);
  } catch (err) {
    console.error(err);
    photo.textContent = '!';
    setTimeout(reset, 1500);
  }
}

// =============================================================
// 5.5 右侧抽屉框架
// =============================================================

let activeDrawerId = null;
let lastDrawerTrigger = null;
let drawerShellInited = false;

function initDrawerShell() {
  if (drawerShellInited) return;
  drawerShellInited = true;

  document.getElementById('drawer-scrim').addEventListener('click', closeSideDrawers);
  document.addEventListener('keydown', (event) => {
    if (!activeDrawerId) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeSideDrawers();
      return;
    }
    if (event.key !== 'Tab') return;

    const drawer = document.getElementById(activeDrawerId);
    const focusable = [...drawer.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [href], [tabindex]:not([tabindex="-1"])')]
      .filter(el => !el.hidden && el.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!drawer.contains(document.activeElement)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
      return;
    }
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
}

function openSideDrawer(drawerId, triggerId) {
  initDrawerShell();
  if (activeDrawerId && activeDrawerId !== drawerId) closeSideDrawers(false);

  activeDrawerId = drawerId;
  lastDrawerTrigger = document.getElementById(triggerId);
  document.querySelectorAll('.side-drawer').forEach(drawer => {
    const isOpen = drawer.id === drawerId;
    drawer.classList.toggle('open', isOpen);
    drawer.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
  });
  document.querySelectorAll('.edge-tab').forEach(tab => {
    tab.setAttribute('aria-expanded', tab.id === triggerId ? 'true' : 'false');
  });
  document.getElementById('drawer-scrim').classList.add('open');
  document.body.classList.add('drawer-open');
  document.getElementById('app').inert = true;

  requestAnimationFrame(() => {
    const drawer = document.getElementById(drawerId);
    if (activeDrawerId !== drawerId || !drawer.classList.contains('open')) return;
    drawer.querySelector('.drawer-close')?.focus();
  });
}

function closeSideDrawers(restoreFocus = true) {
  const closingDrawerId = activeDrawerId;
  document.querySelectorAll('.side-drawer').forEach(drawer => {
    drawer.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
  });
  document.querySelectorAll('.edge-tab').forEach(tab => tab.setAttribute('aria-expanded', 'false'));
  setCognitiveSecondaryExpanded();
  document.getElementById('drawer-scrim').classList.remove('open');
  document.body.classList.remove('drawer-open');
  document.getElementById('app').inert = false;
  activeDrawerId = null;

  if (closingDrawerId === 'archive-drawer') archiveRenderGeneration++;
  if (closingDrawerId === 'daily-summary-drawer') cancelPhotoRender();
  if (restoreFocus) lastDrawerTrigger?.focus();
  if (restoreFocus) lastDrawerTrigger = null;
}

// =============================================================
// 5.6 HTML 归档库 (右侧抽屉)
//     真实文件存 ~/AISecretary/.archives/*.html
//     看列表只用只读权限;上传/删除时才懒升级到读写
// =============================================================

const ARCHIVE_SUBDIR = '.archives';
const ARCHIVE_READ_CONCURRENCY = 3;
const ARCHIVE_TITLE_SCAN_BYTES = 256 * 1024;
const ARCHIVE_CACHE_DECISION_MS = 120;
let archivesInited = false;
let archiveRenderGeneration = 0;
const enqueueArchiveMutation = window.MementoDashboardOperations.createSerialQueue();
const archiveIndexState = {
  session: null,
  items: [],
  ready: false,
  source: 'none',
  liveVerified: false,
  refreshPromise: null,
  refreshId: 0,
  refreshMutationEpoch: -1,
  mutationEpoch: 0,
  cacheContext: null,
  cacheHydrationPromise: null,
};

function resetArchiveIndexState() {
  archiveIndexState.session = null;
  archiveIndexState.items = [];
  archiveIndexState.ready = false;
  archiveIndexState.source = 'none';
  archiveIndexState.liveVerified = false;
  archiveIndexState.refreshPromise = null;
  archiveIndexState.refreshId = 0;
  archiveIndexState.refreshMutationEpoch = -1;
  archiveIndexState.mutationEpoch = 0;
  archiveIndexState.cacheContext = null;
  archiveIndexState.cacheHydrationPromise = null;
}

function archiveReadContextStillCurrent(context) {
  return Boolean(context && directoryContextStillCurrent(context));
}

function ensureArchiveIndexSession(context) {
  if (!archiveReadContextStillCurrent(context)) return false;
  if (archiveIndexState.session === context.session) return true;
  resetArchiveIndexState();
  archiveIndexState.session = context.session;
  return true;
}

function normalizedArchiveItems(items) {
  return (Array.isArray(items) ? items : [])
    .filter(item => item
      && typeof item.name === 'string'
      && !/[\\/\0]/.test(item.name)
      && /\.html?$/i.test(item.name))
    .map(item => ({
      name: item.name,
      title: typeof item.title === 'string' && item.title.trim()
        ? item.title.trim()
        : item.name.replace(/\.html?$/i, ''),
      mtime: Number.isSafeInteger(item.mtime) && item.mtime >= 0 ? item.mtime : 0,
      ...(item.handle ? { handle: item.handle } : {}),
    }))
    .sort((left, right) => right.mtime - left.mtime || right.name.localeCompare(left.name));
}

function installArchiveIndexItems(context, items, source, { liveVerified = false } = {}) {
  if (!ensureArchiveIndexSession(context)) return false;
  // A slow IndexedDB result may arrive after live enumeration has already
  // established the current directory contents. It may fill an empty state,
  // but it must never roll a live/partial list back to stale names.
  if (source === 'cache' && archiveIndexState.source !== 'none') return false;
  archiveIndexState.items = normalizedArchiveItems(items);
  archiveIndexState.ready = true;
  archiveIndexState.source = source;
  archiveIndexState.liveVerified = Boolean(liveVerified);
  updateArchiveIndexView();
  return true;
}

function primeArchiveIndexFromActiveSession() {
  const context = captureActiveDirectoryContext();
  if (!context || !ensureArchiveIndexSession(context)) return false;
  if (archiveIndexState.ready) return true;

  const cached = context.session.bootstrapArchiveIndex;
  if (!cached || !Array.isArray(cached.items)) return false;
  return installArchiveIndexItems(context, cached.items, 'cache');
}

function updateArchiveIndexItem(context, item) {
  if (!ensureArchiveIndexSession(context) || !item || !item.name) return false;
  const items = archiveIndexState.items.filter(current => current.name !== item.name);
  items.push(item);
  archiveIndexState.items = normalizedArchiveItems(items);
  archiveIndexState.ready = true;
  archiveIndexState.source = 'partial';
  updateArchiveIndexView();
  return true;
}

function applyArchiveIndexMutation(context, updateItems) {
  if (!ensureArchiveIndexSession(context) || typeof updateItems !== 'function') return false;
  archiveIndexState.mutationEpoch += 1;
  archiveIndexState.items = normalizedArchiveItems(updateItems([...archiveIndexState.items]));
  archiveIndexState.ready = true;
  archiveIndexState.source = 'partial';
  archiveIndexState.liveVerified = false;
  updateArchiveIndexView();
  persistArchiveIndex(context);
  return true;
}

async function resolveArchiveIndexCacheContext(context) {
  if (!dashboardCacheRepository || !archiveReadContextStillCurrent(context)) return null;
  if (archiveIndexState.session === context.session && archiveIndexState.cacheContext) {
    return archiveIndexState.cacheContext;
  }
  const session = context.session;
  const cacheContext = session.cacheContextReady
    ? session.cacheContext
    : await session.contextPromise;
  if (!archiveReadContextStillCurrent(context)
      || !cacheContext
      || !cacheContext.binding) return null;
  if (ensureArchiveIndexSession(context)) archiveIndexState.cacheContext = cacheContext;
  return cacheContext;
}

async function hydrateArchiveIndexCache(context) {
  if (!ensureArchiveIndexSession(context)) return false;
  if (archiveIndexState.ready) return true;
  if (archiveIndexState.cacheHydrationPromise) return archiveIndexState.cacheHydrationPromise;
  if (!dashboardCacheRepository) return false;
  const session = context.session;
  const hydrationPromise = (async () => {
    try {
      const cacheContext = await resolveArchiveIndexCacheContext(context);
      if (!cacheContext || !archiveReadContextStillCurrent(context)) return false;
      // The archive index is co-read with the core snapshot in the one startup
      // IndexedDB transaction. Reuse that result here; opening the drawer must
      // not launch a second metadata lookup before the live verification.
      session.bootstrapArchiveIndex = cacheContext.archiveIndex || null;
      if (!session.bootstrapArchiveIndex || !archiveReadContextStillCurrent(context)) return false;
      return installArchiveIndexItems(context, session.bootstrapArchiveIndex.items, 'cache');
    } catch (error) {
      console.warn('归档快速缓存不可用，将直接读取本地目录', error);
      return false;
    } finally {
      if (archiveIndexState.session === session
          && archiveIndexState.cacheHydrationPromise === hydrationPromise) {
        archiveIndexState.cacheHydrationPromise = null;
      }
    }
  })();
  archiveIndexState.cacheHydrationPromise = hydrationPromise;
  return hydrationPromise;
}

async function waitForArchiveIndexCache(hydrationPromise) {
  const access = window.MementoDirectoryAccess;
  if (!access || typeof access.withTimeout !== 'function') return hydrationPromise;
  try {
    return await access.withTimeout(
      () => hydrationPromise,
      ARCHIVE_CACHE_DECISION_MS,
      '等待归档列表缓存'
    );
  } catch (error) {
    if (error && error.name === 'TimeoutError') return false;
    throw error;
  }
}

function persistArchiveIndex(context) {
  if (!dashboardCacheRepository
      || typeof dashboardCacheRepository.commitArchiveIndex !== 'function'
      || !archiveReadContextStillCurrent(context)) return;
  const items = archiveIndexState.items.map(({ name, title, mtime }) => ({ name, title, mtime }));
  void resolveArchiveIndexCacheContext(context)
    .then(cacheContext => cacheContext
      ? dashboardCacheRepository.commitArchiveIndex(cacheContext.binding.token, items)
      : null
    )
    .catch(error => console.warn('归档列表缓存保存失败，下次将继续实时读取', error));
}

function setArchiveStatus(message = '', isError = false) {
  const status = document.getElementById('archive-status');
  status.textContent = message;
  status.classList.toggle('is-error', Boolean(message) && isError);
}

function archiveErrorMessage(error, action = '读取') {
  const kind = window.MementoDashboardOperations.errorKind(error);
  if (kind === 'permission') return `归档${action}失败：数据目录权限已失效，请刷新页面后重新允许访问。`;
  if (kind === 'missing') return `归档${action}失败：数据目录或归档文件已移动。`;
  return `归档${action}失败：${shortError(error)}`;
}

async function runArchiveAction(task, action) {
  try {
    return await task();
  } catch (error) {
    console.error(`归档${action}失败`, error);
    setArchiveStatus(archiveErrorMessage(error, action), true);
    return null;
  }
}

function archiveMutationStillCurrent(context) {
  return Boolean(context
    && context.selectionEpoch === selectionEpoch
    && context.handle
    && state.dirHandle === context.handle);
}

async function archiveContextMatchesPersisted(context) {
  if (!archiveMutationStillCurrent(context)) return false;
  const access = window.MementoDirectoryAccess;
  const storedHandle = access && access.withTimeout
    ? await access.withTimeout(loadHandle, STORAGE_OPERATION_TIMEOUT_MS, '确认归档数据目录')
    : await loadHandle();
  if (!archiveMutationStillCurrent(context) || !storedHandle) return false;
  const matches = await context.handle.isSameEntry(storedHandle);
  return Boolean(archiveMutationStillCurrent(context) && matches);
}

function reconcileArchiveSelectionMismatch(context) {
  if (!archiveMutationStillCurrent(context)) return;
  setArchiveStatus('数据目录已在另一页面切换，归档操作已取消。', true);
  void reloadPersistedSelectionAfterBroadcast()
    .catch(error => console.warn('无法同步归档所用的数据目录', error));
}

function runArchiveMutation(task, action) {
  const context = { selectionEpoch, handle: state.dirHandle };
  return enqueueArchiveMutation(() => {
    if (!archiveMutationStillCurrent(context)) return null;
    return runArchiveAction(() => task(context), action);
  });
}

function withArchiveMutationLock(task) {
  // Directory selection commits and archive writes share this cross-tab
  // critical section. Ordinary reads deliberately do not use the lock.
  return window.MementoDashboardOperations.withArchiveMutationLock(navigator.locks, task);
}

async function ensureWritePermission(h = state.dirHandle) {
  if (!h) return false;
  if (await h.queryPermission({ mode: 'readwrite' }) === 'granted') return true;
  return (await h.requestPermission({ mode: 'readwrite' })) === 'granted';
}

// =============================================================
// 5.6 Context Agent (只读候选，用户确认后才写入)
//     模型调用在本地 CLI 中完成；浏览器不持有 API 密钥。
// =============================================================

const CONTEXT_AGENT_CANDIDATE_PATH = ['.context-agent', 'candidates'];
const CONTEXT_AGENT_DECISION_PATH = ['.context-agent', 'decisions'];
const SELF_REFLECTION_REQUEST_PATH = ['.context-agent', 'self-queries', 'requests'];
const SELF_REFLECTION_RESPONSE_PATH = ['.context-agent', 'self-queries', 'responses'];
const SELF_REFLECTION_FEEDBACK_PATH = ['.context-agent', 'self-queries', 'feedback'];
const REMEMBER_AGENT_ROOT_PATH = ['.context-agent', 'agent-v1'];
const REMEMBER_AGENT_REQUEST_PATH = [...REMEMBER_AGENT_ROOT_PATH, 'requests'];
const REMEMBER_AGENT_RESPONSE_PATH = [...REMEMBER_AGENT_ROOT_PATH, 'responses'];
const REMEMBER_AGENT_RUN_PATH = [...REMEMBER_AGENT_ROOT_PATH, 'runs'];
const REMEMBER_AGENT_USER_ACTION_PATH = [...REMEMBER_AGENT_ROOT_PATH, 'user-actions'];
const REMEMBER_AGENT_MEMORY_PATH = [...REMEMBER_AGENT_ROOT_PATH, 'memories'];
const REMEMBER_AGENT_ENABLE_GATE_NAME = 'enabled';
const REMEMBER_AGENT_SCHEDULE_NAME = 'schedule.json';
// This browser-visible value is an advisory UX gate, refreshed from the exact
// file bytes. File System Access cannot verify POSIX owner/mode/link count, so
// the local Worker remains the final security boundary before any Agent run.
let rememberAgentV1Enabled = false;
const CONTEXT_CONFIRMED_PATH = ['Context', 'Confirmed'];
const enqueueContextAgentMutation = window.MementoDashboardOperations.createSerialQueue();
let contextAgentInited = false;
let contextAgentReadId = 0;
let contextAgentMutating = false;
let selfReflectionMutating = false;
let rememberAgentMutating = false;
let selfReflectionPollTimer = null;
let rememberAgentPollTimer = null;
const selfReflectionHiddenInsights = new Set();
const rememberAgentHiddenMemories = new Set();
let contextAgentState = {
  loaded: false,
  candidate: null,
  confirmed: [],
  oneTimePack: '',
  reflectionRequest: null,
  reflectionResponse: null,
  reflectionProfileResponse: null,
  reflectionResponseSeen: false,
  reflectionResponseHash: '',
  reflectionFeedback: [],
  reflectionHistory: [],
  agentProfile: null,
  agentProfileState: 'missing',
  agentProfileAuthoritative: false,
  agentMemories: [],
  agentRequest: null,
  agentResponse: null,
  agentResponseSeen: false,
  agentRuns: [],
  agentUserActions: [],
  agentSchedule: null,
  agentScheduleState: 'absent',
  issue: '',
};

function contextAgentLibrary() {
  const library = window.MementoContextAgent;
  if (!library) throw new Error('Context Agent 数据模块未加载');
  return library;
}

function rememberAgentLibrary() {
  const library = window.MementoRememberAgentV1;
  if (!library) throw new Error('Re:member Agent V1 数据模块未加载');
  return library;
}

async function nestedDirectory(root, path, create = false) {
  let directory = root;
  for (const name of path) {
    try {
      directory = await directory.getDirectoryHandle(name, { create });
    } catch (error) {
      if (!create && error && error.name === 'NotFoundError') return null;
      throw error;
    }
  }
  return directory;
}

async function readContextJsonDirectory(root, path, options = {}) {
  const directory = await nestedDirectory(root, path, false);
  if (!directory) return { records: [], issues: [] };

  const records = [];
  const issues = [];
  for await (const [name, handle] of directory.entries()) {
    if (!name.endsWith('.json') || name.startsWith('.') || handle.kind !== 'file') continue;
    try {
      const file = await handle.getFile();
      const bytes = await file.arrayBuffer();
      const rawText = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
      const value = JSON.parse(rawText);
      const compact = options.canonicalHash
        ? rememberAgentLibrary().compactSortedJsonText(rawText)
        : '';
      records.push({
        name,
        fallbackId: name.replace(/\.json$/i, ''),
        value,
        sha256: await sha256Hex(bytes),
        canonicalSha256: compact
          ? await sha256Hex(new TextEncoder().encode(compact))
          : '',
      });
    } catch (error) {
      issues.push({ name, error });
    }
  }
  return { records, issues };
}

async function readContextJsonFile(root, path, name) {
  const directory = await nestedDirectory(root, path, false);
  if (!directory) return { record: null, exists: false, issues: [] };
  try {
    const handle = await directory.getFileHandle(name);
    const file = await handle.getFile();
    const bytes = await file.arrayBuffer();
    const rawText = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    const value = JSON.parse(rawText);
    return {
      exists: true,
      issues: [],
      record: {
        name,
        fallbackId: name.replace(/\.json$/i, ''),
        value,
        sha256: await sha256Hex(bytes),
      },
    };
  } catch (error) {
    if (error && error.name === 'NotFoundError') {
      return { record: null, exists: false, issues: [] };
    }
    return { record: null, exists: true, issues: [{ name, error }] };
  }
}

async function readRememberAgentV1EnableGate(root) {
  try {
    const directory = await nestedDirectory(root, REMEMBER_AGENT_ROOT_PATH, false);
    if (!directory) return false;
    const handle = await directory.getFileHandle(REMEMBER_AGENT_ENABLE_GATE_NAME);
    if (handle.kind !== 'file') return false;
    const file = await handle.getFile();
    const bytes = new Uint8Array(await file.arrayBuffer());
    return rememberAgentLibrary().isAgentEnableGateBytes(bytes);
  } catch (error) {
    // Missing, unreadable, or invalid gates all fail closed to Self Reflection.
    return false;
  }
}

function contextSourceFiles(records) {
  const files = new Set();
  const collect = record => {
    if (!record || typeof record !== 'object') return;
    const hashes = record.sourceHashes || record.source_hashes;
    if (Array.isArray(hashes)) {
      hashes.forEach(item => {
        if (item && typeof item.file === 'string') files.add(item.file);
      });
    }
    const evidence = [...(record.evidence || []), ...(record.counterevidence || [])];
    evidence.forEach(item => {
      if (item && typeof item.file === 'string') files.add(item.file);
    });
    if (record.memory) collect(record.memory);
    if (Array.isArray(record.memories)) record.memories.forEach(collect);
  };
  for (const record of records) {
    collect(record);
  }
  return [...files].sort();
}

async function readContextSourceBacking(root, records) {
  const sources = new Map();
  const issues = [];
  await Promise.all(contextSourceFiles(records).map(async name => {
    try {
      const handle = await root.getFileHandle(name);
      const file = await handle.getFile();
      const bytes = await file.arrayBuffer();
      const decoded = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
      sources.set(name, {
        sha256: await sha256Hex(bytes),
        lines: decoded.split(/\r\n?|\n/),
      });
    } catch (error) {
      issues.push({ name, error });
    }
  }));
  return { sources, issues };
}

function verifiedContextRecords(records, sources, library) {
  const valid = [];
  const invalid = [];
  for (const record of records) {
    const result = library.verifySourceBacking(record, sources);
    if (result.valid) valid.push(record);
    else invalid.push({ record, result });
  }
  return { valid, invalid };
}

function setContextAgentStatus(message = '', tone = '') {
  const element = document.getElementById('context-status');
  element.textContent = message;
  element.classList.toggle('is-error', tone === 'error');
  element.classList.toggle('is-success', tone === 'success');
}

function contextCategoryLabel(category) {
  return ({
    project_decision: '项目决策',
    constraint: '约束',
    work_preference: '工作偏好',
  })[category] || category || 'Context';
}

function contextEvidenceMarkup(evidence) {
  if (!evidence.length) return '';
  return `
    <p class="context-evidence-title">来自你记录的证据</p>
    <ul class="context-evidence">
      ${evidence.map(item => {
        const source = [item.file || item.date, item.line ? `第 ${item.line} 行` : '']
          .filter(Boolean)
          .join(' · ');
        return `<li>“${escapeHtml(item.quote)}”${source
          ? `<span class="context-evidence-source">${escapeHtml(source)}</span>`
          : ''}</li>`;
      }).join('')}
    </ul>`;
}

function contextCandidateMarkup(candidate) {
  if (!candidate) {
    return `
      <section class="context-section">
        <h3 class="context-section-title">待确认理解</h3>
        <div class="context-empty">现在没有待确认的理解。<br>新候选会在本地 Agent 运行后出现。</div>
      </section>`;
  }

  const whyNow = candidate.whyNow || '模型未提供生成原因。';
  const recovery = candidate.recoveryContext || null;
  const recoveryLabel = recovery && ({
    confirm: '恢复“是的”决策',
    scope: '恢复限定范围决策',
    edit: '恢复修改后的决策',
  })[recovery.decisionAction];
  const actions = recovery ? `
    <p class="context-confirmed-summary">长期 Context 已写入，但上次未完成决策记录。恢复时会沿用原确认时间和内容。</p>
    <div class="context-actions" aria-label="恢复这条已确认理解">
      <button type="button" class="context-action is-primary" data-context-action="${escapeHtml(recovery.decisionAction)}">${escapeHtml(recoveryLabel || '恢复决策记录')}</button>
    </div>` : `
    <div class="context-actions" aria-label="回应这条候选理解">
      <button type="button" class="context-action is-primary" data-context-action="confirm">是的</button>
      <button type="button" class="context-action" data-context-action="just_once">只是这次</button>
      <button type="button" class="context-action" data-context-action="scope">限定范围</button>
      <button type="button" class="context-action" data-context-action="edit">改一下</button>
      <button type="button" class="context-action is-reject" data-context-action="reject">不要记住</button>
    </div>
    <form class="context-form" data-context-form="scope" hidden>
      <label for="context-scope-input">这条理解只在什么范围内生效？</label>
      <input id="context-scope-input" name="scope" value="${escapeHtml(candidate.scope)}" required maxlength="160">
      <button type="submit" class="context-form-submit">确认范围并记住</button>
    </form>
    <form class="context-form" data-context-form="edit" hidden>
      <label for="context-statement-input">修改成你认可的表述</label>
      <textarea id="context-statement-input" name="statement" rows="4" required maxlength="400">${escapeHtml(candidate.statement)}</textarea>
      <button type="submit" class="context-form-submit">确认修改并记住</button>
    </form>`;
  return `
    <section class="context-section">
      <h3 class="context-section-title">待确认理解</h3>
      <article class="context-candidate" data-context-candidate="${escapeHtml(candidate.id)}">
        <span class="context-candidate-badge">${recovery ? '已确认 · 待恢复决策' : '候选 · 尚未记住'}</span>
        <p class="context-statement">${escapeHtml(candidate.statement)}</p>
        <dl class="context-meta">
          <dt>范围</dt><dd>${escapeHtml(candidate.scope)}</dd>
          <dt>类别</dt><dd>${escapeHtml(contextCategoryLabel(candidate.category))}</dd>
          <dt>不确定性</dt><dd>${candidate.uncertainty === 'low' ? '低' : '中'}</dd>
          <dt>为什么</dt><dd>${escapeHtml(whyNow)}</dd>
        </dl>
        ${contextEvidenceMarkup(candidate.evidence)}
        ${actions}
      </article>
    </section>`;
}

function contextConfirmedMarkup(confirmed, oneTimePack) {
  const visible = confirmed.slice(0, 5);
  const list = visible.length ? `
    <ul class="context-confirmed-list">
      ${visible.map(item => `
        <li>
          <strong>${escapeHtml(item.statement)}</strong>
          <span>${escapeHtml(item.scope)} · ${escapeHtml(contextCategoryLabel(item.category))}</span>
        </li>`).join('')}
    </ul>` : '';
  const oneTime = oneTimePack ? `
    <section class="context-section context-one-time">
      <h3 class="context-section-title">单次 Context Pack</h3>
      <p class="context-confirmed-summary">这份 Context 只用于当前一次任务，未进入长期 Context。</p>
      <pre class="context-pack-preview">${escapeHtml(oneTimePack)}</pre>
      <div class="context-pack-actions">
        <button type="button" class="context-pack-action" data-context-pack="copy-once">复制单次 Context Pack</button>
      </div>
    </section>` : '';
  return `${oneTime}
    <section class="context-section">
      <h3 class="context-section-title">已确认 Context</h3>
      <p class="context-confirmed-summary">${confirmed.length
        ? `已有 ${confirmed.length} 条有效 Context。Context Pack 只包含这些经你确认的内容。`
        : '还没有长期 Context。“只是这次”和“不要记住”不会进入这里。'}</p>
      ${list}
      ${confirmed.length > visible.length
        ? `<p class="context-confirmed-summary">另有 ${confirmed.length - visible.length} 条已纳入 Context Pack。</p>`
        : ''}
      <div class="context-pack-actions">
        <button type="button" class="context-pack-action" data-context-pack="generate"${confirmed.length ? '' : ' disabled'}>生成 Context Pack</button>
        <button type="button" class="context-pack-action" data-context-pack="copy"${confirmed.length ? '' : ' disabled'}>复制 Context Pack</button>
      </div>
      <pre id="context-pack-preview" class="context-pack-preview" hidden></pre>
    </section>`;
}

function rememberAgentRequestPending() {
  const request = contextAgentState.agentRequest;
  return Boolean(request && !contextAgentState.agentResponseSeen);
}

function rememberAgentScheduleEnabled() {
  return Boolean(rememberAgentV1Enabled
    && contextAgentState.agentSchedule
    && contextAgentState.agentSchedule.enabled);
}

function rememberAgentNextScheduleLabel(nowValue = new Date()) {
  const now = nowValue instanceof Date ? nowValue : new Date(nowValue);
  if (Number.isNaN(now.getTime())) return '下一次 21:00';
  const next = new Date(now);
  next.setHours(21, 0, 0, 0);
  let dayLabel = '今天';
  if (next.getTime() <= now.getTime()) {
    next.setDate(next.getDate() + 1);
    dayLabel = '明天';
  }
  return `${dayLabel} 21:00`;
}

function rememberAgentLocalDateLabel(value, fallback = '尚无更新时间') {
  if (typeof value !== 'string' || !value) return fallback;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return fallback;
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function rememberAgentControlMarkup() {
  const waiting = rememberAgentRequestPending();
  const scheduleEnabled = rememberAgentScheduleEnabled();
  const runDisabled = !rememberAgentV1Enabled || rememberAgentMutating || waiting;
  const scheduleInvalid = contextAgentState.agentScheduleState === 'invalid';
  const scheduleDisabled = !rememberAgentV1Enabled || rememberAgentMutating || scheduleInvalid;
  let scheduleState = '自动整理已关闭';
  let scheduleDetail = '开启后，每天 21:00 检查一次近期记录';
  if (!rememberAgentV1Enabled) {
    scheduleState = '整理能力当前关闭';
    scheduleDetail = '自动整理不会运行，现有理解仍可阅读';
  } else if (scheduleInvalid) {
    scheduleState = '自动计划需要本地修复';
    scheduleDetail = 'schedule.json 未通过校验，当前按关闭处理';
  } else if (scheduleEnabled) {
    scheduleState = '21:00 自动计划已保存';
    scheduleDetail = `下一计划：${rememberAgentNextScheduleLabel()}`;
  }
  return `
    <section class="context-run-panel" aria-label="整理方式">
      <button class="context-run-now" type="button" data-agent-v1-run${runDisabled ? ' disabled' : ''}>
        <span>${waiting ? '正在整理近期记录…' : '现在整理'}</span>
        <small>${waiting ? '完成前继续保留当前理解' : '立即检查最近 14 天'}</small>
      </button>
      <label class="context-schedule-control${scheduleEnabled ? ' is-enabled' : ''}${scheduleDisabled ? ' is-disabled' : ''}">
        <input type="checkbox" data-agent-schedule-toggle
               aria-label="每天 21:00 自动整理"
               ${scheduleEnabled ? 'checked' : ''}${scheduleDisabled ? ' disabled' : ''}>
        <span class="context-schedule-switch" aria-hidden="true"><span></span></span>
        <span class="context-schedule-copy">
          <strong>${escapeHtml(scheduleState)}</strong>
          <small>${escapeHtml(scheduleDetail)}</small>
        </span>
      </label>
    </section>`;
}

function rememberAgentPersonaHeroMarkup(memoryCount, updated) {
  const recentChanges = cognitiveHomeState.home?.summary?.recent_change_count || 0;
  return `
    <header class="context-persona-hero context-understanding-hero">
      <div class="context-kicker">LONG-TERM UNDERSTANDING · COGNITIVE SECRETARY</div>
      <h3 id="context-insight-title">她理解的我</h3>
      <p class="context-persona-intro">这里尝试把能回到来源版本的多个倾向，收束成少数更深的理解。地景保留它们的短主题。</p>
      <dl class="context-persona-facts">
        <div><dt>当前理解</dt><dd>${memoryCount} 条</dd></div>
        <div><dt>近期变化</dt><dd>${recentChanges} 项</dd></div>
        <div><dt>版本日期</dt><dd>${escapeHtml(updated)}</dd></div>
      </dl>
    </header>`;
}

function rememberAgentFirstInsightMarkup() {
  const waiting = rememberAgentRequestPending();
  const response = contextAgentState.agentResponse;
  let stateNotice = '';
  if (waiting) {
    stateNotice = `
      <div class="context-first-insight-state" aria-live="polite">
        <span class="context-thinking-mark" aria-hidden="true"></span>
        正在核对近期记录…
      </div>`;
  } else if (contextAgentState.agentRequest && !response
      && contextAgentState.agentResponseSeen) {
    stateNotice = '<p class="context-first-insight-error">上一次结果没有通过本地合同与来源校验，未进入这份理解。</p>';
  } else if (response && ['error', 'stale', 'budget_exhausted'].includes(response.status)) {
    stateNotice = `<p class="context-first-insight-error">上一次核对没有形成可用结果：${escapeHtml(response.error || response.status)}。</p>`;
  } else if (response?.status === 'insufficient_evidence') {
    stateNotice = '<p class="context-first-insight-note">近期证据还不足以形成一段可保留的理解。</p>';
  }
  return `
    <section class="context-insight-reading context-insight-reading-empty" aria-labelledby="context-insight-title">
      ${rememberAgentPersonaHeroMarkup(0, '尚未形成')}
      <div class="context-first-insight" aria-labelledby="remember-agent-first-title">
        <div class="context-kicker">理解的起点</div>
        <h4 id="remember-agent-first-title">还没有形成足够有依据的理解</h4>
        <p>逐条记录会先形成回执；日级归并达到长期证据门后，才会形成第一条理解。</p>
        ${stateNotice}
        <p class="context-first-insight-disclosure">你可以回到主页使用“归并今天”，也可以等待本地 21:00 计划。打开本列表不会调用模型，也不会改写原始日记。</p>
      </div>
    </section>`;
}

function rememberAgentEvidenceMarkup(memory) {
  if (!memory.evidence.length && !memory.counterevidence.length) {
    return '<p class="context-evidence-context-note">这段来自旧版已校验理解的迁移投影，当前没有可展示的逐行记录依据。</p>';
  }
  const evidence = memory.evidence.map(item => `
    <li>“${escapeHtml(item.quote)}”
      <span class="context-evidence-source">${escapeHtml(item.file)} · 第 ${item.line} 行</span>
    </li>`).join('');
  const counterevidence = memory.counterevidence.map(item => `
    <li class="context-counter-evidence">反例：“${escapeHtml(item.quote)}”
      <span class="context-evidence-source">${escapeHtml(item.file)} · 第 ${item.line} 行</span>
    </li>`).join('');
  return `<div class="context-insight-clues"><ul class="context-evidence">${evidence}${counterevidence}</ul></div>`;
}

function rememberAgentMemoryById(memoryId) {
  return contextAgentState.agentMemories.find(memory => memory.memoryId === memoryId) || null;
}

const REMEMBER_AGENT_PROVENANCE_LABELS = Object.freeze({
  legacy_projection: '来自旧版已校验理解',
  pending_user_edit: '正在应用你的修改',
  new: '近期新形成',
  reinforce: '近期再次得到支持',
  revise: '近期已修订',
  tension: '近期发现张力',
  user_edit: '由你修改',
});

function rememberAgentMemoryMetaMarkup(memory) {
  const uncertainty = memory.uncertainty === 'low' ? '不确定性较低' : '仍需继续观察';
  const provenance = REMEMBER_AGENT_PROVENANCE_LABELS[memory.provenance.operation]
    || '保留于本地记忆';
  const created = rememberAgentLocalDateLabel(memory.createdAt, '');
  return `
    <div class="context-memory-meta" aria-label="这条理解的状态">
      <span>${escapeHtml(uncertainty)}</span>
      <span>${escapeHtml(provenance)}</span>
      ${created ? `<time datetime="${escapeHtml(memory.createdAt)}">${escapeHtml(created)}</time>` : ''}
    </div>`;
}

function rememberAgentMemoryMarkup(memory) {
  const evidenceParts = [];
  if (memory.evidence.length) evidenceParts.push(`${memory.evidence.length} 条依据`);
  if (memory.counterevidence.length) evidenceParts.push(`${memory.counterevidence.length} 条反例`);
  const evidenceLabel = evidenceParts.length ? `查看 ${evidenceParts.join(' · ')}` : '查看依据边界';
  const changing = ['change', 'tension'].includes(memory.insightKind);
  const stateClass = ` is-${escapeHtml(memory.insightKind)}`;
  const revisionNote = memory.pendingUserAction
    || memory.provenance.operation === 'pending_user_edit'
    ? '<span>你的修改正在保存</span>'
    : changing ? '<span>这项理解包含变化或张力</span>' : '';
  const canManage = rememberAgentV1Enabled
    && contextAgentState.agentProfileAuthoritative;
  const manageMenu = canManage ? `
    <details class="context-memory-menu">
      <summary aria-label="管理这条理解">···</summary>
      <div class="context-memory-menu-popover" role="group" aria-label="管理这条理解">
        <button type="button" data-agent-memory-action="edit" data-memory-id="${escapeHtml(memory.memoryId)}">编辑</button>
        <button class="is-negative" type="button" data-agent-memory-action="delete" data-memory-id="${escapeHtml(memory.memoryId)}">删除</button>
      </div>
    </details>` : '';
  return `
    <article class="context-memory-card${stateClass}" data-agent-memory-id="${escapeHtml(memory.memoryId)}">
      <header class="context-memory-card-head">
        <div class="context-memory-heading">
          <span class="context-memory-scope">${escapeHtml(memory.scope)}</span>
          <h5>${escapeHtml(memory.title)}</h5>
        </div>
        ${manageMenu}
      </header>
      <p class="context-insight-prose">${escapeHtml(memory.statement)}</p>
      ${rememberAgentMemoryMetaMarkup(memory)}
      ${revisionNote ? `<div class="context-memory-state">${revisionNote}</div>` : ''}
      <footer class="context-memory-card-footer">
        <details class="context-evidence-details">
          <summary>${escapeHtml(evidenceLabel)}</summary>
          <div class="context-evidence-body">${rememberAgentEvidenceMarkup(memory)}</div>
        </details>
      </footer>
      ${canManage ? `<form class="context-feedback-editor" data-agent-memory-form="${escapeHtml(memory.memoryId)}" hidden>
        <label>把这段理解改成你的说法</label>
        <textarea name="statement" rows="3" maxlength="400" required>${escapeHtml(memory.statement)}</textarea>
        <input name="scope" type="hidden" value="${escapeHtml(memory.scope)}">
        <button type="submit">保存修改</button>
      </form>` : ''}
    </article>`;
}

const REMEMBER_AGENT_MEMORY_GROUPS = Object.freeze([
  {
    key: 'observation',
    title: '当前理解',
    description: '已提交并通过当前来源校验的稳定理解。',
    kinds: ['observation', 'confirmed'],
  },
  {
    key: 'change',
    title: '近期修订',
    description: '近期记录使表述、范围或证据发生了可核对的变化。',
    kinds: ['change'],
  },
  {
    key: 'tension',
    title: '张力与反例',
    description: '当前版本同时保留着支持证据、反例或尚未收束的边界。',
    kinds: ['tension'],
  },
]);

function rememberAgentMemoryGroupsMarkup(memories) {
  return REMEMBER_AGENT_MEMORY_GROUPS.map(group => {
    const groupMemories = memories.filter(memory => group.kinds.includes(memory.insightKind));
    if (!groupMemories.length) return '';
    return `
      <section class="context-memory-group is-${group.key}" data-memory-group="${group.key}"
               aria-labelledby="context-memory-group-${group.key}">
        <header class="context-memory-group-head">
          <div><span>${String(groupMemories.length).padStart(2, '0')}</span></div>
          <div>
            <h4 id="context-memory-group-${group.key}">${escapeHtml(group.title)}</h4>
            <p>${escapeHtml(group.description)}</p>
          </div>
        </header>
        <div class="context-memory-list">${groupMemories.map(rememberAgentMemoryMarkup).join('')}</div>
      </section>`;
  }).join('');
}

function rememberAgentUpdateNoticeMarkup() {
  const modeLabel = contextAgentState.agentProfileAuthoritative ? '来源已校验' : '当前只读';
  const modeClass = rememberAgentV1Enabled ? ' is-enabled' : '';
  let tone = '';
  let title = '当前版本已校验';
  let detail = '列表只显示通过当前 profile 与来源绑定校验的长期理解。';
  if (contextAgentState.agentProfile && !contextAgentState.agentProfileAuthoritative) {
    tone = ' is-error';
    title = '当前只读显示上一版理解';
    detail = '新结果没有通过完整校验，修改和删除已暂停。';
  } else {
    const request = contextAgentState.agentRequest;
    const response = contextAgentState.agentResponse;
    if (rememberAgentV1Enabled
        && request && !response && !contextAgentState.agentResponseSeen) {
      tone = ' is-pending';
      title = '正在核对近期记录';
      detail = '完成前继续保留当前理解。';
    } else if (rememberAgentV1Enabled
        && request && !response && contextAgentState.agentResponseSeen) {
      tone = ' is-error';
      title = '新结果未通过校验';
      detail = '当前理解没有被覆盖。';
    } else if (response && ['error', 'stale', 'budget_exhausted'].includes(response.status)) {
      tone = ' is-error';
      title = '本次核对没有完成';
      detail = '当前理解没有被覆盖。';
    } else if (response?.status === 'insufficient_evidence') {
      title = '没有形成可提交的更新';
      detail = '本轮可用证据不足，当前版本保持不变。';
    } else if (response?.status === 'no_change') {
      title = '当前版本保持不变';
      detail = '本轮没有发现需要提交的实质变化。';
    } else if (response?.status === 'updated') {
      title = '长期理解已更新';
      detail = '新版本已经通过本地来源和写入校验。';
    } else if (!contextAgentState.agentProfile
        && contextAgentState.reflectionProfileResponse) {
      title = '当前展示迁移前的理解';
      detail = '重新核对后，会建立可修改、可删除的新版本。';
    }
  }
  return `
    <section class="context-product-status${tone}" aria-label="当前整理状态">
      <div class="context-product-status-copy">
        <span class="context-product-status-mark" aria-hidden="true"></span>
        <div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail)}</p></div>
      </div>
      <span class="context-mode-badge${modeClass}">${escapeHtml(modeLabel)}</span>
    </section>`;
}

const REMEMBER_AGENT_ACTION_LABELS = Object.freeze({
  investigate: '判断候选并发起取证',
  read_memory: '查阅已有理解',
  search_history: '核对历史证据',
  finalize_patch: '提交一项记忆修订',
  finish: '结束本次核对',
  invalid_action: '拒绝无效动作',
});

const REMEMBER_AGENT_REASON_LABELS = Object.freeze({
  plan_evidence: '选择候选与取证计划',
  inspect_existing: '检查现有记忆',
  need_history_evidence: '需要更多历史依据',
  check_counterevidence: '查找反例',
  evidence_sufficient: '证据达到写入门槛',
  no_material_change: '没有实质变化',
  insufficient_evidence: '证据不足',
});

function rememberAgentTraceMarkup() {
  const run = contextAgentState.agentProfile?.latestRun;
  if (!run) return '<p>还没有可展示的整理记录。</p>';
  const steps = run.actions.map((action, index) => `
    <li><span>${escapeHtml(REMEMBER_AGENT_ACTION_LABELS[action] || action)}</span>
      <small>${escapeHtml(REMEMBER_AGENT_REASON_LABELS[run.reasonCodes[index]] || run.reasonCodes[index])}</small>
    </li>`).join('');
  const usage = run.usage;
  const cost = usage.cost_usd === null ? '未提供' : `$${usage.cost_usd.toFixed(6)}`;
  const execution = run.cacheHit && usage.model_calls === 0
    ? '本次仅使用本地已有结果'
    : `本次使用智能整理 ${usage.model_calls} 次`;
  return `
    <div class="context-agent-trace" data-agent-latest-run="${escapeHtml(run.runId)}">
      <h4>最近一次整理记录</h4>
      <p>${escapeHtml(execution)} · ${run.modelTurns} 轮判断 · ${run.toolCalls} 个受控步骤 · 命中 ${run.historyMatches} 条历史依据</p>
      ${steps ? `<ol>${steps}</ol>` : '<p>本次没有进入模型动作循环。</p>'}
      <p class="context-agent-usage">用量 ${usage.total_tokens} tokens · 成本 ${escapeHtml(cost)}${usage.usage_missing ? ' · 用量信息不完整' : ''}</p>
    </div>`;
}

function rememberAgentHistoryDetailsMarkup() {
  const homeSchedule = cognitiveHomeState.home?.schedule;
  const scheduleCopy = homeSchedule?.enabled
    ? `本地日级计划为 ${String(homeSchedule.hour).padStart(2, '0')}:${String(homeSchedule.minute).padStart(2, '0')}；打开和阅读页面不会调用模型。`
    : '本地日级计划当前关闭；打开和阅读页面不会调用模型。';
  const readonlyNote = rememberAgentV1Enabled
    ? ''
    : '<p class="context-product-readonly-note">长期理解写入能力当前关闭。现有内容仍可阅读，编辑和删除暂不可用。</p>';
  const dataQualityNote = contextAgentState.issue
    ? `<p class="context-product-data-note">${escapeHtml(contextAgentState.issue)}</p>`
    : '';
  return `
    <details class="context-footer-details context-product-about">
      <summary>形成与控制边界</summary>
      <div class="context-footer-details-body">
        <h4>它们如何形成</h4>
        <p>这些理解来自已经逐条整理并进入正式日级归并的记录；每条依据仍绑定当时的来源版本。</p>
        <h4>你的控制权</h4>
        <p>每条理解都有独立版本。你的修改、删除、变化和反例会留在本地记录中；原始日记不会被改写。</p>
        <h4>日级更新方式</h4>
        <p>${escapeHtml(scheduleCopy)}</p>
        ${dataQualityNote}
        ${readonlyNote}
        <details class="context-diagnostic-details">
          <summary>整理详情与用量</summary>
          ${rememberAgentTraceMarkup()}
        </details>
      </div>
    </details>`;
}

function rememberAgentArticleMarkup() {
  const profile = contextAgentState.agentProfile;
  const visibleMemories = contextAgentState.agentMemories
    .filter(memory => !rememberAgentHiddenMemories.has(memory.memoryId));
  const article = rememberAgentMemoryGroupsMarkup(visibleMemories);
  const updated = rememberAgentLocalDateLabel(profile.projectionUpdatedAt);
  return `
    <section class="context-insight-reading" aria-labelledby="context-insight-title">
      ${rememberAgentPersonaHeroMarkup(visibleMemories.length, updated)}
      ${rememberAgentUpdateNoticeMarkup()}
      ${article
        ? `<div class="context-insight-article">${article}</div>`
        : '<div class="context-empty context-all-removed">目前没有保留中的理解。<br>原始记录没有被删除。</div>'}
      <footer class="context-insight-footer">
        ${rememberAgentHistoryDetailsMarkup()}
      </footer>
    </section>`;
}

function legacyInsightArticleMarkup() {
  const response = contextAgentState.reflectionProfileResponse;
  const visibleInsights = visibleSelfReflectionInsights();
  const paragraphs = visibleInsights.map(tag => `
    <article class="context-insight-paragraph" data-legacy-reflection-tag="${escapeHtml(tag.tagId)}">
      <p class="context-insight-prose">${escapeHtml(tag.displayStatement)}</p>
      <div class="context-insight-paragraph-meta"><span>适用于：${escapeHtml(tag.displayScope)}</span></div>
      <details class="context-evidence-details">
        <summary>查看迁移前依据</summary>
        <div class="context-evidence-body">${selfReflectionEvidenceMarkup(tag)}</div>
      </details>
    </article>`).join('');
  return `
    <section class="context-insight-reading" aria-labelledby="context-insight-title">
      ${rememberAgentPersonaHeroMarkup(visibleInsights.length, response.asOf)}
      ${rememberAgentUpdateNoticeMarkup()}
      <div class="context-insight-article">${paragraphs}</div>
      <footer class="context-insight-footer">
        ${rememberAgentHistoryDetailsMarkup()}
      </footer>
    </section>`;
}

function compatibilityUnderstandingDetailsMarkup() {
  return `
    <details class="context-footer-details context-product-about">
      <summary>关于这些理解</summary>
      <div class="context-footer-details-body">
        <h4>当前状态</h4>
        <p>这些是此前通过本地来源校验后保留的只读理解。打开和阅读不会调用模型，也不会修改原始日记。</p>
        <h4>以后如何更新</h4>
        <p>重新开启手动整理后，新版本会继续保留逐行依据，并允许你编辑或删除单条理解。</p>
      </div>
    </details>`;
}

function selfReflectionRcArticleMarkup() {
  const response = contextAgentState.reflectionProfileResponse;
  const visibleInsights = visibleSelfReflectionInsights();
  const article = visibleInsights
    .map(tag => selfReflectionInsightMarkup(tag, { readOnly: true }))
    .join('');
  return `
    <section class="context-insight-reading" aria-labelledby="context-insight-title">
      ${rememberAgentPersonaHeroMarkup(visibleInsights.length, response.asOf)}
      <section class="context-product-status" aria-label="当前整理状态">
        <div class="context-product-status-copy">
          <span class="context-product-status-mark" aria-hidden="true"></span>
          <div><strong>手动整理已关闭</strong><p>当前以只读方式显示之前保留的理解。</p></div>
        </div>
        <span class="context-mode-badge">只读</span>
      </section>
      ${article
        ? `<div class="context-insight-article">${article}</div>`
        : '<div class="context-empty">目前没有可展示的理解。</div>'}
      <footer class="context-insight-footer">
        ${compatibilityUnderstandingDetailsMarkup()}
      </footer>
    </section>`;
}

function selfReflectionRequestPending() {
  const request = contextAgentState.reflectionRequest;
  return Boolean(request && !contextAgentState.reflectionResponseSeen);
}

function selfReflectionFirstInsightMarkup() {
  const waiting = selfReflectionRequestPending();
  const disabled = selfReflectionMutating || waiting ? ' disabled' : '';
  const terminalResponse = contextAgentState.reflectionResponse;
  let stateNotice = '';
  if (waiting) {
    stateNotice = `
      <div class="context-first-insight-state" aria-live="polite">
        <span class="context-thinking-mark" aria-hidden="true"></span>
        正在根据近期记录整理第一份理解…
      </div>`;
  } else if (contextAgentState.reflectionRequest && !terminalResponse
      && contextAgentState.reflectionResponseSeen) {
    stateNotice = '<p class="context-first-insight-error">上一次结果没有通过本地校验，未进入这份理解。</p>';
  } else if (terminalResponse?.status === 'error') {
    stateNotice = `<p class="context-first-insight-error">上一次整理没有完成：${escapeHtml(terminalResponse.error)}。</p>`;
  } else if (terminalResponse?.status === 'insufficient_evidence') {
    stateNotice = `<p class="context-first-insight-note">${escapeHtml(terminalResponse.reflection.summary)} 这次没有形成可保留的理解。</p>`;
  }
  return `
    <section class="context-first-insight" aria-labelledby="self-reflection-first-title">
      <div class="context-kicker">理解的起点</div>
      <h3 id="self-reflection-first-title">她还没有形成足够有依据的理解</h3>
      <p>Memento 会把你主动留下的记录，逐步整理成一份可以被你修改和删除的观察。</p>
      ${stateNotice}
      <button class="context-first-insight-action" type="button"
              data-reflection-question="请根据目前可用的记录，形成第一份有证据、有边界的理解。"${disabled}>
        ${waiting ? '正在整理…' : '生成第一份洞察'}
      </button>
      <p class="context-first-insight-disclosure">当前版本会发起一次本地请求：本地 Worker 会把最近 14 个自然日内的可用记录、有效的旧 Context 与历史校准发送给 DeepSeek。这是第一份基于近期记录的理解，不是一次全量历史扫描，也不代表已开启自动 Review。API 密钥只由本地 Worker 读取，浏览器不保存密钥。</p>
    </section>`;
}

function selfReflectionTagProjection() {
  return contextAgentLibrary().buildSelfReflectionTagProjection(contextAgentState.reflectionHistory);
}

function selfReflectionTagById(tagId) {
  return selfReflectionTagProjection().find(tag => tag.tagId === tagId) || null;
}

function visibleSelfReflectionInsights() {
  return selfReflectionTagProjection()
    .filter(tag => !tag.hidden && !selfReflectionHiddenInsights.has(tag.tagId));
}

function selfReflectionEvidenceMarkup(tag) {
  if (!tag.evidence.length && !tag.counterevidence.length) {
    return '<p class="context-evidence-context-note">这段理解引用了旧版已确认 Context。</p>';
  }
  const evidence = tag.evidence.map(item => `
    <li>“${escapeHtml(item.quote)}”
      <span class="context-evidence-source">${escapeHtml(item.file)} · 第 ${item.line} 行</span>
    </li>`).join('');
  const counterevidence = tag.counterevidence.map(item => `
    <li class="context-counter-evidence">反例：“${escapeHtml(item.quote)}”
      <span class="context-evidence-source">${escapeHtml(item.file)} · 第 ${item.line} 行</span>
    </li>`).join('');
  return `
    <div class="context-insight-clues">
      <ul class="context-evidence">${evidence}${counterevidence}</ul>
    </div>`;
}

function selfReflectionInsightMarkup(tag, { readOnly = false } = {}) {
  const evidenceParts = [];
  if (tag.evidence.length) evidenceParts.push(`${tag.evidence.length} 条依据`);
  if (tag.contextRefs.length) evidenceParts.push(`${tag.contextRefs.length} 条旧 Context`);
  if (tag.counterevidence.length) evidenceParts.push(`${tag.counterevidence.length} 条反例`);
  const evidenceLabel = evidenceParts.length ? `查看 ${evidenceParts.join(' · ')}` : '查看依据';
  const stateClass = tag.status === 'changing' ? ' is-changing' : '';
  const revisionNote = tag.editFeedback
    ? '<span>你改过这段</span>'
    : tag.status === 'changing' ? '<span>这项理解正在变化</span>' : '';
  const controls = readOnly ? '' : `
        <div class="context-feedback-actions" aria-label="调整这段理解">
          <button class="context-feedback-action" type="button" data-reflection-feedback="edit" data-tag-id="${escapeHtml(tag.tagId)}">改一句</button>
          <button class="context-feedback-action is-negative" type="button" data-reflection-feedback="delete" data-tag-id="${escapeHtml(tag.tagId)}">删除这段</button>
        </div>`;
  const editor = readOnly ? '' : `
      <form class="context-feedback-editor" data-reflection-feedback-form="${escapeHtml(tag.tagId)}" hidden>
        <label>把这段理解改成你的说法</label>
        <textarea name="note" rows="3" maxlength="400" required>${escapeHtml(tag.displayStatement)}</textarea>
        <input name="action" type="hidden" value="edit">
        <button type="submit">保存修改</button>
      </form>`;
  return `
    <article class="context-insight-paragraph${stateClass}" data-reflection-tag="${escapeHtml(tag.tagId)}">
      <p class="context-insight-prose">${escapeHtml(tag.displayStatement)}</p>
      <div class="context-insight-paragraph-meta">
        ${revisionNote}
        <span>适用于：${escapeHtml(tag.displayScope)}</span>
      </div>
      <div class="context-insight-paragraph-tools">
        <details class="context-evidence-details">
          <summary>${escapeHtml(evidenceLabel)}</summary>
          <div class="context-evidence-body">
        ${selfReflectionEvidenceMarkup(tag)}
          </div>
        </details>
        ${controls}
      </div>
      ${editor}
    </article>`;
}

function selfReflectionUpdateNoticeMarkup() {
  const request = contextAgentState.reflectionRequest;
  const terminalResponse = contextAgentState.reflectionResponse;
  if (request && !terminalResponse && !contextAgentState.reflectionResponseSeen) {
    return '<div class="context-profile-notice">正在根据新请求整理理解；你仍可继续阅读和调整上一版。</div>';
  } else if (request && !terminalResponse && contextAgentState.reflectionResponseSeen) {
    return '<div class="context-profile-notice is-error">新结果未通过本地校验，下面继续保留上一版理解。</div>';
  } else if (terminalResponse?.status === 'error') {
    return `<div class="context-profile-notice is-error">这次整理没有完成：${escapeHtml(terminalResponse.error)}。下面继续保留上一版理解。</div>`;
  } else if (terminalResponse?.status === 'insufficient_evidence') {
    return '<div class="context-profile-notice">这次可用证据不足，没有覆盖已经存在的理解。</div>';
  }
  return '';
}

function contextHistoryDetailsMarkup() {
  const waiting = selfReflectionRequestPending();
  const disabled = selfReflectionMutating || waiting ? ' disabled' : '';
  const versions = contextAgentState.reflectionHistory.slice(0, 12).map(entry => `
    <li>
      <time>${escapeHtml(entry.response.asOf)}</time>
      <p>一次通过本地校验的理解整理</p>
      <span>当时核对 ${entry.response.windowDays} 个自然日 / ${entry.response.recordDays} 个记录日</span>
    </li>`).join('');
  return `
    <details class="context-footer-details">
      <summary>更新与理解边界</summary>
      <div class="context-footer-details-body">
        <p>当前文章由历次通过本地校验的观察累积而成。每次重新核对仍只使用当时最近 14 个自然日的可用记录、有效旧 Context 与历史校准；这不等于某一次请求扫描了全部历史。</p>
        <p>当前版本尚未开启每日自动 Review。下面的按钮会显式发起一次 DeepSeek 调用；仅打开和阅读这份理解不会调用模型。</p>
        <button class="context-recheck-action" type="button"
                data-reflection-question="请根据目前可用的近期记录，重新核对并更新你对我的有证据理解。"${disabled}>
          ${waiting ? '正在核对近期记录…' : '根据近期记录重新核对'}
        </button>
        <h4>历次有效整理</h4>
        <ol class="context-version-list">${versions}</ol>
      </div>
    </details>`;
}

function contextLegacyDetailsMarkup() {
  const legacyCandidate = contextAgentState.candidate
    ? contextCandidateMarkup(contextAgentState.candidate)
    : '';
  return `
    <details class="context-footer-details context-legacy-panel">
      <summary>旧版 Context 与 Pack</summary>
      <div class="context-footer-details-body">
        <p>这些是旧流程的存量记录与 Pack 工具，仍可使用，但不代表「关于我」的全部理解。</p>
        ${legacyCandidate}
        ${contextConfirmedMarkup(contextAgentState.confirmed, contextAgentState.oneTimePack)}
      </div>
    </details>`;
}

function rememberAgentInvalidProfileMarkup() {
  const recovery = rememberAgentV1Enabled
    ? '未校验的内容不会展示。你可以回到主页使用“积累今天”，或等待本地 21:00 计划；新结果仍需通过完整校验后才会显示。'
    : '未校验的内容不会展示。整理能力当前关闭，因此只能查看这个安全状态。';
  return `
    <section class="context-insight-reading context-insight-reading-empty"
             aria-labelledby="context-insight-title">
      ${rememberAgentPersonaHeroMarkup(0, '校验未通过')}
      <section class="context-integrity-error" role="alert">
        <div class="context-kicker">安全状态</div>
        <h4>当前理解暂时无法显示</h4>
        <p>本地投影或来源没有通过完整性校验，现有文件没有被页面改写。</p>
        <p>${escapeHtml(recovery)}</p>
        ${contextAgentState.issue
          ? `<small>${escapeHtml(contextAgentState.issue)}</small>`
          : ''}
      </section>
    </section>`;
}

function contextInsightMarkup() {
  if (contextAgentState.agentProfile) return rememberAgentArticleMarkup();
  if (contextAgentState.agentProfileState === 'invalid') {
    return rememberAgentInvalidProfileMarkup();
  }
  if (!rememberAgentV1Enabled) {
    if (contextAgentState.reflectionProfileResponse) return selfReflectionRcArticleMarkup();
    return `
      <section class="context-insight-reading context-insight-reading-empty" aria-labelledby="context-insight-title">
        ${rememberAgentPersonaHeroMarkup(0, '尚未形成')}
        <div class="context-first-insight">
          <div class="context-kicker">当前理解</div>
          <h4>还没有形成可以保留的理解</h4>
          <p>整理能力目前已关闭。已有记录不会被修改，也不会创建新的整理请求。</p>
        </div>
      </section>`;
  }
  if (contextAgentState.reflectionProfileResponse) return legacyInsightArticleMarkup();
  return rememberAgentFirstInsightMarkup();
}

function bindContextAgentView() {
  document.querySelectorAll('[data-agent-v1-run]').forEach(button => {
    button.disabled = !rememberAgentV1Enabled
      || rememberAgentMutating || rememberAgentRequestPending();
    if (!rememberAgentV1Enabled) return;
    button.addEventListener('click', () => {
      void submitRememberAgentRequest();
    });
  });
  document.querySelectorAll('[data-agent-schedule-toggle]').forEach(input => {
    input.disabled = !rememberAgentV1Enabled
      || rememberAgentMutating
      || contextAgentState.agentScheduleState === 'invalid';
    input.checked = rememberAgentScheduleEnabled();
    if (input.disabled) return;
    input.addEventListener('change', event => {
      const enabled = event.currentTarget.checked;
      // schedule.json 复读成功前，开关继续呈现最后一次已校验状态。
      event.currentTarget.checked = rememberAgentScheduleEnabled();
      void submitRememberAgentSchedule(enabled);
    });
  });
  document.querySelectorAll('[data-agent-memory-action]').forEach(button => {
    const canMutateAgentMemory = rememberAgentV1Enabled
      && contextAgentState.agentProfileAuthoritative;
    button.disabled = !canMutateAgentMemory || rememberAgentMutating;
    if (!canMutateAgentMemory) return;
    const resetDeleteConfirmation = () => {
      if (button.dataset.agentMemoryAction !== 'delete') return;
      delete button.dataset.deleteArmed;
      button.textContent = '删除';
      button.classList.remove('is-armed');
      button.setAttribute('aria-label', '删除这段理解');
    };
    button.addEventListener('click', () => {
      const action = button.dataset.agentMemoryAction;
      const memoryId = button.dataset.memoryId;
      if (action === 'edit') {
        button.closest('.context-memory-menu')?.removeAttribute('open');
        document.querySelectorAll('[data-agent-memory-form]').forEach(form => {
          const matches = form.dataset.agentMemoryForm === memoryId;
          form.hidden = !matches || !form.hidden;
          if (matches && !form.hidden) form.querySelector('textarea')?.focus();
        });
        return;
      }
      if (button.dataset.deleteArmed !== 'true') {
        button.dataset.deleteArmed = 'true';
        button.textContent = '确认删除';
        button.classList.add('is-armed');
        button.setAttribute('aria-label', '确认不可撤销地删除这段理解');
        return;
      }
      button.closest('.context-memory-menu')?.removeAttribute('open');
      void submitRememberAgentUserAction(memoryId, 'delete');
    });
    button.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || button.dataset.deleteArmed !== 'true') return;
      event.preventDefault();
      resetDeleteConfirmation();
    });
    button.addEventListener('blur', () => {
      queueMicrotask(() => {
        if (document.activeElement !== button) resetDeleteConfirmation();
      });
    });
  });
  document.querySelectorAll('[data-agent-memory-form]').forEach(form => {
    if (!rememberAgentV1Enabled || !contextAgentState.agentProfileAuthoritative) return;
    form.addEventListener('submit', event => {
      event.preventDefault();
      void submitRememberAgentUserAction(
        event.currentTarget.dataset.agentMemoryForm,
        'edit',
        event.currentTarget.elements.statement.value,
        event.currentTarget.elements.scope.value
      );
    });
  });
  document.querySelectorAll('[data-reflection-question]').forEach(button => {
    button.addEventListener('click', () => {
      void submitSelfReflectionQuestion(button.dataset.reflectionQuestion);
    });
  });
  document.querySelectorAll('[data-reflection-feedback]').forEach(button => {
    button.disabled = selfReflectionMutating;
    const resetDeleteConfirmation = () => {
      if (button.dataset.reflectionFeedback !== 'delete') return;
      delete button.dataset.deleteArmed;
      button.textContent = '删除这段';
      button.classList.remove('is-armed');
      button.setAttribute('aria-label', '删除这段理解');
    };
    button.addEventListener('click', () => {
      const action = button.dataset.reflectionFeedback;
      const tagId = button.dataset.tagId;
      if (action === 'edit') {
        document.querySelectorAll('[data-reflection-feedback-form]').forEach(form => {
          const matches = form.dataset.reflectionFeedbackForm === tagId;
          form.hidden = !matches || !form.hidden;
          if (matches && !form.hidden) {
            form.elements.action.value = action;
            form.querySelector('textarea')?.focus();
          }
        });
        return;
      }
      if (action === 'delete' && button.dataset.deleteArmed !== 'true') {
        button.dataset.deleteArmed = 'true';
        button.textContent = '确认删除（不可撤销）';
        button.classList.add('is-armed');
        button.setAttribute('aria-label', '确认永久删除这段理解');
        return;
      }
      void submitSelfReflectionFeedback(tagId, action);
    });
    button.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || button.dataset.deleteArmed !== 'true') return;
      event.preventDefault();
      resetDeleteConfirmation();
    });
    button.addEventListener('blur', () => {
      queueMicrotask(() => {
        if (document.activeElement !== button) resetDeleteConfirmation();
      });
    });
  });
  document.querySelectorAll('[data-reflection-feedback-form]').forEach(form => {
    form.addEventListener('submit', event => {
      event.preventDefault();
      const tagId = event.currentTarget.dataset.reflectionFeedbackForm;
      void submitSelfReflectionFeedback(
        tagId,
        event.currentTarget.elements.action.value,
        event.currentTarget.elements.note.value
      );
    });
  });
  document.querySelectorAll('[data-context-action]').forEach(button => {
    button.disabled = contextAgentMutating;
    button.addEventListener('click', () => {
      const action = button.dataset.contextAction;
      if (contextAgentState.candidate?.recoveryContext) {
        void applyContextAgentDecision(action);
        return;
      }
      if (action === 'scope' || action === 'edit') {
        document.querySelectorAll('[data-context-form]').forEach(form => {
          form.hidden = form.dataset.contextForm !== action || !form.hidden;
        });
        const form = document.querySelector(`[data-context-form="${action}"]`);
        if (form && !form.hidden) form.querySelector('input, textarea')?.focus();
        return;
      }
      void applyContextAgentDecision(action);
    });
  });

  document.querySelector('[data-context-form="scope"]')?.addEventListener('submit', event => {
    event.preventDefault();
    const input = event.currentTarget.elements.scope;
    void applyContextAgentDecision('scope', { scope: input.value });
  });
  document.querySelector('[data-context-form="edit"]')?.addEventListener('submit', event => {
    event.preventDefault();
    const input = event.currentTarget.elements.statement;
    void applyContextAgentDecision('edit', { statement: input.value });
  });
  document.querySelector('[data-context-pack="generate"]')?.addEventListener('click', () => {
    void showContextPackPreview();
  });
  document.querySelector('[data-context-pack="copy"]')?.addEventListener('click', () => {
    void copyContextPack();
  });
  document.querySelector('[data-context-pack="copy-once"]')?.addEventListener('click', () => {
    void copyOneTimeContextPack();
  });
}

function renderContextAgentView() {
  const libraryOpen = cognitiveHomeState.selected?.kind === 'library'
    && document.getElementById('cognitive-chain-drawer')?.getAttribute('aria-hidden') === 'false';
  const content = libraryOpen
    ? document.getElementById('cognitive-drawer-body')
    : document.getElementById('context-content');
  if (!content) return;
  if (!contextAgentState.loaded) {
    content.innerHTML = '<div class="context-empty">正在核对本地长期理解…</div>';
    return;
  }
  content.innerHTML = contextInsightMarkup();
  if (libraryOpen) {
    document.getElementById('cognitive-drawer-eyebrow').textContent = '认知秘书 · 当前版本';
    document.getElementById('cognitive-drawer-title').textContent = '她理解的我';
    document.getElementById('cognitive-drawer-foot').textContent = '这里只显示通过当前 profile 与来源绑定校验的长期理解；打开和阅读不会调用模型。';
  }
  bindContextAgentView();

  const count = document.getElementById('context-count');
  count.textContent = '';
  document.getElementById('context-tab').setAttribute('aria-label', '打开她理解的我');
}

async function refreshContextAgentData(options = {}) {
  const context = options.context || captureActiveDirectoryContext();
  if (!context || !directoryContextStillCurrent(context)) return;
  const readId = ++contextAgentReadId;
  if (!contextAgentState.loaded) renderContextAgentView();

  try {
    const [
      agentGateEnabled,
      candidateResult,
      decisionResult,
      confirmedResult,
      reflectionRequestResult,
      reflectionResponseResult,
      reflectionFeedbackResult,
      agentProfileResult,
      agentRequestResult,
      agentResponseResult,
      agentRunResult,
      agentUserActionResult,
      agentMemoryResult,
      agentScheduleResult,
    ] = await Promise.all([
      readRememberAgentV1EnableGate(context.handle),
      readContextJsonDirectory(context.handle, CONTEXT_AGENT_CANDIDATE_PATH),
      readContextJsonDirectory(context.handle, CONTEXT_AGENT_DECISION_PATH),
      readContextJsonDirectory(context.handle, CONTEXT_CONFIRMED_PATH),
      readContextJsonDirectory(context.handle, SELF_REFLECTION_REQUEST_PATH),
      readContextJsonDirectory(context.handle, SELF_REFLECTION_RESPONSE_PATH),
      readContextJsonDirectory(context.handle, SELF_REFLECTION_FEEDBACK_PATH),
      readContextJsonFile(context.handle, REMEMBER_AGENT_ROOT_PATH, 'profile.json'),
      readContextJsonDirectory(context.handle, REMEMBER_AGENT_REQUEST_PATH),
      readContextJsonDirectory(context.handle, REMEMBER_AGENT_RESPONSE_PATH, { canonicalHash: true }),
      readContextJsonDirectory(context.handle, REMEMBER_AGENT_RUN_PATH),
      readContextJsonDirectory(context.handle, REMEMBER_AGENT_USER_ACTION_PATH),
      readContextJsonDirectory(context.handle, REMEMBER_AGENT_MEMORY_PATH),
      readContextJsonFile(context.handle, REMEMBER_AGENT_ROOT_PATH, REMEMBER_AGENT_SCHEDULE_NAME),
    ]);
    if (readId !== contextAgentReadId || !directoryContextStillCurrent(context)) return;
    rememberAgentV1Enabled = agentGateEnabled;

    const library = contextAgentLibrary();
    const normalizedCandidates = candidateResult.records
      .map(record => library.normalizeCandidate(record.value, record.fallbackId))
      .filter(Boolean);
    const decisions = decisionResult.records
      .map(record => library.normalizeDecision(record.value))
      .filter(Boolean);
    const normalizedConfirmed = confirmedResult.records
      .map(record => library.normalizeConfirmedContext(record.value, record.fallbackId))
      .filter(Boolean);
    const normalizedReflectionRequests = reflectionRequestResult.records
      .map(record => ({
        record,
        value: library.normalizeSelfReflectionRequestRecord(record.value, record.fallbackId),
      }))
      .filter(item => item.value);
    const normalizedReflectionResponses = reflectionResponseResult.records
      .map(record => ({
        record,
        value: library.normalizeSelfReflectionResponseRecord(record.value, record.fallbackId),
      }))
      .filter(item => item.value);
    const normalizedReflectionFeedback = reflectionFeedbackResult.records
      .map(record => library.normalizeSelfReflectionFeedbackRecord(record.value, record.fallbackId))
      .filter(Boolean);
    const agentLibrary = rememberAgentLibrary();
    const normalizedAgentSchedule = agentScheduleResult.record
      ? agentLibrary.normalizeSchedule(agentScheduleResult.record.value)
      : null;
    const normalizedAgentProfile = agentProfileResult.record
      ? agentLibrary.normalizeAgentProfile(agentProfileResult.record.value)
      : null;
    const normalizedAgentRequests = agentRequestResult.records
      .map(record => ({
        record,
        value: agentLibrary.normalizeAgentRequestRecord(record.value, record.fallbackId),
      }))
      .filter(item => item.value);
    const normalizedAgentResponses = agentResponseResult.records
      .map(record => ({
        record,
        value: agentLibrary.normalizeAgentResponseRecord(record.value, record.fallbackId),
      }))
      .filter(item => item.value);
    const normalizedAgentRuns = (await Promise.all(agentRunResult.records.map(async record => {
      const value = agentLibrary.normalizeAgentRunRecord(record.value, record.fallbackId);
      if (!value) return null;
      const policyPayloadCandidates = agentLibrary.policyPayloadCandidatesFromRun(value);
      const expectedPolicies = await Promise.all(policyPayloadCandidates.map(payload => (
        sha256Hex(new TextEncoder().encode(agentLibrary.canonicalJson(payload)))
      )));
      return { record, value, policyValid: expectedPolicies.includes(value.policySha256) };
    }))).filter(Boolean);
    const normalizedAgentUserActions = agentUserActionResult.records
      .map(record => ({
        record,
        value: agentLibrary.normalizeUserActionRecord(record.value, record.fallbackId),
      }))
      .filter(item => item.value);
    const agentTombstoneRecords = agentMemoryResult.records
      .filter(record => record.value?.status === 'tombstone');
    const normalizedAgentTombstones = agentTombstoneRecords
      .map(record => agentLibrary.normalizeMemoryTombstoneRecord(
        record.value, record.fallbackId
      ))
      .filter(Boolean);
    const oneTimeDecisions = decisions
      .filter(decision => decision.action === 'just_once')
      .sort((left, right) => String(right.decidedAt).localeCompare(String(left.decidedAt)));
    const sourceResult = await readContextSourceBacking(context.handle, [
      ...normalizedCandidates,
      ...normalizedConfirmed,
      ...oneTimeDecisions.map(decision => decision.oneTimeContext).filter(Boolean),
      ...normalizedReflectionResponses.map(item => item.value),
      ...(normalizedAgentProfile ? [normalizedAgentProfile] : []),
      ...(contextAgentState.agentProfile ? [contextAgentState.agentProfile] : []),
      ...normalizedAgentResponses.map(item => item.value),
    ]);
    if (readId !== contextAgentReadId || !directoryContextStillCurrent(context)) return;

    const candidateVerification = verifiedContextRecords(
      normalizedCandidates,
      sourceResult.sources,
      library
    );
    const confirmedVerification = verifiedContextRecords(
      normalizedConfirmed,
      sourceResult.sources,
      library
    );
    const candidates = candidateVerification.valid;
    const confirmed = library.activeConfirmedContexts(confirmedVerification.valid);
    const reflectionRequests = normalizedReflectionRequests
      .map(item => item.value)
      .sort((left, right) => {
        const order = Date.parse(right.createdAt) - Date.parse(left.createdAt);
        return order || String(right.id).localeCompare(String(left.id));
      });
    const latestReflectionRequest = reflectionRequests[0] || null;
    const latestReflectionResponseSeen = Boolean(latestReflectionRequest
      && reflectionResponseResult.records.some(record =>
        record.fallbackId === latestReflectionRequest.id));
    const reflectionResponseCandidates = normalizedReflectionResponses
      .filter(item => {
        const request = reflectionRequests.find(candidate => candidate.id === item.value.requestId);
        return request
          && request.question === item.value.question
          && request.asOf === item.value.asOf
          && request.windowDays === item.value.windowDays;
      })
      .sort((left, right) => {
        const order = Date.parse(right.value.createdAt) - Date.parse(left.value.createdAt);
        return order || String(right.value.requestId).localeCompare(String(left.value.requestId));
      });
    const confirmedIds = new Set(confirmed.map(item => item.id));
    const verifiedReflectionResponses = reflectionResponseCandidates.filter(item => {
      const response = item.value;
      const verification = library.verifySelfReflectionBacking(response, sourceResult.sources);
      if (!verification.valid) return false;
      if (response.status === 'error') return true;
      const contextRefsCurrent = response.reflection?.insights.every(insight =>
        insight.contextRefs.every(id => confirmedIds.has(id))) ?? true;
      if (!contextRefsCurrent) return false;
      if (!library.selfReflectionConfirmedInsightsMatch(response, confirmed)) return false;
      return response.status !== 'ready' || response.confirmedContexts === confirmed.length;
    });
    const latestReflectionResponseRecord = latestReflectionRequest
      ? verifiedReflectionResponses.find(item => item.value.requestId === latestReflectionRequest.id) || null
      : verifiedReflectionResponses[0] || null;
    const latestProfileResponseRecord = verifiedReflectionResponses
      .find(item => item.value.status === 'ready') || null;
    const reflectionHistory = verifiedReflectionResponses
      .filter(item => item.value.status === 'ready')
      .map(item => ({
        response: item.value,
        responseHash: item.record.sha256,
        feedback: normalizedReflectionFeedback.filter(feedback =>
          feedback.requestId === item.value.requestId
            && feedback.responseSha256 === item.record.sha256),
      }));
    const agentRequests = normalizedAgentRequests.sort((left, right) => {
      const order = Date.parse(right.value.createdAt) - Date.parse(left.value.createdAt);
      return order || right.value.id.localeCompare(left.value.id);
    });
    const latestAgentRequestRecord = agentRequests[0] || null;
    const latestAgentResponseSeen = Boolean(latestAgentRequestRecord
      && agentResponseResult.records.some(record =>
        record.fallbackId === latestAgentRequestRecord.value.id));
    const agentStateRequiresProfile = agentResponseResult.records.length > 0
      || agentRunResult.records.length > 0
      || agentUserActionResult.records.length > 0
      || agentMemoryResult.records.length > 0;
    const agentArtifactVerification = normalizedAgentProfile
      ? agentLibrary.verifyAgentArtifacts({
        profile: normalizedAgentProfile,
        profileRecord: agentProfileResult.record,
        requests: normalizedAgentRequests,
        responses: normalizedAgentResponses,
        runs: normalizedAgentRuns,
        sources: sourceResult.sources,
        userActions: normalizedAgentUserActions.map(item => item.value),
        tombstoneReceipts: normalizedAgentTombstones,
      })
      : {
        valid: false,
        reason: agentProfileResult.exists || agentStateRequiresProfile ? 'profile' : 'missing',
        verifiedResponses: [],
      };
    const agentProfileValid = Boolean(normalizedAgentProfile && agentArtifactVerification.valid);
    const verifiedAgentResponses = agentProfileValid
      ? agentArtifactVerification.verifiedResponses.sort((left, right) => {
        const order = Date.parse(right.value.createdAt) - Date.parse(left.value.createdAt);
        return order || right.value.requestId.localeCompare(left.value.requestId);
      })
      : [];
    const latestAgentResponseRecord = latestAgentRequestRecord
      ? verifiedAgentResponses.find(item =>
        item.value.requestId === latestAgentRequestRecord.value.id) || null
      : verifiedAgentResponses[0] || null;
    const previousAgentProfile = contextAgentState.agentProfile;
    const previousAgentProfileStillCurrent = previousAgentProfile
      ? agentLibrary.verifyProfileEvidence(previousAgentProfile, sourceResult.sources).valid
      : false;
    const usableAgentProfile = agentProfileValid
      ? normalizedAgentProfile
      : previousAgentProfileStillCurrent ? previousAgentProfile : null;
    const projectedAgentMemories = usableAgentProfile
      ? agentLibrary.projectPendingUserActions(
        usableAgentProfile,
        normalizedAgentUserActions.map(item => item.value)
      )
      : [];
    let oneTimePack = '';
    let invalidOneTimeCount = 0;
    for (const decision of oneTimeDecisions) {
      try {
        const verification = library.verifySourceBacking(
          decision.oneTimeContext,
          sourceResult.sources
        );
        if (!verification.valid) {
          invalidOneTimeCount += 1;
          continue;
        }
        oneTimePack = library.buildOneTimeContextPack(decision.oneTimeContext);
        break;
      } catch (error) {
        console.warn('无法从本地决策重建单次 Context Pack', error);
        invalidOneTimeCount += 1;
      }
    }
    const issueCount = candidateResult.issues.length
      + decisionResult.issues.length
      + confirmedResult.issues.length
      + reflectionRequestResult.issues.length
      + reflectionResponseResult.issues.length
      + reflectionFeedbackResult.issues.length
      + agentProfileResult.issues.length
      + agentRequestResult.issues.length
      + agentResponseResult.issues.length
      + agentRunResult.issues.length
      + agentUserActionResult.issues.length
      + agentMemoryResult.issues.length
      + agentScheduleResult.issues.length
      + (candidateResult.records.length - normalizedCandidates.length)
      + (decisionResult.records.length - decisions.length)
      + (confirmedResult.records.length - normalizedConfirmed.length)
      + (reflectionRequestResult.records.length - normalizedReflectionRequests.length)
      + (reflectionResponseResult.records.length - normalizedReflectionResponses.length)
      + (reflectionFeedbackResult.records.length - normalizedReflectionFeedback.length)
      + (agentRequestResult.records.length - normalizedAgentRequests.length)
      + (agentResponseResult.records.length - normalizedAgentResponses.length)
      + (agentRunResult.records.length - normalizedAgentRuns.length)
      + (agentUserActionResult.records.length - normalizedAgentUserActions.length)
      + (agentTombstoneRecords.length - normalizedAgentTombstones.length)
      + (agentScheduleResult.exists && !normalizedAgentSchedule ? 1 : 0)
      + candidateVerification.invalid.length
      + confirmedVerification.invalid.length
      + (reflectionResponseCandidates.length - verifiedReflectionResponses.length)
      + ((agentProfileResult.exists || agentStateRequiresProfile) && !agentProfileValid ? 1 : 0)
      + (normalizedAgentProfile
        ? normalizedAgentResponses.length - verifiedAgentResponses.length
        : 0)
      + normalizedAgentRuns.filter(item => !item.policyValid).length
      + invalidOneTimeCount;
    const pendingCandidate = library.selectPendingCandidate(candidates, decisions, confirmed);
    const recoveryContext = pendingCandidate
      ? confirmed.find(item => item.originalCandidateId === pendingCandidate.id) || null
      : null;

    contextAgentState = {
      loaded: true,
      candidate: recoveryContext ? { ...pendingCandidate, recoveryContext } : pendingCandidate,
      confirmed,
      oneTimePack,
      reflectionRequest: latestReflectionRequest,
      reflectionResponse: latestReflectionResponseRecord?.value || null,
      reflectionProfileResponse: latestProfileResponseRecord?.value || null,
      reflectionResponseSeen: latestReflectionResponseSeen,
      reflectionResponseHash: latestReflectionResponseRecord?.record.sha256 || '',
      reflectionFeedback: latestReflectionResponseRecord
        ? normalizedReflectionFeedback.filter(item => item.requestId === latestReflectionResponseRecord.value.requestId
          && item.responseSha256 === latestReflectionResponseRecord.record.sha256)
        : [],
      reflectionHistory,
      agentProfile: usableAgentProfile,
      agentProfileState: agentProfileValid
        ? 'valid'
        : usableAgentProfile ? 'stale-fallback'
        : agentProfileResult.exists || agentStateRequiresProfile ? 'invalid' : 'missing',
      agentProfileAuthoritative: agentProfileValid,
      agentMemories: projectedAgentMemories,
      agentRequest: latestAgentRequestRecord?.value || null,
      agentResponse: latestAgentResponseRecord?.value || null,
      agentResponseSeen: latestAgentResponseSeen,
      agentRuns: normalizedAgentRuns,
      agentUserActions: normalizedAgentUserActions.map(item => item.value),
      agentSchedule: normalizedAgentSchedule,
      agentScheduleState: normalizedAgentSchedule
        ? 'valid'
        : agentScheduleResult.exists ? 'invalid' : 'absent',
      issue: issueCount ? `有 ${issueCount} 项本地 Re:member 数据未通过合同或来源校验，已跳过。` : '',
    };
    renderContextAgentView();
    if (cognitiveHomeState.candidate) finalizeCognitiveHomeAuthority();
    else if (cognitiveHomeState.status === 'ready') renderCognitiveHome();
    const message = options.errorMessage || options.successMessage || '';
    setContextAgentStatus(
      message,
      options.errorMessage ? 'error' : options.successMessage ? 'success' : ''
    );
    scheduleRememberAgentPoll();
  } catch (error) {
    if (readId !== contextAgentReadId || !directoryContextStillCurrent(context)) return;
    rememberAgentV1Enabled = false;
    console.warn('Context Agent 可选数据读取失败，核心 Dashboard 继续可用', error);
    contextAgentState = {
      loaded: true,
      candidate: null,
      confirmed: [],
      oneTimePack: '',
      reflectionRequest: null,
      reflectionResponse: null,
      reflectionProfileResponse: null,
      reflectionResponseSeen: false,
      reflectionResponseHash: '',
      reflectionFeedback: [],
      reflectionHistory: [],
      agentProfile: null,
      agentProfileState: 'invalid',
      agentProfileAuthoritative: false,
      agentMemories: [],
      agentRequest: null,
      agentResponse: null,
      agentResponseSeen: false,
      agentRuns: [],
      agentUserActions: [],
      agentSchedule: null,
      agentScheduleState: 'invalid',
      issue: shortError(error),
    };
    renderContextAgentView();
    if (cognitiveHomeState.candidate) finalizeCognitiveHomeAuthority();
    else if (cognitiveHomeState.status === 'ready') renderCognitiveHome();
    setContextAgentStatus(`Context 暂时无法读取：${shortError(error)}`, 'error');
  }
}

let selfReflectionPollAttempts = 0;
let rememberAgentPollAttempts = 0;

function newSelfReflectionId(prefix) {
  const bytes = new Uint8Array(12);
  globalThis.crypto.getRandomValues(bytes);
  return `${prefix}_${[...bytes].map(value => value.toString(16).padStart(2, '0')).join('')}`;
}

function scheduleRememberAgentPoll() {
  if (rememberAgentPollTimer) clearTimeout(rememberAgentPollTimer);
  rememberAgentPollTimer = null;
  const request = contextAgentState.agentRequest;
  if (!rememberAgentV1Enabled
      || activeDrawerId !== 'context-drawer'
      || !request
      || contextAgentState.agentResponseSeen
      || rememberAgentPollAttempts >= 120) return;
  rememberAgentPollTimer = setTimeout(() => {
    rememberAgentPollAttempts += 1;
    void refreshContextAgentData();
  }, 2000);
}

async function submitRememberAgentRequest() {
  if (!rememberAgentV1Enabled) {
    setContextAgentStatus('手动整理已关闭，本次没有发起核对。');
    return;
  }
  if (rememberAgentMutating) return;
  if (rememberAgentRequestPending()) {
    setContextAgentStatus('上一次整理还在等待本地 Agent 返回。为避免重复任务，这次没有创建新请求。');
    scheduleRememberAgentPoll();
    return;
  }
  const context = captureActiveDirectoryContext();
  if (!context) return;
  let request;
  try {
    request = rememberAgentLibrary().buildAgentRequest({
      id: newSelfReflectionId('arq'),
      asOf: getLocalDate(),
    });
  } catch (error) {
    setContextAgentStatus(shortError(error), 'error');
    return;
  }

  rememberAgentMutating = true;
  renderContextAgentView();
  setContextAgentStatus('正在保存一次手动 14 天 Agent 核对请求…');
  try {
    if (!(await ensureWritePermission(context.handle))) {
      setContextAgentStatus('未获得读写授权，这次 Agent 请求没有保存。', 'error');
      return;
    }
    if (!directoryContextStillCurrent(context)) return;
    const persisted = await enqueueContextAgentMutation(() => withArchiveMutationLock(async () => {
      if (!directoryContextStillCurrent(context)) return;
      if (!await archiveContextMatchesPersisted(context)) {
        throw new Error('数据目录已在另一页切换，本次核对已取消');
      }
      // Re-read immediately before creating the inbox directory. This closes
      // stale UI state; the Worker separately enforces the POSIX-safe gate.
      if (!await readRememberAgentV1EnableGate(context.handle)) return false;
      const directory = await nestedDirectory(context.handle, REMEMBER_AGENT_REQUEST_PATH, true);
      const fileName = rememberAgentLibrary().requestFileName(request.id);
      await writeContextJsonAtomically(directory, fileName, request);
      return true;
    }));
    if (!directoryContextStillCurrent(context)) return;
    if (!persisted) {
      rememberAgentV1Enabled = false;
      renderContextAgentView();
      setContextAgentStatus('手动整理状态已经变化，本次没有发起核对。');
      return;
    }
    rememberAgentPollAttempts = 0;
    await refreshContextAgentData({
      context,
      successMessage: '核对请求已保存。没有实质变化时，会直接沿用当前理解。',
    });
  } catch (error) {
    if (directoryContextStillCurrent(context)) {
      console.error('Agent V1 请求保存失败', error);
      setContextAgentStatus(`Agent V1 请求保存失败：${shortError(error)}`, 'error');
    }
  } finally {
    rememberAgentMutating = false;
    if (directoryContextStillCurrent(context)) renderContextAgentView();
  }
}

async function submitRememberAgentSchedule(enabled) {
  if (typeof enabled !== 'boolean') return;
  if (!rememberAgentV1Enabled) {
    setContextAgentStatus('整理能力已关闭，自动计划没有修改。');
    return;
  }
  if (rememberAgentMutating) return;
  const context = captureActiveDirectoryContext();
  if (!context) return;
  const expectedScheduleState = contextAgentState.agentScheduleState;
  const expectedSchedule = contextAgentState.agentSchedule;
  if (!['absent', 'valid'].includes(expectedScheduleState)) {
    setContextAgentStatus('schedule.json 需要先在本地修复，页面不会覆盖无效计划。', 'error');
    return;
  }
  let schedule;
  try {
    schedule = rememberAgentLibrary().buildSchedule({ enabled });
  } catch (error) {
    setContextAgentStatus('自动整理计划无效，没有写入。', 'error');
    return;
  }

  rememberAgentMutating = true;
  renderContextAgentView();
  setContextAgentStatus(enabled ? '正在保存 21:00 自动计划…' : '正在关闭自动计划…');
  try {
    if (!(await ensureWritePermission(context.handle))) {
      setContextAgentStatus('未获得读写授权，自动计划没有修改。', 'error');
      return;
    }
    if (!directoryContextStillCurrent(context)) return;
    const persisted = await enqueueContextAgentMutation(() => withArchiveMutationLock(async () => {
      if (!directoryContextStillCurrent(context)) return false;
      if (!await archiveContextMatchesPersisted(context)) {
        throw new Error('数据目录已在另一页切换，自动计划修改已取消');
      }
      // 自动计划只有在同一 mutation lock 内复核主开关后才可替换。
      if (!await readRememberAgentV1EnableGate(context.handle)) return false;
      const scheduleResult = await readContextJsonFile(
        context.handle, REMEMBER_AGENT_ROOT_PATH, REMEMBER_AGENT_SCHEDULE_NAME
      );
      const currentSchedule = scheduleResult.record
        ? rememberAgentLibrary().normalizeSchedule(scheduleResult.record.value)
        : null;
      const currentScheduleState = currentSchedule
        ? 'valid'
        : scheduleResult.exists ? 'invalid' : 'absent';
      if (!['absent', 'valid'].includes(currentScheduleState)) {
        throw new Error('schedule.json 未通过校验，页面已拒绝覆盖，请先在本地修复');
      }
      const scheduleStateUnchanged = currentScheduleState === expectedScheduleState
        && (currentScheduleState === 'absent'
          || (expectedSchedule
            && rememberAgentLibrary().canonicalJson(currentSchedule)
              === rememberAgentLibrary().canonicalJson(expectedSchedule)));
      if (!scheduleStateUnchanged) {
        throw new Error('自动计划已在其他页面或进程中变化，请刷新后再试');
      }
      const directory = await nestedDirectory(context.handle, REMEMBER_AGENT_ROOT_PATH, false);
      if (!directory) return false;
      await writeContextJsonReplacementAtomically(
        directory, REMEMBER_AGENT_SCHEDULE_NAME, schedule
      );
      return true;
    }));
    if (!directoryContextStillCurrent(context)) return;
    if (!persisted) {
      rememberAgentV1Enabled = false;
      renderContextAgentView();
      setContextAgentStatus('整理能力状态已经变化，自动计划没有修改。');
      return;
    }
    await refreshContextAgentData({
      context,
      successMessage: enabled
        ? `21:00 自动计划已保存。下一计划：${rememberAgentNextScheduleLabel()}`
        : '自动整理已关闭；“现在整理”仍可继续使用。',
    });
  } catch (error) {
    if (directoryContextStillCurrent(context)) {
      console.error('Agent V1 自动计划保存失败', error);
      setContextAgentStatus(`自动计划保存失败：${shortError(error)}`, 'error');
    }
  } finally {
    rememberAgentMutating = false;
    if (directoryContextStillCurrent(context)) renderContextAgentView();
  }
}

async function rememberAgentAuthoritativeMemoryStillCurrent(
  context, expectedProfile, expectedMemory
) {
  if (!contextAgentState.agentProfileAuthoritative
      || contextAgentState.agentProfile !== expectedProfile) return false;
  const profileResult = await readContextJsonFile(
    context.handle, REMEMBER_AGENT_ROOT_PATH, 'profile.json'
  );
  const currentProfile = profileResult.record
    ? rememberAgentLibrary().normalizeAgentProfile(profileResult.record.value)
    : null;
  if (!currentProfile
      || rememberAgentLibrary().canonicalJson(currentProfile)
        !== rememberAgentLibrary().canonicalJson(expectedProfile)) return false;
  const sourceResult = await readContextSourceBacking(context.handle, [currentProfile]);
  if (!rememberAgentLibrary().verifyProfileEvidence(
    currentProfile, sourceResult.sources
  ).valid) return false;
  const currentMemory = currentProfile.memories.find(item => (
    item.memoryId === expectedMemory.memoryId
  ));
  return Boolean(currentMemory
    && currentMemory.revision === expectedMemory.revision
    && currentMemory.revisionSha256 === expectedMemory.revisionSha256);
}

async function submitRememberAgentUserAction(
  memoryId, action, statement = null, scope = null
) {
  if (!rememberAgentV1Enabled) {
    setContextAgentStatus('手动整理已关闭，本次没有保存修改。');
    return;
  }
  if (!contextAgentState.agentProfileAuthoritative) {
    setContextAgentStatus('当前只显示上一版只读理解；新投影通过完整校验前不能修改或删除。', 'error');
    return;
  }
  if (rememberAgentMutating) return;
  const memory = rememberAgentMemoryById(memoryId);
  if (!memory) {
    setContextAgentStatus('这段理解已变化，请重新打开后再调整。', 'error');
    return;
  }
  const context = captureActiveDirectoryContext();
  if (!context) return;
  const authoritativeProfile = contextAgentState.agentProfile;
  let userAction;
  try {
    userAction = rememberAgentLibrary().buildUserAction({
      id: newSelfReflectionId('uact'),
      action,
      memoryId: memory.memoryId,
      baseRevision: memory.revision,
      baseRevisionSha256: memory.revisionSha256,
      statement: action === 'edit' ? String(statement || '').trim() : null,
      scope: action === 'edit' ? String(scope || memory.scope).trim() : null,
    });
  } catch (error) {
    setContextAgentStatus('这次调整不符合本地记忆合同，没有写入。', 'error');
    return;
  }

  const optimisticDelete = action === 'delete';
  let persisted = false;
  if (optimisticDelete) rememberAgentHiddenMemories.add(memory.memoryId);
  rememberAgentMutating = true;
  renderContextAgentView();
  setContextAgentStatus(optimisticDelete
    ? '正在保存不可撤销的删除动作…'
    : '正在保存你对这段理解的修改…');
  try {
    if (!(await ensureWritePermission(context.handle))) {
      setContextAgentStatus('未获得读写授权，这次调整没有保存。', 'error');
      return;
    }
    if (!directoryContextStillCurrent(context)) return;
    const wroteUserAction = await enqueueContextAgentMutation(() => withArchiveMutationLock(async () => {
      if (!directoryContextStillCurrent(context)) return;
      if (!await archiveContextMatchesPersisted(context)) {
        throw new Error('数据目录已在另一页切换，本次调整已取消');
      }
      // Do not create user-actions/ until the exact gate bytes are rechecked
      // inside the same Dashboard mutation lock used for the write.
      if (!await readRememberAgentV1EnableGate(context.handle)) return false;
      if (!await rememberAgentAuthoritativeMemoryStillCurrent(
        context, authoritativeProfile, memory
      )) {
        throw new Error('当前理解已经变化，本次调整已取消');
      }
      const directory = await nestedDirectory(context.handle, REMEMBER_AGENT_USER_ACTION_PATH, true);
      const fileName = rememberAgentLibrary().userActionFileName(userAction.id);
      await writeContextJsonAtomically(directory, fileName, userAction);
      return true;
    }));
    if (!directoryContextStillCurrent(context)) return;
    if (!wroteUserAction) {
      rememberAgentV1Enabled = false;
      renderContextAgentView();
      setContextAgentStatus('手动整理状态已经变化，本次没有保存修改。');
      return;
    }
    persisted = true;
    await refreshContextAgentData({
      context,
      successMessage: optimisticDelete
        ? '删除已提交，正在安全保存。完成后不可撤销；原始日记不会被删除。'
        : '修改已提交，正在安全保存。',
    });
  } catch (error) {
    if (directoryContextStillCurrent(context)) {
      console.error('Agent V1 用户动作保存失败', error);
      setContextAgentStatus(`调整保存失败：${shortError(error)}`, 'error');
    }
  } finally {
    if (optimisticDelete && !persisted) rememberAgentHiddenMemories.delete(memory.memoryId);
    rememberAgentMutating = false;
    if (directoryContextStillCurrent(context)) renderContextAgentView();
  }
}

function scheduleSelfReflectionPoll() {
  if (selfReflectionPollTimer) clearTimeout(selfReflectionPollTimer);
  selfReflectionPollTimer = null;
  const request = contextAgentState.reflectionRequest;
  const response = contextAgentState.reflectionResponse;
  if (activeDrawerId !== 'context-drawer'
      || !request
      || contextAgentState.reflectionResponseSeen
      || response?.requestId === request.id
      || selfReflectionPollAttempts >= 120) return;
  selfReflectionPollTimer = setTimeout(() => {
    selfReflectionPollAttempts += 1;
    void refreshContextAgentData();
  }, 2000);
}

async function submitSelfReflectionQuestion(questionValue) {
  if (selfReflectionMutating) return;
  const question = typeof questionValue === 'string' ? questionValue.trim() : '';
  if (!question || question.length > 160) {
    setContextAgentStatus('这次理解整理请求的内容无效。', 'error');
    return;
  }
  if (/[\r\n]/.test(question)
      || contextAgentLibrary().containsSensitiveText({ statement: question })) {
    setContextAgentStatus('当前版本不自动推断情绪、心理、医疗或身份等敏感信息。', 'error');
    return;
  }
  const context = captureActiveDirectoryContext();
  if (!context) return;
  if (selfReflectionRequestPending()) {
    setContextAgentStatus('上一次整理还在等待本地 Agent 返回。为避免重复调用，这次没有创建新请求。');
    scheduleSelfReflectionPoll();
    return;
  }

  let request;
  try {
    request = contextAgentLibrary().buildSelfReflectionRequest({
      id: newSelfReflectionId('srq'),
      question,
      asOf: getLocalDate(),
    });
  } catch (error) {
    setContextAgentStatus(shortError(error), 'error');
    return;
  }

  selfReflectionMutating = true;
  renderContextAgentView();
  setContextAgentStatus('正在保存这次理解整理请求…');
  try {
    if (!(await ensureWritePermission(context.handle))) {
      setContextAgentStatus('未获得读写授权，这次整理请求没有保存。', 'error');
      return;
    }
    if (!directoryContextStillCurrent(context)) return;
    await enqueueContextAgentMutation(() => withArchiveMutationLock(async () => {
      if (!directoryContextStillCurrent(context)) return;
      if (!await archiveContextMatchesPersisted(context)) {
        throw new Error('数据目录已在另一页切换，本次整理已取消');
      }
      const directory = await nestedDirectory(context.handle, SELF_REFLECTION_REQUEST_PATH, true);
      const fileName = contextAgentLibrary().selfReflectionRequestFileName(request.id);
      await writeContextJsonAtomically(directory, fileName, request);
    }));
    if (!directoryContextStillCurrent(context)) return;
    selfReflectionPollAttempts = 0;
    await refreshContextAgentData({
      context,
      successMessage: '已发给本地 Agent。通过校验的结果会累积进当前理解，不会伪装成旧版 Confirmed Context。',
    });
  } catch (error) {
    if (directoryContextStillCurrent(context)) {
      console.error('主动理解请求保存失败', error);
      setContextAgentStatus(`整理请求保存失败：${shortError(error)}`, 'error');
    }
  } finally {
    selfReflectionMutating = false;
    if (directoryContextStillCurrent(context)) renderContextAgentView();
  }
}

async function submitSelfReflectionFeedback(tagId, action, note = '') {
  if (selfReflectionMutating) return;
  const tag = selfReflectionTagById(tagId);
  if (!tag) {
    setContextAgentStatus('这段理解已变化，请刷新后再调整。', 'error');
    return;
  }
  const contractAction = action === 'delete' ? 'reject' : action;
  const optimisticDelete = contractAction === 'reject';
  const insightKey = tag.tagId;
  let persisted = false;
  const response = tag.response;
  const insightIndex = tag.insightIndex;
  if (!response
      || response.status !== 'ready'
      || !response.reflection.insights[insightIndex]
      || !tag.responseHash) {
    setContextAgentStatus('这段理解的来源已变化，请重新核对后再调整。', 'error');
    return;
  }
  const context = captureActiveDirectoryContext();
  if (!context) return;
  if (typeof note === 'string' && note.trim()
      && contextAgentLibrary().containsSensitiveText({ statement: note.trim() })) {
    setContextAgentStatus('这次校准涉及敏感状态或身份推断，不会写入本地理解队列。', 'error');
    return;
  }
  let feedback;
  try {
    feedback = contextAgentLibrary().buildSelfReflectionFeedback({
      id: newSelfReflectionId('srf'),
      requestId: response.requestId,
      insightIndex,
      action: contractAction,
      note,
      responseSha256: tag.responseHash,
    });
  } catch (error) {
    setContextAgentStatus(shortError(error), 'error');
    return;
  }

  if (optimisticDelete) selfReflectionHiddenInsights.add(insightKey);
  selfReflectionMutating = true;
  renderContextAgentView();
  setContextAgentStatus(optimisticDelete ? '正在从当前理解中删除这段…' : '正在保存你的修改…');
  try {
    if (!(await ensureWritePermission(context.handle))) {
      setContextAgentStatus('未获得读写授权，这次校准没有保存。', 'error');
      return;
    }
    if (!directoryContextStillCurrent(context)) return;
    await enqueueContextAgentMutation(() => withArchiveMutationLock(async () => {
      if (!directoryContextStillCurrent(context)) return;
      if (!await archiveContextMatchesPersisted(context)) {
        throw new Error('数据目录已在另一页切换，本次校准已取消');
      }
      const directory = await nestedDirectory(context.handle, SELF_REFLECTION_FEEDBACK_PATH, true);
      const fileName = contextAgentLibrary().selfReflectionFeedbackFileName(feedback.id);
      await writeContextJsonAtomically(directory, fileName, feedback);
    }));
    if (!directoryContextStillCurrent(context)) return;
    persisted = true;
    await refreshContextAgentData({
      context,
      successMessage: optimisticDelete
        ? '已删除这段。删除记录已保存在本地，会阻止相同表述与范围再次进入当前理解；同义改写仍可能重复出现，旧版 Confirmed Context 保持不变。'
        : '已保存修改，这段理解已立即更新。它也会作为本地反馈影响下一次重新核对；旧版 Confirmed Context 保持不变。',
    });
  } catch (error) {
    if (directoryContextStillCurrent(context)) {
      console.error('主动理解校准保存失败', error);
      setContextAgentStatus(`校准保存失败：${shortError(error)}`, 'error');
    }
  } finally {
    if (optimisticDelete && !persisted) selfReflectionHiddenInsights.delete(insightKey);
    selfReflectionMutating = false;
    if (directoryContextStillCurrent(context)) renderContextAgentView();
  }
}

function contextTempName(finalName) {
  const nonce = globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function'
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `.memento-${finalName}-${nonce}.tmp`;
}

function contextJsonEqual(left, right) {
  return contextAgentLibrary().jsonRecordsEqual(left, right);
}

async function existingContextJson(directory, finalName) {
  try {
    const handle = await directory.getFileHandle(finalName);
    const value = JSON.parse(await (await handle.getFile()).text());
    return { exists: true, value };
  } catch (error) {
    if (error && error.name === 'NotFoundError') return { exists: false, value: null };
    throw error;
  }
}

async function assertContextJsonWritable(directory, finalName, value, timing = '') {
  const existing = await existingContextJson(directory, finalName);
  if (existing.exists && !contextJsonEqual(existing.value, value)) {
    const conflict = new Error(timing
      ? `Context 记录 ${finalName} 在${timing}被其他进程写入，已拒绝覆盖`
      : `Context 记录 ${finalName} 已存在且内容不同，已拒绝覆盖`);
    conflict.name = 'ContextConflictError';
    throw conflict;
  }
  return existing;
}

async function writeContextJsonAtomically(directory, finalName, value) {
  const beforeStage = await assertContextJsonWritable(directory, finalName, value);
  if (beforeStage.exists) return { unchanged: true };

  const serialized = `${JSON.stringify(value, null, 2)}\n`;
  const tempName = contextTempName(finalName);
  const tempHandle = await directory.getFileHandle(tempName, { create: true });
  const tempWriter = await tempHandle.createWritable();
  try {
    await tempWriter.write(serialized);
    await tempWriter.close();
  } catch (error) {
    try { await tempWriter.abort(); } catch {}
    throw error;
  }

  const stagedText = await (await tempHandle.getFile()).text();
  JSON.parse(stagedText);

  // 外部 CLI 不受浏览器 Web Lock 约束，因此 temp 完成后再做一次冲突检查。
  const beforeCommit = await assertContextJsonWritable(directory, finalName, value, '保存期间');
  if (beforeCommit.exists) {
    try { await directory.removeEntry(tempName); } catch {}
    return { unchanged: true };
  }

  // Chromium 的普通本地目录不保证支持 FileSystemHandle.move。
  // 支持时直接重命名；否则 createWritable.close() 自身以浏览器临时文件提交，
  // 我们仍先验证完整的显式 temp，再复制到最终文件。
  if (typeof tempHandle.move === 'function') {
    try {
      await tempHandle.move(finalName);
      return { unchanged: false };
    } catch (error) {
      console.warn('Context temp 重命名不可用，改用验证后复制', error);
    }
  }

  const finalHandle = await directory.getFileHandle(finalName, { create: true });
  const finalWriter = await finalHandle.createWritable();
  try {
    await finalWriter.write(stagedText);
    await finalWriter.close();
  } catch (error) {
    try { await finalWriter.abort(); } catch {}
    throw error;
  }
  try {
    await directory.removeEntry(tempName);
  } catch (error) {
    // Worker 会忽略精确的 .memento-*.tmp 名称，但仍将清理失败
    // 告诉用户，避免把本地遗留误报成完全成功。
    console.warn('Context 最终 JSON 已提交，但临时文件清理失败', error);
  }
  return { unchanged: false };
}

async function writeContextJsonReplacementAtomically(directory, finalName, value) {
  const serialized = `${JSON.stringify(value, null, 2)}\n`;
  JSON.parse(serialized);
  const finalHandle = await directory.getFileHandle(finalName, { create: true });
  // Chromium buffers createWritable writes into a temporary backing file and
  // makes them visible on close, so readers see either the old or new JSON.
  const writer = await finalHandle.createWritable();
  try {
    await writer.write(serialized);
    await writer.close();
  } catch (error) {
    try { await writer.abort(); } catch {}
    throw error;
  }
  const committed = JSON.parse(await (await finalHandle.getFile()).text());
  if (!contextJsonEqual(committed, value)) {
    throw new Error(`Context 记录 ${finalName} 写入后校验失败`);
  }
  return { unchanged: false };
}

function contextDecisionSuccessMessage(action) {
  return ({
    confirm: '已确认，这条理解已进入长期 Context。',
    just_once: '已记录为“只是这次”。单次 Context Pack 已显示在下方，未写入长期 Context。',
    scope: '已按你限定的范围写入 Context。',
    edit: '已按你修改后的表述写入 Context。',
    reject: '已记录为“不要记住”，未写入长期 Context。',
  })[action] || '已记录你的回应。';
}

async function applyContextAgentDecision(action, changes = {}) {
  if (contextAgentMutating || !contextAgentState.candidate) return;
  const context = captureActiveDirectoryContext();
  if (!context) return;
  const candidate = contextAgentState.candidate;
  let bundle;
  try {
    bundle = candidate.recoveryContext
      ? contextAgentLibrary().buildRecoveryDecisionBundle(candidate, candidate.recoveryContext)
      : contextAgentLibrary().buildDecisionBundle(candidate, action, changes);
    if (bundle.decision.action !== action) {
      throw new TypeError('恢复操作必须与原已确认决策一致');
    }
  } catch (error) {
    setContextAgentStatus(shortError(error), 'error');
    return;
  }

  contextAgentMutating = true;
  renderContextAgentView();
  setContextAgentStatus('正在保存你的回应…');
  try {
    // 立即由点击链路请求升级权限，避免在串行队列中丢失用户激活。
    if (!(await ensureWritePermission(context.handle))) {
      setContextAgentStatus('未获得读写授权，你的回应没有保存。', 'error');
      return;
    }
    if (!directoryContextStillCurrent(context)) return;

    await enqueueContextAgentMutation(() => withArchiveMutationLock(async () => {
      if (!directoryContextStillCurrent(context)) return;
      if (!await archiveContextMatchesPersisted(context)) {
        throw new Error('数据目录已在另一页切换，本次保存已取消');
      }
      if (!directoryContextStillCurrent(context)) return;

      const currentSources = await readContextSourceBacking(context.handle, [candidate]);
      const sourceVerification = contextAgentLibrary().verifySourceBacking(
        candidate,
        currentSources.sources
      );
      if (!sourceVerification.valid) {
        throw new Error('候选 Context 的原始记录已变化或证据不再匹配，请重新生成候选');
      }
      if (!directoryContextStillCurrent(context)) return;

      const fileName = contextAgentLibrary().recordFileName(candidate.id);
      const decisionDir = await nestedDirectory(context.handle, CONTEXT_AGENT_DECISION_PATH, true);
      const confirmedDir = bundle.confirmedContext
        ? await nestedDirectory(context.handle, CONTEXT_CONFIRMED_PATH, true)
        : null;
      // 在任何最终文件落盘前预检两个目标，避免一半成功后才发现决策冲突。
      await assertContextJsonWritable(decisionDir, fileName, bundle.decision);
      if (bundle.confirmedContext) {
        await assertContextJsonWritable(confirmedDir, fileName, bundle.confirmedContext);
        await writeContextJsonAtomically(confirmedDir, fileName, bundle.confirmedContext);
      }
      await writeContextJsonAtomically(decisionDir, fileName, bundle.decision);
    }));
    if (!directoryContextStillCurrent(context)) return;
    await refreshContextAgentData({
      context,
      successMessage: contextDecisionSuccessMessage(action),
    });
  } catch (error) {
    if (directoryContextStillCurrent(context)) {
      console.error('Context 决策保存失败', error);
      const errorMessage = `保存失败：${shortError(error)}`;
      setContextAgentStatus(errorMessage, 'error');
      void refreshContextAgentData({ context, errorMessage });
    }
  } finally {
    contextAgentMutating = false;
    if (directoryContextStillCurrent(context)) renderContextAgentView();
  }
}

async function showContextPackPreview() {
  const context = captureActiveDirectoryContext();
  if (!context) return '';
  await refreshContextAgentData({ context });
  if (!directoryContextStillCurrent(context) || !contextAgentState.confirmed.length) {
    if (directoryContextStillCurrent(context)) {
      setContextAgentStatus('没有通过当前来源校验的已确认 Context。', 'error');
    }
    return '';
  }
  const preview = document.getElementById('context-pack-preview');
  if (!preview) return '';
  const pack = contextAgentLibrary().buildContextPack(contextAgentState.confirmed);
  preview.textContent = pack;
  preview.hidden = false;
  return pack;
}

async function copyContextPack() {
  try {
    const pack = await showContextPackPreview();
    if (!pack) return;
    await navigator.clipboard.writeText(pack);
    setContextAgentStatus('✓ Context Pack 已复制，可以粘贴给任何 AI。', 'success');
  } catch (error) {
    console.error('Context Pack 复制失败', error);
    setContextAgentStatus(`复制失败：${shortError(error)}`, 'error');
  }
}

async function copyOneTimeContextPack() {
  const context = captureActiveDirectoryContext();
  if (!context) return;
  try {
    await refreshContextAgentData({ context });
    if (!directoryContextStillCurrent(context) || !contextAgentState.oneTimePack) {
      if (directoryContextStillCurrent(context)) {
        setContextAgentStatus('单次 Context 的来源已变化或无法校验，已停止复制。', 'error');
      }
      return;
    }
    await navigator.clipboard.writeText(contextAgentState.oneTimePack);
    setContextAgentStatus('✓ 单次 Context Pack 已复制，未进入长期 Context。', 'success');
  } catch (error) {
    console.error('单次 Context Pack 复制失败', error);
    setContextAgentStatus('复制失败。Pack 仍显示在下方，也可由候选和决策文件重建。', 'error');
  }
}

function openContextAgentDrawer() {
  openSideDrawer('context-drawer', 'context-tab');
  // 每次打开先隐藏上次投影，避免在来源重校验完成前短暂显示 stale Context。
  contextAgentState.loaded = false;
  renderContextAgentView();
  selfReflectionPollAttempts = 0;
  rememberAgentPollAttempts = 0;
  void refreshContextAgentData();
}

function initContextAgent() {
  document.getElementById('context-tab').hidden = false;
  if (!contextAgentInited) {
    contextAgentInited = true;
    document.getElementById('context-tab').addEventListener('click', openContextAgentDrawer);
    document.getElementById('context-drawer-close').addEventListener('click', closeSideDrawers);
  }
  void refreshContextAgentData();
}

function resetContextAgentState() {
  contextAgentReadId += 1;
  rememberAgentV1Enabled = false;
  contextAgentMutating = false;
  selfReflectionMutating = false;
  rememberAgentMutating = false;
  if (selfReflectionPollTimer) clearTimeout(selfReflectionPollTimer);
  selfReflectionPollTimer = null;
  if (rememberAgentPollTimer) clearTimeout(rememberAgentPollTimer);
  rememberAgentPollTimer = null;
  selfReflectionPollAttempts = 0;
  rememberAgentPollAttempts = 0;
  selfReflectionHiddenInsights.clear();
  rememberAgentHiddenMemories.clear();
  contextAgentState = {
    loaded: false,
    candidate: null,
    confirmed: [],
    oneTimePack: '',
    reflectionRequest: null,
    reflectionResponse: null,
    reflectionProfileResponse: null,
    reflectionResponseSeen: false,
    reflectionResponseHash: '',
    reflectionFeedback: [],
    reflectionHistory: [],
    agentProfile: null,
    agentProfileState: 'missing',
    agentProfileAuthoritative: false,
    agentMemories: [],
    agentRequest: null,
    agentResponse: null,
    agentResponseSeen: false,
    agentRuns: [],
    agentUserActions: [],
    agentSchedule: null,
    agentScheduleState: 'absent',
    issue: '',
  };
  document.getElementById('context-count').textContent = '';
  document.getElementById('context-content').innerHTML = '<div class="context-empty">正在读取本地 Context…</div>';
  setContextAgentStatus();
}

function applyCognitiveDemoFixture(fixture) {
  if (!fixture || fixture.mode !== 'synthetic') {
    throw new Error('当前认知数据没有通过完整性校验');
  }
  cognitiveDemoState.active = true;
  cognitiveDemoState.fixture = fixture;
  cognitiveDemoState.rawRecordsById = new Map(Object.entries(fixture.rawRecordsById || {}));
  cognitiveDemoSyncTodayCounts();

  rememberAgentV1Enabled = false;
  contextAgentState = {
    loaded: true,
    candidate: null,
    confirmed: [],
    oneTimePack: '',
    reflectionRequest: null,
    reflectionResponse: null,
    reflectionProfileResponse: null,
    reflectionResponseSeen: false,
    reflectionResponseHash: '',
    reflectionFeedback: [],
    reflectionHistory: [],
    agentProfile: fixture.agentProfile,
    agentProfileState: 'valid',
    agentProfileAuthoritative: true,
    agentMemories: fixture.agentMemories || fixture.agentProfile?.memories || [],
    agentRequest: null,
    agentResponse: null,
    agentResponseSeen: false,
    agentRuns: [],
    agentUserActions: [],
    agentSchedule: null,
    agentScheduleState: 'absent',
    issue: '',
  };

  cognitiveHomeState.status = 'ready';
  cognitiveHomeState.home = fixture.home;
  cognitiveHomeState.landscape = fixture.landscape;
  cognitiveHomeState.landscapeSha256 = fixture.landscapeSha256 || '';
  cognitiveHomeState.recordLocators = new Map();
  cognitiveHomeState.verifiedReceipts = cognitiveDemoRevisionMap(
    fixture.receipts, ['receipt_id', 'receiptId', 'id']
  );
  cognitiveHomeState.verifiedMemories = cognitiveDemoRevisionMap(
    fixture.reusableMemories, ['memory_id', 'memoryId', 'id']
  );
  cognitiveHomeState.verifiedRelations = cognitiveDemoRevisionMap(
    fixture.relations, ['relation_id', 'relationId', 'id']
  );
  cognitiveHomeState.candidate = null;
  cognitiveHomeState.stale = false;
  cognitiveHomeState.issue = '';
  cognitiveHomeState.activeView = 'map';

  const files = fixture.legacyFiles || [];
  const entries = files.flatMap(file => parseFile(file.text, file.date));
  state.files = files;
  state.allEntries = entries;
  state.todayDate = fixture.window?.end || getLocalDate();
  state.todayFileText = files.find(file => file.date === state.todayDate)?.text || '';
  state.todayEntries = entries.filter(entry => entry.date === state.todayDate);
  state.selectedDate = state.todayDate;
  state.selectedRange = getSavedRange();
  state.selectedStyle = getSavedStyle();
  state.dirHandle = null;
  state.snapshots = [];
  state.reviewFiles = [];
  state.reviewStateFiles = [];
  state.reviews = [];
  state.reviewStates = {};
  state.dayCards = [];
  state.recordReadIssues = [];
  state.recordScanIssue = '';
  state.persistenceIssue = '';
  state.recordSource = 'demo';
  state.recordRefreshMessage = '';
  state.todayResolved = true;

  hero.hidden = true;
  grantSection.hidden = true;
  dashboardSection.hidden = false;
  populateSelectors();
  renderDashboard();
  initCognitiveHomeInteractions();
}

function enterCognitiveDemo() {
  const library = cognitiveDemoLibrary();
  if (!library || typeof library.createFixture !== 'function') {
    showGrantUI({
      title: '20 天数据未加载',
      help: '当前页面缺少用于展示认知地景的数据模块。基础记录仍保留在本地。',
      label: '重新检查',
      status: '认知数据模块未启用',
      tone: 'accent',
    });
    return;
  }
  selectionFlowId += 1;
  directoryLoadGate.begin();
  retireActiveCoreLoad();
  quarantineDirectoryActions();
  applyCognitiveDemoFixture(library.createFixture());
}

async function getArchiveDir(create = false, h = state.dirHandle) {
  if (!h) return null;
  try {
    return await h.getDirectoryHandle(ARCHIVE_SUBDIR, { create });
  } catch (error) {
    if (!create && error && error.name === 'NotFoundError') return null;
    throw error;
  }
}

const archiveReadQueue = [];
let archiveReadActive = 0;

function pumpArchiveReadQueue() {
  while (archiveReadActive < ARCHIVE_READ_CONCURRENCY && archiveReadQueue.length) {
    const queued = archiveReadQueue.shift();
    if (!queued.shouldStart()) {
      queued.resolve({ skipped: true });
      continue;
    }
    archiveReadActive++;
    Promise.resolve()
      .then(queued.task)
      .then(queued.resolve, error => queued.resolve({ error }))
      .finally(() => {
        archiveReadActive--;
        pumpArchiveReadQueue();
      });
  }
}

function scheduleArchiveRead(task, shouldStart) {
  return new Promise(resolve => {
    archiveReadQueue.push({ task, shouldStart, resolve });
    pumpArchiveReadQueue();
  });
}

async function enumerateArchiveEntries(context) {
  if (!archiveReadContextStillCurrent(context)) return null;
  const dir = await getArchiveDir(false, context.handle);
  if (!dir) return [];
  const entries = [];
  const iterator = dir.entries()[Symbol.asyncIterator]();
  while (true) {
    if (!archiveReadContextStillCurrent(context)) return null;
    const next = await iterator.next();
    if (!archiveReadContextStillCurrent(context)) return null;
    if (next.done) break;
    const [name, entry] = next.value;
    if (entry.kind === 'file' && /\.html?$/i.test(name)) entries.push({ name, handle: entry });
  }
  return entries;
}

function notifyArchiveItem(callback, item) {
  if (typeof callback !== 'function') return;
  try {
    callback(item);
  } catch (error) {
    console.warn('无法渐进更新归档条目', error);
  }
}

async function readArchiveItems(entries, options = {}) {
  const isCurrent = typeof options.isCurrent === 'function' ? options.isCurrent : () => true;
  const cachedByName = new Map((Array.isArray(options.cachedItems) ? options.cachedItems : [])
    .filter(item => item && item.name)
    .map(item => [item.name, item]));
  let permissionFailure = null;
  const results = await Promise.all(entries.map(item =>
    scheduleArchiveRead(async () => {
      if (permissionFailure || !isCurrent()) return { skipped: true };
      let file = null;
      try {
        file = await item.handle.getFile();
        if (permissionFailure || !isCurrent()) return { skipped: true };
        const mtime = Number(file.lastModified) || 0;
        const cached = cachedByName.get(item.name);
        let title = cached && cached.mtime === mtime ? cached.title : '';
        if (!title) {
          const titleSource = typeof file.slice === 'function'
            ? file.slice(0, ARCHIVE_TITLE_SCAN_BYTES)
            : file;
          const text = await titleSource.text();
          if (permissionFailure || !isCurrent()) return { skipped: true };
          title = extractTitle(text, item.name.replace(/\.html?$/i, ''));
        }
        const resolved = { ...item, mtime, title };
        notifyArchiveItem(options.onItem, resolved);
        return { item: resolved };
      } catch (error) {
        const kind = window.MementoDashboardOperations.errorKind(error);
        if (kind === 'permission') {
          permissionFailure = error;
          return { error, permissionLost: true };
        }
        if (kind !== 'missing') {
          const cached = cachedByName.get(item.name);
          const fallback = {
            ...item,
            mtime: file ? Number(file.lastModified) || 0 : Number(cached && cached.mtime) || 0,
            title: cached && cached.title || item.name.replace(/\.html?$/i, ''),
          };
          notifyArchiveItem(options.onItem, fallback);
          return { item: fallback };
        }
        return { skipped: true };
      }
    }, () => !permissionFailure && isCurrent())
  ));
  if (!isCurrent()) return null;
  if (permissionFailure) throw permissionFailure;
  return results.map(result => result.item).filter(Boolean)
    .sort((a, b) => b.mtime - a.mtime);
}

async function refreshArchiveIndex(context, isRefreshCurrent) {
  const entries = await enumerateArchiveEntries(context);
  if (!entries || !isRefreshCurrent()) return null;

  const cachedByName = new Map(archiveIndexState.items.map(item => [item.name, item]));
  const visibleItems = entries.map(entry => {
    const cached = cachedByName.get(entry.name);
    return {
      ...entry,
      title: cached && cached.title || entry.name.replace(/\.html?$/i, ''),
      mtime: cached && cached.mtime || 0,
    };
  });
  if (!isRefreshCurrent()) return null;
  installArchiveIndexItems(context, visibleItems, 'partial');
  // Persist the useful filename-level result now. A single title read may
  // never settle in Chrome; it must not prevent the next tab from receiving
  // an immediate archive list. The completed pass will replace this index
  // with exact mtimes/titles when available.
  persistArchiveIndex(context);
  // Once filenames are visible, title verification is optional background
  // work. Do not leave the whole drawer looking stuck because one broker call
  // remains pending; an individual unresolved row already says “正在核对”.
  if (activeDrawerId === 'archive-drawer') setArchiveStatus('');

  if (!entries.length) {
    installArchiveIndexItems(context, [], 'live', { liveVerified: true });
    persistArchiveIndex(context);
    return [];
  }

  const items = await readArchiveItems(entries, {
    cachedItems: visibleItems,
    isCurrent: isRefreshCurrent,
    onItem: item => {
      if (isRefreshCurrent()) updateArchiveIndexItem(context, item);
    },
  });
  if (!items || !isRefreshCurrent()) return null;
  installArchiveIndexItems(context, items, 'live', { liveVerified: true });
  if (activeDrawerId === 'archive-drawer') setArchiveStatus('');
  persistArchiveIndex(context);
  return items;
}

function startArchiveIndexRefresh(context, { force = false } = {}) {
  if (!ensureArchiveIndexSession(context)) return null;
  // Reuse one in-flight traversal even after a local mutation. The mutation is
  // already reflected optimistically; spawning another pass could duplicate a
  // Chrome broker request that is itself permanently pending and consume all
  // three read slots.
  if (archiveIndexState.refreshPromise) return archiveIndexState.refreshPromise;
  if (archiveIndexState.liveVerified && !force) return Promise.resolve(archiveIndexState.items);

  archiveIndexState.liveVerified = false;
  const refreshId = archiveIndexState.refreshId + 1;
  const refreshMutationEpoch = archiveIndexState.mutationEpoch;
  archiveIndexState.refreshId = refreshId;
  archiveIndexState.refreshMutationEpoch = refreshMutationEpoch;
  const isRefreshCurrent = () => archiveReadContextStillCurrent(context)
    && archiveIndexState.session === context.session
    && archiveIndexState.refreshId === refreshId
    && archiveIndexState.mutationEpoch === refreshMutationEpoch;
  const refreshPromise = refreshArchiveIndex(context, isRefreshCurrent)
    .catch(error => {
      if (!isRefreshCurrent()) return null;
      console.error('归档后台核对失败', error);
      if (activeDrawerId === 'archive-drawer') {
        if (archiveIndexState.ready) {
          setArchiveStatus(`${archiveErrorMessage(error, '核对')} 继续显示上次列表。`, true);
        } else {
          updateArchiveIndexView();
          setArchiveStatus(archiveErrorMessage(error, '读取'), true);
        }
      }
      return null;
    })
    .finally(() => {
      if (archiveIndexState.session === context.session
          && archiveIndexState.refreshId === refreshId
          && archiveIndexState.refreshPromise === refreshPromise) {
        archiveIndexState.refreshPromise = null;
        archiveIndexState.refreshMutationEpoch = -1;
      }
    });
  archiveIndexState.refreshPromise = refreshPromise;
  return refreshPromise;
}

function extractTitle(htmlText, fallback) {
  const t = htmlText.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  if (t && t[1].replace(/\s+/g, ' ').trim()) return t[1].replace(/\s+/g, ' ').trim();
  const h = htmlText.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i);
  if (h) {
    const s = h[1].replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
    if (s) return s;
  }
  return fallback;
}

function fmtArchiveDate(ms) {
  const d = new Date(ms);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function flashDrop(msg) {
  const drop = document.getElementById('archive-drop');
  const t = drop.querySelector('.ad-text');
  const orig = t.innerHTML;
  t.textContent = msg;
  setTimeout(() => { t.innerHTML = orig; }, 1800);
}

async function saveArchiveFiles(fileList, context) {
  const operations = window.MementoDashboardOperations;
  const files = [...(fileList || [])].filter(file => operations.isArchiveHtmlName(file.name));
  if (!files.length) { flashDrop('只接受 .html 文件'); return; }

  if (!(await ensureWritePermission(context.handle))) {
    setArchiveStatus('未获得读写授权，归档未保存。', true);
    return;
  }
  if (!archiveMutationStillCurrent(context)) return;

  let saved = 0;
  let renamed = 0;
  let failed = 0;
  let fatalError = null;
  let selectionMismatch = false;
  const savedItems = [];
  await withArchiveMutationLock(async () => {
    if (!archiveMutationStillCurrent(context)) return;
    if (!await archiveContextMatchesPersisted(context)) {
      selectionMismatch = archiveMutationStillCurrent(context);
      return;
    }
    // The directory and its contents may have changed in another tab while
    // this tab was waiting. Re-read both only after acquiring the shared lock.
    const dir = await getArchiveDir(true, context.handle);
    if (!dir) throw new Error('无法创建 .archives 目录');

    const existingNames = new Set();
    for await (const [name] of dir.entries()) existingNames.add(name);

    // Keep the whole batch in one critical section: otherwise another tab
    // could claim a later name between two files from this drop.
    for (const file of files) {
      if (!archiveMutationStillCurrent(context)) return;
      try {
        const saveName = operations.uniqueArchiveName(file.name, existingNames);
        const fh = await dir.getFileHandle(saveName, { create: true });
        const w = await fh.createWritable();
        await w.write(file);
        await w.close();
        existingNames.add(saveName);
        savedItems.push({
          name: saveName,
          title: saveName.replace(/\.html?$/i, ''),
          mtime: Date.now(),
          handle: fh,
        });
        if (saveName !== file.name) renamed++;
        saved++;
      } catch (error) {
        failed++;
        console.error('写入归档文件失败', error);
        const kind = operations.errorKind(error);
        if (kind === 'permission' || kind === 'missing') {
          fatalError = error;
          break;
        }
      }
    }
  });

  if (selectionMismatch) {
    reconcileArchiveSelectionMismatch(context);
    return;
  }
  if (!archiveMutationStillCurrent(context)) return;

  flashDrop(saved ? `已存入 ${saved} 份` : '存档失败');
  const details = [];
  if (renamed) details.push(`${renamed} 份同名文件已自动改名`);
  if (failed) details.push(`${failed} 份写入失败`);
  const directoryContext = captureActiveDirectoryContext();
  if (savedItems.length && directoryContext) {
    applyArchiveIndexMutation(directoryContext, currentItems => {
      const savedNames = new Set(savedItems.map(item => item.name));
      return currentItems.filter(item => !savedNames.has(item.name)).concat(savedItems);
    });
    if (activeDrawerId === 'archive-drawer') {
      void startArchiveIndexRefresh(directoryContext, { force: true });
    }
  } else if (activeDrawerId === 'archive-drawer') {
    void renderArchives({ forceRefresh: true });
  }
  if (archiveMutationStillCurrent(context) && details.length) {
    setArchiveStatus(details.join('；'), failed > 0);
  }
  if (fatalError) throw fatalError;
}

function updateArchiveIndexView() {
  const list = document.getElementById('archive-list');
  const countEl = document.getElementById('archive-count');
  if (!list || !countEl) return;
  const items = archiveIndexState.items;
  countEl.textContent = items.length ? String(items.length) : '';

  // The badge can update while the drawer is closed, but rebuilding the hidden
  // list would do work the user cannot see and could disturb a later focus restore.
  if (activeDrawerId !== 'archive-drawer') return;

  if (!archiveIndexState.ready) {
    list.innerHTML = '<div class="archive-empty">正在准备归档列表…</div>';
    return;
  }

  if (!items.length) {
    list.innerHTML = `<div class="archive-empty">还没有归档。<br>把 AI 整理好的 HTML 拖进来。</div>`;
    return;
  }

  list.innerHTML = items.map((it, i) => `
    <div class="archive-item" data-idx="${i}">
      <button type="button" class="archive-open" data-idx="${i}"
              aria-label="打开归档 ${escapeHtml(it.name.replace(/\.html?$/i, ''))}">
        <span class="ai-doc" aria-hidden="true">📄</span>
        <span class="ai-main">
          <span class="ai-title">${escapeHtml(it.title || it.name.replace(/\.html?$/i, ''))}</span>
          <span class="ai-meta">${it.mtime ? fmtArchiveDate(it.mtime) : '正在核对'}</span>
        </span>
        <span class="ai-open" aria-hidden="true" title="在新标签打开">↗</span>
      </button>
      <button type="button" class="ai-del" data-name="${escapeHtml(it.name)}"
              aria-label="删除归档 ${escapeHtml(it.name.replace(/\.html?$/i, ''))}" title="删除">✕</button>
    </div>`).join('');

  list.querySelectorAll('.archive-open').forEach(button => {
    button.addEventListener('click', () => {
      const context = captureActiveDirectoryContext();
      if (!context) return;
      void runArchiveAction(() => openArchive(items[+button.dataset.idx], context), '打开');
    });
  });
  list.querySelectorAll('.ai-del').forEach(btn => {
    btn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const name = btn.dataset.name;
      if (!confirm(`删除归档「${name}」?(会从 .archives 目录移除)`)) return;
      void runArchiveMutation(async context => {
        if (!(await ensureWritePermission(context.handle))) {
          setArchiveStatus('未获得读写授权，归档未删除。', true);
          return;
        }
        if (!archiveMutationStillCurrent(context)) return;
        let selectionMismatch = false;
        await withArchiveMutationLock(async () => {
          if (!archiveMutationStillCurrent(context)) return;
          if (!await archiveContextMatchesPersisted(context)) {
            selectionMismatch = archiveMutationStillCurrent(context);
            return;
          }
          const dir = await getArchiveDir(false, context.handle);
          if (!dir) throw Object.assign(new Error('归档目录不存在'), { name: 'NotFoundError' });
          if (!archiveMutationStillCurrent(context)) return;
          await dir.removeEntry(name);
        });
        if (selectionMismatch) {
          reconcileArchiveSelectionMismatch(context);
          return;
        }
        if (!archiveMutationStillCurrent(context)) return;
        const directoryContext = captureActiveDirectoryContext();
        if (directoryContext) {
          applyArchiveIndexMutation(
            directoryContext,
            currentItems => currentItems.filter(item => item.name !== name)
          );
          if (activeDrawerId === 'archive-drawer') {
            setArchiveStatus('');
            void startArchiveIndexRefresh(directoryContext, { force: true });
          }
        } else if (activeDrawerId === 'archive-drawer') {
          void renderArchives({ forceRefresh: true });
        }
      }, '删除');
    });
  });
}

async function renderArchives({ forceRefresh = false } = {}) {
  const generation = ++archiveRenderGeneration;
  const context = captureActiveDirectoryContext();
  if (!context) {
    document.getElementById('archive-count').textContent = '';
    document.getElementById('archive-list').innerHTML =
      '<div class="archive-empty">归档暂时无法读取。<br>请恢复数据目录访问后重试。</div>';
    setArchiveStatus('归档读取失败：当前数据目录尚未就绪。', true);
    return;
  }

  ensureArchiveIndexSession(context);
  if (archiveIndexState.ready) {
    updateArchiveIndexView();
    // Cached content is already useful content. Freshness verification remains
    // silent unless it actually fails.
    setArchiveStatus('');
  }

  const operations = window.MementoDashboardOperations;
  if (!operations || typeof operations.startCacheFirstRefresh !== 'function') {
    throw new Error('归档快速启动模块未加载');
  }
  await operations.startCacheFirstRefresh({
    cacheFirst: true,
    hydrateCache: () => hydrateArchiveIndexCache(context),
    waitForCache: waitForArchiveIndexCache,
    hasVisibleContent: () => archiveIndexState.ready,
    shouldRefresh: () => forceRefresh || !archiveIndexState.liveVerified,
    showWaiting: () => {
      if (generation !== archiveRenderGeneration
          || activeDrawerId !== 'archive-drawer'
          || !archiveReadContextStillCurrent(context)) return;
      setArchiveStatus('正在读取归档…');
      updateArchiveIndexView();
    },
    afterFirstPaint: afterFirstDashboardPaint,
    startRefresh: () => startArchiveIndexRefresh(context, { force: forceRefresh }),
    isCurrent: () => generation === archiveRenderGeneration
      && activeDrawerId === 'archive-drawer'
      && archiveReadContextStillCurrent(context),
  });
}

// 点击归档 → 在独立 sandbox 页中预览。
// viewer 会先移除任意脚本、刷新/外链和嵌入内容，仅保留静态 HTML/CSS、
// details/summary 和页内锚点；避免 AI 生成的归档通过 location/meta refresh 绕过网络 CSP。
async function openArchive(item, context) {
  if (!archiveMutationStillCurrent(context)) return;
  // 在点击的用户激活尚有效时先打开窗口，再异步读文件。
  const viewer = window.open(chrome.runtime.getURL('viewer.html'), '_blank');
  if (!viewer) {
    setArchiveStatus('无法打开归档，请允许 Memento 打开新标签后重试。', true);
    return;
  }

  try {
    let fileHandle = item && item.handle;
    if (!fileHandle) {
      const dir = await getArchiveDir(false, context.handle);
      if (!dir) throw Object.assign(new Error('归档目录不存在'), { name: 'NotFoundError' });
      if (!archiveMutationStillCurrent(context)) {
        try { viewer.close(); } catch {}
        return;
      }
      // A cross-tab cache intentionally stores metadata only. Resolve exactly
      // the clicked file instead of traversing or re-reading the whole archive.
      fileHandle = await dir.getFileHandle(item.name);
    }
    const file = await fileHandle.getFile();
    const text = await file.text();
    if (!archiveMutationStillCurrent(context)) {
      try { viewer.close(); } catch {}
      return;
    }
    const send = () => {
      if (!archiveMutationStillCurrent(context)) return;
      try { viewer.postMessage({ type: 'memento-html', html: text }, '*'); } catch {}
    };
    const onMsg = (e) => {
      if (e.source !== viewer || !e.data || e.data.type !== 'memento-viewer-ready') return;
      send();
      window.removeEventListener('message', onMsg);
      clearTimeout(cleanupTimer);
    };
    window.addEventListener('message', onMsg);
    const cleanupTimer = setTimeout(() => window.removeEventListener('message', onMsg), 10000);
    // 兜底：即使错过 viewer 的第一次 ready 消息，也主动补发一次。
    setTimeout(send, 500);
  } catch (error) {
    try { viewer.close(); } catch {}
    throw error;
  }
}

function openDrawer() {
  openSideDrawer('archive-drawer', 'archive-tab');
  void runArchiveAction(renderArchives, '读取');
}
function closeDrawer() {
  closeSideDrawers();
}

function initArchives() {
  document.getElementById('archive-tab').hidden = false;
  // Only lightweight metadata is restored here. The directory and HTML files
  // remain untouched until the drawer's post-paint background verification.
  primeArchiveIndexFromActiveSession();
  const context = captureActiveDirectoryContext();
  if (context
      && archiveIndexState.session === context.session
      && archiveIndexState.ready) {
    updateArchiveIndexView();
  } else {
    document.getElementById('archive-count').textContent = '';
  }
  if (archivesInited) return;
  archivesInited = true;

  document.getElementById('archive-tab').addEventListener('click', openDrawer);
  document.getElementById('drawer-close').addEventListener('click', closeDrawer);

  const drop = document.getElementById('archive-drop');
  const input = document.getElementById('archive-input');
  drop.addEventListener('click', () => input.click());
  drop.addEventListener('keydown', event => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    input.click();
  });
  input.addEventListener('change', () => {
    const files = [...input.files];
    input.value = '';
    void runArchiveMutation(context => saveArchiveFiles(files, context), '保存');
  });
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('dragover'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    drop.classList.remove('dragover');
    const files = [...e.dataTransfer.files];
    void runArchiveMutation(context => saveArchiveFiles(files, context), '保存');
  });
}

// =============================================================
// 5.7 每日总结 (当天第一帧 + Daily Review + 运行状态)
// =============================================================

let dailySummariesInited = false;
let selectedSummaryMonth = null;
const PHOTO_LOAD_CONCURRENCY = 3;
const PHOTO_CACHE_MAX_ENTRIES = 32;
const PHOTO_THUMBNAIL_MAX_WIDTH = 480;
const PHOTO_VIEWPORT_ROOT_MARGIN = '600px 0px';
const PHOTO_THUMBNAIL_VARIANT = 'w480-webp-q72-v1';
const PHOTO_PERSISTENT_DECISION_MS = 120;
let photoRenderGeneration = 0;
let photoViewportLoader = null;
let photoPermissionLost = false;
let photoPersistentReadDisabled = false;
let photoPersistentWriteDisabled = false;
let dailySummaryDataVersion = 0;
let dailySummaryRenderedVersion = -1;
let dailySummaryRenderedMonth = null;
let dailySummaryRenderedLayout = '';

function createPhotoThumbnailCanvas(width, height) {
  if (typeof OffscreenCanvas === 'function') return new OffscreenCanvas(width, height);
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

function encodePhotoThumbnailCanvas(canvas, { type, quality }) {
  if (canvas && typeof canvas.convertToBlob === 'function') {
    return canvas.convertToBlob({ type, quality });
  }
  return new Promise((resolve, reject) => {
    if (!canvas || typeof canvas.toBlob !== 'function') {
      reject(new Error('当前浏览器无法编码照片缩略图'));
      return;
    }
    canvas.toBlob(blob => {
      if (blob) resolve(blob);
      else reject(new Error('照片缩略图编码失败'));
    }, type, quality);
  });
}

const preparePhotoForDisplay = window.MementoPhotos.createThumbnailer({
  maxWidth: PHOTO_THUMBNAIL_MAX_WIDTH,
  type: 'image/webp',
  quality: 0.72,
  createImageBitmap: typeof window.createImageBitmap === 'function'
    ? window.createImageBitmap.bind(window)
    : null,
  createCanvas: createPhotoThumbnailCanvas,
  encodeCanvas: encodePhotoThumbnailCanvas,
  onError: error => console.warn('照片缩略图生成失败，将显示原图', error),
});

function photoCacheScopeIsCurrent(scope, isCurrent) {
  return Boolean(scope
    && isCurrent()
    && activeCoreLoad === scope.session
    && directoryLoadGate.isCurrent(scope.session.generation)
    && state.dirHandle === scope.session.handle);
}

async function resolvePhotoCacheScope(isCurrent) {
  if (!photoThumbnailCacheRepository || !isCurrent()) return null;
  const session = activeCoreLoad;
  if (!session
      || !directoryLoadGate.isCurrent(session.generation)
      || state.dirHandle !== session.handle) return null;

  let context = session.cacheContextReady ? session.cacheContext : null;
  if (!context) {
    try {
      const access = window.MementoDirectoryAccess;
      context = access && typeof access.withTimeout === 'function'
        ? await access.withTimeout(
            () => session.contextPromise,
            PHOTO_PERSISTENT_DECISION_MS,
            '等待照片缩略图缓存身份'
          )
        : await session.contextPromise;
    } catch (error) {
      if (error && error.name === 'TimeoutError') return null;
      throw error;
    }
  }
  if (!context || !context.binding || !photoCacheScopeIsCurrent({ session }, isCurrent)) return null;
  return { session, bindingToken: context.binding.token };
}

async function loadPersistentPhoto(record, isCurrent) {
  if (photoPersistentReadDisabled) return null;
  const scope = await resolvePhotoCacheScope(isCurrent);
  if (!scope) return null;
  const readThumbnail = () => photoThumbnailCacheRepository.get(
    scope.bindingToken,
    record.assetName,
    PHOTO_THUMBNAIL_VARIANT
  );
  const access = window.MementoDirectoryAccess;
  // IndexedDB is only an acceleration layer. If a browser/storage hiccup
  // keeps this optional read pending, fall back to the source photo instead
  // of turning the cache itself into a new loading bottleneck.
  const hit = access && typeof access.withTimeout === 'function'
    ? await access.withTimeout(
        readThumbnail,
        PHOTO_PERSISTENT_DECISION_MS,
        '读取照片缩略图缓存'
      )
    : await readThumbnail();
  if (!hit || !photoCacheScopeIsCurrent(scope, isCurrent)) return null;
  return {
    blob: hit.blob,
    sourceSize: hit.sourceSize,
    sourceLastModified: hit.sourceLastModified,
  };
}

async function storePersistentPhoto(thumbnail, sourceFile, record, isCurrent) {
  if (photoPersistentWriteDisabled
      || !thumbnail
      || thumbnail.type !== 'image/webp') return { stored: false, reason: 'ineligible' };
  const scope = await resolvePhotoCacheScope(isCurrent);
  if (!scope || !photoCacheScopeIsCurrent(scope, isCurrent)) {
    return { stored: false, reason: 'stale' };
  }
  return photoThumbnailCacheRepository.put({
    bindingToken: scope.bindingToken,
    assetName: record.assetName,
    variant: PHOTO_THUMBNAIL_VARIANT,
    blob: thumbnail,
    sourceSize: Number(sourceFile && sourceFile.size) || 0,
    sourceLastModified: Number(sourceFile && sourceFile.lastModified) || 0,
  });
}

async function deletePersistentPhoto(record) {
  if (!photoThumbnailCacheRepository || !record || !record.assetName) return;
  const scope = await resolvePhotoCacheScope(() => true);
  if (!scope || !photoCacheScopeIsCurrent(scope, () => true)) return;
  await photoThumbnailCacheRepository.delete(
    scope.bindingToken,
    record.assetName,
    PHOTO_THUMBNAIL_VARIANT
  );
}

const photoAssetLoader = window.MementoPhotos.createAssetLoader({
  concurrency: PHOTO_LOAD_CONCURRENCY,
  maxEntries: PHOTO_CACHE_MAX_ENTRIES,
  prepareFile: preparePhotoForDisplay,
  loadPersistent: loadPersistentPhoto,
  storePersistent: storePersistentPhoto,
  onPersistentError(error, record, stage) {
    if (stage === 'read') photoPersistentReadDisabled = true;
    if (stage === 'write') photoPersistentWriteDisabled = true;
    console.warn(`照片持久缓存${stage === 'read' ? '读取' : '写入'}失败，本页改用实时照片`, error, record);
  },
  createObjectURL: file => URL.createObjectURL(file),
  revokeObjectURL: url => URL.revokeObjectURL(url),
});

function cancelPhotoRender() {
  photoRenderGeneration++;
  if (photoViewportLoader) photoViewportLoader.stop();
  photoViewportLoader = null;
  document.querySelectorAll('.day-photo[data-photo-state="loading"]').forEach(figure => {
    figure.dataset.photoState = 'idle';
  });
}

function releasePhotoObjectUrls() {
  cancelPhotoRender();
  photoAssetLoader.clear();
  if (photoThumbnailCacheRepository) photoThumbnailCacheRepository.clearMemory();
  photoPermissionLost = false;
  photoPersistentReadDisabled = false;
  photoPersistentWriteDisabled = false;
  dailySummaryRenderedVersion = -1;
  dailySummaryRenderedMonth = null;
  dailySummaryRenderedLayout = '';
}

function markDailySummaryDataChanged() {
  dailySummaryDataVersion++;
}

function dailySummaryMonthKeys() {
  return [...new Set(state.dayCards.map(day => window.MementoDailySummaries.monthKey(day)).filter(Boolean))]
    .sort((a, b) => b.localeCompare(a));
}

function formatSummaryMonth(month) {
  const match = String(month || '').match(/^(\d{4})-(\d{2})$/);
  return match ? `${match[1]} 年 ${Number(match[2])} 月` : month;
}

function formatDayDate(date) {
  const match = String(date || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return match ? `${Number(match[2])}.${Number(match[3])}` : date;
}

function formatDayWeekday(day) {
  if (day.photo && day.photo.weekday) return day.photo.weekday;
  const parsed = new Date(`${day.dayKey}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return '';
  return `周${'日一二三四五六'[parsed.getDay()]}`;
}

function formatPhotoAlt(record) {
  const match = String(record.date || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
  const day = match ? `${match[1]}年${Number(match[2])}月${Number(match[3])}日` : record.date;
  return `${day} ${record.time} 的每日第一帧`;
}

function compactObservedTime(value) {
  const match = String(value || '').match(/T(\d{2}:\d{2})/);
  return match ? match[1] : String(value || '');
}

function photoContext(record) {
  const parts = [];
  if (record.timezone) parts.push(record.timezone);
  if (record.observedAt) parts.push(`天气 ${compactObservedTime(record.observedAt)}`);
  if (record.source) parts.push(`首条 ${record.source}`);
  return parts.join(' / ');
}

function renderSummaryMonthOptions() {
  const select = document.getElementById('daily-summary-month');
  const months = dailySummaryMonthKeys();
  if (!months.length) {
    selectedSummaryMonth = null;
    select.innerHTML = '<option>暂无总结</option>';
    select.disabled = true;
    return;
  }

  if (!selectedSummaryMonth || !months.includes(selectedSummaryMonth)) selectedSummaryMonth = months[0];
  select.disabled = false;
  select.innerHTML = months.map(month =>
    `<option value="${escapeHtml(month)}"${month === selectedSummaryMonth ? ' selected' : ''}>${escapeHtml(formatSummaryMonth(month))}</option>`
  ).join('');
}

function renderDailySummaryCount() {
  const count = state.dayCards.length;
  document.getElementById('daily-summary-count').textContent = count ? String(count) : '';
}

function dailySummaryStatusMessages() {
  return [
    state.reviewReadIssue,
    state.reviewStatusReadIssue,
    state.reviewPromptReadIssue,
  ].filter(Boolean);
}

function renderDailySummaryStatus(extraMessages = []) {
  document.getElementById('daily-summary-status').textContent = [
    ...dailySummaryStatusMessages(),
    ...extraMessages.filter(Boolean),
  ].join(' ');
}

function summaryPhotoLayout(day) {
  const photo = day && day.photo;
  return {
    dayKey: day && day.dayKey || '',
    assetName: photo && photo.assetName || '',
    time: photo && photo.time || '',
    weekday: photo && photo.weekday || '',
    weather: photo && photo.weather || '',
    timezone: photo && photo.timezone || '',
    observedAt: photo && photo.observedAt || '',
    source: photo && photo.source || '',
    issues: photo && photo.issues || [],
  };
}

function dailySummaryDaysForSelectedMonth() {
  return state.dayCards.filter(day => window.MementoDailySummaries.monthKey(day) === selectedSummaryMonth);
}

function dailySummaryLayoutSignature(days) {
  return JSON.stringify((days || []).map(summaryPhotoLayout));
}

function compactGeneratedTime(value) {
  const match = String(value || '').match(/T(\d{2}:\d{2})/);
  return match ? match[1] : '';
}

function meaningfulReviewText(value) {
  const text = String(value || '').trim();
  return text && text !== '无' ? text : '';
}

function reviewLead(review) {
  if (!review) return '';
  for (const key of ['scene', 'insights', 'personal', 'actionClues']) {
    const text = meaningfulReviewText(review.sections[key]);
    if (text) return text;
  }
  return '';
}

function reviewSectionMarkup(title, text) {
  const content = meaningfulReviewText(text);
  if (!content) return '';
  return `
    <section class="review-section">
      <h4>${escapeHtml(title)}</h4>
      <div class="review-section-body">${renderMarkdown(content)}</div>
    </section>`;
}

function fullReviewMarkup(review) {
  if (!review) return '';
  const sections = [
    ['工作与生活现场', review.sections.scene],
    ['灵感与想法', review.sections.insights],
    ['个人记录/情绪', review.sections.personal],
    ['行动线索', review.sections.actionClues],
    ['我的补充', review.sections.supplement],
    ['已忽略', review.sections.ignored],
  ].map(([title, text]) => reviewSectionMarkup(title, text)).join('');
  return sections || '<p class="review-format-note">这份总结没有可展示的正文。</p>';
}

function reviewStatus(day) {
  if (day.summaryStatus === 'failed') {
    return {
      tone: 'failed',
      text: '生成失败',
      title: day.reviewState && day.reviewState.message || '上次总结生成未完成',
    };
  }
  if (day.summaryStatus === 'stale') return { tone: 'updated', text: '记录有更新', title: '现有总结未包含当天最新记录' };
  if (day.review && day.freshness === 'unknown' && state.reviewCacheSource !== 'fresh') {
    return { tone: 'current', text: '总结已保存 · 核对中', title: '先显示已有总结，当前月份正在后台核对' };
  }
  if (day.summaryStatus === 'rebuild') {
    const issues = [
      ...(day.review && day.review.issues || []),
      ...(day.contractIssues || []),
    ];
    return { tone: 'updated', text: '待重建', title: issues.length ? issues.join('。') : '总结合同无法校验' };
  }
  if (day.summaryStatus === 'current') {
    const title = day.freshness === 'unknown'
      ? '总结已存在，但当前缺少来源哈希，暂时无法校验'
      : day.review && day.review.issues.length ? day.review.issues.join('。') : '';
    return { tone: 'current', text: '总结已更新', title };
  }
  return { tone: 'quiet', text: '待总结', title: '' };
}

function reviewStatusMarkup(status) {
  const title = status.title ? ` title="${escapeHtml(status.title)}"` : '';
  return `<p class="day-review-status is-${status.tone}"${title}>${escapeHtml(status.text)}</p>`;
}

function shouldOfferReviewRerun(day) {
  if (day.review && day.freshness === 'unknown' && state.reviewCacheSource !== 'fresh') return false;
  if (day.summaryStatus === 'failed' || day.summaryStatus === 'stale' || day.summaryStatus === 'rebuild') return true;
  return day.summaryStatus === 'pending' && day.dayKey < state.todayDate;
}

function reviewRerunMarkup(day) {
  if (!shouldOfferReviewRerun(day)) return '';
  return `<button type="button" class="day-review-rerun" data-review-rerun="${escapeHtml(day.dayKey)}">复制补跑指令</button>`;
}

function dailyReviewRerunPrompt(dayKey) {
  return `请在 ~/AISecretary 中为 Memento 补跑 ${dayKey} 的 Daily Review，严格按 .review/DAILY_REVIEW.md 执行并完成校验。`;
}

function bindDailyReviewRerunActions(container) {
  container.querySelectorAll('[data-review-rerun]').forEach(button => {
    button.addEventListener('click', async () => {
      const original = button.textContent;
      try {
        await navigator.clipboard.writeText(dailyReviewRerunPrompt(button.dataset.reviewRerun));
        button.textContent = '已复制，粘贴给 Codex';
      } catch (error) {
        console.error(error);
        button.textContent = '复制失败，请重试';
      }
      setTimeout(() => { button.textContent = original; }, 1800);
    });
  });
}

function dayReviewMarkup(day) {
  const status = reviewStatus(day);
  if (!day.review) {
    return `
      <section class="day-review is-empty">
        ${reviewStatusMarkup(status)}
        <h3>当天总结</h3>
        <p class="day-review-empty-copy">${day.summaryStatus === 'failed'
          ? '上次生成没有完成，原始记录仍然安全保留。'
          : '当天记录已经保留，总结生成后会显示在这里。'}</p>
        ${reviewRerunMarkup(day)}
      </section>`;
  }

  const lead = reviewLead(day.review);
  const generated = compactGeneratedTime(day.review.generatedAt);
  return `
    <section class="day-review">
      ${reviewStatusMarkup(status)}
      <h3>当天总结</h3>
      <div class="day-review-preview">${lead ? renderMarkdown(lead) : '<p>这份总结没有提取出明显主题。</p>'}</div>
      <details class="day-review-details">
        <summary>展开完整总结</summary>
        <div class="day-review-full">${fullReviewMarkup(day.review)}</div>
      </details>
      <p class="day-review-meta">${generated ? `生成于 ${escapeHtml(generated)}` : '生成时间未记录'}${day.review.sourceMock ? ' / 模拟来源' : ''}</p>
      ${reviewRerunMarkup(day)}
    </section>`;
}

function dayPhotoMarkup(day, index) {
  const record = day.photo;
  if (!record) return '';
  const context = photoContext(record);
  const issue = record.issues.length ? record.issues.join('。') : '';
  const photoState = record.assetName ? 'idle' : 'error';
  const media = record.assetName
    ? '<span class="day-photo-file-error">滚动到附近时加载照片</span>'
    : `<span class="day-photo-file-error">${escapeHtml(issue || '照片引用缺失')}</span>`;
  return `
    <figure class="day-photo" data-day-photo-index="${index}" data-photo-state="${photoState}" title="${escapeHtml(issue)}">
      <div class="day-photo-media">${media}</div>
      <figcaption class="day-photo-caption">
        <time datetime="${escapeHtml(`${record.date}T${record.time}`)}">${escapeHtml(record.time || '时间未记录')}</time>
        <p class="day-photo-weather">${escapeHtml(record.weather)}</p>
        ${context ? `<p class="day-photo-context">${escapeHtml(context)}</p>` : ''}
      </figcaption>
    </figure>`;
}

function dayCardMarkup(day, index) {
  const weekday = formatDayWeekday(day);
  const classes = day.photo ? 'day-card' : 'day-card has-no-photo';
  return `
    <article class="${classes}" data-day-index="${index}" data-day-key="${escapeHtml(day.dayKey)}">
      <header class="day-card-head">
        <time datetime="${escapeHtml(day.dayKey)}">${escapeHtml(formatDayDate(day.dayKey))}</time>
        ${weekday ? `<span>${escapeHtml(weekday)}</span>` : ''}
      </header>
      <div class="day-card-body">
        ${dayPhotoMarkup(day, index)}
        ${dayReviewMarkup(day)}
      </div>
    </article>`;
}

function setDayPhotoError(figure, message) {
  if (!figure) return;
  figure.dataset.photoState = 'error';
  const media = figure.querySelector('.day-photo-media');
  if (media) media.innerHTML = `<span class="day-photo-file-error">${escapeHtml(message)}</span>`;
}

function throwIfPhotoDirectoryChanged(isCurrent) {
  if (typeof isCurrent === 'function' && !isCurrent()) {
    const error = new Error('照片目录已经切换');
    error.name = 'AbortError';
    throw error;
  }
}

async function readDayPhotoFile(record, resolveAssetsDir, isCurrent) {
  throwIfPhotoDirectoryChanged(isCurrent);
  const assetsDir = await resolveAssetsDir();
  throwIfPhotoDirectoryChanged(isCurrent);
  const handle = await assetsDir.getFileHandle(record.assetName);
  throwIfPhotoDirectoryChanged(isCurrent);
  return handle.getFile();
}

async function renderDayPhotoAsset(record, figure, asset, generation, isCurrent = () => true) {
  if (!figure || !asset || !asset.url) return { ok: false, reason: '照片文件不可用' };
  const renderIsCurrent = () => generation === photoRenderGeneration
    && isCurrent()
    && figure.isConnected;
  try {
    if (!renderIsCurrent()) return { ok: false, stale: true };

    const img = document.createElement('img');
    img.alt = formatPhotoAlt(record);
    img.loading = 'eager';
    img.decoding = 'async';
    const media = figure.querySelector('.day-photo-media');
    media.replaceChildren(img);
    const legacyLoad = typeof img.decode !== 'function'
      ? new Promise((resolve, reject) => {
          img.addEventListener('load', resolve, { once: true });
          img.addEventListener('error', reject, { once: true });
        })
      : null;
    img.src = asset.url;

    try {
      if (typeof img.decode === 'function') await img.decode();
      else await legacyLoad;
    } catch {
      if (!renderIsCurrent()) return { ok: false, stale: true };
      photoAssetLoader.deleteAsset(record, asset.url);
      void deletePersistentPhoto(record).catch(() => {});
      setDayPhotoError(figure, '图片无法显示');
      return { ok: false, reason: '图片无法显示' };
    }

    if (!renderIsCurrent()) return { ok: false, stale: true };
    figure.dataset.photoState = 'ready';
    return { ok: true };
  } catch (error) {
    const message = '图片无法显示';
    setDayPhotoError(figure, message);
    return { ok: false, reason: message, error };
  }
}

function canReuseRenderedDailySummary(days, layout) {
  return dailySummaryRenderedVersion === dailySummaryDataVersion
    && dailySummaryRenderedMonth === selectedSummaryMonth
    && dailySummaryRenderedLayout === layout
    && document.getElementById('daily-summary-list').childElementCount > 0;
}

function refreshDailySummaryOptionalView(options = {}) {
  if (activeDrawerId !== 'daily-summary-drawer') return false;
  renderSummaryMonthOptions();
  renderDailySummaryCount();
  renderDailySummaryStatus();

  const days = dailySummaryDaysForSelectedMonth();
  const layout = dailySummaryLayoutSignature(days);
  const list = document.getElementById('daily-summary-list');
  const cards = [...list.querySelectorAll('.day-card')];
  const sameLayout = dailySummaryRenderedMonth === selectedSummaryMonth
    && dailySummaryRenderedLayout === layout
    && cards.length === days.length
    && cards.every((card, index) => card.dataset.dayKey === days[index].dayKey);
  if (!sameLayout) {
    if (options.renderOnMismatch !== false) void renderDailySummaryList({ force: true });
    return false;
  }

  cards.forEach((card, index) => {
    const current = card.querySelector('.day-review');
    const template = document.createElement('template');
    template.innerHTML = dayReviewMarkup(days[index]).trim();
    const updated = template.content.firstElementChild;
    current.replaceWith(updated);
    bindDailyReviewRerunActions(updated);
  });
  document.getElementById('daily-summary-meta').textContent = `${days.length} 天`;
  dailySummaryRenderedVersion = dailySummaryDataVersion;
  return true;
}

function startDailySummaryPhotoViewport(days, generation) {
  const list = document.getElementById('daily-summary-list');
  const items = days
    .map((day, index) => ({ day, index }))
    .filter(item => item.day.photo && item.day.photo.assetName);
  if (!items.length) return;
  if (photoPermissionLost) {
    for (const item of items) {
      const figure = list.querySelector(`[data-day-index="${item.index}"] .day-photo`);
      if (figure && figure.dataset.photoState !== 'ready') setDayPhotoError(figure, '照片读取已暂停');
    }
    renderDailySummaryStatus(['照片访问权限已失效，请刷新页面并重新允许数据目录访问。']);
    return;
  }

  let assetsDirPromise = null;
  let directoryError = null;
  const failures = new Map();
  const resolveAssetsDir = () => {
    if (!assetsDirPromise) {
      assetsDirPromise = state.dirHandle.getDirectoryHandle('assets')
        .catch(error => {
          directoryError = error;
          throw error;
        });
    }
    return assetsDirPromise;
  };
  const renderPhotoIssues = () => {
    const results = [...failures.values()];
    const permissionLost = results.some(result => result && result.permissionLost)
      || (directoryError
        && (directoryError.name === 'NotAllowedError' || directoryError.name === 'SecurityError'));
    const messages = [];
    if (permissionLost) messages.push('照片访问权限已失效，请刷新页面并重新允许数据目录访问。');
    else if (directoryError) messages.push('照片目录暂时不可用。');
    else if (results.length) messages.push(`${results.length} 张照片暂时无法显示。`);
    renderDailySummaryStatus(messages);
  };

  let controller = null;
  controller = window.MementoPhotos.createViewportLoader({
    createObserver: typeof IntersectionObserver === 'function'
      ? callback => new IntersectionObserver(callback, {
          root: list,
          rootMargin: PHOTO_VIEWPORT_ROOT_MARGIN,
          threshold: 0.01,
        })
      : null,
    isCurrent: () => generation === photoRenderGeneration
      && photoViewportLoader === controller,
    async load(item, figure, viewportIsCurrent) {
      const [result] = await photoAssetLoader.loadBatch([item.day.photo], {
        isCurrent: () => viewportIsCurrent()
          && figure.isConnected
          && list.contains(figure),
        canStart: () => !directoryError,
        loadFile: (record, isDirectoryCurrent) =>
          readDayPhotoFile(record, resolveAssetsDir, isDirectoryCurrent),
        onReady: (asset, record) => renderDayPhotoAsset(
          record,
          figure,
          asset,
          generation,
          viewportIsCurrent
        ),
      });
      return result || { ok: false, reason: '照片暂时无法显示' };
    },
    onState(figure, item, photoState, result) {
      if (generation !== photoRenderGeneration || !figure.isConnected) return;
      if (result && result.permissionLost) photoPermissionLost = true;
      figure.dataset.photoState = photoState;
      if (photoState === 'loading') {
        const media = figure.querySelector('.day-photo-media');
        if (media) media.innerHTML = '<span class="day-photo-file-error">正在生成轻量缩略图</span>';
      } else if (photoState === 'error') {
        const message = result && result.terminal
          ? '照片读取已暂停'
          : result && (result.skipped ? '照片读取已暂停' : result.reason)
            || '照片暂时不可用';
        setDayPhotoError(figure, message);
        if (!result || !result.skipped) failures.set(item.day.photo.assetName, result || {});
        renderPhotoIssues();
      } else if (photoState === 'ready') {
        failures.delete(item.day.photo.assetName);
        renderPhotoIssues();
      }
    },
  });
  photoViewportLoader = controller;

  for (const item of items) {
    const card = list.querySelector(`[data-day-index="${item.index}"]`);
    const figure = card && card.querySelector('.day-photo');
    if (!figure || (figure.dataset.photoState === 'ready' && figure.querySelector('img'))) continue;
    figure.dataset.photoState = 'idle';
    const media = figure.querySelector('.day-photo-media');
    if (media) media.innerHTML = '<span class="day-photo-file-error">滚动到附近时加载照片</span>';
    controller.observe(figure, item);
  }
}

async function renderDailySummaryList(options = {}) {
  renderSummaryMonthOptions();
  renderDailySummaryCount();
  const days = dailySummaryDaysForSelectedMonth();
  const layout = dailySummaryLayoutSignature(days);
  cancelPhotoRender();
  const generation = photoRenderGeneration;
  if (!options.force && canReuseRenderedDailySummary(days, layout)) {
    renderDailySummaryStatus();
    document.getElementById('daily-summary-meta').textContent = `${days.length} 天`;
    startDailySummaryPhotoViewport(days, generation);
    return;
  }

  const list = document.getElementById('daily-summary-list');
  const meta = document.getElementById('daily-summary-meta');
  renderDailySummaryStatus();

  if (!state.dayCards.length) {
    meta.textContent = '0 天';
    list.innerHTML = `
      <div class="daily-summary-empty">
        <strong>还没有每日总结</strong>
        第一次记录后，这一天会先出现在这里；照片和总结准备好后会自动补齐。
      </div>`;
    dailySummaryRenderedVersion = dailySummaryDataVersion;
    dailySummaryRenderedMonth = selectedSummaryMonth;
    dailySummaryRenderedLayout = layout;
    return;
  }

  meta.textContent = `${days.length} 天`;
  list.innerHTML = days.map(dayCardMarkup).join('');
  bindDailyReviewRerunActions(list);
  dailySummaryRenderedVersion = dailySummaryDataVersion;
  dailySummaryRenderedMonth = selectedSummaryMonth;
  dailySummaryRenderedLayout = layout;
  startDailySummaryPhotoViewport(days, generation);
}

function openDailySummaryDrawer() {
  openSideDrawer('daily-summary-drawer', 'daily-summary-tab');
  if (dailySummaryRenderedVersion !== dailySummaryDataVersion) {
    refreshDailySummaryOptionalView({ renderOnMismatch: false });
  }
  void renderDailySummaryList();
  scheduleSummaryMonthHydration(activeCoreLoad, selectedSummaryMonth);
}

function initDailySummaries() {
  if (cognitiveDemoState.active) {
    document.getElementById('daily-summary-tab').hidden = true;
    return;
  }
  document.getElementById('daily-summary-tab').hidden = false;
  renderDailySummaryCount();
  if (dailySummariesInited) return;
  dailySummariesInited = true;

  document.getElementById('daily-summary-tab').addEventListener('click', openDailySummaryDrawer);
  document.getElementById('daily-summary-drawer-close').addEventListener('click', closeSideDrawers);
  document.getElementById('daily-summary-month').addEventListener('change', event => {
    selectedSummaryMonth = event.target.value;
    void renderDailySummaryList({ force: true });
    scheduleSummaryMonthHydration(activeCoreLoad, selectedSummaryMonth);
  });
  window.addEventListener('pagehide', releasePhotoObjectUrls);
}

// =============================================================
// 6. 主流程
// =============================================================

const grantBtn = document.getElementById('grant-btn');
const grantSection = document.getElementById('grant-section');
const hero = document.getElementById('hero');
const statusEl = document.getElementById('status');
const dashboardSection = document.getElementById('dashboard-section');
const btnLabelGrant = grantBtn.querySelector('.btn-label');
const grantTitle = grantSection.querySelector('h2');
const grantHelp = grantSection.querySelector('.muted');
let rememberedDirectoryHandle = null;
let forceFolderPicker = false;

function setStatus(text, tone = 'muted') {
  statusEl.textContent = text;
  statusEl.style.color = tone === 'accent' ? 'var(--accent)'
                        : tone === 'ink'    ? 'var(--ink)'
                        : 'var(--ink-muted)';
}

function setGrantBusy(busy) {
  grantBtn.disabled = busy;
  grantBtn.setAttribute('aria-busy', String(busy));
}

function quarantineDirectoryActions() {
  selectionEpoch += 1;
  state.dirHandle = null;
  state.files = [];
  state.allEntries = [];
  state.todayFileText = null;
  state.todayEntries = [];
  state.selectedDate = null;
  state.snapshots = [];
  state.reviewFiles = [];
  state.reviewStateFiles = [];
  state.reviews = [];
  state.reviewStates = {};
  state.reviewSourceHashes = {};
  state.reviewSourceMocks = {};
  state.reviewPromptHash = '';
  state.reviewCacheSource = 'none';
  state.dayCards = [];
  state.recordSource = 'none';
  state.todayResolved = false;

  archiveRenderGeneration += 1;
  resetArchiveIndexState();
  resetContextAgentState();
  resetCognitiveHomeState();
  releasePhotoObjectUrls();
  for (const id of [
    'entry-list',
    'chips',
    'heatmap',
    'archive-list',
    'daily-summary-list',
  ]) document.getElementById(id)?.replaceChildren();
  for (const id of [
    'record-summary',
    'stats',
    'dashboard-notice',
    'archive-count',
    'archive-status',
    'daily-summary-count',
    'daily-summary-status',
    'daily-summary-meta',
  ]) {
    const element = document.getElementById(id);
    if (element) element.textContent = '';
  }
}

function showGrantUI({ title, help, label, status, tone = 'muted', forcePicker = false }) {
  retireActiveCoreLoad();
  quarantineDirectoryActions();
  closeSideDrawers(false);
  hero.hidden = false;
  grantSection.hidden = false;
  dashboardSection.hidden = true;
  document.getElementById('archive-tab').hidden = true;
  document.getElementById('daily-summary-tab').hidden = true;
  document.getElementById('context-tab').hidden = true;
  grantTitle.textContent = title;
  grantHelp.innerHTML = help;
  btnLabelGrant.textContent = label;
  forceFolderPicker = forcePicker;
  setStatus(status, tone);
}

function shortError(error) {
  if (!error) return '未知错误';
  return error.message || error.name || String(error);
}

function setRestoreStage(stage) {
  const messages = {
    'load-handle': '正在读取浏览器授权记录…',
    'query-permission': '正在检查数据目录权限…',
    'load-directory': '正在读取 Memento 数据文件…',
  };
  setStatus(messages[stage] || '正在恢复数据目录…');
  btnLabelGrant.textContent = '正在恢复…';
}

function retireActiveCoreLoad() {
  if (!activeCoreLoad) return;
  directoryLoadGate.invalidate(activeCoreLoad.generation);
  activeCoreLoad = null;
}

function showPersistedSelectionChanged(storedHandle) {
  rememberedDirectoryHandle = storedHandle;
  showGrantUI({
    title: '数据目录已在另一页面切换',
    help: '另一个 Memento 页面选择了新的数据目录。旧页面已停止使用之前的记录，点击后加载当前目录。',
    label: '加载当前数据目录',
    status: '已停止使用旧目录，等待加载当前目录',
  });
}

function setRegrantUI(permission = 'prompt', handle = rememberedDirectoryHandle, contextPromise = null) {
  if (permission === 'denied') {
    void invalidateFastStartCache(handle, contextPromise)
      .catch(error => console.warn('无法清除已撤权目录缓存', error));
    showGrantUI({
      title: '数据目录访问已被关闭',
      help: 'Chrome 仍记得之前的目录,但当前不允许继续访问。请重新选择 <code>~/AISecretary</code>。',
      label: '重新选择数据目录',
      status: '目录权限已被移除',
      tone: 'accent',
      forcePicker: true,
    });
    return;
  }

  showGrantUI({
    title: '请确认访问已保存的数据目录',
    help: 'Chrome 已记住 <code>~/AISecretary</code>,无需重新查找目录。点击后若浏览器询问授权期限,请选择“允许每次访问”。',
    label: '允许访问',
    status: '已记住数据目录,等待权限确认',
  });
}

function getLocalDate() {
  // 用本地时区取今天,与 append_text.sh 的 `date +%Y-%m-%d` 一致;
  // 不能用 toISOString().slice(0,10),那是 UTC,跨日会与文件名错开。
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

let activeCoreLoad = null;
let coreRefreshChannel = null;
let persistedSelectionReloadId = 0;
let selectionFlowId = 0;
try {
  if (dashboardCacheRepository && typeof BroadcastChannel === 'function') {
    coreRefreshChannel = new BroadcastChannel(CORE_REFRESH_CHANNEL_NAME);
  }
} catch (error) {
  console.warn('无法建立 Dashboard 跨标签页刷新通道', error);
}

function selectionFlowStillCurrent(flowId) {
  return flowId === selectionFlowId;
}

function completeCoverage(files) {
  return {
    enumerationDone: true,
    discoveredCount: files.length,
    completedCount: files.length,
    complete: true,
  };
}

function emptyReviewData() {
  return {
    reviewFiles: [],
    stateFiles: [],
    sourceHashes: {},
    sourceMocks: {},
    promptHash: '',
    promptIssue: '',
    source: 'none',
  };
}

function normalizeReviewData(data, source = 'cache') {
  if (!data || typeof data !== 'object') return emptyReviewData();
  return {
    reviewFiles: Array.isArray(data.reviewFiles) ? data.reviewFiles.map(file => ({ ...file })) : [],
    stateFiles: Array.isArray(data.stateFiles) ? data.stateFiles.map(file => ({ ...file })) : [],
    sourceHashes: { ...(data.sourceHashes || {}) },
    sourceMocks: { ...(data.sourceMocks || {}) },
    promptHash: String(data.promptHash || ''),
    promptIssue: String(data.promptIssue || ''),
    source,
  };
}

function currentReviewDataForHandle(handle, supplied) {
  if (supplied) return normalizeReviewData(supplied, supplied.source || 'cache');
  if (state.dirHandle !== handle) return emptyReviewData();
  return {
    reviewFiles: state.reviewFiles,
    stateFiles: state.reviewStateFiles,
    sourceHashes: state.reviewSourceHashes,
    sourceMocks: state.reviewSourceMocks,
    promptHash: state.reviewPromptHash,
    promptIssue: state.reviewPromptReadIssue,
    source: state.reviewCacheSource,
  };
}

function buildReviewProjection(files, snapshots, reviewData) {
  const reviews = window.MementoDailySummaries
    ? window.MementoDailySummaries.collectReviewRecords(reviewData.reviewFiles)
    : [];
  const reviewStates = window.MementoDailySummaries
    ? window.MementoDailySummaries.collectReviewStates(reviewData.stateFiles)
    : {};
  const sourceHashes = {
    ...buildSourceDaySkeleton(files),
    ...(reviewData.sourceHashes || {}),
  };
  const dayCards = window.MementoDailySummaries
    ? window.MementoDailySummaries.buildDayCards(snapshots, reviews, sourceHashes, reviewStates, {
        sourceMocks: reviewData.sourceMocks || {},
        promptHash: reviewData.promptHash || '',
        promptIssue: reviewData.promptIssue || '',
        cached: reviewData.source === 'cache',
      })
    : [];
  return { reviews, reviewStates, dayCards };
}

function commitReviewDataToVisibleState(handle, generation, suppliedData) {
  if (state.dirHandle !== handle || !directoryLoadGate.isCurrent(generation)) return false;
  const reviewData = normalizeReviewData(suppliedData, suppliedData && suppliedData.source || 'partial');
  const projection = buildReviewProjection(state.files, state.snapshots, reviewData);
  return directoryLoadGate.commit(generation, () => {
    state.reviewFiles = reviewData.reviewFiles;
    state.reviewStateFiles = reviewData.stateFiles;
    state.reviews = projection.reviews;
    state.reviewStates = projection.reviewStates;
    state.reviewSourceHashes = reviewData.sourceHashes;
    state.reviewSourceMocks = reviewData.sourceMocks;
    state.reviewPromptHash = reviewData.promptHash;
    state.reviewPromptReadIssue = reviewData.promptIssue;
    state.reviewCacheSource = reviewData.source;
    state.dayCards = projection.dayCards;
    markDailySummaryDataChanged();
    initDailySummaries();
    renderDailySummaryCount();
    refreshDailySummaryOptionalView();
  });
}

function commitCoreRecordView(handle, generation, recordResult, options) {
  if (!directoryLoadGate.isCurrent(generation)) return false;
  const files = [...(recordResult.files || [])].sort((a, b) => b.date.localeCompare(a.date));
  const today = options.today || getLocalDate();
  const todayFile = files.find(file => file.date === today);
  const allEntries = files.flatMap(file => parseFile(file.text, file.date));
  const snapshots = window.MementoPhotos
    ? window.MementoPhotos.collectSnapshotRecords(files)
    : [];
  const sourceMocks = buildSourceMocks(files);
  const reviewData = currentReviewDataForHandle(handle, options.reviewData);
  if (!Object.keys(reviewData.sourceMocks).length) reviewData.sourceMocks = sourceMocks;
  const projection = buildReviewProjection(files, snapshots, reviewData);
  const coverage = recordResult.coverage || {};
  const todayResolved = options.todayResolved !== undefined
    ? options.todayResolved
    : Boolean(todayFile) || Boolean(coverage.enumerationDone);

  return directoryLoadGate.commit(generation, () => {
    state.files = files;
    state.allEntries = allEntries;
    state.todayDate = today;
    state.todayFileText = todayFile ? todayFile.text : null;
    state.todayEntries = allEntries.filter(entry => entry.date === today);
    state.selectedDate = state.selectedDate
      && state.selectedDate >= dateOffset(today, -89)
      && state.selectedDate <= today
        ? state.selectedDate
        : today;
    state.selectedRange = getSavedRange();
    state.selectedStyle = getSavedStyle();
    state.dirHandle = handle;
    state.snapshots = snapshots;
    state.reviewFiles = reviewData.reviewFiles;
    state.reviewStateFiles = reviewData.stateFiles;
    state.reviews = projection.reviews;
    state.reviewStates = projection.reviewStates;
    state.reviewSourceHashes = reviewData.sourceHashes;
    state.reviewSourceMocks = reviewData.sourceMocks;
    state.reviewPromptHash = reviewData.promptHash;
    state.reviewCacheSource = reviewData.source;
    state.dayCards = projection.dayCards;
    markDailySummaryDataChanged();
    state.reviewReadIssue = '';
    state.reviewStatusReadIssue = '';
    state.reviewPromptReadIssue = reviewData.promptIssue;
    state.recordReadIssues = recordResult.issues || [];
    state.recordScanIssue = recordResult.issue || '';
    state.recordSource = options.source;
    state.recordRefreshMessage = options.message || '';
    state.todayResolved = todayResolved;

    hero.hidden = true;
    grantSection.hidden = true;
    dashboardSection.hidden = false;

    populateSelectors();
    bindEasterEgg();
    if (options.source !== 'waiting') {
      // The archive badge belongs to the cached first paint. Prime it before
      // the rail is revealed so the number never pops in one frame later.
      primeArchiveIndexFromActiveSession();
      initArchives();
      initDailySummaries();
      initContextAgent();
    }
    renderDashboard();
    void refreshCognitiveHomeProjection(handle, generation);
    if (activeDrawerId === 'daily-summary-drawer') void renderDailySummaryList({ force: true });
  });
}

function mergeReviewFile(records, file) {
  return [
    ...(records || []).filter(record => record.name !== file.name),
    file,
  ].sort((a, b) => b.date.localeCompare(a.date));
}

function replaceReviewMonth(records, incoming, month) {
  if (!month) return [...incoming];
  return [
    ...(records || []).filter(record => !String(record.date || '').startsWith(month)),
    ...(incoming || []),
  ].sort((a, b) => b.date.localeCompare(a.date));
}

function replaceDateMapMonth(existing, incoming, month) {
  const next = Object.fromEntries(Object.entries(existing || {})
    .filter(([date]) => !month || !date.startsWith(month)));
  return { ...next, ...(incoming || {}) };
}

function liveReviewData() {
  return currentReviewDataForHandle(state.dirHandle, null);
}

function commitReviewFileDelta(handle, generation, kind, file) {
  if (state.dirHandle !== handle || !directoryLoadGate.isCurrent(generation)) return;
  const data = normalizeReviewData(liveReviewData(), 'partial');
  if (kind === 'review') data.reviewFiles = mergeReviewFile(data.reviewFiles, file);
  else data.stateFiles = mergeReviewFile(data.stateFiles, file);
  commitReviewDataToVisibleState(handle, generation, data);
}

async function persistReviewData(session, data) {
  if (!dashboardCacheRepository
      || !session
      || !directoryLoadGate.isCurrent(session.generation)) return;
  if (!session.cacheContextReady) {
    const access = window.MementoDirectoryAccess;
    if (!access || typeof access.withTimeout !== 'function') return;
    try {
      session.cacheContext = await access.withTimeout(
        () => session.contextPromise,
        CACHE_CONTEXT_GRACE_MS,
        '等待每日总结缓存身份'
      );
      session.cacheContextReady = true;
    } catch {
      return;
    }
  }
  const context = session.cacheContext;
  if (!context || !context.writable || !context.binding) return;
  try {
    await dashboardCacheRepository.commitReviewSnapshot(context.binding.token, {
      reviewFiles: data.reviewFiles,
      stateFiles: data.stateFiles,
      promptHash: data.promptHash,
      sourceHashes: data.sourceHashes,
      sourceMocks: data.sourceMocks,
    });
  } catch (error) {
    console.warn('每日总结快照保存失败，下次将重新核对当前月份', error);
  }
}

async function hydrateOptionalDashboardData(handle, generation, files, options = {}) {
  const month = String(options.month || '');
  const scopedFiles = month
    ? (files || []).filter(file => String(file.date || '').startsWith(month))
    : (files || []);
  const sourceHashes = await buildSourceHashes(scopedFiles);
  if (!directoryLoadGate.isCurrent(generation)) return;
  const sourceMocks = buildSourceMocks(scopedFiles);

  const validatingData = normalizeReviewData(liveReviewData(), 'partial');
  validatingData.sourceHashes = replaceDateMapMonth(
    validatingData.sourceHashes,
    sourceHashes,
    month
  );
  validatingData.sourceMocks = replaceDateMapMonth(
    validatingData.sourceMocks,
    sourceMocks,
    month
  );
  commitReviewDataToVisibleState(handle, generation, validatingData);

  renderDailySummaryStatus(['正在核对当前月份的总结…']);
  const { reviewResult, reviewStateResult, promptResult } = await readOptionalDashboardData(handle, {
    isCurrent: () => directoryLoadGate.isCurrent(generation),
    datePrefix: month,
    onReviewFile: file => commitReviewFileDelta(handle, generation, 'review', file),
    onReviewStateFile: file => commitReviewFileDelta(handle, generation, 'state', file),
  });
  if (!directoryLoadGate.isCurrent(generation)) return;

  const completeData = normalizeReviewData(liveReviewData(), 'fresh');
  completeData.reviewFiles = replaceReviewMonth(completeData.reviewFiles, reviewResult.files, month);
  completeData.stateFiles = replaceReviewMonth(completeData.stateFiles, reviewStateResult.files, month);
  completeData.sourceHashes = replaceDateMapMonth(completeData.sourceHashes, sourceHashes, month);
  completeData.sourceMocks = replaceDateMapMonth(completeData.sourceMocks, sourceMocks, month);
  completeData.promptHash = promptResult.hash;
  completeData.promptIssue = promptResult.issue;

  directoryLoadGate.commit(generation, () => {
    state.reviewReadIssue = reviewResult.issue;
    state.reviewStatusReadIssue = reviewStateResult.issue;
  });
  commitReviewDataToVisibleState(handle, generation, completeData);
  const session = activeCoreLoad;
  if (session && session.handle === handle && session.generation === generation) {
    await persistReviewData(session, completeData);
  }
}

function cacheContextForHandle(handle, suppliedContextPromise) {
  if (suppliedContextPromise) return suppliedContextPromise;
  if (!dashboardCacheRepository) return Promise.resolve(null);
  return dashboardCacheRepository.readBootstrap()
    .then(bootstrap => dashboardCacheRepository.resolveBootstrap(handle, bootstrap))
    .catch(error => {
      console.warn('快速启动缓存不可用，将使用实时读取', error);
      return null;
    });
}

function mergeFilesWithTodayProbe(session, cachedFiles) {
  return window.MementoDashboardOperations.mergeCachedFilesWithTodayProbe(cachedFiles, {
    todayDate: session.today,
    file: session.todayFile,
    resolved: session.todayProbeResolved,
    probedAt: session.todayProbeAt,
  });
}

async function startCacheHydration(session) {
  try {
    const context = await session.contextPromise;
    session.cacheContextReady = true;
    session.cacheContext = context;
    if (!context || !directoryLoadGate.isCurrent(session.generation)) return false;
    const liveAlreadyVerified = state.recordSource === 'fresh' || state.recordSource === 'shared';
    // The normal fast path inherits the permission check immediately before
    // loadAndRender. If cache validation missed its short decision window,
    // re-check before a much later result reveals cached records or metadata.
    if (session.cacheDecisionExpired
        && !liveAlreadyVerified
        && (context.cache || context.archiveIndex)
        && !await permissionStillGranted(session)) return false;
    if (!directoryLoadGate.isCurrent(session.generation)) return false;

    session.bootstrapArchiveIndex = context.archiveIndex || null;
    session.cachedReviewData = context.reviewCache
      ? normalizeReviewData(context.reviewCache, 'cache')
      : null;
    if (state.dirHandle === session.handle) primeArchiveIndexFromActiveSession();
    if (session.cachedReviewData
        && state.dirHandle === session.handle
        && state.reviewFiles.length === 0) {
      commitReviewDataToVisibleState(
        session.handle,
        session.generation,
        session.cachedReviewData
      );
    }
    if (liveAlreadyVerified) {
      return false;
    }
    if (!context.cache) return false;

    // Normally cache hydration is a hard barrier before the live scan starts.
    // Keep the merge defensive anyway: if a future fallback lets today's file
    // arrive first, preserve that fresh file and fill only historical days from
    // the last-known-good snapshot.
    const files = mergeFilesWithTodayProbe(session, context.cache.files);
    const hasLiveToday = Boolean(session.todayFile);
    session.cacheShown = commitCoreRecordView(session.handle, session.generation, {
      files,
      issues: [],
      issue: '',
      coverage: completeCoverage(files),
    }, {
      source: hasLiveToday ? 'partial' : 'cache',
      message: hasLiveToday
        ? '今天的记录已核对；其他历史记录仍显示上次的完整结果。'
        : '正在显示上次完整记录；后台正在核对最新文件。',
      todayResolved: true,
      today: session.today,
      reviewData: session.cachedReviewData,
    });
    return Boolean(session.cacheShown);
  } catch (error) {
    session.cacheContextReady = true;
    session.cacheContext = null;
    session.bootstrapArchiveIndex = null;
    console.warn('快速启动缓存不可用，继续实时读取', error);
    return false;
  }
}

async function waitForStartupCache(session, hydrationPromise) {
  const access = window.MementoDirectoryAccess;
  if (!access || typeof access.withTimeout !== 'function') return hydrationPromise;
  try {
    return await access.withTimeout(
      () => hydrationPromise,
      CACHE_FIRST_DECISION_MS,
      '等待快速启动缓存'
    );
  } catch (error) {
    if (!error || error.name !== 'TimeoutError') throw error;
    // Do not cancel the real lookup: late hydration can still replace waiting
    // or merge with live today. This flag makes that late reveal re-check the
    // directory permission first.
    session.cacheDecisionExpired = true;
    return false;
  }
}

async function permissionStillGranted(session) {
  let permission;
  try {
    permission = await queryRead(session.handle);
  } catch (error) {
    if (directoryLoadGate.isCurrent(session.generation)) {
      directoryLoadGate.invalidate(session.generation);
      showAccessResult({ kind: 'permission-check-error', handle: session.handle, error });
    }
    return false;
  }
  if (permission === 'granted') return directoryLoadGate.isCurrent(session.generation);
  if (directoryLoadGate.isCurrent(session.generation)) {
    directoryLoadGate.invalidate(session.generation);
    rememberedDirectoryHandle = session.handle;
    setRegrantUI(permission, session.handle, session.contextPromise);
  }
  return false;
}

async function produceCoreRecords(session) {
  const recordResult = await listMarkdownFiles(session.handle, {
    todayDate: session.today,
    seedFiles: session.todayFile ? [session.todayFile] : [],
    isCurrent: () => directoryLoadGate.isCurrent(session.generation),
    onFile: detail => {
      if (!directoryLoadGate.isCurrent(session.generation)) return;
      // Today is the only partial result that changes first-screen utility.
      // Historical files converge in the final commit instead of reparsing and
      // repainting the whole dashboard once per completed file.
      if (!detail.isToday) return;
      if (session.todayProbeResolved) return;
      session.todayFile = detail.file;
      session.todayProbeResolved = true;
      session.todayProbeAt = Date.now();
      const files = session.cacheShown
        ? state.files
          .filter(file => file.name !== detail.file.name)
          .concat(detail.file)
        : [detail.file];
      session.liveShown = commitCoreRecordView(session.handle, session.generation, {
        files,
        issues: [],
        issue: '',
        coverage: {
          enumerationDone: false,
          discoveredCount: detail.discoveredCount,
          completedCount: detail.completedCount,
          complete: false,
        },
      }, {
        source: 'partial',
        message: session.cacheShown
          ? '今天的记录已核对；其他历史记录仍显示上次的完整结果。'
          : '今天的记录已显示；历史记录仍在后台核对。',
        todayResolved: true,
        today: session.today,
        reviewData: state.dirHandle === session.handle ? null : session.cachedReviewData,
      });
    },
  });

  if (!directoryLoadGate.isCurrent(session.generation)) return { ...recordResult, stale: true };
  const complete = Boolean(recordResult.coverage && recordResult.coverage.complete);
  if (complete) {
    session.liveShown = commitCoreRecordView(session.handle, session.generation, recordResult, {
      source: 'fresh',
      message: '',
      todayResolved: true,
      today: session.today,
      reviewData: state.dirHandle === session.handle ? null : session.cachedReviewData,
    });
    if (session.liveShown) {
      if (activeDrawerId === 'daily-summary-drawer') {
        scheduleSummaryMonthHydration(session, selectedSummaryMonth);
      }
    }
  } else if (session.cacheShown) {
    directoryLoadGate.commit(session.generation, () => {
      state.recordReadIssues = recordResult.issues || [];
      state.recordScanIssue = recordResult.issue || '';
      state.recordRefreshMessage = state.recordSource === 'partial' && state.todayResolved
        ? '今天的记录已核对；本轮历史记录核对未完整结束，仍保留上次结果。'
        : '本轮核对没有完整结束，继续保留上次的完整记录。';
      renderDashboardNotice();
      updateCtaLabel();
    });
  } else {
    const todayReadFailed = (recordResult.issues || [])
      .some(issue => issue.name === `${session.today}.md`);
    session.liveShown = commitCoreRecordView(session.handle, session.generation, recordResult, {
      source: 'partial',
      message: '本轮只完成了部分文件读取；已显示能够确认的记录。',
      todayResolved: Boolean(recordResult.files.find(file => file.date === session.today))
        || Boolean(recordResult.coverage && recordResult.coverage.enumerationDone && !todayReadFailed),
      today: session.today,
      reviewData: state.dirHandle === session.handle ? null : session.cachedReviewData,
    });
  }
  return recordResult;
}

async function readTodayRecord(session) {
  if (!directoryLoadGate.isCurrent(session.generation)) return { stale: true };
  const operations = window.MementoDashboardOperations;
  try {
    return await operations.readTodayMarkdownFile(session.handle, session.today, {
      isCurrent: () => directoryLoadGate.isCurrent(session.generation),
    });
  } catch (error) {
    const kind = operations.errorKind(error);
    if (kind === 'permission' || kind === 'missing') throw error;
    // The complete scan still gets one independent chance to read today. A
    // transient point-read failure must not strand the historical refresh.
    console.warn('今日记录直读失败，将由后台完整核对重试', error);
    session.todayProbeIssue = error;
    return { failed: true, error };
  }
}

async function refreshTodayReviewFreshness(session, file) {
  if (!file || !file.bytes || !directoryLoadGate.isCurrent(session.generation)) return;
  const hash = await sha256Hex(file.bytes);
  if (!directoryLoadGate.isCurrent(session.generation) || state.dirHandle !== session.handle) return;
  const data = normalizeReviewData(liveReviewData(), state.reviewCacheSource || 'partial');
  data.sourceHashes[session.today] = hash;
  data.sourceMocks[session.today] = sourceMockFromText(file.text);
  commitReviewDataToVisibleState(session.handle, session.generation, data);
}

function commitTodayRecord(session, result) {
  if (!result || result.stale || !directoryLoadGate.isCurrent(session.generation)) {
    return false;
  }
  if (result.failed) return false;

  session.todayProbeResolved = true;
  session.todayProbeAt = Date.now();
  session.todayFile = result.file || null;
  const files = mergeFilesWithTodayProbe(session, state.files);
  session.liveShown = commitCoreRecordView(session.handle, session.generation, {
    files,
    issues: [],
    issue: '',
    coverage: {
      enumerationDone: false,
      discoveredCount: files.length,
      completedCount: 1,
      complete: false,
    },
  }, {
    source: 'partial',
    message: result.file
      ? '今天的记录已同步；历史记录仍在后台核对。'
      : '已确认今天暂无记录；历史记录仍在后台核对。',
    todayResolved: true,
    today: session.today,
    reviewData: state.dirHandle === session.handle ? null : session.cachedReviewData,
  });
  if (result.file) void refreshTodayReviewFreshness(session, result.file);
  return Boolean(session.liveShown);
}

function scheduleSummaryMonthHydration(session, month) {
  const monthKey = String(month || '');
  if (!session
      || !monthKey
      || session.reviewMonthsStarted.has(monthKey)
      || !directoryLoadGate.isCurrent(session.generation)) return;
  session.reviewMonthsStarted.add(monthKey);
  void hydrateOptionalDashboardData(session.handle, session.generation, state.files, {
    month: monthKey,
  })
    .catch(error => handleOptionalReadError(session, error));
}

function handleOptionalReadError(session, error) {
  if (!directoryLoadGate.isCurrent(session.generation)) return;
  const access = window.MementoDirectoryAccess;
  if (access && access.isPermissionError(error)) {
    void permissionStillGranted(session).then(stillGranted => {
      if (!stillGranted || !directoryLoadGate.isCurrent(session.generation)) return;
      directoryLoadGate.commit(session.generation, () => {
        state.reviewReadIssue = `每日总结暂时无法读取: ${shortError(error)}`;
        if (activeDrawerId === 'daily-summary-drawer') renderDailySummaryStatus();
      });
    });
    return;
  }
  console.warn('每日总结增强数据读取失败', error);
  directoryLoadGate.commit(session.generation, () => {
    state.reviewReadIssue = `每日总结暂时无法读取: ${shortError(error)}`;
    if (activeDrawerId === 'daily-summary-drawer') renderDailySummaryStatus();
  });
}

async function persistCompleteSnapshot(session, recordResult) {
  if (!dashboardCacheRepository) return { stored: false, reason: 'cache-unavailable' };
  if (!directoryLoadGate.isCurrent(session.generation)) return { stored: false, reason: 'stale-session' };
  // Cache is optional. If its bootstrap lookup is still pending when live has
  // completed, give the already-started IDB read one short grace period. This
  // never starts or duplicates a File System Access request.
  if (!session.cacheContextReady) {
    const access = window.MementoDirectoryAccess;
    if (!access || !access.withTimeout) return { stored: false, reason: 'context-pending' };
    try {
      const context = await access.withTimeout(
        () => session.contextPromise,
        CACHE_CONTEXT_GRACE_MS,
        '等待快速缓存上下文'
      );
      session.cacheContextReady = true;
      session.cacheContext = context;
    } catch {
      return { stored: false, reason: 'context-pending' };
    }
  }
  const context = session.cacheContext;
  if (!context || !context.writable || !context.binding) {
    return { stored: false, reason: context?.reason || 'cache-readonly' };
  }
  if (!directoryLoadGate.isCurrent(session.generation)) return { stored: false, reason: 'stale-session' };
  const stored = await dashboardCacheRepository.commitComplete(context.binding.token, {
    ...recordResult,
    scanDate: session.today,
  });
  if (stored.stored && coreRefreshChannel) {
    coreRefreshChannel.postMessage({
      type: 'core-snapshot-committed',
      bindingToken: context.binding.token,
      committedAt: stored.snapshot.committedAt,
      scanDate: session.today,
    });
  }
  return stored;
}

async function reloadSharedSnapshot(session, publication = null) {
  if (!dashboardCacheRepository || !directoryLoadGate.isCurrent(session.generation)) return false;
  const bootstrap = await dashboardCacheRepository.readBootstrap();
  const context = await dashboardCacheRepository.resolveBootstrap(session.handle, bootstrap);
  if (!context.cache || !directoryLoadGate.isCurrent(session.generation)) return false;
  if (!await permissionStillGranted(session)) return false;
  if (state.recordSource === 'fresh' || state.recordSource === 'shared') return true;
  const sharedFresh = Boolean(publication
    && context.binding
    && publication.bindingToken === context.binding.token
    && publication.committedAt === context.cache.committedAt
    && publication.scanDate === session.today
    && context.cache.scanDate === session.today);
  const verifiedShared = sharedFresh;
  if (publication && !verifiedShared) return false;
  if (!verifiedShared && session.liveShown) return false;
  const files = mergeFilesWithTodayProbe(session, context.cache.files);
  session.cacheShown = commitCoreRecordView(session.handle, session.generation, {
    files,
    issues: [],
    issue: '',
    coverage: completeCoverage(context.cache.files),
  }, {
    source: verifiedShared ? 'shared' : 'cache',
    message: verifiedShared ? '' : '正在显示上次完整记录；另一页面正在核对最新文件。',
    todayResolved: true,
    today: session.today,
    reviewData: state.dirHandle === session.handle ? null : session.cachedReviewData,
  });
  if (verifiedShared && session.cacheShown && activeDrawerId === 'daily-summary-drawer') {
    scheduleSummaryMonthHydration(session, selectedSummaryMonth);
  }
  return Boolean(session.cacheShown);
}

function keepCurrentViewAfterCoreError(session, error) {
  if (!directoryLoadGate.isCurrent(session.generation)) return;
  console.error('Memento 核心记录刷新失败', error);
  if (state.recordSource === 'cache' || state.recordSource === 'partial'
      || state.recordSource === 'fresh' || state.recordSource === 'shared') {
    directoryLoadGate.commit(session.generation, () => {
      state.recordScanIssue = `最新记录核对失败: ${shortError(error)}`;
      state.recordRefreshMessage = state.recordSource === 'cache'
        ? '继续显示上次的完整记录。'
        : state.recordRefreshMessage;
      renderDashboardNotice();
      updateCtaLabel();
    });
    return;
  }
  directoryLoadGate.invalidate(session.generation);
  showAccessResult({ kind: 'read-error', handle: session.handle, error });
}

function handleCoreRefreshError(session, error) {
  if (!directoryLoadGate.isCurrent(session.generation)) return;
  const access = window.MementoDirectoryAccess;
  if (access && access.isPermissionError(error)) {
    void permissionStillGranted(session).then(stillGranted => {
      if (stillGranted) keepCurrentViewAfterCoreError(session, error);
    });
    return;
  }
  if (access && access.isStaleHandleError(error)) {
    directoryLoadGate.invalidate(session.generation);
    showAccessResult({
      kind: 'directory-missing',
      handle: session.handle,
      cacheContextPromise: session.contextPromise,
      error,
    });
    return;
  }
  keepCurrentViewAfterCoreError(session, error);
}

async function produceCoordinatedCoreRecords(session, coordination) {
  const recordResult = await produceCoreRecords(session);
  const complete = Boolean(recordResult
    && recordResult.coverage
    && recordResult.coverage.complete);
  let snapshotResult = { stored: false, reason: coordination.shared ? 'incomplete' : 'local-only' };
  if (coordination.shared && complete && directoryLoadGate.isCurrent(session.generation)) {
    try {
      // Keep the Web Lock until the complete snapshot is committed and its
      // publication is sent. Followers never race a half-published refresh.
      snapshotResult = await persistCompleteSnapshot(session, recordResult);
    } catch (error) {
      console.warn('完整快照保存失败，下次将继续实时读取', error);
      snapshotResult = { stored: false, reason: 'commit-error', error };
    }
  }
  return { recordResult, snapshotResult };
}

function scheduleCoreRefresh(session) {
  if (!directoryLoadGate.isCurrent(session.generation)) return;
  const operations = window.MementoDashboardOperations;
  const canShare = Boolean(coreRefreshChannel
    && navigator.locks
    && typeof navigator.locks.request === 'function');
  const lockManager = canShare ? navigator.locks : null;
  // Every Tab performs one exact today probe before asking for the global
  // history lock. A reloaded page therefore sees a new append even when an old
  // document still owns the non-cancellable full-scan lock.
  const refreshPromise = operations.startTodayFirstRefresh({
    isCurrent: () => directoryLoadGate.isCurrent(session.generation),
    readToday: () => readTodayRecord(session),
    commitToday: result => commitTodayRecord(session, result),
    startHistory: () => operations.coordinateCoreRefresh(
      lockManager,
      coordination => {
        session.coordinationRole = coordination.role;
        return produceCoordinatedCoreRecords(session, coordination);
      }
    ),
  }).then(result => result.stale ? { role: 'stale' } : result.historyResult);

  void refreshPromise.then(result => {
    if (!directoryLoadGate.isCurrent(session.generation)) return;
    if (result.role === 'stale') return;
    session.coordinationRole = result.role;
    if (result.role === 'follower') {
      directoryLoadGate.commit(session.generation, () => {
        state.recordRefreshMessage = session.todayProbeResolved
          ? '今天的记录已同步；另一页面正在后台核对历史记录。'
          : state.recordSource === 'cache'
            ? '正在显示上次完整记录；另一页面正在核对最新文件。'
            : '另一 Memento 页面正在读取最新记录，完成后会自动显示；若长时间无变化，请关闭其他 Memento 页面后刷新。';
        renderDashboardNotice();
        updateCtaLabel();
      });
      // Cache hydration has already had its own bounded opportunity and keeps
      // running if late. Do not immediately re-read the same IndexedDB state
      // or query permission a second time; the leader's publication message
      // will trigger one exact, token-checked reload when data really changes.
    }
  }).catch(error => {
    handleCoreRefreshError(session, error);
  });
}

function afterFirstDashboardPaint() {
  if (typeof requestAnimationFrame !== 'function' || document.visibilityState === 'hidden') {
    return Promise.resolve();
  }

  return new Promise(resolve => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(fallback);
      resolve();
    };
    // One rAF callback still runs before paint. The second frame proves the
    // cached DOM had a paint opportunity before any root-directory traversal.
    const fallback = setTimeout(finish, 120);
    requestAnimationFrame(() => requestAnimationFrame(finish));
  });
}

async function loadAndRenderLocked(
  handle,
  generation,
  suppliedContextPromise = null,
  { cacheFirst = true } = {}
) {
  if (!directoryLoadGate.isCurrent(generation)) return { stale: true };
  const sessionSelectionEpoch = ++selectionEpoch;
  const session = {
    handle,
    generation,
    selectionEpoch: sessionSelectionEpoch,
    today: getLocalDate(),
    cacheShown: false,
    liveShown: false,
    todayFile: null,
    todayProbeResolved: false,
    todayProbeAt: 0,
    todayProbeIssue: null,
    cacheContext: null,
    cacheContextReady: false,
    bootstrapArchiveIndex: null,
    cachedReviewData: null,
    cacheDecisionExpired: false,
    contextPromise: cacheContextForHandle(handle, suppliedContextPromise),
    coordinationRole: 'pending',
    reviewMonthsStarted: new Set(),
  };
  activeCoreLoad = session;
  state.persistenceIssue = '';
  const operations = window.MementoDashboardOperations;
  if (!operations || typeof operations.startCacheFirstRefresh !== 'function') {
    throw new Error('Dashboard 快速启动模块未加载');
  }
  const startup = await operations.startCacheFirstRefresh({
    cacheFirst,
    hydrateCache: () => startCacheHydration(session),
    waitForCache: hydrationPromise => waitForStartupCache(session, hydrationPromise),
    hasVisibleContent: () => state.dirHandle === handle
      && ['cache', 'partial', 'fresh', 'shared'].includes(state.recordSource),
    shouldRefresh: () => !(state.dirHandle === handle
      && (state.recordSource === 'fresh' || state.recordSource === 'shared')),
    showWaiting: () => commitCoreRecordView(handle, generation, {
      files: [],
      issues: [],
      issue: '',
      coverage: { enumerationDone: false, discoveredCount: 0, completedCount: 0, complete: false },
    }, {
      source: 'waiting',
      message: '正在并行读取最新记录…',
      todayResolved: false,
      today: session.today,
    }),
    afterFirstPaint: afterFirstDashboardPaint,
    startRefresh: () => scheduleCoreRefresh(session),
    isCurrent: () => directoryLoadGate.isCurrent(generation) && activeCoreLoad === session,
  });
  return {
    stale: Boolean(startup.stale),
    scheduled: Boolean(startup.started),
    cacheShown: Boolean(startup.cacheHit),
  };
}

function loadAndRender(handle, generation, suppliedContextPromise = null, options = {}) {
  return loadAndRenderLocked(handle, generation, suppliedContextPromise, options);
}

if (coreRefreshChannel) {
  coreRefreshChannel.onmessage = event => {
    if (cognitiveDemoState.active) return;
    const data = event.data || {};
    if (data.type === 'selection-changed') {
      void reloadPersistedSelectionAfterBroadcast()
        .catch(error => console.warn('无法加载跨标签页选择的目录', error));
      return;
    }
    const session = activeCoreLoad;
    if (!session) return;
    if (data.type !== 'core-snapshot-committed') return;
    const publication = {
      bindingToken: typeof data.bindingToken === 'string' ? data.bindingToken : '',
      committedAt: Number.isSafeInteger(data.committedAt) ? data.committedAt : -1,
      scanDate: typeof data.scanDate === 'string' ? data.scanDate : '',
    };
    void reloadSharedSnapshot(session, publication)
      .catch(error => console.warn('跨标签页快照更新失败', error));
  };
}

async function reloadPersistedSelectionAfterBroadcast() {
  const flowId = ++selectionFlowId;
  const reloadId = ++persistedSelectionReloadId;
  // Invalidate even a restore/picker flow that has not created a session yet.
  directoryLoadGate.begin();
  retireActiveCoreLoad();
  showGrantUI({
    title: '数据目录正在同步切换',
    help: '另一个 Memento 页面选择了数据目录。旧页面已停用，正在加载当前保存的目录。',
    label: '正在加载…',
    status: '正在读取当前数据目录…',
  });
  setGrantBusy(true);

  try {
    const access = window.MementoDirectoryAccess;
    const storedHandle = access && access.withTimeout
      ? await access.withTimeout(loadHandle, STORAGE_OPERATION_TIMEOUT_MS, '读取当前数据目录')
      : await loadHandle();
    if (!selectionFlowStillCurrent(flowId) || reloadId !== persistedSelectionReloadId) return;
    if (!storedHandle) {
      showAccessResult({ kind: 'missing' });
      return;
    }
    rememberedDirectoryHandle = storedHandle;

    let permission;
    try {
      permission = await queryRead(storedHandle);
    } catch (error) {
      if (selectionFlowStillCurrent(flowId) && reloadId === persistedSelectionReloadId) {
        showAccessResult({ kind: 'permission-check-error', handle: storedHandle, error });
      }
      return;
    }
    if (!selectionFlowStillCurrent(flowId) || reloadId !== persistedSelectionReloadId) return;
    if (permission !== 'granted') {
      showAccessResult({ kind: 'permission-required', handle: storedHandle, permission });
      return;
    }
    await loadSelectedDirectory(storedHandle, null, flowId);
  } catch (error) {
    if (selectionFlowStillCurrent(flowId) && reloadId === persistedSelectionReloadId) {
      showAccessResult({ kind: 'storage-error', error });
    }
  } finally {
    if (selectionFlowStillCurrent(flowId) && reloadId === persistedSelectionReloadId) {
      setGrantBusy(false);
    }
  }
}

window.addEventListener('pagehide', () => {
  retireActiveCoreLoad();
  quarantineDirectoryActions();
  if (coreRefreshChannel) {
    coreRefreshChannel.onmessage = null;
    coreRefreshChannel.close();
    coreRefreshChannel = null;
  }
}, { once: true });
window.addEventListener('pageshow', event => {
  if (event.persisted) window.location.reload();
});

function showAccessResult(result) {
  if (result.handle) rememberedDirectoryHandle = result.handle;

  switch (result.kind) {
    case 'ready':
      return;
    case 'missing':
      rememberedDirectoryHandle = null;
      showGrantUI({
        title: '首次使用需要选择数据目录',
        help: '请选择 Memento 数据目录(默认 <code>~/AISecretary</code>)。Chrome 会在本机记住这个目录;如果询问授权期限,请选择“允许每次访问”。',
        label: '选择数据目录',
        status: '尚未选择数据目录',
        forcePicker: true,
      });
      return;
    case 'permission-required':
      setRegrantUI(
        result.permission,
        result.handle,
        activeCoreLoad && activeCoreLoad.handle === result.handle
          ? activeCoreLoad.contextPromise
          : null
      );
      return;
    case 'storage-error':
      rememberedDirectoryHandle = null;
      console.error('读取目录授权记录失败', result.error);
      const storageTimedOut = result.error && result.error.name === 'TimeoutError';
      showGrantUI({
        title: storageTimedOut ? '浏览器授权记录读取超时' : '无法恢复上次的数据目录',
        help: storageTimedOut
          ? '已经定位到 Chrome 的 IndexedDB 授权记录没有按时返回,不是数据文件过多。可以重新选择 <code>~/AISecretary</code> 尝试覆盖这条记录。'
          : '浏览器本地授权记录读取失败,这不是“从未授权”。请重新选择 <code>~/AISecretary</code>;如果仍失败,页面会显示具体错误。',
        label: '重新选择数据目录',
        status: `授权记录读取失败: ${shortError(result.error)}`,
        tone: 'accent',
        forcePicker: true,
      });
      return;
    case 'permission-check-error':
      console.error('检查目录权限失败', result.error);
      showGrantUI({
        title: '无法确认数据目录权限',
        help: '上次保存的目录句柄无法正常检查。请重新选择 <code>~/AISecretary</code> 建立新的授权。',
        label: '重新选择数据目录',
        status: `权限检查失败: ${shortError(result.error)}`,
        tone: 'accent',
        forcePicker: true,
      });
      return;
    case 'directory-missing':
      void invalidateFastStartCache(
        result.handle,
        result.cacheContextPromise
          || (activeCoreLoad && activeCoreLoad.handle === result.handle
            ? activeCoreLoad.contextPromise
            : null)
      ).catch(cacheError => console.warn('无法清除失效目录缓存', cacheError));
      console.error('保存的数据目录或文件已不存在', result.error);
      showGrantUI({
        title: '原数据目录无法读取',
        help: '目录可能已移动、改名,或其中的文件刚刚被移除。请重新选择当前的 <code>~/AISecretary</code>。',
        label: '重新选择数据目录',
        status: `原目录不可用: ${shortError(result.error)}`,
        tone: 'accent',
        forcePicker: true,
      });
      return;
    case 'read-error':
    default:
      console.error('Memento 数据加载失败', result.error);
      showGrantUI({
        title: '数据目录已授权,但加载失败',
        help: '目录授权仍然存在。可以先重试;若持续失败,请根据下方错误检查对应文件,无需反复重新授权。',
        label: '重试加载',
        status: `数据加载失败: ${shortError(result.error)}`,
        tone: 'accent',
      });
  }
}

async function tryAutoLoad() {
  const flowId = ++selectionFlowId;
  const generation = directoryLoadGate.begin();
  retireActiveCoreLoad();
  setGrantBusy(true);
  try {
    if (!window.MementoDirectoryAccess) throw new Error('目录授权恢复模块未加载');
    // Start the larger snapshot transaction in parallel with the small handle
    // lookup and permission check. The handle path remains independent, so a
    // slow/corrupt snapshot cannot turn into a false "missing permission" UI.
    const bootstrapPromise = dashboardCacheRepository
      ? dashboardCacheRepository.readBootstrap().catch(error => {
          console.warn('无法预读快速启动缓存，将使用实时读取', error);
          return null;
        })
      : Promise.resolve(null);
    let restoredContextPromise = null;
    const result = await window.MementoDirectoryAccess.restore({
      loadHandle: async () => {
        const handle = await loadHandle();
        if (handle && dashboardCacheRepository) {
          restoredContextPromise = bootstrapPromise.then(bootstrap => bootstrap
            ? dashboardCacheRepository.resolveBootstrap(handle, bootstrap)
            : null
          ).catch(error => {
            console.warn('快速启动缓存校验失败，将使用实时读取', error);
            return null;
          });
        }
        return handle;
      },
      queryPermission: queryRead,
      loadDirectory: handle => {
        if (!selectionFlowStillCurrent(flowId) || !directoryLoadGate.isCurrent(generation)) {
          return { stale: true };
        }
        return loadAndRender(handle, generation, restoredContextPromise);
      },
      onStage: stage => {
        if (selectionFlowStillCurrent(flowId) && directoryLoadGate.isCurrent(generation)) {
          setRestoreStage(stage);
        }
      },
      // The access module applies this only to IndexedDB handle recovery;
      // permission and file-system calls are awaited directly.
      timeoutMs: STORAGE_OPERATION_TIMEOUT_MS,
    });
    if (!selectionFlowStillCurrent(flowId) || !directoryLoadGate.isCurrent(generation)) return;
    if (result.kind !== 'ready') directoryLoadGate.invalidate(generation);
    showAccessResult(result);
  } catch (error) {
    if (!selectionFlowStillCurrent(flowId)) return;
    directoryLoadGate.invalidate(generation);
    showAccessResult({ kind: 'read-error', error });
  } finally {
    if (selectionFlowStillCurrent(flowId)) setGrantBusy(false);
  }
}

async function loadSelectedDirectory(
  handle,
  cacheContextPromise = null,
  flowId = selectionFlowId,
  options = {}
) {
  if (!selectionFlowStillCurrent(flowId)) return { ok: false, stale: true, generation: null };
  const generation = directoryLoadGate.begin();
  try {
    setRestoreStage('load-directory');
    const result = await loadAndRender(handle, generation, cacheContextPromise, options);
    if (!selectionFlowStillCurrent(flowId)) {
      directoryLoadGate.invalidate(generation);
      return { ok: false, stale: true, generation };
    }
    return { ok: !result?.stale, stale: Boolean(result?.stale), generation };
  } catch (error) {
    const current = selectionFlowStillCurrent(flowId) && directoryLoadGate.isCurrent(generation);
    directoryLoadGate.invalidate(generation);
    if (!current) return { ok: false, stale: true, generation, error };
    const access = window.MementoDirectoryAccess;
    const kind = access && access.isPermissionError(error)
      ? 'permission-required'
      : access && access.isStaleHandleError(error)
        ? 'directory-missing'
        : 'read-error';
    showAccessResult({ kind, handle, permission: 'prompt', error });
    return { ok: false, stale: false, generation, error };
  }
}

grantBtn.addEventListener('click', async () => {
  if (grantBtn.disabled) return;
  const flowId = ++selectionFlowId;
  // A permission prompt or picker can overlap an earlier restore before that
  // restore created activeCoreLoad. Fence it immediately, not only via UI.
  directoryLoadGate.begin();
  retireActiveCoreLoad();
  quarantineDirectoryActions();
  setGrantBusy(true);

  try {
    // requestPermission/showDirectoryPicker 依赖当前点击的用户激活。
    // 自动恢复阶段已把 handle 缓存在内存,这里不能先等待一次 IndexedDB。
    if (rememberedDirectoryHandle && !forceFolderPicker) {
      let permission;
      try {
        // requestPermission may legitimately wait while the user reads the
        // Chrome prompt. It is not cancellable, so it must not use a timer.
        permission = await requestRead(rememberedDirectoryHandle);
      } catch (error) {
        if (!selectionFlowStillCurrent(flowId)) return;
        if (error.name === 'AbortError') return;
        console.error('请求目录权限失败', error);
        showGrantUI({
          title: '未能发起目录授权',
          help: 'Chrome 没有完成这次权限请求。请再点一次重试;若仍失败,再重新选择数据目录。',
          label: '重试允许访问',
          status: `授权请求失败: ${shortError(error)}`,
          tone: 'accent',
        });
        return;
      }

      if (!selectionFlowStillCurrent(flowId)) return;
      if (permission === 'granted') {
        await loadSelectedDirectory(rememberedDirectoryHandle, null, flowId);
        return;
      }

      // 当前点击的用户激活通常已结束，不在这里接着打开 picker。
      // 只有明确 denied 才清缓存并要求重选；prompt 继续保留句柄。
      setRegrantUI(
        permission,
        rememberedDirectoryHandle,
        activeCoreLoad && activeCoreLoad.handle === rememberedDirectoryHandle
          ? activeCoreLoad.contextPromise
          : null
      );
      return;
    }

    const handle = await pickFolder();
    if (!selectionFlowStillCurrent(flowId)) return;
    rememberedDirectoryHandle = handle;
    forceFolderPicker = false;
    const operations = window.MementoDashboardOperations;
    let preparedSelection = null;
    if (dashboardCacheRepository) {
      try {
        preparedSelection = dashboardCacheRepository.prepareSelection(handle);
      } catch (error) {
        console.warn('快速启动缓存初始化失败，当前目录仍会实时加载', error);
      }
    }
    const notifySelectionPersisted = () => {
      if (coreRefreshChannel) {
        try {
          coreRefreshChannel.postMessage({
            type: 'selection-changed',
            bindingToken: preparedSelection?.binding?.token || '',
          });
        } catch (error) {
          console.warn('无法广播已保存的数据目录', error);
        }
      }
      if (selectionFlowStillCurrent(flowId)
          && state.persistenceIssue
          && activeCoreLoad
          && activeCoreLoad.handle === handle
          && state.dirHandle === handle) {
        state.persistenceIssue = '';
        renderDashboardNotice();
      }
      // BroadcastChannel never echoes to its sender. If this picker became
      // stale while its queued transaction was waiting, reconcile this page
      // with the actual persisted winner as well.
      if (!selectionFlowStillCurrent(flowId)) {
        void reloadPersistedSelectionAfterBroadcast()
          .catch(error => console.warn('无法协调晚到的数据目录保存', error));
      }
    };
    const selection = await operations.loadWhilePersisting(handle, {
      load: currentHandle => loadSelectedDirectory(
        currentHandle,
        preparedSelection ? preparedSelection.contextPromise : null,
        flowId,
        { cacheFirst: false }
      ),
      persist: currentHandle => persistSelectedDirectoryHandle(
        currentHandle,
        preparedSelection,
        notifySelectionPersisted
      ),
    });
    if (!selectionFlowStillCurrent(flowId)) return;
    if (!selection.persistence.ok) {
      console.error('目录已加载，但授权记录未持久化', selection.persistence.error);
      const loadResult = selection.loadResult;
      if (loadResult.ok && directoryLoadGate.isCurrent(loadResult.generation)) {
        const timedOut = selection.persistence.error && selection.persistence.error.name === 'TimeoutError';
        state.persistenceIssue = timedOut
          ? `当前目录已正常加载，但 Chrome 未按时确认保存授权记录；下次打开时可能需重新选择。`
          : `当前目录已正常加载，但授权记录未保存；下次打开时需重新选择。`;
        renderDashboardNotice();
      }
    }
  } catch (error) {
    if (!selectionFlowStillCurrent(flowId)) return;
    if (error.name === 'AbortError') return;
    console.error('选择或保存数据目录失败', error);
    const pickerBlocked = error.name === 'SecurityError';
    showGrantUI({
      title: pickerBlocked ? 'Chrome 未能打开目录选择器' : '数据目录授权未保存',
      help: pickerBlocked
        ? '目录选择器必须由一次有效点击打开。请关闭其他弹窗后再试。'
        : '选择目录后,浏览器未能保存授权记录。请重试;该错误不会再被当成“从未授权”。',
      label: '重试选择数据目录',
      status: `目录授权失败: ${shortError(error)}`,
      tone: 'accent',
      forcePicker: true,
    });
  } finally {
    if (selectionFlowStillCurrent(flowId)) setGrantBusy(false);
  }
});

enterCognitiveDemo();

(() => {
  if (window.parent === window) return;

  const root = document.documentElement;
  let publishQueued = false;

  const publishHeight = () => {
    if (publishQueued) return;
    publishQueued = true;
    requestAnimationFrame(() => {
      publishQueued = false;
      const height = Math.max(
        root.scrollHeight,
        root.offsetHeight,
        document.body.scrollHeight,
        document.body.offsetHeight
      );
      window.parent.postMessage({
        source: 'memento-standalone-preview',
        type: 'content-height',
        height: Math.ceil(height),
      }, '*');
    });
  };

  const setMode = (fullscreen) => {
    root.classList.toggle('memento-embedded-fullscreen', fullscreen);
    root.classList.toggle('memento-embedded-normal', !fullscreen);
    publishHeight();
  };

  window.addEventListener('message', (event) => {
    if (event.source !== window.parent) return;
    if (event.data?.source !== 'memento-product-page' || event.data?.type !== 'display-mode') return;
    setMode(Boolean(event.data.fullscreen));
  });

  window.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    window.parent.postMessage({
      source: 'memento-standalone-preview',
      type: 'exit-fullscreen',
    }, '*');
  });

  if ('ResizeObserver' in window) {
    const observer = new ResizeObserver(publishHeight);
    observer.observe(root);
    observer.observe(document.body);
  }

  setMode(false);
  window.addEventListener('load', publishHeight);
  window.setTimeout(publishHeight, 240);
  window.setTimeout(publishHeight, 900);
})();
