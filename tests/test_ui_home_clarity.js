'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const recordBrowser = require('../chrome-newtab/cognitive-record-browser.js');
const source = fs.readFileSync(path.join(__dirname, '..', 'chrome-newtab/dashboard.js'), 'utf8');

function functionSource(name) {
  const start = source.indexOf(`\nfunction ${name}(`);
  const end = source.indexOf('\n}', start + 1);
  assert.ok(start >= 0 && end > start, `${name} must come from the actual dashboard`);
  return source.slice(start + 1, end + 2);
}

function scope(names, globals) {
  const sandbox = vm.createContext({...globals});
  vm.runInContext(names.map(functionSource).join('\n'), sandbox,
    {filename: 'dashboard-home-clarity-functions.js', timeout: 1000});
  return sandbox;
}

function node() {
  const classes = new Set();
  return {
    hidden: false, textContent: '', innerHTML: '', value: '', dataset: {}, attributes: {},
    classList: {
      add: value => classes.add(value),
      contains: value => classes.has(value),
      toggle: (value, enabled) => enabled ? classes.add(value) : classes.delete(value),
    },
    setAttribute(key, value) { this.attributes[key] = value; },
  };
}

function nodeStore() {
  const nodes = new Map();
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, node());
    return nodes.get(id);
  };
  return {nodes, get};
}

function growthScope({live = true, activity = null, error = ''} = {}) {
  const {get} = nodeStore();
  const backend = {runtimeActivity: activity, runtimeActivityRefreshError: error};
  const sandbox = scope(['escapeHtml', 'cognitiveGrowthStageLabel', 'renderCognitiveGrowthActivity'], {
    document: {getElementById: get, querySelector: () => get('today-section')},
    cognitiveUsingLiveBackend: () => live,
    cognitiveIsPublicPreview: () => false,
    cognitiveBackendState: backend,
    cognitiveHomeState: {stale: false, snapshotLocalDate: '2026-09-04'},
    cognitiveDateTimeLabel: value => String(value),
    cognitiveReadableLocalDate: value => String(value),
  });
  return {get, sandbox, backend, render: () => sandbox.renderCognitiveGrowthActivity({records: []})};
}

function healthyActivity() {
  return {
    daily: {state: 'completed'},
    theme: {state: 'completed', last_change_at: '2026-09-05T10:00:00+08:00'},
    self: {state: 'no_change'},
  };
}

test('normal update date appears in the status summary and the old caption is cleared', () => {
  const {get, render} = growthScope({activity: healthyActivity()});
  get('cognitive-landscape-caption').textContent = '旧的重复更新时间';
  render();
  assert.equal(get('cognitive-growth-summary').textContent, '更新于 2026-09-05T10:00:00+08:00');
  assert.equal(get('cognitive-landscape-caption').textContent, '');
  assert.equal(get('cognitive-landscape-caption').hidden, true);
  assert.equal(get('cognitive-growth-status').hidden, false);
  assert.equal(get('cognitive-growth-status').classList.contains('has-issue'), false);
  assert.equal(get('cognitive-learning-strip').hidden, true);
});

test('failed, retry, rejected and conflict states are visible for every processing stage', () => {
  const readable = {failed: '更新失败', retry_wait: '更新失败 · 待重试',
    rejected: '处理未完成', conflict: '输入已变化'};
  for (const stage of ['daily', 'theme', 'self']) {
    for (const [state, label] of Object.entries(readable)) {
      const activity = healthyActivity();
      activity[stage].state = state;
      const {get, render} = growthScope({activity});
      render();
      assert.equal(get('cognitive-growth-status').hidden, false, `${stage}/${state}`);
      assert.equal(get('cognitive-growth-status').classList.contains('has-issue'), true, `${stage}/${state}`);
      assert.equal(get('cognitive-growth-summary').textContent, '整理异常 · 原有结果已保留', `${stage}/${state}`);
      assert.ok(get('cognitive-growth-stages').innerHTML.includes(label), `${stage}/${state}`);
      assert.equal(get('cognitive-landscape-caption').hidden, true);
    }
  }
});

