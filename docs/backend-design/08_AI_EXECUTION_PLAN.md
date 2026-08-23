# 08 · AI 执行计划

> 目标：让 AI 按可审计、可暂停、可回滚的顺序独立完成 Memento Backend V2，再通过稳定合同与现有前端合码
>
> 开始位置：B0 合同冻结
>
> 默认开发模型：GPT-5.6 Terra · medium

## 1. 最合适的开始方式

先完成一个可以运行测试、可以生成静态 Projection、完全不调用模型的后端骨架

首轮只解决四件事：

1. 建立独立的 `backend/` Python package
2. 冻结正式对象、revision、Projection 和 action 的 JSON Schema
3. 用固定 fixture 生成一份完整 Projection bundle
4. 验证这份 bundle 可以通过 V2 合同，并能经 adapter 被当前 V1 前端合同接受

这一轮暂不处理：

- 真实 Vault 写入
- Provider 与模型 Prompt
- Agent 自动提交正式理解
- 前端页面修改
- MCP 对外开放
- 视觉组件重做

这样可以先固定前后端共同使用的语言。后续 Agent、存储和模型都围绕同一套对象合同开发，减少做到中途才发现字段和页面无法对应的风险

## 2. 开发组织方式

### 2.1 一条主线，阶段复核

后端采用单主线顺序开发。对象 Schema、Store 和 Projection 不并行改写

```text
主开发 AI
  ↓ 完成一轮实现与测试
阶段复核
  ↓ 通过质量门
下一轮主开发
```

推荐配置：

| 角色 | 模型 | 使用位置 |
|---|---|---|
| 主开发 | GPT-5.6 Terra · medium | 日常实现、测试、修复、文档同步 |
| 阶段复核 | GPT-5.6 Sol · high | B0、B1、B4、B5、B7 结束时 |
| 机械整理 | GPT-5.6 Luna · medium，可选 | 测试日志归类和低风险格式整理 |

开发模型与 Memento 产品运行时模型分开管理。产品 Agent 使用哪个 Provider，要等离线评测集能够稳定区分质量后再选择

### 2.2 独立开发边界

开始编码前先保存当前前端的可恢复基线，再建立 `codex/backend-v2` 分支或独立 worktree

后端阶段允许修改：

```text
backend/**
docs/backend-design/**
```

后端阶段保持不动：

```text
docs/index.html
docs/assets/product/**
chrome-newtab/dashboard.html
chrome-newtab/dashboard.css
chrome-newtab/dashboard.js
docs/Memento-Cognitive-Home-Standalone.html
```

现有 `context-agent/` 先作为只读实现参考。明确进入兼容层提取任务后，才对选定文件做小范围改造

## 3. 每轮 AI 必须遵循的执行协议

每个执行轮次都使用同一份任务结构：

```text
目标
权威输入
允许修改
禁止修改
交付文件
验证命令
停止条件
完成报告
```

AI 开始工作前必须：

1. 阅读 `00_BACKEND_MASTER_PLAN.md` 和本轮对应专题文档
2. 检查 `git status`，标记既有用户改动
3. 列出本轮会创建或修改的文件
4. 明确本轮是否允许模型调用和正式写入

AI 完成工作前必须：

1. 运行本轮指定测试
2. 运行已有回归测试
3. 检查 Schema、代码和文档命名一致性
4. 生成 `backend/reports/Bx_STATUS.md`
5. 区分“文件完成”“测试通过”“影子运行通过”“真实写入启用”四种状态

出现以下情况立即停止当前轮次：

- 需要改变三层产品展示或六级 Agent 边界
- 需要新增正式对象类型
- 当前前端字段与总合同发生冲突
- 测试只能通过放宽来源、权限或 revision 约束
- 需要读取或改写真实 Vault 才能继续
- 用户已有改动与本轮目标重叠且无法安全保留

## 4. 分阶段 AI 执行路线

### R0 · 基线审计与工程入口

目标：建立安全的后端独立开发环境

创建：

```text
backend/README.md
backend/pyproject.toml
backend/src/memento_backend/__init__.py
backend/tests/conftest.py
backend/reports/R0_BASELINE.md
```

检查：

- 当前 Python 版本和测试运行方式
- `context-agent/` 中可复用的 ID、hash、revision、atomic write 机制
- 当前前端 validator 和 fixture 的真实字段
- 前端基线是否已可恢复

验收：

