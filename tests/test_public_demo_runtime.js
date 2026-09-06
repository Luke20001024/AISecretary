'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const js = fs.readFileSync(path.join(root, 'chrome-newtab/dashboard.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'chrome-newtab/dashboard.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'chrome-newtab/dashboard.css'), 'utf8');
const configSource = fs.readFileSync(path.join(root, 'chrome-newtab/cognitive-runtime-config.js'), 'utf8');

function sourceBetween(start, end) {
  const first = js.indexOf(start);
  const last = js.indexOf(end, first + start.length);
  assert.ok(first >= 0 && last > first, `Source boundary must exist: ${start}`);
  return js.slice(first, last);
}

function element() {
  return {
    hidden: false, disabled: false, checked: true, value: '', title: '',
    textContent: '', dataset: {}, attributes: {}, listeners: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, handler) { this.listeners[name] = handler; },
  };
}

function environment(config, savedMode = 'v2_live') {
  const counts = { storageReads: 0, storageWrites: 0, autoConnections: 0,
    transports: 0, directorySources: 0, fixtures: 0, actions: 0, reloads: 0, directoryReads: 0 };
  const nodes = new Map();
  const getNode = id => {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  };
  const actionClient = { submitAction() { counts.actions += 1; },
    requestRun() { counts.actions += 1; }, waitForActionResult() { counts.actions += 1; },
    waitForRunResult() { counts.actions += 1; } };
  const window = {
    MementoRuntimeConfig: config,
    MementoCognitiveV2DataSource: {
      readFeatureMode() { counts.storageReads += 1; return savedMode; },
      writeFeatureMode() { counts.storageWrites += 1; },
      createDirectoryDataSource() { counts.directorySources += 1; },
    },
    MementoCognitiveV2Actions: {
      createActionClient: ({ mode }) => ({ ...actionClient, mode }),
      createHttpTransport() { counts.transports += 1; throw new Error('unexpected network transport'); },
    },
    location: { reload() { counts.reloads += 1; } },
  };
  const state = { mode: 'fixture' };
  const sandbox = {
    window, console, cognitiveBackendState: state, cognitiveHomeState: { home: {} },
    document: {
      getElementById: getNode,
      querySelector: getNode,
      documentElement: { classList: { toggle() {} } },
    },
    enterCognitiveDemo() { counts.fixtures += 1; },
    showGrantUI() {},
    connectCognitiveRuntimeFromDirectory() { counts.autoConnections += 1; },
    cognitiveUsingLiveBackend: () => state.mode === 'v2_live',
    refreshCognitiveGrowthActivity: async () => {},
    indexedDB: { open() { counts.directoryReads += 1; } },
  };
  vm.createContext(sandbox);
  vm.runInContext([
    sourceBetween('const COGNITIVE_PUBLIC_PREVIEW', "const DB_NAME ="),
    sourceBetween('function syncCognitivePortraitUpdateControls(', 'async function refreshCognitiveGrowthActivity('),
    sourceBetween('async function enterConfiguredCognitiveBackend()', 'async function connectCognitiveRuntimeFromDirectory('),
  ].join('\n'), sandbox);
  return { sandbox, window, nodes, getNode, counts, state };
}

