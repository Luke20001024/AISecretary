'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const {normalizeRecords, buildView} = require('../chrome-newtab/cognitive-record-browser.js');

const root = path.resolve(__dirname, '..');
const dashboard = fs.readFileSync(path.join(root, 'chrome-newtab/dashboard.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'chrome-newtab/dashboard.html'), 'utf8');

// Evaluate selected real functions without booting the dashboard, loading a
// directory, opening a transport or constructing a full browser DOM.
function functionSource(name) {
  const marker = `\nfunction ${name}(`;
  const start = dashboard.indexOf(marker);
  assert.ok(start >= 0, `${name} must exist`);
  const end = dashboard.indexOf('\n}', start + marker.length);
  assert.ok(end > start, `${name} must have a complete top-level body`);
  return dashboard.slice(start + 1, end + 2);
}

function scope(names, globals = {}) {
  const sandbox = vm.createContext({...globals});
  vm.runInContext(names.map(functionSource).join('\n'), sandbox,
    {filename: 'dashboard-selected-functions.js', timeout: 1000});
  return sandbox;
}

function sourceRecord(id, extra = {}) {
  return {
    id, record_ref: {id}, date: '2026-09-05', captured_at: '2026-09-05T10:00:00+08:00',
    time: '10:00', source_app: '备忘录', topics: ['产品方法'],
    summary: '已发布摘要', status: 'ready', understanding_refs: [], ...extra,
  };
}

function recordScope(published, recent = [], live = false) {
  return scope(['cognitiveBrowserRecords'], {
    cognitiveDemoState: {fixture: {records: published}},
    cognitiveHomeState: {home: {records: published}},
    cognitiveBackendState: {learningActivity: {recent_records: recent}},
    cognitiveUsingLiveBackend: () => live,
    cognitiveRecordById: id => published.find(record => record.id === id) || null,
    COGNITIVE_SOURCE_LABELS: {voice_transcript: '语音记录'},
    cognitivePeakById: () => ({title: '长期判断'}),
    cognitivePeakTitle: peak => peak.title,
    cognitivePeakIndex: () => 0,
  });
}

test('record browsing initially selects tags with matching button order and pressed states', () => {
  const declaration = dashboard.match(/const cognitiveRecordBrowserState = \{[\s\S]*?\n\};/);
  assert.ok(declaration, 'the real initial record-browser state must be locatable');
  const initialState = vm.runInNewContext(`${declaration[0]}\ncognitiveRecordBrowserState;`, {},
    {filename: 'dashboard-initial-record-state.js', timeout: 1000});
  assert.equal(initialState.mode, 'tags');
  const buttons = [...html.matchAll(/<button\b[^>]*data-cognitive-record-mode="(tags|time)"[^>]*>/g)];
  assert.deepEqual(buttons.map(button => button[1]), ['tags', 'time'],
    'the tags control must precede the time control');
  for (const button of buttons) {
    const pressed = button[0].match(/aria-pressed="(true|false)"/)?.[1];
    assert.equal(pressed, String(button[1] === initialState.mode),
      `${button[1]} pressed state must agree with the actual initial mode`);
  }
});

test('dashboard adapter preserves explicit unavailable detail even when ID is published', () => {
  const records = [sourceRecord('restricted', {detailAvailable: false}), sourceRecord('available')];
  const before = structuredClone(records);
  const sandbox = recordScope(records);
  const result = sandbox.cognitiveBrowserRecords();
  assert.equal(result[0].detailAvailable, false);
  assert.equal(result[1].detailAvailable, true);
  assert.deepEqual(records, before);
});

test('published records precede recent heads and remain authoritative after deduplication', () => {
  const published = [sourceRecord('published', {understanding_refs: [{id: 'theme-one'}]})];
  const recent = [
    sourceRecord('published', {summary: '尚未发布的新摘要', topics: ['近期标签']}),
    sourceRecord('pending', {summary: '', summary_state: 'pending'}),
  ];
  const sandbox = recordScope(published, recent, true);
  const adapted = sandbox.cognitiveBrowserRecords();
  assert.deepEqual(Array.from(adapted, record => record.id), ['published', 'published', 'pending']);
  assert.equal(adapted[1].detailAvailable, false);
  assert.equal(adapted[2].detailAvailable, false);
  const result = buildView(adapted, {mode: 'tags', range: 'all', tag: null, today: '2026-09-05'});
  assert.equal(result.totalCount, 2);
  const authoritative = result.records.find(record => record.id === 'published');
  assert.equal(authoritative.summary, '已发布摘要');
  assert.equal(authoritative.detailAvailable, true);
  assert.deepEqual(authoritative.tags, ['产品方法']);
  assert.deepEqual(authoritative.themeTitles, ['长期判断']);
  assert.ok(result.tags.every(tag => tag.key !== '长期判断' && tag.key !== '近期标签'));
});

test('recent heads are excluded from fixture mode and pending rows have no detail action', () => {
  const recent = [sourceRecord('pending', {summary: '', summary_state: 'pending'})];
  const fixtureSandbox = recordScope([], recent, false);
  assert.equal(fixtureSandbox.cognitiveBrowserRecords().length, 0);
  const liveSandbox = recordScope([], recent, true);
  const item = normalizeRecords(liveSandbox.cognitiveBrowserRecords())[0];
  const renderer = scope(['escapeHtml', 'cognitiveRecordBrowserItem']);
  const markup = renderer.cognitiveRecordBrowserItem(item);
  assert.match(markup, /disabled aria-disabled="true"/);
  assert.doesNotMatch(markup, /data-cognitive-browser-record=/);
  assert.match(markup, /等待摘要/);
  assert.match(markup, /详情等待下一份完整快照发布/);
});

test('record renderer escapes hostile content and attribute payloads in every field', () => {
  const renderer = scope(['escapeHtml', 'cognitiveRecordBrowserItem']);
  const hostileId = 'record" onclick="globalThis.injected=true';
  const hostile = '<img src=x onerror="globalThis.injected=true"> & \'probe\'';
  const item = {
    id: hostileId, time: hostile, source: hostile, summary: hostile,
    summaryState: 'ready', tags: [hostile], themeTitles: [hostile], detailAvailable: true,
  };
  const markup = renderer.cognitiveRecordBrowserItem(item);
  const expectedText = '&lt;img src=x onerror=&quot;globalThis.injected=true&quot;&gt; &amp; &#39;probe&#39;';
  assert.ok(markup.includes('data-cognitive-browser-record="record&quot; onclick=&quot;globalThis.injected=true"'));
  assert.equal(markup.split(expectedText).length - 1, 5,
    'time, source, summary, tag and linked theme title must all be escaped');
  assert.doesNotMatch(markup, /<img\b|<script\b|\sonclick="/);
  assert.equal(renderer.injected, undefined);
});

function runtimeScope() {
  const nodes = {
    'cognitive-runtime-action': {tagName: 'SPAN', dataset: {}, textContent: ''},
    'cognitive-runtime-status': {textContent: '', hidden: true},
    'cognitive-reconnect-action': {tagName: 'BUTTON', disabled: false, textContent: ''},
    'cognitive-connection-menu': {open: false},
  };
  return {nodes, sandbox: scope(['setCognitiveRuntimeUi'], {
    cognitiveIsPublicPreview: () => false,
    document: {getElementById: id => nodes[id] || null, querySelector: () => null},
  })};
}

test('runtime state is a passive status span and live state reads 本地已连接', () => {
  assert.match(html, /<span id="cognitive-runtime-action"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.doesNotMatch(html, /<button[^>]*id="cognitive-runtime-action"/);
  assert.match(html, /<button id="cognitive-reconnect-action"[^>]*type="button"/);
  const {nodes, sandbox} = runtimeScope();
  sandbox.setCognitiveRuntimeUi('live', '本地数据已就绪');
  assert.equal(nodes['cognitive-runtime-action'].textContent, '本地已连接');
  assert.equal(nodes['cognitive-runtime-action'].dataset.runtimeState, 'live');
  assert.equal(nodes['cognitive-reconnect-action'].disabled, false);
  assert.equal(nodes['cognitive-reconnect-action'].textContent, '重新连接本地数据');
  assert.equal(nodes['cognitive-runtime-status'].textContent, '本地数据已就绪');
  assert.equal(nodes['cognitive-runtime-status'].hidden, false);
});

test('connecting disables reconnection and an error reveals the existing connection menu', () => {
  const {nodes, sandbox} = runtimeScope();
  sandbox.setCognitiveRuntimeUi('connecting');
  assert.equal(nodes['cognitive-reconnect-action'].disabled, true);
  assert.equal(nodes['cognitive-runtime-status'].hidden, true);
  sandbox.setCognitiveRuntimeUi('error', '连接失败');
  assert.equal(nodes['cognitive-runtime-action'].textContent, '连接中断');
  assert.equal(nodes['cognitive-reconnect-action'].disabled, false);
  assert.equal(nodes['cognitive-connection-menu'].open, true);
});

test('the shell click handler connects only through the reconnect control', () => {
  const functionText = functionSource('initCognitiveHomeInteractions');
  const start = functionText.indexOf("shell.addEventListener('click', event => {");
  const end = functionText.indexOf("\n  document.getElementById('cognitive-record-range')", start);
  assert.ok(start >= 0 && end > start, 'the delegated shell click handler must be locatable');
  const requests = [];
  let onClick;
  const sandbox = vm.createContext({
    shell: {addEventListener: (event, handler) => { assert.equal(event, 'click'); onClick = handler; }},
    document: {getElementById: id => id === 'cognitive-connection-menu' ? {open: false} : null},
    performance: {now: () => 1},
    cognitiveMapCameraState: {suppressClickUntil: 0},
    connectCognitiveRuntimeFromDirectory: options => { requests.push(options); return Promise.resolve(); },
  });
  vm.runInContext(functionText.slice(start, end), sandbox, {timeout: 1000});
  const click = id => onClick({target: {closest: selector => selector === `#${id}` ? {id} : null}});
  click('cognitive-runtime-action');
  click('cognitive-runtime-status');
  assert.equal(requests.length, 0);
  click('cognitive-reconnect-action');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].requestPermission, true);
});

test('hidden home leaves camera state and viewBox untouched without calling layout helpers', () => {
  const attributes = {viewBox: '10 20 550 260'};
  let writes = 0;
  const svg = {setAttribute: (key, value) => { writes++; attributes[key] = value; }};
  const camera = {zoom: 2, centerX: 285, centerY: 150};
  const before = {...camera};
  const sandbox = scope(['cognitiveApplyMapCamera'], {
    document: {getElementById: id => id === 'cognitive-landscape-map' ? svg
      : id === 'cognitive-home-view' ? {hidden: true} : null},
    cognitiveMapCameraState: camera,
    cognitiveClampMapCamera: () => assert.fail('hidden map must not clamp against zero layout dimensions'),
    cognitiveUpdateAtlasHud: () => assert.fail('hidden map must not update its HUD'),
  });
  sandbox.cognitiveApplyMapCamera();
  sandbox.cognitiveApplyMapCamera({clamp: false});
  assert.equal(writes, 0);
  assert.equal(attributes.viewBox, '10 20 550 260');
  assert.deepEqual(camera, before);
});
