# Memento Backend V2 · 前后端统一总契约

> 状态：后端开发前冻结稿
>
> 目标：先定义每一层最终产物、Agent 权限、正式数据、前端投影与合码门槛，再开始编码
>
> 产品终态来源：`docs/MEMENTO_PRODUCT_FINAL_STATE.md`
>
> 产品叙事来源：`docs/MEMENTO_PRODUCT_NARRATIVE.md`
>
> 当前前端兼容目标：`chrome-newtab/cognitive-home-library.js` 与 `chrome-newtab/cognitive-demo-fixture.js`

## 1. 我对最终产品结构的理解

Memento 的主链由五段组成：

```text
接住输入
→ 逐条理解
→ 跨时间形成主题
→ 多个主题收束为“她理解的我”
→ 将相关理解接回真实工作，并接收工作结果继续更新
```

用户看到的认知产品分为三层：

1. **主题地图**：我最近持续在意、实践和修订什么
2. **形成依据**：一个主题由哪些记录、记忆、边界、反例和版本变化形成
3. **她理解的我**：多个主题长期交汇后，Memento 目前怎样理解我的方向、价值排序和工作方式

记录入口和外部工作流属于三层认知的前后两端：前端负责接住和回看，后端负责让输入进入正确层级，并让外部 AI 的使用结果重新回到证据链

## 2. 展示层与计算层的对应关系

展示层按用户理解产品的顺序组织，Agent 按处理尺度组织。两者通过正式对象和 Projection 对接

| 用户所见 | 要回答的问题 | 正式事实来源 | 负责形成的 Agent | 前端只读取 |
|---|---|---|---|---|
| 今日记录与处理状态 | 我留下的内容有没有被接住，它去了哪里 | SourceRecord、CaptureDecision、RecordInterpretation | L0、L1 | HomeProjection、RecordDetailProjection |
| 第一层：主题地图 | 哪些长期主题正在显形，哪里近期变化 | Theme、MemoryAtom、Relation | L2、L3 | LandscapeProjection |
| 第二层：形成依据 | 这个主题怎样形成，哪些证据支持或限制它 | Theme revision、MemoryAtom、Relation、SourceSpan | L2、L3 | ThemeDetailProjection |
| 第三层：她理解的我 | 多个主题共同说明了怎样的我 | SelfInsight、Theme | L4 | SelfProjection、SelfInsightDetailProjection |
| 接回工作 | 当前任务需要调用哪一部分我，本次工作又留下了什么 | ContextGrantRevision、ContextPack、ReadAudit、ExternalTraceRevision | L5、L0/L1 回流 | MCP/Local Tool 与 ExternalSessionProjection |

### 2.1 一个必须长期保持的边界

- Theme 是认知地形中的峰
- SelfInsight 是“她理解的我”中的句子
- MemoryAtom 是从具体记录中整理出的可复用记忆点
- SourceRecord 是用户真实留下的原文和附件
- 前端不根据 MemoryAtom 数量自行生成 Theme，也不根据 Theme 文案自行拼出 SelfInsight

## 3. 每一层最终需要展示什么

### 3.1 入口层：今天留下了什么

主页时间河和记录详情最终显示：

- 时间、来源 App、输入形态和原文摘要
- L0 判断的内容角色：自己的表达、资料、待阅读、纯保存或需要确认
- 当前处理路线与状态
- L1 的一句可检查整理结果
- 形成的 MemoryAtom 数量
- 进入的 Theme，或“尚未进入长期积累”
- 原文、附件、OCR、转写和用户备注
- 用户动作：正确、改一下、只留原文、删除、重新处理

后端不得为了填满卡片而生成解释。资料、待阅读和纯保存内容允许只显示“已保存”及其去向

### 3.2 第一层：主题地图

地形每座主峰最终显示：

- Theme 标题
- 一句当前倾向
- active / tension / dormant 生命周期
- 依据数量、边界或反例数量
- 最近是否发生实质变化
- 确定性计算的位置、高度与地形参数
- 与其他 Theme 的正式关系

地图中的点与线：

- 次峰或实心点：正式 MemoryAtom
- 连线：正式 Relation
- 淡蓝变化状态：本次 Theme revision 发生实质变化
- 等高线：确定性地形函数对证据覆盖、时间跨度和近期变化的投影

位置、高度和等高线属于 Projection，不能写入 Theme 正式 revision

### 3.3 第二层：形成依据

点击 Theme 后，详情最终按照固定顺序展示：

