'use strict';

const assert = require('node:assert/strict');
const library = require('../chrome-newtab/cognitive-home-library.js');

const A = 'a'.repeat(64);
const B = 'b'.repeat(64);
const C = 'c'.repeat(64);
const D = 'd'.repeat(64);
const E = 'e'.repeat(64);
const F = 'f'.repeat(64);
const RECORD_ID = 'rec_111111111111111111111111';
const RECEIPT_ID = 'rcp_813864bbcc409b9290d17579';
const MEMORY_ID = 'rmem_333333333333333333333333';
const RELATION_ID = 'rel_444444444444444444444444';
const UNDERSTANDING_ID = 'mem_555555555555555555555555';
const PEAK_ID = 'peak_555555555555555555555555';
const SECOND_UNDERSTANDING_ID = 'mem_777777777777777777777777';
const SECOND_PEAK_ID = 'peak_777777777777777777777777';
const SNAPSHOT_ID = 'lnd_666666666666666666666666';

function clone(value) {
  return structuredClone(value);
}

function ref(kind, id, revision, revisionSha256) {
  return { kind, id, revision, revision_sha256: revisionSha256 };
}

function makeLandscape() {
  return {
    schema_version: '1.0',
    kind: 'memento_landscape_snapshot',
    snapshot_id: SNAPSHOT_ID,
    created_at: '2026-08-18T21:00:00+08:00',
    as_of: '2026-08-18',
    projection_version: 'cognitive-landscape-v1',
    input_hashes: {
      agent_profile_sha256: A,
      reusable_memory_head_sha256: B,
      relation_head_sha256: C,
      user_action_watermark_sha256: D,
    },
    summary: {
      active_understandings: 1,
      recent_changes: 1,
      observing_candidates: 0,
    },
    terrain: {
      algorithm_version: 'stable-anchor-kde-v1',
      grid_size: 96,
      contour_levels: 18,
      coordinate_space: 'normalized_0_1',
    },
    peaks: [{
      peak_id: PEAK_ID,
      understanding_ref: ref('understanding', UNDERSTANDING_ID, 2, A),
      x: 0.72,
      y: 0.31,
      elevation: 0.83,
      evidence_count: 8,
      counterevidence_count: 1,
      recent_change: true,
      lifecycle: 'tension',
    }],
    nodes: [{
      memory_ref: ref('reusable_memory', MEMORY_ID, 3, B),
      x: 0.51,
      y: 0.47,
      state: 'committed',
      recent: true,
    }],
    edges: [{
      relation_ref: ref('relation', RELATION_ID, 1, C),
      from_id: MEMORY_ID,
      to_id: UNDERSTANDING_ID,
      type: 'supports',
    }],
    previous_snapshot_sha256: E,
  };
}

function makeHome(landscapeSha256) {
  return {
    schema_version: '1.0',
    kind: 'memento_home_projection',
    projection_version: 'cognitive-secretary-home-v1',
    generated_at: '2026-08-18T21:00:01+08:00',
    local_date: '2026-08-18',
    input_hashes: {
      record_head_sha256: E,
      receipt_head_sha256: F,
      daily_bundle_head_sha256: A,
      agent_profile_sha256: A,
      landscape_snapshot_sha256: landscapeSha256,
      user_action_watermark_sha256: D,
    },
    landscape_ref: {
      snapshot_id: SNAPSHOT_ID,
      snapshot_sha256: landscapeSha256,
    },
    landscape_summary: {
      active_understandings: 1,
      recent_changes: 1,
      observing_candidates: 0,
    },
    today_status: {
      saved: 1,
      interpreted: 1,
      merged: 1,
      needs_review: 0,
      daily_run_status: 'committed',
    },
    records: [{
      record_ref: ref('source_record', RECORD_ID, 1, E),
      receipt_ref: ref('interpretation_receipt', RECEIPT_ID, 1, F),
      captured_at: '2026-08-18T10:12:00+08:00',
      source_type: 'voice_transcript',
      source_app: 'Memento Voice Capture',
      status: 'merged',
      summary: '先交付一个可验证的小版本。',
      content_types: ['observation'],
      topics: ['产品设计'],
      purposes: ['future_decision'],
      memory_refs: [ref('reusable_memory', MEMORY_ID, 3, B)],
      understanding_refs: [ref('understanding', UNDERSTANDING_ID, 2, A)],
    }],
    schedule: {
      enabled: true,
      hour: 21,
      minute: 0,
      next_due_at: '2026-08-19T21:00:00+08:00',
      last_run_status: 'committed',
    },
    warnings: [],
  };
}

