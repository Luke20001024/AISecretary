'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const dataSources = require('../chrome-newtab/cognitive-v2-data-source.js');
const actions = require('../chrome-newtab/cognitive-v2-actions.js');

const fixture = JSON.parse(fs.readFileSync(
  path.join(__dirname, 'fixtures', 'cognitive-v2-bundle.json'), 'utf8'
));

function storage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: key => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, value),
  };
}

function directoryTree(files) {
  const root = {};
  for (const [filePath, value] of Object.entries(files)) {
    const parts = filePath.split('/');
    const name = parts.pop();
    let current = root;
    for (const part of parts) current = current[part] ||= {};
    current[name] = JSON.stringify(value);
  }
  function handle(node) {
    return {
      async getDirectoryHandle(name) {
        const value = node[name];
        if (!value || typeof value === 'string') throw Object.assign(new Error('missing'), { name: 'NotFoundError' });
        return handle(value);
      },
      async getFileHandle(name) {
        const value = node[name];
        if (typeof value !== 'string') throw Object.assign(new Error('missing'), { name: 'NotFoundError' });
        return { getFile: async () => ({ size: Buffer.byteLength(value), text: async () => value }) };
      },
    };
  }
  return handle(root);
}

function directoryFixture() {
  const manifest = fixture.snapshot.manifest;
  const bundleBase = `projections/bundles/${manifest.bundle_id}`;
  const bridge = fixture.bridge;
  const bridgePath = `frontend/bridges/${bridge.bridge_id}.json`;
  const files = {
    '.memento-backend-v2/data/projections/current.json': {
      kind: 'memento_projection_current',
      bundle_id: manifest.bundle_id,
      bundle_sha256: fixture.bundle_sha256,
      manifest_path: `${bundleBase}/manifest.json`,
    },
    [`.memento-backend-v2/data/${bundleBase}/manifest.json`]: manifest,
    '.memento-backend-v2/data/frontend/current.json': {
      kind: 'memento_frontend_current',
      bridge_id: bridge.bridge_id,
      bridge_sha256: require('../chrome-newtab/cognitive-v2-contract.js').sha256Json(bridge),
      bridge_path: bridgePath,
      bundle_id: manifest.bundle_id,
      bundle_sha256: fixture.bundle_sha256,
    },
    [`.memento-backend-v2/data/${bridgePath}`]: bridge,
  };
  for (const [projectionPath, value] of Object.entries(fixture.snapshot.projections)) {
    files[`.memento-backend-v2/data/${bundleBase}/${projectionPath}`] = value;
  }
  return directoryTree(files);
}

function directoryFixtureDuringPublish() {
  const oldManifest = fixture.snapshot.manifest;
  const oldBundleBase = `projections/bundles/${oldManifest.bundle_id}`;
  const bridge = fixture.bridge;
  const bridgePath = `frontend/bridges/${bridge.bridge_id}.json`;
  const files = {
    '.memento-backend-v2/data/projections/current.json': {
      kind: 'memento_projection_current',
      bundle_id: 'prjb_ffffffffffffffffffffffff',
      bundle_sha256: 'f'.repeat(64),
      manifest_path: 'projections/bundles/prjb_ffffffffffffffffffffffff/manifest.json',
    },
    [`.memento-backend-v2/data/${oldBundleBase}/manifest.json`]: oldManifest,
    '.memento-backend-v2/data/frontend/current.json': {
      kind: 'memento_frontend_current',
      bridge_id: bridge.bridge_id,
      bridge_sha256: require('../chrome-newtab/cognitive-v2-contract.js').sha256Json(bridge),
      bridge_path: bridgePath,
      bundle_id: oldManifest.bundle_id,
      bundle_sha256: fixture.bundle_sha256,
    },
    [`.memento-backend-v2/data/${bridgePath}`]: bridge,
  };
  for (const [projectionPath, value] of Object.entries(fixture.snapshot.projections)) {
    files[`.memento-backend-v2/data/${oldBundleBase}/${projectionPath}`] = value;
  }
  return directoryTree(files);
}

