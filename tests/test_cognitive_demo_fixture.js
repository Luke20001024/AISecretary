'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const fixturePath = path.join(root, 'chrome-newtab', 'cognitive-demo-fixture.js');
const source = fs.readFileSync(fixturePath, 'utf8');
const demo = require(fixturePath);
const cognitive = require(path.join(root, 'chrome-newtab', 'cognitive-home-library.js'));
const remember = require(path.join(root, 'chrome-newtab', 'remember-agent-v1-library.js'));

const browserSandbox = { window: {}, TextEncoder, TextDecoder };
vm.runInNewContext(source, browserSandbox, { filename: 'cognitive-demo-fixture.js' });
assert.equal(typeof browserSandbox.window.MementoCognitiveDemoFixture?.createFixture, 'function',
  '浏览器脚本必须暴露 dashboard 读取的唯一全局');

const first = demo.createFixture();
const second = demo.createFixture();

assert.deepEqual(first, second, 'fixture 必须完全确定');
assert.notEqual(first, second, '每次读取必须返回新的对象');
first.records[0].text = '调用方本地改动';
assert.notEqual(first.records[0].text, demo.createFixture().records[0].text,
  '调用方修改不得污染基准 fixture');

const fixture = demo.createFixture();
assert.equal(fixture.mode, 'synthetic');
assert.match(fixture.syntheticNotice, /合成演示数据/);
assert.deepEqual(fixture.capabilities, {
  persistence: false,
  filesystem: false,
  externalCalls: false,
  formalWrites: false,
});
assert.deepEqual(fixture.window, {
  start: '2026-07-30',
  end: '2026-08-18',
  days: 20,
});
assert.equal(fixture.history.length, 20);
assert.equal(fixture.history[0].date, fixture.window.start);
assert.equal(fixture.history.at(-1).date, fixture.window.end);
assert.equal(fixture.history.filter(day => day.empty).length, 2, '应保留少量自然空日');
assert.equal(fixture.records.length, 261, '唯一固定版本必须稳定提供 261 条记录');
assert.equal(fixture.stats.totalRecords, fixture.records.length);
assert.equal(fixture.stats.activeDays, 18);
assert.equal(fixture.legacyFiles.length, 18);

const weekendCounts = fixture.history
  .filter(day => ['周六', '周日'].includes(day.weekday))
  .map(day => day.count);
const weekdayCounts = fixture.history
  .filter(day => !['周六', '周日'].includes(day.weekday))
  .map(day => day.count);
assert.ok(Math.max(...weekendCounts) <= 10, '周末节奏应显著更轻');
assert.ok(weekdayCounts.filter(count => count >= 12).length >= 10,
  '工作日应形成稳定记录密度');
const activeCounts = fixture.history.filter(day => !day.empty).map(day => day.count);
assert.ok(activeCounts.every(count => count >= 8), '非空日每天至少应有 8 条微记录');
assert.ok(Math.max(...activeCounts) - Math.min(...activeCounts) >= 10,
  '每日数量必须有明显波动');
assert.ok(activeCounts.some(count => count >= 20), '至少应有一个高密记录日');
assert.ok(activeCounts.some(count => count >= 8 && count <= 10), '至少应有一个低密记录日');
assert.ok(fixture.home.records.length >= 12 && fixture.home.records.length <= 16,
  '今天应保持 12–16 条可读样本');

const requiredStatuses = new Set([
  'raw_saved', 'processing', 'ready', 'needs_review', 'original_only',
  'no_candidate', 'merged',
]);
const observedStatuses = new Set(fixture.records.map(record => record.status));
for (const status of requiredStatuses) {
  assert.ok(observedStatuses.has(status), `必须覆盖 ${status}`);
}
assert.deepEqual(
  new Set(fixture.home.records.map(record => record.status)),
  requiredStatuses,
  '今天应完整覆盖基础状态样本'
);

assert.equal(fixture.themes.length, 6);
assert.ok(fixture.themes.every(theme => Array.from(theme.title).length <= 18));
assert.ok(fixture.themes.every(theme => /^[\p{Script=Han}]{2,8}$/u.test(theme.title)),
  '地景标题应为 2–8 个汉字的聚合标签');
assert.ok(fixture.themes.every(theme => typeof theme.boundary === 'string'
  && Array.from(theme.boundary).length >= 20),
  '每个第一层聚合主题必须保留明确适用边界');