- `pytest` 可以启动
- 空测试集与 lint/type-check 入口明确
- 没有修改产品前端
- 没有真实数据和模型调用

### R1 · 基础对象与输入合同

目标：先冻结“发生了什么”和“入口怎样判断”

实现：

- ID、ObjectRef、SourceSpan、revision metadata
- SourceRecordRevision
- CaptureDecisionRevision
- ResourceCardRevision
- ReadLaterIntentRevision

主要文件：

```text
backend/src/memento_backend/domain/ids.py
backend/src/memento_backend/domain/refs.py
backend/src/memento_backend/domain/revisions.py
backend/src/memento_backend/schemas/source-record-v2.schema.json
backend/src/memento_backend/schemas/capture-decision-v1.schema.json
backend/src/memento_backend/schemas/resource-card-v1.schema.json
backend/src/memento_backend/schemas/read-later-intent-v1.schema.json
backend/tests/contracts/test_source_record_contract.py
backend/tests/contracts/test_capture_decision_contract.py
backend/tests/contracts/test_resource_card_contract.py
```

质量门：

- 未知字段拒绝
- 原文、时间、来源和附件引用完整
- 资料卡与个人理解对象明确分离
- “链接 + 待会再看”和长网页场景通过合同测试

### R2 · 认知对象合同

目标：冻结逐条理解、日级记忆、长期主题和第三层理解之间的对象关系

实现：

- RecordInterpretationRevision
- MemoryAtomRevision
- RelationRevision
- ThemeRevision
- SelfInsightRevision
- Candidate action envelope

质量门：

- Theme 必须引用 MemoryAtom 或正式 Relation
- SelfInsight 必须引用多个 Theme 或达到明确 material gate
- 每条正式理解都可以沿引用回到 SourceRecord
- 模型输出只能成为 candidate
- 用户修订拥有更高优先级

### R3 · Projection 与当前前端兼容合同

目标：在 0 模型条件下生成完整可视结果

实现：

- ProjectionBundleManifest
- HomeProjection
- TimelineProjection
- LandscapeProjection
- SelfProjection
- DetailIndex 与四类 detail projection
- V2 → V1 adapter

同时固定：

- 20 天回放 fixture
- empty、loading、stale、conflict、failed_preserved fixture
- 认知地形的确定性布局

质量门：

- 相同正式对象生成完全相同的 bundle hash
- 所有 Projection 由同一 manifest 发布
- Theme 和 SelfInsight 使用独立 ID 与详情入口
- V1 adapter 通过当前前端 validator
- 地形可以回到形成它的记录和关系

R3 完成后进行第一次 Sol high 合同复核。复核通过即完成 B0

### R4 · Revision Store 与 Action Store

目标：让正式对象能够安全落盘、修订和恢复

实现：

- AtomicFileStore
- RevisionStore
- HeadIndex
- BundleStore
- append-only ActionInbox
- CAS、tombstone、失效与恢复

质量门：

- 中断不会产生半份正式 bundle
- stale action 被拒绝并保留原因
- 删除对象不会被旧 run 复活
- head 可以由 revision 重建
- 非法路径和权限错误 fail-closed

### R5 · L0 与 L1：入口判断和逐条理解

目标：让不同输入先得到正确分流，再形成克制的单条理解

实现：

- Capture Understanding Agent
- Record Interpreter
- Resource Reader
- Provider Protocol
- prompt、policy、run 和 usage 版本记录

首批场景：

1. 普通文字判断
2. 链接加“待会再看”
3. 长网页截图
4. 网页高亮加用户备注
5. 纯资料，无个人判断
6. 语音记录
7. AI 对话片段
8. 模糊输入与低置信度输入

执行顺序：

```text
规则与 fixture
→ 离线 Agent 测试
→ 隔离 Provider
→ 候选结果对照
→ 达标后允许写入隔离 Vault
```

质量门：

- 资料不会被自动写成用户信念
- 原文始终先保存
- 模型失败不影响原始记录
- 低置信度时能保存、询问或延后
- 每个 candidate 都有 Prompt、Policy、来源和 usage

### R6 · L2 与 L3：日级整理和长期主题

目标：让同类记忆跨时间连接，形成可追溯主题

实现：

- Daily Integrator
- Theme Synthesizer
- material gate
- Theme 新建、强化、收窄、张力、休眠和恢复
- Landscape 与 Theme detail projector

质量门：

