'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const modulePath = path.join(__dirname, '..', 'chrome-newtab', 'cognitive-record-browser.js');
const browser = require(modulePath);
const { normalizeRecords, buildView } = browser;
const fixture = require('../chrome-newtab/cognitive-demo-fixture.js').createFixture();
const liveFixture = require('./fixtures/cognitive-v2-bundle.json').legacy_view;

function record(id, date, topics = [], extra = {}) {
  return {
    id, date, captured_at: `${date}T12:00:00+08:00`, time: '12:00',
    source_app: '备忘录', summary: `摘要 ${id}`, topics, status: 'ready',
    ...extra,
  };
}

function ids(records) { return records.map(item => item.id); }
function tagCounts(view) { return new Map(view.tags.map(tag => [tag.key, tag.count])); }
function view(records, options = {}) {
  return buildView(records, { mode: 'time', tag: null, range: 'all', today: '2026-09-05', ...options });
}
function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.values(value).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}

test('UMD can run in a browser without Node or side-effect APIs', () => {
  const sandbox = { window: {} };
  vm.runInNewContext(fs.readFileSync(modulePath, 'utf8'), sandbox,
    { filename: 'cognitive-record-browser.js', timeout: 1000 });
  const exposed = sandbox.window.MementoCognitiveRecordBrowser;
  assert.equal(typeof exposed?.normalizeRecords, 'function');
  assert.equal(typeof exposed?.buildView, 'function');
  assert.equal(exposed.buildView([], { today: '2026-09-05' }).totalCount, 0);
});

test('normalization preserves record date, content, source and drill-down availability', () => {
  const source = record('fixture-one', '2026-08-18', ['产品方法'], {
    source_app: 'Chrome', source_type: 'screenshot_ocr',
    summary: '已有摘要', text: '完整原文', themeTitles: ['证据优先'],
  });
  const normalized = normalizeRecords([source])[0];
  assert.equal(normalized.id, source.id);
  assert.equal(normalized.localDate, '2026-08-18');
  assert.equal(normalized.capturedAt, source.captured_at);
  assert.equal(normalized.time, '12:00');
  assert.equal(normalized.source, 'Chrome');
  assert.equal(normalized.summary, '已有摘要');
  assert.equal(typeof normalized.summaryState, 'string');
  assert.deepEqual(normalized.tags, ['产品方法']);
  assert.deepEqual(normalized.themeTitles, ['证据优先']);
  assert.equal(normalized.status, 'ready');
  assert.equal(normalized.detailAvailable, true);
  assert.equal(normalizeRecords([{ ...source, detailAvailable: false }])[0].detailAvailable, false);
});

test('recordId, local_date and tags work for alternate input records', () => {
  const [normalized] = normalizeRecords([{
    recordId: 'live-one', local_date: '2026-09-04', captured_at: '2026-09-04T08:15:00+08:00',
    time: '08:15', source_type: 'voice_transcript', summary: '口述摘要',
    tags: ['研究', '证据'], themeTitles: [], status: 'merged', detailAvailable: false,
  }]);
  assert.equal(normalized.id, 'live-one');
  assert.equal(normalized.localDate, '2026-09-04');
  assert.deepEqual(normalized.tags, ['研究', '证据']);
  assert.ok(normalized.source.length > 0, 'only source_type must still supply source metadata');
  assert.equal(normalized.detailAvailable, false);
});

test('normalization is idempotent and accepts live projection identity fields', () => {
  const input = [{
    record_ref: { id: 'projection-record' }, local_date: '2026-09-04',
    captured_at: '2026-09-04T08:15:00+08:00', time: '08:15',
    source_app: '浏览器', summary: '保留原始日期与来源',
    topics: ['研究', '证据'], tags: ['证据', '笔记'], status: 'merged',
    themeTitles: ['证据优先'], detailAvailable: false,
  }, {
    record_id: 'learning-record', local_date: '2026-09-03',
    summary: '学习记录', topics: [], tags: [], status: 'ready',
  }];
  const normalized = normalizeRecords(input);
  assert.deepEqual(new Set(ids(normalized)), new Set(['projection-record', 'learning-record']));
  assert.deepEqual(normalized.find(item => item.id === 'projection-record').tags,
    ['研究', '证据', '笔记']);
  assert.deepEqual(normalizeRecords(normalized), normalized);
  assert.deepEqual(view(normalized), view(input));
});

test('records without a summary keep the original text readable', () => {
  const [normalized] = normalizeRecords([record('raw', '2026-09-05', [], {
    summary: '', text: '还未整理的原始记录。', status: 'raw_saved',
  })]);
  assert.equal(normalized.summary, '还未整理的原始记录。');
  assert.equal(typeof normalized.summaryState, 'string');
  assert.deepEqual(normalized.tags, []);
  assert.equal(normalized.detailAvailable, true);
});

