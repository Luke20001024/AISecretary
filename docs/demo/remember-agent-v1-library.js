// Memento · Re:member Agent V1 browser-side data contract.
// Pure validation/projection only: no provider connection, API key, or model call.

(function exposeRememberAgentV1(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MementoRememberAgentV1 = api;
})(typeof window !== 'undefined' ? window : globalThis, function createRememberAgentV1() {
  'use strict';

  const SCHEMA_VERSION = '1.0';
  const PROFILE_VERSION = 'remember-agent-profile-v1.0';
  const LEGACY_PROMPT_VERSION = 'remember-agent-v1.9';
  const AGENTIC_WORKFLOW_PROMPT_VERSION = 'remember-agent-v1.22';
  const AGENTIC_WORKFLOW_POLICY_VERSION = 'agentic-workflow-investigation-v1.13';
  const AGENTIC_WORKFLOW_INSTRUCTION_SHA256 = 'f22de6ec40b800bdf2781e91a71f63e962e19adc046bcbbfe696e1ffcca1c6f3';
  const HISTORICAL_AGENTIC_WORKFLOW_V20 = Object.freeze({
    promptVersion: 'remember-agent-v1.20',
    policyVersion: 'agentic-workflow-investigation-v1.11',
    instructionSha256: '12ac2e0aa3ee52f7bb1cac6a3d1c8bd075b27872f73c64e95e90af6db6ab5afb',
  });
  const HISTORICAL_AGENTIC_WORKFLOW_V19 = Object.freeze({
    promptVersion: 'remember-agent-v1.19',
    policyVersion: 'agentic-workflow-investigation-v1.9',
    instructionSha256: '4bcf3ab21b0d92adcab983d4629c1dedda722adea18ca34e12601aaab0d02fa8',
  });
  const PERSON_PROFILE_CANDIDATE_POLICY_VERSION = 'person-profile-candidate-v1.0';
  const PERSON_PROFILE_CANDIDATE_INSTRUCTION_SHA256 = '25dce1b7dbe64984c041a4ad01bbeffbb803b9f25024348d00da26db443b06d5';
  const AGENTIC_WORKFLOW_PROVIDERS = new Set([
    'deepseek-agentic-workflow', 'mock-agentic-workflow',
  ]);
  const POST_CALL_TOKEN_BUDGET_POLICY_VERSION = 'post-call-token-budget-v1.0';
  const CONFLICT_INVESTIGATION_POLICY_VERSION = 'conflict-investigation-v1.0';
  const CONFLICT_INVESTIGATION_INSTRUCTION_SHA256 = 'f09ddb454465d78229d4f003fe8a8f2f692c0a60fc40d1886bb06218a298f815';
  const BOUNDED_FINISH_POLICY_VERSION = 'bounded-finish-investigation-v1.1';
  const BOUNDED_FINISH_INSTRUCTION_SHA256 = '4690dc2624b06749b1ca37fefcf0f4de62a6dedca0d0518f2bf8ddd5e79074ed';
  const BOUNDED_FINISH_MAX_CANDIDATE_MEMORY_IDS = 8;
  const POST_READ_FINISH_POLICY_VERSION = 'post-read-finish-investigation-v1.0';
  const POST_READ_FINISH_INSTRUCTION_SHA256 = '23a1f40d1b34aa7d4c44efa041b6e10f5bdaea4ab0023b9add7f904e7c3e638a';
  const STABLE_NEW_IDENTITY_POLICY_VERSION = 'stable-new-identity-v1.1';
  const STABLE_NEW_IDENTITY_INSTRUCTION_SHA256 = '2e1f9de50c1a34a7262880b12fd699884a1fc30ad86873939acc44b12656003c';
  const HISTORICAL_STABLE_NEW_IDENTITY_POLICY_VERSION = 'stable-new-identity-v1.0';
  const HISTORICAL_STABLE_NEW_IDENTITY_INSTRUCTION_SHA256 = '935c1631ade077bc2708b547b09655439bb6993d2a03961338a9bd2f25d6def8';
  const STABLE_NEW_TERMINAL_GATE_POLICY_VERSION = 'stable-new-terminal-gate-v1.0';
  const STABLE_NEW_TERMINAL_GATE_INSTRUCTION_SHA256 = '170125a1d7c96bd7a066297c3024285d9729884695d59f94b707d79d9ead6f70';
  const STABLE_NEW_SCOPE_RULES = [
    { canonical: 'Memento Context Agent', triggers: ['Memento Context Agent', 'Context Agent', '长期 Context', 'Context Pack'] },
    { canonical: '产品方案评审', triggers: ['产品方案评审', '方案评审', '评审方案'] },
    { canonical: '产品优先级', triggers: ['产品优先级', '优先级决定', '优先级修订'] },
    { canonical: '产品决策', triggers: ['产品决策'] },
    { canonical: '产品规划', triggers: ['产品规划'] },
    { canonical: '产品设计', triggers: ['产品设计'] },
    { canonical: '需求分析', triggers: ['需求分析'] },
    { canonical: '用户研究', triggers: ['用户研究'] },
    { canonical: '用户体验', triggers: ['用户体验'] },
    { canonical: '指标设计', triggers: ['指标设计'] },
    { canonical: '数据分析', triggers: ['数据分析'] },
    { canonical: '交互设计', triggers: ['交互设计'] },
    { canonical: '研发协作', triggers: ['研发协作'] },
    { canonical: '团队协作', triggers: ['团队协作'] },
    { canonical: '项目规划', triggers: ['项目规划'] },
    { canonical: '项目复盘', triggers: ['项目复盘'] },
    { canonical: '工作复盘', triggers: ['工作复盘'] },
    { canonical: '职业发展', triggers: ['职业发展'] },
    { canonical: '求职准备', triggers: ['求职准备'] },
    { canonical: '内容创作', triggers: ['内容创作'] },
    { canonical: '时间管理', triggers: ['时间管理'] },
    { canonical: '日程安排', triggers: ['日程安排'] },
    { canonical: '旅行规划', triggers: ['旅行规划'] },
    { canonical: '个人项目', triggers: ['个人项目'] },
    { canonical: 'Agent Review', triggers: ['Agent Review'] },
    { canonical: '写作', triggers: ['写作习惯', '写作流程'] },
    { canonical: '阅读', triggers: ['阅读习惯', '阅读计划'] },
    { canonical: '学习', triggers: ['学习方式', '学习计划'] },
  ];
  const STABLE_NEW_QUOTE_BLOCK_PATTERN_TEXTS = [
    'MEMENTO_SYNTHETIC|合成测试数据|不代表真实用户',
    '不自动代表|不作为长期|不据此|需要更多证据',
    '提示注入|不得形成\\s*Context|不是产品决定',
    '忽略.{0,40}(?:规则|指令|系统|安全)',
    '(?:system|developer|assistant).{0,40}(?:prompt|message|指令)',
    '请.{0,40}(?:输出|调用|读取|泄露|执行)',
  ];
  const STABLE_NEW_SCOPE_EXCLUSION_TEMPLATES = [
    '(?:与|和|跟|同)\\s*{trigger}\\s*(?:并)?(?:无关|不相关|没关系|没有关系)',
    '(?:并)?(?:不是|不涉及)\\s*(?:(?:关于|一项|一次|一个|一种|该|本次|任何)\\s*){0,3}{trigger}(?:的)?(?:范围|领域|主题|结论|讨论)?',
    '(?:不属于|不应归入|不能归入|不要归为|并非属于)\\s*(?:该|本次|任何)?\\s*{trigger}(?:的)?(?:范围|领域|主题|结论)?',
    '(?:unrelated|not related)\\s+to\\s+{trigger}',
    'does\\s+not\\s+belong\\s+to\\s+{trigger}',
    '(?:is|are|was|were)\\s+not\\s+(?:about|related\\s+to)\\s+{trigger}',
    'does\\s+not\\s+involve\\s+{trigger}',
  ];
  const STABLE_NEW_DIRECT_SELF_PATTERN_TEXTS = [
    '我.{0,32}(?:通常|一般|习惯|倾向|偏好|坚持|优先|总是|往往|常常|会)',
    '(?:通常|一般|习惯上|一般而言).{0,24}我',
  ];
  const STABLE_NEW_TEMPORAL_OR_REPORTED_PATTERN_TEXTS = [
    '(?:曾经|以前|过去|当时|那次|昨天|今天|明天|本周|这周|本月|这个月)',
    '(?:这一次|本次|临时|暂时|一次性|仅这次|只在这次)',
    '(?:假设|假如|如果|比如|例如|示例|模板|转述|据说|他(?:说|认为)|她(?:说|认为))',
  ];
  const REQUEST_ID_RE = /^arq_[0-9a-f]{24}$/;
  const RUN_ID_RE = /^arun_[0-9a-f]{24}$/;
  const RUN_KEY_RE = /^ark_[0-9a-f]{24}$/;
  const MEMORY_ID_RE = /^mem_[0-9a-f]{24}$/;
  const USER_ACTION_ID_RE = /^uact_[0-9a-f]{24}$/;
  const MEMORY_REVISION_FILE_RE = /^(mem_[0-9a-f]{24})\.r([0-9]{6})$/;
  const AGENT_ENABLE_GATE_BYTES = [
    101, 110, 97, 98, 108, 101, 100, 45, 118, 49, 10,
  ];
  const PROFILE_TAG_ID_RE = /^ptag_[0-9a-f]{24}$/;
  const SHA256_RE = /^[0-9a-f]{64}$/;
  const LOCAL_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
  const DAILY_FILE_RE = /^\d{4}-\d{2}-\d{2}\.md$/;
  const ISO_DATETIME_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?(?:Z|[+-](\d{2}):(\d{2}))$/;

  const REQUEST_FIELDS = new Set([
    'schema_version', 'id', 'kind', 'status', 'created_at', 'trigger', 'as_of', 'window_days',
  ]);
  const SCHEDULE_FIELDS = new Set([
    'schema_version', 'kind', 'enabled', 'cadence', 'hour', 'minute', 'updated_at',
  ]);
  const USER_ACTION_FIELDS = new Set([
    'schema_version', 'id', 'kind', 'created_at', 'action', 'memory_id',
    'base_revision', 'base_revision_sha256', 'statement', 'scope',
  ]);
  const MEMORY_REVISION_FIELDS = new Set([
    'schema_version', 'kind', 'memory_id', 'revision', 'status', 'created_at',
    'run_id', 'request_id', 'operation', 'previous_revision_sha256',
    'base_profile_ref', 'user_action_id', 'title', 'statement', 'scope',
    'insight_kind', 'uncertainty', 'evidence', 'counterevidence', 'source_hashes',
  ]);
  const PROFILE_FIELDS = new Set([
    'schema_version', 'kind', 'projection_version', 'projection_updated_at',
    'profile_sha256', 'memories', 'latest_run', 'stats',
  ]);
  const MEMORY_FIELDS = new Set([
    'memory_id', 'revision', 'revision_sha256', 'status', 'title', 'statement',
    'scope', 'insight_kind', 'uncertainty', 'evidence', 'counterevidence',
    'created_at', 'provenance',
  ]);
  const PROVENANCE_FIELDS = new Set([
    'origin', 'run_id', 'request_id', 'operation', 'base_profile_ref',
  ]);
  const BASE_PROFILE_REF_FIELDS = new Set(['tag_id', 'sha256']);
  const LATEST_RUN_FIELDS = new Set([
    'run_id', 'run_key', 'cache_hit', 'request_id', 'status', 'completed_at',
    'model_turns', 'tool_calls', 'actions', 'reason_codes', 'history_matches',
    'stop_reason', 'usage',
  ]);
  const PROFILE_STATS_FIELDS = new Set([
    'legacy_seen', 'stored_seen', 'stored_active', 'tombstones', 'invalid_excluded',
    'user_actions_seen', 'user_actions_valid', 'user_actions_applied', 'active',
  ]);
  const RESPONSE_FIELDS = new Set([
    'schema_version', 'request_id', 'request_sha256', 'kind', 'status', 'created_at',
    'run_id', 'run_key', 'cache_hit', 'as_of', 'window_days', 'record_days',
    'source_hashes', 'input_history_sha256', 'input_profile_sha256',
    'input_feedback_sha256', 'input_user_action_sha256', 'result_profile_sha256',
    'memory', 'trace', 'usage', 'error', 'error_kind',
  ]);
  const TRACE_FIELDS = new Set([
    'model_turns', 'tool_calls', 'actions', 'reason_codes', 'history_matches', 'stop_reason',
  ]);
  const RUN_FIELDS = new Set([
    'schema_version', 'kind', 'run_id', 'run_key', 'cache_hit', 'request_id',
    'request_sha256', 'status', 'started_at', 'completed_at', 'provider', 'model',
    'policy_sha256', 'budget', 'input_hashes', 'steps', 'usage', 'response_sha256',
    'error_kind',
  ]);
  const BUDGET_FIELDS = new Set([
    'max_turns', 'max_tool_calls', 'max_total_tokens', 'max_prompt_chars',
  ]);
  const INPUT_HASH_FIELDS = new Set([
    'source_hashes', 'history_sha256', 'profile_sha256', 'feedback_sha256',
    'user_action_sha256',
  ]);
  const STEP_FIELDS = new Set([
    'turn', 'action', 'reason_code', 'arguments_sha256', 'result_kind',
    'result_count', 'error_kind',
  ]);
  const SOURCE_HASH_FIELDS = new Set(['file', 'sha256']);
  const EVIDENCE_FIELDS = new Set(['file', 'line', 'quote']);
  const USAGE_FIELDS = new Set([
    'model_calls', 'prompt_tokens', 'completion_tokens', 'total_tokens',
    'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens', 'reasoning_tokens',
    'usage_missing', 'cost_usd',
  ]);

  const RESPONSE_STATUSES = new Set([
    'updated', 'no_change', 'insufficient_evidence', 'budget_exhausted', 'stale', 'error',
  ]);
  const INSIGHT_KINDS = new Set(['confirmed', 'observation', 'change', 'tension']);
  const OPERATIONS = new Set([
    'legacy_projection', 'pending_user_edit', 'new', 'reinforce', 'revise', 'tension',
    'user_edit', 'tombstone', 'bootstrap_reject',
  ]);
  const ACTION_REASON_CODES = Object.freeze({
    finalize_patch: new Set(['evidence_sufficient']),
    finish: new Set(['insufficient_evidence', 'no_material_change']),
    investigate: new Set(['plan_evidence']),
    read_memory: new Set(['inspect_existing']),
    search_history: new Set(['check_counterevidence', 'need_history_evidence']),
  });
  const SENSITIVE_PATTERNS = [
    /\b(?:medical|diagnos(?:is|ed)|disease|mental health|mental state|psychological state|emotion(?:al|ally)?|mood|anxi(?:ety|ous)|sadness|depress(?:ed|ion)|religion|religious|political affiliation|sexual orientation|credit score|bank account|social security|password|api[ _-]?key|precise address)\b/i,
    /(?:病历|诊断|疾病|心理健康|心理状态|情绪|焦虑|悲伤|沮丧|抑郁|宗教信仰|政治立场|性取向|身份证号|银行账号|信用评分|精确住址|家庭住址|密码|密钥)/i,
    /\bsk-[A-Za-z0-9_-]{12,}\b/,
    /\bBearer\s+[A-Za-z0-9._~+/-]{12,}\b/i,
    /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/i,
  ];
  const IDENTITY_LABEL_PATTERNS = [
    /^(?:你|用户)是(?:一个|一位)?[^,，。]{1,30}(?:的人|者|型)?[。.!]?$/i,
    /(?:你的|用户的)(?:人格|性格)(?:是|属于)/i,
    /^(?:you|the user) (?:are|is) an? [^.]{1,60}\.?$/i,
  ];

  function exactKeys(value, fields) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const keys = Object.keys(value);
    return keys.length === fields.size && keys.every(key => fields.has(key));
  }

  function isAgentEnableGateBytes(value) {
    return value instanceof Uint8Array
      && value.length === AGENT_ENABLE_GATE_BYTES.length
      && AGENT_ENABLE_GATE_BYTES.every((byte, index) => value[index] === byte);
  }

  function canonicalJson(value) {
    if (value === null || typeof value !== 'object') return JSON.stringify(value);
    if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
    return `{${Object.keys(value).sort().map(key => (
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`
    )).join(',')}}`;
  }

  // Python persists sorted, indented JSON, but run.response_sha256 binds the
  // compact representation. Compact the raw text so numeric spellings such as
  // 0.0 survive; parse + JSON.stringify would silently turn them into 0.
  function compactSortedJsonText(rawText) {
    if (typeof rawText !== 'string') return '';
    let result = '';
    let inString = false;
    let escaped = false;
    for (const character of rawText) {
      if (inString) {
        result += character;
        if (escaped) escaped = false;
        else if (character === '\\') escaped = true;
        else if (character === '"') inString = false;
      } else if (character === '"') {
        inString = true;
        result += character;
      } else if (!/\s/.test(character)) {
        result += character;
      }
    }
    return inString ? '' : result;
  }

  function text(value, maximum = Infinity) {
    return typeof value === 'string'
      && value === value.trim()
      && value.length > 0
      && value.length <= maximum
      ? value : '';
  }

  function isLocalDate(value) {
    if (typeof value !== 'string' || !LOCAL_DATE_RE.test(value)) return false;
    const [year, month, day] = value.split('-').map(Number);
    const parsed = new Date(Date.UTC(year, month - 1, day));
    return parsed.getUTCFullYear() === year
      && parsed.getUTCMonth() === month - 1
      && parsed.getUTCDate() === day;
  }

  function isIsoDateTime(value) {
    if (typeof value !== 'string' || value.length > 64) return false;
    const match = ISO_DATETIME_RE.exec(value);
    if (!match || !Number.isFinite(Date.parse(value))) return false;
    const [, year, month, day, hour, minute, second = '0', offsetHour = '0', offsetMinute = '0'] = match;
    return isLocalDate(`${year}-${month}-${day}`)
      && Number(hour) <= 23
      && Number(minute) <= 59
      && Number(second) <= 59
      && Number(offsetHour) <= 23
      && Number(offsetMinute) <= 59;
  }

  function containsForbiddenText(value) {
    return typeof value === 'string' && SENSITIVE_PATTERNS.some(pattern => pattern.test(value));
  }

  function containsIdentityLabel(...values) {
    return values.some(value => typeof value === 'string'
      && IDENTITY_LABEL_PATTERNS.some(pattern => pattern.test(value)));
  }

  function validActionReasonPair(action, reasonCode) {
    if (!text(action) || !text(reasonCode)) return false;
    if (action === 'provider_attempt') {
      return [
        'provider_attempt_started', 'provider_attempt_budget', 'provider_attempt_runtime',
      ].includes(reasonCode);
    }
    if (action === 'invalid_action') return reasonCode === 'invalid_action';
    return ACTION_REASON_CODES[action]?.has(reasonCode) === true;
  }

  function validPublicActionReasonPair(action, reasonCode) {
    return action !== 'provider_attempt' && validActionReasonPair(action, reasonCode);
  }

  function validStepOutcome(step) {
    const noError = step.error_kind === null;
    if (step.action === 'provider_attempt') {
      const unresolved = step.reason_code === 'provider_attempt_started'
        && step.result_kind === 'provider_attempt_started'
        && noError;
      const resolved = ['budget', 'runtime'].includes(step.error_kind)
        && step.reason_code === `provider_attempt_${step.error_kind}`
        && step.result_kind === 'provider_attempt_resolved';
      return step.result_count === 0 && (unresolved || resolved);
    }
    if (step.action === 'invalid_action') {
      return step.result_kind === 'rejected'
        && step.result_count === 0
        && !noError;
    }
    if (step.result_kind === 'pending') {
      return step.result_count === 0 && noError;
    }
    if (step.result_kind === 'loop_blocked') {
      return ['read_memory', 'search_history'].includes(step.action)
        && step.result_count === 0
        && step.error_kind === 'loop';
    }
    if (step.result_kind === 'budget_blocked') {
      return step.action !== 'finish'
        && step.result_count === 0
        && step.error_kind === 'budget';
    }
    if (step.result_kind === 'rejected') {
      return step.result_count === 0 && !noError;
    }
    if (step.action === 'read_memory') {
      return step.result_kind === 'memory' && step.result_count === 1 && noError;
    }
    if (step.action === 'investigate') {
      return step.result_kind === 'investigation_materialized' && noError;
    }
    if (step.action === 'search_history') {
      return step.result_kind === 'history_matches' && noError;
    }
    if (step.action === 'finalize_patch') {
      return step.result_kind === 'memory_updated' && step.result_count === 1 && noError;
    }
    if (step.action === 'finish') {
      const expected = step.reason_code === 'no_material_change'
        ? 'no_change' : 'insufficient_evidence';
      return step.result_kind === expected && step.result_count === 0 && noError;
    }
    return false;
  }

  function normalizeEvidence(value, maximum = 20) {
    if (!Array.isArray(value) || value.length > maximum) return null;
    const seen = new Set();
    const normalized = [];
    for (const item of value) {
      if (!exactKeys(item, EVIDENCE_FIELDS)
          || typeof item.file !== 'string'
          || !DAILY_FILE_RE.test(item.file)
          || !isLocalDate(item.file.slice(0, 10))
          || !Number.isSafeInteger(item.line)
          || item.line < 1
          || typeof item.quote !== 'string'
          || !item.quote
          || containsForbiddenText(item.quote)) return null;
      const key = `${item.file}\n${item.line}\n${item.quote}`;
      if (seen.has(key)) return null;
      seen.add(key);
      normalized.push({ file: item.file, line: item.line, quote: item.quote });
    }
    return normalized;
  }

  function normalizeSourceHashes(value) {
    if (!Array.isArray(value)) return null;
    const seen = new Set();
    const hashes = [];
    for (const item of value) {
      if (!exactKeys(item, SOURCE_HASH_FIELDS)
          || typeof item.file !== 'string'
          || !DAILY_FILE_RE.test(item.file)
          || !isLocalDate(item.file.slice(0, 10))
          || typeof item.sha256 !== 'string'
          || !SHA256_RE.test(item.sha256)
          || seen.has(item.file)) return null;
      seen.add(item.file);
      hashes.push({ file: item.file, sha256: item.sha256 });
    }
    return hashes;
  }

  function normalizeUsage(value) {
    if (!exactKeys(value, USAGE_FIELDS)) return null;
    const normalized = {};
    for (const field of [...USAGE_FIELDS].filter(item => !['usage_missing', 'cost_usd'].includes(item))) {
      if (!Number.isSafeInteger(value[field]) || value[field] < 0) return null;
      normalized[field] = value[field];
    }
    if (typeof value.usage_missing !== 'boolean'
        || !(value.cost_usd === null
          || (typeof value.cost_usd === 'number' && Number.isFinite(value.cost_usd) && value.cost_usd >= 0))) {
      return null;
    }
    normalized.usage_missing = value.usage_missing;
    normalized.cost_usd = value.cost_usd;
    return normalized;
  }

  function normalizeAgentRequest(value) {
    if (!exactKeys(value, REQUEST_FIELDS)
        || value.schema_version !== SCHEMA_VERSION
        || typeof value.id !== 'string'
        || !REQUEST_ID_RE.test(value.id)
        || value.kind !== 'remember_agent_request'
        || value.status !== 'pending'
        || !isIsoDateTime(value.created_at)
        || !['manual', 'scheduled'].includes(value.trigger)
        || !isLocalDate(value.as_of)
        || value.window_days !== 14) return null;
    return {
      schemaVersion: SCHEMA_VERSION,
      id: value.id,
      kind: value.kind,
      status: value.status,
      createdAt: value.created_at,
      trigger: value.trigger,
      asOf: value.as_of,
      windowDays: value.window_days,
    };
  }

  function buildAgentRequest({ id, asOf, trigger = 'manual', now } = {}) {
    const date = now instanceof Date ? now : new Date(now === undefined ? Date.now() : now);
    if (Number.isNaN(date.getTime())) throw new TypeError('Agent V1 请求时间无效');
    const value = {
      schema_version: SCHEMA_VERSION,
      id,
      kind: 'remember_agent_request',
      status: 'pending',
      created_at: date.toISOString(),
      trigger,
      as_of: asOf,
      window_days: 14,
    };
    if (!normalizeAgentRequest(value)) throw new TypeError('Agent V1 请求字段无效');
    return value;
  }

  function normalizeSchedule(value) {
    if (!exactKeys(value, SCHEDULE_FIELDS)
        || value.schema_version !== SCHEMA_VERSION
        || value.kind !== 'remember_agent_schedule'
        || typeof value.enabled !== 'boolean'
        || value.cadence !== 'daily'
        || value.hour !== 21
        || value.minute !== 0
        || !isIsoDateTime(value.updated_at)) return null;
    return {
      schemaVersion: SCHEMA_VERSION,
      kind: value.kind,
      enabled: value.enabled,
      cadence: value.cadence,
      hour: value.hour,
      minute: value.minute,
      updatedAt: value.updated_at,
    };
  }

  function buildSchedule({ enabled, now } = {}) {
    const date = now instanceof Date ? now : new Date(now === undefined ? Date.now() : now);
    if (Number.isNaN(date.getTime())) throw new TypeError('Agent V1 自动整理更新时间无效');
    const value = {
      schema_version: SCHEMA_VERSION,
      kind: 'remember_agent_schedule',
      enabled,
      cadence: 'daily',
      hour: 21,
      minute: 0,
      updated_at: date.toISOString(),
    };
    if (!normalizeSchedule(value)) throw new TypeError('Agent V1 自动整理字段无效');
    return value;
  }

  function normalizeAgentRequestRecord(value, fallbackId) {
    const normalized = normalizeAgentRequest(value);
    return normalized && normalized.id === fallbackId ? normalized : null;
  }

  function normalizeUserAction(value) {
    if (!exactKeys(value, USER_ACTION_FIELDS)
        || value.schema_version !== SCHEMA_VERSION
        || typeof value.id !== 'string'
        || !USER_ACTION_ID_RE.test(value.id)
        || value.kind !== 'remember_agent_user_action'
        || !isIsoDateTime(value.created_at)
        || !['edit', 'delete'].includes(value.action)
        || typeof value.memory_id !== 'string'
        || !MEMORY_ID_RE.test(value.memory_id)
        || !Number.isSafeInteger(value.base_revision)
        || value.base_revision < 0
        || typeof value.base_revision_sha256 !== 'string'
        || !SHA256_RE.test(value.base_revision_sha256)) return null;
    if (value.action === 'delete') {
      if (value.statement !== null || value.scope !== null) return null;
    } else if (!text(value.statement, 400)
        || !text(value.scope, 160)
        || containsForbiddenText(`${value.statement}\n${value.scope}`)
        || containsIdentityLabel(value.statement)) return null;
    return {
      schemaVersion: SCHEMA_VERSION,
      id: value.id,
      kind: value.kind,
      createdAt: value.created_at,
      action: value.action,
      memoryId: value.memory_id,
      baseRevision: value.base_revision,
      baseRevisionSha256: value.base_revision_sha256,
      statement: value.statement,
      scope: value.scope,
    };
  }

  function buildUserAction({
    id, action, memoryId, baseRevision, baseRevisionSha256, statement = null,
    scope = null, now,
  } = {}) {
    const date = now instanceof Date ? now : new Date(now === undefined ? Date.now() : now);
    if (Number.isNaN(date.getTime())) throw new TypeError('Agent V1 用户动作时间无效');
    const value = {
      schema_version: SCHEMA_VERSION,
      id,
      kind: 'remember_agent_user_action',
      created_at: date.toISOString(),
      action,
      memory_id: memoryId,
      base_revision: baseRevision,
      base_revision_sha256: baseRevisionSha256,
      statement: action === 'delete' ? null : statement,
      scope: action === 'delete' ? null : scope,
    };
    if (!normalizeUserAction(value)) throw new TypeError('Agent V1 用户动作字段无效');
    return value;
  }

  function normalizeUserActionRecord(value, fallbackId) {
    const normalized = normalizeUserAction(value);
    return normalized && normalized.id === fallbackId ? normalized : null;
  }

  function normalizeMemoryTombstone(value) {
    if (!exactKeys(value, MEMORY_REVISION_FIELDS)
        || value.schema_version !== SCHEMA_VERSION
        || value.kind !== 'remember_memory_revision'
        || typeof value.memory_id !== 'string'
        || !MEMORY_ID_RE.test(value.memory_id)
        || !Number.isSafeInteger(value.revision)
        || value.revision < 1
        || value.status !== 'tombstone'
        || !isIsoDateTime(value.created_at)
        || value.run_id !== null
        || value.request_id !== null
        || !['tombstone', 'bootstrap_reject'].includes(value.operation)
        || !(value.previous_revision_sha256 === null
          || (typeof value.previous_revision_sha256 === 'string'
            && SHA256_RE.test(value.previous_revision_sha256)))
        || !text(value.title, 120)
        || !text(value.statement, 400)
        || !text(value.scope, 160)
        || containsForbiddenText(`${value.title}\n${value.statement}\n${value.scope}`)
        || containsIdentityLabel(value.title, value.statement)
        || !['observation', 'change', 'tension'].includes(value.insight_kind)
        || !['low', 'medium'].includes(value.uncertainty)) return null;
    let baseProfileRef = null;
    if (value.base_profile_ref !== null) {
      if (!exactKeys(value.base_profile_ref, BASE_PROFILE_REF_FIELDS)
          || typeof value.base_profile_ref.tag_id !== 'string'
          || !PROFILE_TAG_ID_RE.test(value.base_profile_ref.tag_id)
          || typeof value.base_profile_ref.sha256 !== 'string'
          || !SHA256_RE.test(value.base_profile_ref.sha256)) return null;
      baseProfileRef = {
        tagId: value.base_profile_ref.tag_id,
        sha256: value.base_profile_ref.sha256,
      };
    }
    if (value.operation === 'tombstone') {
      if (typeof value.user_action_id !== 'string'
          || !USER_ACTION_ID_RE.test(value.user_action_id)) return null;
    } else if (value.user_action_id !== null) return null;
    const evidence = normalizeEvidence(value.evidence);
    const counterevidence = normalizeEvidence(value.counterevidence);
    const sourceHashes = normalizeSourceHashes(value.source_hashes);
    if (!evidence || !counterevidence || !sourceHashes) return null;
    const supports = new Set(evidence.map(item => `${item.file}\n${item.line}\n${item.quote}`));
    if (counterevidence.some(item => supports.has(`${item.file}\n${item.line}\n${item.quote}`))) return null;
    const citedFiles = new Set([...evidence, ...counterevidence].map(item => item.file));
    const hashFiles = new Set(sourceHashes.map(item => item.file));
    if (citedFiles.size !== hashFiles.size
        || [...citedFiles].some(file => !hashFiles.has(file))) return null;
    return {
      memoryId: value.memory_id,
      revision: value.revision,
      createdAt: value.created_at,
      operation: value.operation,
      previousRevisionSha256: value.previous_revision_sha256,
      baseProfileRef,
      userActionId: value.user_action_id,
    };
  }

  function normalizeMemoryTombstoneRecord(value, fallbackId) {
    const normalized = normalizeMemoryTombstone(value);
    const match = typeof fallbackId === 'string'
      ? MEMORY_REVISION_FILE_RE.exec(fallbackId) : null;
    return normalized
      && match
      && match[1] === normalized.memoryId
      && Number(match[2]) === normalized.revision
      ? normalized : null;
  }

  function normalizeProvenance(value) {
    if (!exactKeys(value, PROVENANCE_FIELDS)
        || !['legacy_profile', 'agent_memory'].includes(value.origin)
        || !(value.run_id === null || (typeof value.run_id === 'string' && RUN_ID_RE.test(value.run_id)))
        || !(value.request_id === null || typeof value.request_id === 'string')
        || typeof value.operation !== 'string'
        || !OPERATIONS.has(value.operation)) return null;
    let baseProfileRef = null;
    if (value.base_profile_ref !== null) {
      if (!exactKeys(value.base_profile_ref, BASE_PROFILE_REF_FIELDS)
          || typeof value.base_profile_ref.tag_id !== 'string'
          || !PROFILE_TAG_ID_RE.test(value.base_profile_ref.tag_id)
          || typeof value.base_profile_ref.sha256 !== 'string'
          || !SHA256_RE.test(value.base_profile_ref.sha256)) return null;
      baseProfileRef = {
        tagId: value.base_profile_ref.tag_id,
        sha256: value.base_profile_ref.sha256,
      };
    }
    return {
      origin: value.origin,
      runId: value.run_id,
      requestId: value.request_id,
      operation: value.operation,
      baseProfileRef,
    };
  }

  function normalizeMemory(value) {
    if (!exactKeys(value, MEMORY_FIELDS)
        || typeof value.memory_id !== 'string'
        || !MEMORY_ID_RE.test(value.memory_id)
        || !Number.isSafeInteger(value.revision)
        || value.revision < 0
        || typeof value.revision_sha256 !== 'string'
        || !SHA256_RE.test(value.revision_sha256)
        || value.status !== 'active'
        || !text(value.title, 120)
        || !text(value.statement, 400)
        || !text(value.scope, 160)
        || containsForbiddenText(`${value.title}\n${value.statement}\n${value.scope}`)
        || containsIdentityLabel(value.title, value.statement)
        || !INSIGHT_KINDS.has(value.insight_kind)
        || !['low', 'medium'].includes(value.uncertainty)
        || !isIsoDateTime(value.created_at)) return null;
    const evidence = normalizeEvidence(value.evidence);
    const counterevidence = normalizeEvidence(value.counterevidence);
    const provenance = normalizeProvenance(value.provenance);
    if (!evidence || !counterevidence || !provenance) return null;
    const legacyProjection = provenance.origin === 'legacy_profile';
    if (legacyProjection) {
      if (value.revision !== 0
          || provenance.runId !== null
          || provenance.baseProfileRef === null
          || provenance.baseProfileRef.sha256 !== value.revision_sha256
          || !['legacy_projection', 'pending_user_edit'].includes(provenance.operation)) return null;
    } else {
      if (value.revision < 1
          || ['legacy_projection', 'tombstone', 'bootstrap_reject'].includes(provenance.operation)
          || (provenance.baseProfileRef !== null && value.revision !== 1)) return null;
      if (provenance.operation === 'user_edit') {
        if (provenance.runId !== null || provenance.requestId !== null) return null;
      } else if (provenance.operation !== 'pending_user_edit') {
        if (provenance.runId === null
            || provenance.requestId === null
            || !REQUEST_ID_RE.test(provenance.requestId)) return null;
      }
    }
    const supports = new Set(evidence.map(item => `${item.file}\n${item.line}\n${item.quote}`));
    if (counterevidence.some(item => supports.has(`${item.file}\n${item.line}\n${item.quote}`))) return null;
    return {
      memoryId: value.memory_id,
      revision: value.revision,
      revisionSha256: value.revision_sha256,
      status: value.status,
      title: value.title,
      statement: value.statement,
      scope: value.scope,
      insightKind: value.insight_kind,
      uncertainty: value.uncertainty,
      evidence,
      counterevidence,
      createdAt: value.created_at,
      provenance,
    };
  }

  function normalizeLatestRun(value) {
    if (!exactKeys(value, LATEST_RUN_FIELDS)
        || typeof value.run_id !== 'string'
        || !RUN_ID_RE.test(value.run_id)
        || typeof value.run_key !== 'string'
        || !RUN_KEY_RE.test(value.run_key)
        || typeof value.cache_hit !== 'boolean'
        || typeof value.request_id !== 'string'
        || !REQUEST_ID_RE.test(value.request_id)
        || !RESPONSE_STATUSES.has(value.status)
        || !isIsoDateTime(value.completed_at)) return null;
    for (const field of ['model_turns', 'tool_calls', 'history_matches']) {
      if (!Number.isSafeInteger(value[field]) || value[field] < 0) return null;
    }
    if (!Array.isArray(value.actions)
        || !Array.isArray(value.reason_codes)
        || value.actions.length !== value.reason_codes.length
        || value.actions.some((item, index) => !validPublicActionReasonPair(item, value.reason_codes[index]))
        || !text(value.stop_reason, 80)) return null;
    const usage = normalizeUsage(value.usage);
    if (!usage) return null;
    return {
      runId: value.run_id,
      runKey: value.run_key,
      cacheHit: value.cache_hit,
      requestId: value.request_id,
      status: value.status,
      completedAt: value.completed_at,
      modelTurns: value.model_turns,
      toolCalls: value.tool_calls,
      actions: [...value.actions],
      reasonCodes: [...value.reason_codes],
      historyMatches: value.history_matches,
      stopReason: value.stop_reason,
      usage,
    };
  }

  function normalizeAgentProfile(value) {
    if (!exactKeys(value, PROFILE_FIELDS)
        || value.schema_version !== SCHEMA_VERSION
        || value.kind !== 'remember_agent_profile'
        || value.projection_version !== PROFILE_VERSION
        || !(value.projection_updated_at === null || isIsoDateTime(value.projection_updated_at))
        || typeof value.profile_sha256 !== 'string'
        || !SHA256_RE.test(value.profile_sha256)
        || !Array.isArray(value.memories)
        || !exactKeys(value.stats, PROFILE_STATS_FIELDS)) return null;
    const memories = value.memories.map(normalizeMemory);
    if (memories.some(item => !item)
        || new Set(memories.map(item => item.memoryId)).size !== memories.length) return null;
    const stats = {};
    for (const field of PROFILE_STATS_FIELDS) {
      if (!Number.isSafeInteger(value.stats[field]) || value.stats[field] < 0) return null;
      stats[field] = value.stats[field];
    }
    if (stats.active !== memories.length
        || stats.stored_active > stats.stored_seen
        || stats.tombstones > stats.stored_seen
        || stats.stored_active + stats.tombstones > stats.stored_seen
        || stats.user_actions_valid > stats.user_actions_seen
        || stats.user_actions_applied > stats.user_actions_valid) return null;
    const latestRun = value.latest_run === null ? null : normalizeLatestRun(value.latest_run);
    if (value.latest_run !== null && !latestRun) return null;
    return {
      schemaVersion: SCHEMA_VERSION,
      kind: value.kind,
      projectionVersion: value.projection_version,
      projectionUpdatedAt: value.projection_updated_at,
      profileSha256: value.profile_sha256,
      memories,
      latestRun,
      stats,
    };
  }

  function normalizeTrace(value) {
    if (!exactKeys(value, TRACE_FIELDS)) return null;
    for (const field of ['model_turns', 'tool_calls', 'history_matches']) {
      if (!Number.isSafeInteger(value[field]) || value[field] < 0) return null;
    }
    if (!Array.isArray(value.actions)
        || !Array.isArray(value.reason_codes)
        || value.actions.length !== value.reason_codes.length
        || value.actions.some((item, index) => !validPublicActionReasonPair(item, value.reason_codes[index]))
        || !text(value.stop_reason)) return null;
    return {
      modelTurns: value.model_turns,
      toolCalls: value.tool_calls,
      actions: [...value.actions],
      reasonCodes: [...value.reason_codes],
      historyMatches: value.history_matches,
      stopReason: value.stop_reason,
    };
  }

  function normalizeAgentResponse(value) {
    if (!exactKeys(value, RESPONSE_FIELDS)
        || value.schema_version !== SCHEMA_VERSION
        || typeof value.request_id !== 'string'
        || !REQUEST_ID_RE.test(value.request_id)
        || typeof value.request_sha256 !== 'string'
        || !SHA256_RE.test(value.request_sha256)
        || value.kind !== 'remember_agent_response'
        || !RESPONSE_STATUSES.has(value.status)
        || !isIsoDateTime(value.created_at)
        || typeof value.run_id !== 'string'
        || !RUN_ID_RE.test(value.run_id)
        || typeof value.run_key !== 'string'
        || !RUN_KEY_RE.test(value.run_key)
        || typeof value.cache_hit !== 'boolean'
        || !isLocalDate(value.as_of)
        || value.window_days !== 14
        || !Number.isSafeInteger(value.record_days)
        || value.record_days < 0) return null;
    const sourceHashes = normalizeSourceHashes(value.source_hashes);
    const trace = normalizeTrace(value.trace);
    const usage = normalizeUsage(value.usage);
    if (!sourceHashes || sourceHashes.length !== value.record_days || !trace || !usage) return null;
    for (const field of [
      'input_history_sha256', 'input_profile_sha256', 'input_feedback_sha256',
      'input_user_action_sha256', 'result_profile_sha256',
    ]) {
      if (typeof value[field] !== 'string' || !SHA256_RE.test(value[field])) return null;
    }
    const memory = value.memory === null ? null : normalizeMemory(value.memory);
    if (value.status === 'updated') {
      if (!memory || value.error !== null || value.error_kind !== null) return null;
    } else {
      if (value.memory !== null) return null;
      if (['no_change', 'insufficient_evidence'].includes(value.status)) {
        if (value.error !== null || value.error_kind !== null) return null;
      } else if (!text(value.error, 500) || !text(value.error_kind, 80)) return null;
    }
    return {
      schemaVersion: SCHEMA_VERSION,
      requestId: value.request_id,
      requestSha256: value.request_sha256,
      kind: value.kind,
      status: value.status,
      createdAt: value.created_at,
      runId: value.run_id,
      runKey: value.run_key,
      cacheHit: value.cache_hit,
      asOf: value.as_of,
      windowDays: value.window_days,
      recordDays: value.record_days,
      sourceHashes,
      inputHistorySha256: value.input_history_sha256,
      inputProfileSha256: value.input_profile_sha256,
      inputFeedbackSha256: value.input_feedback_sha256,
      inputUserActionSha256: value.input_user_action_sha256,
      resultProfileSha256: value.result_profile_sha256,
      memory,
      trace,
      usage,
      error: value.error,
      errorKind: value.error_kind,
    };
  }

  function normalizeAgentResponseRecord(value, fallbackId) {
    const normalized = normalizeAgentResponse(value);
    return normalized && normalized.requestId === fallbackId ? normalized : null;
  }

  function normalizeBudget(value) {
    if (!exactKeys(value, BUDGET_FIELDS)) return null;
    const limits = {
      max_turns: [1, 8],
      max_tool_calls: [1, 8],
      max_total_tokens: [1, 200000],
      max_prompt_chars: [1000, 1000000],
    };
    for (const [field, [minimum, maximum]] of Object.entries(limits)) {
      if (!Number.isSafeInteger(value[field])
          || value[field] < minimum
          || value[field] > maximum) return null;
    }
    return {
      maxTurns: value.max_turns,
      maxToolCalls: value.max_tool_calls,
      maxTotalTokens: value.max_total_tokens,
      maxPromptChars: value.max_prompt_chars,
    };
  }

  function normalizeAgentRun(value) {
    if (!exactKeys(value, RUN_FIELDS)
        || value.schema_version !== SCHEMA_VERSION
        || value.kind !== 'remember_agent_run'
        || typeof value.run_id !== 'string'
        || !RUN_ID_RE.test(value.run_id)
        || typeof value.run_key !== 'string'
        || !RUN_KEY_RE.test(value.run_key)
        || typeof value.cache_hit !== 'boolean'
        || typeof value.request_id !== 'string'
        || !REQUEST_ID_RE.test(value.request_id)
        || typeof value.request_sha256 !== 'string'
        || !SHA256_RE.test(value.request_sha256)
        || ![...RESPONSE_STATUSES, 'running'].includes(value.status)
        || !isIsoDateTime(value.started_at)
        || !(value.completed_at === null || isIsoDateTime(value.completed_at))
        || !text(value.provider, 120)
        || !text(value.model, 120)
        || typeof value.policy_sha256 !== 'string'
        || !SHA256_RE.test(value.policy_sha256)
        || !exactKeys(value.input_hashes, INPUT_HASH_FIELDS)) return null;
    const budget = normalizeBudget(value.budget);
    const sourceHashes = normalizeSourceHashes(value.input_hashes.source_hashes);
    const usage = normalizeUsage(value.usage);
    if (!budget || !sourceHashes || !usage) return null;
    for (const field of ['history_sha256', 'profile_sha256', 'feedback_sha256', 'user_action_sha256']) {
      if (typeof value.input_hashes[field] !== 'string' || !SHA256_RE.test(value.input_hashes[field])) return null;
    }
    if (!Array.isArray(value.steps) || value.steps.length > budget.maxTurns) return null;
    const steps = value.steps.map(item => {
      if (!exactKeys(item, STEP_FIELDS)
          || !Number.isSafeInteger(item.turn)
          || item.turn < 1
          || !validActionReasonPair(item.action, item.reason_code)
          || typeof item.arguments_sha256 !== 'string'
          || !SHA256_RE.test(item.arguments_sha256)
          || !text(item.result_kind)
          || !Number.isSafeInteger(item.result_count)
          || item.result_count < 0
          || !(item.error_kind === null || text(item.error_kind, 80))
          || !validStepOutcome(item)) return null;
      return {
        turn: item.turn,
        action: item.action,
        reasonCode: item.reason_code,
        argumentsSha256: item.arguments_sha256,
        resultKind: item.result_kind,
        resultCount: item.result_count,
        errorKind: item.error_kind,
      };
    });
    const providerAttemptIndexes = steps
      .map((item, index) => item?.action === 'provider_attempt' ? index : -1)
      .filter(index => index >= 0);
    const loopBlockedIndexes = steps
      .map((item, index) => item?.resultKind === 'loop_blocked' ? index : -1)
      .filter(index => index >= 0);
    const budgetBlockedIndexes = steps
      .map((item, index) => item?.resultKind === 'budget_blocked' ? index : -1)
      .filter(index => index >= 0);
    const runningShapeValid = value.status === 'running'
      ? value.completed_at === null && value.response_sha256 === null && value.error_kind === null
      : value.completed_at !== null && value.response_sha256 !== null;
    const providerStep = providerAttemptIndexes.length === 1
      ? steps[providerAttemptIndexes[0]] : null;
    const providerAttemptTerminalValid = !providerStep
      || (providerStep.resultKind === 'provider_attempt_started' && (
        value.status === 'running'
        || (value.status === 'error' && value.error_kind === 'unknown_attempt')
      ))
      || (providerStep.resultKind === 'provider_attempt_resolved' && (
        value.status === 'running'
        || (providerStep.errorKind === 'runtime'
          && value.status === 'error'
          && value.error_kind === 'runtime')
        || (providerStep.errorKind === 'budget'
          && value.status === 'budget_exhausted'
          && value.error_kind === 'budget')
      ));
    const loopBlockedValid = loopBlockedIndexes.every(index => {
      const blocked = steps[index];
      return index === steps.length - 1
        && value.status === 'budget_exhausted'
        && value.error_kind === 'loop'
        && steps.slice(0, index).some(previous => (
          previous.action === blocked.action
          && previous.argumentsSha256 === blocked.argumentsSha256
        ));
    });
    const budgetBlockedValid = budgetBlockedIndexes.every(index => (
      index === steps.length - 1
      && value.status === 'budget_exhausted'
      && value.error_kind === 'budget'
    ));
    if (steps.some(item => !item)
        || !runningShapeValid
        || providerAttemptIndexes.length > 1
        || (providerAttemptIndexes.length === 1
          && providerAttemptIndexes[0] !== steps.length - 1)
        || !providerAttemptTerminalValid
        || !loopBlockedValid
        || !budgetBlockedValid
        || !(value.response_sha256 === null
          || (typeof value.response_sha256 === 'string' && SHA256_RE.test(value.response_sha256)))
        || !(value.error_kind === null || text(value.error_kind, 80))) return null;
    return {
      schemaVersion: SCHEMA_VERSION,
      kind: value.kind,
      runId: value.run_id,
      runKey: value.run_key,
      cacheHit: value.cache_hit,
      requestId: value.request_id,
      requestSha256: value.request_sha256,
      status: value.status,
      startedAt: value.started_at,
      completedAt: value.completed_at,
      provider: value.provider,
      model: value.model,
      policySha256: value.policy_sha256,
      budget,
      inputHashes: {
        sourceHashes,
        historySha256: value.input_hashes.history_sha256,
        profileSha256: value.input_hashes.profile_sha256,
        feedbackSha256: value.input_hashes.feedback_sha256,
        userActionSha256: value.input_hashes.user_action_sha256,
      },
      steps,
      usage,
      responseSha256: value.response_sha256,
      errorKind: value.error_kind,
    };
  }

  function normalizeAgentRunRecord(value, fallbackId) {
    const normalized = normalizeAgentRun(value);
    return normalized && normalized.runId === fallbackId ? normalized : null;
  }

  function policyPayloadFromRun(run) {
    if (!run) return null;
    const workflowMode = AGENTIC_WORKFLOW_PROVIDERS.has(run.provider);
    const toolContract = {
      actions: workflowMode
        ? ['finalize_patch', 'finish', 'investigate', 'read_memory', 'search_history']
        : ['finalize_patch', 'finish', 'read_memory', 'search_history'],
      reason_codes: {
        finalize_patch: ['evidence_sufficient'],
        finish: ['insufficient_evidence', 'no_material_change'],
        ...(workflowMode ? { investigate: ['plan_evidence'] } : {}),
        read_memory: ['inspect_existing'],
        search_history: ['check_counterevidence', 'need_history_evidence'],
      },
      patch_operations: ['new', 'reinforce', 'revise', 'tension'],
      one_patch_per_run: true,
      post_call_token_budget: {
        version: POST_CALL_TOKEN_BUDGET_POLICY_VERSION,
        overshoot_condition: 'total_tokens_gt_max_total_tokens',
        next_provider_condition: 'total_tokens_gte_max_total_tokens',
        execute_overshoot_action: false,
        tool_result_kind: 'budget_blocked',
        finish_result_kind: 'rejected',
        invalid_result_kind: 'rejected',
        error_kind: 'budget',
      },
      stable_new_identity: {
        version: STABLE_NEW_IDENTITY_POLICY_VERSION,
        instruction_sha256: STABLE_NEW_IDENTITY_INSTRUCTION_SHA256,
        scope_rules: STABLE_NEW_SCOPE_RULES,
        blocked_quote_patterns: STABLE_NEW_QUOTE_BLOCK_PATTERN_TEXTS,
        scope_exclusion_templates: STABLE_NEW_SCOPE_EXCLUSION_TEMPLATES,
      },
    };
    if (workflowMode) {
      toolContract.agentic_workflow = {
        version: AGENTIC_WORKFLOW_POLICY_VERSION,
        instruction_sha256: AGENTIC_WORKFLOW_INSTRUCTION_SHA256,
        candidate_profile_scope: {
          version: PERSON_PROFILE_CANDIDATE_POLICY_VERSION,
          instruction_sha256: PERSON_PROFILE_CANDIDATE_INSTRUCTION_SHA256,
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
          version: STABLE_NEW_TERMINAL_GATE_POLICY_VERSION,
          instruction_sha256: STABLE_NEW_TERMINAL_GATE_INSTRUCTION_SHA256,
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
          direct_self_patterns: STABLE_NEW_DIRECT_SELF_PATTERN_TEXTS,
          temporal_or_reported_patterns: STABLE_NEW_TEMPORAL_OR_REPORTED_PATTERN_TEXTS,
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
          'candidate_kind',
          'target_memory_id',
          'queries',
          'terminal_patch_or_finish',
        ],
      };
    } else {
      toolContract.conflict_investigation = {
        version: CONFLICT_INVESTIGATION_POLICY_VERSION,
        instruction_sha256: CONFLICT_INVESTIGATION_INSTRUCTION_SHA256,
      };
      toolContract.bounded_finish_investigation = {
        version: BOUNDED_FINISH_POLICY_VERSION,
        instruction_sha256: BOUNDED_FINISH_INSTRUCTION_SHA256,
        max_candidate_memory_ids: BOUNDED_FINISH_MAX_CANDIDATE_MEMORY_IDS,
        max_rejections_per_run: 1,
        minimum_budget_max_turns: 4,
        minimum_remaining_turns_after_rejection: 3,
        required_next_action: 'read_memory',
      };
      toolContract.post_read_finish_investigation = {
        version: POST_READ_FINISH_POLICY_VERSION,
        instruction_sha256: POST_READ_FINISH_INSTRUCTION_SHA256,
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
      };
    }
    return {
      prompt_version: workflowMode
        ? AGENTIC_WORKFLOW_PROMPT_VERSION : LEGACY_PROMPT_VERSION,
      schema_version: SCHEMA_VERSION,
      tool_contract: toolContract,
      authorization: {
        allowed_request_triggers: ['manual', 'scheduled'],
        model_context_trigger: 'user_authorized',
        window_days: 14,
      },
      provider: run.provider,
      model: run.model,
      budget: {
        max_turns: run.budget.maxTurns,
        max_tool_calls: run.budget.maxToolCalls,
        max_total_tokens: run.budget.maxTotalTokens,
        max_prompt_chars: run.budget.maxPromptChars,
      },
    };
  }

  function historicalWorkflowV20PolicyPayloadFromRun(run) {
    if (!run || !AGENTIC_WORKFLOW_PROVIDERS.has(run.provider)) return null;
    const payload = policyPayloadFromRun(run);
    payload.prompt_version = HISTORICAL_AGENTIC_WORKFLOW_V20.promptVersion;
    payload.tool_contract.stable_new_identity.version =
      HISTORICAL_STABLE_NEW_IDENTITY_POLICY_VERSION;
    payload.tool_contract.stable_new_identity.instruction_sha256 =
      HISTORICAL_STABLE_NEW_IDENTITY_INSTRUCTION_SHA256;
    const workflow = payload.tool_contract.agentic_workflow;
    workflow.version = HISTORICAL_AGENTIC_WORKFLOW_V20.policyVersion;
    workflow.instruction_sha256 = HISTORICAL_AGENTIC_WORKFLOW_V20.instructionSha256;
    delete workflow.stable_new_terminal_gate;
    workflow.terminal_new_identity_contract = 'exact_required_statement_scope_when_stable';
    return payload;
  }

  function historicalWorkflowV19PolicyPayloadFromRun(run) {
    const payload = historicalWorkflowV20PolicyPayloadFromRun(run);
    if (!payload) return null;
    payload.prompt_version = HISTORICAL_AGENTIC_WORKFLOW_V19.promptVersion;
    const workflow = payload.tool_contract.agentic_workflow;
    workflow.version = HISTORICAL_AGENTIC_WORKFLOW_V19.policyVersion;
    workflow.instruction_sha256 = HISTORICAL_AGENTIC_WORKFLOW_V19.instructionSha256;
    delete workflow.candidate_profile_scope;
    delete workflow.stable_new_identity_bundle_fields;
    delete workflow.terminal_new_identity_contract;
    payload.authorization = { trigger: 'manual', window_days: 14 };
    return payload;
  }

  // Stored runs remain auditable across policy upgrades. Rebuild every accepted
  // policy from the run's own provider, model and complete budget, then hash the
  // canonical payload in the dashboard. Historical compatibility is limited to
  // the two shipped Agentic Workflow contracts; non-workflow providers accept
  // only the current reconstruction.
  function policyPayloadCandidatesFromRun(run) {
    const current = policyPayloadFromRun(run);
    if (!current) return [];
    if (!AGENTIC_WORKFLOW_PROVIDERS.has(run.provider)) return [current];
    return [
      current,
      historicalWorkflowV20PolicyPayloadFromRun(run),
      historicalWorkflowV19PolicyPayloadFromRun(run),
    ];
  }

  function requestFileName(id) {
    if (typeof id !== 'string' || !REQUEST_ID_RE.test(id)) throw new TypeError('Agent request ID 无效');
    return `${id}.json`;
  }

  function userActionFileName(id) {
    if (typeof id !== 'string' || !USER_ACTION_ID_RE.test(id)) throw new TypeError('Agent user action ID 无效');
    return `${id}.json`;
  }

  function verifyProfileEvidence(profile, sources) {
    if (!profile) return { valid: false, reason: 'profile' };
    const lookup = file => sources instanceof Map ? sources.get(file) : sources && sources[file];
    for (const memory of profile.memories) {
      for (const item of [...memory.evidence, ...memory.counterevidence]) {
        const source = lookup(item.file);
        if (!source || !Array.isArray(source.lines)) {
          return { valid: false, reason: 'missing-source', file: item.file };
        }
        if (item.line > source.lines.length || source.lines[item.line - 1] !== item.quote) {
          return { valid: false, reason: 'evidence', file: item.file, line: item.line };
        }
      }
    }
    return { valid: true, reason: '' };
  }

  function verifyResponseSources(response, sources) {
    if (!response) return { valid: false, reason: 'response' };
    const lookup = file => sources instanceof Map ? sources.get(file) : sources && sources[file];
    for (const item of response.sourceHashes) {
      const source = lookup(item.file);
      if (!source) return { valid: false, reason: 'missing-source', file: item.file };
      if (source.sha256 !== item.sha256) return { valid: false, reason: 'stale', file: item.file };
    }
    if (response.memory) {
      const check = verifyProfileEvidence({ memories: [response.memory] }, sources);
      if (!check.valid) return check;
    }
    return { valid: true, reason: '' };
  }

  function jsonEqual(left, right) {
    return JSON.stringify(left) === JSON.stringify(right);
  }

  function runSummaryFromRun(run) {
    if (!run || run.status === 'running' || !run.completedAt) return null;
    const publicSteps = run.steps.filter(item => (
      item.action !== 'provider_attempt' && item.resultKind !== 'provider_attempt_started'
    ));
    const actions = publicSteps.map(item => item.action);
    return {
      runId: run.runId,
      runKey: run.runKey,
      cacheHit: run.cacheHit,
      requestId: run.requestId,
      status: run.status,
      completedAt: run.completedAt,
      modelTurns: run.usage.model_calls,
      toolCalls: publicSteps.filter(item => (
        !['finish', 'invalid_action'].includes(item.action)
        && item.resultKind !== 'budget_blocked'
        && !['workflow_phase', 'workflow_disabled'].includes(item.errorKind)
      )).length,
      actions,
      reasonCodes: publicSteps.map(item => item.reasonCode),
      historyMatches: publicSteps
        .filter(item => ['history_matches', 'investigation_materialized'].includes(item.resultKind))
        .reduce((total, item) => total + item.resultCount, 0),
      stopReason: run.errorKind || (run.status === 'updated' ? 'patch_committed' : run.status),
      usage: run.usage,
    };
  }

  function traceMatchesRun(response, run) {
    const publicSteps = run.steps.filter(step => (
      step.action !== 'provider_attempt' && step.resultKind !== 'provider_attempt_started'
    ));
    const runActions = publicSteps.map(step => step.action);
    const runReasons = publicSteps.map(step => step.reasonCode);
    const traceActions = response.trace.actions;
    const traceReasons = response.trace.reasonCodes;
    const actionBindingValid = jsonEqual(runActions, traceActions)
      && jsonEqual(runReasons, traceReasons);
    if (!actionBindingValid) return false;

    const expectedModelTurns = Math.max(
      run.usage.model_calls,
      0,
      ...run.steps.map(step => step.turn)
    );
    const expectedToolCalls = publicSteps.filter(step => (
      !['finish', 'invalid_action'].includes(step.action)
      && step.resultKind !== 'budget_blocked'
      && !['workflow_phase', 'workflow_disabled'].includes(step.errorKind)
    )).length;
    const expectedHistoryMatches = publicSteps
      .filter(step => ['history_matches', 'investigation_materialized'].includes(step.resultKind))
      .reduce((total, step) => total + step.resultCount, 0);
    return response.trace.modelTurns === expectedModelTurns
      && response.trace.toolCalls === expectedToolCalls
      && response.trace.historyMatches === expectedHistoryMatches;
  }

  function memoryMatchesResponse(memory, response) {
    const candidate = response?.memory;
    return Boolean(candidate
      && memory.memoryId === candidate.memoryId
      && memory.revision === candidate.revision
      && memory.revisionSha256 === candidate.revisionSha256
      && memory.status === candidate.status
      && memory.title === candidate.title
      && memory.statement === candidate.statement
      && memory.scope === candidate.scope
      && memory.insightKind === candidate.insightKind
      && memory.uncertainty === candidate.uncertainty
      && memory.createdAt === candidate.createdAt
      && jsonEqual(memory.evidence, candidate.evidence)
      && jsonEqual(memory.counterevidence, candidate.counterevidence)
      && jsonEqual(memory.provenance, candidate.provenance));
  }

  function matchingUserEdit(memory, actions, { pending }) {
    return actions.some(action => {
      if (action.action !== 'edit'
          || action.memoryId !== memory.memoryId
          || action.statement !== memory.statement
          || action.scope !== memory.scope) return false;
      if (pending) {
        return action.baseRevision === memory.revision
          && action.baseRevisionSha256 === memory.revisionSha256;
      }
      if (action.createdAt !== memory.createdAt
          || action.baseRevision + 1 !== memory.revision) return false;
      return action.baseRevision === 0
        ? memory.provenance.baseProfileRef?.sha256 === action.baseRevisionSha256
        : memory.provenance.baseProfileRef === null;
    });
  }

  // A public profile is a projection, not an immutable run result. A trusted
  // Worker may legitimately advance it after the latest Agent response by
  // materializing immutable browser user-actions. Accept that evolution only
  // when every visible memory still has a strict provenance link and at least
  // one current edit/delete action explains why the profile hash moved.
  function tombstoneMatchesDelete(receipt, action) {
    if (receipt.operation !== 'tombstone'
        || action.action !== 'delete'
        || receipt.memoryId !== action.memoryId
        || receipt.userActionId !== action.id
        || receipt.createdAt !== action.createdAt
        || receipt.revision !== action.baseRevision + 1) return false;
    if (action.baseRevision === 0) {
      return receipt.previousRevisionSha256 === null
        && receipt.baseProfileRef?.sha256 === action.baseRevisionSha256;
    }
    return receipt.previousRevisionSha256 === action.baseRevisionSha256
      && receipt.baseProfileRef === null;
  }

  function localUserProjectionValid(
    profile, userActions, verifiedResponses, tombstoneReceipts
  ) {
    const actions = (userActions || []).filter(Boolean);
    const receipts = (tombstoneReceipts || []).filter(Boolean);
    if (!actions.length
        || profile.stats.user_actions_valid < 1
        || profile.stats.user_actions_seen !== actions.length
        || profile.stats.user_actions_valid > actions.length
        || profile.stats.tombstones !== receipts.length
        || new Set(receipts.map(item => `${item.memoryId}:${item.revision}`)).size
          !== receipts.length) return false;
    let mutationExplained = false;
    for (const memory of profile.memories) {
      const provenance = memory.provenance;
      if (provenance.origin === 'legacy_profile') {
        if (provenance.operation === 'pending_user_edit') {
          if (!matchingUserEdit(memory, actions, { pending: true })) return false;
          mutationExplained = true;
        }
        continue;
      }
      if (provenance.operation === 'user_edit') {
        if (!matchingUserEdit(memory, actions, { pending: false })) return false;
        mutationExplained = true;
        continue;
      }
      if (provenance.operation === 'pending_user_edit') {
        if (!matchingUserEdit(memory, actions, { pending: true })) return false;
        mutationExplained = true;
        continue;
      }
      const linked = verifiedResponses.some(item => (
        item.value.runId === provenance.runId
        && item.value.requestId === provenance.requestId
        && memoryMatchesResponse(memory, item.value)
      ));
      if (!linked) return false;
    }
    const activeIds = new Set(profile.memories.map(memory => memory.memoryId));
    const appliedDelete = actions.some(action => action.action === 'delete'
      && !activeIds.has(action.memoryId)
      && receipts.some(receipt => tombstoneMatchesDelete(receipt, action)));
    if (appliedDelete) mutationExplained = true;
    return mutationExplained;
  }

  function verifyAgentArtifacts({
    profile, profileRecord, requests, responses, runs, sources, userActions = [],
    tombstoneReceipts = [],
  }) {
    if (!profile || !profileRecord) return { valid: false, reason: 'profile' };
    const evidence = verifyProfileEvidence(profile, sources);
    if (!evidence.valid) return evidence;
    const requestById = new Map((requests || []).map(item => [item.value.id, item]));
    const runById = new Map((runs || []).map(item => [item.value.runId, item]));
    const verifiedResponses = [];
    for (const item of responses || []) {
      const response = item.value;
      const request = requestById.get(response.requestId);
      const run = runById.get(response.runId);
      const sourceStale = response.status === 'stale' && response.errorKind === 'stale';
      const unknownAttempt = response.errorKind === 'unknown_attempt';
      const sourcesMatchRun = run
        && jsonEqual(run.value.inputHashes.sourceHashes, response.sourceHashes);
      const sourceBindingValid = sourceStale
        ? response.sourceHashes.length === 0
        : unknownAttempt
          ? (response.sourceHashes.length === 0 || sourcesMatchRun)
          : sourcesMatchRun;
      if (!request
          || request.record.sha256 !== response.requestSha256
          || request.value.asOf !== response.asOf
          || request.value.windowDays !== response.windowDays
          || !run
          || run.value.requestId !== response.requestId
          || run.value.requestSha256 !== response.requestSha256
          || run.value.runKey !== response.runKey
          || run.value.cacheHit !== response.cacheHit
          || run.value.status !== response.status
          || run.value.errorKind !== response.errorKind
          || run.policyValid !== true
          || run.value.responseSha256 !== item.record.canonicalSha256
          || !run.value.completedAt
          || Date.parse(run.value.completedAt) < Date.parse(response.createdAt)
          || !sourceBindingValid
          || run.value.inputHashes.historySha256 !== response.inputHistorySha256
          || run.value.inputHashes.profileSha256 !== response.inputProfileSha256
          || run.value.inputHashes.feedbackSha256 !== response.inputFeedbackSha256
          || run.value.inputHashes.userActionSha256 !== response.inputUserActionSha256
          || !jsonEqual(run.value.usage, response.usage)
          || (response.errorKind !== null && response.trace.stopReason !== response.errorKind)
          || (unknownAttempt && (
            response.status !== 'error'
            || response.usage.usage_missing !== true
            || response.usage.cost_usd !== null
          ))) continue;
      const sourceCheck = verifyResponseSources(response, sources);
      if (!sourceCheck.valid) continue;
      if (!traceMatchesRun(response, run.value)) continue;
      verifiedResponses.push(item);
    }
    if (profile.latestRun) {
      const run = runById.get(profile.latestRun.runId);
      if (!run || !jsonEqual(runSummaryFromRun(run.value), profile.latestRun)) {
        return { valid: false, reason: 'latest-run' };
      }
      const response = verifiedResponses.find(item => item.value.requestId === profile.latestRun.requestId);
      if (!response || (response.value.resultProfileSha256 !== profile.profileSha256
          && !localUserProjectionValid(
            profile, userActions, verifiedResponses, tombstoneReceipts
          ))) {
        return { valid: false, reason: 'profile-link' };
      }
    }
    return { valid: true, reason: '', verifiedResponses };
  }

  function projectPendingUserActions(profile, actions) {
    if (!profile) return [];
    const memories = new Map(profile.memories.map(item => [item.memoryId, { ...item }]));
    const valid = (actions || []).filter(action => {
      const memory = memories.get(action.memoryId);
      return memory
        && memory.revision === action.baseRevision
        && memory.revisionSha256 === action.baseRevisionSha256;
    }).sort((left, right) => {
      const order = Date.parse(left.createdAt) - Date.parse(right.createdAt);
      return order || left.id.localeCompare(right.id);
    });
    const state = new Map();
    for (const action of valid) {
      const current = state.get(action.memoryId) || { delete: null, edit: null };
      if (action.action === 'delete') {
        if (!current.delete) current.delete = action;
      } else if (!current.delete) {
        current.edit = action;
      }
      state.set(action.memoryId, current);
    }
    const projected = [];
    for (const memory of profile.memories) {
      const action = state.get(memory.memoryId);
      if (action?.delete) continue;
      if (action?.edit) {
        projected.push({
          ...memory,
          title: action.edit.statement,
          statement: action.edit.statement,
          scope: action.edit.scope,
          pendingUserAction: action.edit,
        });
      } else {
        projected.push({ ...memory, pendingUserAction: null });
      }
    }
    return projected;
  }

  return Object.freeze({
    SCHEMA_VERSION,
    PROFILE_VERSION,
    normalizeAgentRequest,
    buildAgentRequest,
    normalizeAgentRequestRecord,
    normalizeSchedule,
    buildSchedule,
    normalizeUserAction,
    buildUserAction,
    normalizeUserActionRecord,
    normalizeMemoryTombstone,
    normalizeMemoryTombstoneRecord,
    normalizeAgentProfile,
    normalizeAgentResponse,
    normalizeAgentResponseRecord,
    normalizeAgentRun,
    normalizeAgentRunRecord,
    isAgentEnableGateBytes,
    canonicalJson,
    compactSortedJsonText,
    policyPayloadFromRun,
    policyPayloadCandidatesFromRun,
    requestFileName,
    userActionFileName,
    verifyProfileEvidence,
    verifyResponseSources,
    verifyAgentArtifacts,
    runSummaryFromRun,
    projectPendingUserActions,
  });
});
