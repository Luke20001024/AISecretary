// Memento Cognitive Secretary homepage projection contract.
// Pure validation and deterministic presentation helpers only: no file access,
// provider calls, persistent writes, or raw-record reads.

(function exposeCognitiveHome(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MementoCognitiveHome = api;
})(typeof window !== 'undefined' ? window : globalThis, function createCognitiveHome() {
  'use strict';

  const COGNITIVE_SCHEMA_VERSION = '1.0';
  const HOME_PROJECTION_VERSION = 'cognitive-secretary-home-v1';
  const LANDSCAPE_PROJECTION_VERSION = 'cognitive-landscape-v1';

  const SHA256_RE = /^[0-9a-f]{64}$/;
  const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
  const DATETIME_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?(Z|([+-])(\d{2}):(\d{2}))$/;
  const RECORD_RE = /^rec_[0-9a-f]{24}$/;
  const RECEIPT_RE = /^rcp_[0-9a-f]{24}$/;
  const REUSABLE_MEMORY_RE = /^rmem_[0-9a-f]{24}$/;
  const RELATION_RE = /^rel_[0-9a-f]{24}$/;
  const UNDERSTANDING_RE = /^mem_[0-9a-f]{24}$/;
  const PEAK_RE = /^peak_[0-9a-f]{24}$/;
  const LANDSCAPE_RE = /^lnd_[0-9a-f]{24}$/;
  const SUMMARY_RE = /^dsum_\d{8}$/;
  const DAILY_BUNDLE_RE = /^db_\d{8}$/;
  const COGNITIVE_ACTION_RE = /^cact_[0-9a-f]{24}$/;
  const COGNITIVE_ACTION_RESULT_RE = /^cares_[0-9a-f]{24}$/;
  const MANUAL_DAY_REQUEST_RE = /^cman_[0-9a-f]{24}$/;
  const MANUAL_DAY_RESULT_RE = /^cmanr_[0-9a-f]{24}$/;

  const CONTENT_TYPES = new Set([
    'quote', 'own_idea', 'observation', 'question', 'decision', 'action',
    'experience', 'fact', 'learning',
  ]);
  const PURPOSES = new Set([
    'find_later', 'continue_thinking', 'create', 'future_decision',
    'action_clue', 'preserve_only',
  ]);
  const STANCES = new Set([
    'agree', 'doubt', 'reject', 'inspired', 'self_observation', 'unresolved',
    'unknown',
  ]);
  const COGNITIVE_STATES = new Set([
    'first_seen', 'repeated', 'supports_existing', 'conflicts_existing',
    'revises_existing', 'verified', 'unknown',
  ]);
  const RELATION_TYPES = new Set([
    'supports', 'counterexample', 'revises', 'scope_boundary', 'same_topic',
  ]);
  const SOURCE_TYPES = new Set([
    'text', 'screenshot_ocr', 'voice_transcript', 'image_note', 'file_note',
  ]);
  const HOME_RECORD_STATUSES = new Set([
    'raw_saved', 'processing', 'ready', 'needs_review', 'original_only',
    'no_candidate', 'failed', 'merged',
  ]);
  const DAILY_RUN_STATUSES = new Set([
    'not_started', 'running', 'committed', 'committed_with_warnings',
    'no_change', 'no_candidate', 'no_records', 'no_receipts', 'stale',
    'error', 'budget_exhausted',
  ]);
  const SCHEDULE_RUN_STATUSES = new Set([
    'not_started', 'committed', 'committed_with_warnings', 'no_change',
    'no_candidate', 'no_records', 'no_receipts', 'stale', 'error',
    'budget_exhausted',
  ]);
  const PEAK_LIFECYCLES = new Set(['active', 'tension', 'dormant']);

  const OBJECT_PATTERNS = new Map([
    ['source_record', RECORD_RE],
    ['interpretation_receipt', RECEIPT_RE],
    ['daily_summary', SUMMARY_RE],
    ['reusable_memory', REUSABLE_MEMORY_RE],
    ['relation', RELATION_RE],
    ['understanding', UNDERSTANDING_RE],
    ['daily_bundle', DAILY_BUNDLE_RE],
  ]);

  const OBJECT_REF_FIELDS = new Set(['kind', 'id', 'revision', 'revision_sha256']);
  const PEAK_FIELDS = new Set([
    'peak_id', 'understanding_ref', 'x', 'y', 'elevation', 'evidence_count',
    'counterevidence_count', 'recent_change', 'lifecycle',
  ]);
  const NODE_FIELDS = new Set(['memory_ref', 'x', 'y', 'state', 'recent']);
  const EDGE_FIELDS = new Set(['relation_ref', 'from_id', 'to_id', 'type']);
  const LANDSCAPE_FIELDS = new Set([
    'schema_version', 'kind', 'snapshot_id', 'created_at', 'as_of',
    'projection_version', 'input_hashes', 'summary', 'terrain', 'peaks',
    'nodes', 'edges', 'previous_snapshot_sha256',
  ]);
  const LANDSCAPE_INPUT_FIELDS = new Set([
    'agent_profile_sha256', 'reusable_memory_head_sha256',
    'relation_head_sha256', 'user_action_watermark_sha256',
  ]);
  const LANDSCAPE_SUMMARY_FIELDS = new Set([
    'active_understandings', 'recent_changes', 'observing_candidates',
  ]);
  const TERRAIN_FIELDS = new Set([
    'algorithm_version', 'grid_size', 'contour_levels', 'coordinate_space',
  ]);
  const HOME_RECORD_FIELDS = new Set([
    'record_ref', 'receipt_ref', 'captured_at', 'source_type', 'source_app',
    'status', 'summary', 'content_types', 'topics', 'purposes', 'memory_refs',
    'understanding_refs',
  ]);
  const HOME_FIELDS = new Set([
    'schema_version', 'kind', 'projection_version', 'generated_at',
    'local_date', 'input_hashes', 'landscape_ref', 'landscape_summary',
    'today_status', 'records', 'schedule', 'warnings',
  ]);
  const HOME_INPUT_FIELDS = new Set([
    'record_head_sha256', 'receipt_head_sha256', 'daily_bundle_head_sha256',
    'agent_profile_sha256', 'landscape_snapshot_sha256',
    'user_action_watermark_sha256',
  ]);
  const LANDSCAPE_REF_FIELDS = new Set(['snapshot_id', 'snapshot_sha256']);
  const TODAY_STATUS_FIELDS = new Set([
    'saved', 'interpreted', 'merged', 'needs_review', 'daily_run_status',
  ]);
  const SCHEDULE_FIELDS = new Set([
    'enabled', 'hour', 'minute', 'next_due_at', 'last_run_status',
  ]);
  const FORMAL_CATALOG_FIELDS = new Set([
    'schema_version', 'kind', 'revision', 'generated_at', 'daily_bundles',
    'daily_summaries', 'reusable_memories', 'relations',
  ]);
  const PROJECTION_AUTHORITY_FIELDS = new Set([
    'agent_profile_sha256', 'active_understanding_refs', 'current_memory_refs',
    'current_relation_refs', 'user_action_watermark_sha256',
    'today_record_refs', 'today_receipt_refs', 'daily_bundle_head_sha256',
  ]);
  const COGNITIVE_ACTION_FIELDS = new Set([
    'schema_version', 'kind', 'id', 'created_at', 'action', 'target_ref',
    'payload',
  ]);
  const COGNITIVE_ACTION_RESULT_FIELDS = new Set([
    'schema_version', 'kind', 'id', 'action_id', 'action_sha256', 'status',
    'completed_at', 'materialized_refs', 'error_kind',
  ]);
  const MANUAL_DAY_REQUEST_FIELDS = new Set([
    'schema_version', 'kind', 'id', 'created_at', 'local_date', 'status',
  ]);
  const MANUAL_DAY_RESULT_FIELDS = new Set([
    'schema_version', 'kind', 'id', 'request_id', 'request_sha256',
    'completed_at', 'local_date', 'status', 'runner_status', 'error_kind',
  ]);
  const RECEIPT_FACET_FIELDS = new Set([
    'content_types', 'topics', 'objects', 'stance', 'cognitive_state',
    'purposes',
  ]);
  const RECEIPT_EDIT_FIELDS = new Set(['summary', 'facets']);
  const MEMORY_EDIT_FIELDS = new Set(['statement', 'topics', 'purposes']);
  const RELATION_EDIT_FIELDS = new Set(['type', 'statement']);

  const STATUS_LABELS = Object.freeze({
    raw_saved: '原文已保存',
    processing: '正在整理这一条',
    ready: '已初步整理，等待今日归并',
    needs_review: '有一处需要你确认',
    original_only: '仅保留原文',
    no_candidate: '已检查，本条没有形成可归并内容',
    failed: '原文已保存，整理尚未完成',
    merged: '已进入今日归并',
  });

  const RELATION_LABELS = Object.freeze({
    supports: '支持',
    counterexample: '反例',
    revises: '修订',
    scope_boundary: '适用边界',
    same_topic: '同一主题',
  });

  class CognitiveContractError extends Error {
    constructor(message, kind = 'schema') {
      super(message);
      this.name = 'CognitiveContractError';
      this.kind = kind;
    }
  }

  function fail(message, kind = 'schema') {
    throw new CognitiveContractError(message, kind);
  }

  function isObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }

  function exactObject(value, fields, name) {
    if (!isObject(value)) fail(`${name} 必须是 JSON object`);
    const keys = Object.keys(value);
    if (keys.length !== fields.size || keys.some(key => !fields.has(key))) {
      fail(`${name} 字段不符合合同`);
    }
    return value;
  }

  function ensureText(value, name, maximum) {
    if (typeof value !== 'string' || !value.trim() || value !== value.trim()) {
      fail(`${name} 必须是无首尾空白的非空字符串`);
    }
    if (Array.from(value).length > maximum) fail(`${name} 超过 ${maximum} 个字符`);
    return value;
  }

  function ensureSha(value, name) {
    if (typeof value !== 'string' || !SHA256_RE.test(value)) fail(`${name} 必须是 SHA-256`);
    return value;
  }

  function ensureId(value, pattern, name) {
    if (typeof value !== 'string' || !pattern.test(value)) fail(`${name} 无效`);
    return value;
  }

  function ensureInteger(value, name, minimum = null) {
    if (!Number.isSafeInteger(value) || (minimum !== null && value < minimum)) {
      fail(`${name} 必须是有效整数`);
    }
    return value;
  }

  function daysInMonth(year, month) {
    if (month === 2) {
      const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
      return leap ? 29 : 28;
    }
    return [4, 6, 9, 11].includes(month) ? 30 : 31;
  }

  function validDateParts(year, month, day) {
    return year >= 1 && year <= 9999 && month >= 1 && month <= 12
      && day >= 1 && day <= daysInMonth(year, month);
  }

  function ensureDate(value, name) {
    if (typeof value !== 'string') fail(`${name} 必须是 YYYY-MM-DD`);
    const match = DATE_RE.exec(value);
    if (!match || !validDateParts(Number(match[1]), Number(match[2]), Number(match[3]))) {
      fail(`${name} 不是有效日期`);
    }
    return value;
  }

  function ensureDateTime(value, name) {
    ensureText(value, name, 64);
    const match = DATETIME_RE.exec(value);
    if (!match) fail(`${name} 必须是带时区的 ISO-8601 时间`);
    const [, y, mo, d, h, mi, s = '0', , zone, , zh = '0', zm = '0'] = match;
    if (!validDateParts(Number(y), Number(mo), Number(d))
      || Number(h) > 23 || Number(mi) > 59 || Number(s) > 59
      || (zone !== 'Z' && (Number(zh) > 23 || Number(zm) > 59))) {
      fail(`${name} 必须是带时区的有效 ISO-8601 时间`);
    }
    return value;
  }

  function ensureStringList(value, name, allowed = null, maximum = 24) {
    if (!Array.isArray(value) || value.length > maximum) {
      fail(`${name} 必须是最多 ${maximum} 项的 array`);
    }
    const result = value.map((item, index) => ensureText(item, `${name}[${index}]`, 600));
    if (new Set(result).size !== result.length) fail(`${name} 不能重复`);
    if (allowed && result.some(item => !allowed.has(item))) fail(`${name} 含不允许值`);
    return result;
  }

  function ensureUnit(value, name) {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1) {
      fail(`${name} 必须在 0 到 1`);
    }
    return value;
  }

  function cloneJson(value) {
    if (Array.isArray(value)) return value.map(cloneJson);
    if (isObject(value)) {
      const copy = {};
      for (const key of Object.keys(value)) copy[key] = cloneJson(value[key]);
      return copy;
    }
    return value;
  }

  function validateObjectRef(value) {
    const item = exactObject(value, OBJECT_REF_FIELDS, 'object ref');
    const pattern = OBJECT_PATTERNS.get(item.kind);
    if (!pattern) fail('object ref kind 无效');
    ensureId(item.id, pattern, 'object_ref.id');
    ensureInteger(item.revision, 'object_ref.revision', 1);
    ensureSha(item.revision_sha256, 'object_ref.revision_sha256');
    return cloneJson(item);
  }

  function validateReceiptFacets(value) {
    const item = exactObject(value, RECEIPT_FACET_FIELDS, 'receipt facets');
    ensureStringList(item.content_types, 'receipt facets.content_types', CONTENT_TYPES);
    ensureStringList(item.topics, 'receipt facets.topics');
    ensureStringList(item.objects, 'receipt facets.objects');
    ensureStringList(item.purposes, 'receipt facets.purposes', PURPOSES);
    if (!STANCES.has(item.stance) || !COGNITIVE_STATES.has(item.cognitive_state)) {
      fail('receipt facets enum 无效');
    }
    return cloneJson(item);
  }

  function validateCognitiveActionPayload(action, value) {
    if (['confirm_receipt', 'original_only', 'delete_reusable_memory', 'delete_relation'].includes(action)) {
      if (value !== null) fail(`${action} payload 必须是 null`);
      return null;
    }
    if (action === 'edit_receipt') {
      const item = exactObject(value, RECEIPT_EDIT_FIELDS, 'edit_receipt.payload');
      ensureText(item.summary, 'edit_receipt.summary', 600);
      validateReceiptFacets(item.facets);
      return cloneJson(item);
    }
    if (action === 'edit_reusable_memory') {
      const item = exactObject(value, MEMORY_EDIT_FIELDS, 'edit_reusable_memory.payload');
      ensureText(item.statement, 'edit_reusable_memory.statement', 1000);
      ensureStringList(item.topics, 'edit_reusable_memory.topics');
      ensureStringList(item.purposes, 'edit_reusable_memory.purposes', PURPOSES);
      return cloneJson(item);
    }
    if (action === 'edit_relation') {
      const item = exactObject(value, RELATION_EDIT_FIELDS, 'edit_relation.payload');
      if (!RELATION_TYPES.has(item.type)) fail('edit_relation.type 无效');
      ensureText(item.statement, 'edit_relation.statement', 1000);
      return cloneJson(item);
    }
    fail('cognitive action 无效', 'action');
  }

  function validateCognitiveUserAction(value) {
    const item = exactObject(value, COGNITIVE_ACTION_FIELDS, 'cognitive user action');
    if (item.schema_version !== COGNITIVE_SCHEMA_VERSION
        || item.kind !== 'memento_cognitive_user_action') {
      fail('cognitive user action schema/kind 无效');
    }
    ensureId(item.id, COGNITIVE_ACTION_RE, 'cognitive action.id');
    ensureDateTime(item.created_at, 'cognitive action.created_at');
    const target = validateObjectRef(item.target_ref);
    const targetKinds = {
      confirm_receipt: 'interpretation_receipt',
      edit_receipt: 'interpretation_receipt',
      original_only: 'interpretation_receipt',
      edit_reusable_memory: 'reusable_memory',
      delete_reusable_memory: 'reusable_memory',
      edit_relation: 'relation',
      delete_relation: 'relation',
    };
    if (!targetKinds[item.action] || targetKinds[item.action] !== target.kind) {
      fail('cognitive action 与 target kind 不匹配', 'action');
    }
    validateCognitiveActionPayload(item.action, item.payload);
    return cloneJson(item);
  }

  function buildCognitiveUserAction({ id, createdAt, action, targetRef, payload }) {
    return validateCognitiveUserAction({
      schema_version: COGNITIVE_SCHEMA_VERSION,
      kind: 'memento_cognitive_user_action',
      id,
      created_at: createdAt || new Date().toISOString(),
      action,
      target_ref: cloneJson(targetRef),
      payload: payload === null ? null : cloneJson(payload),
    });
  }

  function cognitiveActionFileName(actionId) {
    ensureId(actionId, COGNITIVE_ACTION_RE, 'cognitive action.id');
    return `${actionId}.json`;
  }

  function makeCognitiveActionResultId(actionId) {
    ensureId(actionId, COGNITIVE_ACTION_RE, 'cognitive action.id');
    return `cares_${sha256Hex(canonicalJson({
      namespace: 'cognitive-action-result-v1', action_id: actionId,
    })).slice(0, 24)}`;
  }

  function cognitiveActionResultFileName(actionId) {
    return `${makeCognitiveActionResultId(actionId)}.json`;
  }

  function serializeCognitiveAction(value) {
    const item = validateCognitiveUserAction(value);
    return `${JSON.stringify(item, null, 2)}\n`;
  }

  function validateCognitiveActionResult(value) {
    const item = exactObject(value, COGNITIVE_ACTION_RESULT_FIELDS, 'cognitive action result');
    if (item.schema_version !== COGNITIVE_SCHEMA_VERSION
        || item.kind !== 'memento_cognitive_action_result') {
      fail('cognitive action result schema/kind 无效');
    }
    ensureId(item.action_id, COGNITIVE_ACTION_RE, 'action_result.action_id');
    ensureId(item.id, COGNITIVE_ACTION_RESULT_RE, 'action_result.id');
    if (item.id !== makeCognitiveActionResultId(item.action_id)) {
      fail('cognitive action result id 与 action 不一致');
    }
    ensureSha(item.action_sha256, 'action_result.action_sha256');
    ensureDateTime(item.completed_at, 'action_result.completed_at');
    if (!['applied', 'rejected', 'conflict'].includes(item.status)) {
      fail('cognitive action result status 无效');
    }
    if (!Array.isArray(item.materialized_refs)) fail('action_result.materialized_refs 无效');
    const refs = item.materialized_refs.map(validateObjectRef);
    if (new Set(refs.map(ref => `${ref.kind}:${ref.id}:${ref.revision}:${ref.revision_sha256}`)).size !== refs.length) {
      fail('action_result.materialized_refs 不能重复');
    }
    if (item.status === 'applied') {
      if (item.error_kind !== null) fail('applied action result 不得有 error_kind');
    } else {
      if (refs.length || !['schema', 'action', 'evidence', 'conflict', 'runtime'].includes(item.error_kind)) {
        fail('未应用 action result 无效');
      }
    }
    return cloneJson(item);
  }

  function validateManualDayRequest(value) {
    const item = exactObject(value, MANUAL_DAY_REQUEST_FIELDS, 'manual day request');
    if (item.schema_version !== COGNITIVE_SCHEMA_VERSION
        || item.kind !== 'memento_cognitive_manual_day_request'
        || item.status !== 'pending') {
      fail('manual day request schema/kind/status 无效');
    }
    ensureId(item.id, MANUAL_DAY_REQUEST_RE, 'manual_day_request.id');
    ensureDateTime(item.created_at, 'manual_day_request.created_at');
    ensureDate(item.local_date, 'manual_day_request.local_date');
    if (item.created_at.slice(0, 10) !== item.local_date) {
      fail('manual day request created_at/local_date 不一致');
    }
    return cloneJson(item);
  }

  function localIsoTimestamp(value = new Date()) {
    if (!(value instanceof Date) || Number.isNaN(value.getTime())) {
      fail('manual day request 本地时间无效');
    }
    const pad = number => String(number).padStart(2, '0');
    const offsetMinutes = -value.getTimezoneOffset();
    const sign = offsetMinutes >= 0 ? '+' : '-';
    const absoluteOffset = Math.abs(offsetMinutes);
    return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`
      + `T${pad(value.getHours())}:${pad(value.getMinutes())}:${pad(value.getSeconds())}`
      + `.${String(value.getMilliseconds()).padStart(3, '0')}`
      + `${sign}${pad(Math.floor(absoluteOffset / 60))}:${pad(absoluteOffset % 60)}`;
  }

  function buildManualDayRequest({ id, createdAt, localDate }) {
    // Keep insertion order identical to Python sort_keys output. The browser
    // atomic writer serializes this validated object with indent=2 and a final
    // newline, so the immutable file bytes are deterministic.
    return validateManualDayRequest({
      created_at: createdAt || localIsoTimestamp(),
      id,
      kind: 'memento_cognitive_manual_day_request',
      local_date: localDate,
      schema_version: COGNITIVE_SCHEMA_VERSION,
      status: 'pending',
    });
  }

  function manualDayRequestFileName(requestId) {
    ensureId(requestId, MANUAL_DAY_REQUEST_RE, 'manual_day_request.id');
    return `${requestId}.json`;
  }

  function serializeManualDayRequest(value) {
    const item = validateManualDayRequest(value);
    return `${JSON.stringify(item, null, 2)}\n`;
  }

  function makeManualDayResultId(requestSha256) {
    ensureSha(requestSha256, 'manual_day_result.request_sha256');
    return `cmanr_${sha256Hex(`manual-result:${requestSha256}`).slice(0, 24)}`;
  }

  function validateManualDayResult(value) {
    const item = exactObject(value, MANUAL_DAY_RESULT_FIELDS, 'manual day result');
    if (item.schema_version !== COGNITIVE_SCHEMA_VERSION
        || item.kind !== 'memento_cognitive_manual_day_result') {
      fail('manual day result schema/kind 无效');
    }
    ensureId(item.id, MANUAL_DAY_RESULT_RE, 'manual_day_result.id');
    ensureId(item.request_id, MANUAL_DAY_REQUEST_RE, 'manual_day_result.request_id');
    ensureSha(item.request_sha256, 'manual_day_result.request_sha256');
    if (item.id !== makeManualDayResultId(item.request_sha256)) {
      fail('manual day result id 与 request_sha256 不一致');
    }
    ensureDateTime(item.completed_at, 'manual_day_result.completed_at');
    ensureDate(item.local_date, 'manual_day_result.local_date');
    const resultStatuses = new Set([
      'completed', 'master_gate_disabled', 'rejected_date', 'runner_failed',
    ]);
    const runnerStatuses = new Set([
      'completed', 'committed', 'committed_with_warnings', 'no_change',
      'no_candidate', 'no_records', 'no_receipts', 'stale', 'error',
      'budget_exhausted',
    ]);
    if (!resultStatuses.has(item.status)) fail('manual day result status 无效');
    if (item.status === 'completed') {
      if (!runnerStatuses.has(item.runner_status)) fail('manual day runner_status 无效');
    } else if (item.runner_status !== null) {
      fail('未完成 manual day result 的 runner_status 必须为 null');
    }
    const allowedErrors = {
      completed: new Set([null]),
      master_gate_disabled: new Set([null]),
      rejected_date: new Set(['date']),
      runner_failed: new Set(['contract', 'runtime']),
    };
    if (!allowedErrors[item.status].has(item.error_kind)) {
      fail('manual day result status/error_kind 不一致');
    }
    return cloneJson(item);
  }

  function validateRefList(value, kind, name, requireSorted = true) {
    if (!Array.isArray(value)) fail(`${name} 必须是 array`);
    const refs = value.map(validateObjectRef);
    if (refs.some(ref => ref.kind !== kind)) fail(`${name} kind 无效`);
    const ids = refs.map(ref => ref.id);
    if (ids.length !== new Set(ids).size
      || (requireSorted && ids.some((id, index) => index > 0 && ids[index - 1] > id))) {
      fail(`${name} 必须唯一${requireSorted ? '且按 id 排序' : ''}`);
    }
    return refs;
  }

  function validateFormalHeadIndex(value) {
    const item = exactObject(value, FORMAL_CATALOG_FIELDS, 'formal head index');
    if (item.schema_version !== COGNITIVE_SCHEMA_VERSION
      || item.kind !== 'memento_cognitive_formal_head_index') {
      fail('formal head index schema/kind 无效');
    }
    ensureInteger(item.revision, 'formal head index.revision', 0);
    ensureDateTime(item.generated_at, 'formal head index.generated_at');
    validateRefList(item.daily_bundles, 'daily_bundle', 'daily_bundles');
    validateRefList(item.daily_summaries, 'daily_summary', 'daily_summaries');
    validateRefList(item.reusable_memories, 'reusable_memory', 'reusable_memories');
    validateRefList(item.relations, 'relation', 'relations');
    return cloneJson(item);
  }

  // Synchronous SHA-256 for exact IDs and already-read local file bytes. The
  // implementation is intentionally dependency-free so the extension remains
  // fully offline and the same function works in Node contract tests.
  function sha256Hex(input) {
    let bytes;
    if (typeof input === 'string') bytes = new TextEncoder().encode(input);
    else if (input instanceof Uint8Array) bytes = input;
    else if (input instanceof ArrayBuffer) bytes = new Uint8Array(input);
    else if (ArrayBuffer.isView(input)) bytes = new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
    else fail('sha256 input 必须是 string 或 bytes');

    const bitLength = bytes.length * 8;
    const paddedLength = Math.ceil((bytes.length + 9) / 64) * 64;
    const padded = new Uint8Array(paddedLength);
    padded.set(bytes);
    padded[bytes.length] = 0x80;
    const view = new DataView(padded.buffer);
    const high = Math.floor(bitLength / 0x100000000);
    const low = bitLength >>> 0;
    view.setUint32(paddedLength - 8, high, false);
    view.setUint32(paddedLength - 4, low, false);

    const k = [
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
      0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
      0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
      0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
      0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
      0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
      0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ];
    let h0 = 0x6a09e667;
    let h1 = 0xbb67ae85;
    let h2 = 0x3c6ef372;
    let h3 = 0xa54ff53a;
    let h4 = 0x510e527f;
    let h5 = 0x9b05688c;
    let h6 = 0x1f83d9ab;
    let h7 = 0x5be0cd19;
    const w = new Uint32Array(64);
    const rotr = (value, amount) => (value >>> amount) | (value << (32 - amount));

    for (let offset = 0; offset < padded.length; offset += 64) {
      for (let index = 0; index < 16; index += 1) w[index] = view.getUint32(offset + index * 4, false);
      for (let index = 16; index < 64; index += 1) {
        const s0 = rotr(w[index - 15], 7) ^ rotr(w[index - 15], 18) ^ (w[index - 15] >>> 3);
        const s1 = rotr(w[index - 2], 17) ^ rotr(w[index - 2], 19) ^ (w[index - 2] >>> 10);
        w[index] = (w[index - 16] + s0 + w[index - 7] + s1) >>> 0;
      }
      let a = h0;
      let b = h1;
      let c = h2;
      let d = h3;
      let e = h4;
      let f = h5;
      let g = h6;
      let h = h7;
      for (let index = 0; index < 64; index += 1) {
        const s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        const ch = (e & f) ^ (~e & g);
        const t1 = (h + s1 + ch + k[index] + w[index]) >>> 0;
        const s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (s0 + maj) >>> 0;
        h = g;
        g = f;
        f = e;
        e = (d + t1) >>> 0;
        d = c;
        c = b;
        b = a;
        a = (t1 + t2) >>> 0;
      }
      h0 = (h0 + a) >>> 0;
      h1 = (h1 + b) >>> 0;
      h2 = (h2 + c) >>> 0;
      h3 = (h3 + d) >>> 0;
      h4 = (h4 + e) >>> 0;
      h5 = (h5 + f) >>> 0;
      h6 = (h6 + g) >>> 0;
      h7 = (h7 + h) >>> 0;
    }
    return [h0, h1, h2, h3, h4, h5, h6, h7]
      .map(value => value.toString(16).padStart(8, '0')).join('');
  }

  function canonicalJson(value) {
    if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
    if (isObject(value)) {
      return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
    }
    return JSON.stringify(value);
  }

  function makeReceiptId(recordId) {
    ensureId(recordId, RECORD_RE, 'record_id');
    return `rcp_${sha256Hex(canonicalJson({ namespace: 'receipt-v1', record_id: recordId })).slice(0, 24)}`;
  }

  function makePeakId(understandingId) {
    ensureId(understandingId, UNDERSTANDING_RE, 'understanding_id');
    return `peak_${understandingId.slice(4)}`;
  }

  function validateLandscapeSnapshot(value) {
    const item = exactObject(value, LANDSCAPE_FIELDS, 'landscape');
    if (item.schema_version !== COGNITIVE_SCHEMA_VERSION
      || item.kind !== 'memento_landscape_snapshot'
      || item.projection_version !== LANDSCAPE_PROJECTION_VERSION) {
      fail('landscape schema/kind/version 无效');
    }
    ensureId(item.snapshot_id, LANDSCAPE_RE, 'snapshot_id');
    ensureDateTime(item.created_at, 'created_at');
    ensureDate(item.as_of, 'as_of');

    const hashes = exactObject(item.input_hashes, LANDSCAPE_INPUT_FIELDS, 'landscape.input_hashes');
    for (const key of LANDSCAPE_INPUT_FIELDS) ensureSha(hashes[key], key);
    const summary = exactObject(item.summary, LANDSCAPE_SUMMARY_FIELDS, 'landscape.summary');
    for (const key of LANDSCAPE_SUMMARY_FIELDS) ensureInteger(summary[key], `landscape.summary.${key}`, 0);

    const terrain = exactObject(item.terrain, TERRAIN_FIELDS, 'landscape.terrain');
    if (terrain.algorithm_version !== 'stable-anchor-kde-v1'
      || terrain.coordinate_space !== 'normalized_0_1') fail('terrain 无效');
    ensureInteger(terrain.grid_size, 'terrain.grid_size');
    ensureInteger(terrain.contour_levels, 'terrain.contour_levels');
    if (!Array.isArray(item.peaks) || !Array.isArray(item.nodes) || !Array.isArray(item.edges)) {
      fail('landscape lists 无效');
    }

    const understandingIds = new Set();
    const memoryIds = new Set();
    const relationIds = new Set();
    for (const raw of item.peaks) {
      const peak = exactObject(raw, PEAK_FIELDS, 'peak');
      const ref = validateObjectRef(peak.understanding_ref);
      if (ref.kind !== 'understanding' || peak.peak_id !== makePeakId(ref.id)) {
        fail('peak 只能来自 current active Agent V1 understanding');
      }
      ensureId(peak.peak_id, PEAK_RE, 'peak_id');
      ensureUnit(peak.x, 'peak.x');
      ensureUnit(peak.y, 'peak.y');
      ensureUnit(peak.elevation, 'peak.elevation');
      ensureInteger(peak.evidence_count, 'peak.evidence_count', 0);
      ensureInteger(peak.counterevidence_count, 'peak.counterevidence_count', 0);
      if (typeof peak.recent_change !== 'boolean' || !PEAK_LIFECYCLES.has(peak.lifecycle)) {
        fail('peak 字段无效');
      }
      if (understandingIds.has(ref.id)) fail('peak understanding 不得重复');
      understandingIds.add(ref.id);
    }
    for (const raw of item.nodes) {
      const node = exactObject(raw, NODE_FIELDS, 'node');
      const ref = validateObjectRef(node.memory_ref);
      if (ref.kind !== 'reusable_memory' || node.state !== 'committed'
        || typeof node.recent !== 'boolean') fail('node 必须是 committed reusable memory');
      ensureUnit(node.x, 'node.x');
      ensureUnit(node.y, 'node.y');
      if (memoryIds.has(ref.id)) fail('node memory 不得重复');
      memoryIds.add(ref.id);
    }
    const endpointIds = new Set([...understandingIds, ...memoryIds]);
    for (const raw of item.edges) {
      const edge = exactObject(raw, EDGE_FIELDS, 'edge');
      const ref = validateObjectRef(edge.relation_ref);
      if (ref.kind !== 'relation' || !RELATION_TYPES.has(edge.type)
        || edge.from_id === edge.to_id || !endpointIds.has(edge.from_id)
        || !endpointIds.has(edge.to_id)) fail('edge 必须绑定当前正式图谱');
      if (relationIds.has(ref.id)) fail('edge relation 不得重复');
      relationIds.add(ref.id);
    }
    if (summary.active_understandings !== understandingIds.size) {
      fail('summary.active_understandings 与 peaks 不一致');
    }
    if (summary.recent_changes !== item.peaks.filter(peak => peak.recent_change).length) {
      fail('summary.recent_changes 与 peaks 不一致');
    }
    if (item.previous_snapshot_sha256 !== null) {
      ensureSha(item.previous_snapshot_sha256, 'previous_snapshot_sha256');
    }
    return cloneJson(item);
  }

  function validateHomeProjection(value) {
    const item = exactObject(value, HOME_FIELDS, 'home projection');
    if (item.schema_version !== COGNITIVE_SCHEMA_VERSION
      || item.kind !== 'memento_home_projection'
      || item.projection_version !== HOME_PROJECTION_VERSION) {
      fail('home projection schema/kind/version 无效');
    }
    ensureDateTime(item.generated_at, 'generated_at');
    ensureDate(item.local_date, 'local_date');
    const hashes = exactObject(item.input_hashes, HOME_INPUT_FIELDS, 'home input_hashes');
    for (const key of HOME_INPUT_FIELDS) ensureSha(hashes[key], key);
    const landscapeRef = exactObject(item.landscape_ref, LANDSCAPE_REF_FIELDS, 'landscape_ref');
    ensureId(landscapeRef.snapshot_id, LANDSCAPE_RE, 'snapshot_id');
    ensureSha(landscapeRef.snapshot_sha256, 'snapshot_sha256');
    const summary = exactObject(item.landscape_summary, LANDSCAPE_SUMMARY_FIELDS, 'landscape_summary');
    for (const key of LANDSCAPE_SUMMARY_FIELDS) ensureInteger(summary[key], `landscape_summary.${key}`, 0);
    const today = exactObject(item.today_status, TODAY_STATUS_FIELDS, 'today_status');
    for (const key of ['saved', 'interpreted', 'merged', 'needs_review']) {
      ensureInteger(today[key], `today_status.${key}`, 0);
    }
    if (!DAILY_RUN_STATUSES.has(today.daily_run_status)) fail('today_status 无效');
    if (!Array.isArray(item.records)) fail('records 必须是 array');

    const seen = new Set();
    for (const raw of item.records) {
      const record = exactObject(raw, HOME_RECORD_FIELDS, 'home record');
      const source = validateObjectRef(record.record_ref);
      if (source.kind !== 'source_record') fail('home record source ref 无效');
      if (seen.has(source.id)) fail('home record 重复');
      seen.add(source.id);
      ensureDateTime(record.captured_at, 'captured_at');
      if (!SOURCE_TYPES.has(record.source_type) || !HOME_RECORD_STATUSES.has(record.status)) {
        fail('home record 状态无效');
      }
      ensureText(record.source_app, 'home record.source_app', 600);
      if (record.receipt_ref === null) {
        if (!new Set(['raw_saved', 'processing', 'no_candidate', 'failed']).has(record.status)) {
          fail('已整理 home record 必须绑定 receipt');
        }
      } else {
        const receipt = validateObjectRef(record.receipt_ref);
        if (receipt.kind !== 'interpretation_receipt' || receipt.id !== makeReceiptId(source.id)) {
          fail('home record receipt ref 无效');
        }
        if (record.status === 'raw_saved' || record.status === 'processing'
          || record.status === 'no_candidate') {
          fail('无回执 home record 不得绑定 receipt');
        }
      }
      if (record.summary !== null) ensureText(record.summary, 'home record.summary', 600);
      ensureStringList(record.content_types, 'content_types', CONTENT_TYPES);
      ensureStringList(record.topics, 'topics');
      ensureStringList(record.purposes, 'purposes', PURPOSES);
      if (!Array.isArray(record.memory_refs) || !Array.isArray(record.understanding_refs)) {
        fail('home record downstream refs 无效');
      }
      for (const ref of record.memory_refs) {
        if (validateObjectRef(ref).kind !== 'reusable_memory') fail('home record downstream refs 无效');
      }
      for (const ref of record.understanding_refs) {
        if (validateObjectRef(ref).kind !== 'understanding') fail('home record downstream refs 无效');
      }
      if (new Set(['original_only', 'no_candidate']).has(record.status)
          && (record.summary !== null
            || record.content_types.length
            || record.topics.length
            || record.purposes.length
            || record.memory_refs.length
            || record.understanding_refs.length)) {
        fail(`${record.status} 不得携带 AI 整理内容或下游引用`);
      }
    }
    const schedule = exactObject(item.schedule, SCHEDULE_FIELDS, 'schedule');
    if (typeof schedule.enabled !== 'boolean') fail('schedule 无效');
    ensureInteger(schedule.hour, 'schedule.hour');
    ensureInteger(schedule.minute, 'schedule.minute');
    if (schedule.hour < 0 || schedule.hour > 23 || schedule.minute < 0 || schedule.minute > 59) {
      fail('schedule 无效');
    }
    ensureDateTime(schedule.next_due_at, 'next_due_at');
    if (!SCHEDULE_RUN_STATUSES.has(schedule.last_run_status)) fail('schedule status 无效');
    ensureStringList(item.warnings, 'warnings', null, 12);
    return cloneJson(item);
  }

  function sameObjectRef(left, right) {
    return left.kind === right.kind && left.id === right.id
      && left.revision === right.revision
      && left.revision_sha256 === right.revision_sha256;
  }

  function refKey(ref) {
    return `${ref.kind}\u0000${ref.id}\u0000${ref.revision}\u0000${ref.revision_sha256}`;
  }

  function sameSummary(left, right) {
    return [...LANDSCAPE_SUMMARY_FIELDS].every(key => left[key] === right[key]);
  }

  function validateProjectionPair(homeValue, landscapeValue, landscapeSha256) {
    const home = validateHomeProjection(homeValue);
    const landscape = validateLandscapeSnapshot(landscapeValue);
    ensureSha(landscapeSha256, 'landscapeSha256');
    if (home.landscape_ref.snapshot_id !== landscape.snapshot_id) {
      fail('home 与 landscape snapshot_id 不一致', 'stale');
    }
    if (home.landscape_ref.snapshot_sha256 !== landscapeSha256
      || home.input_hashes.landscape_snapshot_sha256 !== landscapeSha256
      || home.landscape_ref.snapshot_sha256 !== home.input_hashes.landscape_snapshot_sha256) {
      fail('home 与 landscape 文件校验不一致', 'stale');
    }
    if (home.local_date !== landscape.as_of) fail('home 与 landscape 日期不一致', 'stale');
    if (!sameSummary(home.landscape_summary, landscape.summary)) {
      fail('home 与 landscape summary 不一致', 'stale');
    }
    for (const key of ['agent_profile_sha256', 'user_action_watermark_sha256']) {
      if (home.input_hashes[key] !== landscape.input_hashes[key]) {
        fail(`home 与 landscape ${key} 不一致`, 'stale');
      }
    }

    const nodeRefs = new Map(landscape.nodes.map(node => [node.memory_ref.id, node.memory_ref]));
    const peakRefs = new Map(landscape.peaks.map(peak => [peak.understanding_ref.id, peak.understanding_ref]));
    const expectedToday = {
      saved: home.records.length,
      interpreted: home.records.filter(record => (
        record.receipt_ref !== null || record.status === 'no_candidate'
      )).length,
      merged: home.records.filter(record => record.status === 'merged').length,
      needs_review: home.records.filter(record => record.status === 'needs_review').length,
    };
    for (const [key, value] of Object.entries(expectedToday)) {
      if (home.today_status[key] !== value) {
        fail(`home today_status.${key} 与 records 不一致`, 'stale');
      }
    }
    for (const record of home.records) {
      for (const ref of record.memory_refs) {
        const current = nodeRefs.get(ref.id);
        if (!current || !sameObjectRef(ref, current)) {
          fail('home record memory_ref 未绑定当前 landscape node', 'stale');
        }
      }
      for (const ref of record.understanding_refs) {
        const current = peakRefs.get(ref.id);
        if (!current || !sameObjectRef(ref, current)) {
          fail('home record understanding_ref 未绑定当前 landscape peak', 'stale');
        }
      }
    }
    // Force exact current ref identity even when future code changes map lookup
    // behavior from id-only to compound keys.
    const nodeRefKeys = new Set(landscape.nodes.map(node => refKey(node.memory_ref)));
    const peakRefKeys = new Set(landscape.peaks.map(peak => refKey(peak.understanding_ref)));
    if (home.records.some(record => record.memory_refs.some(ref => !nodeRefKeys.has(refKey(ref)))
      || record.understanding_refs.some(ref => !peakRefKeys.has(refKey(ref))))) {
      fail('home record downstream ref 与 landscape revision 不一致', 'stale');
    }
    return { home, landscape };
  }

  function sameRefSet(left, right) {
    if (left.length !== right.length) return false;
    const expected = new Set(left.map(refKey));
    return right.every(ref => expected.has(refKey(ref)));
  }

  function validateProjectionAuthority(homeValue, landscapeValue, authorityValue) {
    const home = validateHomeProjection(homeValue);
    const landscape = validateLandscapeSnapshot(landscapeValue);
    const authority = exactObject(authorityValue, PROJECTION_AUTHORITY_FIELDS, 'projection authority');
    ensureSha(authority.agent_profile_sha256, 'authority.agent_profile_sha256');
    ensureSha(authority.user_action_watermark_sha256, 'authority.user_action_watermark_sha256');
    ensureSha(authority.daily_bundle_head_sha256, 'authority.daily_bundle_head_sha256');
    const understandings = validateRefList(
      authority.active_understanding_refs, 'understanding',
      'authority.active_understanding_refs', false
    );
    const memories = validateRefList(
      authority.current_memory_refs, 'reusable_memory', 'authority.current_memory_refs'
    );
    const relations = validateRefList(
      authority.current_relation_refs, 'relation', 'authority.current_relation_refs'
    );
    const records = validateRefList(
      authority.today_record_refs, 'source_record', 'authority.today_record_refs', false
    );
    const receipts = validateRefList(
      authority.today_receipt_refs, 'interpretation_receipt', 'authority.today_receipt_refs'
    );

    if (home.input_hashes.agent_profile_sha256 !== authority.agent_profile_sha256
      || landscape.input_hashes.agent_profile_sha256 !== authority.agent_profile_sha256) {
      fail('projection 未绑定当前 Agent profile', 'stale');
    }
    if (home.input_hashes.user_action_watermark_sha256 !== authority.user_action_watermark_sha256
      || landscape.input_hashes.user_action_watermark_sha256 !== authority.user_action_watermark_sha256) {
      fail('projection 未绑定当前 user action watermark', 'stale');
    }
    if (home.input_hashes.daily_bundle_head_sha256 !== authority.daily_bundle_head_sha256) {
      fail('home 未绑定当前 daily bundle head', 'stale');
    }

    const projectedUnderstandings = landscape.peaks.map(peak => peak.understanding_ref);
    if (!sameRefSet(understandings, projectedUnderstandings)) {
      fail('landscape peaks 未完整绑定当前 active understandings', 'stale');
    }
    const projectedMemories = landscape.nodes.map(node => node.memory_ref);
    const projectedRelations = landscape.edges.map(edge => edge.relation_ref);
    if (!sameRefSet(memories, projectedMemories)
      || landscape.input_hashes.reusable_memory_head_sha256
        !== sha256Hex(canonicalJson([...memories].sort((a, b) => a.id.localeCompare(b.id))))) {
      fail('landscape 未绑定当前 reusable memory heads', 'stale');
    }
    if (!sameRefSet(relations, projectedRelations)
      || landscape.input_hashes.relation_head_sha256
        !== sha256Hex(canonicalJson([...relations].sort((a, b) => a.id.localeCompare(b.id))))) {
      fail('landscape 未绑定当前 relation heads', 'stale');
    }

    const projectedRecords = home.records.map(record => record.record_ref);
    const projectedReceipts = home.records
      .map(record => record.receipt_ref)
      .filter(Boolean)
      .sort((a, b) => a.id.localeCompare(b.id));
    if (records.length !== projectedRecords.length
      || records.some((ref, index) => !sameObjectRef(ref, projectedRecords[index]))
      || home.input_hashes.record_head_sha256 !== sha256Hex(canonicalJson(records))) {
      fail('home 未绑定当前 record heads', 'stale');
    }
    if (!sameRefSet(receipts, projectedReceipts)
      || home.input_hashes.receipt_head_sha256 !== sha256Hex(canonicalJson(projectedReceipts))) {
      fail('home 未绑定当前 receipt heads', 'stale');
    }
    return { home, landscape, authority: cloneJson(authority) };
  }

  function normalizeHomeProjection(value) {
    try { return validateHomeProjection(value); } catch { return null; }
  }

  function normalizeLandscapeSnapshot(value) {
    try { return validateLandscapeSnapshot(value); } catch { return null; }
  }

  function normalizeProjectionPair(home, landscape, landscapeSha256) {
    try { return validateProjectionPair(home, landscape, landscapeSha256); } catch { return null; }
  }

  function seedNumber(seed) {
    const text = String(seed);
    let hash = 0x811c9dc5;
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return hash / 0xffffffff;
  }

  function organicContourPath(cx, cy, rx, ry, seed, level = 0) {
    for (const [value, name] of [[cx, 'cx'], [cy, 'cy'], [rx, 'rx'], [ry, 'ry']]) {
      if (typeof value !== 'number' || !Number.isFinite(value)) fail(`${name} 必须是有限数字`);
    }
    if (rx <= 0 || ry <= 0 || !Number.isSafeInteger(level) || level < 0) {
      fail('organic contour 尺寸或层级无效');
    }
    const count = 16;
    const contraction = Math.max(0.14, 1 - level * 0.065);
    const phase = seedNumber(seed) * Math.PI * 2;
    const points = [];
    for (let index = 0; index < count; index += 1) {
      const angle = (index / count) * Math.PI * 2;
      const ripple = 1
        + 0.055 * Math.sin(angle * 3 + phase)
        + 0.025 * Math.sin(angle * 5 - phase * 0.7);
      points.push({
        x: cx + Math.cos(angle) * rx * contraction * ripple,
        y: cy + Math.sin(angle) * ry * contraction * (2 - ripple),
      });
    }
    const fmt = value => Number(value.toFixed(2));
    let path = `M ${fmt(points[0].x)} ${fmt(points[0].y)}`;
    for (let index = 0; index < count; index += 1) {
      const p0 = points[(index - 1 + count) % count];
      const p1 = points[index];
      const p2 = points[(index + 1) % count];
      const p3 = points[(index + 2) % count];
      const c1x = p1.x + (p2.x - p0.x) / 6;
      const c1y = p1.y + (p2.y - p0.y) / 6;
      const c2x = p2.x - (p3.x - p1.x) / 6;
      const c2y = p2.y - (p3.y - p1.y) / 6;
      path += ` C ${fmt(c1x)} ${fmt(c1y)} ${fmt(c2x)} ${fmt(c2y)} ${fmt(p2.x)} ${fmt(p2.y)}`;
    }
    return `${path} Z`;
  }

  return Object.freeze({
    COGNITIVE_SCHEMA_VERSION,
    HOME_PROJECTION_VERSION,
    LANDSCAPE_PROJECTION_VERSION,
    STATUS_LABELS,
    RELATION_LABELS,
    CognitiveContractError,
    sha256Hex,
    canonicalJson,
    makeReceiptId,
    makePeakId,
    validateObjectRef,
    validateReceiptFacets,
    validateCognitiveActionPayload,
    validateCognitiveUserAction,
    buildCognitiveUserAction,
    cognitiveActionFileName,
    makeCognitiveActionResultId,
    cognitiveActionResultFileName,
    serializeCognitiveAction,
    validateCognitiveActionResult,
    validateManualDayRequest,
    buildManualDayRequest,
    manualDayRequestFileName,
    serializeManualDayRequest,
    makeManualDayResultId,
    validateManualDayResult,
    validateFormalHeadIndex,
    validateHomeProjection,
    validateLandscapeSnapshot,
    validateProjectionPair,
    validateProjectionAuthority,
    normalizeHomeProjection,
    normalizeLandscapeSnapshot,
    normalizeProjectionPair,
    organicContourPath,
  });
});
