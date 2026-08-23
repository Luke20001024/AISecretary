import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

await import('../chrome-newtab/remember-agent-v1-library.js');

const library = globalThis.MementoRememberAgentV1;
const fixtureUrl = new URL('./fixtures/remember_agent_v1_contract.json', import.meta.url);
const fixturePath = fileURLToPath(fixtureUrl);
const rootPath = fileURLToPath(new URL('../', import.meta.url));
const fixture = JSON.parse(readFileSync(fixtureUrl, 'utf8'));

function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

function clone(value) {
  return structuredClone(value);
}

function withoutFirstField(value) {
  const copy = clone(value);
  delete copy[Object.keys(copy)[0]];
  return copy;
}

function sortedJsonValue(value) {
  if (Array.isArray(value)) return value.map(sortedJsonValue);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(
    Object.keys(value).sort().map(key => [key, sortedJsonValue(value[key])])
  );
}

const request = library.normalizeAgentRequest(fixture.request);
const profile = library.normalizeAgentProfile(fixture.profile);
const response = library.normalizeAgentResponse(fixture.response);
const run = library.normalizeAgentRun(fixture.run);
const userActions = fixture.user_actions.map(library.normalizeUserAction);

assert.equal(
  library.isAgentEnableGateBytes(new TextEncoder().encode('enabled-v1\n')),
  true,
  'Dashboard 只接受字节精确的 Agent enable gate'
);
for (const invalidGate of [
  new TextEncoder().encode('enabled-v1'),
  new TextEncoder().encode('enabled-v1\nextra'),
  new TextEncoder().encode(' enabled-v1\n'),
  new Uint8Array(),
  'enabled-v1\n',
  null,
]) {
  assert.equal(library.isAgentEnableGateBytes(invalidGate), false);
}

assert.ok(request, '共享 fixture 的 request 必须符合前端合同');
assert.ok(profile, '共享 fixture 的 profile 必须符合前端合同');
assert.ok(response, '共享 fixture 的 response 必须符合前端合同');
assert.ok(run, '共享 fixture 的 run 必须符合前端合同');
assert.equal(userActions.every(Boolean), true, '共享 fixture 的 user actions 必须符合前端合同');

// Every public artifact is an exact-key schema, not a permissive JSON shape.
for (const [name, value, normalize] of [
  ['request', fixture.request, library.normalizeAgentRequest],
  ['profile', fixture.profile, library.normalizeAgentProfile],
  ['response', fixture.response, library.normalizeAgentResponse],
  ['run', fixture.run, library.normalizeAgentRun],
  ['user action', fixture.user_actions[0], library.normalizeUserAction],
]) {
  assert.equal(normalize({ ...clone(value), unexpected: true }), null, `${name} 不能接受未知字段`);
  assert.equal(normalize(withoutFirstField(value)), null, `${name} 不能接受缺失字段`);
}

assert.equal(library.normalizeAgentRequest({ ...fixture.request, trigger: 'daily' }), null);
assert.ok(library.normalizeAgentRequest({ ...fixture.request, trigger: 'scheduled' }));
assert.equal(library.normalizeAgentRequest({ ...fixture.request, window_days: 7 }), null);
assert.equal(library.normalizeAgentProfile({
  ...clone(fixture.profile),
  stats: { ...fixture.profile.stats, active: 0 },
}), null, 'profile.stats.active 必须与 memories 数量一致');
assert.equal(library.normalizeAgentResponse({
  ...clone(fixture.response), status: 'no_change', memory: fixture.memory,
}), null, '非 updated response 不得夹带 memory');
assert.equal(library.normalizeUserAction({
  ...clone(fixture.user_actions[1]), statement: '删除时不得夹带文本',
}), null, 'delete 的 statement/scope 必须严格为 null');

const builtRequest = library.buildAgentRequest({
  id: fixture.request.id,
  asOf: fixture.request.as_of,
  now: new Date(fixture.request.created_at),
});
assert.deepEqual(builtRequest, fixture.request, '浏览器只能创建 manual + 14d request');
assert.equal(library.buildAgentRequest({
  id: fixture.request.id,
  asOf: fixture.request.as_of,
  trigger: 'scheduled',
  now: new Date(fixture.request.created_at),
}).trigger, 'scheduled', '共享请求合同允许 Scheduler 创建 scheduled request');

const scheduleRecord = {
  schema_version: '1.0',
  kind: 'remember_agent_schedule',
  enabled: true,
  cadence: 'daily',
  hour: 21,
  minute: 0,
  updated_at: fixture.request.created_at,
};
assert.deepEqual(library.normalizeSchedule(scheduleRecord), {
  schemaVersion: '1.0',
  kind: 'remember_agent_schedule',
  enabled: true,
  cadence: 'daily',
  hour: 21,
  minute: 0,
  updatedAt: fixture.request.created_at,
});
assert.deepEqual(library.buildSchedule({
  enabled: false,
  now: new Date(fixture.request.created_at),
}), { ...scheduleRecord, enabled: false });
for (const invalidSchedule of [
  { ...scheduleRecord, unexpected: true },
  withoutFirstField(scheduleRecord),
  { ...scheduleRecord, enabled: 'true' },
  { ...scheduleRecord, cadence: 'weekly' },
  { ...scheduleRecord, hour: 20 },
  { ...scheduleRecord, minute: 1 },
  { ...scheduleRecord, updated_at: 'not-a-date' },
]) {
  assert.equal(library.normalizeSchedule(invalidSchedule), null, 'schedule 必须严格匹配固定日程合同');
}
assert.deepEqual(library.buildUserAction({
  id: fixture.user_actions[1].id,
  action: 'delete',
  memoryId: fixture.memory.memory_id,
  baseRevision: fixture.memory.revision,
  baseRevisionSha256: fixture.memory.revision_sha256,
  statement: '这段文本必须被强制清空',
  scope: '也必须清空',
  now: new Date(fixture.user_actions[1].created_at),
}), fixture.user_actions[1]);

// The filename is part of the identity contract for all immutable records.
assert.ok(library.normalizeAgentRequestRecord(fixture.request, fixture.request.id));
assert.equal(library.normalizeAgentRequestRecord(fixture.request, `arq_${'2'.repeat(24)}`), null);
assert.ok(library.normalizeAgentResponseRecord(fixture.response, fixture.request.id));
assert.equal(library.normalizeAgentResponseRecord(fixture.response, `arq_${'2'.repeat(24)}`), null);
assert.ok(library.normalizeAgentRunRecord(fixture.run, fixture.run.run_id));
assert.equal(library.normalizeAgentRunRecord(fixture.run, `arun_${'2'.repeat(24)}`), null);
assert.ok(library.normalizeUserActionRecord(fixture.user_actions[0], fixture.user_actions[0].id));
assert.equal(
  library.normalizeUserActionRecord(fixture.user_actions[0], `uact_${'2'.repeat(24)}`),
  null
);
assert.equal(library.requestFileName(fixture.request.id), `${fixture.request.id}.json`);
assert.equal(library.userActionFileName(fixture.user_actions[0].id), `${fixture.user_actions[0].id}.json`);
assert.throws(() => library.requestFileName('../request'), /ID/);
assert.throws(() => library.userActionFileName('../action'), /ID/);

// Request hash binds the exact browser-written bytes; response hash binds canonical JSON.
const requestRaw = `${JSON.stringify(fixture.request, null, 2)}\n`;
assert.equal(sha256(requestRaw), fixture.hashes.request_raw_sha256);
assert.equal(fixture.response.request_sha256, fixture.hashes.request_raw_sha256);
assert.equal(fixture.run.request_sha256, fixture.hashes.request_raw_sha256);

