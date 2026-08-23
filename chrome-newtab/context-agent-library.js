// Memento · Context Agent 纯数据层
// 只处理候选、用户决策、已确认 Context 和 Context Pack。
// 模型调用与 API 密钥不属于浏览器端。

(function exposeContextAgentLibrary(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MementoContextAgent = api;
})(typeof window !== 'undefined' ? window : globalThis, function createContextAgentLibrary() {
  'use strict';

  const SCHEMA_VERSION = '1.0';
  const ACTIONS = new Set(['confirm', 'just_once', 'scope', 'edit', 'reject']);
  const CATEGORIES = new Set(['project_decision', 'constraint', 'work_preference']);
  const ALLOWED_UNCERTAINTY = new Set(['low', 'medium']);
  const MAX_STATEMENT_LENGTH = 400;
  const MAX_SCOPE_LENGTH = 160;
  const SAFE_ID_RE = /^ctx_[0-9a-f]{24}$/;
  const SELF_REFLECTION_REQUEST_ID_RE = /^srq_[0-9a-f]{24}$/;
  const SELF_REFLECTION_FEEDBACK_ID_RE = /^srf_[0-9a-f]{24}$/;
  const DAILY_FILE_RE = /^\d{4}-\d{2}-\d{2}\.md$/;
  const LOCAL_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
  const ISO_DATETIME_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?(?:Z|[+-](\d{2}):(\d{2}))$/;
  const SHA256_RE = /^[a-f0-9]{64}$/;
  const SELF_REFLECTION_KINDS = new Set(['confirmed', 'observation', 'change', 'tension']);
  const SELF_REFLECTION_STATUSES = new Set(['ready', 'insufficient_evidence', 'error']);
  const SELF_REFLECTION_FEEDBACK_ACTIONS = new Set(['accurate', 'scope', 'edit', 'changed', 'reject']);
  const SELF_REFLECTION_TAG_KEY_VERSION = 'pinned-ws-ascii-lower-statement-scope-fnv96-v1';
  const PINNED_PROFILE_WHITESPACE_RE = /[\u0009-\u000d\u0020\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+/g;
  const CONFIRMED_FIELDS = new Set([
    'schema_version', 'id', 'original_candidate_id', 'status', 'confirmed_at',
    'decision_action', 'statement', 'scope', 'category', 'evidence', 'source_hashes',
  ]);
  const ONE_TIME_FIELDS = new Set([
    'statement', 'scope', 'category', 'evidence', 'source_hashes', 'original_candidate_id',
  ]);
  const PENDING_FIELDS = new Set([
    'schema_version', 'id', 'candidate_id', 'status', 'created_at', 'provider', 'model',
    'generation_key', 'source_hashes', 'statement', 'scope', 'why_now', 'category',
    'evidence', 'sensitive', 'uncertainty',
  ]);
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
  const CHANGE_QUESTION_PATTERNS = [
    /(?:变化|改变|发生了什么变化)/i,
    /\brecent changes?\b/i,
    /\bwhat (?:has )?changed\b/i,
    /\bchanged\b/i,
  ];
  const EXPLICIT_CHANGE_EVIDENCE_PATTERNS = [
    /(?:不再|改为|改成|改用|转向|转为|替代|取代|修订|调整为|调整成|已变化|发生(?:了)?变化|变更为)/i,
    /\b(?:no longer|has changed|have changed)\b/i,
    /\b(?:chang(?:e|ed|ing) (?:to|from)|shift(?:ed|ing)? to|switch(?:ed|ing)? to|transition(?:ed|ing)? to|replac(?:e|ed|ing)|revis(?:e|ed|ing)|adjust(?:ed|ing)? to)\b/i,
  ];
  const EXPLICIT_TENSION_EVIDENCE_PATTERNS = [
    /(?:冲突|不一致|相反|矛盾|一方面.{0,80}另一方面|但同时|与此同时却|两种方向并存|出现分歧|背离)/i,
    /\b(?:conflict(?:s|ed|ing)?|inconsistent|opposite|in tension|but at the same time)\b/i,
    /\bon the one hand.{0,160}on the other hand\b/i,
    /\bcontradict(?:s|ed|ing|ory)?\b/i,
  ];

  function text(value) {
    if (typeof value !== 'string' || !value.trim() || value !== value.trim()) return '';
    return value;
  }

  function safeRecordId(value) {
    const id = text(value);
    return SAFE_ID_RE.test(id) ? id : '';
  }

  function containsSensitiveText(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const combined = [
      value.statement,
      value.scope,
      value.why_now || value.whyNow,
    ].map(item => typeof item === 'string' ? item : '').join('\n');
    return SENSITIVE_PATTERNS.some(pattern => pattern.test(combined));
  }

  function containsIdentityLabel(...values) {
    return values.some(value => typeof value === 'string'
      && IDENTITY_LABEL_PATTERNS.some(pattern => pattern.test(value)));
  }

  function isChangeQuestion(value) {
    return typeof value === 'string'
      && CHANGE_QUESTION_PATTERNS.some(pattern => pattern.test(value));
  }

  function evidenceHasSignal(items, patterns) {
    return items.some(item => patterns.some(pattern => pattern.test(item.quote)));
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
    const [, yearText, monthText, dayText, hourText, minuteText, secondText = '0',
      offsetHourText = '0', offsetMinuteText = '0'] = match;
    const year = Number(yearText);
    const month = Number(monthText);
    const day = Number(dayText);
    const hour = Number(hourText);
    const minute = Number(minuteText);
    const second = Number(secondText);
    const offsetHour = Number(offsetHourText);
    const offsetMinute = Number(offsetMinuteText);
    return isLocalDate(`${yearText}-${monthText}-${dayText}`)
      && hour <= 23
      && minute <= 59
      && second <= 59
      && offsetHour <= 23
      && offsetMinute <= 59;
  }

  function normalizeScope(value) {
    return text(value);
  }

  function normalizeEvidence(value) {
    if (!Array.isArray(value)) return [];
    return value.map(item => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return null;
      const keys = Object.keys(item);
      if (keys.length !== 3 || keys.some(key => !['file', 'line', 'quote'].includes(key))) return null;
      const quote = typeof item.quote === 'string' ? item.quote : '';
      const file = typeof item.file === 'string' ? item.file : '';
      const line = Number.isSafeInteger(item.line) ? item.line : null;
      if (!DAILY_FILE_RE.test(file)
          || !isLocalDate(file.slice(0, 10))
          || !Number.isSafeInteger(line)
          || line < 1
          || !quote
          || containsSensitiveText({ statement: quote })) return null;
      return { file, line, quote };
    }).filter(Boolean);
  }

  function normalizeSourceHashes(value) {
    if (!Array.isArray(value)) return [];
    return value.map(item => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return null;
      const keys = Object.keys(item);
      if (keys.length !== 2 || keys.some(key => !['file', 'sha256'].includes(key))) return null;
      const file = typeof item.file === 'string' ? item.file : '';
      const sha256 = typeof item.sha256 === 'string' ? item.sha256 : '';
      if (!DAILY_FILE_RE.test(file) || !SHA256_RE.test(sha256)) return null;
      return { file, sha256 };
    }).filter(Boolean);
  }

  function exactKeys(value, keys) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const actual = Object.keys(value);
    return actual.length === keys.size && actual.every(key => keys.has(key));
  }

  function safeSelfReflectionRequestId(value) {
    const id = text(value);
    return SELF_REFLECTION_REQUEST_ID_RE.test(id) ? id : '';
  }

  function safeSelfReflectionFeedbackId(value) {
    const id = text(value);
    return SELF_REFLECTION_FEEDBACK_ID_RE.test(id) ? id : '';
  }

  function verifySourceBacking(value, sources) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return { valid: false, reason: 'record' };
    }
    const evidenceInput = value.evidence;
    const hashesInput = value.source_hashes || value.sourceHashes;
    if (!Array.isArray(evidenceInput) || !Array.isArray(hashesInput)) {
      return { valid: false, reason: 'contract' };
    }
    const evidence = normalizeEvidence(evidenceInput);
    const hashes = normalizeSourceHashes(hashesInput);
    if (!evidence.length
        || evidence.length !== evidenceInput.length
        || !hashes.length
        || hashes.length !== hashesInput.length) {
      return { valid: false, reason: 'contract' };
    }

    const lookup = file => sources instanceof Map ? sources.get(file) : sources && sources[file];
    const expectedFiles = new Set();
    for (const item of hashes) {
      if (expectedFiles.has(item.file)) return { valid: false, reason: 'duplicate-source' };
      expectedFiles.add(item.file);
      const source = lookup(item.file);
      if (!source || source.sha256 !== item.sha256 || !Array.isArray(source.lines)) {
        return { valid: false, reason: source ? 'stale' : 'missing-source', file: item.file };
      }
    }
    for (const item of evidence) {
      if (!expectedFiles.has(item.file)) {
        return { valid: false, reason: 'unhashed-evidence', file: item.file };
      }
      const source = lookup(item.file);
      if (!source || item.line > source.lines.length || source.lines[item.line - 1] !== item.quote) {
        return { valid: false, reason: 'evidence', file: item.file, line: item.line };
      }
    }
    return { valid: true, reason: '' };
  }

  function unwrapCandidate(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    if (value.candidate && typeof value.candidate === 'object') {
      return { ...value.candidate, status: value.status || value.candidate.status };
    }
    return value;
  }

  function normalizeCandidate(value, fallbackId = '') {
    const raw = unwrapCandidate(value);
    if (!raw) return null;
    const normalizedInput = raw.schemaVersion === SCHEMA_VERSION;
    if (!normalizedInput) {
      const keys = Object.keys(raw);
      if (keys.length !== PENDING_FIELDS.size || keys.some(key => !PENDING_FIELDS.has(key))) return null;
    }
    if ((raw.schema_version || raw.schemaVersion) !== SCHEMA_VERSION || raw.status !== 'candidate') return null;
    if (raw.sensitive !== false) return null;

    const explicitId = raw.id ? safeRecordId(raw.id) : '';
    const explicitCandidateId = raw.candidate_id || raw.candidateId
      ? safeRecordId(raw.candidate_id || raw.candidateId)
      : '';
    if (raw.id && !explicitId) return null;
    if ((raw.candidate_id || raw.candidateId) && !explicitCandidateId) return null;
    if (explicitId && explicitCandidateId && explicitId !== explicitCandidateId) return null;
    const id = explicitCandidateId || explicitId || safeRecordId(fallbackId);
    const statement = text(raw.statement);
    const scope = typeof raw.scope === 'string' ? text(raw.scope) : '';
    const uncertainty = text(raw.uncertainty);
    const evidenceInput = raw.evidence;
    const hashesInput = raw.source_hashes || raw.sourceHashes;
    const evidence = normalizeEvidence(evidenceInput);
    const sourceHashes = normalizeSourceHashes(hashesInput);
    const category = text(raw.category);
    const whyNow = text(raw.why_now || raw.whyNow);
    const evidenceFiles = new Set(evidence.map(item => item.file));
    const hashFiles = new Set(sourceHashes.map(item => item.file));
    const evidenceUnique = new Set(evidence.map(item => `${item.file}\n${item.line}\n${item.quote}`));
    const provider = text(raw.provider);
    const model = text(raw.model);
    const generationKey = text(raw.generation_key || raw.generationKey);
    const createdAt = text(raw.created_at || raw.createdAt || raw.generated_at || raw.updated_at);
    if (!id
        || !statement
        || statement.length > MAX_STATEMENT_LENGTH
        || !scope
        || scope.length > MAX_SCOPE_LENGTH
        || !whyNow
        || whyNow.length > MAX_STATEMENT_LENGTH
        || !CATEGORIES.has(category)
        || !ALLOWED_UNCERTAINTY.has(uncertainty)
        || !Array.isArray(evidenceInput)
        || !evidence.length
        || evidence.length > 5
        || evidence.length !== evidenceInput.length
        || evidenceUnique.size !== evidence.length
        || !Array.isArray(hashesInput)
        || !sourceHashes.length
        || sourceHashes.length !== hashesInput.length
        || hashFiles.size !== sourceHashes.length
        || [...evidenceFiles].some(file => !hashFiles.has(file))
        || (category === 'work_preference' && evidenceFiles.size < 2)
        || !provider
        || provider.length > 120
        || !model
        || model.length > 120
        || !/^gen_[0-9a-f]{24}$/.test(generationKey)
        || !createdAt) return null;
    if (containsSensitiveText({ statement, scope, whyNow })) return null;

    return {
      schemaVersion: SCHEMA_VERSION,
      id,
      candidateId: id,
      statement,
      scope,
      category,
      whyNow,
      evidence,
      sourceHashes,
      uncertainty,
      provider,
      model,
      generationKey,
      sensitive: false,
      createdAt,
      status: 'candidate',
    };
  }

  function normalizeDecision(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const normalizedInput = value.schemaVersion === SCHEMA_VERSION;
    if ((value.schema_version || value.schemaVersion) !== SCHEMA_VERSION) return null;
    const candidateId = safeRecordId(value.candidate_id || value.candidateId || value.original_candidate_id);
    const action = text(value.action);
    const decidedAt = text(value.decided_at || value.decidedAt);
    if (!candidateId || !ACTIONS.has(action) || !decidedAt) return null;
    if (!normalizedInput) {
      const required = new Set(['schema_version', 'candidate_id', 'action', 'decided_at']);
      if (action === 'scope') required.add('scope');
      if (action === 'edit') required.add('statement');
      if (action === 'just_once') required.add('one_time_context');
      const keys = Object.keys(value);
      if (keys.some(key => !required.has(key) && !(action === 'edit' && key === 'scope'))
          || [...required].some(key => !(key in value))) return null;
    }
    if (action === 'scope' && (typeof value.scope !== 'string' || !text(value.scope) || text(value.scope).length > MAX_SCOPE_LENGTH)) return null;
    if (action === 'edit' && (!text(value.statement) || text(value.statement).length > MAX_STATEMENT_LENGTH)) return null;
    if (action === 'edit' && value.scope !== undefined
        && (typeof value.scope !== 'string' || !text(value.scope) || text(value.scope).length > MAX_SCOPE_LENGTH)) return null;
    const oneTimeContext = normalizeOneTimeContext(value.one_time_context || value.oneTimeContext);
    if (action === 'just_once'
        && (!oneTimeContext || oneTimeContext.original_candidate_id !== candidateId)) return null;
    return {
      schemaVersion: SCHEMA_VERSION,
      candidateId,
      action,
      decidedAt,
      oneTimeContext,
    };
  }

  function normalizeOneTimeContext(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const keys = Object.keys(value);
    if (keys.length !== ONE_TIME_FIELDS.size || keys.some(key => !ONE_TIME_FIELDS.has(key))) return null;
    const statement = text(value.statement);
    const scope = typeof value.scope === 'string' ? text(value.scope) : '';
    const originalCandidateId = safeRecordId(value.original_candidate_id);
    const category = text(value.category);
    if (!Array.isArray(value.evidence) || !Array.isArray(value.source_hashes)) return null;
    const evidence = normalizeEvidence(value.evidence);
    const sourceHashes = normalizeSourceHashes(value.source_hashes);
    const evidenceFiles = new Set(evidence.map(item => item.file));
    const hashFiles = new Set(sourceHashes.map(item => item.file));
    if (!statement
        || statement.length > MAX_STATEMENT_LENGTH
        || !scope
        || scope.length > MAX_SCOPE_LENGTH
        || !originalCandidateId
        || !CATEGORIES.has(category)
        || evidence.length !== value.evidence.length
        || !evidence.length
        || evidence.length > 5
        || sourceHashes.length !== value.source_hashes.length
        || !sourceHashes.length
        || [...evidenceFiles].some(file => !hashFiles.has(file))
        || (category === 'work_preference' && evidenceFiles.size < 2)) return null;
    if (containsSensitiveText({ statement, scope })) return null;
    return {
      statement,
      scope,
      category,
      evidence,
      source_hashes: sourceHashes,
      original_candidate_id: originalCandidateId,
    };
  }

  function normalizeConfirmedContext(value, fallbackId = '') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const raw = value.confirmed_context && typeof value.confirmed_context === 'object'
      ? value.confirmed_context
      : value;
    const normalizedInput = raw.schemaVersion === SCHEMA_VERSION;
    if (!normalizedInput) {
      const keys = Object.keys(raw);
      if (keys.length !== CONFIRMED_FIELDS.size || keys.some(key => !CONFIRMED_FIELDS.has(key))) return null;
    }
    if ((raw.schema_version || raw.schemaVersion) !== SCHEMA_VERSION) return null;
    const id = safeRecordId(raw.id);
    const originalCandidateId = safeRecordId(raw.original_candidate_id || raw.originalCandidateId);
    const statement = text(raw.statement);
    const scope = typeof raw.scope === 'string' ? text(raw.scope) : '';
    const category = text(raw.category);
    const evidenceInput = raw.evidence;
    const hashesInput = raw.source_hashes || raw.sourceHashes;
    const evidence = normalizeEvidence(evidenceInput);
    const sourceHashes = normalizeSourceHashes(hashesInput);
    const evidenceFiles = new Set(evidence.map(item => item.file));
    const hashFiles = new Set(sourceHashes.map(item => item.file));
    const confirmedAt = text(raw.confirmed_at || raw.confirmedAt);
    const decisionAction = text(raw.decision_action || raw.decisionAction);
    if (!id
        || id !== originalCandidateId
        || raw.status !== 'active'
        || !confirmedAt
        || !['confirm', 'scope', 'edit'].includes(decisionAction)
        || !statement
        || statement.length > MAX_STATEMENT_LENGTH
        || !scope
        || scope.length > MAX_SCOPE_LENGTH
        || !CATEGORIES.has(category)
        || !Array.isArray(evidenceInput)
        || !evidence.length
        || evidence.length > 5
        || evidence.length !== evidenceInput.length
        || !Array.isArray(hashesInput)
        || !sourceHashes.length
        || sourceHashes.length !== hashesInput.length
        || [...evidenceFiles].some(file => !hashFiles.has(file))
        || (category === 'work_preference' && evidenceFiles.size < 2)) return null;
    if (containsSensitiveText({ statement, scope })) return null;
    return {
      schemaVersion: SCHEMA_VERSION,
      id,
      originalCandidateId,
      statement,
      scope,
      category,
      evidence,
      sourceHashes,
      confirmedAt,
      decisionAction,
      status: 'active',
    };
  }

  function newestFirst(left, right) {
    const timeOrder = String(right.createdAt || right.confirmedAt || '')
      .localeCompare(String(left.createdAt || left.confirmedAt || ''));
    return timeOrder || String(right.id).localeCompare(String(left.id));
  }

  function selectPendingCandidate(candidates, decisions = [], confirmedContexts = []) {
    const decided = new Set((Array.isArray(decisions) ? decisions : [])
      .map(normalizeDecision)
      .filter(Boolean)
      .map(item => item.candidateId));

    return (Array.isArray(candidates) ? candidates : [])
      .map(item => normalizeCandidate(item))
      .filter(item => item && !decided.has(item.id))
      .sort(newestFirst)[0] || null;
  }

  function storedConfirmedContext(value) {
    const confirmed = normalizeConfirmedContext(value);
    if (!confirmed) return null;
    return {
      schema_version: SCHEMA_VERSION,
      id: confirmed.id,
      original_candidate_id: confirmed.originalCandidateId,
      status: 'active',
      confirmed_at: confirmed.confirmedAt,
      decision_action: confirmed.decisionAction,
      statement: confirmed.statement,
      scope: confirmed.scope,
      category: confirmed.category,
      evidence: confirmed.evidence,
      source_hashes: confirmed.sourceHashes,
    };
  }

  function isoNow(now) {
    const value = now instanceof Date ? now : new Date(now === undefined ? Date.now() : now);
    if (Number.isNaN(value.getTime())) throw new TypeError('无法生成有效的决策时间');
    return value.toISOString();
  }

  function resolveChanges(candidate, action, changes) {
    const statement = action === 'edit' ? text(changes.statement) : candidate.statement;
    const scope = action === 'scope' ? normalizeScope(changes.scope) : candidate.scope;
    if (!statement) throw new TypeError('修改后的理解不能为空');
    if (!scope) throw new TypeError('限定范围不能为空');
    if (statement.length > MAX_STATEMENT_LENGTH) throw new TypeError(`理解不能超过 ${MAX_STATEMENT_LENGTH} 个字符`);
    if (scope.length > MAX_SCOPE_LENGTH) throw new TypeError(`范围不能超过 ${MAX_SCOPE_LENGTH} 个字符`);
    if (containsSensitiveText({ statement, scope, whyNow: candidate.whyNow })) {
      throw new TypeError('修改后的理解涉及敏感的情绪、心理或身份推断，不会写入 Context');
    }
    return { statement, scope };
  }

  function buildDecisionBundle(candidateValue, action, changes = {}, now) {
    const candidate = normalizeCandidate(candidateValue);
    if (!candidate) throw new TypeError('候选 Context 无效');
    if (!ACTIONS.has(action)) throw new TypeError(`不支持的 Context 决策: ${action}`);

    const decidedAt = isoNow(now);
    const resolved = resolveChanges(candidate, action, changes);
    const decision = {
      schema_version: SCHEMA_VERSION,
      candidate_id: candidate.id,
      action,
      decided_at: decidedAt,
    };
    if (action === 'scope') decision.scope = resolved.scope;
    if (action === 'edit') decision.statement = resolved.statement;

    const persists = action === 'confirm' || action === 'scope' || action === 'edit';
    const confirmedContext = persists ? {
      schema_version: SCHEMA_VERSION,
      id: candidate.id,
      original_candidate_id: candidate.id,
      status: 'active',
      confirmed_at: decidedAt,
      decision_action: action,
      statement: resolved.statement,
      scope: resolved.scope,
      category: candidate.category,
      evidence: candidate.evidence,
      source_hashes: candidate.sourceHashes,
    } : null;

    const oneTimeContext = action === 'just_once' ? {
      statement: candidate.statement,
      scope: candidate.scope,
      category: candidate.category,
      evidence: candidate.evidence,
      source_hashes: candidate.sourceHashes,
      original_candidate_id: candidate.id,
    } : null;
    if (oneTimeContext) decision.one_time_context = oneTimeContext;

    return { decision, confirmedContext, oneTimeContext };
  }

  function buildRecoveryDecisionBundle(candidateValue, confirmedValue) {
    const candidate = normalizeCandidate(candidateValue);
    const confirmed = normalizeConfirmedContext(confirmedValue);
    if (!candidate || !confirmed || candidate.id !== confirmed.originalCandidateId) {
      throw new TypeError('无法用已确认 Context 恢复该候选的决策');
    }
    if (candidate.category !== confirmed.category
        || !jsonRecordsEqual(candidate.evidence, confirmed.evidence)
        || !jsonRecordsEqual(candidate.sourceHashes, confirmed.sourceHashes)) {
      throw new TypeError('已确认 Context 与候选的来源或类别不一致');
    }
    const action = confirmed.decisionAction;
    if (action === 'confirm'
        && (confirmed.statement !== candidate.statement || confirmed.scope !== candidate.scope)) {
      throw new TypeError('已确认 Context 与 confirm 候选内容不一致');
    }
    if (action === 'scope' && confirmed.statement !== candidate.statement) {
      throw new TypeError('已确认 Context 与 scope 候选文本不一致');
    }

    const decision = {
      schema_version: SCHEMA_VERSION,
      candidate_id: candidate.id,
      action,
      decided_at: confirmed.confirmedAt,
    };
    if (action === 'scope') decision.scope = confirmed.scope;
    if (action === 'edit') {
      decision.statement = confirmed.statement;
      if (confirmed.scope !== candidate.scope) decision.scope = confirmed.scope;
    }
    return {
      decision,
      confirmedContext: storedConfirmedContext(confirmed),
      oneTimeContext: null,
      recovery: true,
    };
  }

  function activeConfirmedContexts(values) {
    const byId = new Map();
    for (const value of Array.isArray(values) ? values : []) {
      const context = normalizeConfirmedContext(value);
      if (!context || context.status !== 'active') continue;
      const existing = byId.get(context.id);
      if (!existing || newestFirst(context, existing) < 0) byId.set(context.id, context);
    }
    return [...byId.values()].sort(newestFirst);
  }

  function oneLine(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
  }

  function buildContextPack(values, options = {}) {
    const contexts = activeConfirmedContexts(values);
    const lines = ['# Memento Context Pack', ''];

    if (!contexts.length) {
      lines.push('没有匹配的已确认 Context。', '');
      return `${lines.join('\n')}\n`;
    }

    contexts.forEach(context => {
      lines.push(`## ${oneLine(context.statement)}`, '');
      lines.push(`- 类型：${oneLine(context.category)}`);
      lines.push(`- 范围：${oneLine(context.scope)}`);
      lines.push(`- Context ID：${context.id}`);
      lines.push('');
    });
    return `${lines.join('\n')}\n`;
  }

  function buildOneTimeContextPack(value) {
    const normalized = normalizeOneTimeContext(value);
    if (!normalized) throw new TypeError('单次 Context 字段不完整');
    const { statement, scope, original_candidate_id: originalCandidateId } = normalized;
    const lines = [
      '# Memento One-time Context Pack',
      '',
      '> 仅用于当前这一次任务，未进入长期 Context。',
      '',
      `理解: ${oneLine(statement)}`,
      `适用范围: ${oneLine(scope)}`,
      `类别: ${oneLine(normalized.category)}`,
      `候选 ID: ${originalCandidateId}`,
      '',
    ];
    return `${lines.join('\n')}\n`;
  }

  function recordFileName(id) {
    const safe = safeRecordId(id);
    if (!safe) throw new TypeError('Context ID 不能用作本地文件名');
    return `${safe}.json`;
  }

  function canonicalJson(value) {
    if (Array.isArray(value)) return value.map(canonicalJson);
    if (!value || typeof value !== 'object') return value;
    return Object.fromEntries(Object.keys(value).sort()
      .map(key => [key, canonicalJson(value[key])]));
  }

  function jsonRecordsEqual(left, right) {
    return JSON.stringify(canonicalJson(left)) === JSON.stringify(canonicalJson(right));
  }

  function assertCompatibleRecord(existing, next, recordId = '') {
    if (existing === null || existing === undefined) return 'new';
    if (jsonRecordsEqual(existing, next)) return 'identical';
    const error = new Error(`Context 记录${recordId ? ` ${recordId}` : ''} 已存在且内容不同，已拒绝覆盖`);
    error.name = 'ContextConflictError';
    throw error;
  }

  const SELF_REFLECTION_REQUEST_FIELDS = new Set([
    'schema_version', 'id', 'kind', 'status', 'created_at', 'question', 'as_of', 'window_days',
  ]);
  const SELF_REFLECTION_RESPONSE_FIELDS = new Set([
    'schema_version', 'request_id', 'kind', 'status', 'created_at', 'cache_hit',
    'question', 'as_of', 'window_days', 'record_days', 'source_hashes',
    'confirmed_contexts', 'reflection', 'usage',
    'error', 'error_kind',
  ]);
  const SELF_REFLECTION_FIELDS = new Set(['summary', 'scope_note', 'unknown', 'insights']);
  const SELF_REFLECTION_INSIGHT_FIELDS = new Set([
    'title', 'statement', 'scope', 'kind', 'uncertainty', 'sensitive',
    'evidence', 'counterevidence', 'context_refs',
  ]);
  const SELF_REFLECTION_FEEDBACK_FIELDS = new Set([
    'schema_version', 'id', 'kind', 'status', 'created_at', 'request_id',
    'insight_index', 'action', 'note', 'response_sha256',
  ]);
  const MODEL_USAGE_FIELDS = new Set([
    'schema_version', 'kind', 'timestamp', 'provider', 'model', 'request_id',
    'prompt_tokens', 'completion_tokens', 'total_tokens', 'prompt_cache_hit_tokens',
    'prompt_cache_miss_tokens', 'reasoning_tokens', 'usage_missing', 'cost_usd', 'pricing',
  ]);
  const MODEL_PRICING_FIELDS = new Set([
    'effective_date', 'cache_hit_input_usd_per_million',
    'cache_miss_input_usd_per_million', 'output_usd_per_million',
  ]);

  function normalizeSelfReflectionRequest(value) {
    if (!exactKeys(value, SELF_REFLECTION_REQUEST_FIELDS)) return null;
    const id = safeSelfReflectionRequestId(value.id);
    const createdAt = text(value.created_at);
    const question = text(value.question);
    if (value.schema_version !== SCHEMA_VERSION
        || !id
        || value.kind !== 'self_reflection_request'
        || value.status !== 'pending'
        || !isIsoDateTime(createdAt)
        || !question
        || question.length > 160
        || /[\r\n]/.test(question)
        || containsSensitiveText({ statement: question })
        || !isLocalDate(value.as_of)
        || value.window_days !== 14) return null;
    return {
      schemaVersion: SCHEMA_VERSION,
      id,
      kind: value.kind,
      status: value.status,
      createdAt,
      question,
      asOf: value.as_of,
      windowDays: value.window_days,
    };
  }

  function buildSelfReflectionRequest({ id, question, asOf, now } = {}) {
    const request = {
      schema_version: SCHEMA_VERSION,
      id,
      kind: 'self_reflection_request',
      status: 'pending',
      created_at: isoNow(now),
      question,
      as_of: asOf,
      window_days: 14,
    };
    if (!normalizeSelfReflectionRequest(request)) {
      throw new TypeError('主动理解请求的 ID、问题或日期无效');
    }
    return request;
  }

  function normalizeSelfReflectionInsight(value, changeIntent = false) {
    if (!exactKeys(value, SELF_REFLECTION_INSIGHT_FIELDS)) return null;
    const title = text(value.title);
    const statement = text(value.statement);
    const scope = text(value.scope);
    const kind = text(value.kind);
    const uncertainty = text(value.uncertainty);
    const evidence = normalizeEvidence(value.evidence);
    const counterevidence = normalizeEvidence(value.counterevidence);
    const contextRefs = Array.isArray(value.context_refs)
      ? value.context_refs.map(safeRecordId).filter(Boolean)
      : [];
    const evidenceKeys = new Set(evidence.map(item => `${item.file}\n${item.line}\n${item.quote}`));
    const counterKeys = new Set(counterevidence.map(item => `${item.file}\n${item.line}\n${item.quote}`));
    const evidenceDays = new Set(evidence.map(item => item.file));
    const evidenceDates = evidence.map(item => item.file.slice(0, 10));
    const counterDates = counterevidence.map(item => item.file.slice(0, 10));
    const quotedEvidence = [...evidence, ...counterevidence];
    if (!title
        || title.length > 120
        || !statement
        || statement.length > MAX_STATEMENT_LENGTH
        || !scope
        || scope.length > MAX_SCOPE_LENGTH
        || containsIdentityLabel(title, statement)
        || !SELF_REFLECTION_KINDS.has(kind)
        || (changeIntent && !['change', 'tension'].includes(kind))
        || !ALLOWED_UNCERTAINTY.has(uncertainty)
        || value.sensitive !== false
        || !Array.isArray(value.evidence)
        || evidence.length > 5
        || evidence.length !== value.evidence.length
        || evidenceKeys.size !== evidence.length
        || !Array.isArray(value.counterevidence)
        || counterevidence.length > 3
        || counterevidence.length !== value.counterevidence.length
        || counterKeys.size !== counterevidence.length
        || [...counterKeys].some(key => evidenceKeys.has(key))
        || !Array.isArray(value.context_refs)
        || contextRefs.length !== value.context_refs.length
        || new Set(contextRefs).size !== contextRefs.length
        || (kind === 'confirmed' && !contextRefs.length)
        || (kind === 'observation' && evidenceDays.size < 2)
        || (['change', 'tension'].includes(kind) && (!evidence.length || !counterevidence.length))
        || (kind === 'change'
          && evidenceDates.length
          && counterDates.length
          && evidenceDates.sort()[0] <= counterDates.sort().at(-1))
        || (changeIntent
          && kind === 'change'
          && !evidenceHasSignal(quotedEvidence, EXPLICIT_CHANGE_EVIDENCE_PATTERNS))
        || (changeIntent
          && kind === 'tension'
          && !evidenceHasSignal(quotedEvidence, EXPLICIT_TENSION_EVIDENCE_PATTERNS))
        || containsSensitiveText({ statement: `${title}\n${statement}`, scope })) return null;
    return {
      title,
      statement,
      scope,
      kind,
      uncertainty,
      sensitive: false,
      evidence,
      counterevidence,
      contextRefs,
    };
  }

  function normalizeSelfReflection(value, status, question = '') {
    if (!exactKeys(value, SELF_REFLECTION_FIELDS)) return null;
    const summary = text(value.summary);
    const scopeNote = text(value.scope_note);
    const unknown = text(value.unknown);
    const insightInput = value.insights;
    const changeIntent = isChangeQuestion(question);
    const insights = Array.isArray(insightInput)
      ? insightInput.map(insight => normalizeSelfReflectionInsight(insight, changeIntent)).filter(Boolean)
      : [];
    if (!summary
        || summary.length > 600
        || !scopeNote
        || scopeNote.length > 400
        || !unknown
        || unknown.length > 400
        || !Array.isArray(insightInput)
        || insights.length !== insightInput.length
        || insights.length > 3
        || (status === 'ready' && !insights.length)
        || (status === 'insufficient_evidence' && insights.length)
        || containsSensitiveText({ statement: summary })
        || containsIdentityLabel(summary)) return null;
    return { summary, scopeNote, unknown, insights };
  }

  function normalizeSelfReflectionResponse(value) {
    if (!exactKeys(value, SELF_REFLECTION_RESPONSE_FIELDS)) return null;
    const requestId = safeSelfReflectionRequestId(value.request_id);
    const status = text(value.status);
    const createdAt = text(value.created_at);
    const question = text(value.question);
    const error = value.error === null ? null : text(value.error);
    const errorKind = value.error_kind === null ? null : text(value.error_kind);
    const sourceHashes = normalizeSourceHashes(value.source_hashes);
    const usage = normalizeModelUsage(value.usage);
    if (value.schema_version !== SCHEMA_VERSION
        || !requestId
        || value.kind !== 'self_reflection_response'
        || !SELF_REFLECTION_STATUSES.has(status)
        || !isIsoDateTime(createdAt)
        || typeof value.cache_hit !== 'boolean'
        || !question
        || question.length > 160
        || /[\r\n]/.test(question)
        || containsSensitiveText({ statement: question })
        || !isLocalDate(value.as_of)
        || value.window_days !== 14
        || !Number.isSafeInteger(value.record_days)
        || value.record_days < 0
        || value.record_days > 14
        || !Array.isArray(value.source_hashes)
        || sourceHashes.length !== value.source_hashes.length
        || value.record_days !== sourceHashes.length
        || new Set(sourceHashes.map(item => item.file)).size !== sourceHashes.length
        || !Number.isSafeInteger(value.confirmed_contexts)
        || value.confirmed_contexts < 0
        || (value.usage !== null && !usage)
        || (value.cache_hit && value.usage !== null)
        || (status !== 'error' && value.record_days < 1)) return null;

    if (status === 'error') {
      if (value.cache_hit || !error || !errorKind || value.reflection !== null) return null;
      return {
        schemaVersion: SCHEMA_VERSION,
        requestId,
        kind: value.kind,
        status,
        createdAt,
        cacheHit: value.cache_hit,
        question,
        asOf: value.as_of,
        windowDays: value.window_days,
        recordDays: value.record_days,
        confirmedContexts: value.confirmed_contexts,
        usage,
        error,
        errorKind,
        sourceHashes,
        reflection: null,
      };
    }

    if (error !== null || errorKind !== null) return null;
    const reflection = normalizeSelfReflection(value.reflection, status, question);
    if (!reflection || (status === 'ready' && !sourceHashes.length)) return null;
    const hashedFiles = new Set(sourceHashes.map(item => item.file));
    const citedFiles = new Set(reflection.insights.flatMap(insight => [
      ...insight.evidence,
      ...insight.counterevidence,
    ]).map(item => item.file));
    if ([...citedFiles].some(file => !hashedFiles.has(file))) return null;
    return {
      schemaVersion: SCHEMA_VERSION,
      requestId,
      kind: value.kind,
      status,
      createdAt,
      cacheHit: value.cache_hit,
      question,
      asOf: value.as_of,
      windowDays: value.window_days,
      recordDays: value.record_days,
      confirmedContexts: value.confirmed_contexts,
      usage,
      error: null,
      errorKind: null,
      sourceHashes,
      reflection,
    };
  }

  function normalizeModelUsage(value) {
    if (value === null) return null;
    if (!exactKeys(value, MODEL_USAGE_FIELDS)
        || value.schema_version !== SCHEMA_VERSION
        || value.kind !== 'model_usage'
        || !isIsoDateTime(text(value.timestamp))
        || !text(value.provider)
        || !text(value.model)
        || !(value.request_id === null || (typeof value.request_id === 'string' && value.request_id.length <= 240))
        || typeof value.usage_missing !== 'boolean'
        || !(value.cost_usd === null || (Number.isFinite(value.cost_usd) && value.cost_usd >= 0))
        || !exactKeys(value.pricing, MODEL_PRICING_FIELDS)
        || !isLocalDate(value.pricing.effective_date)) return null;
    for (const field of [
      'prompt_tokens', 'completion_tokens', 'total_tokens', 'prompt_cache_hit_tokens',
      'prompt_cache_miss_tokens', 'reasoning_tokens',
    ]) {
      if (!Number.isSafeInteger(value[field]) || value[field] < 0) return null;
    }
    for (const field of [
      'cache_hit_input_usd_per_million', 'cache_miss_input_usd_per_million',
      'output_usd_per_million',
    ]) {
      if (!Number.isFinite(value.pricing[field]) || value.pricing[field] < 0) return null;
    }
    if (value.usage_missing !== (value.cost_usd === null)) return null;
    return value;
  }

  function verifySelfReflectionBacking(value, sources) {
    const response = value && value.schemaVersion === SCHEMA_VERSION
      ? value
      : normalizeSelfReflectionResponse(value);
    if (!response) return { valid: false, reason: 'contract' };
    if (response.status === 'error') return { valid: true, reason: '' };
    const verifyHashesOnly = () => {
      const lookup = file => sources instanceof Map ? sources.get(file) : sources && sources[file];
      for (const item of response.sourceHashes) {
        const source = lookup(item.file);
        if (!source || source.sha256 !== item.sha256 || !Array.isArray(source.lines)) {
          return { valid: false, reason: source ? 'stale' : 'missing-source', file: item.file };
        }
      }
      return { valid: true, reason: '' };
    };
    if (response.status === 'insufficient_evidence') return verifyHashesOnly();
    const citedEvidence = response.reflection.insights.flatMap(insight => [
      ...insight.evidence,
      ...insight.counterevidence,
    ]);
    if (!citedEvidence.length) return verifyHashesOnly();
    return verifySourceBacking({
      evidence: citedEvidence,
      source_hashes: response.sourceHashes,
    }, sources);
  }

  function selfReflectionConfirmedInsightsMatch(value, activeContexts) {
    const response = value && value.schemaVersion === SCHEMA_VERSION
      ? value
      : normalizeSelfReflectionResponse(value);
    if (!response || response.status !== 'ready') return Boolean(response);
    const contextsById = new Map();
    for (const context of Array.isArray(activeContexts) ? activeContexts : []) {
      const id = safeRecordId(context?.id);
      const statement = text(context?.statement);
      const scope = text(context?.scope);
      if (id && statement && scope) contextsById.set(id, { statement, scope });
    }
    return response.reflection.insights.every(insight => {
      if (insight.kind !== 'confirmed') return true;
      return insight.contextRefs.some(id => {
        const context = contextsById.get(id);
        return context
          && context.statement === insight.statement
          && context.scope === insight.scope;
      });
    });
  }

  function normalizeSelfReflectionFeedback(value) {
    if (!exactKeys(value, SELF_REFLECTION_FEEDBACK_FIELDS)) return null;
    const id = safeSelfReflectionFeedbackId(value.id);
    const requestId = safeSelfReflectionRequestId(value.request_id);
    const action = text(value.action);
    const note = value.note === null
      ? null
      : typeof value.note === 'string' ? value.note.trim() : undefined;
    const createdAt = text(value.created_at);
    if (value.schema_version !== SCHEMA_VERSION
        || !id
        || value.kind !== 'self_reflection_feedback'
        || value.status !== 'pending'
        || !isIsoDateTime(createdAt)
        || !requestId
        || !Number.isSafeInteger(value.insight_index)
        || value.insight_index < 0
        || value.insight_index > 2
        || !SELF_REFLECTION_FEEDBACK_ACTIONS.has(action)
        || (typeof note === 'string' && note.length > 400)
        || (typeof note === 'string' && containsSensitiveText({ statement: note }))
        || (['scope', 'edit', 'changed'].includes(action) && !note)
        || (['accurate', 'reject'].includes(action) && note !== null)
        || !SHA256_RE.test(value.response_sha256)) return null;
    return {
      schemaVersion: SCHEMA_VERSION,
      id,
      kind: value.kind,
      status: value.status,
      createdAt,
      requestId,
      insightIndex: value.insight_index,
      action,
      note,
      responseSha256: value.response_sha256,
    };
  }

  function buildSelfReflectionFeedback({
    id, requestId, insightIndex, action, note = '', responseSha256, now,
  } = {}) {
    const feedback = {
      schema_version: SCHEMA_VERSION,
      id,
      kind: 'self_reflection_feedback',
      status: 'pending',
      created_at: isoNow(now),
      request_id: requestId,
      insight_index: insightIndex,
      action,
      note: ['scope', 'edit', 'changed'].includes(action) && typeof note === 'string'
        ? note.trim()
        : null,
      response_sha256: responseSha256,
    };
    if (!normalizeSelfReflectionFeedback(feedback)) {
      throw new TypeError('这次自我理解校准的字段不完整');
    }
    return feedback;
  }

  function normalizeSelfReflectionRequestRecord(value, fallbackId) {
    const normalized = normalizeSelfReflectionRequest(value);
    return normalized && normalized.id === fallbackId ? normalized : null;
  }

  function normalizeSelfReflectionResponseRecord(value, fallbackId) {
    const normalized = normalizeSelfReflectionResponse(value);
    return normalized && normalized.requestId === fallbackId ? normalized : null;
  }

  function normalizeSelfReflectionFeedbackRecord(value, fallbackId) {
    const normalized = normalizeSelfReflectionFeedback(value);
    return normalized && normalized.id === fallbackId ? normalized : null;
  }

  function selfReflectionRequestFileName(id) {
    const safe = safeSelfReflectionRequestId(id);
    if (!safe) throw new TypeError('主动理解请求 ID 不能用作本地文件名');
    return `${safe}.json`;
  }

  function selfReflectionFeedbackFileName(id) {
    const safe = safeSelfReflectionFeedbackId(id);
    if (!safe) throw new TypeError('主动理解反馈 ID 不能用作本地文件名');
    return `${safe}.json`;
  }

  function normalizeSelfReflectionTagText(value) {
    if (typeof value !== 'string') return '';
    const collapsed = value.replace(PINNED_PROFILE_WHITESPACE_RE, ' ')
      .replace(/^ | $/g, '');
    return collapsed.replace(/[A-Z]/g, character =>
      String.fromCharCode(character.charCodeAt(0) + 32));
  }

  function selfReflectionTagKey(insight) {
    if (!insight || typeof insight !== 'object') return '';
    const statement = normalizeSelfReflectionTagText(insight.statement);
    const scope = normalizeSelfReflectionTagText(insight.scope);
    return statement && scope ? `${statement}\n${scope}` : '';
  }

  function selfReflectionTagId(key) {
    const normalized = typeof key === 'string' ? key : '';
    if (!normalized) return '';
    const hash32 = seed => {
      let hash = (0x811c9dc5 ^ seed) >>> 0;
      for (let index = 0; index < normalized.length; index += 1) {
        hash = Math.imul(hash ^ normalized.charCodeAt(index), 0x01000193) >>> 0;
      }
      hash ^= hash >>> 16;
      hash = Math.imul(hash, 0x85ebca6b) >>> 0;
      hash ^= hash >>> 13;
      hash = Math.imul(hash, 0xc2b2ae35) >>> 0;
      return ((hash ^ (hash >>> 16)) >>> 0).toString(16).padStart(8, '0');
    };
    return `ptag_${hash32(0)}${hash32(0x9e3779b9)}${hash32(0x7f4a7c15)}`;
  }

  function selfReflectionTimestamp(value) {
    const timestamp = Date.parse(typeof value === 'string' ? value : '');
    return Number.isFinite(timestamp) ? timestamp : Number.NEGATIVE_INFINITY;
  }

  function newestSelfReflectionFeedback(items) {
    return [...(Array.isArray(items) ? items : [])].sort((left, right) => {
      const order = selfReflectionTimestamp(right.createdAt)
        - selfReflectionTimestamp(left.createdAt);
      return order || String(right.id).localeCompare(String(left.id));
    })[0] || null;
  }

  function aggregateSelfReflectionEvidence(occurrences, field) {
    const unique = new Map();
    for (const occurrence of occurrences) {
      for (const item of occurrence.insight[field]) {
        const key = `${item.file}\n${item.line}\n${item.quote}`;
        if (!unique.has(key)) unique.set(key, item);
      }
    }
    return [...unique.values()].sort((left, right) => {
      const fileOrder = left.file.localeCompare(right.file);
      return fileOrder || left.line - right.line || left.quote.localeCompare(right.quote);
    });
  }

  function selfReflectionProjectionStatus({
    insight, statusFeedback, hasConfirmedInsight, supportEvidenceDayCount,
  }) {
    if (['edit', 'scope'].includes(statusFeedback?.action)) return 'user_edited';
    if (statusFeedback?.action === 'changed') return 'changing';
    if (statusFeedback?.action === 'accurate') return 'continuing';
    if (['change', 'tension'].includes(insight.kind)) return 'changing';
    if (hasConfirmedInsight || supportEvidenceDayCount >= 3) return 'continuing';
    return 'system_observation';
  }

  function buildSelfReflectionTagProjection(entries) {
    const groups = new Map();
    for (const entry of Array.isArray(entries) ? entries : []) {
      const response = entry?.response?.schemaVersion === SCHEMA_VERSION
        ? entry.response
        : normalizeSelfReflectionResponse(entry?.response);
      const responseHash = typeof entry?.responseHash === 'string' && SHA256_RE.test(entry.responseHash)
        ? entry.responseHash
        : '';
      if (!response || response.status !== 'ready' || !responseHash) continue;
      const feedback = (Array.isArray(entry.feedback) ? entry.feedback : [])
        .map(item => item?.schemaVersion === SCHEMA_VERSION ? item : normalizeSelfReflectionFeedback(item))
        .filter(item => item
          && item.requestId === response.requestId
          && item.responseSha256 === responseHash);
      response.reflection.insights.forEach((insight, insightIndex) => {
        const key = selfReflectionTagKey(insight);
        if (!key) return;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push({
          response,
          responseHash,
          insight,
          insightIndex,
          feedback: feedback.filter(item => item.insightIndex === insightIndex),
        });
      });
    }

    const projected = [];
    for (const [key, occurrences] of groups) {
      occurrences.sort((left, right) => {
        const order = selfReflectionTimestamp(right.response.createdAt)
          - selfReflectionTimestamp(left.response.createdAt);
        const requestOrder = String(right.response.requestId)
          .localeCompare(String(left.response.requestId));
        return order || requestOrder || right.insightIndex - left.insightIndex;
      });
      const latest = occurrences[0];
      const feedbackEvents = occurrences.flatMap(item => item.feedback);
      const rejectFeedback = newestSelfReflectionFeedback(
        feedbackEvents.filter(item => item.action === 'reject')
      );
      const editFeedback = newestSelfReflectionFeedback(
        feedbackEvents.filter(item => item.action === 'edit')
      );
      const scopeFeedback = newestSelfReflectionFeedback(
        feedbackEvents.filter(item => item.action === 'scope')
      );
      const statusFeedback = newestSelfReflectionFeedback(
        feedbackEvents.filter(item => ['accurate', 'changed', 'edit', 'scope'].includes(item.action))
      );
      const evidence = aggregateSelfReflectionEvidence(occurrences, 'evidence');
      const counterevidence = aggregateSelfReflectionEvidence(occurrences, 'counterevidence');
      const contextRefs = [...new Set(occurrences.flatMap(item => item.insight.contextRefs))].sort();
      const supportEvidenceDayCount = new Set(evidence.map(item => item.file)).size;
      const hasConfirmedInsight = occurrences.some(item => item.insight.kind === 'confirmed');
      projected.push({
        tagId: selfReflectionTagId(key),
        key,
        semanticKeyVersion: SELF_REFLECTION_TAG_KEY_VERSION,
        response: latest.response,
        responseHash: latest.responseHash,
        insight: latest.insight,
        insightIndex: latest.insightIndex,
        occurrenceCount: occurrences.length,
        evidence,
        counterevidence,
        contextRefs,
        supportEvidenceDayCount,
        hasConfirmedInsight,
        feedback: rejectFeedback || statusFeedback,
        rejectFeedback,
        editFeedback,
        scopeFeedback,
        statusFeedback,
        hidden: Boolean(rejectFeedback),
        displayStatement: editFeedback?.note || latest.insight.statement,
        displayScope: scopeFeedback?.note || latest.insight.scope,
        status: selfReflectionProjectionStatus({
          insight: latest.insight,
          statusFeedback,
          hasConfirmedInsight,
          supportEvidenceDayCount,
        }),
      });
    }
    return projected.sort((left, right) => {
      const order = selfReflectionTimestamp(right.response.createdAt)
        - selfReflectionTimestamp(left.response.createdAt);
      return order || left.tagId.localeCompare(right.tagId);
    });
  }

  return Object.freeze({
    SCHEMA_VERSION,
    SELF_REFLECTION_TAG_KEY_VERSION,
    ACTIONS,
    safeRecordId,
    containsSensitiveText,
    normalizeCandidate,
    normalizeDecision,
    normalizeOneTimeContext,
    normalizeConfirmedContext,
    verifySourceBacking,
    selectPendingCandidate,
    buildDecisionBundle,
    buildRecoveryDecisionBundle,
    activeConfirmedContexts,
    buildContextPack,
    buildOneTimeContextPack,
    recordFileName,
    jsonRecordsEqual,
    assertCompatibleRecord,
    safeSelfReflectionRequestId,
    safeSelfReflectionFeedbackId,
    normalizeSelfReflectionRequest,
    buildSelfReflectionRequest,
    normalizeSelfReflectionResponse,
    verifySelfReflectionBacking,
    selfReflectionConfirmedInsightsMatch,
    normalizeSelfReflectionFeedback,
    buildSelfReflectionFeedback,
    normalizeSelfReflectionRequestRecord,
    normalizeSelfReflectionResponseRecord,
    normalizeSelfReflectionFeedbackRecord,
    selfReflectionRequestFileName,
    selfReflectionFeedbackFileName,
    normalizeSelfReflectionTagText,
    selfReflectionTagKey,
    selfReflectionTagId,
    buildSelfReflectionTagProjection,
  });
});
