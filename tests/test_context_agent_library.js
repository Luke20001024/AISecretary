import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

await import('../chrome-newtab/context-agent-library.js');
const contextAgent = globalThis.MementoContextAgent;
const OLDER_ID = 'ctx_111111111111111111111111';
const NEWER_ID = 'ctx_222222222222222222222222';
const SCOPE_ID = 'ctx_333333333333333333333333';
const INACTIVE_ID = 'ctx_444444444444444444444444';
const SAFE_ID = 'ctx_555555555555555555555555';

const evidence = [
  { file: '2026-08-08.md', line: 12, quote: '用户要求先看可验证的结果。' },
  { file: '2026-08-09.md', line: 4, quote: '用户再次要求先验证。' },
];

const older = {
  schema_version: '1.0',
  status: 'candidate',
  id: OLDER_ID,
  candidate_id: OLDER_ID,
  statement: '在做重要变更前先验证。',
  scope: '工程任务',
  category: 'work_preference',
  sensitive: false,
  uncertainty: 'low',
  why_now: '近期多次出现。',
  evidence,
  source_hashes: [
    { file: '2026-08-08.md', sha256: 'a'.repeat(64) },
    { file: '2026-08-09.md', sha256: 'b'.repeat(64) },
  ],
  provider: 'deepseek',
  model: 'deepseek-v4-pro',
  generation_key: 'gen_aaaaaaaaaaaaaaaaaaaaaaaa',
  created_at: '2026-08-09T10:00:00Z',
};

const newerEnvelope = {
  status: 'candidate',
  candidate: {
    schema_version: '1.0',
    id: NEWER_ID,
    candidate_id: NEWER_ID,
    statement: '把不确定的结论明确标出。',
    scope: '产品结论',
    category: 'constraint',
    sensitive: false,
    uncertainty: 'medium',
    why_now: '今天明确提出。',
    evidence,
    source_hashes: [
      { file: '2026-08-08.md', sha256: 'a'.repeat(64) },
      { file: '2026-08-09.md', sha256: 'b'.repeat(64) },
    ],
    provider: 'deepseek',
    model: 'deepseek-v4-pro',
    generation_key: 'gen_bbbbbbbbbbbbbbbbbbbbbbbb',
    created_at: '2026-08-10T10:00:00Z',
  },
};

const normalized = contextAgent.normalizeCandidate(newerEnvelope);
assert.equal(normalized.id, NEWER_ID);
assert.equal(normalized.scope, '产品结论');
assert.equal(normalized.evidence[1].file, '2026-08-09.md');
assert.equal(normalized.evidence[1].line, 4);
assert.equal(normalized.uncertainty, 'medium');
assert.equal(contextAgent.normalizeCandidate({ ...older, status: 'no_candidate' }), null);
assert.equal(contextAgent.normalizeCandidate({ ...older, sensitive: true }), null);
assert.equal(contextAgent.normalizeCandidate({ ...older, uncertainty: 'high' }), null);
assert.equal(contextAgent.normalizeCandidate({ ...older, uncertainty: 'unknown' }), null);
assert.equal(contextAgent.normalizeCandidate({ ...older, candidate_id: '../escape' }), null);
assert.equal(contextAgent.normalizeCandidate({ candidate_id: 'empty-statement' }), null);

let pending = contextAgent.selectPendingCandidate([older, newerEnvelope]);
assert.equal(pending.id, NEWER_ID, '只选择最新的一条待确认 Context');

pending = contextAgent.selectPendingCandidate([older, newerEnvelope], [{
  schema_version: '1.0',
  candidate_id: NEWER_ID,
  action: 'reject',
  decided_at: '2026-08-10T11:00:00Z',
}]);
assert.equal(pending.id, OLDER_ID, '已决策的候选不再显示');

