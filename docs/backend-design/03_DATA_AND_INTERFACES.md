# 03 · 数据对象与接口

## 1. 对象分层

| 层 | 正式对象 | 说明 |
|---|---|---|
| Source | SourceRecordRevision | 用户实际留下的内容与来源 |
| Routing | CaptureDecisionRevision | 入口 Agent 对输入角色和后续路线的可检查判断 |
| Resource | ResourceCardRevision | 链接、网页、PDF、截图 OCR 等可按需阅读资料 |
| Intent | ReadLaterIntentRevision | 用户明确留下的“待会再看”等轻量意图 |
| Interpretation | RecordInterpretationRevision | 对单条记录的可检查解释 |
| Memory | MemoryAtomRevision | 可被检索、关联和引用的最小记忆 |
| Relation | RelationRevision | 记忆、主题和理解之间的正式关系 |
| Theme | ThemeRevision | 跨时间形成的长期主题，映射为地形峰 |
| Self | SelfInsightRevision | 多个主题共同支持的当前理解 |
| External | ContextGrantRevision、ExternalSession、ContextPackSnapshot、ContextReadAudit、ExternalTraceRevision | 外部 AI 的授权、读取和写回 |
| Projection | ProjectionBundleManifest、Home、Timeline、Landscape、Self、Detail | 前端和外部接口使用的可重建读模型 |

## 2. 新对象

### 2.1 CaptureDecisionRevision

```json
{
  "schema_version": "1.0",
  "kind": "memento_capture_decision_revision",
  "decision_id": "cap_<24hex>",
  "revision": 1,
  "previous_revision_sha256": null,
  "status": "active",
  "operation": "route",
  "source_record_ref": {},
  "content_role": "read_later",
  "processing_route": "ask_on_use",
  "user_signal_spans": [],
  "resource_scope": "whole_resource",
  "reason_code": "explicit_read_later_intent",
  "confidence": "high",
  "needs_user_confirmation": false,
  "prompt_version": "capture-agent-v1",
  "policy_version": "capture-policy-v1",
  "user_action_watermark_sha256": "<sha256>",
  "created_at": "<datetime>",
  "committed_by": "workflow"
}
```

它记录 L0 对输入语义角色和处理路线的正式判断。用户改变路线时追加 revision，旧判断继续可追溯

### 2.2 ResourceCardRevision

```json
{
  "schema_version": "1.0",
  "kind": "memento_resource_card_revision",
  "resource_id": "res_<24hex>",
  "revision": 1,
  "previous_revision_sha256": null,
  "status": "active",
  "operation": "index",
  "source_record_ref": {},
  "resource_type": "web_page",
  "url": "https://example.com/article",
  "title": "从页面 metadata 或用户确认中获得的标题",
  "local_asset_refs": [],
  "ocr_index_ref": "<optional local index>",
  "user_selected_spans": [],
  "user_note": "待会再看",
  "processing_route": "ask_on_use",
  "created_at": "<datetime>",
  "committed_by": "workflow"
}
```

`ResourceCardRevision` 保存资料的可找回性，不表达用户赞同、采用或内化了资料内容

### 2.3 ReadLaterIntentRevision

```json
{
  "schema_version": "1.0",
  "kind": "memento_read_later_intent_revision",
  "intent_id": "rli_<24hex>",
  "revision": 1,
  "previous_revision_sha256": null,
  "resource_ref": {},
  "intent_type": "read_later",
  "created_at": "<datetime>",
  "status": "open",
  "operation": "create",
  "user_note": "待会再看",
  "committed_by": "workflow"
}
```

它只进入待阅读和找回体验，不进入 Theme 和 Self Insight 的证据集合

### 2.4 RecordInterpretationRevision

它是对单条记录的正式、可检查解释，必须绑定 `SourceRecordRevision`、`CaptureDecisionRevision` 和精确 `SourceSpan`

`ready` 与 `needs_review` 状态至少包含一条用户信号定位。`original_only` 只保留原始记录引用，不能保存模型摘要、主题或记忆候选

### 2.5 MemoryAtomRevision 与 RelationRevision