- 单条偶发记录不形成长期主题
- 关系跨越多个日期后才进入 Theme
- 支持证据、反例和变化原因同时保留
- 20 天 fixture 能从点、局部地形逐步形成完整认知地形
- 地形动画只读取不同时间点的 Projection，不改认知语义

R6 离线实现固定为四个回放节点：第 1 天显示单点，第 5 天显示首个跨日关系与局部地形，第 11 天继续积累新点，第 20 天形成三座可回溯主题峰。前端动画只在这些确定性 Projection 之间切换

### R7 · L4：她理解的我

目标：由多个主题形成少量、可修订、可撤回的当前理解

实现：

- Self Understanding Agent
- SelfInsight material gate
- 敏感推断策略
- 用户确认与修改 action
- SelfProjection 与 SelfInsight detail

质量门：

- 每条判断都展示形成依据和版本变化
- 局部行为不会直接上升为完整人格判断
- 敏感内容默认不进入外部 Context
- 用户可以修订、限定范围或撤回
- 旧版本与变化理由继续可查

R7 完成后进行 Sol high 证据链与过度推断复核

R7 离线合同实现已完成：确定性 Self Understanding Agent 只处理受限 Theme heads；Workflow 复核 material gate、敏感词、引用、当前 revision 与用户 action watermark。普通用户确认可进入 `grant_only`，敏感对象持续 `restricted`；历史 revision 通过已发布 head 链查询。产品模型、真实样本和影子 Vault 尚未启用

### R8 · L5：接回工作与双向 Context

用户价值：让每个 AI 都从同一个你开始

产品能力：把 Memento 对用户的长期理解整理成可调用的个人记忆，并按当前任务返回最小充分内容

实现边界：ContextGrant、MCP、调用范围与读取审计继续约束每次访问，完整 Vault 保持在本地

实现：

- ContextGrantRevision
- Context Router
- ContextPackProjection
- MCP read tools
- ContextReadAuditRevision
- ExternalTraceRevision
- correction、outcome 和新线索回流

链路：

```text
外部 AI 请求当前任务 Context
→ Context Router 按主题、时间和敏感级别检索
→ 用户授权范围内返回 Context Pack
→ 外部会话留下 outcome、correction 或新线索
→ ExternalTraceRevision
→ 重新进入 L0 / L1
→ 经证据链更新 Theme 或 SelfInsight
```

质量门：

- 无授权、过期授权和越界请求全部拒绝
- 每次读取与写回都有审计
- 外部 AI 无法直接修改 Theme 或 SelfInsight
- 新痕迹能回到具体 client、session 和任务
- 撤销授权后停止后续读取

R8 完成后进行 Sol high 权限与 MCP 安全复核

R8 已关闭：ContextGrant、ExternalSession、Context Pack、read/writeback audit、ExternalTrace 回流与八个本地 allow-list 工具已通过合成合同测试和独立 Sol high 权限与 MCP 安全复核。复核补齐 task 精确绑定、完成时到期检查、严格布尔确认、Pack read-audit 绑定、Context ref 类型校验、Source quote 时间过滤与工具参数字段 allow-list。实现没有网络 transport、产品模型或真实 Vault 默认路径；当前停止在 R8，不进入 R9

### R9 · 真实数据影子运行

目标：在不改写正式 Vault 的条件下验证真实质量

运行：

- 对真实 Vault 创建只读快照
- 新后端只生成 shadow candidate 与 shadow projection
- 将候选结果与现有数据对照
- 统计误连、漏连、过度推断、停止质量、成本和延迟

启用正式写入前必须由用户确认：

- 可以接受的误连与漏连范围
- Theme 和 SelfInsight material gate
- 敏感信息默认策略
- 每日和长期 Agent 的运行频率
- 产品运行时 Provider 与成本上限

R9 基础设施已完成合成合同实现：可将用户确认的数据范围、质量阈值、Theme / SelfInsight Gate、敏感策略、Agent 频率、Provider 与预算封为确定性 `shadow-consent-v1`，并将 consent hash 精确绑定到密封只读快照、预注册 plan 与 sealed report。`shadow-case-set-v1` 预登记每例精确输入、标准答案与检查分母；`ShadowProducer` 只接收无 gold 的 bounded input；`shadow-work-product-v1` 绑定 plan、case set、snapshot、预测、usage 与候选 ProjectionBundle。评估器独立合并两份证据，计算误连、漏连、过度推断、停止质量、证据回溯、资源误归因、旧对象复活、adapter、原文 hash、成本与延迟，再以只读原子目录封存全部证据