function syncTodayCounts(value) {
  value.today_status.saved = value.records.length;
  value.today_status.interpreted = value.records.filter(record => (
    record.receipt_ref !== null || record.status === 'no_candidate'
  )).length;
  value.today_status.merged = value.records.filter(record => record.status === 'merged').length;
  value.today_status.needs_review = value.records.filter(record => record.status === 'needs_review').length;
  return value;
}

function makeAuthorizedFixture() {
  const authorizedLandscape = makeLandscape();
  const memoryRefs = authorizedLandscape.nodes.map(node => node.memory_ref);
  const relationRefs = authorizedLandscape.edges.map(edge => edge.relation_ref);
  authorizedLandscape.input_hashes.reusable_memory_head_sha256 = library.sha256Hex(
    library.canonicalJson(memoryRefs)
  );
  authorizedLandscape.input_hashes.relation_head_sha256 = library.sha256Hex(
    library.canonicalJson(relationRefs)
  );
  const authorizedLandscapeSha = library.sha256Hex(JSON.stringify(authorizedLandscape));
  const authorizedHome = makeHome(authorizedLandscapeSha);
  const recordRefs = authorizedHome.records.map(record => record.record_ref);
  const receiptRefs = authorizedHome.records.map(record => record.receipt_ref);
  authorizedHome.input_hashes.record_head_sha256 = library.sha256Hex(
    library.canonicalJson(recordRefs)
  );
  authorizedHome.input_hashes.receipt_head_sha256 = library.sha256Hex(
    library.canonicalJson(receiptRefs)
  );
  return {
    home: authorizedHome,
    landscape: authorizedLandscape,
    landscapeSha256: authorizedLandscapeSha,
    authority: {
      agent_profile_sha256: A,
      active_understanding_refs: [clone(authorizedLandscape.peaks[0].understanding_ref)],
      current_memory_refs: memoryRefs,
      current_relation_refs: relationRefs,
      user_action_watermark_sha256: D,
      today_record_refs: recordRefs,
      today_receipt_refs: receiptRefs,
      daily_bundle_head_sha256: A,
    },
  };
}

function syncAuthorizedLandscape(value) {
  value.landscape.summary.active_understandings = value.landscape.peaks.length;
  value.landscape.summary.recent_changes = value.landscape.peaks
    .filter(peak => peak.recent_change).length;
  value.home.landscape_summary = clone(value.landscape.summary);
  value.landscapeSha256 = library.sha256Hex(JSON.stringify(value.landscape));
  value.home.landscape_ref.snapshot_sha256 = value.landscapeSha256;
  value.home.input_hashes.landscape_snapshot_sha256 = value.landscapeSha256;
  return value;
}

function addSecondUnderstanding(value) {
  const secondRef = ref('understanding', SECOND_UNDERSTANDING_ID, 3, B);
  value.landscape.peaks.push({
    peak_id: SECOND_PEAK_ID,
    understanding_ref: secondRef,
    x: 0.18,
    y: 0.76,
    elevation: 0.5,
    evidence_count: 4,
    counterevidence_count: 0,
    recent_change: false,
    lifecycle: 'active',
  });
  value.authority.active_understanding_refs.push(clone(secondRef));
  return syncAuthorizedLandscape(value);
}

assert.equal(
  library.sha256Hex('abc'),
  'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
  'SHA-256 必须与标准测试向量一致'
);
assert.equal(
  library.sha256Hex('中文\n'),
  'a20758305f24aa08fdd593d564de930e95c652027327bd291c5559db3d03f76c',
  'SHA-256 必须以 UTF-8 字节校验本地 JSON'
);
assert.equal(library.makeReceiptId(RECORD_ID), RECEIPT_ID, '必须与 Python make_receipt_id 一致');
assert.equal(library.makePeakId(UNDERSTANDING_ID), PEAK_ID, '必须与 Python make_peak_id 一致');

