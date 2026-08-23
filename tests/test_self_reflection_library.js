import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

await import('../chrome-newtab/context-agent-library.js');
const library = globalThis.MementoContextAgent;
const tagIdVectors = JSON.parse(readFileSync(
  new URL('./fixtures/self_reflection_tag_id_vectors.json', import.meta.url),
  'utf8'
));

const REQUEST_ID = 'srq_111111111111111111111111';
const FEEDBACK_ID = 'srf_222222222222222222222222';
const CONTEXT_ID = 'ctx_333333333333333333333333';
const fixedNow = new Date('2026-08-11T12:00:00.000Z');

const request = library.buildSelfReflectionRequest({
  id: REQUEST_ID,
  question: '现在，你怎么看我？',
  asOf: '2026-08-11',
  now: fixedNow,
});
assert.deepEqual(request, {
  schema_version: '1.0',
  id: REQUEST_ID,
  kind: 'self_reflection_request',
  status: 'pending',
  created_at: '2026-08-11T12:00:00.000Z',
  question: '现在，你怎么看我？',
  as_of: '2026-08-11',
  window_days: 14,
});
assert.equal(library.normalizeSelfReflectionRequest(request).id, REQUEST_ID);
assert.equal(library.normalizeSelfReflectionRequestRecord(request, REQUEST_ID).id, REQUEST_ID);
assert.equal(
  library.normalizeSelfReflectionRequestRecord(request, 'srq_000000000000000000000000'),
  null,
  'request JSON id 必须与文件名绑定'
);
assert.equal(library.normalizeSelfReflectionRequest({ ...request, unexpected: true }), null);
assert.equal(
  library.normalizeSelfReflectionRequest({ ...request, created_at: 'not-a-time' }),
  null,
  'request.created_at 必须是严格 ISO-8601 时间'
);
assert.equal(
  library.normalizeSelfReflectionRequest({ ...request, created_at: '2026-02-30T12:00:00Z' }),
  null,
  'request.created_at 不能接受溢出日期'
);
assert.throws(
  () => library.buildSelfReflectionRequest({
    id: '../unsafe', question: '我是谁？', asOf: '2026-08-11', now: fixedNow,
  }),
  /ID/
);
assert.throws(
  () => library.buildSelfReflectionRequest({
    id: REQUEST_ID, question: '我的情绪状态怎么样？', asOf: '2026-08-11', now: fixedNow,
  }),
  /问题/
);
assert.equal(library.selfReflectionRequestFileName(REQUEST_ID), `${REQUEST_ID}.json`);
assert.throws(() => library.selfReflectionRequestFileName('../escape'), /ID/);

const evidence = [
  { file: '2026-08-08.md', line: 2, quote: '今天评审方案时，我先检查了目标和证据。' },
  { file: '2026-08-10.md', line: 1, quote: '再次要求先说明关键取舍，再进入实现。' },
];
const sourceHashes = [
  { file: '2026-08-08.md', sha256: 'a'.repeat(64) },
  { file: '2026-08-10.md', sha256: 'b'.repeat(64) },
];
const response = {
  schema_version: '1.0',
  request_id: REQUEST_ID,
  kind: 'self_reflection_response',
  status: 'ready',
  created_at: '2026-08-11T12:00:05.000Z',
  cache_hit: false,
  question: request.question,
  as_of: '2026-08-11',
  window_days: 14,
  record_days: 2,
  source_hashes: sourceHashes,
  confirmed_contexts: 1,
  reflection: {
    summary: '在近期产品工作中，你反复要求先明确目标、证据和取舍。',
    scope_note: '这只是工作场景下的近期理解，不代表完整的你。',
    unknown: '现有记录不足以支持对工作之外场景的判断。',
    insights: [{
      title: '先看目标和取舍，再进入实现',
      statement: '这项偏好在多个记录日中重复出现。',
      scope: '产品方案与 PRD',
      kind: 'confirmed',
      uncertainty: 'low',
      sensitive: false,
      evidence,
      counterevidence: [],
      context_refs: [CONTEXT_ID],
    }],
  },
  usage: null,
  error: null,
  error_kind: null,
};