const fixedNow = new Date('2026-08-10T12:00:00.000Z');
const crashedConfirmed = contextAgent.buildDecisionBundle(older, 'confirm', {}, fixedNow).confirmedContext;
pending = contextAgent.selectPendingCandidate(
  [older],
  [],
  [crashedConfirmed]
);
assert.equal(pending.id, OLDER_ID, 'confirmed 已写但 decision 缺失时候选必须重现以便恢复');
const recovered = contextAgent.buildRecoveryDecisionBundle(pending, crashedConfirmed);
assert.equal(recovered.recovery, true);
assert.equal(recovered.decision.action, 'confirm');
assert.equal(recovered.decision.decided_at, crashedConfirmed.confirmed_at);
assert.deepEqual(recovered.confirmedContext, crashedConfirmed, '恢复不得改写 confirmed_at 或已授权内容');
assert.throws(
  () => contextAgent.buildRecoveryDecisionBundle(pending, {
    ...crashedConfirmed,
    statement: '不同的已确认内容',
  }),
  /不一致/
);

const confirmed = contextAgent.buildDecisionBundle(older, 'confirm', {}, fixedNow);
assert.deepEqual(confirmed.decision, {
  schema_version: '1.0',
  candidate_id: OLDER_ID,
  action: 'confirm',
  decided_at: '2026-08-10T12:00:00.000Z',
});
assert.equal(confirmed.confirmedContext.original_candidate_id, OLDER_ID);
assert.equal(confirmed.confirmedContext.id, OLDER_ID);
assert.equal(confirmed.confirmedContext.decision_action, 'confirm');
assert.equal('context_id' in confirmed.confirmedContext, false);
assert.equal(confirmed.confirmedContext.statement, older.statement);
assert.equal(confirmed.confirmedContext.scope, '工程任务');
assert.equal(confirmed.confirmedContext.status, 'active');
assert.deepEqual(confirmed.confirmedContext.evidence[0], {
  file: '2026-08-08.md',
  line: 12,
  quote: '用户要求先看可验证的结果。',
});
assert.equal(confirmed.confirmedContext.source_hashes[0].sha256, 'a'.repeat(64));
assert.deepEqual(Object.keys(confirmed.confirmedContext), [
  'schema_version',
  'id',
  'original_candidate_id',
  'status',
  'confirmed_at',
  'decision_action',
  'statement',
  'scope',
  'category',
  'evidence',
  'source_hashes',
], 'Dashboard Confirmed JSON must match the strict CLI field contract');

const scoped = contextAgent.buildDecisionBundle(older, 'scope', {
  scope: '仅 Memento 仓库',
}, fixedNow);
assert.equal(scoped.decision.scope, '仅 Memento 仓库');
assert.equal(scoped.confirmedContext.scope, '仅 Memento 仓库');

const edited = contextAgent.buildDecisionBundle(older, 'edit', {
  statement: '重要变更前，只做与风险匹配的验证。',
}, fixedNow);
assert.equal(edited.decision.statement, '重要变更前，只做与风险匹配的验证。');
assert.equal(edited.confirmedContext.statement, edited.decision.statement);

for (const action of ['just_once', 'reject']) {
  const bundle = contextAgent.buildDecisionBundle(older, action, {}, fixedNow);
  assert.equal(bundle.decision.action, action);
  assert.equal(bundle.confirmedContext, null, `${action} 不应写入长期 Context`);
}
assert.equal(
  contextAgent.buildDecisionBundle(older, 'just_once', {}, fixedNow).oneTimeContext.original_candidate_id,
  OLDER_ID
);
assert.deepEqual(
  contextAgent.buildDecisionBundle(older, 'just_once', {}, fixedNow).decision.one_time_context,
  contextAgent.buildDecisionBundle(older, 'just_once', {}, fixedNow).oneTimeContext,
  '单次 Context 应跟决策持久化，但不进入 Confirmed'
);
assert.equal(contextAgent.buildDecisionBundle(older, 'reject', {}, fixedNow).oneTimeContext, null);

