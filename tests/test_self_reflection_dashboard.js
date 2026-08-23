import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const html = readFileSync(new URL('../chrome-newtab/dashboard.html', import.meta.url), 'utf8');
const css = readFileSync(new URL('../chrome-newtab/dashboard.css', import.meta.url), 'utf8');
const js = readFileSync(new URL('../chrome-newtab/dashboard.js', import.meta.url), 'utf8');
const manifest = JSON.parse(readFileSync(new URL('../chrome-newtab/manifest.json', import.meta.url), 'utf8'));

assert.match(html, /本地认知秘书 · 可追溯理解/);
assert.match(html, /她理解的我 · 认知秘书/);
assert.match(html, /查看能回到来源的倾向，以及它们如何收束成更深的理解/);
assert.doesNotMatch(html, /关于我 · RE:MEMBER|她眼中的我/);
assert.doesNotMatch(html, /data-context-view=|role="tablist"/);
assert.ok(
  html.indexOf('remember-agent-v1-library.js') < html.indexOf('dashboard.js'),
  'Agent V1 strict contract must load before Dashboard'
);
assert.match(css, /\.context-insight-reading/);
assert.match(css, /\.context-memory-card/);
assert.match(css, /\.context-product-status/);
assert.match(css, /\.context-memory-menu/);
assert.match(css, /\.context-agent-trace/);
assert.match(css, /\.context-persona-hero/);
assert.match(css, /\.context-run-now/);
assert.match(css, /\.context-schedule-control/);
assert.match(css, /\.context-integrity-error/);
assert.match(css, /\.context-memory-meta \{[\s\S]*color: var\(--ink-muted\);[\s\S]*font: 10px/);
assert.match(css, /\.context-schedule-copy small \{[\s\S]*font: 10px/);
assert.match(css, /@media \(max-width: 400px\)/);
assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);

// Browser reads and writes only the local Agent V1 inbox/projection. It does
// not possess a provider key or a Provider network route.
assert.match(js, /REMEMBER_AGENT_ROOT_PATH = \['\.context-agent', 'agent-v1'\]/);
assert.match(js, /REMEMBER_AGENT_REQUEST_PATH = \[\.\.\.REMEMBER_AGENT_ROOT_PATH, 'requests'\]/);
assert.match(js, /REMEMBER_AGENT_RESPONSE_PATH = \[\.\.\.REMEMBER_AGENT_ROOT_PATH, 'responses'\]/);
assert.match(js, /REMEMBER_AGENT_RUN_PATH = \[\.\.\.REMEMBER_AGENT_ROOT_PATH, 'runs'\]/);
assert.match(js, /REMEMBER_AGENT_USER_ACTION_PATH = \[\.\.\.REMEMBER_AGENT_ROOT_PATH, 'user-actions'\]/);
assert.match(js, /REMEMBER_AGENT_MEMORY_PATH = \[\.\.\.REMEMBER_AGENT_ROOT_PATH, 'memories'\]/);
assert.match(js, /REMEMBER_AGENT_ENABLE_GATE_NAME = 'enabled'/);
assert.match(js, /REMEMBER_AGENT_SCHEDULE_NAME = 'schedule\.json'/);
assert.match(js, /let rememberAgentV1Enabled = false;/);
assert.match(js, /readRememberAgentV1EnableGate\(context\.handle\)/);
assert.match(js, /isAgentEnableGateBytes\(bytes\)/);
assert.match(js, /File System Access cannot verify POSIX owner\/mode\/link count/);
const gateReadSource = js.slice(
  js.indexOf('async function readRememberAgentV1EnableGate'),
  js.indexOf('function contextSourceFiles')
);
assert.match(gateReadSource, /nestedDirectory\(root, REMEMBER_AGENT_ROOT_PATH, false\)/);
assert.match(gateReadSource, /if \(!directory\) return false/);
assert.match(gateReadSource, /catch \(error\)[\s\S]*return false/);
const refreshSource = js.slice(
  js.indexOf('async function refreshContextAgentData'),
  js.indexOf('let selfReflectionPollAttempts')
);
assert.match(refreshSource, /rememberAgentV1Enabled = agentGateEnabled/);
assert.match(refreshSource, /catch \(error\)[\s\S]*rememberAgentV1Enabled = false/);
const agentPollSource = js.slice(
  js.indexOf('function scheduleRememberAgentPoll'),
  js.indexOf('async function submitRememberAgentRequest')
);
assert.match(
  agentPollSource,
  /if \(!rememberAgentV1Enabled[\s\S]*\|\| activeDrawerId !== 'context-drawer'/,
  'gate 关闭后不得轮询残留的 Agent request'
);
assert.match(js, /readContextJsonFile\(context\.handle, REMEMBER_AGENT_ROOT_PATH, 'profile\.json'\)/);
assert.match(js, /readContextJsonDirectory\(context\.handle, REMEMBER_AGENT_MEMORY_PATH\)/);
assert.match(
  js,
  /readContextJsonFile\(context\.handle, REMEMBER_AGENT_ROOT_PATH, REMEMBER_AGENT_SCHEDULE_NAME\)/
);
assert.match(js, /normalizeSchedule\(agentScheduleResult\.record\.value\)/);
assert.match(js, /agentScheduleState: normalizedAgentSchedule[\s\S]*'invalid'[\s\S]*'absent'/);
assert.match(js, /normalizeMemoryTombstoneRecord/);
assert.match(js, /tombstoneReceipts: normalizedAgentTombstones/);
assert.match(js, /normalizeAgentProfile/);
assert.match(js, /normalizeAgentRequestRecord\(record\.value, record\.fallbackId\)/);
assert.match(js, /normalizeAgentResponseRecord\(record\.value, record\.fallbackId\)/);
assert.match(js, /normalizeAgentRunRecord\(record\.value, record\.fallbackId\)/);
assert.match(js, /normalizeUserActionRecord\(record\.value, record\.fallbackId\)/);
assert.match(js, /verifyAgentArtifacts/);
assert.match(js, /compactSortedJsonText/);
assert.match(js, /policyPayloadCandidatesFromRun\(value\)/);
assert.match(js, /policyValid: expectedPolicies\.includes\(value\.policySha256\)/);

const firstRunSource = js.slice(
  js.indexOf('function rememberAgentFirstInsightMarkup'),
  js.indexOf('function rememberAgentEvidenceMarkup')
);
const legacyControlSource = js.slice(
  js.indexOf('function rememberAgentScheduleEnabled'),
  js.indexOf('function rememberAgentPersonaHeroMarkup')
);
const personaSource = js.slice(
  js.indexOf('function rememberAgentPersonaHeroMarkup'),
  js.indexOf('function rememberAgentFirstInsightMarkup')
);
assert.match(personaSource, /LONG-TERM UNDERSTANDING · COGNITIVE SECRETARY/);
assert.match(personaSource, /<h3 id="context-insight-title">她理解的我<\/h3>/);
assert.match(personaSource, /地景保留它们的短主题/);
assert.match(personaSource, /当前理解/);
assert.match(personaSource, /近期变化/);
assert.match(personaSource, /版本日期/);
assert.doesNotMatch(personaSource,
  /她眼中的我|RE:MEMBER|现在整理|最近 14 天|data-agent-v1-run|data-agent-schedule-toggle/,
  '当前理解列表头部不得沿用旧 Re:member 人格化与手动整理控件');
// 旧控件函数仍作为迁移兼容存在，但当前 hero 不再调用它。
assert.match(legacyControlSource, /data-agent-v1-run/);
assert.match(legacyControlSource, /data-agent-schedule-toggle/);
assert.match(legacyControlSource, /scheduleDisabled = [^;]+\|\| scheduleInvalid/);
assert.match(firstRunSource, /日级归并达到长期证据门/);
assert.match(firstRunSource, /回到主页使用“归并今天”|回到主页使用“积累今天”/);
assert.match(firstRunSource, /等待本地 21:00 计划/);
assert.match(firstRunSource, /不会改写原始日记/);
assert.doesNotMatch(firstRunSource, /Worker|material gate|Agent 请求/);
const currentArticleSource = js.slice(
  js.indexOf('function rememberAgentArticleMarkup'),
  js.indexOf('function legacyInsightArticleMarkup')
);
assert.match(currentArticleSource, /rememberAgentPersonaHeroMarkup/);
assert.match(currentArticleSource, /rememberAgentMemoryGroupsMarkup/);
assert.match(currentArticleSource, /rememberAgentHistoryDetailsMarkup/);
assert.doesNotMatch(currentArticleSource, /rememberAgentControlMarkup|data-agent-v1-run|data-agent-schedule-toggle/,
  '当前已校验理解路径不得混入旧抽屉控件');

// A validated Agent profile remains readable when manual organization is off.
// The gate controls new requests and writes, not which verified profile wins.
const insightRouteSource = js.slice(
  js.indexOf('function contextInsightMarkup'),
  js.indexOf('function bindContextAgentView')
);
assert.ok(
  insightRouteSource.indexOf('if (contextAgentState.agentProfile)')
    < insightRouteSource.indexOf('if (!rememberAgentV1Enabled)'),
  'a verified Agent profile must remain the primary read-only product view'
);
assert.match(insightRouteSource, /selfReflectionRcArticleMarkup/);
assert.match(insightRouteSource, /rememberAgentInvalidProfileMarkup\(\)/);
assert.match(insightRouteSource, /整理能力目前已关闭/);
assert.doesNotMatch(insightRouteSource, /Agent V1 RC 未启用|request 或 user-action/);

const rcFallbackSource = js.slice(
  js.indexOf('function selfReflectionRcArticleMarkup'),
  js.indexOf('function selfReflectionRequestPending')
);
assert.match(rcFallbackSource, /const visibleInsights = visibleSelfReflectionInsights\(\)/);
assert.match(rcFallbackSource, /selfReflectionInsightMarkup\(tag, \{ readOnly: true \}\)/);
assert.match(rcFallbackSource, /compatibilityUnderstandingDetailsMarkup\(\)/);
assert.doesNotMatch(rcFallbackSource, /selfReflectionUpdateNoticeMarkup|contextHistoryDetailsMarkup|contextLegacyDetailsMarkup/);
assert.match(rcFallbackSource, /当前以只读方式显示之前保留的理解/);
assert.doesNotMatch(rcFallbackSource, /Agent V1|RC|Self Reflection|user-action/);

const selfReflectionMarkupSource = js.slice(
  js.indexOf('function selfReflectionInsightMarkup'),
  js.indexOf('function selfReflectionUpdateNoticeMarkup')
);
assert.match(selfReflectionMarkupSource, /\{ readOnly = false \}/);
assert.match(selfReflectionMarkupSource, /const controls = readOnly \? ''/);
assert.match(selfReflectionMarkupSource, /const editor = readOnly \? ''/);

const invalidProfileSource = js.slice(
  js.indexOf('function rememberAgentInvalidProfileMarkup'),
  js.indexOf('function contextInsightMarkup')
);
assert.match(invalidProfileSource, /rememberAgentPersonaHeroMarkup\(0, '校验未通过'\)/);
assert.match(invalidProfileSource, /context-integrity-error/);
assert.doesNotMatch(invalidProfileSource, /现在整理|最近 14 天|RE:MEMBER|她眼中的我/,
  '当前抽屉的安全错误态也必须使用新流程文案');
assert.match(invalidProfileSource, /新结果仍需通过完整校验/);

// Main output is one paper-like article: one paragraph per projected memory,
// no prompt box, chips, tabs, visible tag wall, or conversational loop.
const memoryMarkup = js.slice(
  js.indexOf('function rememberAgentMemoryMarkup'),
  js.indexOf('function rememberAgentUpdateNoticeMarkup')
);
assert.match(memoryMarkup, /data-agent-memory-id="\$\{escapeHtml\(memory\.memoryId\)\}"/);
assert.match(memoryMarkup, /escapeHtml\(memory\.statement\)/);
assert.match(memoryMarkup, /rememberAgentEvidenceMarkup\(memory\)/);
assert.match(memoryMarkup, /context-memory-card/);
assert.match(memoryMarkup, /context-memory-scope/);
assert.match(memoryMarkup, /escapeHtml\(memory\.title\)/);
assert.match(js, /memory\.uncertainty === 'low'/);
assert.match(js, /memory\.provenance\.operation/);
assert.match(js, /rememberAgentLocalDateLabel\(memory\.createdAt, ''\)/);
assert.match(js, /rememberAgentLocalDateLabel\(profile\.projectionUpdatedAt\)/);
assert.doesNotMatch(js, /projectionUpdatedAt\.slice\(0, 10\)|createdAt\.slice\(0, 10\)/);
assert.match(memoryMarkup, /context-memory-menu/);
assert.match(memoryMarkup, /const canManage = rememberAgentV1Enabled[\s\S]*agentProfileAuthoritative/);
assert.match(memoryMarkup, /canManage \? `[\s\S]*data-agent-memory-action="edit"/);
assert.match(memoryMarkup, /canManage \? `<form/);
assert.match(memoryMarkup, /data-agent-memory-action="edit"/);
assert.match(memoryMarkup, /data-agent-memory-action="delete"/);
assert.doesNotMatch(memoryMarkup, /question|chat/i);
assert.match(js, /title: '当前理解'/);
assert.match(js, /title: '近期修订'/);
assert.match(js, /title: '张力与反例'/);
assert.match(memoryMarkup, /if \(!groupMemories\.length\) return ''/);
assert.doesNotMatch(js, /context-ask-panel|context-question-chips|context-question-form/);
assert.doesNotMatch(js, /主动提问|快捷问题|问一个更具体的问题/);
assert.match(js, /count\.textContent = ''/);

// Pending, invalid, terminal error, insufficient evidence, and no-change are
// projected into one compact product status around the prior article.
const articleSource = js.slice(
  js.indexOf('function rememberAgentUpdateNoticeMarkup'),
  js.indexOf('function legacyInsightArticleMarkup')
);
assert.match(articleSource, /context-product-status/);
assert.match(articleSource, /来源已校验/);
assert.match(articleSource, /当前只读/);
assert.match(articleSource, /当前理解没有被覆盖/);
assert.match(articleSource, /没有形成可提交的更新/);
assert.match(articleSource, /当前版本保持不变/);
assert.match(articleSource, /contextAgentState\.agentMemories/);

// Legacy self-reflection is migration-only and read-only in the visible path.
const fallbackSource = js.slice(
  js.indexOf('function legacyInsightArticleMarkup'),
  js.indexOf('function selfReflectionRequestPending')
);
assert.match(fallbackSource, /rememberAgentPersonaHeroMarkup/);
assert.match(fallbackSource, /rememberAgentUpdateNoticeMarkup\(\)/);
assert.doesNotMatch(fallbackSource, /context-profile-notice|contextLegacyDetailsMarkup/);
assert.match(fallbackSource, /data-legacy-reflection-tag/);
assert.doesNotMatch(fallbackSource, /data-reflection-feedback|data-reflection-question/);

// Manual requests are fixed Agent V1 requests and duplicate pending missions
// are rejected before a new request object is built.
const requestSource = js.slice(
  js.indexOf('async function submitRememberAgentRequest'),
  js.indexOf('async function submitRememberAgentSchedule')
);
assert.ok(
  requestSource.indexOf('if (!rememberAgentV1Enabled)')
    < requestSource.indexOf('buildAgentRequest'),
  'disabled RC must stop before building an Agent request'
);
assert.ok(
  requestSource.indexOf('if (rememberAgentRequestPending())')
    < requestSource.indexOf('buildAgentRequest'),
  'pending Agent mission must be rejected before creating another request'
);
assert.match(requestSource, /buildAgentRequest/);
assert.match(requestSource, /newSelfReflectionId\('arq'\)/);
assert.match(requestSource, /REMEMBER_AGENT_REQUEST_PATH/);
assert.match(requestSource, /requestFileName\(request\.id\)/);
assert.match(requestSource, /writeContextJsonAtomically\(directory, fileName, request\)/);
assert.match(requestSource, /手动整理已关闭，本次没有发起核对/);
const requestLockIndex = requestSource.indexOf('withArchiveMutationLock(async () =>');
const requestGateIndex = requestSource.lastIndexOf('readRememberAgentV1EnableGate(context.handle)');
const requestDirectoryIndex = requestSource.indexOf(
  'nestedDirectory(context.handle, REMEMBER_AGENT_REQUEST_PATH, true)'
);
assert.ok(
  requestLockIndex !== -1
    && requestLockIndex < requestGateIndex
    && requestGateIndex < requestDirectoryIndex,
  'mutation lock 内必须先复核 gate，再创建 request 目录'
);

// The schedule is a strict mutable singleton. Missing/invalid means disabled;
// a visible toggle never becomes enabled until the authoritative file is read.
const scheduleBindingSource = js.slice(
  js.indexOf("document.querySelectorAll('[data-agent-schedule-toggle]')"),
  js.indexOf("document.querySelectorAll('[data-agent-memory-action]')")
);
assert.match(scheduleBindingSource, /input\.checked = rememberAgentScheduleEnabled\(\)/);
assert.match(
  scheduleBindingSource,
  /contextAgentState\.agentScheduleState === 'invalid'/
);
assert.ok(
  scheduleBindingSource.indexOf('event.currentTarget.checked = rememberAgentScheduleEnabled()')
    < scheduleBindingSource.indexOf('submitRememberAgentSchedule(enabled)'),
  'schedule switch must stay on its last verified state until the write is reread'
);
const scheduleMutationSource = js.slice(
  js.indexOf('async function submitRememberAgentSchedule'),
  js.indexOf('async function rememberAgentAuthoritativeMemoryStillCurrent')
);
assert.match(scheduleMutationSource, /buildSchedule\(\{ enabled \}\)/);
assert.match(scheduleMutationSource, /withArchiveMutationLock\(async \(\) =>/);
assert.match(scheduleMutationSource, /readRememberAgentV1EnableGate\(context\.handle\)/);
assert.match(scheduleMutationSource, /REMEMBER_AGENT_SCHEDULE_NAME/);
assert.match(scheduleMutationSource, /writeContextJsonReplacementAtomically/);
assert.match(scheduleMutationSource, /expectedScheduleState = contextAgentState\.agentScheduleState/);
assert.match(scheduleMutationSource, /expectedSchedule = contextAgentState\.agentSchedule/);
assert.match(scheduleMutationSource, /normalizeSchedule\(scheduleResult\.record\.value\)/);
assert.match(scheduleMutationSource, /currentScheduleState === expectedScheduleState/);
assert.match(scheduleMutationSource, /canonicalJson\(currentSchedule\)/);
assert.match(scheduleMutationSource, /canonicalJson\(expectedSchedule\)/);
assert.match(scheduleMutationSource, /页面已拒绝覆盖/);
const scheduleLockIndex = scheduleMutationSource.indexOf('withArchiveMutationLock(async () =>');
const scheduleGateIndex = scheduleMutationSource.indexOf(
  'readRememberAgentV1EnableGate(context.handle)'
);
const scheduleWriteIndex = scheduleMutationSource.indexOf(
  'writeContextJsonReplacementAtomically('
);
const scheduleReadIndex = scheduleMutationSource.indexOf('const scheduleResult = await readContextJsonFile(');
assert.ok(
  scheduleLockIndex !== -1
    && scheduleLockIndex < scheduleGateIndex
    && scheduleGateIndex < scheduleReadIndex
    && scheduleReadIndex < scheduleWriteIndex
    && scheduleGateIndex < scheduleWriteIndex,
  'schedule write must stay behind the mutation lock and a fresh master-gate read'
);
const replacementWriterSource = js.slice(
  js.indexOf('async function writeContextJsonReplacementAtomically'),
  js.indexOf('function contextDecisionSuccessMessage')
);
assert.match(replacementWriterSource, /createWritable\(\)/);
assert.match(replacementWriterSource, /await writer\.close\(\)/);
assert.match(replacementWriterSource, /contextJsonEqual\(committed, value\)/);

// Edit/delete emit immutable user-actions bound to revision+hash. Dashboard
// never writes memory revisions directly. Successful delete has no undo.
const actionSource = js.slice(
  js.indexOf('async function submitRememberAgentUserAction'),
  js.indexOf('function scheduleSelfReflectionPoll')
);
assert.match(actionSource, /buildUserAction/);
assert.ok(
  actionSource.indexOf('if (!rememberAgentV1Enabled)')
    < actionSource.indexOf('buildUserAction'),
  'disabled RC must stop before building a user-action'
);
assert.ok(
  actionSource.indexOf('if (!contextAgentState.agentProfileAuthoritative)')
    < actionSource.indexOf('buildUserAction'),
  'stale fallback must stop before building a user-action'
);
assert.match(actionSource, /baseRevision: memory\.revision/);
assert.match(actionSource, /baseRevisionSha256: memory\.revisionSha256/);
assert.match(actionSource, /REMEMBER_AGENT_USER_ACTION_PATH/);
assert.match(actionSource, /userActionFileName\(userAction\.id\)/);
assert.match(actionSource, /完成后不可撤销/);
assert.match(actionSource, /手动整理已关闭，本次没有保存修改/);
const actionLockIndex = actionSource.indexOf('withArchiveMutationLock(async () =>');
const actionGateIndex = actionSource.lastIndexOf('readRememberAgentV1EnableGate(context.handle)');
const actionDirectoryIndex = actionSource.indexOf(
  'nestedDirectory(context.handle, REMEMBER_AGENT_USER_ACTION_PATH, true)'
);
assert.ok(
  actionLockIndex !== -1
    && actionLockIndex < actionGateIndex
    && actionGateIndex < actionDirectoryIndex,
  'mutation lock 内必须先复核 gate，再创建 user-action 目录'
);
const actionCurrentProfileIndex = actionSource.indexOf(
  'rememberAgentAuthoritativeMemoryStillCurrent('
);
assert.ok(
  actionGateIndex < actionCurrentProfileIndex
    && actionCurrentProfileIndex < actionDirectoryIndex,
  'mutation lock 内必须用当前权威 profile 复核 revision/hash 后才能创建 user-action 目录'
);
assert.doesNotMatch(actionSource, /REMEMBER_AGENT_MEMORY_PATH|remember_memory_revision|writeContextJsonAtomically\([^\n]+revision/i);

const actionBinding = js.slice(
  js.indexOf("document.querySelectorAll('[data-agent-memory-action]')"),
  js.indexOf("document.querySelectorAll('[data-reflection-question]')")
);
assert.match(actionBinding, /contextAgentState\.agentProfileAuthoritative/);
assert.match(actionBinding, /button\.disabled = !canMutateAgentMemory/);
assert.match(actionBinding, /if \(!canMutateAgentMemory\) return/);
assert.match(actionBinding, /确认删除/);
assert.ok(
  actionBinding.indexOf("button.dataset.deleteArmed !== 'true'")
    < actionBinding.indexOf("submitRememberAgentUserAction(memoryId, 'delete')"),
  'first delete click only arms inline irreversible confirmation'
);
assert.doesNotMatch(actionBinding, /window\.confirm|confirm\(/);
assert.doesNotMatch(js, /撤销删除|恢复这段/);

const authoritativeReloadSource = js.slice(
  js.indexOf('async function rememberAgentAuthoritativeMemoryStillCurrent'),
  js.indexOf('async function submitRememberAgentUserAction')
);
assert.match(authoritativeReloadSource, /normalizeAgentProfile/);
assert.match(authoritativeReloadSource, /canonicalJson\(currentProfile\)/);
assert.match(authoritativeReloadSource, /verifyProfileEvidence/);
assert.match(authoritativeReloadSource, /currentMemory\.revision === expectedMemory\.revision/);
assert.match(authoritativeReloadSource, /currentMemory\.revisionSha256 === expectedMemory\.revisionSha256/);
assert.match(js, /agentProfileAuthoritative: agentProfileValid/,
  'only the profile validated in the current read may authorize mutations');
assert.match(js, /stale-fallback/);
assert.match(js, /当前只读显示上一版理解/);
assert.match(js, /修改和删除已暂停/);

// Latest-run display is deliberately bounded observability, not CoT.
const traceSource = js.slice(
  js.indexOf('function rememberAgentTraceMarkup'),
  js.indexOf('function rememberAgentHistoryDetailsMarkup')
);
assert.match(traceSource, /actions\.map/);
assert.match(traceSource, /reasonCodes/);
assert.match(traceSource, /modelTurns/);
assert.match(traceSource, /toolCalls/);
assert.match(traceSource, /total_tokens/);
assert.match(traceSource, /cost_usd/);
assert.match(traceSource, /个受控步骤/);
assert.doesNotMatch(traceSource, /<code>\$\{escapeHtml\(action\)\}<\/code>/);
assert.match(js, /<summary>关于这些理解<\/summary>/);
assert.match(js, /<summary>整理详情与用量<\/summary>/);
assert.match(js, /长期理解写入能力当前关闭。现有内容仍可阅读/);
assert.match(js, /21:00 自动计划已保存/);
assert.match(js, /下一计划/);
assert.doesNotMatch(js, /自动整理已开启/);
assert.match(js, /investigate: '判断候选并发起取证'/);
assert.match(js, /plan_evidence: '选择候选与取证计划'/);

assert.equal(
  JSON.stringify(manifest.content_security_policy).includes("connect-src 'none'"),
  true,
  'Dashboard remains without a direct model Provider connection'
);
assert.doesNotMatch(js, /api\.deepseek\.com|DEEPSEEK_API_KEY|Authorization\s*:/);

console.log('✓ Re:member Dashboard: dynamic gate preserves fallback and blocks stale Agent writes');