async function main() {
  const configContext = { window: {} };
  vm.runInNewContext(configSource, configContext);
  const published = configContext.window.MementoRuntimeConfig;
  assert.equal(published.publicPreview, true);
  assert.equal(published.mode, 'fixture');
  assert.equal(published.token, '');
  assert.equal(published.baseUrl, '');
  assert.ok(Object.isFrozen(published));

  // Both an old v2_live preference and an installed-looking token must lose to publicPreview.
  const demo = environment({ ...published, mode: 'v2_live', token: 'stale-token-'.repeat(4) });
  await demo.sandbox.enterConfiguredCognitiveBackend();
  assert.equal(demo.window.MementoCognitiveBackend.getMode(), 'fixture');
  assert.equal(demo.counts.storageReads, 0);
  assert.equal(demo.counts.autoConnections, 0);
  assert.equal(demo.counts.fixtures, 1);
  const backend = demo.window.MementoCognitiveBackend;
  await assert.rejects(() => backend.connectRuntime({ mode: 'v2_live' }), /在线体验/);
  await assert.rejects(() => backend.activateDirectorySource({}), /在线体验/);
  for (const [name, args] of [
    ['submitAction', [{}]], ['requestRun', ['daily', {}]], ['setMode', ['v2_live']],
    ['updateRuntimeSettings', [{ schedule: { enabled: true } }]], ['readRuntimeSettings', []],
    ['readExternalSession', ['session']], ['listRunStatuses', []],
    ['waitForActionResult', ['action']], ['waitForRunResult', ['run']],
  ]) assert.throws(() => backend[name](...args), /在线体验/, name);
  assert.equal(demo.counts.transports, 0);
  assert.equal(demo.counts.directorySources, 0);
  assert.equal(demo.counts.storageWrites, 0);
  assert.equal(demo.counts.actions, 0);
  assert.equal(demo.counts.reloads, 0);

  vm.runInContext(sourceBetween('async function connectCognitiveRuntimeFromDirectory(', 'async function getArchiveDir('), demo.sandbox);
  assert.equal(await demo.sandbox.connectCognitiveRuntimeFromDirectory({ requestPermission: true }), null);
  assert.equal(demo.counts.transports, 0);
  vm.runInContext(sourceBetween('async function queryRead(', 'async function persistSelectedDirectoryHandle('), demo.sandbox);
  await assert.rejects(() => demo.sandbox.queryRead({}), /在线体验/);
  await assert.rejects(() => demo.sandbox.requestRead({}), /在线体验/);
  await assert.rejects(() => demo.sandbox.pickFolder(), /在线体验/);
  vm.runInContext(sourceBetween('function openDB()', 'async function saveHandle('), demo.sandbox);
  assert.throws(() => demo.sandbox.openDB(), /在线体验/);
  assert.equal(demo.counts.directoryReads, 0);

  // An installed config without the flag keeps automatic v2_live startup unchanged.
  const installed = environment({ mode: 'v2_live', token: 'installed-token-'.repeat(3) }, 'fixture');
  await installed.sandbox.enterConfiguredCognitiveBackend();
  assert.equal(installed.window.MementoCognitiveBackend.getMode(), 'v2_live');
  assert.equal(installed.counts.autoConnections, 1);
  assert.equal(installed.counts.fixtures, 0);
  assert.equal(installed.counts.storageReads, 0);
  assert.equal(installed.getNode('cognitive-reconnect-action').hidden, false);
  installed.sandbox.cognitiveRequireLocalRuntime();
  installed.state.runtimeSettings = { schedule: { enabled: true, hour: 21, minute: 0 } };
  installed.state.runtimeTransport = { updateRuntimeSettings() {} };
  installed.sandbox.syncCognitivePortraitUpdateControls();
  assert.equal(installed.getNode('cognitive-portrait-auto-update').checked, true);
  assert.equal(installed.getNode('cognitive-portrait-auto-update').disabled, false);

  const localPreference = environment({}, 'v2_live');
  await localPreference.sandbox.enterConfiguredCognitiveBackend();
  assert.equal(localPreference.counts.storageReads, 1);
  assert.equal(localPreference.counts.autoConnections, 1);

  // Public labels and disabled scheduling never imply an actual local connection or saved plan.
  demo.sandbox.setCognitiveRuntimeUi('live', 'stale success message');
  assert.equal(demo.getNode('.cognitive-connection-label').textContent, '在线体验');
  assert.equal(demo.getNode('cognitive-runtime-action').textContent, '示例数据');
  assert.equal(demo.getNode('cognitive-reconnect-action').hidden, true);
  assert.equal(demo.getNode('cognitive-reconnect-action').disabled, true);
  assert.match(demo.getNode('cognitive-runtime-status').textContent, /不读取个人记录/);
  demo.state.runtimeSettings = { schedule: { enabled: true, hour: 21, minute: 0 } };
  demo.state.runtimeTransport = { updateRuntimeSettings() { demo.counts.actions += 1; } };
  demo.sandbox.initCognitivePortraitUpdateControls();
  const toggle = demo.getNode('cognitive-portrait-auto-update');
  assert.equal(toggle.disabled, true);
  assert.equal(toggle.checked, false);
  assert.equal(toggle.attributes['aria-describedby'], 'cognitive-update-scope');
  assert.equal(demo.getNode('cognitive-portrait-update-at').disabled, true);
  assert.match(demo.getNode('cognitive-update-scope').textContent, /在线体验不启用/);
  toggle.checked = true;
  await toggle.listeners.change();
  assert.equal(toggle.checked, false);
  assert.equal(demo.counts.actions, 0);
  assert.match(html, /id="cognitive-reconnect-action"[^>]*hidden/);
  assert.match(css, /\.cognitive-connection-popover button\[hidden\]\s*\{ display: none;/);
  assert.match(css, /\.is-public-preview[\s\S]*cursor: not-allowed/);
  assert.match(js, /async function tryAutoLoad\(\) \{\s*if \(cognitiveIsPublicPreview\(\)\) return/);
  assert.match(js, /grantBtn.addEventListener\('click', async \(\) => \{\s*if \(cognitiveIsPublicPreview\(\)\) return/);
  console.log('public demo runtime: startup priority, local entry guards, installed compatibility, and honest UI passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