const responseCanonical = library.canonicalJson(fixture.response);
assert.equal(sha256(responseCanonical), fixture.hashes.response_canonical_sha256);
assert.equal(fixture.run.response_sha256, fixture.hashes.response_canonical_sha256);
const sortedResponseRaw = `${JSON.stringify(sortedJsonValue(fixture.response), null, 2)}\n`;
assert.equal(
  sha256(library.compactSortedJsonText(sortedResponseRaw)),
  fixture.hashes.response_canonical_sha256,
  'Python 持久化的 sorted+indented JSON 必须还原为同一 canonical hash'
);
assert.equal(
  library.compactSortedJsonText('{\n  "cost_usd": 0.0\n}\n'),
  '{"cost_usd":0.0}',
  '压缩 Python JSON 时必须保留 0.0 的数字拼写'
);
assert.notEqual(
  library.canonicalJson(JSON.parse('{"cost_usd":0.0}')),
  '{"cost_usd":0.0}',
  '不得用 parse/stringify 冒充响应原始 canonical 哈希'
);

// Current policy reconstruction is versioned independently from the frozen
// legacy artifact fixture. Historical runs remain schema-valid while the
// dashboard correctly treats their old policy hash as no longer current.
assert.equal(
  sha256(library.canonicalJson(library.policyPayloadFromRun(run))),
  '90cbd7072a5347692a60b6f65701a8a319fba528c1aa077b7bb60bd8fcfbacaf'
);
assert.notEqual(
  sha256(library.canonicalJson(library.policyPayloadFromRun(run))),
  fixture.hashes.policy_sha256
);
assert.equal(library.policyPayloadFromRun(run).prompt_version, 'remember-agent-v1.9');
assert.deepEqual(library.policyPayloadFromRun(run).authorization, {
  allowed_request_triggers: ['manual', 'scheduled'],
  model_context_trigger: 'user_authorized',
  window_days: 14,
});
const postCallTokenBudget = library.policyPayloadFromRun(run)
  .tool_contract.post_call_token_budget;
assert.deepEqual(postCallTokenBudget, {
  version: 'post-call-token-budget-v1.0',
  overshoot_condition: 'total_tokens_gt_max_total_tokens',
  next_provider_condition: 'total_tokens_gte_max_total_tokens',
  execute_overshoot_action: false,
  tool_result_kind: 'budget_blocked',
  finish_result_kind: 'rejected',
  invalid_result_kind: 'rejected',
  error_kind: 'budget',
});
for (const [field, replacement] of [
  ['version', 'post-call-token-budget-drift'],
  ['overshoot_condition', 'total_tokens_gte_max_total_tokens'],
  ['next_provider_condition', 'total_tokens_gt_max_total_tokens'],
  ['execute_overshoot_action', true],
  ['tool_result_kind', 'rejected'],
  ['finish_result_kind', 'budget_blocked'],
  ['invalid_result_kind', 'budget_blocked'],
  ['error_kind', 'runtime'],
]) {
  const mutatedPolicy = clone(library.policyPayloadFromRun(run));
  mutatedPolicy.tool_contract.post_call_token_budget[field] = replacement;
  assert.notEqual(
    sha256(library.canonicalJson(mutatedPolicy)),
    fixture.hashes.policy_sha256,
    `post_call_token_budget.${field} 必须绑定 policy hash`
  );
}
assert.deepEqual(
  library.policyPayloadFromRun(run).tool_contract.bounded_finish_investigation,
  {
    version: 'bounded-finish-investigation-v1.1',
    instruction_sha256: '4690dc2624b06749b1ca37fefcf0f4de62a6dedca0d0518f2bf8ddd5e79074ed',
    max_candidate_memory_ids: 8,
    max_rejections_per_run: 1,
    minimum_budget_max_turns: 4,
    minimum_remaining_turns_after_rejection: 3,
    required_next_action: 'read_memory',
  },
);
assert.deepEqual(
  library.policyPayloadFromRun(run).tool_contract.post_read_finish_investigation,
  {
    version: 'post-read-finish-investigation-v1.0',
    instruction_sha256: '23a1f40d1b34aa7d4c44efa041b6e10f5bdaea4ab0023b9add7f904e7c3e638a',
    max_rejections_per_run: 1,
    minimum_budget_max_turns: 5,
    minimum_remaining_turns_after_rejection: 2,
    requires_immediately_previous_successful_action: 'read_memory',
    prerequisites: {
      active_memories_nonempty: true,
      successful_read_memory: true,
      successful_search_history: false,
    },
    decision_review_required: true,
    next_action_scope: 'existing_action_allowlist',
  },
);
assert.deepEqual(
  library.policyPayloadFromRun(run).tool_contract.conflict_investigation,
  {
    version: 'conflict-investigation-v1.0',
    instruction_sha256: 'f09ddb454465d78229d4f003fe8a8f2f692c0a60fc40d1886bb06218a298f815',
  },
);
assert.equal(run.policySha256, fixture.hashes.policy_sha256);