`MemoryAtomRevision` 是可以继续检索、关联和引用的最小正式记忆，至少引用一条正式 RecordInterpretation 和一个 SourceSpan

`RelationRevision` 连接 MemoryAtom、Theme 和 SelfInsight 的精确 revision。每条关系保留方向、类型、形成依据和变化原因；`same_topic` 使用无方向关系，其余类型保持有方向关系

日级 Workflow 使用 `DailyIntegrationCandidate` 作为临时事务包，一次携带待提交的 MemoryAtom revisions 与 Relation revisions。该包绑定目标日期、Prompt、Policy、用户 action watermark 和冻结输入 hash，经再次校验后由 RevisionStore 原子提交。它不拥有正式对象 ID，也不进入 Theme 或 SelfInsight 的证据引用

### 2.6 ThemeRevision

```json
{
  "schema_version": "2.0",
  "kind": "theme",
  "theme_id": "thm_<24hex>",
  "revision": 3,
  "previous_revision_sha256": "<sha256>",
  "title": "证据优先",
  "statement": "面对高风险判断时会先核对来源、范围和反例",
  "scope": "需要形成可复核结论的工作场景",
  "lifecycle": "active",
  "confidence": "observed",
  "evidence_refs": [],
  "evidence_days": ["2026-08-09", "2026-08-18"],
  "counterevidence_refs": [],
  "relation_refs": [],
  "change_reason": "新增跨日期证据后收窄适用范围",
  "policy_version": "theme-policy-v1",
  "prompt_version": "theme-agent-v1",
  "created_at": "<datetime>",
  "committed_by": "workflow"
}
```

Theme 新建至少需要两个不同日期的正式 MemoryAtom。证据数量与日期跨度都由 Schema 和版本化 policy 共同约束

R6 初始 material gate 还要求至少一条连接这些 MemoryAtom 的当前正式 Relation。Theme 修订分别保留支持证据、反例、关系、旧 revision hash 和变化原因；休眠只改变 lifecycle，历史 revision 继续可追溯

### 2.7 SelfInsightRevision

```json
{
  "schema_version": "2.0",
  "kind": "self_insight",
  "insight_id": "sin_<24hex>",
  "revision": 2,
  "previous_revision_sha256": "<sha256>",
  "title": "可验证的结论更值得长期保留",
  "statement": "在重要判断中，会优先保留可追溯依据和允许结论被修订的空间",
  "scope": "产品、研究和需要承担失败成本的工作",
  "uncertainty": "仍需观察低风险探索中的行动偏好",
  "maturity": "observed",
  "confirmation": "observed",
  "theme_refs": [],
  "support_refs": [],
  "boundary_refs": [],
  "change_reason": "研究方法与产品判断两个主题出现了同一证据规则",
  "sensitivity": "normal",
  "visibility": "local_only",
  "policy_version": "self-policy-v1",
  "prompt_version": "self-agent-v1",
  "created_at": "<datetime>",
  "committed_by": "workflow",
  "committing_action_id": null
}
```

SelfInsight 新建至少需要两个不同的 active / tension Theme，且每个 Theme 已经拥有不少于两条正式证据。`confirmation` 使用 `draft / observed / user_confirmed / restricted` 四级状态。Agent 新建只允许 `draft + local_only`，用户确认的普通内容才可变为 `user_confirmed + grant_only`。敏感推断停止自动提交；迁移进入 Store 的敏感对象始终保持 `restricted`

SelfInsight 的每次修订继续保存 `previous_revision_sha256` 与 `change_reason`。RevisionStore 的历史读取只沿已发布 head 链返回 revision，能够排除中断事务留下的未发布文件

用户直接提交的 SelfInsight revision 必须写入 `committing_action_id`。若正式 revision 已提交而 terminal result 写入中断，Workflow 可以在重试时沿已发布历史精确找到该 action 对应的 revision，并补写 applied 结果；其他 action 不能冒用同一 revision

### 2.8 AgentActionCandidate

每层 Agent 使用统一 candidate envelope 提交 `propose_create`、`propose_revise`、`propose_tombstone`、`no_change` 或 `stop`

