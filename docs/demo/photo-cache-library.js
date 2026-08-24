// Memento · cross-tab thumbnail cache
//
// This database is deliberately separate from the `aisecretary` directory
// handle database. A thumbnail schema change must never make an older
// dashboard unable to recover the user's FileSystemDirectoryHandle.

(function exposePhotoCache(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MementoPhotoCache = api;
})(typeof window !== 'undefined' ? window : globalThis, function createPhotoCacheLibrary() {
  'use strict';

  const DB_NAME = 'memento-photo-thumbnails';
  const DB_VERSION = 1;
  const STORE_NAME = 'thumbnails';
  const CACHE_SCHEMA = 1;
  const MAX_ENTRY_BYTES = 512 * 1024;
  const MAX_TOTAL_BYTES = 32 * 1024 * 1024;
  const MAX_ENTRIES = 96;

  function defaultOpenDB() {
    return new Promise((resolve, reject) => {
      if (typeof indexedDB === 'undefined') {
        reject(new Error('IndexedDB is unavailable'));
        return;
      }

      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME, { keyPath: 'key' });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error('无法打开照片缩略图缓存'));
    });
  }

  function validToken(value) {
    return typeof value === 'string' && value.length > 0 && value.length <= 128;
  }

  function validAssetName(value) {
    return typeof value === 'string'
      && value.length > 0
      && value.length <= 255
      && value !== '.'
      && value !== '..'
      && !/[\\/\0]/.test(value);
  }

  function validVariant(value) {
    return typeof value === 'string'
      && /^[a-z0-9][a-z0-9._-]{0,127}$/i.test(value);
  }

  function validTimestamp(value) {
    return Number.isSafeInteger(value) && value >= 0;
  }

  function validSize(value) {
    return Number.isSafeInteger(value) && value >= 0;
  }

  function isBlob(value) {
    return typeof Blob !== 'undefined' && value instanceof Blob;
  }

  function cacheKey(bindingToken, assetName, variant) {
    return JSON.stringify([bindingToken, assetName, variant]);
  }

  function decodeCacheKey(value) {
    if (typeof value !== 'string') return null;
    try {
      const decoded = JSON.parse(value);
      if (!Array.isArray(decoded) || decoded.length !== 3) return null;
      if (!validToken(decoded[0]) || !validAssetName(decoded[1]) || !validVariant(decoded[2])) {
        return null;
      }
      return decoded;
    } catch {
      return null;
    }
  }

  function validateRecord(record, expectedKey) {
    if (!record || typeof record !== 'object') return { ok: false, reason: 'invalid-record' };
    if (!Number.isInteger(record.schema)) return { ok: false, reason: 'invalid-schema' };
    if (record.schema > CACHE_SCHEMA) return { ok: false, reason: 'future-schema', future: true };
    if (record.schema !== CACHE_SCHEMA) return { ok: false, reason: 'invalid-schema' };
    if (!validToken(record.bindingToken)) return { ok: false, reason: 'invalid-binding-token' };
    if (!validAssetName(record.assetName)) return { ok: false, reason: 'invalid-asset-name' };
    if (!validVariant(record.variant)) return { ok: false, reason: 'invalid-variant' };

    const key = cacheKey(record.bindingToken, record.assetName, record.variant);
    if (record.key !== key || (expectedKey !== undefined && key !== expectedKey)) {
      return { ok: false, reason: 'key-mismatch' };
    }
    if (!isBlob(record.blob)) return { ok: false, reason: 'invalid-blob' };
    if (!validSize(record.blobSize)
        || record.blobSize <= 0
        || record.blobSize !== record.blob.size
        || record.blobSize > MAX_ENTRY_BYTES) {
      return { ok: false, reason: 'invalid-blob-size' };
    }
    if (!validSize(record.sourceSize)) return { ok: false, reason: 'invalid-source-size' };
    if (!validTimestamp(record.sourceLastModified)) {
      return { ok: false, reason: 'invalid-source-last-modified' };
    }
    if (!validTimestamp(record.createdAt)
        || !validTimestamp(record.lastAccessedAt)
        || !validTimestamp(record.validatedAt)) {
      return { ok: false, reason: 'invalid-timestamp' };
    }
    return { ok: true };
  }

  function validateIdentity(bindingToken, assetName, variant) {
    return validToken(bindingToken) && validAssetName(assetName) && validVariant(variant);
  }

  function createRepository(options = {}) {
    const openDB = options.openDB || defaultOpenDB;
    const now = options.now || Date.now;
    let dbPromise = null;
    let activeDB = null;
    let memoryEpoch = 0;
    const scheduledTouches = new Set();

    function connection() {
      if (!dbPromise) {
        const openingEpoch = memoryEpoch;
        dbPromise = Promise.resolve()
          .then(() => openDB())
          .then(db => {
            if (!db || typeof db.transaction !== 'function') {
              throw new TypeError('照片缩略图缓存需要有效的 IndexedDB 连接');
            }
            if (openingEpoch !== memoryEpoch) {
              try { if (typeof db.close === 'function') db.close(); } catch {}
              const error = new Error('照片缩略图缓存连接已经失效');
              error.name = 'AbortError';
              throw error;
            }
            activeDB = db;
            if ('onversionchange' in db) {
              db.onversionchange = () => {
                try { db.close(); } catch {}
                if (activeDB === db) {
                  activeDB = null;
                  dbPromise = null;
                  memoryEpoch++;
                  scheduledTouches.clear();
                }
              };
            }
            return db;
          })
          .catch(error => {
            dbPromise = null;
            throw error;
          });
      }
      return dbPromise;
    }

    function transaction(mode, start) {
      return connection().then(db => new Promise((resolve, reject) => {
        let tx;
        let result;
        let requestError = null;
        let settled = false;

        const finish = (ok, value) => {
          if (settled) return;
          settled = true;
          if (ok) resolve(value);
          else reject(value);
        };
        const rememberError = request => {
          if (!request) return request;
          const previous = request.onerror;
          request.onerror = event => {
            requestError = request.error || requestError;
            if (typeof previous === 'function') previous.call(request, event);
          };
          return request;
        };

        try {
          tx = db.transaction(STORE_NAME, mode);
          const store = tx.objectStore(STORE_NAME);
          start(store, value => { result = value; }, rememberError);
        } catch (error) {
          try { if (tx && typeof tx.abort === 'function') tx.abort(); } catch {}
          finish(false, error);
          return;
        }

        tx.oncomplete = () => finish(true, result);
        const fail = () => finish(
          false,
          requestError || tx.error || new Error('照片缩略图缓存事务失败')
        );
        tx.onerror = fail;
        tx.onabort = fail;
      }));
    }

    function readRaw(key) {
      return transaction('readonly', (store, setResult, rememberError) => {
        const request = rememberError(store.get(key));
        request.onsuccess = () => setResult(request.result || null);
      });
    }

    function scheduleCorruptCleanup(key, epoch) {
      Promise.resolve().then(async () => {
        if (epoch !== memoryEpoch) return;
        await transaction('readwrite', (store, setResult, rememberError) => {
          const request = rememberError(store.get(key));
          request.onsuccess = () => {
            const current = request.result || null;
            const validation = validateRecord(current, key);
            if (current && !validation.ok && !validation.future) {
              rememberError(store.delete(key));
              setResult({ deleted: true });
            } else {
              setResult({ deleted: false });
            }
          };
        });
      }).catch(() => {
        // A damaged optional cache must never create an unhandled rejection.
      });
    }

    function scheduleTouch(key, epoch) {
      if (scheduledTouches.has(key)) return;
      scheduledTouches.add(key);
      Promise.resolve().then(async () => {
        try {
          if (epoch !== memoryEpoch) return;
          const accessedAt = now();
          if (!validTimestamp(accessedAt)) return;
          await transaction('readwrite', (store, setResult, rememberError) => {
            const request = rememberError(store.get(key));
            request.onsuccess = () => {
              const current = request.result || null;
              if (epoch !== memoryEpoch || !validateRecord(current, key).ok) {
                setResult({ touched: false });
                return;
              }
              rememberError(store.put({ ...current, lastAccessedAt: accessedAt }));
              setResult({ touched: true });
            };
          });
        } catch {
          // LRU accuracy is best effort. A hit has already been returned.
        } finally {
          scheduledTouches.delete(key);
        }
      });
    }

    async function get(bindingToken, assetName, variant) {
      if (!validateIdentity(bindingToken, assetName, variant)) return null;
      const key = cacheKey(bindingToken, assetName, variant);
      const epoch = memoryEpoch;
      const record = await readRaw(key);
      if (epoch !== memoryEpoch) return null;
      const validation = validateRecord(record, key);
      if (!validation.ok) {
        if (record && !validation.future) scheduleCorruptCleanup(key, epoch);
        return null;
      }

      // Return before the optional readwrite touch settles. The thumbnail Blob
      // is immutable, so callers may safely create an object URL immediately.
      scheduleTouch(key, epoch);
      return { ...record };
    }

    function normalizePutInput(input) {
      if (!input || typeof input !== 'object') return { ok: false, reason: 'invalid-record' };
      const {
        bindingToken,
        assetName,
        variant,
        blob,
        sourceSize,
        sourceLastModified,
      } = input;
      if (!validateIdentity(bindingToken, assetName, variant)) {
        return { ok: false, reason: 'invalid-identity' };
      }
      if (!isBlob(blob) || blob.size <= 0) return { ok: false, reason: 'invalid-blob' };
      if (blob.size > MAX_ENTRY_BYTES) return { ok: false, reason: 'entry-too-large' };
      if (!validSize(sourceSize)) return { ok: false, reason: 'invalid-source-size' };
      if (!validTimestamp(sourceLastModified)) {
        return { ok: false, reason: 'invalid-source-last-modified' };
      }
      const timestamp = now();
      if (!validTimestamp(timestamp)) return { ok: false, reason: 'invalid-timestamp' };
      const validatedAt = input.validatedAt === undefined ? timestamp : input.validatedAt;
      if (!validTimestamp(validatedAt)) return { ok: false, reason: 'invalid-validated-at' };
      const key = cacheKey(bindingToken, assetName, variant);
      return {
        ok: true,
        record: {
          schema: CACHE_SCHEMA,
          key,
          bindingToken,
          assetName,
          variant,
          blob,
          blobSize: blob.size,
          sourceSize,
          sourceLastModified,
          createdAt: timestamp,
          lastAccessedAt: timestamp,
          validatedAt,
        },
      };
    }

    async function put(input) {
      const normalized = normalizePutInput(input);
      if (!normalized.ok) return { stored: false, reason: normalized.reason };
      const entry = normalized.record;

      return transaction('readwrite', (store, setResult, rememberError) => {
        const existingRequest = rememberError(store.get(entry.key));
        existingRequest.onsuccess = () => {
          const existing = existingRequest.result || null;
          if (existing && Number.isInteger(existing.schema) && existing.schema > CACHE_SCHEMA) {
            setResult({ stored: false, reason: 'future-schema' });
            return;
          }
          // Two Tabs can encode the same immutable asset concurrently. Never
          // let a slower job based on an older File snapshot replace a
          // thumbnail already produced from a newer source version.
          if (existing
              && validateRecord(existing, entry.key).ok
              && existing.sourceLastModified > entry.sourceLastModified) {
            setResult({ stored: false, reason: 'stale-source', entry: { ...existing } });
            return;
          }

          const putRequest = rememberError(store.put(entry));
          putRequest.onsuccess = () => {
            const allRequest = rememberError(store.getAll());
            allRequest.onsuccess = () => {
              const currentEntries = [];
              const corruptKeys = [];
              for (const candidate of allRequest.result || []) {
                const validation = validateRecord(candidate);
                if (validation.ok) currentEntries.push(candidate);
                else if (!validation.future && typeof candidate?.key === 'string') {
                  corruptKeys.push(candidate.key);
                }
              }

              for (const key of corruptKeys) rememberError(store.delete(key));
              currentEntries.sort((left, right) =>
                left.lastAccessedAt - right.lastAccessedAt
                || left.createdAt - right.createdAt
                || left.key.localeCompare(right.key)
              );

              let totalBytes = currentEntries.reduce((sum, candidate) => sum + candidate.blobSize, 0);
              let totalEntries = currentEntries.length;
              const evicted = [];
              for (const candidate of currentEntries) {
                if (totalEntries <= MAX_ENTRIES && totalBytes <= MAX_TOTAL_BYTES) break;
                rememberError(store.delete(candidate.key));
                totalEntries--;
                totalBytes -= candidate.blobSize;
                evicted.push(candidate.key);
              }
              setResult({
                stored: !evicted.includes(entry.key),
                reason: evicted.includes(entry.key) ? 'evicted' : 'stored',
                entry: { ...entry },
                evicted,
                totalEntries,
                totalBytes,
              });
            };
          };
        };
      });
    }

    async function deleteEntry(bindingToken, assetName, variant) {
      if (!validateIdentity(bindingToken, assetName, variant)) {
        return { deleted: false, reason: 'invalid-identity' };
      }
      const key = cacheKey(bindingToken, assetName, variant);
      return transaction('readwrite', (store, setResult, rememberError) => {
        const request = rememberError(store.get(key));
        request.onsuccess = () => {
          if (!request.result) {
            setResult({ deleted: false, reason: 'missing' });
            return;
          }
          rememberError(store.delete(key));
          setResult({ deleted: true, key });
        };
      });
    }

    async function deleteBinding(bindingToken) {
      if (!validToken(bindingToken)) return { deleted: 0, reason: 'invalid-binding-token' };
      return transaction('readwrite', (store, setResult, rememberError) => {
        const request = rememberError(store.getAll());
        request.onsuccess = () => {
          let deleted = 0;
          for (const record of request.result || []) {
            const identity = decodeCacheKey(record && record.key);
            if (!identity || identity[0] !== bindingToken) continue;
            rememberError(store.delete(record.key));
            deleted++;
          }
          setResult({ deleted });
        };
      });
    }

    function clearMemory() {
      memoryEpoch++;
      scheduledTouches.clear();
      const db = activeDB;
      activeDB = null;
      dbPromise = null;
      try { if (db && typeof db.close === 'function') db.close(); } catch {}
    }

    return {
      clearMemory,
      delete: deleteEntry,
      deleteBinding,
      get,
      put,
    };
  }

  return {
    CACHE_SCHEMA,
    DB_NAME,
    DB_VERSION,
    MAX_ENTRIES,
    MAX_ENTRY_BYTES,
    MAX_TOTAL_BYTES,
    STORE_NAME,
    cacheKey,
    createRepository,
  };
});