// Production Agentic Workflow has a distinct, cross-language frozen policy
// while frozen legacy runs continue to validate unchanged.
const workflowRunRecord = clone(fixture.run);
workflowRunRecord.provider = 'mock-agentic-workflow';
workflowRunRecord.policy_sha256 = '5b6ab05f055e4698675352829d470ece25b0ae950b9e25260357920e811b44f0';
workflowRunRecord.steps[0] = {
  ...workflowRunRecord.steps[0],
  action: 'investigate',
  reason_code: 'plan_evidence',
  result_kind: 'investigation_materialized',
};
const workflowRun = library.normalizeAgentRun(workflowRunRecord);
assert.ok(workflowRun);
const workflowPolicy = library.policyPayloadFromRun(workflowRun);
assert.equal(workflowPolicy.prompt_version, 'remember-agent-v1.22');
assert.deepEqual(workflowPolicy.tool_contract.agentic_workflow, {
  version: 'agentic-workflow-investigation-v1.13',
  instruction_sha256: 'f22de6ec40b800bdf2781e91a71f63e962e19adc046bcbbfe696e1ffcca1c6f3',
  candidate_profile_scope: {
    version: 'person-profile-candidate-v1.0',
    instruction_sha256: '25dce1b7dbe64984c041a4ad01bbeffbb803b9f25024348d00da26db443b06d5',
    eligible_meaning: [
      'stable_user_preference',
      'user_judgment_method',
      'user_working_method',
      'user_change_or_tension',
    ],
    system_content_requires_explicit_user_preference: true,
    enforcement: 'candidate_scout_semantic_judgment',
  },
  first_phase_actions: ['finish', 'investigate'],
  search_phase_actions: ['finish', 'search_history'],
  decision_phase_actions: ['finalize_patch', 'finish'],
  max_initial_queries: 2,
  max_additional_searches: 2,
  max_query_results: 5,
  query_match_mode: 'exact_phrase_or_ranked_any_term',
  query_result_ranking: 'candidate_signal_then_term_count',
  max_patch_repairs: 1,
  fresh_phase_contexts: [
    'candidate_scout', 'query_planner', 'terminal_judge',
  ],
  workflow_reports_missing_evidence_requirements: true,
  candidate_relation_precedes_query_planning: true,
  new_target_null_omission_normalized: true,
  candidate_finish_review: {
    max_rejections_per_run: 0,
    requires_active_memories: true,
    minimum_remaining_turns: 2,
    agent_retains_target_query_patch_decisions: true,
    reason: 'dedicated_candidate_scout_is_the_review',
  },
  evidence_bundle_signal_labels: [
    'change_signal_refs', 'tension_signal_refs',
  ],
  stable_new_identity_bundle_fields: [
    'status',
    'required_statement',
    'required_scope',
    'eligible_evidence_refs',
  ],
  stable_new_terminal_gate: {
    version: 'stable-new-terminal-gate-v1.0',
    instruction_sha256: '170125a1d7c96bd7a066297c3024285d9729884695d59f94b707d79d9ead6f70',
    required_action: 'finalize_patch',
    required_uncertainty: 'medium',
    minimum_distinct_dates: 2,
    eligible_ref_source: 'evidence_bundle.stable_new_identity.eligible_evidence_refs',
    fatal_identity_statuses: [
      'ambiguous_statement',
      'scope_ambiguous',
      'scope_missing',
      'unsafe_repeated_statement',
    ],
    direct_self_patterns: [
      '我.{0,32}(?:通常|一般|习惯|倾向|偏好|坚持|优先|总是|往往|常常|会)',
      '(?:通常|一般|习惯上|一般而言).{0,24}我',
    ],
    temporal_or_reported_patterns: [
      '(?:曾经|以前|过去|当时|那次|昨天|今天|明天|本周|这周|本月|这个月)',
      '(?:这一次|本次|临时|暂时|一次性|仅这次|只在这次)',
      '(?:假设|假如|如果|比如|例如|示例|模板|转述|据说|他(?:说|认为)|她(?:说|认为))',
    ],
  },
  terminal_new_identity_contract: 'exact_required_statement_scope_and_finalize_when_stable',
  terminal_evidence_contract: 'verified_ref_ids',
  repair_uses_materialized_bundle_only: true,
  repair_includes_previous_decision: true,
  new_patch_receives_finite_evidence_guidance: true,
  terminal_statement_contract: {
    new: 'stable_identity_exact',
    reinforce: 'target_statement_exact',
    revise_tension: 'latest_selected_evidence_quote_exact',
  },
  flattened_finalize_exact_shape_normalized: true,
  flattened_finalize_optional_envelope_fields: [
    'schema_version', 'reason_code',
  ],
  invalid_action_retry_context: 'fresh_current_phase',
  workflow_executes: ['read_memory', 'search_history'],
  agent_decides: [
    'candidate_kind', 'target_memory_id', 'queries', 'terminal_patch_or_finish',
  ],
});
assert.equal(workflowPolicy.tool_contract.bounded_finish_investigation, undefined);
assert.equal(
  sha256(library.canonicalJson(workflowPolicy)),
  workflowRunRecord.policy_sha256,
);
const frozenWorkflowRun = {
  provider: 'deepseek-agentic-workflow',
  model: 'deepseek-v4-pro',
  budget: {
    maxTurns: 5,
    maxToolCalls: 5,
    maxTotalTokens: 40000,
    maxPromptChars: 180000,
  },
};
const frozenWorkflowPolicyCandidates = library
  .policyPayloadCandidatesFromRun(frozenWorkflowRun);
assert.deepEqual(
  frozenWorkflowPolicyCandidates.map(payload => payload.prompt_version),
  ['remember-agent-v1.22', 'remember-agent-v1.20', 'remember-agent-v1.19'],
  '只接受当前合同与两套实际发布过的 Workflow 历史合同',
);
assert.deepEqual(
  frozenWorkflowPolicyCandidates.map(payload => sha256(library.canonicalJson(payload))),
  [
    '68ee32c93cb3547ad15633b0557f37d3e0feb9cf072858e0e37e64a521537d8d',
    '2173475b4f96dc4751f4a0ca173b036be9313835f810df4ded319c6d7a35cce0',
    '2b610931fd2aac13c02ffcfb0e82c105f3fff40a3b1fed138ce961040c0cbcf9',
  ],
  '历史兼容必须来自完整 payload 重建，且与冻结跨语言 SHA 精确一致',
);
for (const payload of frozenWorkflowPolicyCandidates) {
  assert.equal(payload.provider, frozenWorkflowRun.provider);
  assert.equal(payload.model, frozenWorkflowRun.model);
  assert.deepEqual(payload.budget, {
    max_turns: 5,
    max_tool_calls: 5,
    max_total_tokens: 40000,
    max_prompt_chars: 180000,
  });
}
const historicalV20 = frozenWorkflowPolicyCandidates[1];
assert.equal(
  historicalV20.tool_contract.agentic_workflow.stable_new_terminal_gate,
  undefined,
);
assert.equal(
  historicalV20.tool_contract.agentic_workflow.terminal_new_identity_contract,
  'exact_required_statement_scope_when_stable',
);
const historicalV19 = frozenWorkflowPolicyCandidates[2];
assert.deepEqual(historicalV19.authorization, { trigger: 'manual', window_days: 14 });
assert.equal(historicalV19.tool_contract.agentic_workflow.candidate_profile_scope, undefined);
assert.equal(historicalV19.tool_contract.agentic_workflow.stable_new_identity_bundle_fields, undefined);
assert.equal(historicalV19.tool_contract.agentic_workflow.terminal_new_identity_contract, undefined);

for (const [label, changed] of [
  ['provider', { provider: 'mock-agentic-workflow' }],
  ['model', { model: 'deepseek-v3' }],
  ['max_turns', { budget: { ...frozenWorkflowRun.budget, maxTurns: 4 } }],
  ['max_tool_calls', { budget: { ...frozenWorkflowRun.budget, maxToolCalls: 4 } }],
  ['max_total_tokens', { budget: { ...frozenWorkflowRun.budget, maxTotalTokens: 39999 } }],
  ['max_prompt_chars', { budget: { ...frozenWorkflowRun.budget, maxPromptChars: 179999 } }],
]) {
  const candidateRun = {
    ...frozenWorkflowRun,
    ...changed,
  };
  const candidateHashes = library.policyPayloadCandidatesFromRun(candidateRun)
    .map(payload => sha256(library.canonicalJson(payload)));
  assert.equal(
    candidateHashes.includes('2173475b4f96dc4751f4a0ca173b036be9313835f810df4ded319c6d7a35cce0'),
    false,
    `${label} 漂移后不得借用 v1.20 冻结 SHA`,
  );
  assert.equal(
    candidateHashes.includes('2b610931fd2aac13c02ffcfb0e82c105f3fff40a3b1fed138ce961040c0cbcf9'),
    false,
    `${label} 漂移后不得借用 v1.19 冻结 SHA`,
  );
}
const mutatedCurrentPolicy = clone(frozenWorkflowPolicyCandidates[0]);
mutatedCurrentPolicy.tool_contract.agentic_workflow
  .stable_new_terminal_gate.minimum_distinct_dates = 1;
assert.equal(
  frozenWorkflowPolicyCandidates
    .map(payload => sha256(library.canonicalJson(payload)))
    .includes(sha256(library.canonicalJson(mutatedCurrentPolicy))),
  false,
  '当前策略字段被修改后仍必须 fail-closed',
);
assert.equal(
  library.policyPayloadCandidatesFromRun(run).length,
  1,
  '非 Workflow provider 不得获得 Workflow 历史兼容候选',
);
const workflowSummary = library.runSummaryFromRun(workflowRun);
assert.deepEqual(workflowSummary.actions, ['investigate', 'finalize_patch']);
assert.deepEqual(workflowSummary.reasonCodes, ['plan_evidence', 'evidence_sufficient']);
assert.equal(workflowSummary.toolCalls, 2);
assert.equal(workflowSummary.historyMatches, 2);
const dailyInventory = fixture.sources
  .map(source => ({ file: source.file, sha256: sha256(source.content) }))
  .sort((left, right) => left.file.localeCompare(right.file));