const landscape = makeLandscape();
const landscapeBytes = JSON.stringify(landscape);
const landscapeSha256 = library.sha256Hex(landscapeBytes);
const home = makeHome(landscapeSha256);
const pair = library.validateProjectionPair(home, landscape, landscapeSha256);
assert.deepEqual(pair.home, home, '有效 home 应返回无原型污染的等价拷贝');
assert.deepEqual(pair.landscape, landscape, '有效 landscape 应返回等价拷贝');
assert.notEqual(pair.home, home, '验证结果不得复用输入对象');
assert.ok(library.normalizeHomeProjection(home));
assert.ok(library.normalizeLandscapeSnapshot(landscape));
assert.ok(library.normalizeProjectionPair(home, landscape, landscapeSha256));

for (const status of ['raw_saved', 'processing', 'no_candidate', 'failed']) {
  const value = clone(home);
  value.records[0].status = status;
  value.records[0].receipt_ref = null;
  value.records[0].summary = status === 'failed' ? '原文已保存。' : null;
  value.records[0].memory_refs = [];
  value.records[0].understanding_refs = [];
  if (status === 'no_candidate') {
    value.records[0].content_types = [];
    value.records[0].topics = [];
    value.records[0].purposes = [];
  }
  syncTodayCounts(value);
  assert.ok(library.validateHomeProjection(value), `${status} 空回执状态应通过单文件合同`);
  assert.ok(library.validateProjectionPair(value, landscape, landscapeSha256));
}

for (const status of ['no_candidate', 'no_records', 'no_receipts']) {
  const value = clone(home);
  value.today_status.daily_run_status = status;
  value.schedule.last_run_status = status;
  assert.ok(library.validateProjectionPair(value, landscape, landscapeSha256),
    `${status} 应通过日级与调度合同`);
}

for (const status of ['ready', 'needs_review', 'original_only', 'merged']) {
  const value = clone(home);
  value.records[0].status = status;
  value.records[0].memory_refs = status === 'merged' ? value.records[0].memory_refs : [];
  value.records[0].understanding_refs = status === 'merged' ? value.records[0].understanding_refs : [];
  if (status === 'original_only') {
    value.records[0].summary = null;
    value.records[0].content_types = [];
    value.records[0].topics = [];
    value.records[0].purposes = [];
  }
  syncTodayCounts(value);
  assert.ok(library.validateProjectionPair(value, landscape, landscapeSha256), `${status} 应通过完整投影合同`);
}

const originalOnlyLeak = clone(home);
originalOnlyLeak.records[0].status = 'original_only';
originalOnlyLeak.records[0].summary = null;
originalOnlyLeak.records[0].content_types = [];
originalOnlyLeak.records[0].topics = [];
originalOnlyLeak.records[0].purposes = [];
originalOnlyLeak.records[0].understanding_refs = [];
syncTodayCounts(originalOnlyLeak);
assert.throws(
  () => library.validateHomeProjection(originalOnlyLeak),
  /original_only 不得携带/,
  'original_only 不得保留下游 memory refs'
);

const noCandidateLeak = clone(home);
noCandidateLeak.records[0].status = 'no_candidate';
noCandidateLeak.records[0].receipt_ref = null;
noCandidateLeak.records[0].summary = '不应暴露的整理内容。';
noCandidateLeak.records[0].content_types = [];
noCandidateLeak.records[0].topics = [];
noCandidateLeak.records[0].purposes = [];
noCandidateLeak.records[0].memory_refs = [];
noCandidateLeak.records[0].understanding_refs = [];
syncTodayCounts(noCandidateLeak);
assert.throws(
  () => library.validateHomeProjection(noCandidateLeak),
  /no_candidate 不得携带/,
  'no_candidate 不得携带摘要或下游引用'
);