Candidate 不进入正式证据链。Workflow 必须按目标对象 Schema 重新验证 `proposed_object`，校验来源、policy、用户 watermark 和当前 head 后才能提交 revision

### 2.9 AgentRun 与 ResourceReadResult

`AgentRun` 是 append-only 运行审计。每条记录绑定 run、Prompt、Policy、冻结输入 hash、用户 action watermark、candidate hash、Provider usage、终态与实际提交引用。它不复制原文，也不保存 Provider 凭据

`ResourceReadResult` 是按需资料阅读的临时结果，包含问题、回答、未知项与至少一条精确 SourceSpan。Workflow 会再次核对引用是否存在于授权文本。该结果不属于正式认知对象，不能直接成为 MemoryAtom、Theme 或 SelfInsight

### 2.10 ContextGrantRevision

```json
{
  "grant_id": "grt_<24hex>",
  "revision": 1,
  "previous_revision_sha256": null,
  "client_id": "codex-local",
  "allowed_kinds": ["self_insight", "theme", "memory_atom"],
  "topic_scope": ["Memento", "产品工作"],
  "time_scope": null,
  "max_sensitivity": "normal",
  "allow_source_quotes": true,
  "allowed_writeback": ["decision", "correction", "outcome", "new_question"],
  "expires_at": null,
  "revoked_at": null
}
```

### 2.11 ContextPackSnapshot 与 ContextReadAudit

`ContextPackSnapshot` 是一次外部任务的只读投影，绑定任务范围、Grant、使用的对象 revision、未知项和允许写回的动作

`ContextReadAudit` 记录 client、session、grant、读取的 Context Pack hash、时间和结果状态，不保存外部模型的隐藏推理

### 2.12 ExternalTraceRevision

```json
{
  "trace_id": "xtr_<24hex>",
  "revision": 1,
  "previous_revision_sha256": null,
  "session_id": "ses_<24hex>",
  "client_id": "codex-local",
  "trace_type": "outcome",
  "content": "用户验证后决定保留当前范围",
  "context_refs": [],
  "captured_at": "<datetime>",
  "user_confirmed": false,
  "processing_status": "raw_saved"
}
```

## 3. 关系类型

V2 关系集合：

- `supports`
- `counterexample`
- `revises`
- `scope_boundary`
- `same_topic`
- `derived_from`
- `theme_supports_insight`
- `theme_limits_insight`
- `external_trace_updates_source`

每条关系必须绑定两端的精确 revision。关系自身也使用不可变 revision

## 3.1 R4 本地存储合同

### 正式对象事务

九类已冻结正式对象共享同一套 `RevisionStore`：

```text
校验对象 Schema 与精确 expected_ref
→ 原子追加一组 revision 文件
→ 原子追加 RevisionTransaction manifest
→ 单次替换 FormalHeadIndex
```

RevisionTransaction 是一组 revision 的可见性边界。中断发生在 transaction manifest 之前时，已写 revision 保持不可见；中断发生在 manifest 之后时，`recover()` 可以从完整 transaction 链重建 Head Index。Head Index 只收录精确 `ObjectRef`、revision 路径和 transaction generation

更新现有对象必须提交当前完整 `ObjectRef`。revision、revision SHA 或用户 action watermark 任一变化都会让旧 run 的提交失败。`status`、`lifecycle` 或 `maturity` 已进入 `tombstone` 后，旧 run 无法创建后续 active revision

### 用户 Action 与终态回执

`UserAction` 保存 action、目标对象精确 revision、用户 payload、提交前 watermark 和提交时间。每个 action 最多对应一个不可变 `ActionResult`

```text
actions/inbox/uact_*.json
actions/results/uact_*.json
indexes/user-action-watermark.json
```

Action watermark 由全部 append-only action ID 与文件 SHA 排序计算。索引损坏或中断后可以重建。提交基准或目标 revision 过期时，action 仍保留原始请求，并写入 `conflict` terminal result 与稳定 `reason_code`

### Projection 发布

`BundleStore` 使用以下发布顺序：

