# 06 · 测试、模型与成本

## 1. 开发时使用哪个 GPT

如果只选择一个模型完成后端开发，建议使用：

> **GPT-5.6 Terra · medium reasoning**

原因：

- 适合持续阅读现有大型代码库
- 对 Python、Schema、状态机、测试和文档的综合能力足够
- medium 可以覆盖日常实现与回归修复
- 相比持续使用 Sol high，调用成本和等待时间更可控
- 相比 Luna，涉及 revision、并发、权限和证据链时稳定性更合适

推荐协作方式：

| 工作 | 模型与推理 |
|---|---|
| 日常编码、单测、文档同步 | GPT-5.6 Terra · medium |
| 架构冻结、迁移和安全评审 | GPT-5.6 Sol · high，按阶段使用一次 |
| 机械整理、批量测试日志归类 | GPT-5.6 Luna · medium，可选 |

若希望始终只用一个配置，保持 Terra medium 即可。不要长期使用 max 或 ultra，除非遇到可复现且 medium 无法收束的并发、迁移或安全问题

这里描述的是 Codex 开发模型。Memento 产品运行时继续通过 Provider Protocol 配置，不与开发模型绑定

## 2. 产品运行时模型策略

| Agent | 默认策略 | 升级条件 |
|---|---|---|
| Record Interpreter | 低成本模型 | 严格 Schema retry 后仍失败 |
| Daily Integrator | 低成本或中档模型 | 多主题拆分、冲突或历史检索 |
| Theme Synthesizer | 高质量模型 | 默认保持保守停止，不降级提交 |
| Self Understanding | 高质量模型，低频运行 | 只在 material gate 通过时调用 |
| Context Router | 0 模型确定性检索 | 召回过多且用户允许 rerank |
| Projector | 0 模型 | 无 |

首轮评测固定同一模型，先验证架构和 Prompt。质量稳定后再比较便宜模型，避免同时改变模型和 Workflow

## 3. 必须覆盖的测试层

### 3.1 Contract

- strict fields
- ID 与 revision transition
- ObjectRef 与 hash
- SourceSpan 逐字引用
- Theme 与 Self Insight 的证据要求
- ContextGrantRevision 与 ExternalTraceRevision
- Resource Card、Read Later intent 与处理路由

### 3.2 Store

- 原子写入
- revision 不可覆盖
- head 重建
- CAS stale
- tombstone 不复活
- 中断恢复
- 非法路径和权限 fail-closed

### 3.3 Agent

- 合法 action
- 未知 action 拒绝
- 超预算停止
- 证据不足主动停止
- Prompt injection 不获得工具权限
- 用户 action 使旧 run 失效
- 敏感 Self Insight 未确认时不外发

### 3.4 Workflow

- 原文保存后模型失败
- 同日并发归并
- late record 与 source edit
- Theme 新增、强化、修订、张力、休眠
- Self Insight 新增、修订、边界和 no_change
- ExternalTraceRevision 进入入口判断与逐条链路

### 3.5 Projection

- 同输入同输出
- Theme 与地形峰一一对应
- Self Insight 与主题引用一致
- Theme 与 Self Insight 使用独立 ID、Schema 和 Projection
- Home、Timeline、Landscape、Self 和 Detail Index 属于同一 Projection bundle
- 任一对象可以回到 Source
- 无效 Projection 回退上一版
- V2 → V1 adapter 可被当前前端 validator 接受
- empty、loading、stale、conflict、failed_preserved 均有固定 fixture

### 3.6 MCP

- 无 grant 拒绝
- 过期或撤销 grant 拒绝
- topic、time、sensitivity 范围过滤
- 每次读取产生审计
- 外部写回不直接修改 Theme 或 Self Insight
- correction 和 outcome 保留来源 client 与 session

## 4. 预注册评测场景

至少固定以下场景：

1. 单条普通记录，只形成解释，不形成 Theme
2. 只保存一个链接并写“待会再看”，形成 Read Later intent，不产生个人理解
3. 截取长网页，保存原图、OCR 与 Resource Card，不把全文自动归为用户信念
4. 网页高亮加用户备注，只对高亮与备注形成候选解释
5. 两个日期出现同类判断，形成 Theme candidate
6. 新证据强化现有 Theme
7. 新证据收窄 Theme 适用范围
8. 支持与反例同时存在，形成 tension
9. 多主题共同形成低风险 Self Insight
10. 敏感身份推断被 policy 阻止
11. 用户修改 Self Insight 后，旧模型 run 无法覆盖
12. 外部 AI 读取 Context 后写回 outcome
13. 外部 AI 写回错误理解，用户 correction 优先
14. Provider attempt 状态未知，0 正式写入
15. Projection 损坏，前端读取上一版

## 5. 质量指标

| 指标 | 含义 |
|---|---|
| evidence_valid_rate | 正式理解的来源引用有效率 |
| theme_precision | 形成的 Theme 是否属于同一长期方向 |
| over_inference_rate | 是否把局部记录过度上升为用户理解 |
| stop_accuracy | 证据不足时是否保持安静 |
| revision_accuracy | 新证据进入后是否正确强化、修订或形成张力 |
| traceability_rate | Theme 和 Self Insight 是否可以完整回到原文 |
| user_override_integrity | 用户修改和删除是否始终优先 |
| context_reuse_success | 外部任务是否实际复用了相关 Context |
| writeback_reuse_rate | 外部会话留下的 trace 是否在后续形成价值 |

## 6. 成本账本

每个 run 记录：

```text
agent_role
model
prompt_version
policy_version
calls_attempted
calls_completed
prompt_tokens
completion_tokens
reasoning_tokens
cache_hit_tokens
cost_usd
latency_ms
result_status
formal_writes
```

阶段报告至少提供：

- 单条记录平均成本
- 每日归并平均成本
- 每次 Theme 变化成本
- 每次 Self Insight 变化成本
- 每个有效变化的总成本
- no_change 任务占比
- 本地 0 调用拦截比例

## 7. 开发节奏建议

一次只推进一个后端层级：

```text
合同
→ 确定性存储与投影
→ 离线 Agent
→ 隔离真实模型
→ 影子运行
→ 正式写入
→ 前端合码
```

每层通过测试和视觉/数据检查后再进入下一层，可以同时控制返工、模型调用和真实数据风险
