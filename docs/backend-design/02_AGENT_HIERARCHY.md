# 02 · Agent 层级设计

## 1. 总原则

每个 Agent 只理解自己被授权的一层，避免一个大 Agent 同时阅读全部记录、定义主题、判断用户和写入数据库

每层都必须定义：

- 看什么材料
- 能提出什么 action
- 不能做什么
- 何时运行
- 如何停止
- 哪个本地 Workflow 校验它

## 2. Agent 总览

| 层级 | Agent | 理解范围 | 正式输出 |
|---|---|---|---|
| L0 | Capture Understanding Agent | 一条输入在当下是资料、待阅读、判断、决定或待澄清内容 | CaptureDecisionRevision + ResourceCard candidate |
| L1 | Record Interpreter | 一条记录在当时语境中的含义 | RecordInterpretationRevision |
| L2 | Daily Integrator | 同一天记录之间的重复、拆分和关系 | MemoryAtomRevision、RelationRevision |
| L3 | Theme Synthesizer | 同类记忆跨时间反复出现后形成的主题 | ThemeRevision |
| L4 | Self Understanding Agent | 多个主题共同呈现的方向、价值和方式 | SelfInsightRevision |
| L5 | Context Router | 当前外部任务需要调用哪部分“我” | ContextPackCandidate |

Context Router 默认以确定性检索完成。只有召回过多或范围存在歧义时才允许模型 rerank

## 3. L0 · Capture Understanding Agent

### 它解决什么

每条输入先回答一个更基础的问题：这是一段值得进入个人理解的表达，还是一份仅需保存和稍后阅读的资料

L0 由 Agent 做语义判断，同时读取 Workflow 提供的来源、URL、附件类型、是否有用户备注、是否有选中文本和显式快捷动作。它输出候选路由，Workflow 通过校验后提交 `CaptureDecisionRevision`，并约束每条路由能创建的对象

### 正式输出

`CaptureDecisionRevision` 至少包含：

- `content_role`: self_expression / resource / read_later / archive / ambiguous
- `processing_route`: interpret / resource_index / ask_on_use / archive_only / needs_confirmation
- `user_signal_spans`
- `resource_scope`
- `reason_code`
- `confidence`
- source revision、Prompt、Policy 和用户 action watermark

### 五条处理路由

| 路由 | 典型输入 | 立即结果 | 是否进入 L1 |
|---|---|---|---|
| `archive_only` | 无说明的截图、暂存文件、纯附件 | 保存原件、时间和来源 | 否 |
| `resource_index` | 链接、网页截图、文章、PDF | Resource Card、URL、标题、OCR、定位信息 | 仅存在用户判断时进入 |
| `interpret` | 自己的判断、提问、决定、带备注的内容 | SourceRecord + interpretation request | 是 |
| `ask_on_use` | “待会再看”、未读文章、无明确主张的素材 | Read Later intent，等待用户打开或提问 | 否 |
| `needs_confirmation` | 用户信号与资料正文无法可靠区分 | 保留原文并提示用户选择去向 | 用户确认后决定 |

### 用户给出的两个例子

**只截一个链接并说“待会再看”**

```text
保存 URL、页面标题、时间和“待会再看”意图
→ 创建 Read Later intent
→ 不生成 Theme、Memory Atom 或个人理解
→ 用户再次打开、提问或添加备注时再进入阅读链路
```

**截取一个有大量文字的网页**

```text
保存原截图和完整 OCR
→ 创建 Resource Card，保留标题、URL、章节定位和检索索引
→ 若存在高亮或用户备注，只理解该片段与备注
→ 页面全文保持为可按需阅读的资料
→ 用户要求“总结”“与项目关联”或“解释这段”时才运行 Resource Reader
```

L0 不形成长期理解，也不把资料页的观点归因给用户

## 4. L1 · Record Interpreter

### 进入条件

只有 `interpret` 路由，或 `resource_index` 中存在用户选择、高亮、备注、明确提问时，才创建 Interpretation request