当前已完成合成端到端 worker 演练，并新增签发授权前的元数据预检：它一次检查 consent 草案、规范路径、真实目录形态、12—15 条场景、停止正例与全部质量分母，同时保持零正文读取、零 Provider、零快照和零写入。真实 Vault 尚未读取，产品 Provider 尚未调用，也没有产生真实质量结论。手工 observation 只能得到 `infrastructure_only`，无法进入真实质量终态。待确认内容已汇总到 `09_R9_USER_CONFIRMATION.md` 与 `backend/eval/consent-template.json`。用户尚未确认真实数据授权、质量阈值、Theme / SelfInsight material gate、敏感策略、Agent 频率、Provider 和预算，因此 R9 仍保持打开，不进入 R10

真实 Provider 接线已补齐为 fail-closed 边界：`bind_provider_shadow_producer(plan, consent, producer)` 只接受已确认的真实 Vault `provider_shadow` plan，并将产品实际 worker 报告的每条 usage 锁定到 consent 中的 Provider 与模型。它不含凭据、网络 client、泛化 Prompt 或真实数据路径；候选 ProjectionBundle 必须仍由产品 Agent / Workflow 图生成，避免评测中出现第二条未经审查的运行路径

### R10 · 前后端合码

目标：保留现有视觉设计，只替换数据源并接入真实动作

合码顺序：

```text
fixture
→ V1 adapter
→ V2 shadow
→ V2 live
```

新增前端接线文件：

```text
chrome-newtab/cognitive-v2-contract.js
chrome-newtab/cognitive-v2-data-source.js
chrome-newtab/cognitive-v2-actions.js
tests/test_cognitive_v2_contract.js
tests/test_cognitive_v2_integration.js
```

质量门：

- feature flag 可以随时回到上一份合法数据
- 页面只读取 Projection，不读取 Store、Prompt 或 Provider 状态
- 所有按钮都有 action terminal result
- 现有 fixture 永久保留用于演示与回归
- 原始记录 hash 在新旧数据源切换时不变

## 5. 场景集何时由用户参与

AI 可以先建立合成场景和标注模板。进入 R5 前，需要用户提供 12—15 条匿名化真实样本，覆盖自己最常见的记录方式

每条样本只需要提供：

```text
原始输入
来源场景
当时真正想做什么
希望系统留下什么
明确不希望系统推断什么
几天后它是否应该进入长期主题
```

这些样本用于评测和 Prompt 校准，不直接当作模型训练集。先验证 Agent 能否做出正确取舍，再决定是否需要更大的场景库

## 6. 每轮状态报告模板

```markdown
# Rn Status

## 本轮目标
## 实际修改文件
## 完成状态
- 文件已创建：是 / 否
- 合同测试通过：是 / 否
- 回归测试通过：是 / 否
- 模型评测通过：未运行 / 是 / 否
- 影子运行通过：未运行 / 是 / 否
- 真实写入启用：否 / 是

## 测试证据
## 已知缺口
## 合同是否变化
## 下一轮允许开始的条件
```

## 7. 第一条可直接执行的 AI 任务

```text
执行 R0：建立 Memento Backend V2 的独立工程入口与基线审计

权威输入：
- docs/backend-design/00_BACKEND_MASTER_PLAN.md
- docs/backend-design/04_FILE_MANIFEST.md
- docs/backend-design/06_TEST_MODEL_AND_COST.md
- docs/backend-design/08_AI_EXECUTION_PLAN.md

允许修改：
- backend/**
- docs/backend-design/**

禁止修改：
- docs/index.html
- docs/assets/product/**
- chrome-newtab/**
- 真实 Vault

交付：
- backend/pyproject.toml
- backend/README.md
- backend/src/memento_backend/__init__.py
- backend/tests/conftest.py
- backend/reports/R0_BASELINE.md

要求：
- 检查当前工作树并保留用户已有改动
- 确认 Python、pytest、lint 和 type-check 命令
- 记录现有 V1 可复用模块与当前前端 validator
- 不调用模型
- 不移动或重写 context-agent
- 运行工程启动测试并报告真实结果

停止条件：
- 当前前端还没有可恢复基线
- backend 目录已有来源不明且会被覆盖的文件
- 权威文档之间存在对象或字段冲突
```

R0 完成后再执行 R1。每个轮次都保持同样的输入、范围、验证和停止条件，AI 才能持续开发而不让后端与已经完成的前端逐渐偏离