assert.deepEqual(dailyInventory, fixture.response.source_hashes);
assert.equal(sha256(library.canonicalJson(dailyInventory)), fixture.hashes.history_sha256);
assert.equal(response.inputHistorySha256, fixture.hashes.history_sha256);
assert.equal(run.inputHashes.historySha256, fixture.hashes.history_sha256);
assert.equal(response.inputProfileSha256, run.inputHashes.profileSha256);
assert.equal(response.inputFeedbackSha256, run.inputHashes.feedbackSha256);
assert.equal(response.inputUserActionSha256, run.inputHashes.userActionSha256);

const sources = Object.fromEntries(fixture.sources.map(source => [source.file, {
  sha256: source.sha256,
  lines: source.content.split(/\r?\n/),
}]));
assert.deepEqual(library.verifyProfileEvidence(profile, sources), { valid: true, reason: '' });
assert.deepEqual(library.verifyResponseSources(response, sources), { valid: true, reason: '' });

const driftedLines = clone(sources);
driftedLines['2026-08-01.md'].lines[1] = '这一行已被改写。';
assert.deepEqual(library.verifyProfileEvidence(profile, driftedLines), {
  valid: false, reason: 'evidence', file: '2026-08-01.md', line: 2,
});
const driftedHash = clone(sources);
driftedHash['2026-08-01.md'].sha256 = '0'.repeat(64);
assert.deepEqual(library.verifyResponseSources(response, driftedHash), {
  valid: false, reason: 'stale', file: '2026-08-01.md',
});

function artifactInput({
  normalizedProfile = profile,
  normalizedResponse = response,
  normalizedRun = run,
  requestSha256 = fixture.hashes.request_raw_sha256,
  responseCanonicalSha256 = fixture.hashes.response_canonical_sha256,
  policyValid = true,
  sourceMap = sources,
} = {}) {
  return {
    profile: normalizedProfile,
    profileRecord: { file: 'profile.json' },
    requests: [{ value: request, record: { sha256: requestSha256 } }],
    responses: [{
      value: normalizedResponse,
      record: { canonicalSha256: responseCanonicalSha256 },
    }],
    runs: [{ value: normalizedRun, record: {}, policyValid }],
    sources: sourceMap,
  };
}

const verified = library.verifyAgentArtifacts(artifactInput());
assert.equal(verified.valid, true);
assert.equal(verified.verifiedResponses.length, 1);

// A browser edit is materialized by the trusted Worker after the last Agent
// run. The profile hash therefore advances while latest_run correctly remains
// the same. This must not make the browser fall back to revision 0: the exact
// immutable action + legacy base ref prove the local r0 -> r1 transition.
const legacyBaseSha = 'a'.repeat(64);
const editedRevisionSha = 'b'.repeat(64);
const editedStatement = '长期记忆保存在本地，只有我确认后才能修改。';
const materializedEditRaw = {
  ...clone(fixture.user_actions[0]),
  created_at: '2026-08-11T05:00:00.000Z',
  base_revision: 0,
  base_revision_sha256: legacyBaseSha,
  statement: editedStatement,
  scope: '本地记忆',
};
const materializedEdit = library.normalizeUserAction(materializedEditRaw);
assert.ok(materializedEdit);
const editedProfileRaw = clone(fixture.profile);
editedProfileRaw.profile_sha256 = 'c'.repeat(64);
editedProfileRaw.projection_updated_at = materializedEditRaw.created_at;
editedProfileRaw.stats = {
  ...editedProfileRaw.stats,
  stored_seen: 1,
  stored_active: 1,
  tombstones: 0,
  user_actions_seen: 1,
  user_actions_valid: 1,
  user_actions_applied: 0,
};
editedProfileRaw.memories[0] = {
  ...editedProfileRaw.memories[0],
  revision: 1,
  revision_sha256: editedRevisionSha,
  title: editedStatement,
  statement: editedStatement,
  scope: materializedEditRaw.scope,
  created_at: materializedEditRaw.created_at,
  provenance: {
    origin: 'agent_memory',
    run_id: null,
    request_id: null,
    operation: 'user_edit',
    base_profile_ref: {
      tag_id: `ptag_${'d'.repeat(24)}`,
      sha256: legacyBaseSha,
    },
  },
};
const editedProfile = library.normalizeAgentProfile(editedProfileRaw);
assert.ok(editedProfile, '合法 user_edit r1 必须通过 profile 合同');
assert.equal(library.verifyAgentArtifacts(artifactInput({
  normalizedProfile: editedProfile,
})).reason, 'profile-link', '没有 user-action 时不得宽松 profile hash');
const editedVerification = library.verifyAgentArtifacts({
  ...artifactInput({ normalizedProfile: editedProfile }),
  userActions: [materializedEdit],
});
assert.equal(editedVerification.valid, true,
  `严格 user-action 链应允许 Agent run 后的本地 profile 演进: ${editedVerification.reason}`);
const nextDelete = library.buildUserAction({
  id: `uact_${'e'.repeat(24)}`,
  action: 'delete',
  memoryId: editedProfile.memories[0].memoryId,
  baseRevision: editedProfile.memories[0].revision,
  baseRevisionSha256: editedProfile.memories[0].revisionSha256,
  now: new Date('2026-08-11T05:01:00.000Z'),
});
assert.equal(nextDelete.base_revision, 1, '下一个用户动作必须绑定已固化的 revision 1');
assert.equal(nextDelete.base_revision_sha256, editedRevisionSha,
  '下一个用户动作必须绑定 revision 1 的精确 hash');

const wrongEditBase = { ...materializedEdit, baseRevisionSha256: 'f'.repeat(64) };
assert.equal(library.verifyAgentArtifacts({
  ...artifactInput({ normalizedProfile: editedProfile }),
  userActions: [wrongEditBase],
}).reason, 'profile-link', '错误 base hash 不得解释本地 profile 演进');

// Stale user-actions remain immutable audit records. Seeing all three files is
// required, but the stale base-0 delete must neither invalidate the valid r1
// projection nor count as an applied delete while that r1 memory is active.
const secondAuditedEdit = library.normalizeUserAction({
  ...materializedEditRaw,
  id: `uact_${'2'.repeat(24)}`,
  created_at: '2026-08-11T05:00:30.000Z',
});
const staleBaseZeroDelete = library.normalizeUserAction({
  ...nextDelete,
  id: `uact_${'3'.repeat(24)}`,
  created_at: '2026-08-11T05:01:30.000Z',
  base_revision: 0,
  base_revision_sha256: legacyBaseSha,
});
const auditedProfileRaw = clone(editedProfileRaw);
auditedProfileRaw.stats.user_actions_seen = 3;
auditedProfileRaw.stats.user_actions_valid = 2;
const auditedProfile = library.normalizeAgentProfile(auditedProfileRaw);
assert.ok(auditedProfile);
assert.equal(library.verifyAgentArtifacts({
  ...artifactInput({ normalizedProfile: auditedProfile }),
  userActions: [materializedEdit, secondAuditedEdit, staleBaseZeroDelete],
}).valid, true, '完整审计列表可保留 stale action，但当前 r1 必须由 exact edit 解释');
const invalidBaseRefRaw = clone(editedProfileRaw);
invalidBaseRefRaw.memories[0].provenance.base_profile_ref.tag_id = 'ptag_invalid';
assert.equal(library.normalizeAgentProfile(invalidBaseRefRaw), null,
  '非法 base_profile_ref 仍必须 fail-closed');