const normalizedResponse = library.normalizeSelfReflectionResponse(response);
assert.equal(normalizedResponse.requestId, REQUEST_ID);
assert.equal(normalizedResponse.recordDays, 2);
assert.equal(normalizedResponse.confirmedContexts, 1);
assert.equal(normalizedResponse.reflection.insights[0].contextRefs[0], CONTEXT_ID);
assert.equal(library.normalizeSelfReflectionResponseRecord(response, REQUEST_ID).requestId, REQUEST_ID);
assert.equal(
  library.normalizeSelfReflectionResponseRecord(response, 'srq_000000000000000000000000'),
  null,
  'response request_id 必须与文件名绑定'
);
assert.equal(library.normalizeSelfReflectionResponse({ ...response, extra: true }), null);
assert.equal(
  library.normalizeSelfReflectionResponse({ ...response, created_at: 'yesterday' }),
  null,
  'response.created_at 必须是严格 ISO-8601 时间'
);
assert.equal(
  library.normalizeSelfReflectionResponse({
    ...response,
    reflection: {
      ...response.reflection,
      summary: '用户的情绪很不稳定。',
    },
  }),
  null,
  '自我理解结论不能混入敏感状态推断'
);
assert.equal(
  library.normalizeSelfReflectionResponse({
    ...response,
    reflection: { ...response.reflection, summary: '你是一个完美主义者。' },
  }),
  null,
  '不能把局部观察写成固定人格总结'
);
assert.equal(
  library.normalizeSelfReflectionResponse({
    ...response,
    reflection: {
      ...response.reflection,
      insights: [{
        ...response.reflection.insights[0],
        statement: '你是一个完美主义者。',
      }],
    },
  }),
  null,
  '不能把局部证据写成固定人格段落'
);
assert.equal(
  library.normalizeSelfReflectionResponse({
    ...response,
    reflection: {
      ...response.reflection,
      insights: [{
        ...response.reflection.insights[0],
        evidence: [
          { ...evidence[0], quote: 'sk-abcdefghijklmnop' },
          evidence[1],
        ],
      }],
    },
  }),
  null,
  '密钥不能被当作洞察证据展示'
);
assert.equal(
  library.normalizeSelfReflectionResponse({
    ...response,
    reflection: {
      ...response.reflection,
      insights: [{ ...response.reflection.insights[0], context_refs: [] }],
    },
  }),
  null,
  'confirmed insight 必须引用已确认 Context'
);
const contextOnlyResponse = {
  ...response,
  reflection: {
    ...response.reflection,
    insights: [{
      ...response.reflection.insights[0],
      evidence: [],
    }],
  },
};
assert.equal(
  library.normalizeSelfReflectionResponse(contextOnlyResponse).reflection.insights[0].evidence.length,
  0,
  'confirmed insight 可以只由已确认 Context 支持'
);
const exactActiveContext = {
  id: CONTEXT_ID,
  statement: response.reflection.insights[0].statement,
  scope: response.reflection.insights[0].scope,
};
assert.equal(
  library.selfReflectionConfirmedInsightsMatch(response, [exactActiveContext]),
  true,
  'confirmed insight 的 statement/scope 与 active Context 逐字一致时才可展示'
);
assert.equal(
  library.selfReflectionConfirmedInsightsMatch(response, [{
    ...exactActiveContext,
    statement: '同一 ID 下已经改变的 Context 文本。',
  }]),
  false,
  'Context 数量和 ID 未变也不能接受 statement 漂移'
);
assert.equal(
  library.selfReflectionConfirmedInsightsMatch(response, [{
    ...exactActiveContext,
    scope: '已经改变的范围',
  }]),
  false,
  'Context 数量和 ID 未变也不能接受 scope 漂移'
);
const observationOnlyResponse = {
  ...response,
  confirmed_contexts: 0,
  reflection: {
    ...response.reflection,
    insights: [{
      ...response.reflection.insights[0],
      kind: 'observation',
      context_refs: [],
    }],
  },
};
assert.equal(
  library.selfReflectionConfirmedInsightsMatch(observationOnlyResponse, []),
  true,
  'observation 不需要伪造一个 active Context 匹配'
);
assert.equal(
  library.normalizeSelfReflectionResponse({
    ...response,
    reflection: {
      ...response.reflection,
      insights: [{
        ...response.reflection.insights[0],
        kind: 'observation',
        evidence: evidence.slice(0, 1),
        context_refs: [],
      }],
    },
  }),
  null,
  'observation 必须由两个不同记录日支持'
);
assert.equal(
  library.normalizeSelfReflectionResponse({
    ...response,
    reflection: {
      ...response.reflection,
      insights: [{
        ...response.reflection.insights[0],
        kind: 'change',
        context_refs: [],
      }],
    },
  }),
  null,
  'change 必须同时包含证据和反例'
);