1. 当前倾向与适用范围
2. 最近一次变化：此前怎样理解、现在怎样理解、变化原因
3. 代表性支持证据
4. 边界、反例和仍有张力的部分
5. 相关 MemoryAtom 与主题关系
6. 完整版本历史
7. 可返回的 RecordInterpretation、SourceSpan 和原始记录

这一层负责证明 Theme 怎样形成。它必须支持双向追溯：Theme 可以找到记录，记录也可以找到参与形成的 Theme

### 3.4 第三层：她理解的我

主页右侧和详情最终显示：

- 一条当前最重要的 SelfInsight
- 其余少量 SelfInsight
- 每条理解的一句话、完整 statement 和适用范围
- 来源 Theme 列表
- 支持它的共同模式与限制它的边界
- 仍不确定的部分
- forming / observed / user_confirmed / restricted 状态
- 最近变化原因和历史版本
- 用户动作：确认、限定范围、修改、停止使用、限制外部调用

这层输出保持少量、长期、可修订。一次记录、一篇文章或单个 Theme 都不能直接生成一条正式 SelfInsight

### 3.5 接回工作：可调用的个人记忆

这一层承接的用户价值是 **“让每个 AI，都从同一个你开始”**。Context Pack、ContextGrant、MCP 与读取审计负责把价值落实为可控、可追溯的系统行为。

外部 AI 每次只得到当前任务的最小充分 Context Pack，完整 Vault 始终留在本地：

- 当前任务范围
- 最相关的 SelfInsight
- 相关 Theme
- 已确认约束与偏好
- 关键依据、反例和适用边界
- 未知项与禁止外推项
- 数据版本与授权范围
- 本次允许写回的动作

外部 AI 使用后可以写回决定、纠正、结果或新问题。写回先成为 ExternalTraceRevision，再进入 L0 和 L1，不直接修改 Theme 或 SelfInsight

## 4. Agent 全部设计

### 4.1 统一运行协议

每个 Agent 都遵守同一协议：

```text
Workflow 准备受限上下文和工具
→ Agent 提出结构化 Candidate Action
→ Schema 校验
→ Policy 与 Material Gate 校验
→ CAS 检查来源 revision 与用户 action watermark
→ Workflow 原子提交正式 revision
→ 重建 Projection
```

Agent 没有正式存储写权限，也不能读取任意本地路径。Workflow 负责权限、锁、预算、状态机、提交和回滚

### 4.2 L0 · Capture Understanding Agent

**任务**：判断一条输入在当下扮演什么角色

输入：

- SourceRecord 与附件元数据
- URL、标题、OCR、转写、选中文本
- 用户备注和显式快捷动作
- 来源 App 与捕获方式

输出 Candidate：

- `content_role`: self_expression / resource / read_later / archive / ambiguous
- `processing_route`: interpret / resource_index / ask_on_use / archive_only / needs_confirmation
- `user_signal_spans`
- `resource_scope`
- `reason_code`
- `confidence`

正式提交：`CaptureDecisionRevision`

它不能形成 MemoryAtom、Theme 或 SelfInsight

### 4.3 L1 · Record Interpreter

**任务**：解释用户在这条记录中明确表达了什么

进入条件：L0 路由为 `interpret`，或资料中存在用户高亮、备注、问题和明确采用动作

输出 Candidate：

- 一句 summary
- content types、topics、objects
- 用户 stance 与认知状态
- purposes
- MemoryAtom candidates
- Relation candidates
- 每个判断的精确 SourceSpan

正式提交：`RecordInterpretationRevision`

它不能创建 Theme，也不能对用户人格、身份和跨时间方向下结论

### 4.4 L2 · Daily Integrator

**任务**：将同一天已解释的记录整理成可长期使用的最小记忆

输出 Candidate：

- merge / split MemoryAtom
- duplicate
- supports / counterexample / revises / scope_boundary / same_topic Relation
- no_change / insufficient_evidence

正式提交：`MemoryAtomRevision`、`RelationRevision`

`DailyIntegrationCandidate` 是一次日级提交的事务候选包，负责把多条 MemoryAtom 与 Relation 一起交给 Workflow 校验和原子提交。它不进入正式认知对象集合，也不在用户侧形成每日人格评价

### 4.5 L3 · Theme Synthesizer

**任务**：识别 MemoryAtom 跨时间反复出现后形成的长期主题

触发：Material Change Gate 发现跨日期的新材料、反例、用户修订或现实结果

输出 Candidate：

- new / reinforce / revise / tension / dormant / no_change
- title、statement、scope
- evidence、counterevidence、relations
- change_reason

正式提交：`ThemeRevision`

Theme 新建至少需要两个不同日期的正式 MemoryAtom。阈值由版本化 policy 管理，并通过场景集校准