const invalidOriginRaw = clone(editedProfileRaw);
invalidOriginRaw.memories[0].provenance.origin = 'browser_memory';
assert.equal(library.normalizeAgentProfile(invalidOriginRaw), null,
  '非法 provenance origin 仍必须 fail-closed');

// The same rule also permits a materialized delete after a reload, without
// resurrecting the removed memory from the older run-linked profile.
const materializedDelete = library.normalizeUserAction(nextDelete);
const tombstoneRaw = {
  schema_version: '1.0',
  kind: 'remember_memory_revision',
  memory_id: editedProfileRaw.memories[0].memory_id,
  revision: 2,
  status: 'tombstone',
  created_at: nextDelete.created_at,
  run_id: null,
  request_id: null,
  operation: 'tombstone',
  previous_revision_sha256: editedRevisionSha,
  base_profile_ref: null,
  user_action_id: nextDelete.id,
  title: editedStatement,
  statement: editedStatement,
  scope: materializedEditRaw.scope,
  insight_kind: editedProfileRaw.memories[0].insight_kind,
  uncertainty: editedProfileRaw.memories[0].uncertainty,
  evidence: clone(editedProfileRaw.memories[0].evidence),
  counterevidence: clone(editedProfileRaw.memories[0].counterevidence),
  source_hashes: clone(fixture.response.source_hashes),
};
const tombstoneFileId = `${tombstoneRaw.memory_id}.r000002`;
const tombstoneReceipt = library.normalizeMemoryTombstoneRecord(
  tombstoneRaw, tombstoneFileId
);
assert.ok(tombstoneReceipt, '真实 r1 -> r2 tombstone receipt 必须通过严格合同');
const deletedProfileRaw = clone(editedProfileRaw);
deletedProfileRaw.profile_sha256 = '1'.repeat(64);
deletedProfileRaw.memories = [];
deletedProfileRaw.stats = {
  ...deletedProfileRaw.stats,
  stored_active: 0,
  tombstones: 1,
  user_actions_seen: 2,
  user_actions_valid: 2,
  active: 0,
};
const deletedProfile = library.normalizeAgentProfile(deletedProfileRaw);
assert.ok(deletedProfile);
assert.equal(library.verifyAgentArtifacts({
  ...artifactInput({ normalizedProfile: deletedProfile }),
  userActions: [materializedEdit, materializedDelete],
  tombstoneReceipts: [tombstoneReceipt],
}).valid, true, '已固化 delete 后的空投影必须可在重载后校验');
assert.equal(library.verifyAgentArtifacts({
  ...artifactInput({ normalizedProfile: deletedProfile }),
  userActions: [materializedEdit, materializedDelete],
  tombstoneReceipts: [],
}).reason, 'profile-link', '不得只凭 profile.stats.tombstones 接受删除');
const unrelatedDelete = {
  ...materializedDelete, id: `uact_${'4'.repeat(24)}`, memoryId: `mem_${'4'.repeat(24)}`,
};
assert.equal(library.verifyAgentArtifacts({
  ...artifactInput({ normalizedProfile: deletedProfile }),
  userActions: [materializedEdit, unrelatedDelete],
  tombstoneReceipts: [tombstoneReceipt],
}).reason, 'profile-link', '无关 delete 不得借用其他 memory 的 tombstone');
for (const [label, changedDelete] of [
  ['wrong base revision', { ...materializedDelete, baseRevision: 0 }],
  ['wrong base hash', { ...materializedDelete, baseRevisionSha256: '7'.repeat(64) }],
]) {
  assert.equal(library.verifyAgentArtifacts({
    ...artifactInput({ normalizedProfile: deletedProfile }),
    userActions: [materializedEdit, changedDelete],
    tombstoneReceipts: [tombstoneReceipt],
  }).reason, 'profile-link', `${label} 必须拒绝`);
}
for (const [label, changed] of [
  ['wrong action id', { ...tombstoneReceipt, userActionId: `uact_${'5'.repeat(24)}` }],
  ['wrong previous hash', { ...tombstoneReceipt, previousRevisionSha256: '6'.repeat(64) }],
  ['wrong revision chain', { ...tombstoneReceipt, revision: 3 }],
]) {
  assert.equal(library.verifyAgentArtifacts({
    ...artifactInput({ normalizedProfile: deletedProfile }),
    userActions: [materializedEdit, materializedDelete],
    tombstoneReceipts: [changed],
  }).reason, 'profile-link', `${label} 必须拒绝`);
}
assert.equal(library.normalizeMemoryTombstoneRecord(
  tombstoneRaw, `${tombstoneRaw.memory_id}.r000003`
), null, 'tombstone 文件名必须绑定精确 revision');

function latestRunRawFrom(runRaw) {
  const publicSteps = runRaw.steps.filter(step => (
    step.action !== 'provider_attempt' && step.result_kind !== 'provider_attempt_started'
  ));
  const actions = publicSteps.map(step => step.action);
  return {
    run_id: runRaw.run_id,
    run_key: runRaw.run_key,
    cache_hit: runRaw.cache_hit,
    request_id: runRaw.request_id,
    status: runRaw.status,
    completed_at: runRaw.completed_at,
    model_turns: runRaw.usage.model_calls,
    tool_calls: publicSteps.filter(step => (
      !['finish', 'invalid_action'].includes(step.action)
      && step.result_kind !== 'budget_blocked'
    )).length,
    actions,
    reason_codes: publicSteps.map(step => step.reason_code),
    history_matches: publicSteps
      .filter(step => step.result_kind === 'history_matches')
      .reduce((total, step) => total + step.result_count, 0),
    stop_reason: runRaw.error_kind || (runRaw.status === 'updated' ? 'patch_committed' : runRaw.status),
    usage: clone(runRaw.usage),
  };
}

function verifyTerminalVariant(responseRaw, runRaw) {
  const canonicalSha256 = sha256(library.canonicalJson(responseRaw));
  runRaw.response_sha256 = canonicalSha256;
  const profileRaw = clone(fixture.profile);
  profileRaw.latest_run = latestRunRawFrom(runRaw);
  const normalizedResponse = library.normalizeAgentResponse(responseRaw);
  const normalizedRun = library.normalizeAgentRun(runRaw);
  const normalizedProfile = library.normalizeAgentProfile(profileRaw);
  assert.ok(normalizedResponse, 'variant response 必须先通过字段合同');
  assert.ok(normalizedRun, 'variant run 必须先通过字段合同');
  assert.ok(normalizedProfile, 'variant profile 必须先通过字段合同');
  return library.verifyAgentArtifacts(artifactInput({
    normalizedProfile,
    normalizedResponse,
    normalizedRun,
    responseCanonicalSha256: canonicalSha256,
  }));
}

