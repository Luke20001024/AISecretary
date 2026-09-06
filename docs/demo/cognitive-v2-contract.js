// Memento Backend V2 browser contract.
// Validates sealed ProjectionBundle reads before any value reaches the UI.

(function exposeCognitiveV2Contract(root, factory) {
  const home = typeof module !== 'undefined' && module.exports
    ? require('./cognitive-home-library.js')
    : root.MementoCognitiveHome;
  const api = factory(home);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MementoCognitiveV2Contract = api;
})(typeof window !== 'undefined' ? window : globalThis, function createContract(home) {
  'use strict';

  if (!home || typeof home.sha256Hex !== 'function' || typeof home.canonicalJson !== 'function') {
    throw new Error('Memento V2 contract requires cognitive-home-library');
  }

  const MODES = Object.freeze(['fixture', 'v1_adapter', 'v2_shadow', 'v2_live']);
  const MODE_SET = new Set(MODES);
  const SHA_RE = /^[0-9a-f]{64}$/;
  const ID_RE = Object.freeze({
    bundle: /^prjb_[0-9a-f]{24}$/,
    home: /^home_[0-9a-f]{24}$/,
    timeline: /^tln_[0-9a-f]{24}$/,
    landscape: /^lnd_[0-9a-f]{24}$/,
    self: /^self_[0-9a-f]{24}$/,
    detail_index: /^dix_[0-9a-f]{24}$/,
    record_detail: /^rdt_[0-9a-f]{24}$/,
    resource_detail: /^rsd_[0-9a-f]{24}$/,
    theme_detail: /^tdt_[0-9a-f]{24}$/,
    self_insight_detail: /^sdt_[0-9a-f]{24}$/,
    frontend_bridge: /^fbr_[0-9a-f]{24}$/,
  });
  const TOP = Object.freeze({
    'projections/home.json': ['home', 'memento_home_projection', 'memento-home-v2'],
    'projections/timeline.json': ['timeline', 'memento_timeline_projection', 'memento-timeline-v1'],
    'projections/landscape.json': ['landscape', 'memento_landscape_projection', 'memento-landscape-v2'],
    'projections/self.json': ['self', 'memento_self_projection', 'memento-self-v1'],
    'projections/detail-index.json': ['detail_index', 'memento_detail_index_projection', 'memento-detail-index-v1'],
  });
  const DETAIL = Object.freeze({
    record: ['record_detail', 'memento_record_detail_projection', 'memento-record-detail-v1', 'record_ref', /^rec_/],
    resource: ['resource_detail', 'memento_resource_detail_projection', 'memento-resource-detail-v1', 'resource_ref', /^res_/],
    theme: ['theme_detail', 'memento_theme_detail_projection', 'memento-theme-detail-v1', 'theme_ref', /^thm_/],
    self_insight: ['self_insight_detail', 'memento_self_insight_detail_projection', 'memento-self-insight-detail-v1', 'insight_ref', /^sin_/],
  });

  class CognitiveV2ContractError extends Error {
    constructor(message) {
      super(message);
      this.name = 'CognitiveV2ContractError';
    }
  }

  function fail(message) { throw new CognitiveV2ContractError(message); }
  function object(value, name) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) fail(`${name} 必须是对象`);
    return value;
  }
  function array(value, name) {
    if (!Array.isArray(value)) fail(`${name} 必须是数组`);
    return value;
  }
  function string(value, name) {
    if (typeof value !== 'string' || !value) fail(`${name} 必须是非空字符串`);
    return value;
  }
  function exact(value, fields, name) {
    object(value, name);
    const actual = Object.keys(value).sort();
    const expected = [...fields].sort();
    if (actual.length !== expected.length || actual.some((field, index) => field !== expected[index])) {
      fail(`${name} 字段集合不匹配`);
    }
    return value;
  }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function sha256Json(value) { return home.sha256Hex(home.canonicalJson(value)); }
  function validateSha(value, name) {
    if (typeof value !== 'string' || !SHA_RE.test(value)) fail(`${name} 不是 SHA-256`);
  }
  function validateFeatureMode(value) {
    if (!MODE_SET.has(value)) fail('cognitive_backend 模式无效');
    return value;
  }
  function validateRef(value, name, expectedKind, expectedId) {
    exact(value, ['kind', 'id', 'revision', 'revision_sha256'], name);
    if (expectedKind && value.kind !== expectedKind) fail(`${name}.kind 不匹配`);
    string(value.id, `${name}.id`);
    if (expectedId && !expectedId.test(value.id)) fail(`${name}.id 不匹配`);
    if (!Number.isSafeInteger(value.revision) || value.revision < 1) fail(`${name}.revision 无效`);
    validateSha(value.revision_sha256, `${name}.revision_sha256`);
    return value;
  }

  function validateProjectionBase(value, descriptor, manifest, path) {
    const [name, kind, version] = descriptor;
    object(value, path);
    if (value.kind !== kind || value.projection_version !== version) fail(`${path} 类型或版本无效`);
    if (!ID_RE[name].test(value.projection_id || '')) fail(`${path}.projection_id 无效`);
    if (value.bundle_id !== manifest.bundle_id || value.generated_at !== manifest.generated_at
        || value.as_of !== manifest.as_of || value.input_sha256 !== manifest.input_sha256) {
      fail(`${path} 与 manifest 元数据不一致`);
    }
    return value;
  }

  function validateManifest(value) {
    exact(value, [
      'schema_version', 'kind', 'projection_version', 'bundle_id', 'generated_at',
      'as_of', 'input_sha256', 'entries', 'previous_bundle_sha256',
    ], 'ProjectionBundleManifest');
    if (value.schema_version !== '1.0' || value.kind !== 'memento_projection_bundle_manifest'
        || value.projection_version !== 'memento-projection-bundle-v1'
        || !ID_RE.bundle.test(value.bundle_id || '')) fail('ProjectionBundleManifest 版本或身份无效');
    validateSha(value.input_sha256, 'manifest.input_sha256');
    if (value.previous_bundle_sha256 !== null) validateSha(value.previous_bundle_sha256, 'manifest.previous_bundle_sha256');
    const paths = new Set();
    const ids = new Set();
    for (const [index, entry] of array(value.entries, 'manifest.entries').entries()) {
      exact(entry, ['name', 'projection_id', 'path', 'sha256'], `manifest.entries[${index}]`);
      string(entry.path, `manifest.entries[${index}].path`);
      if (!/^projections\/(?!.*(?:^|\/)\.\.(?:\/|$))[A-Za-z0-9._/-]+\.json$/.test(entry.path)) fail('manifest entry path 无效');
      validateSha(entry.sha256, `manifest.entries[${index}].sha256`);
      if (paths.has(entry.path) || ids.has(entry.projection_id)) fail('manifest entry 重复');
      paths.add(entry.path); ids.add(entry.projection_id);
    }
    if (value.entries.length < 5) fail('manifest 缺少顶层投影');
    for (const path of Object.keys(TOP)) if (!paths.has(path)) fail(`manifest 缺少 ${path}`);
    return clone(value);
  }

  function validateProjectionShape(value, entry, manifest) {
    const path = entry.path;
    const top = TOP[path];
    if (top) validateProjectionBase(value, top, manifest, path);
    else {
      const match = /^projections\/details\/(record|resource|theme|self_insight)\/([^/]+)\.json$/.exec(path);
      if (!match) fail(`未知投影路径 ${path}`);
      const descriptor = DETAIL[match[1]];
      validateProjectionBase(value, descriptor, manifest, path);
      validateRef(value[descriptor[3]], `${path}.${descriptor[3]}`, null, descriptor[4]);
      if (value[descriptor[3]].id !== match[2]) fail(`${path} subject 与文件名不一致`);
    }
    if (value.projection_id !== entry.projection_id) fail(`${path} projection_id 与 manifest 不一致`);
    if (sha256Json(value) !== entry.sha256) fail(`${path} SHA-256 不匹配`);
  }

  function validateProjectionSpecifics(projections) {
    const homeProjection = projections['projections/home.json'];
    const timeline = projections['projections/timeline.json'];
    const landscape = projections['projections/landscape.json'];
    const self = projections['projections/self.json'];
    const index = projections['projections/detail-index.json'];
    array(homeProjection.recent_changes, 'home.recent_changes');
    array(homeProjection.resource_entries, 'home.resource_entries');
    array(homeProjection.warnings, 'home.warnings');
    array(timeline.entries, 'timeline.entries');
    array(timeline.change_dates, 'timeline.change_dates');
    array(landscape.peaks, 'landscape.peaks');
    array(landscape.nodes, 'landscape.nodes');
    array(landscape.edges, 'landscape.edges');
    array(self.other_insights, 'self.other_insights');
    array(self.related_theme_refs, 'self.related_theme_refs');
    const indexed = new Set();
    for (const entry of array(index.entries, 'detail-index.entries')) {
      const descriptor = DETAIL[entry.detail_kind];
      if (!descriptor || !projections[entry.path]) fail('detail-index 指向不存在的详情');
      if (indexed.has(entry.path)) fail('detail-index 路径重复');
      indexed.add(entry.path);
      if (entry.projection_id !== projections[entry.path].projection_id
          || entry.sha256 !== sha256Json(projections[entry.path])) fail('detail-index hash 或身份失效');
    }
    const detailPaths = Object.keys(projections).filter(path => path.startsWith('projections/details/'));
    if (detailPaths.length !== indexed.size || detailPaths.some(path => !indexed.has(path))) fail('detail-index 未完整覆盖详情');
    for (const [name, projection] of [['landscape', landscape], ['self', self], ['timeline', timeline]]) {
      const ref = homeProjection[`${name}_ref`];
      if (!ref || ref.projection_id !== projection.projection_id || ref.sha256 !== sha256Json(projection)) {
        fail(`home.${name}_ref 已失效`);
      }
    }
  }

  function validateProjectionBundleSnapshot(snapshot) {
    exact(snapshot, ['manifest', 'projections'], 'ProjectionBundleSnapshot');
    const manifest = validateManifest(snapshot.manifest);
    const projections = object(snapshot.projections, 'snapshot.projections');
    const entries = new Map(manifest.entries.map(entry => [entry.path, entry]));
    const paths = Object.keys(projections).sort();
    const expected = [...entries.keys()].sort();
    if (paths.length !== expected.length || paths.some((path, index) => path !== expected[index])) fail('bundle 文件集合与 manifest 不一致');
    for (const path of paths) validateProjectionShape(projections[path], entries.get(path), manifest);
    validateProjectionSpecifics(projections);
    return clone({ manifest, projections });
  }

  function bundleSha256(snapshot) {
    const value = validateProjectionBundleSnapshot(snapshot);
    return sha256Json({ manifest: value.manifest, projections: value.projections });
  }

  function validateFrontendBridge(value, snapshot) {
    exact(value, [
      'schema_version', 'kind', 'bridge_version', 'bridge_id', 'generated_at',
      'mode', 'bundle_id', 'bundle_sha256', 'legacy_view_sha256', 'legacy_view',
    ], 'FrontendBridge');
    if (value.schema_version !== '1.0' || value.kind !== 'memento_frontend_bridge'
        || value.bridge_version !== 'memento-frontend-bridge-v1'
        || !ID_RE.frontend_bridge.test(value.bridge_id || '')) fail('FrontendBridge 版本或身份无效');
    validateFeatureMode(value.mode);
    if (value.mode === 'fixture') fail('FrontendBridge 不接受 fixture 模式');
    validateSha(value.bundle_sha256, 'FrontendBridge.bundle_sha256');
    validateSha(value.legacy_view_sha256, 'FrontendBridge.legacy_view_sha256');
    const validated = validateProjectionBundleSnapshot(snapshot);
    if (value.bundle_id !== validated.manifest.bundle_id
        || value.bundle_sha256 !== bundleSha256(validated)) fail('FrontendBridge 未绑定当前 bundle');
    const legacy = object(value.legacy_view, 'FrontendBridge.legacy_view');
    if (sha256Json(legacy) !== value.legacy_view_sha256) fail('FrontendBridge legacy view hash 无效');
    if (legacy.mode !== value.mode) fail('FrontendBridge mode 不一致');
    home.validateProjectionPair(legacy.home, legacy.landscape, legacy.landscapeSha256);
    home.validateProjectionAuthority(legacy.home, legacy.landscape, legacy.projectionAuthority);
    return clone(value);
  }

  function normalizeProjectionBundleSnapshot(value) {
    try { return validateProjectionBundleSnapshot(value); } catch { return null; }
  }

  return Object.freeze({
    MODES,
    CognitiveV2ContractError,
    sha256Json,
    bundleSha256,
    validateFrontendBridge,
    validateFeatureMode,
    validateManifest,
    validateProjectionBundleSnapshot,
    normalizeProjectionBundleSnapshot,
  });
});