test('resource previews and pending placeholders never claim to be completed summaries', () => {
  const pending = normalizeRecords([record('pending', '2026-09-05', [], {
    summary: '摘要正在生成', summary_kind: 'pending',
  })])[0];
  const preview = normalizeRecords([record('preview', '2026-09-05', [], {
    summary: '网页内容预览', summary_kind: 'resource_preview',
  })])[0];
  const raw = normalizeRecords([record('raw', '2026-09-05', [], {
    summary: '', text: '原始记录',
  })])[0];
  assert.equal(pending.summaryState, 'pending');
  assert.equal(preview.summaryState, 'preview');
  assert.equal(raw.summaryState, 'preview');
});

test('manual tags join explicit topics while the generic record marker stays unclassified', () => {
  const tagged = record('tagged', '2026-09-05', ['研究'], { tags: ['证据'], tag: '下次再读' });
  const untagged = record('untagged', '2026-09-04', [], { tag: '记录' });
  const result = view([tagged, untagged], { mode: 'tags' });
  assert.deepEqual(tagCounts(result), new Map([
    ['研究', 1], ['证据', 1], ['下次再读', 1], ['__untagged__', 1],
  ]));
  assert.deepEqual(ids(view([tagged, untagged], { mode: 'tags', tag: '下次再读' }).records), ['tagged']);
});

test('duplicate IDs and duplicate tags do not inflate records or navigation counts', () => {
  const multi = record('multi', '2026-09-05', ['研究', '证据', '研究']);
  const result = view([
    multi, structuredClone(multi), record('single', '2026-09-04', ['研究']),
    record('untagged', '2026-09-03'),
  ]);
  assert.equal(result.totalCount, 3);
  assert.equal(result.visibleCount, 3);
  assert.equal(new Set(ids(result.records)).size, 3);
  assert.deepEqual(tagCounts(result), new Map([['研究', 2], ['证据', 1], ['__untagged__', 1]]));
  assert.equal(result.tags.find(tag => tag.key === '__untagged__').label, '未分类');
});

test('first duplicate is authoritative across date range, summary and tag counts', () => {
  const result = view([
    record('same-id', '2026-09-05', ['当前标签'], { summary: '权威记录', detailAvailable: false }),
    record('same-id', '2026-08-01', ['旧标签'], { summary: '旧的重复条目' }),
  ], { range: '7' });
  assert.equal(result.totalCount, 1);
  assert.equal(result.records[0].summary, '权威记录');
  assert.equal(result.records[0].detailAvailable, false);
  assert.deepEqual(tagCounts(result), new Map([['当前标签', 1]]));
  assert.equal(view([
    record('same-id', '2026-08-01', ['较早']),
    record('same-id', '2026-09-05', ['近期']),
  ], { range: '7' }).totalCount, 0, 'deduplication must happen before range filtering');
});

test('tag selection retains all navigation counts while filtering unique records', () => {
  const records = [
    record('multi', '2026-09-05', ['研究', '证据']),
    record('research', '2026-09-04', ['研究']),
    record('evidence', '2026-09-03', ['证据']), record('untagged', '2026-09-02'),
  ];
  const all = view(records, { mode: 'tags' });
  const selected = view(records, { mode: 'tags', tag: '研究' });
  assert.deepEqual(ids(selected.records), ['multi', 'research']);
  assert.equal(selected.totalCount, 4);
  assert.equal(selected.visibleCount, 2);
  assert.deepEqual(selected.tags, all.tags);
  assert.deepEqual(ids(view(records, { mode: 'tags', tag: '__untagged__' }).records), ['untagged']);
  const missing = view(records, { mode: 'tags', tag: '不存在的标签' });
  assert.equal(missing.totalCount, 4);
  assert.equal(missing.visibleCount, 0);
  assert.deepEqual(missing.groups, []);
  assert.deepEqual(missing.tags, all.tags);
});

test('Chinese tags remain exact concepts and never become fuzzy merged categories', () => {
  const records = [
    record('product', '2026-09-05', ['产品']),
    record('product-method', '2026-09-04', ['产品方法']),
    record('research', '2026-09-03', ['研究']),
    record('research-method', '2026-09-02', ['研究方法']),
  ];
  assert.equal(view(records, { mode: 'tags' }).tags.length, 4);
  assert.deepEqual(ids(view(records, { mode: 'tags', tag: '产品' }).records), ['product']);
  assert.deepEqual(ids(view(records, { mode: 'tags', tag: '研究方法' }).records), ['research-method']);
});