// V1.5 keeps the durable provider marker only while running or when its
// outcome is genuinely unknown. Its fixed shape and position are auditable.
const runningAttemptRaw = clone(fixture.run);
Object.assign(runningAttemptRaw, {
  status: 'running', completed_at: null, response_sha256: null, error_kind: null,
});
runningAttemptRaw.steps = [{
  turn: 1,
  action: 'provider_attempt',
  reason_code: 'provider_attempt_started',
  arguments_sha256: '9'.repeat(64),
  result_kind: 'provider_attempt_started',
  result_count: 0,
  error_kind: null,
}];
assert.ok(library.normalizeAgentRun(runningAttemptRaw));
assert.equal(library.normalizeAgentRun({
  ...clone(runningAttemptRaw),
  steps: [{ ...runningAttemptRaw.steps[0], reason_code: 'need_history_evidence' }],
}), null, 'provider marker 的 reason 不可漂移');
assert.equal(library.normalizeAgentRun({
  ...clone(runningAttemptRaw),
  steps: [clone(runningAttemptRaw.steps[0]), clone(runningAttemptRaw.steps[0])],
}), null, '同一 run 不可保留两个 provider marker');
const terminalBudgetMarker = clone(runningAttemptRaw);
Object.assign(terminalBudgetMarker, {
  status: 'budget_exhausted',
  completed_at: fixture.run.completed_at,
  response_sha256: fixture.hashes.response_canonical_sha256,
  error_kind: 'budget',
});
assert.equal(library.normalizeAgentRun(terminalBudgetMarker), null,
  '已知终态不得保留未决 provider marker；只有 unknown_attempt 例外');
const resolvedRuntimeRaw = clone(terminalBudgetMarker);
Object.assign(resolvedRuntimeRaw, { status: 'error', error_kind: 'runtime' });
resolvedRuntimeRaw.steps[0] = {
  ...resolvedRuntimeRaw.steps[0],
  reason_code: 'provider_attempt_runtime',
  result_kind: 'provider_attempt_resolved',
  error_kind: 'runtime',
};
assert.ok(library.normalizeAgentRun(resolvedRuntimeRaw),
  '已知 runtime 失败必须保留严格的 resolved 内部 checkpoint');
const resolvedBudgetRaw = clone(resolvedRuntimeRaw);
Object.assign(resolvedBudgetRaw, { status: 'budget_exhausted', error_kind: 'budget' });
resolvedBudgetRaw.steps[0] = {
  ...resolvedBudgetRaw.steps[0], reason_code: 'provider_attempt_budget', error_kind: 'budget',
};
assert.ok(library.normalizeAgentRun(resolvedBudgetRaw),
  '已知 budget 失败必须保留严格的 resolved 内部 checkpoint');
assert.equal(library.normalizeAgentRun({
  ...clone(resolvedRuntimeRaw),
  steps: [{ ...resolvedRuntimeRaw.steps[0], reason_code: 'provider_attempt_budget' }],
}), null, 'resolved checkpoint 的 reason 必须与 error_kind 一致');

const unknownUsage = {
  model_calls: 1,
  prompt_tokens: 0,
  completion_tokens: 0,
  total_tokens: 0,
  prompt_cache_hit_tokens: 0,
  prompt_cache_miss_tokens: 0,
  reasoning_tokens: 0,
  usage_missing: true,
  cost_usd: null,
};
const unknownRunRaw = clone(fixture.run);
Object.assign(unknownRunRaw, {
  status: 'error', error_kind: 'unknown_attempt', usage: clone(unknownUsage),
});
unknownRunRaw.steps = clone(runningAttemptRaw.steps);
const unknownResponseRaw = clone(fixture.response);
Object.assign(unknownResponseRaw, {
  status: 'error', record_days: 0, source_hashes: [], memory: null,
  usage: clone(unknownUsage), error: '上一次 Provider 调用结果未知',
  error_kind: 'unknown_attempt',
});
unknownResponseRaw.trace = {
  model_turns: 1,
  tool_calls: 0,
  actions: [],
  reason_codes: [],
  history_matches: 0,
  stop_reason: 'unknown_attempt',
};
assert.equal(verifyTerminalVariant(clone(unknownResponseRaw), clone(unknownRunRaw)).valid, true,
  'unknown_attempt 可保留 run 来源审计，同时 response 降为空来源');
const partialUnknownResponse = clone(unknownResponseRaw);
partialUnknownResponse.source_hashes = [clone(fixture.response.source_hashes[0])];
partialUnknownResponse.record_days = 1;
assert.equal(verifyTerminalVariant(partialUnknownResponse, clone(unknownRunRaw)).valid, false,
  'unknown_attempt 来源只能完整匹配 run 或全部清空，不能接受部分集合');
const countedMarkerResponse = clone(unknownResponseRaw);
countedMarkerResponse.trace.tool_calls = 1;
assert.equal(verifyTerminalVariant(countedMarkerResponse, clone(unknownRunRaw)).valid, false,
  'response trace 不得把 provider checkpoint 算作工具调用');
const exposedMarkerResponse = clone(unknownResponseRaw);
exposedMarkerResponse.trace.actions = ['provider_attempt'];
exposedMarkerResponse.trace.reason_codes = ['provider_attempt_started'];
assert.equal(library.normalizeAgentResponse(exposedMarkerResponse), null,
  'provider checkpoint 是内部审计事件，不得进入公开 trace');

const resolvedRuntimeRun = clone(fixture.run);
Object.assign(resolvedRuntimeRun, {
  status: 'error', error_kind: 'runtime', usage: clone(unknownUsage),
});
resolvedRuntimeRun.steps = [clone(resolvedRuntimeRaw.steps[0])];
const resolvedRuntimeResponse = clone(fixture.response);
Object.assign(resolvedRuntimeResponse, {
  status: 'error', memory: null, usage: clone(unknownUsage),
  error: 'Provider 运行失败', error_kind: 'runtime',
});
resolvedRuntimeResponse.trace = {
  model_turns: 1,
  tool_calls: 0,
  actions: [],
  reason_codes: [],
  history_matches: 0,
  stop_reason: 'runtime',
};
assert.equal(verifyTerminalVariant(
  clone(resolvedRuntimeResponse), clone(resolvedRuntimeRun)
).valid, true, 'resolved runtime checkpoint 不得暴露到公开 trace/latest_run');

const resolvedBudgetRun = clone(resolvedRuntimeRun);
Object.assign(resolvedBudgetRun, { status: 'budget_exhausted', error_kind: 'budget' });
resolvedBudgetRun.steps = [clone(resolvedBudgetRaw.steps[0])];
const resolvedBudgetResponse = clone(resolvedRuntimeResponse);
Object.assign(resolvedBudgetResponse, {
  status: 'budget_exhausted', error: 'Agent 已超过 Token 预算', error_kind: 'budget',
});
resolvedBudgetResponse.trace.stop_reason = 'budget';
assert.equal(verifyTerminalVariant(
  clone(resolvedBudgetResponse), clone(resolvedBudgetRun)
).valid, true, 'resolved budget checkpoint 不得暴露到公开 trace/latest_run');

// A CAS conflict keeps the complete source audit; source-byte staleness is the
// only stale branch that clears it. These are distinct Python producer paths.
const casRunRaw = clone(fixture.run);
Object.assign(casRunRaw, { status: 'stale', error_kind: 'cas' });
casRunRaw.steps[1] = {
  ...casRunRaw.steps[1], result_kind: 'rejected', result_count: 0, error_kind: 'cas',
};
const casResponseRaw = clone(fixture.response);
Object.assign(casResponseRaw, {
  status: 'stale', memory: null, error: 'Agent 输入已被用户动作更新', error_kind: 'cas',
});
casResponseRaw.trace.stop_reason = 'cas';
assert.equal(verifyTerminalVariant(clone(casResponseRaw), clone(casRunRaw)).valid, true,
  'CAS stale 必须保留 response/run 一致的完整来源审计');
const emptyCasResponse = clone(casResponseRaw);
emptyCasResponse.source_hashes = [];
emptyCasResponse.record_days = 0;
assert.equal(verifyTerminalVariant(emptyCasResponse, clone(casRunRaw)).valid, false,
  'CAS stale 不得伪装成无来源的 source-stale');