### 看到的材料

- 当前 SourceRecordRevision
- 当前记录的来源、时间和附件元数据
- 用户对当前记录的既有校正
- 少量显式目标 Theme 标题，用于候选连接

### 提出的 action

- `propose_interpretation`
- `finish_original_only`
- `finish_insufficient_signal`

### 输出内容

- 内容类型
- 主题候选
- 用户与内容的关系
- 当前认知状态
- 后续用途
- 一个或多个 Memory Atom candidate
- 候选关系
- 精确 SourceSpan

### 禁区

- 直接创建 Theme
- 对用户性格和身份下结论
- 修改原文
- 任意搜索整个 Vault

## 5. L2 · Daily Integrator

### 看到的材料

- 目标日全部有效 Record Interpretation
- 对应原文片段
- 当天已有用户 action
- 受限的近期 Memory Atom 和 Theme 摘要

### 提出的 action

- `merge_memory_atoms`
- `split_memory_atom`
- `propose_relation`
- `mark_duplicate`
- `finish_no_change`
- `finish_insufficient_evidence`

### 正式结果

- 可检索的 Memory Atom
- 支持、反例、修订、范围边界和同主题关系
- 每条 Memory Atom 的来源与适用范围

### R6 确定性基线

- 每条可用 RecordInterpretation 先物化为 MemoryAtom
- 完全相同的既有记忆在新日期再次出现时追加证据并生成 `reinforce` revision
- 同主题 MemoryAtom 生成正式 `same_topic` Relation
- 日级 Candidate 只是原子提交包，正式 Store 只接收 MemoryAtom 与 Relation
- 旧证据和 SourceSpan 必须原样保留，当天运行只能追加本次授权的解释与定位

### 禁区

- 生成用户每日评价
- 直接修改 Self Insight
- 用模型摘要充当独立证据

## 6. L3 · Theme Synthesizer

### 它真正理解什么

Theme 表达用户在一段时间内持续关心、实践或修订的一个方向。它是认知地形中的峰

### 看到的材料

- 通过 Material Gate 的 Memory Atom
- 相关历史 Theme revision
- 支持、反例和范围关系
- 用户确认、修改、删除和 outcome

### 工具

- `read_theme(theme_id)`
- `search_memory_atoms(query, scope)`
- `inspect_relation(relation_id)`
- `propose_theme_patch`
- `finish`

### action

- `new`
- `reinforce`
- `revise`
- `tension`
- `dormant`
- `no_change`

### 初始证据门

- `new` 至少覆盖两个不同日期的正式 Memory Atom
- `reinforce` 必须加入此前未使用的新证据
- `revise` 必须保存旧表述和变化理由
- `tension` 同时绑定支持与反例
- `dormant` 只改变当前活跃状态，不删除历史

具体阈值进入评测后版本化调整

R6 的 0 模型基线使用规范化后的精确 topic 召回。语义近似、同义词和复杂关系识别留给后续 Provider 对照评测；Provider 结果仍需通过相同 material gate、来源校验和 Workflow 权限边界

## 7. L4 · Self Understanding Agent

### 它真正理解什么

这一层回答：多个长期主题共同说明 Memento 目前怎样理解用户，以及用户正在走向哪里

它不创建人格评分，只保留少量能被证据修订的当前理解

### 看到的材料

- 当前 active Theme revision
- Theme 之间的正式关系
- 每个 Theme 的边界、反例和近期变化
- 已有 Self Insight revision
- 用户对 Self Insight 的确认、限定、修改和删除

原始记录只通过受限工具按需核对，不把整个 Vault 放入 Prompt

### 工具

- `read_self_insight(insight_id)`
- `read_theme(theme_id)`
- `trace_theme_evidence(theme_id, limit)`
- `propose_self_insight_patch`
- `finish`

### action

- `new`
- `reinforce`
- `revise`
- `add_boundary`
- `add_tension`
- `dormant`
- `no_change`

