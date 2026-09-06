'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'chrome-newtab/dashboard.js'), 'utf8');

function functionSource(name) {
  const start = source.indexOf(`\nfunction ${name}(`);
  const end = source.indexOf('\n}', start + 1);
  assert.ok(start >= 0 && end > start, `${name} must be extractable from the real dashboard`);
  return source.slice(start + 1, end + 2);
}

function scope({active = true, fixture = {portrait: [], themes: []}, receipt = null} = {}) {
  const sandbox = vm.createContext({
    cognitiveDemoState: {active, fixture},
    cognitiveHomeState: {verifiedReceipts: new Map()},
    COGNITIVE_CONTENT_LABELS: {}, COGNITIVE_PURPOSE_LABELS: {},
    COGNITIVE_SOURCE_LABELS: {}, COGNITIVE_STANCE_LABELS: {}, COGNITIVE_STATE_LABELS: {},
    COGNITIVE_PORTRAIT_MATURITY: {forming: '形成中', stable: '已稳定'},
    cognitiveRecordStatusLabel: () => '摘要已形成',
    cognitiveRecordDestination: () => ({title: '等待归并', detail: '尚未升级'}),
    cognitiveVerifiedRevision: () => receipt,
    cognitiveTimeLabel: () => '10:00',
    cognitiveDateTimeLabel: () => '2026-09-05 10:00',
    cognitiveRefVersionLabel: () => 'v1',
    cognitiveDemoRawText: () => 'SYNTHETIC_RAW',
  });
  vm.runInContext([
    'escapeHtml', 'cognitiveChoiceOptions', 'cognitiveListInput', 'cognitiveCheckboxes',
    'cognitiveReceiptActions', 'cognitiveRecordDrawer', 'cognitiveRecordBrowserItem',
    'cognitivePortraitMaturity', 'cognitivePortraitMaturityLabel', 'cognitiveDemoPortraitMarkup',
  ].map(functionSource).join('\n'), sandbox, {filename: 'dashboard-detail-functions.js', timeout: 1000});
  return sandbox;
}

function record(extra = {}) {
  return {
    record_ref: {id: 'synthetic_record'}, receipt_ref: null,
    captured_at: '2026-09-05T10:00:00+08:00', summary: '唯一的整理摘要',
    status: 'ready', content_types: [], topics: [], purposes: [],
    understanding_refs: [], memory_refs: [], ...extra,
  };
}

function occurrences(text, value) { return text.split(value).length - 1; }

test('record details use a short title and display the summary once', () => {
  for (const active of [true, false]) {
    const detail = scope({active}).cognitiveRecordDrawer(record());
    assert.equal(detail.title, '记录详情');
    assert.equal(occurrences(`${detail.title}\n${detail.body}`, '唯一的整理摘要'), 1);
    assert.match(detail.body, /<h3>本地原文<\/h3>/);
    assert.match(detail.body, /data-cognitive-original="synthetic_record"/);
    assert.match(detail.body, /正在.*本地原文/);
    assert.doesNotMatch(detail.body, /当前快照暂不可读|已从当前 SourceRecord 读取/,
      'original loading state must be owned by the async original-reader region');
  }
});

test('memory details keep long content in the body instead of repeating it as the title', () => {
  const memory = {statement: '这段完整记忆只显示一次', topics: []};
  const memoryNode = {memory_ref: {id: 'memory-one'}};
  const sandbox = vm.createContext({
    cognitiveHomeState: {landscape: {nodes: [memoryNode], edges: []}, verifiedMemories: new Map()},
    cognitiveVerifiedRevision: () => memory, cognitiveRelatedRecords: () => [],
    cognitiveRecordSummaryList: () => '', cognitiveMemoryActions: () => '', cognitiveRefVersionLabel: () => 'v1',
  });
  vm.runInContext(['escapeHtml', 'cognitiveNodeDrawer'].map(functionSource).join('\n'), sandbox);
  const detail = sandbox.cognitiveNodeDrawer(memoryNode);
  assert.equal(detail.title, '可用记忆 01');
  assert.equal(occurrences(detail.title + detail.body, memory.statement), 1);
});