test('source metadata, raw text and long-term theme titles cannot invent record tags', () => {
  const records = [record('only-metadata', '2026-09-05', [], {
    source_app: '产品方法', source_type: '研究', summary: '证据优先', text: '研究方法',
    themeTitles: ['长期积累', '协作边界'],
  })];
  const result = view(records, { mode: 'tags' });
  assert.deepEqual(tagCounts(result), new Map([['__untagged__', 1]]));
  assert.deepEqual(result.records[0].themeTitles, ['长期积累', '协作边界']);
  for (const tag of ['产品方法', '研究', '研究方法', '证据优先', '长期积累']) {
    assert.equal(view(records, { mode: 'tags', tag }).visibleCount, 0);
  }
});

test('timeline uses each record date and sorts time descending within each day', () => {
  const records = [
    record('old', '2026-08-17', [], { generated_at: '2026-09-05T23:00:00+08:00' }),
    record('morning', '2026-08-18', [], { time: '08:01', captured_at: '2026-08-18T08:01:00+08:00' }),
    record('evening', '2026-08-18', [], { time: '22:59', captured_at: '2026-08-18T22:59:00+08:00' }),
  ];
  const result = view(records);
  assert.deepEqual(ids(result.records), ['evening', 'morning', 'old']);
  assert.deepEqual(result.groups.map(group => group.date), ['2026-08-18', '2026-08-17']);
  assert.deepEqual(ids(result.groups[0].records), ['evening', 'morning']);
  assert.equal(result.groups.flatMap(group => group.records).length, result.visibleCount);
  assert.equal(view(records, { range: '7' }).visibleCount, 0);
});

test('explicit local date controls date groups even when timestamps use another date', () => {
  const result = view([record('offset', '2026-09-05', ['研究'], {
    captured_at: '2026-09-04T16:30:00Z', time: '00:30',
  })], { range: '7' });
  assert.equal(result.records[0].localDate, '2026-09-05');
  assert.equal(result.groups[0].date, '2026-09-05');
});

test('seven days includes today and six previous calendar dates only', () => {
  const records = [
    record('future', '2026-09-06', ['未来']),
    record('today', '2026-09-05', ['当前']),
    record('oldest', '2026-08-30', ['当前']),
    record('outside', '2026-08-29', ['较早']),
  ];
  const result = view(records, { range: '7' });
  assert.deepEqual(ids(result.records), ['today', 'oldest']);
  assert.equal(result.totalCount, 2);
  assert.deepEqual(tagCounts(result), new Map([['当前', 2]]));
});

test('thirty days applies before tag filtering and switching ranges restores navigation', () => {
  const records = [
    record('today', '2026-09-05', ['近期']),
    record('seven', '2026-08-30', ['近期', '研究']),
    record('thirty', '2026-08-07', ['研究']),
    record('outside', '2026-08-06', ['旧资料']),
    record('future', '2026-09-06', ['未来']),
  ];
  const month = view(records, { mode: 'tags', range: '30', tag: '研究' });
  assert.equal(month.totalCount, 3);
  assert.equal(month.visibleCount, 2);
  assert.deepEqual(ids(month.records), ['seven', 'thirty']);
  assert.deepEqual(tagCounts(month), new Map([['近期', 2], ['研究', 2]]));
  const week = view(records, { mode: 'tags', range: '7', tag: '研究' });
  assert.equal(week.totalCount, 2);
  assert.equal(week.visibleCount, 1);
  assert.deepEqual(tagCounts(week), new Map([['近期', 2], ['研究', 1]]));
  assert.deepEqual(view(records, { mode: 'tags', range: '30', tag: '研究' }), month);
  assert.equal(view(records, { mode: 'tags' }).totalCount, 5);
});

test('calendar windows work across a year boundary', () => {
  const records = [
    record('new-year', '2027-01-01'), record('oldest', '2026-12-26'),
    record('outside', '2026-12-25'), record('future', '2027-01-02'),
  ];
  assert.deepEqual(ids(view(records, { range: '7', today: '2027-01-01' }).records),
    ['new-year', 'oldest']);
});

test('leap day is valid and thirty-day windows cross February correctly', () => {
  const records = [
    record('march', '2024-03-01'), record('leap', '2024-02-29'),
    record('oldest', '2024-02-01'), record('outside', '2024-01-31'),
    record('invalid-leap', '2023-02-29'),
  ];
  const result = view(records, { range: '30', today: '2024-03-01' });
  assert.deepEqual(ids(result.records), ['march', 'leap', 'oldest']);
  assert.equal(result.undatedCount, 0);
});