### 4.6 L4 · Self Understanding Agent

**任务**：从多个 Theme 中形成少量对用户当前方向、价值排序和工作方式的理解

触发：至少两个相关 Theme 发生实质变化，或 SelfInsight 收到新反例、用户校正和主动重算请求

输出 Candidate：

- new / reinforce / revise / add_boundary / add_tension / dormant / no_change
- title、statement、scope、uncertainty
- theme refs、support refs、boundary refs
- maturity、visibility、change_reason

正式提交：`SelfInsightRevision`

敏感内容默认 restricted；用户未确认时不能进入外部 Context Pack

### 4.7 L5 · Context Router

**任务**：按真实工作任务选择最小充分的个人 Context，并管理双向回流

默认使用确定性检索；召回过多或任务范围含糊时才允许受限 rerank

正式输出：

- `ContextPackSnapshot`
- `ContextReadAudit`
- 外部写回后的 `ExternalTraceRevision`

ExternalTraceRevision 必须经过常规入口判断和逐条解释，外部模型不能直接写长期对象

### 4.8 按需 Resource Reader

Resource Reader 是 L0 的按需辅助 Agent。只有用户打开资料并提出“总结、解释、关联项目、寻找依据”等任务时运行

它可以读取受限的资源章节、返回带定位的摘要与候选用户信号；文章作者观点不能被当作用户观点

## 5. 正式对象链

```text
SourceRecordRevision
├── CaptureDecisionRevision
├── ResourceCardRevision
│   └── ReadLaterIntentRevision
└── RecordInterpretationRevision
    └── MemoryAtomRevision
        └── RelationRevision
            └── ThemeRevision
                └── SelfInsightRevision

ContextGrantRevision
└── ContextPackSnapshot
    ├── ContextReadAudit
    └── ExternalTraceRevision
        └── 回到 SourceRecordRevision / CaptureDecisionRevision
```

### 5.1 事实、候选和投影

| 级别 | 能否作为下一层证据 | 能否覆盖 | 示例 |
|---|---|---|---|
| Source | 可以 | 原文 revision 追加，旧版保留 | SourceRecord |
| Candidate | 不可以 | 可丢弃 | Agent action candidate |
| Formal revision | 可以 | 只追加 revision | MemoryAtom、Theme、SelfInsight |
| Projection | 不可以 | 可删除重建 | Home、Landscape、Self、Detail |
| Audit | 只用于审计 | 只追加 | ReadAudit、Run、Usage |

## 6. 前端 Projection 合同

### 6.1 ProjectionBundleManifest

前端每次只读取同一批次的投影，避免 Home、Landscape 和 Self 来自不同 revision

```json
{
  "projection_bundle_id": "pb_<24hex>",
  "projection_version": "memento-product-v2",
  "generated_at": "<datetime>",
  "home": {"path": "home.json", "sha256": "<sha256>"},
  "timeline": {"path": "timeline.json", "sha256": "<sha256>"},
  "landscape": {"path": "landscape.json", "sha256": "<sha256>"},
  "self": {"path": "self.json", "sha256": "<sha256>"},
  "detail_index": {"path": "detail-index.json", "sha256": "<sha256>"},
  "previous_bundle_sha256": "<sha256>"
}
```

发布顺序：先写全部临时 Projection，校验成功后原子替换 Manifest。前端校验任一失败时继续读取上一份合法 bundle。Detail Index 保存各详情投影的对象 ref、路径与 hash

### 6.2 HomeProjection

提供：

- 日期、全局数字和运行状态
- 今天的 RecordCardProjection
- 每条记录的 capture role、route、summary、destination 和 status
- 当前 Landscape 与 Self snapshot 引用
- warnings、schedule 和 stale 状态

### 6.3 LandscapeProjection

提供：

- Theme peaks
- MemoryAtom nodes
- formal Relation edges
- summary 与 recent changes
- 确定性 terrain 参数

### 6.4 TimelineProjection

提供今天的时间河和历史记录轨迹，包括时间范围、记录状态、Theme 去向、认知变化日期和分页游标

### 6.5 ThemeDetailProjection

提供：

- 当前 Theme revision
- previous / current / change reason
- representative evidence
- counterevidence、boundaries、relations
- revision history
- trace path 到 SourceRecord

### 6.6 SelfProjection

提供：

- primary insight
- other insights
- 每条 insight 的 maturity、scope、uncertainty 和 theme refs
- recent changes

### 6.7 SelfInsightDetailProjection

提供：