test('refresh failures override stale success summaries and conceal stale stage details', () => {
  for (const activity of [healthyActivity(), null]) {
    const {get, render} = growthScope({activity, error: '运行状态刷新失败：连接中断'});
    render();
    assert.equal(get('cognitive-growth-summary').textContent, '运行状态刷新失败：连接中断');
    assert.equal(get('cognitive-growth-status').hidden, false);
    assert.equal(get('cognitive-growth-status').classList.contains('has-issue'), true);
    assert.equal(get('cognitive-growth-stages').hidden, true);
    assert.equal(get('cognitive-growth-feedback').hidden, false);
    assert.match(get('cognitive-growth-feedback').textContent, /连接管理/);
    assert.equal(get('cognitive-landscape-caption').textContent, '');
    assert.equal(get('cognitive-landscape-caption').hidden, true);
  }
});

test('a successful refresh clears the previous error state', () => {
  const {get, backend, render} = growthScope({activity: healthyActivity(), error: '运行状态刷新失败'});
  render();
  backend.runtimeActivityRefreshError = '';
  render();
  assert.equal(get('cognitive-growth-status').classList.contains('has-issue'), false);
  assert.equal(get('cognitive-growth-stages').hidden, false);
  assert.match(get('cognitive-growth-summary').textContent, /^更新于 /);
});

test('fixture mode hides runtime-only activity even if earlier runtime data remains', () => {
  const {get, render} = growthScope({live: false, activity: healthyActivity()});
  render();
  assert.equal(get('cognitive-growth-status').hidden, true);
  assert.equal(get('cognitive-update-scope').hidden, true);
  assert.equal(get('cognitive-learning-strip').hidden, true);
});

test('stale snapshot context is kept inside the disclosure without repeating the header caption', () => {
  const {get, render, sandbox} = growthScope({activity: healthyActivity()});
  sandbox.cognitiveHomeState.stale = true;
  render();
  assert.equal(get('cognitive-growth-snapshot').hidden, false);
  assert.match(get('cognitive-growth-snapshot').textContent, /2026-09-04/);
  assert.equal(get('cognitive-landscape-caption').hidden, true);
});

test('the saved total has one owner instead of a second headline count', () => {
  const sandbox = scope(['cognitiveTodayHeadline'], {cognitiveDemoState: {active: true}});
  assert.equal(sandbox.cognitiveTodayHeadline({today_status: {saved: 15}}), '');
});

test('digest status stays independent from long-term memory destination', () => {
  const sandbox = scope(['cognitiveRecordDigestLabel']);
  for (const memory_state of ['candidate', 'waiting_evidence', 'waiting_confirmation', 'promoted', 'not_candidate']) {
    assert.equal(sandbox.cognitiveRecordDigestLabel({status: 'ready', summary: '已有摘要', memory_state}),
      '摘要已形成', memory_state);
  }
  assert.equal(sandbox.cognitiveRecordDigestLabel({status: 'ready', summary: '已有摘要', memory_state: 'rejected'}),
    '摘要已保留');
  assert.equal(sandbox.cognitiveRecordDigestLabel({status: 'raw_saved', summary: ''}), '原文已保存 · 待整理');
});

test('failed digest overrides pending or memory state and cannot look like ordinary waiting', () => {
  const sandbox = scope(['cognitiveRecordDigestLabel']);
  for (const extra of [{}, {summary_state: 'pending'}, {memory_state: 'candidate', summary: '已有摘要'}]) {
    const label = sandbox.cognitiveRecordDigestLabel({status: 'failed', ...extra});
    assert.equal(label, '整理失败 · 原文已保存');
    assert.doesNotMatch(label, /待整理|正在整理|摘要已形成/);
  }
  assert.equal(sandbox.cognitiveRecordDigestLabel({status: 'processing'}), '正在整理');
});