const emptyLandscape = clone(landscape);
emptyLandscape.summary.active_understandings = 0;
emptyLandscape.summary.recent_changes = 0;
emptyLandscape.peaks = [];
emptyLandscape.nodes = [];
emptyLandscape.edges = [];
const emptyLandscapeSha = library.sha256Hex(JSON.stringify(emptyLandscape));
const emptyHome = makeHome(emptyLandscapeSha);
emptyHome.landscape_summary = clone(emptyLandscape.summary);
emptyHome.records = [];
emptyHome.today_status = {
  saved: 0,
  interpreted: 0,
  merged: 0,
  needs_review: 0,
  daily_run_status: 'not_started',
};
emptyHome.warnings = ['partial_source_unavailable'];
assert.ok(
  library.validateProjectionPair(emptyHome, emptyLandscape, emptyLandscapeSha),
  '没有长期理解时必须允许空地景，不能补造候选峰'
);

// Every public and nested object uses exact keys. Raw text therefore cannot be
// smuggled into a home projection under an uncontracted property.
for (const invalid of [
  { ...clone(home), raw: '原文' },
  { ...clone(home), input_hashes: { ...home.input_hashes, unexpected: A } },
  (() => {
    const value = clone(home);
    value.records[0].original_text = '不应进入主页投影';
    return value;
  })(),
  (() => {
    const value = clone(home);
    value.records[0].record_ref.extra = true;
    return value;
  })(),
]) {
  assert.throws(() => library.validateHomeProjection(invalid), library.CognitiveContractError);
  assert.equal(library.normalizeHomeProjection(invalid), null);
}

for (const invalid of [
  { ...clone(landscape), raw_records: [] },
  { ...clone(landscape), terrain: { ...landscape.terrain, color: 'blue' } },
  (() => {
    const value = clone(landscape);
    value.peaks[0].candidate = true;
    return value;
  })(),
]) {
  assert.throws(() => library.validateLandscapeSnapshot(invalid), library.CognitiveContractError);
  assert.equal(library.normalizeLandscapeSnapshot(invalid), null);
}

const candidatePeak = clone(landscape);
candidatePeak.peaks[0].understanding_ref = ref(
  'understanding', 'cmem_777777777777777777777777', 1, A
);
assert.throws(
  () => library.validateLandscapeSnapshot(candidatePeak),
  /object_ref\.id 无效/,
  '候选记忆不得伪装成正式认知峰'
);

const wrongReceipt = clone(home);
wrongReceipt.records[0].receipt_ref.id = 'rcp_999999999999999999999999';
assert.throws(() => library.validateHomeProjection(wrongReceipt), /receipt ref 无效/);

const missingReceipt = clone(home);
missingReceipt.records[0].receipt_ref = null;
assert.throws(() => library.validateHomeProjection(missingReceipt), /必须绑定 receipt/);

const processingWithReceipt = clone(home);
processingWithReceipt.records[0].status = 'processing';
assert.throws(() => library.validateHomeProjection(processingWithReceipt), /不得绑定 receipt/);

const invalidDate = clone(home);
invalidDate.local_date = '2026-02-29';
assert.throws(() => library.validateHomeProjection(invalidDate), /有效日期/);

const timezoneMissing = clone(home);
timezoneMissing.generated_at = '2026-08-18T21:00:01';
assert.throws(() => library.validateHomeProjection(timezoneMissing), /带时区/);

const booleanCount = clone(landscape);
booleanCount.peaks[0].evidence_count = true;
assert.throws(() => library.validateLandscapeSnapshot(booleanCount), /有效整数/);

const duplicatePeak = clone(landscape);
duplicatePeak.peaks.push(clone(duplicatePeak.peaks[0]));
duplicatePeak.summary.active_understandings = 2;
assert.throws(() => library.validateLandscapeSnapshot(duplicatePeak), /不得重复/);

const brokenEdge = clone(landscape);
brokenEdge.edges[0].to_id = 'mem_999999999999999999999999';
assert.throws(() => library.validateLandscapeSnapshot(brokenEdge), /当前正式图谱/);

const badActiveCount = clone(landscape);
badActiveCount.summary.active_understandings = 0;
assert.throws(() => library.validateLandscapeSnapshot(badActiveCount), /active_understandings/);