const sourceStaleRunRaw = clone(fixture.run);
Object.assign(sourceStaleRunRaw, { status: 'stale', error_kind: 'stale' });
sourceStaleRunRaw.steps[1] = {
  ...sourceStaleRunRaw.steps[1], result_kind: 'rejected', result_count: 0, error_kind: 'stale',
};
const sourceStaleResponseRaw = clone(fixture.response);
Object.assign(sourceStaleResponseRaw, {
  status: 'stale', record_days: 0, source_hashes: [], memory: null,
  error: 'Agent 来源已变化', error_kind: 'stale',
});
sourceStaleResponseRaw.trace.stop_reason = 'stale';
assert.equal(verifyTerminalVariant(clone(sourceStaleResponseRaw), clone(sourceStaleRunRaw)).valid, true,
  'source-stale 清空 response 来源时仍可保留 run 的输入审计');
const nonemptySourceStale = clone(sourceStaleResponseRaw);
nonemptySourceStale.source_hashes = clone(fixture.response.source_hashes);
nonemptySourceStale.record_days = nonemptySourceStale.source_hashes.length;
assert.equal(verifyTerminalVariant(nonemptySourceStale, clone(sourceStaleRunRaw)).valid, false,
  'source-stale 的公开 response 必须清空已漂移来源');

const loopRunRaw = clone(fixture.run);
Object.assign(loopRunRaw, { status: 'budget_exhausted', error_kind: 'loop' });
loopRunRaw.steps = [clone(fixture.run.steps[0]), {
  ...clone(fixture.run.steps[0]),
  turn: 2,
  result_kind: 'loop_blocked',
  result_count: 0,
  error_kind: 'loop',
}];
const loopResponseRaw = clone(fixture.response);
Object.assign(loopResponseRaw, {
  status: 'budget_exhausted', memory: null, error: 'Agent 重复了相同工具动作',
  error_kind: 'loop',
});
loopResponseRaw.trace = {
  model_turns: 2,
  tool_calls: 2,
  actions: ['search_history', 'search_history'],
  reason_codes: ['need_history_evidence', 'need_history_evidence'],
  history_matches: 2,
  stop_reason: 'loop',
};
assert.equal(verifyTerminalVariant(clone(loopResponseRaw), clone(loopRunRaw)).valid, true,
  'loop_blocked 必须保留真实重复动作的 trace');
const unboundLoopRun = clone(loopRunRaw);
unboundLoopRun.steps[1].arguments_sha256 = '8'.repeat(64);
assert.equal(library.normalizeAgentRun(unboundLoopRun), null,
  'loop_blocked 必须绑定更早的同 action + arguments hash');

const budgetRunRaw = clone(fixture.run);
Object.assign(budgetRunRaw, { status: 'budget_exhausted', error_kind: 'budget' });
budgetRunRaw.steps[1] = {
  ...budgetRunRaw.steps[1], result_kind: 'budget_blocked', result_count: 0, error_kind: 'budget',
};
const budgetResponseRaw = clone(fixture.response);
Object.assign(budgetResponseRaw, {
  status: 'budget_exhausted', memory: null, error: 'Agent 已超过工具调用预算',
  error_kind: 'budget',
});
budgetResponseRaw.trace = {
  model_turns: 2,
  tool_calls: 1,
  actions: ['search_history', 'finalize_patch'],
  reason_codes: ['need_history_evidence', 'evidence_sufficient'],
  history_matches: 2,
  stop_reason: 'budget',
};
assert.equal(verifyTerminalVariant(clone(budgetResponseRaw), clone(budgetRunRaw)).valid, true,
  'budget_blocked 保留已解析动作，但不计为已执行工具');
const countedBlockedTool = clone(budgetResponseRaw);
countedBlockedTool.trace.tool_calls = 2;
assert.equal(verifyTerminalVariant(countedBlockedTool, clone(budgetRunRaw)).valid, false,
  'budget_blocked 不得增加 response trace.tool_calls');

const laterCompletedRun = library.normalizeAgentRun({
  ...clone(fixture.run), completed_at: '2026-08-11T04:00:07.000Z',
});
const laterCompletedProfileRaw = clone(fixture.profile);
laterCompletedProfileRaw.latest_run.completed_at = '2026-08-11T04:00:07.000Z';
assert.equal(library.verifyAgentArtifacts(artifactInput({
  normalizedProfile: library.normalizeAgentProfile(laterCompletedProfileRaw),
  normalizedRun: laterCompletedRun,
})).valid, true, 'run.completed_at 可晚于 response.created_at，但不得早于它');

assert.equal(
  library.verifyAgentArtifacts(artifactInput({ requestSha256: '0'.repeat(64) })).reason,
  'profile-link',
  '请求原始字节哈希漂移后响应链不再可信'
);
assert.equal(
  library.verifyAgentArtifacts(artifactInput({ responseCanonicalSha256: '0'.repeat(64) })).reason,
  'profile-link',
  '响应 canonical hash 漂移后运行链不再可信'
);
assert.equal(
  library.verifyAgentArtifacts(artifactInput({ policyValid: false })).reason,
  'profile-link',
  '策略哈希不匹配时不得接受运行结果'
);

const wrongHistoryResponseRaw = clone(fixture.response);
wrongHistoryResponseRaw.input_history_sha256 = '0'.repeat(64);
const wrongHistoryResponse = library.normalizeAgentResponse(wrongHistoryResponseRaw);
const wrongHistoryCanonical = sha256(library.canonicalJson(wrongHistoryResponseRaw));
const wrongHistoryRunRaw = { ...clone(fixture.run), response_sha256: wrongHistoryCanonical };
const wrongHistoryRun = library.normalizeAgentRun(wrongHistoryRunRaw);
assert.equal(library.verifyAgentArtifacts(artifactInput({
  normalizedResponse: wrongHistoryResponse,
  normalizedRun: wrongHistoryRun,
  responseCanonicalSha256: wrongHistoryCanonical,
})).reason, 'profile-link', 'response/run 的 history hash 不同时不得接受');

const wrongLatestProfileRaw = clone(fixture.profile);
wrongLatestProfileRaw.latest_run.history_matches = 3;
const wrongLatestProfile = library.normalizeAgentProfile(wrongLatestProfileRaw);
assert.equal(library.verifyAgentArtifacts(artifactInput({
  normalizedProfile: wrongLatestProfile,
})).reason, 'latest-run');

const wrongResultResponseRaw = clone(fixture.response);
wrongResultResponseRaw.result_profile_sha256 = '0'.repeat(64);
const wrongResultCanonical = sha256(library.canonicalJson(wrongResultResponseRaw));
const wrongResultRun = library.normalizeAgentRun({
  ...clone(fixture.run), response_sha256: wrongResultCanonical,
});
assert.equal(library.verifyAgentArtifacts(artifactInput({
  normalizedResponse: library.normalizeAgentResponse(wrongResultResponseRaw),
  normalizedRun: wrongResultRun,
  responseCanonicalSha256: wrongResultCanonical,
})).reason, 'profile-link', 'response.result_profile_sha256 必须绑定当前 profile');

// Pending actions are immutable events: first valid delete is terminal.
assert.equal(library.projectPendingUserActions(profile, [userActions[0]]).length, 1);
assert.equal(
  library.projectPendingUserActions(profile, [userActions[0]])[0].statement,
  fixture.user_actions[0].statement
);
assert.deepEqual(
  library.projectPendingUserActions(profile, [userActions[2], userActions[0], userActions[1]]),
  [],
  '即使传入顺序被打乱，删除后的 edit 也不能复活 memory'
);
const staleDelete = {
  ...userActions[1], baseRevisionSha256: '0'.repeat(64),
};
assert.equal(
  library.projectPendingUserActions(profile, [userActions[0], staleDelete]).length,
  1,
  '无法绑定 base revision 的删除动作必须被忽略'
);