- 完整 SelfInsight
- 来源 Theme 和共同模式
- support / boundary / tension
- revision history
- external visibility 与授权状态

### 6.8 RecordDetailProjection 与 ResourceDetailProjection

Record detail 提供 CaptureDecision、RecordInterpretation、MemoryAtom、Theme destination、原文和附件

Resource detail 提供 URL、标题、OCR 索引、用户高亮、Read Later 状态、按需阅读任务和来源定位

### 6.9 当前前端到 V2 的迁移映射

| 当前前端区域 | 当前来源 | V2 正式来源 | 过渡方式 |
|---|---|---|---|
| 顶部主题、变化、记录数字 | fixture 与 Home/Landscape summary | HomeProjection.stats | adapter 先聚合现有 summary |
| 今天的时间河 | `home.records[]` | HomeProjection.records[] | 增加 capture role、route 和 destination 字段 |
| 认知地形 | `landscape.peaks/nodes/edges`，峰使用 `understanding_ref` | LandscapeProjection 的 `theme_ref/memory_atom_ref/relation_ref` | V2 → V1 adapter 将 Theme 映射为 understanding_ref |
| 地图主题详情 | profile memory 与 fixture themes | ThemeDetailProjection | 合码时切换详情读取器 |
| “她理解的我” | fixture `portrait[]` | SelfProjection | 保留现有视觉组件，替换数据源和 validator |
| 深层理解详情 | fixture portrait 与 themeIds | SelfInsightDetailProjection | 以正式 insight/theme refs 替换 fixture id |
| 记录到主题的去向 | `record.understanding_refs[]` | RecordCardProjection.theme_refs[] | adapter 负责旧字段映射 |
| 用户修改与删除 | 现有 action 文件 | V2 append-only Action + terminal result | 保留 inbox 模式，扩展 action kind |

原生 V2 合同启用后，`understanding_ref` 只保留在 V1 adapter，不再进入 V2 Schema

## 7. 前端稳定读取与动作接口

### 7.1 读取接口

```text
readProjectionManifest()
readHome()
readLandscape()
readSelf()
readTimeline(range)
readRecordDetail(recordId)
readResourceDetail(resourceId)
readThemeDetail(themeId)
readSelfInsightDetail(insightId)
readExternalSession(sessionId)
readRunStatus(runId)
```

### 7.2 用户动作接口

```text
submitAction(action)
pollActionResult(actionId)
requestRun(kind, scope)
```

允许的产品动作：

| 对象 | 动作 |
|---|---|
| CaptureDecision | change_route、mark_read_later、interpret_selection |
| RecordInterpretation | confirm、edit、original_only、retry、delete |
| MemoryAtom | edit、merge、split、delete |
| Theme | confirm、edit_statement、edit_scope、merge、split、dormant、delete、recheck |
| SelfInsight | confirm、edit、add_boundary、restrict、dormant、delete、recheck |
| Resource | mark_read、keep_later、ask、archive、delete |
| ContextGrantRevision | create、narrow、revoke |
| ExternalTraceRevision | confirm、correct、ignore、delete |

每个动作带 target revision 和 hash。目标已变化时返回 conflict，前端提示用户重新查看，不自动套用旧修改

## 8. 工作流与状态

### 8.1 主状态

```text
raw_saved
→ routing
→ routed
→ processing
→ ready
→ merged
→ themed
→ reflected
```

任一阶段还可以进入：

- needs_review
- original_only
- no_candidate
- no_change
- failed_preserved
- stale
- conflict
- budget_exhausted

这些状态保留原文和上一份正式结果，不制造半提交对象

### 8.2 调度

| 任务 | 触发 | 幂等键 |
|---|---|---|
| route capture | 保存后 | source revision + user action watermark |
| interpret record | route=interpret | source + capture decision revision |
| consolidate day | 手动或每日一次 | local date + receipt head hash |
| update themes | material gate | memory/relation head hash |
| update self | theme material gate | theme head hash |
| rebuild projections | formal head 改变 | all projection input hashes |
| external writeback | client 显式提交 | grant + session + trace payload hash |

## 9. 存储和安全

- 用户原文和附件留在本地可读目录
- 领域对象使用 append-only JSON revision
- Projection、head index 和 SQLite 检索索引可以重建
- Provider Key 只在本地 Worker
- 模型工具只能使用对象 ID 和受限 query，不能传任意路径
- source quote 在 commit 前逐字核验
- 用户删除、限制和纠正优先于旧 Agent run
- MCP 的每次读取和写回都绑定 grant、session、scope 和 audit
- 未授权的 sensitive / restricted 信息不能进入 Context Pack