const badRecentCount = clone(landscape);
badRecentCount.summary.recent_changes = 0;
assert.throws(() => library.validateLandscapeSnapshot(badRecentCount), /recent_changes/);

for (const [name, badHome, badLandscape, badSha] of [
  ['snapshot id', { ...clone(home), landscape_ref: { ...home.landscape_ref, snapshot_id: 'lnd_999999999999999999999999' } }, landscape, landscapeSha256],
  ['actual bytes sha', home, landscape, F],
  ['summary', { ...clone(home), landscape_summary: { ...home.landscape_summary, recent_changes: 0 } }, landscape, landscapeSha256],
  ['date', { ...clone(home), local_date: '2026-08-17' }, landscape, landscapeSha256],
  ['profile input', { ...clone(home), input_hashes: { ...home.input_hashes, agent_profile_sha256: B } }, landscape, landscapeSha256],
]) {
  assert.throws(
    () => library.validateProjectionPair(badHome, badLandscape, badSha),
    error => error instanceof library.CognitiveContractError && error.kind === 'stale',
    `${name} 不一致时必须 fail closed`
  );
}

const missingNode = clone(home);
missingNode.records[0].memory_refs[0] = ref(
  'reusable_memory', 'rmem_999999999999999999999999', 1, B
);
assert.ok(library.validateHomeProjection(missingNode), '单文件合同只验证 ref 形状');
assert.throws(
  () => library.validateProjectionPair(missingNode, landscape, landscapeSha256),
  error => error instanceof library.CognitiveContractError && error.kind === 'stale',
  'home downstream memory 必须精确存在于 landscape'
);

const staleRevision = clone(home);
staleRevision.records[0].understanding_refs[0].revision = 1;
assert.throws(
  () => library.validateProjectionPair(staleRevision, landscape, landscapeSha256),
  error => error instanceof library.CognitiveContractError && error.kind === 'stale',
  '同 ID 的旧 revision 不得通过跨文件校验'
);

for (const key of ['saved', 'interpreted', 'merged', 'needs_review']) {
  const inconsistent = clone(home);
  inconsistent.today_status[key] += 1;
  assert.throws(
    () => library.validateProjectionPair(inconsistent, landscape, landscapeSha256),
    error => error instanceof library.CognitiveContractError && error.kind === 'stale',
    `today_status.${key} 必须能从 records 重算`
  );
}

const authorized = makeAuthorizedFixture();
assert.ok(library.validateProjectionPair(
  authorized.home, authorized.landscape, authorized.landscapeSha256
));
assert.deepEqual(
  library.validateProjectionAuthority(
    authorized.home, authorized.landscape, authorized.authority
  ).authority,
  authorized.authority,
  '当前权威 heads 必须能够二次授权投影'
);

const reorderedUnderstandings = addSecondUnderstanding(clone(authorized));
reorderedUnderstandings.authority.active_understanding_refs.reverse();
assert.ok(library.validateProjectionPair(
  reorderedUnderstandings.home,
  reorderedUnderstandings.landscape,
  reorderedUnderstandings.landscapeSha256
));
assert.ok(
  library.validateProjectionAuthority(
    reorderedUnderstandings.home,
    reorderedUnderstandings.landscape,
    reorderedUnderstandings.authority
  ),
  'active understanding 引用顺序不得影响精确集合授权'
);

const missingPeak = addSecondUnderstanding(clone(authorized));
missingPeak.landscape.peaks.pop();
syncAuthorizedLandscape(missingPeak);
assert.ok(library.validateProjectionPair(
  missingPeak.home, missingPeak.landscape, missingPeak.landscapeSha256
));
assert.throws(
  () => library.validateProjectionAuthority(
    missingPeak.home, missingPeak.landscape, missingPeak.authority
  ),
  error => error instanceof library.CognitiveContractError && error.kind === 'stale',
  '当前 active understanding 缺少山峰时必须 fail closed'
);