const subpeaks = fixture.themes.flatMap(theme => theme.subpeaks || []);
assert.ok(fixture.themes.every(theme => theme.subpeaks.length >= 2),
  '每个主题必须展开成至少两个次级倾向峰');
assert.ok(fixture.themes.length + subpeaks.length >= 18,
  '地形应至少包含 18 个主峰与次峰');
assert.ok(subpeaks.every(subpeak => /^[\p{Script=Han}]{2,6}$/u.test(subpeak.title)),
  '次级倾向必须使用可在放大后直接阅读的短标签');
assert.ok(subpeaks.some(subpeak => subpeak.x <= .05)
  && subpeaks.some(subpeak => subpeak.x >= .95)
  && subpeaks.some(subpeak => subpeak.y <= .05)
  && subpeaks.some(subpeak => subpeak.y >= .9),
  '次峰必须扩展到地形边缘，避免六个等距大圆包');
assert.ok(fixture.themes.every(theme => Number.isFinite(theme.terrain?.spreadX)
  && Number.isFinite(theme.terrain?.spreadY) && Number.isFinite(theme.terrain?.angle)),
  '主峰必须有独立展宽和方向，避免统一圆形地形');
const landscapeNodeIds = new Set(fixture.landscape.nodes.map(node => node.memory_ref.id));
assert.ok(subpeaks.every(subpeak => landscapeNodeIds.has(subpeak.memoryId)),
  '每个次峰必须绑定一个已提交可用记忆，不得是无来源的装饰山头');
assert.ok(subpeaks.every(subpeak => typeof subpeak.recent === 'boolean'),
  '每个次峰都必须显式声明是否为近期变化');
const landscapeNodeById = new Map(fixture.landscape.nodes.map(node => [node.memory_ref.id, node]));
assert.ok(subpeaks.every(subpeak => {
  const node = landscapeNodeById.get(subpeak.memoryId);
  return node && node.x === subpeak.x && node.y === subpeak.y && node.recent === subpeak.recent;
}), '次峰坐标和近期状态必须与正式 memory node 精确一致');
assert.equal(fixture.agentMemories.length, fixture.themes.length);
assert.equal(fixture.portrait.length, 4);
assert.deepEqual(fixture.portrait.map(item => item.maturity), [
  'forming', 'stable', 'stable', 'forming',
]);
assert.ok(fixture.portrait.every(item => ['forming', 'stable'].includes(item.maturity)),
  '每条第三层理解必须显式声明形成中或已稳定');
assert.ok(fixture.portrait.every(item => item.boundary.length >= 20));
assert.ok(fixture.portrait.every(item => !item.boundary.includes('合成演示')),
  '第三层可见边界不得暴露内部 fixture 术语');
assert.ok(fixture.changes.some(item => item.kind === 'revision'));
assert.ok(fixture.changes.some(item => item.kind === 'tension'));

const normalizedTexts = fixture.records.map(record => record.text.replace(/\s+/gu, ' ').trim());
assert.ok(normalizedTexts.every(text => !/(?:这一轮最值得保留的是|准备下一轮评审时发现)。/u.test(text)),
  '记录模板不得出现谓语与正文被句号截断的病句');
assert.ok(new Set(normalizedTexts).size / normalizedTexts.length >= 0.95,
  '微记录的完整文本重复率不得超过 5%');
const textLengths = normalizedTexts.map(text => Array.from(text).length);
assert.ok(textLengths.filter(length => length < 40).length >= fixture.records.length * 0.25,
  '至少四分之一应保持随手短记节奏');
assert.ok(textLengths.filter(length => length >= 65).length >= fixture.records.length * 0.1,
  '应混入足量较长思考记录');
const closingFrequency = new Map();
normalizedTexts.forEach(text => {
  const closing = text.split(/[。！？]/u).filter(Boolean).at(-1) || '';
  closingFrequency.set(closing, (closingFrequency.get(closing) || 0) + 1);
});
assert.ok(Math.max(...closingFrequency.values()) <= fixture.records.length * 0.06,
  '不得机械重复同一收尾句');
assert.ok(fixture.records.filter(record => record.topics.length > 1).length >= 30,
  '应包含足量跨主题微记录');
assert.ok(fixture.records.filter(record => record.topics.length === 0).length >= 20,
  '应包含只保存或尚未形成主题的记录');
for (const status of ['raw_saved', 'processing']) {
  assert.ok(fixture.records.filter(record => record.status === status).length >= 3,
    `${status} 应有多个样本`);
}