// Validate the exact same fixture against the trusted Python contract.
const pythonContract = String.raw`
import hashlib
import json
import sys
import tempfile
from pathlib import Path

fixture_path = Path(sys.argv[1])
root = Path(sys.argv[2])
sys.path.insert(0, str(root / "context-agent"))

import agent_v1
from core import ContractError, canonical_json

fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

with tempfile.TemporaryDirectory(prefix="remember-agent-v1-contract-") as temporary:
    vault = Path(temporary)
    for source in fixture["sources"]:
        (vault / source["file"]).write_text(source["content"], encoding="utf-8")

    request_dir = vault / ".context-agent" / "agent-v1" / "requests"
    request_dir.mkdir(parents=True)
    request_raw = (json.dumps(fixture["request"], ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    request_file = request_dir / f"{fixture['request']['id']}.json"
    request_file.write_bytes(request_raw)
    loaded, _, request_sha = agent_v1.load_agent_request(vault, fixture["request"]["id"])
    assert loaded == fixture["request"]
    assert request_sha == fixture["hashes"]["request_raw_sha256"]

    agent_v1.validate_agent_profile(fixture["profile"], vault)
    agent_v1.validate_agent_response(fixture["response"], vault)
    agent_v1.validate_agent_run(fixture["run"])
    for action in fixture["user_actions"]:
        agent_v1.validate_user_action(action)

    provider_marker = {
        "turn": 3,
        "action": "provider_attempt",
        "reason_code": "provider_attempt_started",
        "arguments_sha256": "9" * 64,
        "result_kind": "provider_attempt_started",
        "result_count": 0,
        "error_kind": None,
    }
    public_steps = agent_v1._public_run_steps(
        [*fixture["run"]["steps"], provider_marker]
    )
    assert public_steps == fixture["run"]["steps"]
    for error_kind in ("runtime", "budget"):
        resolved_marker = dict(provider_marker)
        resolved_marker.update(
            {
                "reason_code": f"provider_attempt_{error_kind}",
                "result_kind": "provider_attempt_resolved",
                "error_kind": error_kind,
            }
        )
        resolved_run = dict(fixture["run"])
        resolved_run["steps"] = [resolved_marker]
        resolved_run["status"] = (
            "error" if error_kind == "runtime" else "budget_exhausted"
        )
        resolved_run["error_kind"] = error_kind
        agent_v1.validate_agent_run(resolved_run)
        assert agent_v1._public_run_steps([resolved_marker]) == []
    budget_blocked = dict(fixture["run"]["steps"][-1])
    budget_blocked.update(
        {"result_kind": "budget_blocked", "result_count": 0, "error_kind": "budget"}
    )
    assert agent_v1._public_tool_call_count(
        [fixture["run"]["steps"][0], budget_blocked, provider_marker]
    ) == 1

    validators = (
        (fixture["request"], agent_v1.validate_agent_request),
        (fixture["profile"], lambda value: agent_v1.validate_agent_profile(value, vault)),
        (fixture["response"], lambda value: agent_v1.validate_agent_response(value, vault)),
        (fixture["run"], agent_v1.validate_agent_run),
        (fixture["user_actions"][0], agent_v1.validate_user_action),
    )
    for value, validate in validators:
        extra = dict(value)
        extra["unexpected"] = True
        missing = dict(value)
        missing.pop(next(iter(missing)))
        for invalid in (extra, missing):
            try:
                validate(invalid)
            except ContractError:
                pass
            else:
                raise AssertionError("exact-key contract accepted an invalid artifact")

    expected_response_hash = hashlib.sha256(
        canonical_json(fixture["response"]).encode("utf-8")
    ).hexdigest()
    assert expected_response_hash == fixture["hashes"]["response_canonical_sha256"]
    assert fixture["run"]["response_sha256"] == expected_response_hash
    assert agent_v1._daily_history_watermark(
        vault, as_of=fixture["request"]["as_of"]
    ) == fixture["hashes"]["history_sha256"]
    assert fixture["response"]["input_history_sha256"] == fixture["run"]["input_hashes"]["history_sha256"]
    assert fixture["run"]["input_hashes"]["history_sha256"] == fixture["hashes"]["history_sha256"]

    budget = agent_v1.AgentBudget(**fixture["run"]["budget"])
    expected_policy = agent_v1.make_agent_policy_sha256(
        provider=fixture["run"]["provider"],
        model=fixture["run"]["model"],
        budget=budget,
    )
    assert expected_policy == "90cbd7072a5347692a60b6f65701a8a319fba528c1aa077b7bb60bd8fcfbacaf"
    assert fixture["run"]["policy_sha256"] != expected_policy
    workflow_policy = agent_v1.make_agent_policy_sha256(
        provider="mock-agentic-workflow",
        model=fixture["run"]["model"],
        budget=budget,
    )
    assert workflow_policy == "5b6ab05f055e4698675352829d470ece25b0ae950b9e25260357920e811b44f0"
    run_key_payload = {
        "policy_sha256": fixture["run"]["policy_sha256"],
        "source_hashes": sorted(
            fixture["run"]["input_hashes"]["source_hashes"],
            key=lambda item: (item["file"], item["sha256"]),
        ),
        "history_sha256": fixture["run"]["input_hashes"]["history_sha256"],
        "profile_sha256": fixture["run"]["input_hashes"]["profile_sha256"],
        "feedback_sha256": fixture["run"]["input_hashes"]["feedback_sha256"],
        "user_action_sha256": fixture["run"]["input_hashes"]["user_action_sha256"],
    }
    expected_run_key = "ark_" + hashlib.sha256(
        canonical_json(run_key_payload).encode("utf-8")
    ).hexdigest()[:24]
    assert fixture["run"]["run_key"] == expected_run_key
    assert fixture["response"]["run_key"] == expected_run_key
    assert fixture["profile"]["latest_run"]["run_key"] == expected_run_key

    mismatched = request_dir / ("arq_" + "2" * 24 + ".json")
    mismatched.write_bytes(request_raw)
    try:
        agent_v1.load_agent_request(vault, str(mismatched))
    except ContractError:
        pass
    else:
        raise AssertionError("request filename/id mismatch was accepted")

    states = agent_v1._reduce_user_actions(fixture["user_actions"])
    state = states[fixture["memory"]["memory_id"]]
    assert state["delete"]["id"] == fixture["user_actions"][1]["id"]
    assert state["edit"]["id"] == fixture["user_actions"][0]["id"]

    original = (vault / "2026-08-01.md").read_text(encoding="utf-8")
    (vault / "2026-08-01.md").write_text(original.replace("评审方案前", "原文已改动"), encoding="utf-8")
    try:
        agent_v1.validate_agent_response(fixture["response"], vault)
    except ContractError as error:
        assert error.kind == "stale"
    else:
        raise AssertionError("stale source hash was accepted")

print("python-cross-contract-ok")
`;

const python = spawnSync('python3', ['-c', pythonContract, fixturePath, rootPath], {
  encoding: 'utf8',
});
assert.equal(
  python.status,
  0,
  `Python cross-contract validation failed:\n${python.stdout}${python.stderr}`
);
assert.match(python.stdout, /python-cross-contract-ok/);

console.log('✓ Remember Agent V1 library: cross-validates exact schemas, hashes, evidence, and terminal delete');