const extraPeak = addSecondUnderstanding(clone(authorized));
extraPeak.authority.active_understanding_refs.pop();
assert.ok(library.validateProjectionPair(
  extraPeak.home, extraPeak.landscape, extraPeak.landscapeSha256
));
assert.throws(
  () => library.validateProjectionAuthority(
    extraPeak.home, extraPeak.landscape, extraPeak.authority
  ),
  error => error instanceof library.CognitiveContractError && error.kind === 'stale',
  '地景多出非当前 understanding 山峰时必须 fail closed'
);

for (const field of ['revision', 'revision_sha256']) {
  const mismatchedUnderstanding = clone(authorized);
  mismatchedUnderstanding.authority.active_understanding_refs[0][field] = field === 'revision'
    ? mismatchedUnderstanding.authority.active_understanding_refs[0][field] + 1
    : B;
  assert.throws(
    () => library.validateProjectionAuthority(
      mismatchedUnderstanding.home,
      mismatchedUnderstanding.landscape,
      mismatchedUnderstanding.authority
    ),
    error => error instanceof library.CognitiveContractError && error.kind === 'stale',
    `understanding ${field} 不一致时必须 fail closed`
  );
}

for (const [name, mutate] of [
  ['profile', value => { value.authority.agent_profile_sha256 = B; }],
  ['action watermark', value => { value.authority.user_action_watermark_sha256 = E; }],
  ['daily bundle', value => { value.authority.daily_bundle_head_sha256 = F; }],
  ['understanding head', value => {
    value.authority.active_understanding_refs[0] = {
      ...value.authority.active_understanding_refs[0],
      revision: value.authority.active_understanding_refs[0].revision + 1,
    };
  }],
  ['memory head', value => {
    value.authority.current_memory_refs[0] = {
      ...value.authority.current_memory_refs[0],
      revision: value.authority.current_memory_refs[0].revision + 1,
    };
  }],
  ['relation head', value => { value.authority.current_relation_refs = []; }],
  ['record head', value => {
    value.authority.today_record_refs[0] = {
      ...value.authority.today_record_refs[0],
      revision: value.authority.today_record_refs[0].revision + 1,
    };
  }],
  ['receipt head', value => {
    value.authority.today_receipt_refs[0] = {
      ...value.authority.today_receipt_refs[0],
      revision: value.authority.today_receipt_refs[0].revision + 1,
    };
  }],
]) {
  const stale = clone(authorized);
  mutate(stale);
  assert.throws(
    () => library.validateProjectionAuthority(stale.home, stale.landscape, stale.authority),
    error => error instanceof library.CognitiveContractError && error.kind === 'stale',
    `${name} 变化后必须 fail closed`
  );
}

const catalog = {
  schema_version: '1.0',
  kind: 'memento_cognitive_formal_head_index',
  revision: 2,
  generated_at: '2026-08-18T21:00:00+08:00',
  daily_bundles: [ref('daily_bundle', 'db_20260818', 1, A)],
  daily_summaries: [ref('daily_summary', 'dsum_20260818', 1, B)],
  reusable_memories: [ref('reusable_memory', MEMORY_ID, 3, B)],
  relations: [ref('relation', RELATION_ID, 1, C)],
};
assert.deepEqual(library.validateFormalHeadIndex(catalog), catalog);
const unsortedCatalog = clone(catalog);
unsortedCatalog.reusable_memories = [
  ref('reusable_memory', 'rmem_ffffffffffffffffffffffff', 1, A),
  ref('reusable_memory', MEMORY_ID, 3, B),
];
assert.throws(() => library.validateFormalHeadIndex(unsortedCatalog), /按 id 排序/);

const contour = library.organicContourPath(500, 240, 180, 110, PEAK_ID, 2);
assert.equal(contour, library.organicContourPath(500, 240, 180, 110, PEAK_ID, 2));
assert.notEqual(contour, library.organicContourPath(500, 240, 180, 110, PEAK_ID, 3));
assert.match(contour, /^M [-\d.]+ [-\d.]+(?: C [-\d.]+ [-\d.]+ [-\d.]+ [-\d.]+ [-\d.]+ [-\d.]+){16} Z$/);
assert.equal(contour.includes('NaN'), false);
assert.equal(library.STATUS_LABELS.processing, '正在整理这一条');
assert.equal(library.STATUS_LABELS.no_candidate, '已检查，本条没有形成可归并内容');
assert.equal(library.RELATION_LABELS.counterexample, '反例');