## 10. 评测与真实场景集

场景集先用于产品验收、Prompt 校准和模型比较。首轮不进行微调

必须覆盖：

- URL + “待会再看”
- 长网页截图，无备注
- 网页高亮 + 用户判断
- AI 对话中的个人判断
- 语音中的模糊意图
- 同一主题跨日期重复
- 新证据强化、收窄或反驳 Theme
- 多个 Theme 形成 SelfInsight
- 敏感理解被拦截
- 用户修订使旧 run 失效
- 外部 AI 读取、写回、纠正和现实结果
- Provider 状态未知、Projection 损坏和恢复

质量门：

- 正式理解来源引用有效率 100%
- 任何 SelfInsight 均能回溯到 Theme → MemoryAtom → Source
- 资料误写为用户观点的比例低于预注册阈值
- 用户删除或修改后旧结果复活次数为 0
- V2 → V1 adapter 全部通过当前前端 validator
- 新旧 Projection 切换不改变原始记录 hash

## 11. 后端开发阶段

### B0 · 冻结合同

- 完成本总契约与 JSON Schema
- 冻结 36 个场景和 20 天回放 fixture
- 完成 ProjectionBundleManifest 与 V2 → V1 adapter 合同
- 0 模型生成可被前端读取的完整 bundle

### B1 · 本地事实与 revision store

- 正式对象、ID、ObjectRef、CAS、tombstone
- 原子写入、head 重建、中断恢复
- action inbox 与 terminal result

### B2 · 入口判断、资料与逐条理解

- Capture Understanding Agent
- Resource Card、Read Later、Resource Reader
- Record Interpreter
- 记录详情 Projection

### B3 · 日级整理与长期主题

- Daily Integrator
- MemoryAtom、Relation
- Theme Synthesizer
- Landscape 与 Theme detail Projection

### B4 · 她理解的我

- Self Understanding Agent
- Self material gate 与敏感策略
- Self 与 SelfInsight detail Projection

### B5 · 接回工作

- ContextGrantRevision、Context Router、Context Pack
- MCP、本地工具、读取审计
- ExternalTraceRevision 双向回流

### B6 · 影子运行

- 真实 Vault 只读快照
- 候选与当前结果对照
- 误连、漏连、过度推断、停止质量、成本与延迟报告

### B7 · 前后端合码

- feature flag 接入 V2 bundle
- 先走 V1 adapter，再接 V2 原生 validator
- 接详情和用户 action
- fixture 永久保留为演示与回归数据

## 12. 前后端对齐的硬门槛

开始合码前必须同时满足：

1. 每个视觉模块都有唯一 Projection 来源
2. 每个按钮都有明确 action contract 和 terminal result
3. empty、loading、stale、conflict、failed_preserved 均有前端文案与 fixture
4. Theme 和 SelfInsight 在对象、ID、Projection 和 UI 中全部分离
5. 任何详情都能沿正式引用回到 SourceRecord
6. Home、Timeline、Landscape、Self 和 Detail Index 由同一 ProjectionBundleManifest 发布
7. 当前前端不需要读取 Provider、Prompt、run 或正式 Store 目录
8. 回滚 feature flag 后仍可读取上一份合法前端数据

## 13. 明确暂缓的内容

- 云端账户与远程数据库
- 自动微调个人模型
- 用向量相似度直接提交正式关系
- 未经确认的敏感人格推断
- 将整篇网页自动写入个人长期理解
- 让外部 AI 直接写 Theme 或 SelfInsight
- 视觉坐标反向改变认知语义

## 14. 已冻结内容与待校准参数

开发前已经冻结：

- 三层用户展示与六级 Agent 的职责边界
- Source → CaptureDecision → Interpretation → MemoryAtom → Theme → SelfInsight 的对象链
- Theme 与 SelfInsight 分离
- Agent 只提 Candidate、Workflow 校验提交
- ProjectionBundleManifest 原子发布
- 前端只读 Projection、只写 Action
- 外部 Context 的授权、审计和双向回流方式

需要通过场景集和影子运行校准：

- Theme 新建所需日期数、证据数和时间跨度
- SelfInsight 的 material gate 与主页最大展示数量
- L0 低置信度时自动保存、询问或延后处理的阈值
- sensitive / restricted 分类细节
- 每层运行时模型、Prompt、Token 预算和超时
- 代表证据与反例在详情中的排序

这些项目全部进入版本化 Policy 和评测报告。校准不会改变对象边界和前端接口

完成 B0 后，后端代码才能开始。后续任何字段、Agent 权限和前端展示变化都先修改本总契约，再进入实现