assert.throws(
  () => contextAgent.buildDecisionBundle(older, 'scope', { scope: '  ' }, fixedNow),
  /范围/
);
assert.throws(
  () => contextAgent.buildDecisionBundle(older, 'scope', { scope: 'x'.repeat(161) }, fixedNow),
  /160/
);
assert.throws(
  () => contextAgent.buildDecisionBundle(older, 'edit', { statement: 'x'.repeat(401) }, fixedNow),
  /400/
);
assert.throws(
  () => contextAgent.buildDecisionBundle(older, 'edit', { statement: '' }, fixedNow),
  /理解/
);
assert.throws(
  () => contextAgent.buildDecisionBundle(older, 'delete', {}, fixedNow),
  /不支持/
);
for (const sensitiveText of [
  '用户正在经历情绪低落',
  '用户今天情绪很好',
  '该任务适用于心理状态不稳定时',
  'user is emotionally unstable',
]) {
  assert.equal(contextAgent.containsSensitiveText({ statement: sensitiveText }), true);
}
assert.throws(
  () => contextAgent.buildDecisionBundle(older, 'edit', {
    statement: '用户处于情绪低落时需要更多提醒',
  }, fixedNow),
  /敏感/
);
assert.throws(
  () => contextAgent.buildDecisionBundle(older, 'scope', {
    scope: '心理状态不稳定时',
  }, fixedNow),
  /敏感/
);

const activeContexts = contextAgent.activeConfirmedContexts([
  confirmed.confirmedContext,
  { ...scoped.confirmedContext, id: SCOPE_ID, original_candidate_id: SCOPE_ID },
  { ...edited.confirmedContext, id: INACTIVE_ID, original_candidate_id: INACTIVE_ID, status: 'inactive' },
  { broken: true },
]);
assert.equal(activeContexts.length, 2);