const ACTION_ID = 'cact_777777777777777777777777';
const ACTION_TIME = '2026-08-18T21:05:00+08:00';
const receiptTarget = ref('interpretation_receipt', RECEIPT_ID, 1, F);
const receiptFacets = {
  content_types: ['observation'],
  topics: ['产品设计'],
  objects: ['Memento'],
  stance: 'self_observation',
  cognitive_state: 'repeated',
  purposes: ['future_decision'],
};
const confirmAction = library.buildCognitiveUserAction({
  id: ACTION_ID,
  createdAt: ACTION_TIME,
  action: 'confirm_receipt',
  targetRef: receiptTarget,
  payload: null,
});
assert.deepEqual(confirmAction, {
  schema_version: '1.0',
  kind: 'memento_cognitive_user_action',
  id: ACTION_ID,
  created_at: ACTION_TIME,
  action: 'confirm_receipt',
  target_ref: receiptTarget,
  payload: null,
});
assert.equal(library.cognitiveActionFileName(ACTION_ID), `${ACTION_ID}.json`);
assert.equal(
  library.makeCognitiveActionResultId(ACTION_ID),
  'cares_d6b40680fdb98533ac9583df',
  'action result ID 必须与 Python 合同一致'
);
assert.equal(
  library.cognitiveActionResultFileName(ACTION_ID),
  'cares_d6b40680fdb98533ac9583df.json'
);
assert.ok(library.serializeCognitiveAction(confirmAction).endsWith('\n'));

const editReceipt = library.buildCognitiveUserAction({
  id: ACTION_ID,
  createdAt: ACTION_TIME,
  action: 'edit_receipt',
  targetRef: receiptTarget,
  payload: { summary: '先交付可验证的部分。', facets: receiptFacets },
});
assert.deepEqual(editReceipt.payload.facets, receiptFacets,
  'receipt edit 必须完整保留六个 facets 字段');

for (const [action, targetRef, payload] of [
  ['original_only', receiptTarget, null],
  ['edit_reusable_memory', ref('reusable_memory', MEMORY_ID, 3, B), {
    statement: '评审前先定义最早可验证部分。',
    topics: ['产品设计'], purposes: ['future_decision'],
  }],
  ['delete_reusable_memory', ref('reusable_memory', MEMORY_ID, 3, B), null],
  ['edit_relation', ref('relation', RELATION_ID, 1, C), {
    type: 'scope_boundary', statement: '只在方案评审时适用。',
  }],
  ['delete_relation', ref('relation', RELATION_ID, 1, C), null],
]) {
  assert.equal(library.buildCognitiveUserAction({
    id: ACTION_ID, createdAt: ACTION_TIME, action, targetRef, payload,
  }).action, action);
}

assert.throws(
  () => library.buildCognitiveUserAction({
    id: ACTION_ID, createdAt: ACTION_TIME, action: 'confirm_receipt',
    targetRef: ref('reusable_memory', MEMORY_ID, 3, B), payload: null,
  }),
  /target kind/,
  '动作不得写到错误对象类型'
);
assert.throws(
  () => library.buildCognitiveUserAction({
    id: ACTION_ID, createdAt: ACTION_TIME, action: 'original_only',
    targetRef: receiptTarget, payload: {},
  }),
  /payload 必须是 null/,
  '终态动作必须使用精确 null payload'
);
assert.throws(
  () => library.buildCognitiveUserAction({
    id: ACTION_ID, createdAt: ACTION_TIME, action: 'edit_receipt',
    targetRef: receiptTarget,
    payload: { summary: '有效摘要', facets: { ...receiptFacets, purposes: undefined } },
  }),
  /facets/,
  '缺少任一 facet 时不得写入'
);
assert.throws(
  () => library.validateCognitiveUserAction({ ...confirmAction, extra: true }),
  /字段不符合合同/,
  '用户动作必须严格拒绝额外字段'
);