```text
staging 写齐全部 Projection 与 Manifest
→ 重读并执行完整跨文件合同校验
→ 原子封存为 immutable bundle
→ 追加 publication
→ 替换 current pointer
```

前端只读取 `projections/current.json` 指向的 sealed bundle。staging 和未发布 bundle 对前端不可见。current pointer 中断后可由完整 publication 链恢复；最新 bundle 损坏时回到上一份通过完整合同校验的 publication。显式 rollback 会追加新的 publication，保留回退动作历史

### 文件安全

- Store 必须由调用方显式传入隔离根目录，R4 没有真实 Vault 默认路径
- 目录与文件均要求当前用户所有，目录 `0700`、文件 `0600`
- 路径必须是根目录内的 POSIX 相对路径，拒绝绝对路径、`..`、反斜线、symlink、hardlink 和非普通文件
- append-only 文件使用同目录临时文件、`fsync` 与无覆盖链接发布
- 原子 replace 只允许用于 `indexes/` 与 `projections/current.json`
- 并发写入通过 owner-only lock 序列化，CAS 决定唯一胜者

## 4. 前端 Projection 合同

### 4.0 ProjectionBundleManifest

Home、Timeline、Landscape、Self、DetailIndex 与全部 Detail Projection 必须以同一 bundle 原子发布。Manifest 保存每份 Projection 的 `projection_id`、相对路径与 SHA-256；所有文件共享 `bundle_id`、`as_of`、`generated_at` 与 `input_sha256`

发布前执行跨文件语义校验：Manifest 必须完整覆盖 bundle 内的文件，Home 对 Timeline、Landscape、Self 的引用 hash 必须与实际文件一致，DetailIndex 必须完整覆盖四类详情。Timeline、Landscape、Self、Home 内的详情入口还必须精确指向 DetailIndex 中同一主体的详情文件。前端读取失败时继续使用上一份合法 bundle

Projector 先按 `as_of` 截取当日可见的正式 revision heads，再计算 `input_sha256` 和所有读模型。同一份 20 天 fixture 固定生成第 1 天、第 5 天、第 11 天和第 20 天四个快照，依次呈现单点、局部关系、局部地形和完整地形；历史 Projection 不会读入未来创建的记录、Theme 或 SelfInsight

`bundle_id` 同时绑定 `as_of`、`generated_at`、可见输入 hash 和上一版链路 hash。相同发布输入得到相同 ID 与 bundle hash；任何发布链路变化都会得到新的身份

### 4.1 LandscapeProjection

地形只读取 Theme，不再直接把 Self Insight 当作峰

```text
peak.theme_ref
peak.evidence_count
peak.counterevidence_count
peak.lifecycle
peak.position
node.memory_atom_ref
edge.relation_ref
```

地形高度来源于版本化的确定性函数，初期只使用证据覆盖、时间跨度和最近变化。视觉位置服务排版，不产生语义结论

初版算法版本固定为 `stable-theme-terrain-v1`。相同可见正式对象、`as_of` 与发布链路元数据必须得到完全一致的位置、高度和 bundle hash，输入排列顺序不得改变结果

### 4.2 SelfProjection

```text
primary_insight
other_insights[]
insights[].insight_ref
insights[].theme_refs[]
insights[].detail_ref
recent_changes[]
boundaries[]
```

这一份 Projection 对应前端“她理解的我”区域。每条 insight 自己携带精确 `theme_refs`；前端只有在用户点击该条理解后，才将这些引用投影为地图上的临时无文字山系。完整字段、状态机、降级策略与验收门见 [10_SELF_INSIGHT_MAP_BRIDGE.md](10_SELF_INSIGHT_MAP_BRIDGE.md)

### 4.3 HomeProjection

Home 只聚合引用：

- 当前 Landscape snapshot
- 当前 Self snapshot
- 今天的记录与处理状态
- 近期变化
- warnings 与 schedule 状态
- Read Later intent 与 Resource Card 的轻量入口

`loading`、`stale`、`conflict` 与 `failed_preserved` fixture 用于验证上一份合法 Home 的展示覆盖层，不作为新的 ProjectionBundle 发布。R4 会把运行与发布状态保存在不可变 bundle 之外

