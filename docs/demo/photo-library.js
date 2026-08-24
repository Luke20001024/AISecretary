// Memento · 每日第一帧解析与资源加载层
// 文件读取和 blob URL 能力由调用方注入，便于在 Chrome 和 Node 测试中共用。

(function exposePhotoLibrary(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MementoPhotos = api;
})(typeof window !== 'undefined' ? window : globalThis, function createPhotoLibrary() {
  'use strict';

  const FRONTMATTER_RE = /^---\s*\n[\s\S]*?\n---\s*\n/;
  const ENTRY_SPLIT_RE = /\n---\s*\n/;
  const SNAPSHOT_LABEL = '每日第一帧';

  function normalizeNewlines(text) {
    return String(text || '').replace(/\r\n?/g, '\n');
  }

  function splitEntryBlocks(text) {
    return normalizeNewlines(text)
      .replace(FRONTMATTER_RE, '')
      .split(ENTRY_SPLIT_RE)
      .map(block => block.trim())
      .filter(Boolean);
  }

  function fieldValue(block, label) {
    const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const match = block.match(new RegExp(`^>\\s*${escaped}\\s*[:：]\\s*(.*)$`, 'm'));
    return match ? match[1].trim() : '';
  }

  function parseHeading(block) {
    const match = block.match(/^##\s+(.+)$/m);
    if (!match) return null;
    const parts = match[1].split(' · ').map(part => part.trim()).filter(Boolean);
    if (!parts.includes(SNAPSHOT_LABEL)) return null;
    return {
      time: parts[0] || '',
      weekday: parts.find(part => /^周[一二三四五六日]$/.test(part)) || '',
    };
  }

  function normalizeAssetReference(reference) {
    if (!reference) return { assetPath: '', assetName: '', issue: '照片引用缺失' };
    const cleaned = reference.trim().replace(/^<|>$/g, '').split(/[?#]/, 1)[0];
    const match = cleaned.match(/^(?:\.\/)?assets\/([^/\\]+)$/);
    if (!match || !match[1] || match[1] === '.' || match[1] === '..' || match[1].includes('..')) {
      return { assetPath: cleaned, assetName: '', issue: '照片路径无效' };
    }
    return {
      assetPath: `assets/${match[1]}`,
      assetName: match[1],
      issue: '',
    };
  }

  function extractAssetReference(block) {
    const exact = block.match(/!\[\s*每日第一帧\s*\]\(([^)]+)\)/);
    if (exact) return exact[1];

    const images = [...block.matchAll(/!\[[^\]]*\]\(([^)]+)\)/g)];
    const fallback = images.find(match => /daily-portrait/i.test(match[1]));
    return fallback ? fallback[1] : '';
  }

  function parseObserved(value) {
    if (!value) return { observedAt: '', weatherProvider: '' };
    const parts = value.split(/\s+·\s+/).map(part => part.trim()).filter(Boolean);
    if (parts.length < 2) return { observedAt: value, weatherProvider: '' };
    return {
      observedAt: parts.slice(0, -1).join(' · '),
      weatherProvider: parts[parts.length - 1],
    };
  }

  function parseSnapshotBlock(block, fileDate, index) {
    const heading = parseHeading(block);
    if (!heading) return null;

    const issues = [];
    const explicitTime = fieldValue(block, '时间');
    const timeMatch = explicitTime.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?:\s*·\s*(.+))?$/);
    const date = timeMatch ? timeMatch[1] : fileDate;
    const time = timeMatch ? timeMatch[2] : heading.time;
    const timezone = timeMatch && timeMatch[3] ? timeMatch[3].trim() : '';
    if (timeMatch && date !== fileDate) issues.push('记录日期与文件日期不一致');

    const weather = fieldValue(block, '天气') || '暂不可用';
    const observed = parseObserved(fieldValue(block, '天气观测'));
    const source = fieldValue(block, '首条记录来源');
    const asset = normalizeAssetReference(extractAssetReference(block));
    if (asset.issue) issues.push(asset.issue);

    return {
      id: `${fileDate}#snapshot-${index}`,
      fileDate,
      date,
      time,
      timezone,
      weekday: heading.weekday,
      weather,
      weatherStatus: weather === '暂不可用' ? 'unavailable' : 'available',
      observedAt: observed.observedAt,
      weatherProvider: observed.weatherProvider,
      source,
      assetPath: asset.assetPath,
      assetName: asset.assetName,
      issues,
      sortKey: `${date}T${time || '00:00'}`,
    };
  }

  function collectSnapshotRecords(files) {
    const records = [];
    for (const file of files || []) {
      const fileDate = file && file.date ? file.date : '';
      splitEntryBlocks(file && file.text).forEach((block, index) => {
        const record = parseSnapshotBlock(block, fileDate, index);
        if (record) records.push(record);
      });
    }
    return records.sort((a, b) => b.sortKey.localeCompare(a.sortKey));
  }

  function monthKey(record) {
    return /^\d{4}-\d{2}/.test(record.date || '') ? record.date.slice(0, 7) : '';
  }

  function permissionError(error) {
    return Boolean(error && (error.name === 'NotAllowedError' || error.name === 'SecurityError'));
  }

  function createThumbnailer(options = {}) {
    const maxWidth = Number.isSafeInteger(options.maxWidth) && options.maxWidth > 0
      ? options.maxWidth
      : 480;
    const type = typeof options.type === 'string' && options.type
      ? options.type
      : 'image/webp';
    const quality = Number.isFinite(options.quality)
      ? Math.min(1, Math.max(0, options.quality))
      : 0.72;
    const createBitmap = options.createImageBitmap;
    const createCanvas = options.createCanvas;
    const encodeCanvas = options.encodeCanvas;
    const reportError = typeof options.onError === 'function' ? options.onError : () => {};

    return async function createThumbnail(file, record, isCurrent = () => true) {
      // Capability failure is deliberately a quality fallback, not a broken
      // photo. The original local File remains the interoperable source.
      if (!file
          || typeof createBitmap !== 'function'
          || typeof createCanvas !== 'function'
          || typeof encodeCanvas !== 'function') return file;

      let bitmap = null;
      let canvas = null;
      try {
        if (!isCurrent()) return file;
        bitmap = await createBitmap(file, {
          imageOrientation: 'from-image',
          resizeWidth: maxWidth,
          resizeQuality: 'high',
        });
        if (!isCurrent()) return file;
        if (!bitmap
            || !Number.isSafeInteger(bitmap.width)
            || !Number.isSafeInteger(bitmap.height)
            || bitmap.width <= 0
            || bitmap.height <= 0) return file;

        canvas = createCanvas(bitmap.width, bitmap.height);
        const context = canvas && canvas.getContext && canvas.getContext('2d');
        if (!context || typeof context.drawImage !== 'function') return file;
        context.drawImage(bitmap, 0, 0, bitmap.width, bitmap.height);
        if (!isCurrent()) return file;

        const thumbnail = await encodeCanvas(canvas, { type, quality });
        if (!isCurrent()) return file;
        return thumbnail && typeof thumbnail.size === 'number' ? thumbnail : file;
      } catch (error) {
        try { reportError(error, record); } catch {}
        return file;
      } finally {
        if (bitmap && typeof bitmap.close === 'function') bitmap.close();
        if (canvas) {
          try {
            canvas.width = 0;
            canvas.height = 0;
          } catch {}
        }
      }
    };
  }

  function createViewportLoader(options = {}) {
    const load = options.load;
    const isCurrent = typeof options.isCurrent === 'function'
      ? options.isCurrent
      : () => true;
    const onState = typeof options.onState === 'function' ? options.onState : () => {};
    const isTerminalFailure = typeof options.isTerminalFailure === 'function'
      ? options.isTerminalFailure
      : result => Boolean(result && result.permissionLost);
    if (typeof load !== 'function') throw new TypeError('可视区照片加载器需要 load 函数');

    let stopped = false;
    const tracked = new Map();
    let observer = null;

    const setState = (element, item, state, result = null) => {
      const task = tracked.get(element);
      if (!task) return;
      task.state = state;
      try { onState(element, item, state, result); } catch {}
    };

    const request = element => {
      const task = tracked.get(element);
      if (!task || stopped || !isCurrent()) return Promise.resolve({ ok: false, stale: true });
      if (task.promise) return task.promise;
      if (task.state === 'ready') return Promise.resolve({ ok: true, cached: true });

      if (observer && typeof observer.unobserve === 'function') observer.unobserve(element);
      setState(element, task.item, 'loading');
      task.promise = Promise.resolve()
        .then(() => load(task.item, element, () => !stopped && isCurrent()))
        .then(
          result => result || { ok: true },
          error => ({ ok: false, error, reason: '照片暂时无法显示' })
        )
        .then(result => {
          if (stopped || !isCurrent()) return { ...result, ok: false, stale: true };
          setState(element, task.item, result.ok ? 'ready' : 'error', result);
          if (isTerminalFailure(result)) {
            stopped = true;
            if (observer && typeof observer.disconnect === 'function') observer.disconnect();
            observer = null;
            for (const [pendingElement, pendingTask] of tracked.entries()) {
              if (pendingElement === element
                  || pendingTask.state === 'ready'
                  || pendingTask.state === 'error') continue;
              setState(pendingElement, pendingTask.item, 'error', {
                ok: false,
                skipped: true,
                terminal: true,
                reason: result.reason,
              });
            }
          }
          return result;
        });
      return task.promise;
    };

    const handleEntries = entries => {
      if (stopped || !isCurrent()) return;
      for (const entry of entries || []) {
        if (!entry || (!entry.isIntersecting && !(entry.intersectionRatio > 0))) continue;
        void request(entry.target);
      }
    };

    if (typeof options.createObserver === 'function') {
      try { observer = options.createObserver(handleEntries); } catch { observer = null; }
    }

    return {
      observe(element, item) {
        if (!element || stopped || !isCurrent()) return false;
        if (tracked.has(element)) return false;
        tracked.set(element, { item, state: 'idle', promise: null });
        if (observer && typeof observer.observe === 'function') observer.observe(element);
        else void request(element);
        return true;
      },
      request,
      state(element) {
        return tracked.get(element)?.state || 'missing';
      },
      stop() {
        stopped = true;
        if (observer && typeof observer.disconnect === 'function') observer.disconnect();
        observer = null;
        tracked.clear();
      },
    };
  }

  function createAssetLoader(options = {}) {
    const concurrency = Number.isSafeInteger(options.concurrency) && options.concurrency > 0
      ? options.concurrency
      : 3;
    const maxEntries = Number.isSafeInteger(options.maxEntries) && options.maxEntries > 0
      ? options.maxEntries
      : 32;
    const createObjectURL = options.createObjectURL;
    const revokeObjectURL = options.revokeObjectURL;
    const prepareFile = typeof options.prepareFile === 'function'
      ? options.prepareFile
      : async file => file;
    const loadPersistent = typeof options.loadPersistent === 'function'
      ? options.loadPersistent
      : null;
    const storePersistent = typeof options.storePersistent === 'function'
      ? options.storePersistent
      : null;
    const reportPersistentError = typeof options.onPersistentError === 'function'
      ? options.onPersistentError
      : () => {};
    if (typeof createObjectURL !== 'function' || typeof revokeObjectURL !== 'function') {
      throw new TypeError('照片资源加载器需要 object URL 创建与回收函数');
    }

    const cache = new Map();
    const inFlight = new Map();
    const queue = [];
    let cacheEpoch = 0;
    let activeCount = 0;

    function assetKey(record) {
      return String(record && record.assetName || '');
    }

    function getCached(key) {
      if (!cache.has(key)) return null;
      const entry = cache.get(key);
      cache.delete(key);
      cache.set(key, entry);
      return entry;
    }

    function releaseEntry(entry) {
      if (!entry || !entry.url || entry.released) return;
      entry.released = true;
      revokeObjectURL(entry.url);
    }

    function retireEntry(entry) {
      if (!entry || entry.released) return;
      if (entry.pins) {
        entry.releaseWhenUnused = true;
        return;
      }
      releaseEntry(entry);
    }

    function deleteCached(key, expectedUrl = '') {
      const entry = cache.get(key);
      if (!entry || (expectedUrl && entry.url !== expectedUrl)) return false;
      cache.delete(key);
      retireEntry(entry);
      return true;
    }

    function trimCache() {
      while (cache.size > maxEntries) {
        const candidate = [...cache.entries()]
          .find(([key, entry]) => !entry.pins && !inFlight.has(key));
        if (!candidate) return;
        deleteCached(candidate[0]);
      }
    }

    function storeCached(key, entry) {
      const previous = cache.get(key);
      if (previous && previous !== entry) retireEntry(previous);
      cache.delete(key);
      cache.set(key, entry);
      trimCache();
      return entry;
    }

    function pinEntry(entry) {
      if (!entry || cache.get(entry.key) !== entry) return false;
      entry.pins = (entry.pins || 0) + 1;
      return true;
    }

    function unpinEntry(entry) {
      if (!entry) return;
      entry.pins = Math.max(0, (entry.pins || 0) - 1);
      if (!entry.pins && entry.releaseWhenUnused) {
        releaseEntry(entry);
        return;
      }
      trimCache();
    }

    function pumpQueue() {
      while (activeCount < concurrency && queue.length) {
        const queued = queue.shift();
        if (!queued.shouldStart()) {
          queued.resolve({ ok: false, skipped: true });
          continue;
        }

        activeCount++;
        Promise.resolve()
          .then(queued.task)
          .then(
            queued.resolve,
            error => queued.resolve({ ok: false, error, reason: '照片暂时无法显示' })
          )
          .finally(() => {
            activeCount--;
            pumpQueue();
          });
      }
    }

    function schedule(task, shouldStart) {
      return new Promise(resolve => {
        queue.push({ task, shouldStart, resolve });
        pumpQueue();
      });
    }

    async function acquire(record, loadFile, epoch) {
      if (epoch !== cacheEpoch) return { ok: false, stale: true };
      const key = assetKey(record);
      if (!key) return { ok: false, reason: '照片引用缺失' };

      const cached = getCached(key);
      if (cached) {
        pinEntry(cached);
        return { ok: true, asset: cached, cached: true };
      }

      const existing = inFlight.get(key);
      if (existing && existing.epoch === epoch) {
        existing.waiters++;
        try {
          const loaded = await existing.promise;
          if (loaded.ok) pinEntry(loaded.asset);
          return { ...loaded, cached: loaded.ok ? true : loaded.cached };
        } finally {
          existing.waiters--;
          if (existing.waiters === 0 && inFlight.get(key) === existing) inFlight.delete(key);
          trimCache();
        }
      }

      const pending = { epoch, promise: null, waiters: 1 };
      pending.promise = (async () => {
        let file = null;
        let displayFile = null;
        let persistentHit = false;
        let persistentMetadata = null;

        if (loadPersistent) {
          try {
            persistentMetadata = await loadPersistent(record, () => epoch === cacheEpoch);
            displayFile = persistentMetadata && (persistentMetadata.blob || persistentMetadata.file)
              || null;
            persistentHit = Boolean(displayFile);
          } catch (error) {
            try { reportPersistentError(error, record, 'read'); } catch {}
          }
          if (epoch !== cacheEpoch) return { ok: false, stale: true };
        }

        if (!displayFile) {
          try {
            // The second argument lets a multi-step File System Access reader
            // re-check the directory epoch between getDirectory/getHandle/getFile.
            file = await loadFile(record, () => epoch === cacheEpoch);
          } catch (error) {
            if (epoch !== cacheEpoch) return { ok: false, stale: true };
            return {
              ok: false,
              error,
              permissionLost: permissionError(error),
              reason: permissionError(error) ? '访问权限已失效' : '照片文件不存在',
            };
          }

          if (epoch !== cacheEpoch) return { ok: false, stale: true };

          const alreadyCached = getCached(key);
          if (alreadyCached) return { ok: true, asset: alreadyCached, cached: true };

          try {
            displayFile = await prepareFile(file, record, () => epoch === cacheEpoch);
          } catch (error) {
            if (epoch !== cacheEpoch) return { ok: false, stale: true };
            return { ok: false, error, reason: '图片处理失败' };
          }
        }
        if (epoch !== cacheEpoch) return { ok: false, stale: true };
        if (!displayFile) return { ok: false, reason: '图片处理失败' };

        let url;
        try {
          url = createObjectURL(displayFile);
        } catch (error) {
          return { ok: false, error, reason: '图片无法显示' };
        }
        if (epoch !== cacheEpoch) {
          revokeObjectURL(url);
          return { ok: false, stale: true };
        }

        const entry = storeCached(key, {
          key,
          url,
          size: Number(displayFile && displayFile.size) || 0,
          sourceSize: Number(file && file.size)
            || Number(persistentMetadata && persistentMetadata.sourceSize)
            || 0,
          lastModified: Number(file && file.lastModified)
            || Number(persistentMetadata && persistentMetadata.sourceLastModified)
            || 0,
          persistentHit,
          pins: 0,
          released: false,
          releaseWhenUnused: false,
        });

        // Rendering must never wait for IndexedDB writes. Only a genuinely
        // derived Blob is eligible; a thumbnail failure that returned the
        // original File remains a one-Tab fallback and is never persisted.
        if (!persistentHit && storePersistent && file && displayFile !== file) {
          void Promise.resolve()
            .then(() => storePersistent(
              displayFile,
              file,
              record,
              () => epoch === cacheEpoch
            ))
            .catch(error => {
              try { reportPersistentError(error, record, 'write'); } catch {}
            });
        }
        return { ok: true, asset: entry, cached: false };
      })();
      inFlight.set(key, pending);

      try {
        const loaded = await pending.promise;
        if (loaded.ok) pinEntry(loaded.asset);
        return loaded;
      } finally {
        pending.waiters--;
        if (pending.waiters === 0 && inFlight.get(key) === pending) inFlight.delete(key);
        trimCache();
      }
    }

    async function loadBatch(items, batchOptions = {}) {
      const list = Array.from(items || []);
      const loadFile = batchOptions.loadFile;
      const onReady = typeof batchOptions.onReady === 'function'
        ? batchOptions.onReady
        : async () => ({ ok: true });
      const isCurrent = typeof batchOptions.isCurrent === 'function'
        ? batchOptions.isCurrent
        : () => true;
      const canStart = typeof batchOptions.canStart === 'function'
        ? batchOptions.canStart
        : () => true;
      if (typeof loadFile !== 'function') throw new TypeError('照片批次需要 loadFile');

      const epoch = cacheEpoch;
      let permissionLost = false;
      return Promise.all(list.map((record, index) => schedule(async () => {
        const loaded = await acquire(record, loadFile, epoch);
        try {
          if (loaded.permissionLost) permissionLost = true;
          if (!loaded.ok || epoch !== cacheEpoch || !isCurrent()) {
            return !loaded.ok ? loaded : { ...loaded, ok: false, stale: true };
          }

          const rendered = await onReady(loaded.asset, record, index);
          if (rendered && rendered.ok === false) {
            return { ...rendered, asset: loaded.asset, cached: loaded.cached };
          }
          return { ok: true, asset: loaded.asset, cached: loaded.cached };
        } catch (error) {
          return { ok: false, error, asset: loaded.asset, reason: '图片无法显示' };
        } finally {
          if (loaded.ok) unpinEntry(loaded.asset);
        }
      }, () => epoch === cacheEpoch && !permissionLost && isCurrent() && canStart())));
    }

    function clear() {
      cacheEpoch++;
      inFlight.clear();
      [...cache.keys()].forEach(key => deleteCached(key));
      pumpQueue();
    }

    return {
      activeCount: () => activeCount,
      cacheSize: () => cache.size,
      clear,
      deleteAsset(record, expectedUrl = '') {
        return deleteCached(assetKey(record), expectedUrl);
      },
      loadBatch,
    };
  }

  return {
    collectSnapshotRecords,
    createAssetLoader,
    createThumbnailer,
    createViewportLoader,
    monthKey,
    normalizeAssetReference,
    parseSnapshotBlock,
    splitEntryBlocks,
  };
});