const actionResult = {
  schema_version: '1.0',
  kind: 'memento_cognitive_action_result',
  id: library.makeCognitiveActionResultId(ACTION_ID),
  action_id: ACTION_ID,
  action_sha256: library.sha256Hex(library.serializeCognitiveAction(confirmAction)),
  status: 'applied',
  completed_at: '2026-08-18T21:05:01+08:00',
  materialized_refs: [ref('interpretation_receipt', RECEIPT_ID, 2, A)],
  error_kind: null,
};
assert.deepEqual(library.validateCognitiveActionResult(actionResult), actionResult);
assert.throws(
  () => library.validateCognitiveActionResult({ ...actionResult, status: 'conflict' }),
  /未应用 action result/,
  'conflict 不得伪造 materialized refs'
);

assert.throws(
  () => library.validateCognitiveActionPayload('edit_reusable_memory', {
    statement: '有效内容', topics: ['产品'], purposes: ['invalid'],
  }),
  /不允许值/,
  '正式对象的编辑字段必须使用合同枚举'
);

const MANUAL_REQUEST_ID = 'cman_888888888888888888888888';
const manualRequest = library.buildManualDayRequest({
  id: MANUAL_REQUEST_ID,
  createdAt: ACTION_TIME,
  localDate: '2026-08-18',
});
assert.deepEqual(manualRequest, {
  created_at: ACTION_TIME,
  id: MANUAL_REQUEST_ID,
  kind: 'memento_cognitive_manual_day_request',
  local_date: '2026-08-18',
  schema_version: '1.0',
  status: 'pending',
});
assert.equal(library.manualDayRequestFileName(MANUAL_REQUEST_ID), `${MANUAL_REQUEST_ID}.json`);
assert.equal(
  library.serializeManualDayRequest(manualRequest),
  `${JSON.stringify(manualRequest, null, 2)}\n`,
  'manual day request 必须按 sort_keys 顺序、indent=2 且以换行结尾'
);
assert.throws(
  () => library.validateManualDayRequest({ ...manualRequest, status: 'completed' }),
  /schema\/kind\/status/,
  '浏览器只能写 pending manual request'
);
assert.throws(
  () => library.validateManualDayRequest({
    ...manualRequest,
    created_at: '2026-08-17T16:05:00.000Z',
  }),
  /created_at\/local_date 不一致/,
  '本地凌晨不得用前一天的 UTC 日期伪造今日请求'
);

const manualResult = {
  schema_version: '1.0',
  kind: 'memento_cognitive_manual_day_result',
  request_id: MANUAL_REQUEST_ID,
  request_sha256: library.sha256Hex(library.serializeManualDayRequest(manualRequest)),
  completed_at: '2026-08-18T21:06:00+08:00',
  local_date: '2026-08-18',
  status: 'completed',
  runner_status: 'committed_with_warnings',
  error_kind: null,
};
manualResult.id = library.makeManualDayResultId(manualResult.request_sha256);
assert.deepEqual(library.validateManualDayResult(manualResult), manualResult);
assert.deepEqual(library.validateManualDayResult({
  ...manualResult, status: 'runner_failed', runner_status: null, error_kind: 'runtime',
}).status, 'runner_failed');
assert.throws(
  () => library.validateManualDayResult({ ...manualResult, status: 'runner_failed' }),
  /runner_status 必须为 null/,
  '失败结果不得伪造 runner 完成态'
);
assert.throws(
  () => library.validateManualDayResult({ ...manualResult, runner_status: 'unknown' }),
  /runner_status 无效/
);
assert.throws(
  () => library.validateManualDayResult({ ...manualResult, extra: true }),
  /字段不符合合同/,
  'manual result 必须严格拒绝额外字段'
);
assert.throws(
  () => library.validateManualDayResult({
    ...manualResult, id: 'cmanr_999999999999999999999999',
  }),
  /id 与 request_sha256 不一致/,
  'manual result id 必须由请求精确字节哈希派生'
);
assert.throws(
  () => library.validateManualDayResult({
    ...manualResult, status: 'master_gate_disabled', runner_status: null, error_kind: 'runtime',
  }),
  /status\/error_kind 不一致/,
  'manual result 状态与错误类型必须精确匹配'
);

console.log('cognitive-home-library contract tests passed');