### 输出结构

- 一句当前理解
- 关联 Theme refs
- 适用范围
- 仍不确定的部分
- 支持与边界
- 形成时间
- 最近变化理由
- 确认等级

### 确认等级

| 等级 | 内容 | 使用规则 |
|---|---|---|
| draft | 新形成的低风险工作倾向 | 只在 Memento 内展示 |
| observed | 多次出现且边界清楚 | 可在相关任务中提示 |
| user_confirmed | 用户明确确认或修改 | 可进入授权 Context Pack |
| restricted | 身份、关系、情绪等敏感内容 | 未确认时禁止外部调用 |

### 运行条件

满足任一条件才运行：

- 两个以上相关 Theme 发生实质 revision
- 某个 Self Insight 收到新反例或用户校正
- 用户主动点击“重新理解这一部分”
- 外部任务需要一条尚未形成的跨主题理解，且用户允许生成候选

### R7 确定性基线

- 至少两个不同的 active / tension Theme 才能通过 SelfInsight material gate
- 每个 Theme 必须已有两条以上正式 evidence refs
- 单个 Theme、局部偶发行为和资料正文不会被提升为 SelfInsight
- 涉及人格、身份、政治、宗教、健康、情绪、家庭与亲密关系的自动推断立即停止，不写正式对象
- Agent 只能提交 `draft / observed + local_only` 候选，不能覆盖 `user_confirmed` revision
- 用户 action 可以确认、限定 scope、直接修订或撤回；普通确认可进入 `grant_only`，敏感对象持续保持 `restricted`

## 8. L5 · Context Router

### 职责

根据外部任务，从已授权对象中选择最小充分 Context，并明确哪些信息仍未知

### 默认执行

1. 解析任务范围
2. 读取 Grant
3. 确定性检索 Self Insight、Theme、Memory Atom
4. 依据时间、项目、主题和敏感级别过滤
5. 生成 Context Pack
6. 写入读取审计

### 返回内容

- 对任务最相关的当前理解
- 相关 Theme 和 Memory Atom
- 必要的原文引用
- 边界、反例和不确定性
- 数据版本与生成时间
- 允许的写回类型

### 外部写回

外部 AI 可提交：

- `decision`
- `correction`
- `outcome`
- `new_question`
- `session_note`

它们被保存为 ExternalTraceRevision，再进入 L0 和 L1。外部 AI 无权直接执行 `revise_theme` 或 `revise_self_insight`

## 9. Resource Reader · 按需资料理解

Resource Reader 只在用户打开资料后主动调用，用于处理长网页、PDF、截图 OCR 或外部文档

它可以：

- 按用户问题定位相关段落
- 生成可回到原文位置的摘要
- 提取用户明确要求的待办、判断依据或可引用信息
- 将用户最终确认的结论写成新的 SourceRecord

当前 R5 实现将读取结果定义为临时 `ResourceReadResult`。结果必须包含能够在授权文本中重新定位的精确引用；它不进入 RevisionStore。用户明确确认、补充自己的判断或要求继续记录时，后续 Workflow 再创建一条新的 SourceRecord，重新经过 L0 与 L1

它不会：

- 把整篇文章自动写入长期记忆
- 将作者观点当成用户信念
- 因为用户收藏资料就创建 Theme

## 10. Workflow 与 Agent 的共同合同

所有 Agent 共享以下执行顺序：

```text
freeze authorized inputs
→ create immutable request
→ run bounded action loop
→ validate strict schema
→ recheck evidence and source hashes
→ recheck user watermark and target revision
→ commit immutable revision
→ rebuild projections
```

失败、材料过期、预算耗尽和未知 Provider attempt 均为 0 正式写入

每次 Agent 候选完成后，Workflow 都要向 append-only `RunLedger` 写入终态。记录只包含输入快照 hash、candidate hash、Prompt、Policy、usage、提交引用与终态，不复制原文或 Provider 凭据