async function main() {
  const empty = storage();
  assert.equal(dataSources.readFeatureMode(empty), 'fixture');
  assert.equal(dataSources.writeFeatureMode('v2_shadow', empty), 'v2_shadow');
  assert.equal(dataSources.readFeatureMode(empty), 'v2_shadow');
  const invalid = storage({ [dataSources.FEATURE_KEY]: 'deepseek' });
  assert.equal(dataSources.readFeatureMode(invalid), 'fixture', 'unknown flags fail closed');

  const originalHome = fixture.legacy_view.home;
  const currentDay = dataSources.resolveTodayView(originalHome, '2026-08-18');
  assert.equal(currentDay.stale, false);
  assert.equal(currentDay.home.records.length, 1);
  assert.equal(currentDay.home.today_status.saved, 1);
  const nextDay = dataSources.resolveTodayView(originalHome, '2026-08-19');
  assert.equal(nextDay.stale, true);
  assert.equal(nextDay.snapshotLocalDate, '2026-08-18');
  assert.equal(nextDay.runtimeLocalDate, '2026-08-19');
  assert.deepEqual(nextDay.home.records, []);
  assert.deepEqual(nextDay.home.today_status, {
    ...originalHome.today_status,
    saved: 0,
    interpreted: 0,
    merged: 0,
    needs_review: 0,
    daily_run_status: 'no_records',
  });
  assert.equal(originalHome.records.length, 1, '日期视图不得改写已发布快照');
  const nextDayPublishedHome = JSON.parse(JSON.stringify(originalHome));
  nextDayPublishedHome.local_date = '2026-08-19';
  nextDayPublishedHome.records[0].captured_at = '2026-08-19T09:05:00+08:00';
  const nextDayWithRecord = dataSources.resolveTodayView(nextDayPublishedHome, '2026-08-19');
  assert.equal(nextDayWithRecord.stale, false);
  assert.equal(nextDayWithRecord.home.records.length, 1,
    '当新日期投影发布后，今日时间河必须立即恢复该日记录');
  assert.throws(() => dataSources.resolveTodayView(originalHome, '2026-02-30'), /本地日期/);

  const source = dataSources.createMemoryDataSource(fixture.snapshot, {
    mode: 'v2_shadow', legacyView: fixture.legacy_view,
  });
  assert.equal(source.bundleSha256, fixture.bundle_sha256);
  assert.equal((await source.readProjectionManifest()).bundle_id, fixture.snapshot.manifest.bundle_id);
  const timeline = await source.readTimeline();
  assert.equal(timeline.entries.length, 6);
  assert.deepEqual(await source.readTimeline(timeline.range), timeline);
  await assert.rejects(() => source.readTimeline({ start: '2026-01-01' }), /超出/);
  const recordId = timeline.entries[0].record_ref.id;
  const record = await source.readRecordDetail(recordId);
  assert.equal(record.record_ref.id, recordId);
  record.source.source_app = 'caller mutation';
  assert.notEqual((await source.readRecordDetail(recordId)).source.source_app, 'caller mutation');
  const landscape = await source.readLandscape();
  assert.equal((await source.readThemeDetail(landscape.peaks[0].theme_ref.id)).theme_ref.id,
    landscape.peaks[0].theme_ref.id);
  const self = await source.readSelf();
  assert.equal((await source.readSelfInsightDetail(self.primary_insight.insight_ref.id)).insight_ref.id,
    self.primary_insight.insight_ref.id);
  assert.equal((await source.readLegacyView()).mode, 'v2_shadow');
  assert.equal(dataSources.resolveDataSource('v2_shadow', { v2_shadow: source }), source);
  assert.throws(() => dataSources.resolveDataSource('v2_live', {}), /尚未配置/);

  const directorySource = await dataSources.createDirectoryDataSource(directoryFixture(), { mode: 'v2_shadow' });
  assert.equal(directorySource.bundleSha256, fixture.bundle_sha256);
  assert.equal((await directorySource.readLegacyView()).mode, 'v2_shadow');
  const inFlightSource = await dataSources.createDirectoryDataSource(
    directoryFixtureDuringPublish(), { mode: 'v2_live' }
  );
  assert.equal(inFlightSource.bundleSha256, fixture.bundle_sha256,
    'frontend pointer keeps the prior complete personal snapshot during publish');

  const tokenValue = 'runtime-token-'.padEnd(40, 'x');
  const tokenRoot = {
    async getDirectoryHandle(name) {
      assert.equal(name, '.memento-backend-v2');
      return {
        async getDirectoryHandle(child) {
          assert.equal(child, 'data');
          return {
            async getDirectoryHandle(grandchild) {
              assert.equal(grandchild, 'frontend');
              return {
                async getFileHandle(fileName) {
                  assert.equal(fileName, 'runtime.token');
                  return { getFile: async () => ({ size: tokenValue.length, text: async () => `${tokenValue}\n` }) };
                },
              };
            },
          };
        },
      };
    },
  };
  assert.equal(await dataSources.readRuntimeToken(tokenRoot), tokenValue);

  const readOnly = actions.createActionClient({ mode: 'v2_shadow', transport: {} });
  await assert.rejects(() => readOnly.submitAction({}), error => error.kind === 'read_only');
  await assert.rejects(() => readOnly.requestRun('daily_integrator', {}), error => error.kind === 'read_only');

  let polls = 0;
  const live = actions.createActionClient({
    mode: 'v2_live', pollIntervalMs: 1, maximumPolls: 3,
    transport: {
      submitAction: async value => value,
      pollActionResult: async actionId => (++polls < 2 ? null : { action_id: actionId, status: 'applied' }),
      requestRun: async (kind, scope) => ({ request_id: 'rrq_111111111111111111111111', kind, scope }),
      readRunStatus: async requestId => ({ request_id: requestId, status: 'completed' }),
    },
  });
  const submitted = await live.submitAction({ action_id: 'uact_111111111111111111111111' });
  assert.equal(submitted.action_id, 'uact_111111111111111111111111');
  assert.equal((await live.waitForActionResult(submitted.action_id)).status, 'applied');
  const request = await live.requestRun('daily_integrator', { local_date: '2026-08-18' });
  assert.equal((await live.waitForRunResult(request.request_id)).status, 'completed');

  const requests = [];
  const http = actions.createHttpTransport({
    baseUrl: 'http://127.0.0.1:4318', token: 't'.repeat(32),
    fetchImpl: async (url, init) => {
      requests.push({ url: String(url), init });
      return { ok: true, status: 200, json: async () => ({ ok: true, value: { status: 'completed' } }) };
    },
  });
  assert.equal((await http.bootstrap()).status, 'completed');
  assert.equal((await http.readRuntimeSettings()).status, 'completed');
  assert.equal((await http.updateRuntimeSettings({ schedule: { enabled: false } })).status, 'completed');
  assert.equal((await http.readRunStatus('run_111111111111111111111111')).status, 'completed');
  assert.match(requests[0].url, /\/v2\/bootstrap/);
  assert.match(requests[1].url, /\/v2\/runtime-settings/);
  assert.match(requests[2].url, /\/v2\/runtime-settings/);
  assert.equal(requests[2].init.method, 'POST');
  assert.equal(requests[2].init.body, JSON.stringify({ schedule: { enabled: false } }));
  assert.match(requests[3].url, /\/v2\/run-status\?id=/);
  assert.equal(requests[0].init.headers.Authorization, `Bearer ${'t'.repeat(32)}`);
  assert.throws(() => actions.createHttpTransport({ baseUrl: 'https://example.com', token: 't'.repeat(32) }),
    error => error.kind === 'authorization');
  console.log('cognitive-v2-integration tests passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