test('raw previews and empty summaries never claim a completed digest', () => {
  const sandbox = scope(['cognitiveRecordDigestLabel']);
  assert.equal(sandbox.cognitiveRecordDigestLabel({status: 'ready', summary: 'OCR节选', summary_kind: 'resource_preview'}), '原文预览 · 待整理');
  assert.equal(sandbox.cognitiveRecordDigestLabel({status: 'ready', summary: '', memory_state: 'not_candidate'}), '原文已保存 · 待整理');
  assert.equal(sandbox.cognitiveRecordDigestLabel({status: 'original_only', summary: ''}), '原文已保存');
});

function browserScope(tag) {
  const {get} = nodeStore();
  get('cognitive-record-tags-panel').querySelector = () => get('tag-summary');
  const state = {mode: 'tags', tag, tagQuery: '', range: 'all', limit: 40, tagsInitialized: true};
  const records = [
    {id: 'older', date: '2026-08-20', summary: '旧资料', topics: tag === '__untagged__' ? [] : [tag]},
    {id: 'recent', date: '2026-09-05', summary: '近期资料', topics: ['产品方法']},
  ];
  const sandbox = scope(['escapeHtml', 'cognitiveRecordBrowserItem', 'renderCognitiveRecordBrowser'], {
    document: {getElementById: get, querySelectorAll: () => []},
    window: {MementoCognitiveRecordBrowser: recordBrowser},
    cognitiveHomeState: {runtimeLocalDate: '2026-09-05'},
    cognitiveRecordBrowserState: state,
    cognitiveBrowserRecords: () => records,
  });
  return {get, state, render: () => sandbox.renderCognitiveRecordBrowser()};
}

test('an empty time range keeps the actual selected tag name visible', () => {
  const {get, state, render} = browserScope('研究方法');
  render();
  assert.equal(get('tag-summary').textContent, '资料标签 · 研究方法');
  assert.match(get('cognitive-records-count').textContent, /研究方法 1 条/);
  state.range = '7';
  render();
  assert.equal(get('tag-summary').textContent, '资料标签 · 研究方法');
  assert.match(get('cognitive-records-count').textContent, /研究方法 0 条/);
  assert.match(get('cognitive-record-results').innerHTML, /这个时间范围内没有该标签的记录/);
  assert.doesNotMatch(get('tag-summary').textContent, /当前标签/);
});

test('the untagged filter retains a readable name even when its range has no untagged records', () => {
  const {get, state, render} = browserScope('__untagged__');
  state.range = '7';
  render();
  assert.equal(get('tag-summary').textContent, '资料标签 · 未分类');
  assert.match(get('cognitive-records-count').textContent, /未分类 0 条/);
  assert.doesNotMatch(get('cognitive-records-count').textContent, /__untagged__/);
});

test('records page hides home statistics and returning home restores the same summary', () => {
  const {get} = nodeStore();
  const state = {page: 'records'};
  const navigation = ['home', 'records'].map(page => ({...node(), dataset: {cognitivePage: page}}));
  let renders = 0;
  const sandbox = scope(['applyCognitivePage'], {
    document: {getElementById: get, querySelectorAll: () => navigation},
    cognitiveRecordBrowserState: state,
    renderCognitiveRecordBrowser: () => { renders++; },
  });
  get('cognitive-home-summary').textContent = '66 条记录 · 8 主题';
  sandbox.applyCognitivePage();
  assert.equal(get('cognitive-home-view').hidden, true);
  assert.equal(get('cognitive-records-view').hidden, false);
  assert.equal(get('cognitive-home-summary').hidden, true);
  assert.equal(navigation[1].attributes['aria-pressed'], 'true');
  assert.equal(renders, 1);
  state.page = 'home';
  sandbox.applyCognitivePage();
  assert.equal(get('cognitive-home-view').hidden, false);
  assert.equal(get('cognitive-records-view').hidden, true);
  assert.equal(get('cognitive-home-summary').hidden, false);
  assert.equal(get('cognitive-home-summary').textContent, '66 条记录 · 8 主题');
  assert.equal(navigation[0].attributes['aria-pressed'], 'true');
  assert.equal(navigation[1].attributes['aria-pressed'], 'false');
  assert.equal(renders, 1);
});
