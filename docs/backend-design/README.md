# Memento 后端设计包

> 状态：后端 V2 规划基线
>
> 日期：2026-08-22
>
> 范围：后端独立开发，完成质量门后再与产品前端合码
>
> 产品叙事口径：`docs/MEMENTO_PRODUCT_NARRATIVE.md`

## 1. 这份设计包解决什么

Memento 的前端已经明确呈现三层产品结构：

1. 多条记录怎样跨时间形成主题地图
2. 每个主题由哪些记录、记忆、边界、反例和版本变化形成
3. 多个主题怎样形成 Memento 对用户的当前理解

记录流负责接住输入并展示处理去向，可调用的个人记忆负责把形成的理解接回工作，两者分别位于三层认知的前端和后端。外部接口承接的用户价值统一为“让每个 AI 都从同一个你开始”

现有后端已经具备原文落盘、逐条解释、日级归并、长期 revision、地景投影、用户 action、调度和评测骨架。下一步需要把这些能力收束为一条可独立开发、可独立验收的后端主干，并补齐主题层、第三层理解和外部 AI 双向调用

## 2. 核心架构决定

- 原文、模型候选、正式对象和前端投影继续分层
- 所有 Agent 只提交候选 action，本地 Workflow 校验并写入正式 revision
- `ThemeRevision` 表达跨时间形成的长期主题
- `SelfInsightRevision` 表达多个主题共同支持的当前理解
- 认知地形由确定性 Projector 生成，不由模型直接绘制
- 外部 AI 通过 `ContextBroker` 读取授权范围内的 Context
- 外部 AI 留下的新线索先写成 `ExternalTraceRevision`，再进入常规记录链路
- 产品前端只读取版本化 Projection，并向 append-only inbox 写用户动作
- `docs/index.html` 保持产品讲述页，不接真实后端

## 3. 后端主链

```text
Capture
  ↓
SourceRecordRevision
  ↓
Capture Understanding Agent
  ↓
CaptureDecisionRevision
  ↓
Record Interpreter
  ↓
RecordInterpretationRevision + MemoryAtomCandidate
  ↓
Daily Integrator
  ↓
MemoryAtomRevision + RelationRevision
  ↓
Theme Synthesizer
  ↓
ThemeRevision
  ↓
Self Understanding Agent
  ↓
SelfInsightRevision
  ↓
Deterministic Projectors
  ├── HomeProjection
  ├── LandscapeProjection
  ├── SelfProjection
  └── ContextPackProjection
          ↓
  产品前端 / MCP / 本地工具
```

## 4. 设计包目录

| 文件 | 内容 |
|---|---|
| [00_BACKEND_MASTER_PLAN.md](00_BACKEND_MASTER_PLAN.md) | 前后端统一总契约：三层展示、六级 Agent、正式对象、Projection、动作与合码门槛 |
| [01_ARCHITECTURE.md](01_ARCHITECTURE.md) | 总体架构、运行链路、边界和兼容策略 |
| [02_AGENT_HIERARCHY.md](02_AGENT_HIERARCHY.md) | 各层 Agent 的理解范围、输入、输出、触发和禁区 |
| [03_DATA_AND_INTERFACES.md](03_DATA_AND_INTERFACES.md) | 核心对象、revision、前端投影、MCP 和双向写回 |
| [04_FILE_MANIFEST.md](04_FILE_MANIFEST.md) | 后端实现阶段需要创建、复用、冻结和测试的具体文件 |
| [05_IMPLEMENTATION_AND_MERGE.md](05_IMPLEMENTATION_AND_MERGE.md) | 独立开发阶段、影子运行和最终前后端合码流程 |
| [06_TEST_MODEL_AND_COST.md](06_TEST_MODEL_AND_COST.md) | 质量门、评测集、运行成本和开发模型建议 |
| [07_SCENARIO_LIBRARY.md](07_SCENARIO_LIBRARY.md) | 记录分流场景集、标注规范与 Agent 验收样本 |
| [08_AI_EXECUTION_PLAN.md](08_AI_EXECUTION_PLAN.md) | AI 分轮执行路线、修改边界、交付物、质量门与首轮任务 |
| [10_SELF_INSIGHT_MAP_BRIDGE.md](10_SELF_INSIGHT_MAP_BRIDGE.md) | “她理解的我”到来源主题山系的点击态、Projection 字段、降级与验收合同 |

## 5. 当前代码与目标后端的关系

当前 `context-agent/` 是经过测试的实现基线，保留为 V1 兼容路径和行为参考。V2 在新的 `backend/` 目录中开发，通过 adapter 复用成熟能力，并逐步替换对象层级

首个阶段不移动现有文件，也不改 Chrome 前端。先让 V2 对固定 20 天 fixture 和隔离 Vault 生成可验证的 Projection，再进入前端接线

字段、Agent 权限和前端展示发生冲突时，以 `00_BACKEND_MASTER_PLAN.md` 为对齐入口，再同步修改对应专题文档与 JSON Schema

## 6. 后端 V2 的完成口径

只有同时满足以下条件，后端才进入合码阶段：

- 新记录可以稳定形成逐条解释
- 日级归并可以生成正式 Memory Atom 和 Relation
- 跨时间材料可以形成或修订 Theme
- 多个 Theme 可以形成少量 Self Insight
- 每个 Theme 和 Self Insight 都能回到原始记录
- 用户修改、删除和限定范围拥有最高优先级
- 外部 AI 的每次读取和写回都可审计、可撤回
- Projector 在相同输入下生成相同结果
- 前端合同测试和 20 天回放测试通过
- 真实模型影子运行通过后，才允许正式写入用户 Vault