const pack = contextAgent.buildContextPack(activeContexts, { now: fixedNow });
assert.match(pack, /^# Memento Context Pack/);
assert.match(pack, /在做重要变更前先验证/);
assert.match(pack, /范围：仅 Memento 仓库/);
assert.doesNotMatch(pack, /2026-08-08\.md:12|用户要求先看可验证的结果/);
assert.doesNotMatch(pack, new RegExp(INACTIVE_ID));
const oneTimePack = contextAgent.buildOneTimeContextPack(
  contextAgent.buildDecisionBundle(older, 'just_once', {}, fixedNow).oneTimeContext
);
assert.match(oneTimePack, /仅用于当前这一次任务/);
assert.match(oneTimePack, /未进入长期 Context/);
assert.doesNotMatch(oneTimePack, /2026-08-08\.md|用户要求先看可验证的结果/);
assert.equal(contextAgent.recordFileName(SAFE_ID), `${SAFE_ID}.json`);
assert.throws(() => contextAgent.recordFileName('../unsafe'), /Context ID/);
assert.equal(
  contextAgent.assertCompatibleRecord(
    { action: 'confirm', candidate_id: OLDER_ID },
    { candidate_id: OLDER_ID, action: 'confirm' },
    `${OLDER_ID}.json`
  ),
  'identical',
  '字段顺序不同的同一 JSON 可幂等重试'
);
assert.throws(
  () => contextAgent.assertCompatibleRecord(
    { candidate_id: OLDER_ID, action: 'reject' },
    { candidate_id: OLDER_ID, action: 'confirm' },
    `${OLDER_ID}.json`
  ),
  error => error.name === 'ContextConflictError' && /拒绝覆盖/.test(error.message),
  '同 ID 的不同决策必须拒绝覆盖'
);

const sourceBacking = new Map([
  ['2026-08-08.md', {
    sha256: 'a'.repeat(64),
    lines: [...Array(11).fill(''), '用户要求先看可验证的结果。'],
  }],
  ['2026-08-09.md', {
    sha256: 'b'.repeat(64),
    lines: ['', '', '', '用户再次要求先验证。'],
  }],
]);
assert.deepEqual(contextAgent.verifySourceBacking(older, sourceBacking), { valid: true, reason: '' });
const staleBacking = new Map(sourceBacking);
staleBacking.set('2026-08-08.md', {
  ...sourceBacking.get('2026-08-08.md'),
  sha256: 'f'.repeat(64),
});
assert.equal(contextAgent.verifySourceBacking(older, staleBacking).reason, 'stale');
const wrongQuoteBacking = new Map(sourceBacking);
wrongQuoteBacking.set('2026-08-09.md', {
  ...sourceBacking.get('2026-08-09.md'),
  lines: ['', '', '', '被修改的原文'],
});
assert.equal(contextAgent.verifySourceBacking(older, wrongQuoteBacking).reason, 'evidence');
assert.equal(contextAgent.verifySourceBacking({
  ...older,
  source_hashes: older.source_hashes.slice(0, 1),
}, sourceBacking).reason, 'unhashed-evidence');
const spacedQuote = '  保留首尾空白  ';
const spacedCandidate = {
  ...older,
  evidence: [
    { file: '2026-08-08.md', line: 12, quote: spacedQuote },
    older.evidence[1],
  ],
};
const normalizedSpaced = contextAgent.normalizeCandidate(spacedCandidate);
assert.equal(normalizedSpaced.evidence[0].quote, spacedQuote, 'evidence.quote 不得 trim');
const spacedBacking = new Map(sourceBacking);
spacedBacking.set('2026-08-08.md', {
  sha256: 'a'.repeat(64),
  lines: [...Array(11).fill(''), spacedQuote],
});
assert.equal(contextAgent.verifySourceBacking(spacedCandidate, spacedBacking).valid, true);
spacedBacking.set('2026-08-08.md', {
  sha256: 'a'.repeat(64),
  lines: [...Array(11).fill(''), spacedQuote.trim()],
});
assert.equal(contextAgent.verifySourceBacking(spacedCandidate, spacedBacking).reason, 'evidence');

const dashboardSource = readFileSync(new URL('../chrome-newtab/dashboard.js', import.meta.url), 'utf8');
const refreshStart = dashboardSource.indexOf('async function refreshContextAgentData');
const refreshEnd = dashboardSource.indexOf('function contextTempName', refreshStart);
const refreshSource = dashboardSource.slice(refreshStart, refreshEnd);
assert.ok(refreshSource.includes('await readContextSourceBacking'), '刷新 Context 必须重读原始来源');
assert.ok(refreshSource.includes('verifiedContextRecords'), '刷新 Context 必须过滤 stale 候选和 Confirmed');

const decideStart = dashboardSource.indexOf('async function applyContextAgentDecision');
const decideEnd = dashboardSource.indexOf('async function showContextPackPreview', decideStart);
const decideSource = dashboardSource.slice(decideStart, decideEnd);
assert.ok(
  decideSource.indexOf('readContextSourceBacking') < decideSource.indexOf('writeContextJsonAtomically'),
  '每次决策落盘前必须再校验候选来源'
);

const packStart = dashboardSource.indexOf('async function showContextPackPreview');
const packEnd = dashboardSource.indexOf('async function copyOneTimeContextPack', packStart);
const packSource = dashboardSource.slice(packStart, packEnd);
assert.ok(
  packSource.indexOf('await refreshContextAgentData') < packSource.indexOf('buildContextPack'),
  '生成浏览器 Context Pack 前必须刷新并校验来源'
);

const dashboardHtml = readFileSync(new URL('../chrome-newtab/dashboard.html', import.meta.url), 'utf8');
assert.ok(dashboardHtml.includes('id="context-tab"'));
assert.ok(dashboardHtml.includes('id="context-drawer"'));
assert.ok(
  dashboardHtml.indexOf('context-agent-library.js') < dashboardHtml.indexOf('dashboard.js'),
  'Context 数据层必须先于 Dashboard 加载'
);
assert.doesNotMatch(dashboardSource, /DEEPSEEK_API_KEY|api\.deepseek\.com/);

// 跨语言互操作：JS 生成的 Confirmed/just_once 直接交给 Python core 合同校验。
const interopRoot = mkdtempSync(join(tmpdir(), 'memento-context-js-python-'));
try {
  const quoteOne = '  用户要求先验证重要变更。  ';
  const quoteTwo = '用户再次要求保留可验证证据。';
  const sourceOne = `${[...Array(11).fill(''), quoteOne].join('\n')}\n`;
  const sourceTwo = `${['', '', '', quoteTwo].join('\n')}\n`;
  writeFileSync(join(interopRoot, '2026-08-08.md'), sourceOne);
  writeFileSync(join(interopRoot, '2026-08-09.md'), sourceTwo);
  const digest = value => createHash('sha256').update(value).digest('hex');
  const interopCandidate = {
    ...older,
    id: 'ctx_666666666666666666666666',
    candidate_id: 'ctx_666666666666666666666666',
    evidence: [
      { file: '2026-08-08.md', line: 12, quote: quoteOne },
      { file: '2026-08-09.md', line: 4, quote: quoteTwo },
    ],
    source_hashes: [
      { file: '2026-08-08.md', sha256: digest(sourceOne) },
      { file: '2026-08-09.md', sha256: digest(sourceTwo) },
    ],
  };
  const interopConfirmed = contextAgent.buildDecisionBundle(
    interopCandidate,
    'confirm',
    {},
    fixedNow
  ).confirmedContext;
  const interopDecision = contextAgent.buildDecisionBundle(
    interopCandidate,
    'just_once',
    {},
    fixedNow
  ).decision;
  const interopRecovery = contextAgent.buildRecoveryDecisionBundle(
    interopCandidate,
    interopConfirmed
  ).decision;
  const confirmedPath = join(interopRoot, 'confirmed.json');
  const decisionPath = join(interopRoot, 'decision.json');
  const recoveryPath = join(interopRoot, 'recovery.json');
  writeFileSync(confirmedPath, `${JSON.stringify(interopConfirmed)}\n`);
  writeFileSync(decisionPath, `${JSON.stringify(interopDecision)}\n`);
  writeFileSync(recoveryPath, `${JSON.stringify(interopRecovery)}\n`);

  const repoRoot = dirname(dirname(fileURLToPath(import.meta.url)));
  const python = spawnSync('python3', ['-c', `
import json
import sys
from pathlib import Path
import core

vault = Path(sys.argv[1])
confirmed = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
decision = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
recovery = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
core.validate_confirmed(confirmed, vault)
assert set(confirmed) == core.CONFIRMED_FIELDS
assert set(decision) == {"schema_version", "candidate_id", "action", "decided_at", "one_time_context"}
assert set(decision["one_time_context"]) == {
    "statement", "scope", "category", "evidence", "source_hashes", "original_candidate_id"
}
assert recovery == {
    "schema_version": "1.0",
    "candidate_id": confirmed["id"],
    "action": "confirm",
    "decided_at": confirmed["confirmed_at"],
}
sensitive = {
    "statement": "用户今天情绪很好",
    "scope": confirmed["scope"],
    "why_now": "用户已确认的 Context",
    "category": confirmed["category"],
    "sensitive": False,
    "uncertainty": "low",
    "evidence": confirmed["evidence"],
}
try:
    core.validate_candidate_body(sensitive, vault)
    raise AssertionError("Python core accepted emotional-state inference")
except core.ContractError as exc:
    assert exc.kind == "sensitive"
`, interopRoot, confirmedPath, decisionPath, recoveryPath], {
    cwd: join(repoRoot, 'context-agent'),
    encoding: 'utf8',
  });
  assert.equal(python.status, 0, `Python core rejected JS records:\n${python.stderr}`);
} finally {
  rmSync(interopRoot, { recursive: true, force: true });
}

console.log('✓ Context Agent library: validates candidates, records five decisions, and packs only active confirmed Context');
