// Memento · Dashboard 可测试操作层
// 把缓存首屏编排、文件读取降级、归档命名和“加载与持久化并行”从 DOM 中拆出。

(function exposeDashboardOperations(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MementoDashboardOperations = api;
})(typeof window !== 'undefined' ? window : globalThis, function createDashboardOperations() {
  'use strict';

  const DAILY_MARKDOWN_RE = /^\d{4}-\d{2}-\d{2}\.md$/;
  const ARCHIVE_HTML_RE = /\.html?$/i;
  const CORE_READ_CONCURRENCY = 4;
  // The initial probe is non-queued: one page owns the four-worker pool while
  // every other page immediately becomes a follower.
  const CORE_REFRESH_LOCK_NAME = 'memento.dashboard.core-refresh.v1';
  // Web Locks are scoped to the extension origin, so every Memento tab must
  // use this exact, stable historical name for directory-selection commits
  // and archive mutations. Keeping the value also protects mixed-version tabs.
  const ARCHIVE_MUTATION_LOCK_NAME = 'memento.archive.mutation';

  function errorKind(error) {
    if (!error) return 'unknown';
    if (error.name === 'NotAllowedError' || error.name === 'SecurityError') return 'permission';
    if (error.name === 'NotFoundError' || error.name === 'InvalidStateError') return 'missing';
    return 'other';
  }

  function entryTimeSeconds(value) {
    const match = String(value || '').match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/);
    if (!match) return -1;

    const hours = Number(match[1]);
    const minutes = Number(match[2]);
    const seconds = Number(match[3] || 0);
    if (hours > 23 || minutes > 59 || seconds > 59) return -1;
    return hours * 3600 + minutes * 60 + seconds;
  }

  function compareEntriesNewestFirst(left, right) {
    const dateOrder = String(right?.date || '').localeCompare(String(left?.date || ''));
    if (dateOrder) return dateOrder;

    const timeOrder = entryTimeSeconds(right?.time) - entryTimeSeconds(left?.time);
    if (timeOrder) return timeOrder;

    // 多条记录可能落在同一分钟；后写入文件的块代表更新的记录。
    const leftIndex = Number.isSafeInteger(left?.sourceIndex) ? left.sourceIndex : -1;
    const rightIndex = Number.isSafeInteger(right?.sourceIndex) ? right.sourceIndex : -1;
    return rightIndex - leftIndex;
  }

  function parseLocalDate(dateString) {
    const match = String(dateString || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) return null;

    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    // Noon avoids the rare local-time transition that can make midnight an
    // ambiguous instant.  Calendar navigation must remain a local-date
    // operation instead of drifting through UTC on DST boundaries.
    const value = new Date(year, month - 1, day, 12, 0, 0, 0);
    if (value.getFullYear() !== year
        || value.getMonth() !== month - 1
        || value.getDate() !== day) {
      return null;
    }
    return value;
  }

  function formatLocalDate(value) {
    return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
  }

  function heatmapLevel(count) {
    if (count <= 0) return 0;
    if (count <= 3) return 1;
    if (count <= 7) return 2;
    if (count <= 15) return 3;
    return 4;
  }

  function buildHeatmapDays(entries, todayDate, selectedDate) {
    const today = parseLocalDate(todayDate);
    if (!today) throw new TypeError('热力条需要有效的 YYYY-MM-DD 今日日期');

    const counts = new Map();
    for (const entry of Array.isArray(entries) ? entries : []) {
      const date = String(entry?.date || '');
      if (!date) continue;
      counts.set(date, (counts.get(date) || 0) + 1);
    }

    const selected = String(selectedDate || '');
    const days = [];
    for (let offset = 89; offset >= 0; offset--) {
      const value = new Date(today.getTime());
      value.setDate(value.getDate() - offset);
      const date = formatLocalDate(value);
      const count = counts.get(date) || 0;
      days.push({
        date,
        count,
        level: heatmapLevel(count),
        selected: date === selected,
      });
    }
    return days;
  }

  function filterEntriesForDate(entries, date, filter = 'all') {
    const targetDate = String(date || '');
    return (Array.isArray(entries) ? entries : [])
      .filter(entry => entry?.date === targetDate
        && (filter === 'all' || entry?.tag === filter))
      .sort(compareEntriesNewestFirst);
  }

  function notifyProgress(callback, detail) {
    if (typeof callback !== 'function') return;
    try {
      callback(detail);
    } catch (error) {
      console.warn('无法更新文件读取进度', error);
    }
  }

  function dailyEntryOrder(todayName) {
    return (left, right) => {
      if (left.name === todayName && right.name !== todayName) return -1;
      if (right.name === todayName && left.name !== todayName) return 1;
      return right.name.localeCompare(left.name);
    };
  }

  function shouldRetryFileSnapshot(error) {
    return Boolean(error && (error.name === 'NotReadableError' || error.name === 'NotFoundError'));
  }

  async function readMarkdownEntry(dirHandle, initialEntry, name, options = {}) {
    // A File becomes unreadable if the underlying file changes after
    // getFile(). Resolve a fresh child handle from the parent directory, then
    // acquire one fresh snapshot inside the same worker slot. Never race a
    // pending request or retry a permission/unknown failure.
    const isCurrent = typeof options.isCurrent === 'function'
      ? options.isCurrent
      : () => true;
    let entry = initialEntry;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        if (!isCurrent()) return null;
        const file = await entry.getFile();
        // `getFile()` and `arrayBuffer()` are separate physical operations.
        // A retired page must not start the latter after the former settles.
        if (!isCurrent()) return null;
        const bytes = await file.arrayBuffer();
        if (!isCurrent()) return null;
        return {
          name,
          date: name.replace(/\.md$/, ''),
          mtime: file.lastModified,
          text: new TextDecoder().decode(bytes),
          bytes,
        };
      } catch (error) {
        if (attempt === 0 && shouldRetryFileSnapshot(error)) {
          // FileSystemDirectoryHandle always exposes getFileHandle. Keeping
          // the guard makes partial test/polyfill implementations fail with
          // the original useful file error rather than an unrelated TypeError.
          if (!dirHandle || typeof dirHandle.getFileHandle !== 'function') throw error;
          if (!isCurrent()) return null;
          entry = await dirHandle.getFileHandle(name);
          if (!isCurrent()) return null;
          continue;
        }
        throw error;
      }
    }
    throw new Error('无法读取每日记录文件');
  }

  async function readTodayMarkdownFile(dirHandle, todayDate, options = {}) {
    const name = `${String(todayDate || '')}.md`;
    if (!DAILY_MARKDOWN_RE.test(name)) {
      throw new TypeError('今日记录直读需要 YYYY-MM-DD 日期');
    }
    if (!dirHandle || typeof dirHandle.getFileHandle !== 'function') {
      throw new TypeError('今日记录直读需要数据目录 handle');
    }

    const isCurrent = typeof options.isCurrent === 'function'
      ? options.isCurrent
      : () => true;
    const staleResult = () => ({ file: null, stale: true });
    if (!isCurrent()) return staleResult();

    let entry;
    try {
      // Deliberately bypass `entries()`: a warm page only needs today's exact
      // child before the complete history scan starts in the background.
      entry = await dirHandle.getFileHandle(name);
    } catch (error) {
      if (!isCurrent()) return staleResult();
      if (error && error.name === 'NotFoundError') {
        return { file: null, missing: true };
      }
      throw error;
    }
    if (!isCurrent()) return staleResult();

    try {
      const file = await readMarkdownEntry(dirHandle, entry, name, { isCurrent });
      if (!file || !isCurrent()) return staleResult();
      return { file, missing: false };
    } catch (error) {
      if (!isCurrent()) return staleResult();
      // The file may disappear during the one allowed snapshot retry. This is
      // a valid "today has no file" result, not a broken root directory scan.
      if (error && error.name === 'NotFoundError') {
        return { file: null, missing: true };
      }
      throw error;
    }
  }

  async function readMarkdownFiles(dirHandle, options = {}) {
    const files = [];
    const issues = [];
    let issue = '';
    let iterator;
    let enumerationDone = false;
    const isCurrent = typeof options.isCurrent === 'function'
      ? options.isCurrent
      : () => true;

    try {
      iterator = dirHandle.entries()[Symbol.asyncIterator]();
    } catch (error) {
      const kind = errorKind(error);
      if (kind === 'permission' || kind === 'missing') throw error;
      return {
        files,
        issues,
        issue: 'Chrome 无法开始扫描每日记录目录。',
        coverage: {
          enumerationDone: false,
          discoveredCount: 0,
          completedCount: 0,
          complete: false,
        },
      };
    }

    const seedFiles = new Map((Array.isArray(options.seedFiles) ? options.seedFiles : [])
      .filter(file => file && typeof file.name === 'string' && DAILY_MARKDOWN_RE.test(file.name))
      .map(file => [file.name, file]));
    const entries = [];
    while (true) {
      if (!isCurrent()) break;
      let next;
      try {
        next = await iterator.next();
      } catch (error) {
        const kind = errorKind(error);
        if (kind === 'permission' || kind === 'missing') throw error;
        issue = 'Chrome 无法继续扫描每日记录目录。';
        break;
      }

      if (next.done) {
        enumerationDone = true;
        break;
      }
      if (!isCurrent()) break;
      const [name, entry] = next.value;
      if (entry.kind !== 'file' || !DAILY_MARKDOWN_RE.test(name)) continue;
      entries.push({ name, entry, seed: seedFiles.get(name) || null });
    }

    const todayName = options.todayDate ? `${options.todayDate}.md` : '';
    entries.sort(dailyEntryOrder(todayName));

    let cursor = 0;
    let completedCount = 0;
    let fatalError = null;

    async function worker() {
      while (true) {
        // Once a root permission failure is observed, no worker may dequeue a
        // replacement. Already-started File System Access promises still have
        // to settle because the browser offers no reliable cancellation.
        if (fatalError || !isCurrent()) return;
        const index = cursor++;
        if (index >= entries.length) return;

        const { name, entry, seed } = entries[index];
        let fileRecord = null;
        try {
          // A direct today probe can seed the complete scan. Enumeration still
          // proves that the file exists, while its bytes are not opened twice.
          const candidate = seed || await readMarkdownEntry(dirHandle, entry, name, { isCurrent });
          if (isCurrent()) {
            fileRecord = candidate;
            files.push(fileRecord);
          }
        } catch (error) {
          const kind = errorKind(error);
          if (kind === 'permission') {
            if (!fatalError) fatalError = error;
          } else {
            issues.push({ name, kind, error });
          }
        } finally {
          completedCount += 1;
          const detail = {
            phase: 'file',
            name,
            count: completedCount,
            completedCount,
            discoveredCount: entries.length,
            ok: Boolean(fileRecord),
          };
          notifyProgress(options.onProgress, detail);
          if (fileRecord) {
            notifyProgress(options.onFile, {
              file: fileRecord,
              isToday: name === todayName,
              completedCount,
              discoveredCount: entries.length,
            });
          }
        }
      }
    }

    const workerCount = Math.min(CORE_READ_CONCURRENCY, entries.length);
    await Promise.all(Array.from({ length: workerCount }, () => worker()));
    // Do not reject early: Promise.all above proves every physical request
    // started by this pool has actually settled before the caller releases its
    // global scan lock.
    if (fatalError) throw fatalError;

    files.sort((a, b) => b.date.localeCompare(a.date));
    issues.sort((a, b) => a.name.localeCompare(b.name));
    const complete = enumerationDone && completedCount === entries.length && issues.length === 0;
    return {
      files,
      issues,
      issue,
      coverage: {
        enumerationDone,
        discoveredCount: entries.length,
        completedCount,
        complete,
      },
    };
  }

  async function coordinateCoreRefresh(lockManager, producer) {
    if (typeof producer !== 'function') throw new TypeError('核心刷新 producer 必须是函数');

    const runLocal = async lockError => {
      const value = await producer({ role: 'local', shared: false });
      return {
        role: 'local',
        shared: false,
        value,
        ...(lockError ? { lockError } : {}),
      };
    };

    if (!lockManager || typeof lockManager.request !== 'function') return runLocal();

    let callbackEntered = false;
    try {
      return await lockManager.request(
        CORE_REFRESH_LOCK_NAME,
        { mode: 'exclusive', ifAvailable: true },
        async lock => {
          callbackEntered = true;
          if (!lock) return { role: 'follower', shared: true };
          const value = await producer({ role: 'leader', shared: true });
          return { role: 'leader', shared: true, value };
        }
      );
    } catch (error) {
      // A failure before the callback proves that no producer was started, so
      // one local attempt is safe. Once entered, retrying would duplicate a
      // potentially still-running File System Access operation.
      if (!callbackEntered) return runLocal(error);
      throw error;
    }
  }

  async function startCacheFirstRefresh(options = {}) {
    const isCurrent = options.isCurrent;
    const showWaiting = options.showWaiting;
    const startRefresh = options.startRefresh;
    if (typeof isCurrent !== 'function') throw new TypeError('缓存优先启动需要 generation 检查函数');
    if (typeof showWaiting !== 'function') throw new TypeError('缓存优先启动需要 waiting 渲染函数');
    if (typeof startRefresh !== 'function') throw new TypeError('缓存优先启动需要刷新函数');

    const staleResult = (cacheHit, cacheError) => ({
      started: false,
      stale: true,
      cacheHit,
      ...(cacheError ? { cacheError } : {}),
    });
    const beginRefresh = (cacheHit, cacheError) => {
      if (!isCurrent()) return staleResult(cacheHit, cacheError);
      const refreshResult = startRefresh();
      return {
        started: true,
        stale: false,
        cacheHit,
        refreshResult,
        ...(cacheError ? { cacheError } : {}),
      };
    };
    const skipRefresh = (cacheHit, cacheError) => ({
      started: false,
      stale: false,
      cacheHit,
      refreshSkipped: true,
      ...(cacheError ? { cacheError } : {}),
    });
    const hasVisibleContent = cacheHit => cacheHit || Boolean(
      typeof options.hasVisibleContent === 'function' && options.hasVisibleContent()
    );
    const shouldRefresh = () => typeof options.shouldRefresh !== 'function'
      || options.shouldRefresh();

    if (!isCurrent()) return staleResult(false);
    if (!options.cacheFirst) {
      if (!hasVisibleContent(false)) showWaiting();
      if (!isCurrent()) return staleResult(false);
      if (!shouldRefresh()) return skipRefresh(false);
      return beginRefresh(false);
    }

    if (typeof options.hydrateCache !== 'function') {
      throw new TypeError('缓存优先启动需要缓存 hydration 函数');
    }

    let cacheHit = false;
    let cacheError = null;
    try {
      const hydrationPromise = Promise.resolve(options.hydrateCache());
      cacheHit = Boolean(options.waitForCache
        ? await options.waitForCache(hydrationPromise)
        : await hydrationPromise);
    } catch (error) {
      // Cache lookup is an optional fast path. A miss and an unavailable cache
      // both fall back to the ordinary waiting view and live refresh.
      cacheError = error;
    }
    if (!isCurrent()) return staleResult(cacheHit, cacheError);

    const visibleContent = hasVisibleContent(cacheHit);
    if (!visibleContent) {
      showWaiting();
      if (!isCurrent()) return staleResult(false, cacheError);
      if (!shouldRefresh()) return skipRefresh(false, cacheError);
      return beginRefresh(false, cacheError);
    }

    if (typeof options.afterFirstPaint !== 'function') {
      throw new TypeError('已有内容后需要显式的首次绘制屏障');
    }
    await options.afterFirstPaint();
    if (!isCurrent()) return staleResult(cacheHit, cacheError);
    if (!shouldRefresh()) return skipRefresh(cacheHit, cacheError);
    return beginRefresh(cacheHit, cacheError);
  }

  function mergeCachedFilesWithToday(cachedFiles, todayFile) {
    const cache = Array.isArray(cachedFiles) ? cachedFiles : [];
    if (!todayFile || typeof todayFile.name !== 'string') return [...cache];
    return cache
      .filter(file => file && file.name !== todayFile.name)
      .concat(todayFile);
  }

  function mergeCachedFilesWithTodayProbe(cachedFiles, probe = {}) {
    const files = Array.isArray(cachedFiles) ? cachedFiles : [];
    const todayName = `${String(probe.todayDate || '')}.md`;
    if (!DAILY_MARKDOWN_RE.test(todayName)) return [...files];
    const cachedToday = files.find(file => file && file.name === todayName) || null;

    if (probe.file) {
      const newestToday = cachedToday
        && Number(cachedToday.mtime) > Number(probe.file.mtime)
        ? cachedToday
        : probe.file;
      return mergeCachedFilesWithToday(files, newestToday);
    }
    if (!probe.resolved) return [...files];
    if (cachedToday && Number(cachedToday.mtime) > Number(probe.probedAt || 0)) {
      return [...files];
    }
    return files.filter(file => file && file.name !== todayName);
  }

  function copyModeForRecordState(options = {}) {
    const source = String(options.recordSource || 'none');
    const rangeDays = Math.max(1, Number(options.rangeDays) || 1);
    if (source === 'fresh' || source === 'shared') return 'fresh';
    if (source === 'partial' && rangeDays <= 1 && options.todayResolved) return 'fresh';
    // A trusted last-known-good view is useful even while Chrome's exact
    // today read is pending. Keep its provenance explicit instead of turning
    // the primary action into an indefinite loading indicator.
    if (source === 'cache' || source === 'partial') return 'visible';
    return 'blocked';
  }

  async function startTodayFirstRefresh(options = {}) {
    const readToday = options.readToday;
    const commitToday = options.commitToday;
    const startHistory = options.startHistory;
    const isCurrent = options.isCurrent;
    if (typeof readToday !== 'function'
        || typeof commitToday !== 'function'
        || typeof startHistory !== 'function'
        || typeof isCurrent !== 'function') {
      throw new TypeError('今日优先刷新需要 read/commit/history/current 边界');
    }
    if (!isCurrent()) return { stale: true, historyStarted: false };

    const todayResult = await readToday();
    if (!isCurrent() || (todayResult && todayResult.stale)) {
      return { stale: true, todayResult, historyStarted: false };
    }
    await commitToday(todayResult);
    if (!isCurrent()) return { stale: true, todayResult, historyStarted: false };

    const historyResult = await startHistory(todayResult);
    return {
      stale: !isCurrent(),
      todayResult,
      historyStarted: true,
      historyResult,
    };
  }

  function isArchiveHtmlName(name) {
    return ARCHIVE_HTML_RE.test(String(name || ''));
  }

  function uniqueArchiveName(requestedName, existingNames) {
    const name = String(requestedName || '');
    if (!isArchiveHtmlName(name)) return '';

    const occupied = new Set([...existingNames || []].map(value => String(value).toLocaleLowerCase()));
    if (!occupied.has(name.toLocaleLowerCase())) return name;

    const match = name.match(/^(.*?)(\.html?)$/i);
    const stem = match[1];
    const extension = match[2];
    let copy = 2;
    let candidate = '';
    do {
      candidate = `${stem} (${copy})${extension}`;
      copy++;
    } while (occupied.has(candidate.toLocaleLowerCase()));
    return candidate;
  }

  function createSerialQueue() {
    let tail = Promise.resolve();
    return task => {
      const result = tail.then(task, task);
      tail = result.then(() => undefined, () => undefined);
      return result;
    };
  }

  function notSupportedError(message) {
    // DOMException is not available in every test/runtime environment.  The
    // name is the interoperable part callers use to distinguish fail-closed
    // behavior from a file-system failure.
    const error = new Error(message);
    error.name = 'NotSupportedError';
    return error;
  }

  async function withArchiveMutationLock(lockManager, task) {
    if (!lockManager || typeof lockManager.request !== 'function') {
      throw notSupportedError('此浏览器不支持安全的跨标签页归档锁');
    }
    if (typeof task !== 'function') throw new TypeError('归档锁任务必须是函数');

    return lockManager.request(
      ARCHIVE_MUTATION_LOCK_NAME,
      { mode: 'exclusive' },
      () => task()
    );
  }

  async function loadWhilePersisting(handle, { load, persist }) {
    const persistencePromise = Promise.resolve()
      .then(() => persist(handle))
      .then(
        value => ({ ok: true, value }),
        error => ({ ok: false, error })
      );

    // 先调用 load，不让 IndexedDB 的写入状态阻塞当前已授权目录。
    const loadResult = await load(handle);
    return { loadResult, persistence: await persistencePromise };
  }

  return {
    ARCHIVE_MUTATION_LOCK_NAME,
    CORE_READ_CONCURRENCY,
    CORE_REFRESH_LOCK_NAME,
    buildHeatmapDays,
    compareEntriesNewestFirst,
    coordinateCoreRefresh,
    copyModeForRecordState,
    createSerialQueue,
    errorKind,
    filterEntriesForDate,
    isArchiveHtmlName,
    loadWhilePersisting,
    mergeCachedFilesWithToday,
    mergeCachedFilesWithTodayProbe,
    readMarkdownFiles,
    readTodayMarkdownFile,
    startCacheFirstRefresh,
    startTodayFirstRefresh,
    uniqueArchiveName,
    withArchiveMutationLock,
  };
});
