// Transport-neutral read layer for Memento Backend V2.

(function exposeCognitiveV2DataSource(root, factory) {
  const contract = typeof module !== 'undefined' && module.exports
    ? require('./cognitive-v2-contract.js')
    : root.MementoCognitiveV2Contract;
  const api = factory(contract);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MementoCognitiveV2DataSource = api;
})(typeof window !== 'undefined' ? window : globalThis, function createDataSource(contract) {
  'use strict';

  if (!contract) throw new Error('Memento V2 data source requires cognitive-v2-contract');

  const FEATURE_KEY = 'memento.cognitive_backend';
  // The directory picker is rooted at ~/AISecretary.  Runtime state lives in
  // the independently managed Backend V2 data directory installed beneath it.
  const BACKEND_ROOT = ['.memento-backend-v2', 'data'];
  const TOP_PATHS = Object.freeze({
    home: 'projections/home.json',
    timeline: 'projections/timeline.json',
    landscape: 'projections/landscape.json',
    self: 'projections/self.json',
    detailIndex: 'projections/detail-index.json',
  });

  class CognitiveV2DataSourceError extends Error {
    constructor(message, kind = 'invalid') {
      super(message);
      this.name = 'CognitiveV2DataSourceError';
      this.kind = kind;
    }
  }

  function clone(value) { return JSON.parse(JSON.stringify(value)); }

  function validateLocalDate(value, label) {
    const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) throw new CognitiveV2DataSourceError(`${label}不是有效的本地日期`);
    const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
    if (date.getUTCFullYear() !== Number(match[1])
        || date.getUTCMonth() + 1 !== Number(match[2])
        || date.getUTCDate() !== Number(match[3])) {
      throw new CognitiveV2DataSourceError(`${label}不是有效的本地日期`);
    }
    return String(value);
  }

  function resolveTodayView(home, runtimeLocalDate) {
    if (!home || typeof home !== 'object' || !Array.isArray(home.records)
        || !home.today_status || typeof home.today_status !== 'object') {
      throw new CognitiveV2DataSourceError('今日投影缺少必要字段');
    }
    const snapshotLocalDate = validateLocalDate(home.local_date, '快照日期');
    const currentLocalDate = validateLocalDate(runtimeLocalDate, '运行时日期');
    const stale = snapshotLocalDate !== currentLocalDate;
    const view = clone(home);
    if (stale) {
      view.records = [];
      view.today_status = {
        ...view.today_status,
        saved: 0,
        interpreted: 0,
        merged: 0,
        needs_review: 0,
        daily_run_status: 'no_records',
      };
    }
    return Object.freeze({
      home: view,
      stale,
      snapshotLocalDate,
      runtimeLocalDate: currentLocalDate,
    });
  }

  function resolveActivityToday(home, runtimeLocalDate, runtimeActivity) {
    const result = resolveTodayView(home, runtimeLocalDate);
    const daily = runtimeActivity?.daily;
    const view = result.home;
    if (view.records.length) {
      const states = { queued: 'running', completed: 'committed', no_change: 'no_change',
        failed: 'error', retry_wait: 'error', rejected: 'error', conflict: 'stale' };
      view.today_status.daily_run_status = daily?.local_date === runtimeLocalDate
        ? (states[daily.state] || 'not_started') : 'not_started';
    }
    return result;
  }

  function changesInWindow(changes, localDate) {
    validateLocalDate(localDate, '运行时日期');
    const end = Date.parse(`${localDate}T00:00:00Z`);
    return (changes || []).filter(item => {
      const value = String(item.date || item.created_at || '').slice(0, 10);
      try { validateLocalDate(value, '变化日期'); } catch { return false; }
      const stamp = Date.parse(`${value}T00:00:00Z`);
      return stamp <= end && stamp >= end - 6 * 86400000;
    });
  }

  function readFeatureMode(storage = globalThis.localStorage) {
    const stored = storage && typeof storage.getItem === 'function' ? storage.getItem(FEATURE_KEY) : null;
    if (stored === null || stored === '') return 'fixture';
    try { return contract.validateFeatureMode(stored); } catch { return 'fixture'; }
  }

  function writeFeatureMode(mode, storage = globalThis.localStorage) {
    const valid = contract.validateFeatureMode(mode);
    if (!storage || typeof storage.setItem !== 'function') throw new CognitiveV2DataSourceError('浏览器存储不可用');
    storage.setItem(FEATURE_KEY, valid);
    return valid;
  }

  function makeReadApi(validated, options = {}) {
    const manifest = validated.manifest;
    const projections = validated.projections;
    const index = projections[TOP_PATHS.detailIndex];
    const detail = (kind, id) => {
      const entries = index.entries.filter(entry => entry.detail_kind === kind && entry.subject_ref.id === id);
      if (entries.length !== 1) throw new CognitiveV2DataSourceError(`找不到唯一的 ${kind} 详情`, 'not_found');
      return clone(projections[entries[0].path]);
    };
    return Object.freeze({
      mode: options.mode || 'v2_shadow',
      bundleSha256: contract.bundleSha256(validated),
      readProjectionManifest: async () => clone(manifest),
      readHome: async () => clone(projections[TOP_PATHS.home]),
      readTimeline: async requestedRange => {
        const value = projections[TOP_PATHS.timeline];
        if (requestedRange && JSON.stringify(requestedRange) !== JSON.stringify(value.range)) {
          throw new CognitiveV2DataSourceError('请求范围超出已发布 Timeline', 'not_found');
        }
        return clone(value);
      },
      readLandscape: async () => clone(projections[TOP_PATHS.landscape]),
      readSelf: async () => clone(projections[TOP_PATHS.self]),
      readRecordDetail: async id => detail('record', id),
      readResourceDetail: async id => detail('resource', id),
      readThemeDetail: async id => detail('theme', id),
      readSelfInsightDetail: async id => detail('self_insight', id),
      readLegacyView: async () => options.legacyView ? clone(options.legacyView) : null,
      readExternalSession: options.readExternalSession || (async () => {
        throw new CognitiveV2DataSourceError('当前只读 bundle 不包含外部会话', 'not_found');
      }),
      readRunStatus: options.readRunStatus || (async () => {
        throw new CognitiveV2DataSourceError('当前只读 bundle 不包含运行状态', 'not_found');
      }),
      readRecentRunStatuses: options.readRecentRunStatuses || (async () => []),
    });
  }

  function createMemoryDataSource(snapshot, options = {}) {
    const validated = contract.validateProjectionBundleSnapshot(snapshot);
    return makeReadApi(validated, options);
  }

  async function directoryAt(root, path) {
    let current = root;
    for (const segment of path) current = await current.getDirectoryHandle(segment, { create: false });
    return current;
  }

  async function readJson(root, relativePath, maximumBytes = 8 * 1024 * 1024) {
    const parts = relativePath.split('/');
    const name = parts.pop();
    const directory = await directoryAt(root, parts);
    const handle = await directory.getFileHandle(name, { create: false });
    const file = await handle.getFile();
    if (file.size > maximumBytes) throw new CognitiveV2DataSourceError(`${relativePath} 超过读取上限`);
    try { return JSON.parse(await file.text()); } catch (error) {
      throw new CognitiveV2DataSourceError(`${relativePath} 不是合法 JSON: ${error.message}`);
    }
  }

  async function readRuntimeToken(vaultRoot) {
    const backendRoot = await directoryAt(vaultRoot, BACKEND_ROOT);
    const frontendRoot = await backendRoot.getDirectoryHandle('frontend', { create: false });
    const handle = await frontendRoot.getFileHandle('runtime.token', { create: false });
    const file = await handle.getFile();
    if (file.size > 256) throw new CognitiveV2DataSourceError('Runtime token 文件超过读取上限');
    const token = (await file.text()).trim();
    if (token.length < 32 || token.length > 192 || /\s/.test(token)) {
      throw new CognitiveV2DataSourceError('Runtime token 无效', 'authorization');
    }
    return token;
  }

  async function loadCurrentSnapshot(vaultRoot) {
    const backendRoot = await directoryAt(vaultRoot, BACKEND_ROOT);
    let pointer = null;
    let frontendPointer = null;
    let bridge = null;
    try {
      frontendPointer = await readJson(backendRoot, 'frontend/current.json');
    } catch (error) {
      if (!(error && error.name === 'NotFoundError')) throw error;
    }
    if (frontendPointer) {
      if (frontendPointer.kind !== 'memento_frontend_current'
          || !/^frontend\/bridges\/fbr_[0-9a-f]{24}\.json$/.test(frontendPointer.bridge_path || '')
          || !/^prjb_[0-9a-f]{24}$/.test(frontendPointer.bundle_id || '')) {
        throw new CognitiveV2DataSourceError('Frontend current pointer 无效');
      }
      bridge = await readJson(backendRoot, frontendPointer.bridge_path);
      if (contract.sha256Json(bridge) !== frontendPointer.bridge_sha256) {
        throw new CognitiveV2DataSourceError('Frontend bridge pointer hash 已失效');
      }
      pointer = {
        bundle_id: frontendPointer.bundle_id,
        bundle_sha256: frontendPointer.bundle_sha256,
        manifest_path: `projections/bundles/${frontendPointer.bundle_id}/manifest.json`,
      };
    } else {
      pointer = await readJson(backendRoot, 'projections/current.json');
      if (!pointer || pointer.kind !== 'memento_projection_current'
          || !/^projections\/bundles\/prjb_[0-9a-f]{24}\/manifest\.json$/.test(pointer.manifest_path || '')) {
        throw new CognitiveV2DataSourceError('V2 current pointer 无效');
      }
    }
    const manifest = await readJson(backendRoot, pointer.manifest_path);
    if (manifest.bundle_id !== pointer.bundle_id) throw new CognitiveV2DataSourceError('发布指针与 manifest 不一致');
    const projections = {};
    for (const entry of manifest.entries || []) projections[entry.path] = await readJson(backendRoot, `${pointer.manifest_path.replace(/\/manifest\.json$/, '')}/${entry.path}`);
    const snapshot = contract.validateProjectionBundleSnapshot({ manifest, projections });
    if (contract.bundleSha256(snapshot) !== pointer.bundle_sha256) throw new CognitiveV2DataSourceError('current pointer bundle hash 已失效');
    const legacyView = bridge
      ? contract.validateFrontendBridge(bridge, snapshot).legacy_view
      : null;
    return { snapshot, legacyView };
  }

  async function createDirectoryDataSource(vaultRoot, options = {}) {
    const loaded = await loadCurrentSnapshot(vaultRoot);
    return makeReadApi(loaded.snapshot, { ...options, legacyView: loaded.legacyView });
  }

  function resolveDataSource(mode, sources) {
    const valid = contract.validateFeatureMode(mode);
    if (valid === 'fixture') return sources.fixture;
    const source = sources[valid];
    if (!source) throw new CognitiveV2DataSourceError(`${valid} 数据源尚未配置`, 'not_found');
    return source;
  }

  return Object.freeze({
    FEATURE_KEY,
    CognitiveV2DataSourceError,
    readFeatureMode,
    writeFeatureMode,
    createMemoryDataSource,
    createDirectoryDataSource,
    resolveTodayView,
    resolveActivityToday,
    changesInWindow,
    readRuntimeToken,
    loadCurrentSnapshot,
    resolveDataSource,
  });
});
