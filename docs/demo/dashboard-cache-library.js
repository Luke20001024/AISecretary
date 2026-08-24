// Memento · Dashboard Lean v1 fast-start cache
//
// The cache deliberately lives in the existing `aisecretary` version-1
// IndexedDB and `handles` object store. Older Memento versions only read the
// `dir` key and therefore ignore the additional cache records.

(function exposeDashboardCache(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MementoDashboardCache = api;
})(typeof window !== 'undefined' ? window : globalThis, function createDashboardCacheLibrary() {
  'use strict';

  const DB_NAME = 'aisecretary';
  const DB_VERSION = 1;
  const STORE_NAME = 'handles';
  const HANDLE_KEY = 'dir';
  const BINDING_KEY = 'dir-binding';
  const SNAPSHOT_KEY = 'core-snapshot';
  const ARCHIVE_INDEX_KEY = 'archive-index';
  const REVIEW_SNAPSHOT_KEY = 'daily-review-snapshot';
  const CACHE_SCHEMA = 1;
  const MAX_SNAPSHOT_BYTES = 5 * 1024 * 1024;
  // One root daily Markdown file per day, with roughly ten years of headroom.
  // This is a cache eligibility limit only; live directory reads remain
  // unrestricted.
  const MAX_SNAPSHOT_FILES = 3660;
  // Archive cache entries are display metadata only. The limits keep a
  // corrupt or accidentally oversized record from becoming startup work.
  const MAX_ARCHIVE_INDEX_ITEMS = 2000;
  const MAX_ARCHIVE_NAME_LENGTH = 255;
  const MAX_ARCHIVE_TITLE_LENGTH = 512;
  const MAX_ARCHIVE_INDEX_STRING_CHARS = 512 * 1024;
  const MAX_REVIEW_CACHE_FILES = 3660;
  const MAX_REVIEW_CACHE_CHARS = 5 * 1024 * 1024;
  const DAILY_MARKDOWN_RE = /^\d{4}-\d{2}-\d{2}\.md$/;
  const DAILY_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
  const REVIEW_STATE_RE = /^\d{4}-\d{2}-\d{2}\.json$/;
  const SHA256_RE = /^[a-f0-9]{64}$/;

  function defaultOpenDB() {
    return new Promise((resolve, reject) => {
      if (typeof indexedDB === 'undefined') {
        reject(new Error('IndexedDB is unavailable'));
        return;
      }

      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) db.createObjectStore(STORE_NAME);
      };
      request.onsuccess = () => {
        const db = request.result;
        db.onversionchange = () => db.close();
        resolve(db);
      };
      request.onerror = () => reject(request.error || new Error('无法打开 Dashboard 本地存储'));
    });
  }

  function safeClose(db) {
    try {
      if (db && typeof db.close === 'function') db.close();
    } catch {
      // Closing a completed/aborted test or browser connection is best effort.
    }
  }

  async function runStoreTransaction(openDB, mode, start) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      let transaction;
      let result;
      let requestError = null;
      let settled = false;

      const finish = (ok, value) => {
        if (settled) return;
        settled = true;
        safeClose(db);
        if (ok) resolve(value);
        else reject(value);
      };

      const rememberRequestError = request => {
        if (!request) return request;
        const previous = request.onerror;
        request.onerror = event => {
          requestError = request.error || requestError;
          if (typeof previous === 'function') previous.call(request, event);
        };
        return request;
      };

      try {
        transaction = db.transaction(STORE_NAME, mode);
        const store = transaction.objectStore(STORE_NAME);
        start(store, value => { result = value; }, rememberRequestError);
      } catch (error) {
        try {
          if (transaction && typeof transaction.abort === 'function') transaction.abort();
        } catch {
          // The original synchronous error is more useful to the caller.
        }
        finish(false, error);
        return;
      }

      transaction.oncomplete = () => finish(true, result);
      const fail = () => finish(
        false,
        requestError || transaction.error || new Error('Dashboard 本地存储事务失败')
      );
      transaction.onerror = fail;
      transaction.onabort = fail;
    });
  }

  function validToken(value) {
    return typeof value === 'string' && value.length > 0 && value.length <= 128;
  }

  function validTimestamp(value) {
    return Number.isSafeInteger(value) && value >= 0;
  }

  function isDirectoryHandle(value) {
    return Boolean(value
      && typeof value === 'object'
      && value.kind === 'directory'
      && typeof value.isSameEntry === 'function');
  }

  function isFutureSchema(value) {
    return Boolean(value
      && typeof value === 'object'
      && Number.isInteger(value.schema)
      && value.schema > CACHE_SCHEMA);
  }

  function validateBinding(binding) {
    return Boolean(binding
      && typeof binding === 'object'
      && binding.schema === CACHE_SCHEMA
      && validToken(binding.token)
      && isDirectoryHandle(binding.boundHandle)
      && validTimestamp(binding.createdAt));
  }

  function createBinding(handle, randomUUID, now) {
    if (!isDirectoryHandle(handle)) throw new TypeError('目录 binding 需要有效的 FileSystemDirectoryHandle');
    const token = randomUUID();
    const createdAt = now();
    if (!validToken(token)) throw new TypeError('目录 binding token 无效');
    if (!validTimestamp(createdAt)) throw new TypeError('目录 binding 时间无效');
    return {
      schema: CACHE_SCHEMA,
      token,
      boundHandle: handle,
      createdAt,
    };
  }

  function isCompleteRecordResult(recordResult) {
    return Boolean(recordResult
      && Array.isArray(recordResult.files)
      && Array.isArray(recordResult.issues)
      && recordResult.issues.length === 0
      && !recordResult.issue
      && recordResult.coverage
      && recordResult.coverage.complete === true);
  }

  function snapshotFailure(reason) {
    return { ok: false, reason };
  }

  function encodeCompleteSnapshot(bindingToken, recordResult, now = Date.now) {
    if (!validToken(bindingToken)) return snapshotFailure('invalid-binding-token');
    if (!isCompleteRecordResult(recordResult)) return snapshotFailure('incomplete');
    if (typeof recordResult.scanDate !== 'string' || !DAILY_DATE_RE.test(recordResult.scanDate)) {
      return snapshotFailure('invalid-scan-date');
    }
    if (recordResult.files.length > MAX_SNAPSHOT_FILES) return snapshotFailure('too-many-files');

    const files = [];
    const names = new Set();
    let totalBytes = 0;

    for (const file of recordResult.files) {
      if (!file || typeof file !== 'object') return snapshotFailure('invalid-file');
      if (typeof file.name !== 'string' || !DAILY_MARKDOWN_RE.test(file.name)) {
        return snapshotFailure('invalid-file-name');
      }
      if (names.has(file.name)) return snapshotFailure('duplicate-file');
      if (!validTimestamp(file.mtime)) return snapshotFailure('invalid-mtime');
      if (!(file.bytes instanceof ArrayBuffer)) return snapshotFailure('invalid-bytes');

      totalBytes += file.bytes.byteLength;
      if (!Number.isSafeInteger(totalBytes) || totalBytes > MAX_SNAPSHOT_BYTES) {
        return snapshotFailure('too-large');
      }

      names.add(file.name);
      files.push({
        name: file.name,
        mtime: file.mtime,
        // Do not retain a mutable reference owned by the live session model.
        bytes: file.bytes.slice(0),
      });
    }

    const committedAt = now();
    if (!validTimestamp(committedAt)) return snapshotFailure('invalid-commit-time');
    files.sort((a, b) => b.name.localeCompare(a.name));
    return {
      ok: true,
      snapshot: {
        schema: CACHE_SCHEMA,
        complete: true,
        bindingToken,
        committedAt,
        scanDate: recordResult.scanDate,
        fileCount: files.length,
        totalBytes,
        files,
      },
    };
  }

  function validateSnapshot(snapshot, expectedToken) {
    if (isFutureSchema(snapshot)) return snapshotFailure('future-schema');
    if (!snapshot || typeof snapshot !== 'object' || snapshot.schema !== CACHE_SCHEMA) {
      return snapshotFailure('invalid-schema');
    }
    if (snapshot.complete !== true) return snapshotFailure('incomplete');
    if (!validToken(snapshot.bindingToken)) return snapshotFailure('invalid-binding-token');
    if (expectedToken !== undefined && snapshot.bindingToken !== expectedToken) {
      return snapshotFailure('binding-mismatch');
    }
    if (!validTimestamp(snapshot.committedAt)) return snapshotFailure('invalid-commit-time');
    if (typeof snapshot.scanDate !== 'string' || !DAILY_DATE_RE.test(snapshot.scanDate)) {
      return snapshotFailure('invalid-scan-date');
    }
    if (!Number.isInteger(snapshot.fileCount)
        || snapshot.fileCount < 0
        || snapshot.fileCount > MAX_SNAPSHOT_FILES) {
      return snapshotFailure('invalid-file-count');
    }
    if (!Number.isSafeInteger(snapshot.totalBytes)
        || snapshot.totalBytes < 0
        || snapshot.totalBytes > MAX_SNAPSHOT_BYTES) {
      return snapshotFailure('invalid-total-bytes');
    }
    if (!Array.isArray(snapshot.files) || snapshot.files.length !== snapshot.fileCount) {
      return snapshotFailure('file-count-mismatch');
    }

    const names = new Set();
    let totalBytes = 0;
    for (const file of snapshot.files) {
      if (!file || typeof file !== 'object') return snapshotFailure('invalid-file');
      if (typeof file.name !== 'string' || !DAILY_MARKDOWN_RE.test(file.name)) {
        return snapshotFailure('invalid-file-name');
      }
      if (names.has(file.name)) return snapshotFailure('duplicate-file');
      if (!validTimestamp(file.mtime)) return snapshotFailure('invalid-mtime');
      if (!(file.bytes instanceof ArrayBuffer)) return snapshotFailure('invalid-bytes');
      totalBytes += file.bytes.byteLength;
      if (!Number.isSafeInteger(totalBytes) || totalBytes > MAX_SNAPSHOT_BYTES) {
        return snapshotFailure('too-large');
      }
      names.add(file.name);
    }
    if (totalBytes !== snapshot.totalBytes) return snapshotFailure('total-bytes-mismatch');
    return { ok: true };
  }

  function decodeAndValidateSnapshot(snapshot, expectedToken) {
    const validation = validateSnapshot(snapshot, expectedToken);
    if (!validation.ok) return validation;

    const decoder = new TextDecoder();
    const files = snapshot.files.map(file => ({
      name: file.name,
      date: file.name.replace(/\.md$/, ''),
      mtime: file.mtime,
      bytes: file.bytes,
      text: decoder.decode(file.bytes),
    })).sort((a, b) => b.date.localeCompare(a.date));

    return {
      ok: true,
      files,
      committedAt: snapshot.committedAt,
      scanDate: snapshot.scanDate,
      totalBytes: snapshot.totalBytes,
    };
  }

  function reviewSnapshotFailure(reason) {
    return { ok: false, reason };
  }

  function encodeReviewFileList(files, pattern, seen, budget) {
    if (!Array.isArray(files)) return reviewSnapshotFailure('invalid-review-files');
    if (files.length > MAX_REVIEW_CACHE_FILES) return reviewSnapshotFailure('too-many-review-files');
    const encoded = [];
    for (const file of files) {
      if (!file || typeof file !== 'object') return reviewSnapshotFailure('invalid-review-file');
      if (typeof file.name !== 'string' || !pattern.test(file.name)) {
        return reviewSnapshotFailure('invalid-review-file-name');
      }
      if (seen.has(file.name)) return reviewSnapshotFailure('duplicate-review-file');
      if (!validTimestamp(file.mtime)) return reviewSnapshotFailure('invalid-review-mtime');
      if (typeof file.text !== 'string') return reviewSnapshotFailure('invalid-review-text');
      budget.value += file.name.length + file.text.length;
      if (!Number.isSafeInteger(budget.value) || budget.value > MAX_REVIEW_CACHE_CHARS) {
        return reviewSnapshotFailure('review-cache-too-large');
      }
      seen.add(file.name);
      encoded.push({ name: file.name, date: file.name.replace(/\.(?:md|json)$/, ''), mtime: file.mtime, text: file.text });
    }
    encoded.sort((a, b) => b.date.localeCompare(a.date));
    return { ok: true, files: encoded };
  }

  function validateDateMap(values, predicate) {
    if (!values || typeof values !== 'object' || Array.isArray(values)) return false;
    return Object.entries(values).every(([date, value]) => DAILY_DATE_RE.test(date) && predicate(value));
  }

  function encodeReviewSnapshot(bindingToken, payload, now = Date.now) {
    if (!validToken(bindingToken)) return reviewSnapshotFailure('invalid-binding-token');
    if (!payload || typeof payload !== 'object') return reviewSnapshotFailure('invalid-review-payload');
    const budget = { value: 0 };
    const reviewFiles = encodeReviewFileList(payload.reviewFiles || [], DAILY_MARKDOWN_RE, new Set(), budget);
    if (!reviewFiles.ok) return reviewFiles;
    const stateFiles = encodeReviewFileList(payload.stateFiles || [], REVIEW_STATE_RE, new Set(), budget);
    if (!stateFiles.ok) return stateFiles;
    const promptHash = String(payload.promptHash || '');
    if (promptHash && !SHA256_RE.test(promptHash)) return reviewSnapshotFailure('invalid-prompt-hash');
    const sourceHashes = payload.sourceHashes || {};
    if (!validateDateMap(sourceHashes, value => SHA256_RE.test(String(value || '')))) {
      return reviewSnapshotFailure('invalid-source-hashes');
    }
    const sourceMocks = payload.sourceMocks || {};
    if (!validateDateMap(sourceMocks, value => typeof value === 'boolean')) {
      return reviewSnapshotFailure('invalid-source-mocks');
    }
    const committedAt = now();
    if (!validTimestamp(committedAt)) return reviewSnapshotFailure('invalid-commit-time');
    return {
      ok: true,
      snapshot: {
        schema: CACHE_SCHEMA,
        bindingToken,
        committedAt,
        reviewFiles: reviewFiles.files,
        stateFiles: stateFiles.files,
        promptHash,
        sourceHashes: { ...sourceHashes },
        sourceMocks: { ...sourceMocks },
      },
    };
  }

  function decodeAndValidateReviewSnapshot(snapshot, expectedToken) {
    if (isFutureSchema(snapshot)) return reviewSnapshotFailure('future-schema');
    if (!snapshot || typeof snapshot !== 'object' || snapshot.schema !== CACHE_SCHEMA) {
      return reviewSnapshotFailure('invalid-schema');
    }
    if (!validToken(snapshot.bindingToken) || snapshot.bindingToken !== expectedToken) {
      return reviewSnapshotFailure('binding-mismatch');
    }
    if (!validTimestamp(snapshot.committedAt)) return reviewSnapshotFailure('invalid-commit-time');
    const encoded = encodeReviewSnapshot(expectedToken, {
      reviewFiles: snapshot.reviewFiles,
      stateFiles: snapshot.stateFiles,
      promptHash: snapshot.promptHash,
      sourceHashes: snapshot.sourceHashes,
      sourceMocks: snapshot.sourceMocks,
    }, () => snapshot.committedAt);
    if (!encoded.ok) return encoded;
    return {
      ok: true,
      committedAt: snapshot.committedAt,
      reviewFiles: encoded.snapshot.reviewFiles.map(file => ({ ...file })),
      stateFiles: encoded.snapshot.stateFiles.map(file => ({ ...file })),
      promptHash: encoded.snapshot.promptHash,
      sourceHashes: { ...encoded.snapshot.sourceHashes },
      sourceMocks: { ...encoded.snapshot.sourceMocks },
    };
  }

  function archiveIndexFailure(reason) {
    return { ok: false, reason };
  }

  function validArchiveName(value) {
    return typeof value === 'string'
      && value.length > 0
      && value.length <= MAX_ARCHIVE_NAME_LENGTH;
  }

  function validArchiveTitle(value) {
    return typeof value === 'string' && value.length <= MAX_ARCHIVE_TITLE_LENGTH;
  }

  function encodeArchiveIndex(bindingToken, sourceItems, now = Date.now) {
    if (!validToken(bindingToken)) return archiveIndexFailure('invalid-binding-token');
    if (!Array.isArray(sourceItems)) return archiveIndexFailure('invalid-items');
    if (sourceItems.length > MAX_ARCHIVE_INDEX_ITEMS) {
      return archiveIndexFailure('too-many-items');
    }

    const items = [];
    const names = new Set();
    let totalStringChars = 0;
    for (const source of sourceItems) {
      if (!source || typeof source !== 'object') return archiveIndexFailure('invalid-item');
      if (!validArchiveName(source.name)) return archiveIndexFailure('invalid-name');
      if (!validArchiveTitle(source.title)) return archiveIndexFailure('invalid-title');
      if (!validTimestamp(source.mtime)) return archiveIndexFailure('invalid-mtime');
      if (names.has(source.name)) return archiveIndexFailure('duplicate-name');

      totalStringChars += source.name.length + source.title.length;
      if (!Number.isSafeInteger(totalStringChars)
          || totalStringChars > MAX_ARCHIVE_INDEX_STRING_CHARS) {
        return archiveIndexFailure('too-many-string-chars');
      }

      names.add(source.name);
      // Deliberately copy only persistent display metadata. Live handles,
      // HTML, body text, and any future session-only fields cannot enter IDB.
      items.push({ name: source.name, title: source.title, mtime: source.mtime });
    }

    const committedAt = now();
    if (!validTimestamp(committedAt)) return archiveIndexFailure('invalid-commit-time');
    return {
      ok: true,
      index: {
        schema: CACHE_SCHEMA,
        bindingToken,
        committedAt,
        itemCount: items.length,
        items,
      },
    };
  }

  function validateArchiveIndex(index, expectedToken) {
    if (isFutureSchema(index)) return archiveIndexFailure('future-schema');
    if (!index || typeof index !== 'object' || index.schema !== CACHE_SCHEMA) {
      return archiveIndexFailure('invalid-schema');
    }
    if (!validToken(index.bindingToken)) return archiveIndexFailure('invalid-binding-token');
    if (index.bindingToken !== expectedToken) return archiveIndexFailure('binding-mismatch');
    if (!validTimestamp(index.committedAt)) return archiveIndexFailure('invalid-commit-time');
    if (!Number.isInteger(index.itemCount)
        || index.itemCount < 0
        || index.itemCount > MAX_ARCHIVE_INDEX_ITEMS) {
      return archiveIndexFailure('invalid-item-count');
    }
    if (!Array.isArray(index.items) || index.items.length !== index.itemCount) {
      return archiveIndexFailure('item-count-mismatch');
    }

    const allowedKeys = new Set(['name', 'title', 'mtime']);
    const names = new Set();
    let totalStringChars = 0;
    for (const item of index.items) {
      if (!item || typeof item !== 'object') return archiveIndexFailure('invalid-item');
      const itemKeys = Object.keys(item);
      if (itemKeys.length !== allowedKeys.size
          || itemKeys.some(key => !allowedKeys.has(key))) {
        return archiveIndexFailure('invalid-item-shape');
      }
      if (!validArchiveName(item.name)) return archiveIndexFailure('invalid-name');
      if (!validArchiveTitle(item.title)) return archiveIndexFailure('invalid-title');
      if (!validTimestamp(item.mtime)) return archiveIndexFailure('invalid-mtime');
      if (names.has(item.name)) return archiveIndexFailure('duplicate-name');

      totalStringChars += item.name.length + item.title.length;
      if (!Number.isSafeInteger(totalStringChars)
          || totalStringChars > MAX_ARCHIVE_INDEX_STRING_CHARS) {
        return archiveIndexFailure('too-many-string-chars');
      }
      names.add(item.name);
    }
    return { ok: true };
  }

  function decodeAndValidateArchiveIndex(index, expectedToken) {
    const validation = validateArchiveIndex(index, expectedToken);
    if (!validation.ok) return validation;
    return {
      ok: true,
      items: index.items.map(item => ({
        name: item.name,
        title: item.title,
        mtime: item.mtime,
      })),
      committedAt: index.committedAt,
    };
  }

  function createRepository(options = {}) {
    const openDB = options.openDB || defaultOpenDB;
    const randomUUID = options.randomUUID
      || (() => {
        if (!globalThis.crypto || typeof globalThis.crypto.randomUUID !== 'function') {
          throw new Error('crypto.randomUUID is unavailable');
        }
        return globalThis.crypto.randomUUID();
      });
    const now = options.now || Date.now;
    const sameEntry = options.sameEntry || ((left, right) => left.isSameEntry(right));

    function readBootstrap() {
      return runStoreTransaction(openDB, 'readonly', (store, setResult, rememberError) => {
        const bootstrap = {
          handle: null,
          binding: null,
          snapshot: null,
          archiveIndex: null,
          reviewSnapshot: null,
        };
        setResult(bootstrap);

        const handleRequest = rememberError(store.get(HANDLE_KEY));
        const bindingRequest = rememberError(store.get(BINDING_KEY));
        const snapshotRequest = rememberError(store.get(SNAPSHOT_KEY));
        const archiveIndexRequest = rememberError(store.get(ARCHIVE_INDEX_KEY));
        const reviewSnapshotRequest = rememberError(store.get(REVIEW_SNAPSHOT_KEY));
        handleRequest.onsuccess = () => { bootstrap.handle = handleRequest.result || null; };
        bindingRequest.onsuccess = () => { bootstrap.binding = bindingRequest.result || null; };
        snapshotRequest.onsuccess = () => { bootstrap.snapshot = snapshotRequest.result || null; };
        archiveIndexRequest.onsuccess = () => {
          bootstrap.archiveIndex = archiveIndexRequest.result || null;
        };
        reviewSnapshotRequest.onsuccess = () => {
          bootstrap.reviewSnapshot = reviewSnapshotRequest.result || null;
        };
      });
    }

    function replaceSelection(handle, binding) {
      return runStoreTransaction(openDB, 'readwrite', (store, setResult, rememberError) => {
        rememberError(store.put(handle, HANDLE_KEY));
        rememberError(store.put(binding, BINDING_KEY));
        rememberError(store.delete(SNAPSHOT_KEY));
        rememberError(store.delete(ARCHIVE_INDEX_KEY));
        rememberError(store.delete(REVIEW_SNAPSHOT_KEY));
        setResult({ binding });
      });
    }

    function prepareSelection(handle) {
      const binding = createBinding(handle, randomUUID, now);
      let persistence = null;
      let resolveContext;
      const contextPromise = new Promise(resolve => { resolveContext = resolve; });

      function startPersistence() {
        if (persistence) return persistence;

        persistence = replaceSelection(handle, binding);
        // Keep the persistence promise rejecting for the existing UI warning,
        // while the optional cache context resolves fail-closed. Attaching the
        // rejection handler here also avoids a second unhandled rejection when
        // a caller only observes the persistence result.
        persistence.then(
          () => resolveContext({
            handle,
            binding,
            cache: null,
            writable: true,
            reason: 'new-selection',
          }),
          error => resolveContext({
            handle,
            binding: null,
            cache: null,
            writable: false,
            reason: 'persistence-error',
            error,
          })
        );
        return persistence;
      }

      return {
        binding,
        startPersistence,
        contextPromise,
      };
    }

    function invalidateCurrent(expectedToken) {
      if (!validToken(expectedToken)) {
        return Promise.reject(new TypeError('缓存失效需要有效的目录 binding token'));
      }
      const nextToken = randomUUID();
      const createdAt = now();
      if (!validToken(nextToken)) return Promise.reject(new TypeError('目录 binding token 无效'));
      if (!validTimestamp(createdAt)) return Promise.reject(new TypeError('目录 binding 时间无效'));

      return runStoreTransaction(openDB, 'readwrite', (store, setResult, rememberError) => {
        const handleRequest = rememberError(store.get(HANDLE_KEY));
        const bindingRequest = rememberError(store.get(BINDING_KEY));
        let handleReady = false;
        let bindingReady = false;

        const finish = () => {
          if (!handleReady || !bindingReady) return;
          const handle = handleRequest.result || null;
          const currentBinding = bindingRequest.result || null;
          if (!validateBinding(currentBinding) || currentBinding.token !== expectedToken) {
            setResult({ invalidated: false, reason: 'binding-mismatch' });
            return;
          }

          if (!isDirectoryHandle(handle)) {
            setResult({ invalidated: false, reason: 'invalid-handle' });
            return;
          }

          const binding = {
            schema: CACHE_SCHEMA,
            token: nextToken,
            boundHandle: handle,
            createdAt,
          };
          rememberError(store.put(binding, BINDING_KEY));
          rememberError(store.delete(SNAPSHOT_KEY));
          rememberError(store.delete(ARCHIVE_INDEX_KEY));
          rememberError(store.delete(REVIEW_SNAPSHOT_KEY));
          setResult({ invalidated: true, binding });
        };

        handleRequest.onsuccess = () => { handleReady = true; finish(); };
        bindingRequest.onsuccess = () => { bindingReady = true; finish(); };
      });
    }

    function bindingExpectation(binding) {
      if (binding == null) return { kind: 'missing' };
      if (validateBinding(binding)) return { kind: 'token', token: binding.token };
      if (isFutureSchema(binding)) return { kind: 'future' };
      return { kind: 'corrupt-v1' };
    }

    function replaceBindingIfUnchanged(handle, expected) {
      const binding = createBinding(handle, randomUUID, now);
      return runStoreTransaction(openDB, 'readwrite', (store, setResult, rememberError) => {
        const request = rememberError(store.get(BINDING_KEY));
        request.onsuccess = () => {
          const current = request.result || null;
          const unchanged = expected.kind === 'missing'
            ? current == null
            : expected.kind === 'token'
              ? validateBinding(current) && current.token === expected.token
              : expected.kind === 'corrupt-v1'
                ? current != null && !validateBinding(current) && !isFutureSchema(current)
                : false;
          if (!unchanged) {
            setResult({ stored: false, binding: null, reason: 'binding-changed' });
            return;
          }
          rememberError(store.put(binding, BINDING_KEY));
          rememberError(store.delete(SNAPSHOT_KEY));
          rememberError(store.delete(ARCHIVE_INDEX_KEY));
          rememberError(store.delete(REVIEW_SNAPSHOT_KEY));
          setResult({ stored: true, binding });
        };
      });
    }

    function discardSnapshotIfCurrent(expectedToken) {
      return runStoreTransaction(openDB, 'readwrite', (store, setResult, rememberError) => {
        const bindingRequest = rememberError(store.get(BINDING_KEY));
        const snapshotRequest = rememberError(store.get(SNAPSHOT_KEY));
        let bindingReady = false;
        let snapshotReady = false;

        const finish = () => {
          if (!bindingReady || !snapshotReady) return;
          const binding = bindingRequest.result || null;
          const snapshot = snapshotRequest.result || null;
          if (!validateBinding(binding) || binding.token !== expectedToken) {
            setResult({ deleted: false, reason: 'binding-mismatch' });
            return;
          }
          if (isFutureSchema(snapshot)) {
            setResult({ deleted: false, reason: 'future-schema' });
            return;
          }
          rememberError(store.delete(SNAPSHOT_KEY));
          setResult({ deleted: true });
        };

        bindingRequest.onsuccess = () => { bindingReady = true; finish(); };
        snapshotRequest.onsuccess = () => { snapshotReady = true; finish(); };
      });
    }

    function bootstrapFailure(handle, reason) {
      return {
        handle,
        binding: null,
        cache: null,
        archiveIndex: null,
        reviewCache: null,
        writable: false,
        reason,
      };
    }

    async function resolveBootstrap(handle, bootstrap) {
      if (!isDirectoryHandle(handle)) {
        return bootstrapFailure(handle, 'invalid-handle');
      }
      if (!bootstrap || !isDirectoryHandle(bootstrap.handle)) {
        return bootstrapFailure(handle, 'missing-stored-handle');
      }

      let currentDirectory;
      try {
        currentDirectory = await sameEntry(handle, bootstrap.handle);
      } catch {
        return bootstrapFailure(handle, 'identity-error');
      }
      if (!currentDirectory) {
        return bootstrapFailure(handle, 'directory-mismatch');
      }

      const expected = bindingExpectation(bootstrap.binding);
      if (expected.kind === 'future') {
        return bootstrapFailure(handle, 'future-binding-schema');
      }

      let binding = validateBinding(bootstrap.binding) ? bootstrap.binding : null;
      if (binding) {
        let boundDirectory;
        try {
          boundDirectory = await sameEntry(handle, binding.boundHandle);
        } catch {
          return bootstrapFailure(handle, 'identity-error');
        }
        if (!boundDirectory) binding = null;
      }

      if (!binding) {
        const replacement = await replaceBindingIfUnchanged(handle, expected);
        if (!replacement.stored) {
          return bootstrapFailure(handle, replacement.reason);
        }
        return {
          handle,
          binding: replacement.binding,
          cache: null,
          archiveIndex: null,
          reviewCache: null,
          writable: true,
          reason: expected.kind === 'missing' ? 'binding-created' : 'binding-replaced',
        };
      }

      let archiveIndex = null;
      if (bootstrap.archiveIndex) {
        const decodedArchiveIndex = decodeAndValidateArchiveIndex(
          bootstrap.archiveIndex,
          binding.token
        );
        if (decodedArchiveIndex.ok) archiveIndex = decodedArchiveIndex;
      }

      let reviewCache = null;
      if (bootstrap.reviewSnapshot) {
        const decodedReviewSnapshot = decodeAndValidateReviewSnapshot(
          bootstrap.reviewSnapshot,
          binding.token
        );
        if (decodedReviewSnapshot.ok) reviewCache = decodedReviewSnapshot;
      }

      if (!bootstrap.snapshot) {
        return {
          handle,
          binding,
          cache: null,
          archiveIndex,
          reviewCache,
          writable: true,
          reason: 'cache-miss',
        };
      }
      if (isFutureSchema(bootstrap.snapshot)) {
        return {
          handle,
          binding,
          cache: null,
          archiveIndex,
          reviewCache,
          writable: false,
          reason: 'future-snapshot-schema',
        };
      }

      const decoded = decodeAndValidateSnapshot(bootstrap.snapshot, binding.token);
      if (!decoded.ok) {
        // Cleanup is part of cache recovery only. Startup gives validation a
        // short cache-first decision window; cleanup failure must still fall
        // through to live reading instead of blocking the selected directory.
        try {
          await discardSnapshotIfCurrent(binding.token);
        } catch {
          // Cache cleanup failure must not turn a valid directory into an error.
        }
        return {
          handle,
          binding,
          cache: null,
          archiveIndex,
          reviewCache,
          writable: true,
          reason: decoded.reason,
        };
      }

      return {
        handle,
        binding,
        cache: decoded,
        archiveIndex,
        reviewCache,
        writable: true,
        reason: 'cache-hit',
      };
    }

    async function commitComplete(expectedToken, recordResult) {
      const encoded = encodeCompleteSnapshot(expectedToken, recordResult, now);
      if (!encoded.ok) {
        if (encoded.reason !== 'incomplete' && validToken(expectedToken)) {
          try {
            await discardSnapshotIfCurrent(expectedToken);
          } catch {
            // An ineligible cache never affects the already-built live view.
          }
        }
        return { stored: false, reason: encoded.reason };
      }

      return runStoreTransaction(openDB, 'readwrite', (store, setResult, rememberError) => {
        const bindingRequest = rememberError(store.get(BINDING_KEY));
        const snapshotRequest = rememberError(store.get(SNAPSHOT_KEY));
        let bindingReady = false;
        let snapshotReady = false;

        const finish = () => {
          if (!bindingReady || !snapshotReady) return;
          const binding = bindingRequest.result || null;
          const currentSnapshot = snapshotRequest.result || null;
          if (!validateBinding(binding) || binding.token !== expectedToken) {
            setResult({ stored: false, reason: 'binding-mismatch' });
            return;
          }
          // A rolled-back v1 reader must not destroy a snapshot produced by a
          // future schema it does not understand.
          if (isFutureSchema(currentSnapshot)) {
            setResult({ stored: false, reason: 'future-schema' });
            return;
          }
          rememberError(store.put(encoded.snapshot, SNAPSHOT_KEY));
          setResult({ stored: true, snapshot: encoded.snapshot });
        };

        bindingRequest.onsuccess = () => { bindingReady = true; finish(); };
        snapshotRequest.onsuccess = () => { snapshotReady = true; finish(); };
      });
    }

    async function commitReviewSnapshot(expectedToken, payload) {
      const encoded = encodeReviewSnapshot(expectedToken, payload, now);
      if (!encoded.ok) return { stored: false, reason: encoded.reason };

      return runStoreTransaction(openDB, 'readwrite', (store, setResult, rememberError) => {
        const bindingRequest = rememberError(store.get(BINDING_KEY));
        const snapshotRequest = rememberError(store.get(REVIEW_SNAPSHOT_KEY));
        let bindingReady = false;
        let snapshotReady = false;

        const finish = () => {
          if (!bindingReady || !snapshotReady) return;
          const binding = bindingRequest.result || null;
          const currentSnapshot = snapshotRequest.result || null;
          if (!validateBinding(binding) || binding.token !== expectedToken) {
            setResult({ stored: false, reason: 'binding-mismatch' });
            return;
          }
          if (isFutureSchema(currentSnapshot)) {
            setResult({ stored: false, reason: 'future-schema' });
            return;
          }
          rememberError(store.put(encoded.snapshot, REVIEW_SNAPSHOT_KEY));
          setResult({ stored: true, snapshot: encoded.snapshot });
        };

        bindingRequest.onsuccess = () => { bindingReady = true; finish(); };
        snapshotRequest.onsuccess = () => { snapshotReady = true; finish(); };
      });
    }

    function readArchiveIndex(expectedToken) {
      if (!validToken(expectedToken)) {
        return Promise.resolve(archiveIndexFailure('invalid-binding-token'));
      }
      return runStoreTransaction(openDB, 'readonly', (store, setResult, rememberError) => {
        const bindingRequest = rememberError(store.get(BINDING_KEY));
        const indexRequest = rememberError(store.get(ARCHIVE_INDEX_KEY));
        let bindingReady = false;
        let indexReady = false;

        const finish = () => {
          if (!bindingReady || !indexReady) return;
          const binding = bindingRequest.result || null;
          const index = indexRequest.result || null;
          if (isFutureSchema(binding)) {
            setResult(archiveIndexFailure('future-binding-schema'));
            return;
          }
          if (!validateBinding(binding) || binding.token !== expectedToken) {
            setResult(archiveIndexFailure('binding-mismatch'));
            return;
          }
          if (!index) {
            setResult(archiveIndexFailure('cache-miss'));
            return;
          }
          setResult(decodeAndValidateArchiveIndex(index, expectedToken));
        };

        bindingRequest.onsuccess = () => { bindingReady = true; finish(); };
        indexRequest.onsuccess = () => { indexReady = true; finish(); };
      });
    }

    async function commitArchiveIndex(expectedToken, items) {
      const encoded = encodeArchiveIndex(expectedToken, items, now);
      if (!encoded.ok) return { stored: false, reason: encoded.reason };

      return runStoreTransaction(openDB, 'readwrite', (store, setResult, rememberError) => {
        const bindingRequest = rememberError(store.get(BINDING_KEY));
        const indexRequest = rememberError(store.get(ARCHIVE_INDEX_KEY));
        let bindingReady = false;
        let indexReady = false;

        const finish = () => {
          if (!bindingReady || !indexReady) return;
          const binding = bindingRequest.result || null;
          const currentIndex = indexRequest.result || null;
          if (isFutureSchema(binding)) {
            setResult({ stored: false, reason: 'future-binding-schema' });
            return;
          }
          if (!validateBinding(binding) || binding.token !== expectedToken) {
            setResult({ stored: false, reason: 'binding-mismatch' });
            return;
          }
          if (isFutureSchema(currentIndex)) {
            setResult({ stored: false, reason: 'future-schema' });
            return;
          }

          rememberError(store.put(encoded.index, ARCHIVE_INDEX_KEY));
          setResult({
            stored: true,
            index: {
              items: encoded.index.items.map(item => ({ ...item })),
              committedAt: encoded.index.committedAt,
            },
          });
        };

        bindingRequest.onsuccess = () => { bindingReady = true; finish(); };
        indexRequest.onsuccess = () => { indexReady = true; finish(); };
      });
    }

    function deleteArchiveIndex(expectedToken) {
      if (!validToken(expectedToken)) {
        return Promise.resolve({ deleted: false, reason: 'invalid-binding-token' });
      }
      return runStoreTransaction(openDB, 'readwrite', (store, setResult, rememberError) => {
        const bindingRequest = rememberError(store.get(BINDING_KEY));
        const indexRequest = rememberError(store.get(ARCHIVE_INDEX_KEY));
        let bindingReady = false;
        let indexReady = false;

        const finish = () => {
          if (!bindingReady || !indexReady) return;
          const binding = bindingRequest.result || null;
          const currentIndex = indexRequest.result || null;
          if (isFutureSchema(binding)) {
            setResult({ deleted: false, reason: 'future-binding-schema' });
            return;
          }
          if (!validateBinding(binding) || binding.token !== expectedToken) {
            setResult({ deleted: false, reason: 'binding-mismatch' });
            return;
          }
          if (isFutureSchema(currentIndex)) {
            setResult({ deleted: false, reason: 'future-schema' });
            return;
          }
          if (!currentIndex) {
            setResult({ deleted: true, existed: false });
            return;
          }
          rememberError(store.delete(ARCHIVE_INDEX_KEY));
          setResult({ deleted: true, existed: true });
        };

        bindingRequest.onsuccess = () => { bindingReady = true; finish(); };
        indexRequest.onsuccess = () => { indexReady = true; finish(); };
      });
    }

    return {
      commitArchiveIndex,
      commitComplete,
      commitReviewSnapshot,
      deleteArchiveIndex,
      invalidateCurrent,
      prepareSelection,
      readArchiveIndex,
      readBootstrap,
      resolveBootstrap,
    };
  }

  return {
    ARCHIVE_INDEX_KEY,
    BINDING_KEY,
    CACHE_SCHEMA,
    HANDLE_KEY,
    MAX_SNAPSHOT_BYTES,
    MAX_SNAPSHOT_FILES,
    MAX_REVIEW_CACHE_CHARS,
    MAX_REVIEW_CACHE_FILES,
    MAX_ARCHIVE_INDEX_ITEMS,
    MAX_ARCHIVE_INDEX_STRING_CHARS,
    MAX_ARCHIVE_NAME_LENGTH,
    MAX_ARCHIVE_TITLE_LENGTH,
    SNAPSHOT_KEY,
    REVIEW_SNAPSHOT_KEY,
    createRepository,
    decodeAndValidateSnapshot,
    decodeAndValidateReviewSnapshot,
    encodeCompleteSnapshot,
    encodeReviewSnapshot,
    isCompleteRecordResult,
  };
});