const changeResponse = {
  ...response,
  question: '最近发生了什么变化？',
  confirmed_contexts: 0,
  reflection: {
    ...response.reflection,
    summary: '近期记录显示，某项产品判断已从旧标准转向新标准。',
    insights: [{
      title: '验证标准发生修订',
      statement: '新记录修订了较早记录中的判断标准。',
      scope: '产品方案验收',
      kind: 'change',
      uncertainty: 'low',
      sensitive: false,
      evidence: [{
        ...evidence[1],
        quote: '这次改为先验证真实使用结果。',
      }],
      counterevidence: [{
        ...evidence[0],
        quote: '之前先根据功能是否完成验收。',
      }],
      context_refs: [],
    }],
  },
};
assert.ok(library.normalizeSelfReflectionResponse(changeResponse), '有新旧时序和明确变化词时可接受 change');
assert.equal(
  library.normalizeSelfReflectionResponse({
    ...changeResponse,
    reflection: {
      ...changeResponse.reflection,
      insights: [{
        ...changeResponse.reflection.insights[0],
        evidence: [{ ...changeResponse.reflection.insights[0].evidence[0], file: '2026-08-08.md' }],
        counterevidence: [{ ...changeResponse.reflection.insights[0].counterevidence[0], file: '2026-08-10.md' }],
      }],
    },
  }),
  null,
  'change 的新方向证据必须全部晚于旧方向反证'
);
assert.equal(
  library.normalizeSelfReflectionResponse({
    ...changeResponse,
    reflection: {
      ...changeResponse.reflection,
      insights: [{
        ...changeResponse.reflection.insights[0],
        evidence: [{ ...changeResponse.reflection.insights[0].evidence[0], quote: '现在使用真实结果评估。' }],
      }],
    },
  }),
  null,
  '明确询问变化时，change 证据至少要有一条逐字变化信号'
);
assert.equal(
  library.normalizeSelfReflectionResponse({
    ...changeResponse,
    reflection: {
      ...changeResponse.reflection,
      insights: [{
        ...response.reflection.insights[0],
        kind: 'observation',
        context_refs: [],
      }],
    },
  }),
  null,
  '明确询问变化时不能用 observation 填充'
);

const sources = new Map([
  ['2026-08-08.md', {
    sha256: 'a'.repeat(64),
    lines: ['', evidence[0].quote],
  }],
  ['2026-08-10.md', {
    sha256: 'b'.repeat(64),
    lines: [evidence[1].quote],
  }],
]);
assert.equal(library.verifySelfReflectionBacking(contextOnlyResponse, sources).valid, true);
assert.equal(library.verifySelfReflectionBacking(response, sources).valid, true);
assert.deepEqual(
  library.verifySelfReflectionBacking(response, new Map([
    ...sources,
    ['2026-08-10.md', { sha256: 'c'.repeat(64), lines: [evidence[1].quote] }],
  ])),
  { valid: false, reason: 'stale', file: '2026-08-10.md' }
);

const insufficient = {
  ...response,
  status: 'insufficient_evidence',
  record_days: 1,
  source_hashes: [sourceHashes[0]],
  confirmed_contexts: 0,
  reflection: {
    summary: '目前还没有足够证据形成稳定理解。',
    scope_note: '本次只检查了已授权的记录。',
    unknown: '记录日数不足，无法判断是否存在重复模式。',
    insights: [],
  },
};
assert.equal(library.normalizeSelfReflectionResponse(insufficient).status, 'insufficient_evidence');
assert.equal(library.verifySelfReflectionBacking(insufficient, sources).valid, true);

const errorResponse = {
  ...response,
  status: 'error',
  record_days: 0,
  source_hashes: [],
  reflection: null,
  error: '模型请求暂时失败',
  error_kind: 'provider_error',
};
assert.equal(library.normalizeSelfReflectionResponse(errorResponse).errorKind, 'provider_error');

