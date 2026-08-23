# 05 · 实施与前后端合码

## 1. 开发隔离方式

当前工作树包含大量尚未提交的前端与产品文档修改。开始后端编码前，先保存前端基线，再从该基线创建独立分支或 worktree

建议：

```text
前端基线：当前认知地景 UI
后端分支：codex/backend-v2
后端目录：backend/
合码方式：Projection contract + feature flag
```

后端分支不修改产品 HTML 和现有视觉组件

## 2. 阶段划分

### B0 · 冻结合同与样本

目标：确定后端要产出什么

- 冻结 20 天 fixture
- 冻结 Source、CaptureDecision、Resource、Read Later 和 RecordInterpretation Schema
- 冻结 MemoryAtom、Relation、Theme、SelfInsight 和 External Context Schema
- 冻结 Home、Timeline、Landscape、Self、Detail 与 ProjectionBundleManifest
- 建立 V2 → V1 adapter
- 建立原始数据 hash 清单

完成条件：0 模型调用生成可被当前前端验证器接受的 fixture Projection

### B1 · 本地领域层与 revision store

目标：所有正式对象可以独立创建、修订、撤回和重建 head

- ObjectRef 与稳定 ID
- CaptureDecisionRevision
- ThemeRevision
- SelfInsightRevision
- ContextGrantRevision 与 ExternalTraceRevision
- append-only revision store
- CAS、tombstone、原子写入和恢复

完成条件：全部 contract、transition 和并发测试通过

### B2 · 入口判断、资料与逐条链路迁入 V2

目标：让每种真实输入进入正确处理路线，并形成可检查的单条理解

- 接入现有 record parser
- Capture Understanding Agent 与处理路由
- Resource Card、Read Later intent 与按需 Resource Reader
- Record Interpreter
- 记录与资料详情 Projection

完成条件：链接、长网页、带备注资料和用户原话均进入正确路线，且资料观点不会被写成用户长期理解

### B3 · 日级归并与 Theme Synthesizer

目标：从正式单条理解形成 Memory Atom，并让同类记忆跨时间形成真实主题

- Daily Integrator
- action reconcile
- material gate
- usage ledger
- 主题候选召回
- new / reinforce / revise / tension / dormant
- 来源与反例核验
- Theme detail projection
- 20 天回放

完成条件：隔离 Vault 可以完成记录 → 解释 → Memory Atom → Relation；固定场景中主题新增、修订、张力和 no_change 均有可复核证据

### B4 · Self Understanding Agent

目标：形成第三层“她理解的我”

- 跨 Theme material gate
- Self Insight 工具和 action
- 敏感推断策略
- 用户确认等级
- Self projection 和 detail projection

完成条件：每条理解都能从 Self Insight → Theme → Memory Atom → Source 回溯

### B5 · Context Broker 与 MCP

目标：把对用户的理解接回真实工作流

- scoped grant
- deterministic search
- Context Pack
- read audit
- append trace / correction / outcome
- trace 重新进入 Record Interpreter

完成条件：一次外部任务可以读取相关 Context，留下结果，并在 Memento 中看到新增 trace 的处理状态

### B6 · 影子运行

目标：验证真实数据质量，仍不修改正式用户理解

- 固定模型、Prompt、Policy 和预算
- 真实用户 Vault 只读快照
- candidate 与现有结果对照
- 记录误连、漏连、过度推断、停止准确率和成本

完成条件：预注册评测门通过，用户明确允许后才开启 commit

### B7 · 前后端合码

目标：用真实 Projection 替换固定 fixture

1. 前端增加 `cognitive-v2-data-source.js`
2. 默认仍使用 fixture
3. feature flag 读取后端 V2 Projection
4. validator 通过后切换数据源
5. 接入详情读取
6. 接入用户 action inbox
7. 接入外部会话与授权状态
8. 保留 fixture 作为演示和回归数据

## 3. 合码契约

前端只依赖以下稳定入口：

```text
readProjectionManifest()
readHome()
readTimeline(range)
readLandscape()
readSelf()
readRecordDetail(recordId)
readResourceDetail(resourceId)
readThemeDetail(themeId)
readSelfInsightDetail(insightId)
readExternalSession(sessionId)
readRunStatus(runId)
submitAction(action)
pollActionResult(actionId)
requestRun(kind, scope)
```

数据源可以是 fixture、本地文件或以后出现的本地服务。视觉组件不感知 Provider、Agent 和存储实现

当前后端已经以 `ProjectionReadApi`、`ActionApi` 和 `RunRequestInbox` 实现上述本地合同边界。B7 只负责新增前端 data source、feature flag 与 transport 组合，不再重新定义读写语义

## 4. Feature Flag

建议状态：

```text
cognitive_backend = fixture | v1_adapter | v2_shadow | v2_live
```

- `fixture`：当前固定产品演示
- `v1_adapter`：V2 对象转成 V1 Projection
- `v2_shadow`：读取 V2 候选，不允许正式用户写入
- `v2_live`：质量门通过后的正式状态

## 5. 回滚策略

- Projection 发布使用临时文件和原子 rename
- 前端发现 V2 Projection 无效时回退上一份合法 snapshot
- 后端 V2 关闭不删除 V1 或用户原文
- V2 migration 只追加，不覆盖 V1 revision
- feature flag 可以退回 `fixture` 或 `v1_adapter`
- 外部 grant 可独立撤销，不影响本地记录

## 6. 每个阶段的交付物

每个阶段都提供：

- 代码文件清单
- Contract 版本
- 测试命令与结果
- fixture 或隔离 Vault
- 模型调用数、Token、成本和延迟
- 未验证项
- 是否允许进入下一阶段

没有完成真机、真实模型或用户授权的部分保持明确待验证状态

R9 已提供密封只读快照、预注册计划、指标聚合和原子影子报告工具。候选必须以完整 ProjectionBundle 提供，基线必须指向同一只读快照内的文件并匹配 hash。缺少其中任一项时，报告不能进入真实质量终态