test('invalid dates remain in the final undated group only in all-time mode', () => {
  const invalid = ['2026-02-29', '2026-04-31', '2026-13-01', '2026-00-01',
    '2026-09-00', '2026-9-05', 'not-a-date', ''];
  const records = invalid.map((date, index) => record(`invalid-${index}`, date, ['待整理']));
  records.push(record('valid', '2026-09-05', ['已归档']));
  const all = view(records);
  assert.equal(all.totalCount, invalid.length + 1);
  assert.equal(all.visibleCount, invalid.length + 1);
  assert.equal(all.undatedCount, invalid.length);
  assert.deepEqual(all.groups.map(group => group.date), ['2026-09-05', '']);
  assert.equal(all.groups.at(-1).records.length, invalid.length);
  assert.ok(all.groups.at(-1).records.every(item => item.localDate === ''));
  for (const range of ['7', '30']) {
    const result = view(records, { range });
    assert.deepEqual(ids(result.records), ['valid']);
    assert.equal(result.undatedCount, 0);
    assert.deepEqual(tagCounts(result), new Map([['已归档', 1]]));
  }
});

test('empty inputs keep counters and groups coherent in either mode', () => {
  for (const mode of ['time', 'tags']) {
    for (const range of ['all', '7', '30']) {
      const result = view([], { mode, range });
      assert.deepEqual(result.records, []);
      assert.deepEqual(result.groups, []);
      assert.deepEqual(result.tags, []);
      assert.equal(result.totalCount, 0);
      assert.equal(result.visibleCount, 0);
      assert.equal(result.undatedCount, 0);
    }
  }
});

test('normalization and view building do not mutate input arrays, nested tags or options', () => {
  const records = [record('older', '2026-09-04', ['研究', '证据'], { themeTitles: ['证据优先'] }),
    record('newer', '2026-09-05')];
  const before = structuredClone(records);
  const options = deepFreeze({ mode: 'tags', tag: '研究', range: '7', today: '2026-09-05' });
  deepFreeze(records);
  assert.doesNotThrow(() => normalizeRecords(records));
  assert.doesNotThrow(() => buildView(records, options));
  assert.deepEqual(records, before);
  const normalized = normalizeRecords(records);
  assert.notEqual(normalized, records);
  assert.notEqual(normalized[0], records[0]);
  assert.notEqual(normalized[0].tags, records[0].topics);
  assert.notEqual(normalized[0].themeTitles, records[0].themeTitles);
});

test('hostile HTML remains inert literal data through normalization and tag selection', () => {
  const payload = '<img src=x onerror="globalThis.recordBrowserInjected=true">';
  const script = '<script>globalThis.recordBrowserInjected=true</script>';
  const source = record('hostile', '2026-09-05', [payload], {
    summary: script, source_app: payload, themeTitles: [script],
  });
  const result = view([source], { mode: 'tags', tag: payload });
  assert.equal(result.visibleCount, 1);
  assert.equal(result.records[0].summary, script);
  assert.equal(result.records[0].source, payload);
  assert.deepEqual(result.records[0].tags, [payload]);
  assert.deepEqual(result.records[0].themeTitles, [script]);
  assert.equal(result.tags[0].key, payload);
  assert.equal(result.tags[0].label, payload);
  assert.equal(globalThis.recordBrowserInjected, undefined);
});

test('canonical demo records remain complete and use existing topics and manual tags', () => {
  const result = view(fixture.records, { mode: 'tags', today: fixture.window.end });
  assert.equal(result.totalCount, fixture.records.length);
  assert.equal(result.visibleCount, fixture.records.length);
  assert.equal(result.undatedCount, 0);
  assert.equal(result.groups[0].date, fixture.window.end);
  assert.equal(result.groups.at(-1).date, fixture.window.start);
  const expected = new Map();
  for (const item of fixture.records) {
    const declaredTags = new Set([
      ...item.topics, ...(item.tags || []),
      ...(item.tag && item.tag !== '记录' ? [item.tag] : []),
    ]);
    for (const key of declaredTags.size ? declaredTags : ['__untagged__']) {
      expected.set(key, (expected.get(key) || 0) + 1);
    }
  }
  assert.deepEqual(tagCounts(result), expected);
  assert.equal(new Set(ids(result.records)).size, fixture.records.length);
  assert.equal(result.groups.flatMap(group => group.records).length, fixture.records.length);
});

test('Backend V2 compatibility fixture is accepted without live I/O', () => {
  const result = view(liveFixture.records, { today: liveFixture.window.end });
  assert.equal(result.totalCount, liveFixture.records.length);
  const originalById = new Map(liveFixture.records.map(item => [item.id, item]));
  for (const item of result.records) {
    const original = originalById.get(item.id);
    assert.equal(item.localDate, original.date);
    assert.equal(item.summary, original.summary || original.text);
    assert.deepEqual(item.tags, [...new Set(original.topics)]);
    assert.equal(item.source, original.source_app);
  }
  assert.deepEqual(ids(result.records), ids(liveFixture.records).reverse());
});