const usage = {
  schema_version: '1.0',
  kind: 'model_usage',
  timestamp: '2026-08-11T12:00:05.000Z',
  provider: 'deepseek',
  model: 'deepseek-test',
  request_id: 'provider-request-id',
  prompt_tokens: 1000,
  completion_tokens: 200,
  total_tokens: 1200,
  prompt_cache_hit_tokens: 600,
  prompt_cache_miss_tokens: 400,
  reasoning_tokens: 80,
  usage_missing: false,
  cost_usd: 0.0003,
  pricing: {
    effective_date: '2026-08-09',
    cache_hit_input_usd_per_million: 0.01,
    cache_miss_input_usd_per_million: 0.1,
    output_usd_per_million: 0.2,
  },
};
assert.equal(library.normalizeSelfReflectionResponse({ ...response, usage }).usage.total_tokens, 1200);
assert.equal(
  library.normalizeSelfReflectionResponse({
    ...response,
    usage: { ...usage, usage_missing: true },
  }),
  null,
  'usage_missing 与 cost_usd 必须一致'
);

const responseSha256 = 'd'.repeat(64);
const accurateFeedback = library.buildSelfReflectionFeedback({
  id: FEEDBACK_ID,
  requestId: REQUEST_ID,
  insightIndex: 0,
  action: 'accurate',
  responseSha256,
  now: fixedNow,
});
assert.equal(accurateFeedback.note, null);
assert.equal(library.normalizeSelfReflectionFeedback(accurateFeedback).action, 'accurate');
assert.equal(
  library.normalizeSelfReflectionFeedback({ ...accurateFeedback, created_at: 'not-a-time' }),
  null,
  'feedback.created_at 必须是严格 ISO-8601 时间'
);
assert.equal(library.normalizeSelfReflectionFeedbackRecord(accurateFeedback, FEEDBACK_ID).id, FEEDBACK_ID);
assert.equal(
  library.normalizeSelfReflectionFeedbackRecord(
    accurateFeedback,
    'srf_000000000000000000000000'
  ),
  null,
  'feedback id 必须与文件名绑定'
);
assert.equal(library.selfReflectionFeedbackFileName(FEEDBACK_ID), `${FEEDBACK_ID}.json`);
assert.throws(
  () => library.buildSelfReflectionFeedback({
    id: FEEDBACK_ID,
    requestId: REQUEST_ID,
    insightIndex: 0,
    action: 'edit',
    responseSha256,
    now: fixedNow,
  }),
  /字段不完整/
);
assert.throws(
  () => library.buildSelfReflectionFeedback({
    id: FEEDBACK_ID,
    requestId: REQUEST_ID,
    insightIndex: 0,
    action: 'edit',
    note: '我的情绪状态应该被长期记住',
    responseSha256,
    now: fixedNow,
  }),
  /字段不完整/
);
assert.equal(
  library.normalizeSelfReflectionFeedback({ ...accurateFeedback, response_sha256: '../bad' }),
  null
);