const recordIds = new Set(fixture.records.map(record => record.id));
const recordById = new Map(fixture.records.map(record => [record.id, record]));
assert.equal(recordIds.size, fixture.records.length);
assert.deepEqual(new Set(Object.keys(fixture.rawRecordsById)), recordIds);
for (const record of fixture.records) {
  assert.equal(record.record_ref.id, record.id);
  assert.equal(record.date, record.captured_at.slice(0, 10));
  assert.equal(typeof fixture.rawRecordsById[record.id].text, 'string');
  assert.ok(fixture.rawRecordsById[record.id].text.length > 10);
}
for (const day of fixture.history) {
  assert.equal(day.count, day.records.length);
  assert.ok(day.records.every(id => recordIds.has(id)));
  const times = day.records.map(id => recordById.get(id).time);
  assert.deepEqual(times, [...times].sort(), `${day.date} 当日记录必须按时间稳定排序`);
}
assert.deepEqual(new Set(fixture.history.flatMap(day => day.records)), recordIds,
  '20 天轨迹必须能覆盖全部 261 条记录');
const activeTimeSignatures = fixture.history.filter(day => !day.empty).map(day => (
  day.records.slice(0, 6).map(id => recordById.get(id).time).join(',')
));
assert.ok(new Set(activeTimeSignatures).size >= 12,
  '确定性分钟抖动应避免多数日期复用完全相同的时间序列');
for (const theme of fixture.themes) {
  assert.ok(theme.evidenceRecordIds.length >= 20 && theme.evidenceRecordIds.length <= 35);
  assert.ok(theme.counterRecordIds.length >= 1 && theme.counterRecordIds.length <= 3);
  assert.ok(theme.evidenceRecordIds.every(id => recordIds.has(id)));
  assert.ok(theme.counterRecordIds.every(id => recordIds.has(id)));
  assert.ok(theme.counterRecordIds.every(id => !theme.evidenceRecordIds.includes(id)),
    '反例应与支持依据分开');
  assert.ok(fixture.agentMemories.some(memory => memory.memoryId === theme.understandingId));
  assert.ok(theme.relatedPortraitIds.every(id => fixture.portrait.some(item => item.id === id)));
}
const themeIds = new Set(fixture.themes.map(theme => theme.id));
assert.ok(fixture.portrait.every(item => item.themeIds.every(id => themeIds.has(id))));
assert.ok(fixture.changes.every(item => themeIds.has(item.themeId)
  && item.evidenceRecordIds.every(id => recordIds.has(id))));

const receiptIds = new Set(fixture.receipts.map(item => item.ref.id));
const reusableIds = new Set(fixture.reusableMemories.map(item => item.ref.id));
const understandingIds = new Set(fixture.agentMemories.map(item => item.memoryId));
const receiptById = new Map(fixture.receipts.map(item => [item.ref.id, item]));
const legacyFileByName = new Map(fixture.legacyFiles.map(file => [file.name, file]));
const sameRef = (left, right) => left && right
  && left.kind === right.kind && left.id === right.id
  && left.revision === right.revision
  && left.revision_sha256 === right.revision_sha256;
const assertSpanBacking = span => {
  const file = legacyFileByName.get(span.source_file);
  assert.ok(file, `${span.source_file} 必须存在于 seed 文件`);
  assert.ok(file.text.split('\n')[span.line_start - 1]?.includes(span.quote),
    `${span.source_file}:${span.line_start} 必须能定位原始引文`);
};
for (const record of fixture.records) {
  if (record.receipt_ref) {
    assert.ok(sameRef(record.receipt_ref, receiptById.get(record.receipt_ref.id)?.ref),
      'record.receipt_ref 必须绑定当前回执');
  }
  assert.ok(record.memory_refs.every(ref => reusableIds.has(ref.id)));
  assert.ok(record.understanding_refs.every(ref => understandingIds.has(ref.id)));
}
for (const item of fixture.receipts) {
  assert.ok(recordIds.has(item.value.record_ref.id));
  assert.equal(item.ref.revision_sha256,
    cognitive.sha256Hex(cognitive.canonicalJson(item.value)));
  item.value.source_spans.forEach(assertSpanBacking);
}
for (const item of fixture.reusableMemories) {
  assert.equal(item.ref.id, item.value.memory_id);
  assert.equal(item.ref.revision_sha256,
    cognitive.sha256Hex(cognitive.canonicalJson(item.value)));
  assert.ok(item.value.origin_receipt_refs.every(ref => receiptIds.has(ref.id)));
  assert.ok(item.value.source_spans.every(span => recordIds.has(span.record_id)));
  item.value.source_spans.forEach(assertSpanBacking);
}
for (const item of fixture.relations) {
  assert.equal(item.ref.id, item.value.relation_id);
  assert.equal(item.ref.revision_sha256,
    cognitive.sha256Hex(cognitive.canonicalJson(item.value)));
  for (const endpoint of [item.value.from_ref, item.value.to_ref]) {
    assert.ok(reusableIds.has(endpoint.id) || understandingIds.has(endpoint.id));
  }
  assert.ok(item.value.source_spans.every(span => recordIds.has(span.record_id)));
  item.value.source_spans.forEach(assertSpanBacking);
}