test('destination appears once with or without semantic facts across record states', () => {
  const sandbox = scope();
  for (const status of ['ready', 'original_only', 'no_candidate']) {
    for (const semantic of [{}, {summary_scope: 'full', authorship: 'user'}]) {
      const detail = sandbox.cognitiveRecordDrawer(record({status, ...semantic}));
      assert.equal(occurrences(detail.body, '等待归并 · 尚未升级'), 1,
        `${status} must retain one destination description`);
      assert.equal(detail.body.includes('Agent 如何理解这条记录'), Boolean(semantic.summary_scope));
      if (semantic.summary_scope) assert.match(detail.body, /摘要范围：全文/);
    }
  }
});

test('resource previews retain their content boundary without repeating the title summary', () => {
  const detail = scope().cognitiveRecordDrawer(record({summary_kind: 'resource_preview'}));
  assert.equal(detail.title, '记录详情');
  assert.match(detail.body, /资源内容预览/);
  assert.match(detail.body, /这是 OCR 原文的节选/);
  assert.equal(occurrences(detail.body, '唯一的整理摘要'), 1);
});

test('record calibration actions and original-reader target remain present', () => {
  const receipt = {
    status: 'ready', summary: '可以编辑的摘要',
    facets: {content_types: [], topics: [], objects: [], stance: '', cognitive_state: '', purposes: []},
  };
  const detail = scope({active: false, receipt}).cognitiveRecordDrawer(record({
    receipt_ref: {id: 'synthetic_receipt'},
  }));
  assert.match(detail.body, /data-cognitive-action="confirm_receipt"/);
  assert.match(detail.body, /data-cognitive-edit-form="edit_receipt"/);
  assert.match(detail.body, /data-cognitive-terminal-action="original_only"/);
  assert.match(detail.body, /<textarea[^>]*name="summary"[^>]*>可以编辑的摘要<\/textarea>/);
  assert.match(detail.body, /data-cognitive-original="synthetic_record"/);
});

test('unavailable empty records explain pending content without an impossible action', () => {
  const markup = scope().cognitiveRecordBrowserItem({
    id: 'pending', detailAvailable: false, summary: '', summaryState: 'pending',
    time: '10:00', source: '本地记录', themeTitles: [], tags: [],
  });
  assert.match(markup, /disabled aria-disabled="true"/);
  assert.match(markup, /原文已保存 · 摘要待整理/);
  assert.match(markup, /详情等待下一份完整快照发布/);
  assert.doesNotMatch(markup, /查看原始记录|查看原文与整理结果|data-cognitive-browser-record=/);
});

test('available records without a summary keep the working original-record action', () => {
  const markup = scope().cognitiveRecordBrowserItem({
    id: 'available', detailAvailable: true, summary: '', summaryState: 'pending',
    time: '10:00', source: '本地记录', themeTitles: [], tags: [],
  });
  assert.match(markup, /data-cognitive-browser-record="available"/);
  assert.match(markup, /查看原始记录/);
  assert.doesNotMatch(markup, /aria-disabled="true"/);
});

test('empty and absent portrait data show a clear empty state without claiming existing understanding', () => {
  for (const fixture of [null, {}, {portrait: [], themes: []}]) {
    const markup = scope({fixture}).cognitiveDemoPortraitMarkup();
    assert.match(markup, /尚未形成长期理解/);
    assert.match(markup, /记录和已有主题会继续保留/);
    assert.doesNotMatch(markup, /由多个可追溯主题收束而来|cognitive-demo-portrait-card|data-demo-open-peak=/);
  }
});

test('existing understanding preserves its statement, maturity and theme navigation', () => {
  const markup = scope({fixture: {
    portrait: [{id: 'portrait_one', title: '一条理解', statement: '唯一的理解正文',
      boundary: '保留适用边界', maturity: 'stable', themeIds: ['theme_one']}],
    themes: [{id: 'theme_one', understandingId: 'understanding_one', title: '可追溯主题'}],
  }}).cognitiveDemoPortraitMarkup('portrait_one');
  assert.equal(occurrences(markup, '唯一的理解正文'), 1);
  assert.match(markup, /aria-current="true"/);
  assert.match(markup, /已稳定/);
  assert.match(markup, /保留适用边界/);
  assert.match(markup, /data-demo-open-peak="understanding_one"/);
  assert.doesNotMatch(markup, /尚未形成长期理解/);
});