### 4.4 TimelineProjection

`TimelineProjection` 服务“今天的时间河”和按日期回看的记录轨迹，提供时间范围、记录状态、Theme 去向、认知变化日期与分页游标。它不重新解释记录

### 4.5 DetailProjection

详情 Projection 允许前端沿以下路径读取：

```text
Self Insight
→ Theme
→ Memory Atom
→ Record Interpretation
→ Source Record
```

每一层都保留正向和反向引用

详情投影拆分为 `RecordDetailProjection`、`ResourceDetailProjection`、`ThemeDetailProjection` 与 `SelfInsightDetailProjection`，每种投影只服务一个明确前端对象

## 5. 与现有前端合同的兼容

过渡阶段提供 `v2_to_v1_projection_adapter.py`：

- `ThemeRevision` 映射为现有 `understanding_ref`
- Memory Atom 映射为现有 `reusable_memory`
- Relation 保持现有五种前端可识别类型
- SelfProjection 暂时填入现有 fixture 的 `portrait` 区域
- adapter 同时生成当前 V1 `ProjectionAuthority`，把记录、回执、Memory、Relation 与 action watermark 的 head hash 绑定到同一读模型。action watermark 必须由当前 Action Store 显式传入，禁止使用 Projection 输入 hash 冒充

这样产品前端可以先读取真实数据，同时保留当前布局和互动

前端完成 V2 validator 后，再移除 adapter

## 6. MCP / 本地工具接口

首版工具：

| 工具 | 作用 | 默认调用模型 |
|---|---|---|
| `memento.search_context` | 按任务、主题和时间检索 | 0 |
| `memento.get_self_insight` | 读取一条当前理解及其边界 | 0 |
| `memento.get_theme` | 读取主题、依据和变化 | 0 |
| `memento.trace_evidence` | 回到 Memory Atom 和原文定位 | 0 |
| `memento.create_context_pack` | 为当前任务生成最小充分包 | 0，歧义时可 rerank |
| `memento.append_trace` | 写入会话产生的新线索 | 0 |
| `memento.correct_context` | 写入用户明确纠正 | 0 |
| `memento.report_outcome` | 写入现实结果 | 0 |

所有工具先校验 ContextGrantRevision 当前 head。读取结果写入 audit，写入结果进入 ExternalTraceRevision

R8 已实现的本地边界不会打开网络端口，也不接受 Store 或 Vault 路径参数。调用方只能提交 Grant、Session、任务、主题范围和 bounded ProjectionInputs。五个读取工具每次生成一份最长存活五分钟的 Context Pack；三个写回工具只生成 `ExternalTraceRevision + SourceRecordRevision + ContextReadAuditRevision` 的正式原子事务

Grant、Session 与 Pack 绑定精确 revision hash。Pack 中的 SelfInsight 只允许 `user_confirmed + grant_only`，且所有返回引用都必须同时满足对象类型授权、主题范围、时间范围和敏感级别。撤销 Grant 后，其 current revision 发生变化，旧 Session 和旧 Pack 会立即失去后续读取与写回权限

写回结果的下一站固定为 L0 Capture Understanding。外部调用方没有创建或修改 Theme、SelfInsight、Relation 与 MemoryAtom 的工具；现实结果、纠正、决定和新问题必须经过原始记录、入口判断与逐条理解链路，之后才可能参与长期认知更新

## 7. Context Pack

建议输出 Markdown 和 JSON 两种形式：

```text
任务范围
当前最相关的理解
相关主题
已确认约束
关键依据
反例与适用边界
仍未知的部分
数据版本
允许的写回动作
```

Context Pack 是一次任务的投影，不成为新的长期证据

## 8. 版本与迁移

- 所有对象带 `schema_version`
- Agent 输出带 `prompt_version` 与 `policy_version`
- Projection 带 `projection_version`
- 迁移只追加新 revision，不覆盖 V1 文件
- V1 `understanding` 先迁移为 Theme candidate
- fixture 中 `portrait` 只作为 Self Insight 合同样例，不作为真实证据
- 迁移后用 hash 清单证明原始记录与附件未变化