const normalizedProfile = remember.normalizeAgentProfile(fixture.agentProfileRecord);
assert.ok(normalizedProfile, '原始 Agent profile 应通过生产归一化合同');
assert.deepEqual(normalizedProfile, fixture.agentProfile);
assert.deepEqual(fixture.agentProfile.memories, fixture.agentMemories);
for (const memory of fixture.agentMemories) {
  for (const evidence of [...memory.evidence, ...memory.counterevidence]) {
    const file = legacyFileByName.get(evidence.file);
    assert.ok(file);
    assert.ok(file.text.split('\n')[evidence.line - 1]?.includes(evidence.quote));
  }
}

cognitive.validateHomeProjection(fixture.home);
cognitive.validateLandscapeSnapshot(fixture.landscape);
cognitive.validateProjectionPair(fixture.home, fixture.landscape, fixture.landscapeSha256);
cognitive.validateProjectionAuthority(
  fixture.home, fixture.landscape, fixture.projectionAuthority
);

function walk(value, visitor, pathParts = []) {
  visitor(value, pathParts);
  if (Array.isArray(value)) {
    value.forEach((item, index) => walk(item, visitor, [...pathParts, String(index)]));
  } else if (value && typeof value === 'object') {
    Object.entries(value).forEach(([key, item]) => walk(item, visitor, [...pathParts, key]));
  }
}

walk(fixture, (value, pathParts) => {
  const key = pathParts.at(-1) || '';
  assert.doesNotMatch(key, /provider/i, 'fixture 不得携带 Provider 字段');
  if (typeof value !== 'string') return;
  assert.doesNotMatch(value, /(?:^|\s)(?:\/Users\/|\/home\/|[A-Za-z]:\\)/,
    'fixture 不得含绝对路径');
  assert.doesNotMatch(value, /\b(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._~+/-]{12,})\b/i,
    'fixture 不得含密钥形态');
});

for (const pattern of [
  /\bfetch\s*\(/,
  /XMLHttpRequest/,
  /WebSocket/,
  /navigator\.storage/,
  /showDirectoryPicker/,
  /getFileHandle/,
  /writeFile\s*\(/,
  /appendFile\s*\(/,
]) {
  assert.doesNotMatch(source, pattern, `fixture 源码不得包含外部调用或持久化能力：${pattern}`);
}

const beforeAppend = demo.createFixture();
const appended = demo.appendLocalRecord(beforeAppend, {
  text: '先记录入口是否容易找到，整理与长期积累继续使用演示数据。',
  capturedAt: '2026-08-18T20:12:00+08:00',
});
assert.equal(beforeAppend.records.length, fixture.records.length,
  '纯内存追加不得修改调用方原对象');
assert.equal(appended.records.length, fixture.records.length + 1);
assert.equal(appended.home.records.length, fixture.home.records.length + 1);
assert.equal(appended.home.today_status.saved, fixture.home.today_status.saved + 1);
assert.equal(appended.records.find(record => (
  record.text === '先记录入口是否容易找到，整理与长期积累继续使用演示数据。'
)).status, 'raw_saved');
assert.ok(appended.legacyFiles.find(file => file.date === '2026-08-18').text.includes('20:12'));
cognitive.validateHomeProjection(appended.home);
cognitive.validateProjectionPair(appended.home, appended.landscape, appended.landscapeSha256);
cognitive.validateProjectionAuthority(
  appended.home, appended.landscape, appended.projectionAuthority
);

assert.throws(() => demo.appendLocalRecord(fixture, {
  text: '日期越界', capturedAt: '2026-08-19T09:00:00+08:00',
}), /固定演示日/);

console.log('cognitive demo fixture tests passed');