assert.equal(
  library.selfReflectionTagKey({ statement: '  Uses\u3000Evidence  ', scope: ' PRD ' }),
  library.selfReflectionTagKey({ statement: 'uses evidence', scope: 'prd' }),
  '标签去重键必须使用 pinned whitespace 与 ASCII lowercase'
);
assert.equal(
  library.SELF_REFLECTION_TAG_KEY_VERSION,
  'pinned-ws-ascii-lower-statement-scope-fnv96-v1'
);
assert.equal(library.normalizeSelfReflectionTagText('Ａ Ᲊ'), 'Ａ Ᲊ', '其他 Unicode 必须原样保留');
assert.doesNotMatch(
  library.normalizeSelfReflectionTagText.toString(),
  /\.normalize\(|\.toLowerCase\(|\.toLocaleLowerCase\(/,
  '身份规范化不得依赖运行时 Unicode 版本'
);
for (const vector of tagIdVectors) {
  const key = library.selfReflectionTagKey(vector);
  assert.equal(key, vector.normalized_key, `${vector.name} 规范化键必须跨语言稳定`);
  assert.equal(library.selfReflectionTagId(key), vector.expected_id, `${vector.name} ID 必须匹配共享向量`);
}
assert.notEqual(tagIdVectors[0].expected_id, tagIdVectors[1].expected_id, 'lower 不能误用 casefold');
const accurateProjection = library.buildSelfReflectionTagProjection([
  { response, responseHash: responseSha256, feedback: [accurateFeedback] },
]);
assert.equal(accurateProjection[0].feedback.action, 'accurate');
assert.equal(accurateProjection[0].semanticKeyVersion, library.SELF_REFLECTION_TAG_KEY_VERSION);
assert.equal(accurateProjection[0].status, 'continuing', 'legacy accurate 只能升级为 continuing');
assert.equal(accurateProjection[0].displayStatement, response.reflection.insights[0].statement);
assert.equal(accurateProjection[0].displayScope, response.reflection.insights[0].scope);
assert.equal('confirmed' in accurateProjection[0], false, 'legacy accurate 只保留原标签状态');
const confirmedProjection = library.buildSelfReflectionTagProjection([
  { response: contextOnlyResponse, responseHash: '5'.repeat(64), feedback: [] },
]);
assert.equal(confirmedProjection[0].status, 'continuing', 'confirmed insight 应显示 continuing');

const multiInsightResponseHash = 'f'.repeat(64);
const multiInsightResponse = {
  ...response,
  reflection: {
    ...response.reflection,
    insights: [
      response.reflection.insights[0],
      {
        title: '反复要求说清验证标准',
        statement: '在进入实现前，你会反复要求说清验证标准。',
        scope: '产品验收',
        kind: 'observation',
        uncertainty: 'medium',
        sensitive: false,
        evidence,
        counterevidence: [],
        context_refs: [],
      },
    ],
  },
};
const secondInsightTombstone = library.buildSelfReflectionFeedback({
  id: 'srf_333333333333333333333333',
  requestId: REQUEST_ID,
  insightIndex: 1,
  action: 'reject',
  responseSha256: multiInsightResponseHash,
  now: new Date('2026-08-11T12:03:00.000Z'),
});
const multiInsightProjection = library.buildSelfReflectionTagProjection([{
  response: multiInsightResponse,
  responseHash: multiInsightResponseHash,
  feedback: [secondInsightTombstone],
}]);
assert.equal(multiInsightProjection.length, 2);
const untouchedFirstInsight = multiInsightProjection.find(tag => tag.insightIndex === 0);
const rejectedSecondInsight = multiInsightProjection.find(tag => tag.insightIndex === 1);
assert.equal(untouchedFirstInsight.hidden, false, '对 insight 1 的反馈不能串到同一 response 的 insight 0');
assert.equal(untouchedFirstInsight.feedback, null);
assert.equal(rejectedSecondInsight.hidden, true);
assert.equal(rejectedSecondInsight.feedback.id, secondInsightTombstone.id);

const repeatedObservationResponse = {
  ...multiInsightResponse,
  request_id: 'srq_454545454545454545454545',
  created_at: '2026-08-11T12:03:30.000Z',
};
const repeatedObservationProjection = library.buildSelfReflectionTagProjection([
  { response: multiInsightResponse, responseHash: multiInsightResponseHash, feedback: [] },
  { response: repeatedObservationResponse, responseHash: '4'.repeat(64), feedback: [] },
]).find(tag => tag.insight.statement === multiInsightResponse.reflection.insights[1].statement);
assert.equal(repeatedObservationProjection.occurrenceCount, 2);
assert.equal(repeatedObservationProjection.supportEvidenceDayCount, 2);
assert.equal(
  repeatedObservationProjection.status,
  'system_observation',
  '重复 response 不能单独把 observation 升级为 continuing'
);

const scopedFeedback = library.buildSelfReflectionFeedback({
  id: 'srf_777777777777777777777777',
  requestId: REQUEST_ID,
  insightIndex: 0,
  action: 'scope',
  note: '高风险产品方案',
  responseSha256,
  now: new Date('2026-08-11T12:04:00.000Z'),
});
const scopedProjection = library.buildSelfReflectionTagProjection([
  { response, responseHash: responseSha256, feedback: [scopedFeedback] },
]);
assert.equal(scopedProjection[0].displayStatement, response.reflection.insights[0].statement);
assert.equal(scopedProjection[0].displayScope, scopedFeedback.note, 'scope 反馈只能改展示范围');
assert.equal(scopedProjection[0].status, 'user_edited');

const retainedEdit = library.buildSelfReflectionFeedback({
  id: 'srf_888888888888888888888888',
  requestId: REQUEST_ID,
  insightIndex: 0,
  action: 'edit',
  note: '先说清目标和验证标准。',
  responseSha256,
  now: new Date('2026-08-11T12:03:00.000Z'),
});
const laterAccurate = library.buildSelfReflectionFeedback({
  id: 'srf_999999999999999999999999',
  requestId: REQUEST_ID,
  insightIndex: 0,
  action: 'accurate',
  responseSha256,
  now: new Date('2026-08-11T12:05:00.000Z'),
});
let reducerProjection = library.buildSelfReflectionTagProjection([{
  response,
  responseHash: responseSha256,
  feedback: [retainedEdit, scopedFeedback, laterAccurate],
}]);
assert.equal(reducerProjection[0].displayStatement, retainedEdit.note, '后续 scope/accurate 不能抹掉 edit');
assert.equal(reducerProjection[0].displayScope, scopedFeedback.note);
assert.equal(reducerProjection[0].editFeedback.id, retainedEdit.id);
assert.equal(reducerProjection[0].scopeFeedback.id, scopedFeedback.id);
assert.equal(reducerProjection[0].statusFeedback.id, laterAccurate.id);
assert.equal(reducerProjection[0].status, 'continuing');

const laterChanged = library.buildSelfReflectionFeedback({
  id: 'srf_aaaaaaaaaaaaaaaaaaaaaaaa',
  requestId: REQUEST_ID,
  insightIndex: 0,
  action: 'changed',
  note: '这项做法正在变化。',
  responseSha256,
  now: new Date('2026-08-11T12:06:00.000Z'),
});
reducerProjection = library.buildSelfReflectionTagProjection([{
  response,
  responseHash: responseSha256,
  feedback: [retainedEdit, scopedFeedback, laterAccurate, laterChanged],
}]);
assert.equal(reducerProjection[0].statusFeedback.id, laterChanged.id, 'changed 状态必须按真实时间排序');
assert.equal(reducerProjection[0].status, 'changing');
assert.equal(reducerProjection[0].displayStatement, retainedEdit.note);
assert.equal(reducerProjection[0].displayScope, scopedFeedback.note);

const sameTime = new Date('2026-08-11T12:04:30.000Z');
const tieFeedbackA = library.buildSelfReflectionFeedback({
  id: `srf_${'a'.repeat(24)}`,
  requestId: REQUEST_ID,
  insightIndex: 0,
  action: 'edit',
  note: '先定义验证标准。',
  responseSha256,
  now: sameTime,
});
const tieFeedbackB = library.buildSelfReflectionFeedback({
  id: `srf_${'b'.repeat(24)}`,
  requestId: REQUEST_ID,
  insightIndex: 0,
  action: 'edit',
  note: '先定义目标、取舍和验证标准。',
  responseSha256,
  now: sameTime,
});
const tiedProjection = library.buildSelfReflectionTagProjection([{
  response,
  responseHash: responseSha256,
  feedback: [tieFeedbackA, tieFeedbackB],
}]);
assert.equal(tiedProjection[0].displayStatement, tieFeedbackB.note, '同时间反馈必须按 id 确定性选择');

const offsetOlderFeedback = {
  ...tieFeedbackA,
  id: 'srf_cccccccccccccccccccccccc',
  created_at: '2026-08-11T12:30:00+08:00',
  note: '这是时区偏移下较早的修改。',
};
const utcNewerFeedback = {
  ...tieFeedbackB,
  id: 'srf_dddddddddddddddddddddddd',
  created_at: '2026-08-11T05:00:00Z',
  note: '这是实际时间更新的修改。',
};
const offsetFeedbackProjection = library.buildSelfReflectionTagProjection([{
  response,
  responseHash: responseSha256,
  feedback: [offsetOlderFeedback, utcNewerFeedback],
}]);
assert.equal(
  offsetFeedbackProjection[0].displayStatement,
  utcNewerFeedback.note,
  '反馈时间必须用 Date.parse，不能比较 ISO 字符串'
);

const offsetOlderResponse = {
  ...response,
  request_id: 'srq_121212121212121212121212',
  created_at: '2026-08-11T12:30:00+08:00',
};
const utcNewerResponse = {
  ...response,
  request_id: 'srq_343434343434343434343434',
  created_at: '2026-08-11T05:00:00Z',
};
const offsetResponseProjection = library.buildSelfReflectionTagProjection([
  { response: offsetOlderResponse, responseHash: '1'.repeat(64), feedback: [] },
  { response: utcNewerResponse, responseHash: '2'.repeat(64), feedback: [] },
]);
assert.equal(
  offsetResponseProjection[0].response.requestId,
  utcNewerResponse.request_id,
  'response 时间必须用 Date.parse，不能比较 ISO 字符串'
);

const secondRequestId = 'srq_666666666666666666666666';
const secondResponseHash = 'e'.repeat(64);
const secondResponse = {
  ...response,
  request_id: secondRequestId,
  created_at: '2026-08-11T12:05:00.000Z',
};
const tombstone = library.buildSelfReflectionFeedback({
  id: 'srf_444444444444444444444444',
  requestId: REQUEST_ID,
  insightIndex: 0,
  action: 'reject',
  responseSha256,
  now: new Date('2026-08-11T12:06:00.000Z'),
});
assert.equal(tombstone.note, null, '删除记录使用严格 reject + null note 合同');
let projection = library.buildSelfReflectionTagProjection([
  { response, responseHash: responseSha256, feedback: [tombstone] },
  { response: secondResponse, responseHash: secondResponseHash, feedback: [] },
]);
assert.equal(projection.length, 1, '多次 ready response 中相同 statement + scope 只保留一个 tag');
assert.match(projection[0].tagId, /^ptag_[0-9a-f]{24}$/);
assert.equal(projection[0].occurrenceCount, 2);
assert.equal(projection[0].supportEvidenceDayCount, 2, '重复 response 不能虚增支持证据日');
assert.equal(projection[0].hidden, true, '旧 response 的 reject 必须继续隐藏后续 exact 重生的同一 tag');

const editedFeedback = library.buildSelfReflectionFeedback({
  id: 'srf_555555555555555555555555',
  requestId: secondRequestId,
  insightIndex: 0,
  action: 'edit',
  note: '先核对目标和关键取舍，再选择实现方式。',
  responseSha256: secondResponseHash,
  now: new Date('2026-08-11T12:07:00.000Z'),
});
const scopedAfterReject = library.buildSelfReflectionFeedback({
  id: 'srf_eeeeeeeeeeeeeeeeeeeeeeee',
  requestId: secondRequestId,
  insightIndex: 0,
  action: 'scope',
  note: '仅限高风险产品方案。',
  responseSha256: secondResponseHash,
  now: new Date('2026-08-11T12:08:00.000Z'),
});
const accurateAfterReject = library.buildSelfReflectionFeedback({
  id: 'srf_ffffffffffffffffffffffff',
  requestId: secondRequestId,
  insightIndex: 0,
  action: 'accurate',
  responseSha256: secondResponseHash,
  now: new Date('2026-08-11T12:09:00.000Z'),
});
const changedAfterReject = library.buildSelfReflectionFeedback({
  id: 'srf_000000000000000000000000',
  requestId: secondRequestId,
  insightIndex: 0,
  action: 'changed',
  note: '这项做法后来发生了变化。',
  responseSha256: secondResponseHash,
  now: new Date('2026-08-11T12:10:00.000Z'),
});
projection = library.buildSelfReflectionTagProjection([
  { response, responseHash: responseSha256, feedback: [tombstone] },
  {
    response: secondResponse,
    responseHash: secondResponseHash,
    feedback: [editedFeedback, scopedAfterReject, accurateAfterReject, changedAfterReject],
  },
]);
assert.equal(projection.length, 1);
assert.equal(projection[0].hidden, true, 'reject 是终态，后续 edit/scope/accurate/changed 不得复活标签');
assert.equal(projection[0].displayStatement, editedFeedback.note);
assert.equal(projection[0].displayScope, scopedAfterReject.note);

const thirdEvidence = {
  file: '2026-08-11.md',
  line: 3,
  quote: '验收时又一次先对齐了目标与失败标准。',
};
const thirdEvidenceResponse = {
  ...response,
  request_id: 'srq_787878787878787878787878',
  created_at: '2026-08-11T13:00:00.000Z',
  record_days: 2,
  source_hashes: [
    sourceHashes[1],
    { file: thirdEvidence.file, sha256: 'c'.repeat(64) },
  ],
  reflection: {
    ...response.reflection,
    insights: [{
      ...response.reflection.insights[0],
      evidence: [evidence[1], thirdEvidence],
    }],
  },
};
const evidenceProjection = library.buildSelfReflectionTagProjection([
  { response, responseHash: responseSha256, feedback: [] },
  { response: thirdEvidenceResponse, responseHash: '3'.repeat(64), feedback: [] },
]);
assert.equal(evidenceProjection[0].supportEvidenceDayCount, 3);
assert.equal(evidenceProjection[0].evidence.length, 3, '累计标签应合并去重后的支持线索');
assert.equal(evidenceProjection[0].status, 'continuing');

console.log('✓ Self reflection library: validates local request/response/feedback contracts and source-backed insights');
