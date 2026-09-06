'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const contract = require('../chrome-newtab/cognitive-v2-contract.js');

const fixture = JSON.parse(fs.readFileSync(
  path.join(__dirname, 'fixtures', 'cognitive-v2-bundle.json'), 'utf8'
));

const validated = contract.validateProjectionBundleSnapshot(fixture.snapshot);
assert.deepEqual(validated, fixture.snapshot);
assert.notEqual(validated, fixture.snapshot, 'validator must detach the returned graph');
assert.equal(contract.bundleSha256(validated), fixture.bundle_sha256);
const validatedBridge = contract.validateFrontendBridge(fixture.bridge, fixture.snapshot);
assert.equal(validatedBridge.bundle_sha256, fixture.bundle_sha256);
assert.equal(validatedBridge.legacy_view.mode, 'v2_shadow');
assert.deepEqual(contract.MODES, ['fixture', 'v1_adapter', 'v2_shadow', 'v2_live']);
for (const mode of contract.MODES) assert.equal(contract.validateFeatureMode(mode), mode);
assert.throws(() => contract.validateFeatureMode('provider'), /模式无效/);

const changedProjection = structuredClone(fixture.snapshot);
changedProjection.projections['projections/home.json'].warnings.push('tampered');
assert.throws(
  () => contract.validateProjectionBundleSnapshot(changedProjection),
  /SHA-256 不匹配/
);

const missingDetail = structuredClone(fixture.snapshot);
const detailPath = Object.keys(missingDetail.projections).find(value => value.includes('/details/theme/'));
delete missingDetail.projections[detailPath];
assert.throws(
  () => contract.validateProjectionBundleSnapshot(missingDetail),
  /文件集合与 manifest 不一致/
);

const staleHome = structuredClone(fixture.snapshot);
staleHome.projections['projections/home.json'].landscape_ref.sha256 = 'f'.repeat(64);
const homeEntry = staleHome.manifest.entries.find(entry => entry.path === 'projections/home.json');
homeEntry.sha256 = contract.sha256Json(staleHome.projections['projections/home.json']);
assert.throws(
  () => contract.validateProjectionBundleSnapshot(staleHome),
  /home.landscape_ref 已失效/
);

const staleBridge = structuredClone(fixture.bridge);
staleBridge.bundle_sha256 = '0'.repeat(64);
assert.throws(() => contract.validateFrontendBridge(staleBridge, fixture.snapshot));

const tamperedBridge = structuredClone(fixture.bridge);
tamperedBridge.legacy_view.stats.totalRecords += 1;
assert.throws(() => contract.validateFrontendBridge(tamperedBridge, fixture.snapshot));

assert.equal(contract.normalizeProjectionBundleSnapshot(changedProjection), null);
console.log('cognitive-v2-contract tests passed');
