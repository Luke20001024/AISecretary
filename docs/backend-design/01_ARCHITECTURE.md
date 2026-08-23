# 01 · 后端总体架构

## 1. 现有实现中应当保留的基础

现有实现已经验证了六个重要原则：

1. 原文先落盘，AI 后处理
2. 不可变 revision 保留每次变化
3. Agent 输出 action，Workflow 负责校验和提交
4. 用户 action 可以使并发模型结果失效
5. Projection 可删除、可重建
6. Provider 失败时保留上一版可信结果

这些原则继续作为 V2 的底座

## 2. 当前需要修正的结构问题

### 2.1 主题与“她理解的我”仍有对象混用

生产合同目前用 `understanding` 同时承担地景主峰和长期理解。前端 fixture 已经出现更清晰的两类对象：

- `themes`：产品决策、证据优先、长期积累等跨时间主题
- `portrait`：多个主题共同形成的方向、价值排序和工作方式理解

V2 将两层正式拆开

### 2.2 日级处理仍带有历史 Daily Review 语义

日级 bundle 可以继续作为内部归并单元。面向用户的每日评价、每日总结和照片不进入新后端主链

### 2.3 可调用的个人记忆仍缺少完整双向体验

这一部分承接“让每个 AI 都从同一个你开始”。技术层继续使用 Context、ContextGrant、Context Broker 与 MCP 等准确对象名

V2 增加授权、读取日志、会话和写回对象。外部 AI 可以读取相关 Context，也可以将本次对话产生的决定、修正、结果或新问题写回 Memento

写回内容仍需经过常规解释、归并和证据校验，不能直接修改 Theme 或 Self Insight

## 3. 目标模块

```text
┌──────────────────────────────────────────────────────────┐
│ Capture Adapters                                         │
│ text · note · tag · screenshot OCR · voice · external AI │
└────────────────────────────┬─────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────┐
│ Source Store                                             │
│ immutable source · stable id · attachment metadata       │
└────────────────────────────┬─────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────┐
│ Cognitive Workflows                                      │
│ Record → Daily → Theme → Self                            │
│ material gates · policy · locks · validation · commit    │
└────────────────────────────┬─────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────┐
│ Revision Stores                                          │
│ memory atoms · relations · themes · self insights        │
└────────────────────────────┬─────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────┐
│ Read Models                                              │
│ home · landscape · self · trace · context pack           │
└───────────────────────┬───────────────────┬──────────────┘
                        ▼                   ▼
                 Product Frontend      Context Broker / MCP
```

## 4. 四条运行链路

### 4.1 记录理解入口与逐条链路

```text
记录成功保存
→ Capture Understanding Agent 判断输入在当下的语义位置
→ archive_only / resource_index / interpret / ask_on_use / needs_confirmation
→ Workflow 提交 CaptureDecisionRevision
→ 建立可选 ResourceCardRevision 与 ReadLaterIntentRevision
→ 仅 interpret 路由进入 Record Interpreter
→ Record Interpreter 生成候选解释
→ 校验引用、原文片段、敏感级别和用户 watermark
→ 提交 RecordInterpretationRevision
→ 等待日级归并
```

目标延迟：记录先立即可见，逐条解释异步完成

入口 Agent 先分辨用户留下的是资料、待阅读意图、局部判断、明确决定，还是仍需用户补充的内容。Workflow 提供来源、附件和快捷动作等确定性约束，并负责保存原件、权限和状态机

### 4.2 日级归并链路

```text
冻结目标日有效记录
→ 检查逐条覆盖率
→ Daily Integrator 去重、拆分和建立候选关系
→ 原子提交 MemoryAtomRevision 与 RelationRevision
→ 发布当天处理状态
```

日级归并是内部事务，不生成一段面向用户的每日评价

### 4.3 长期理解链路

```text
Material Change Gate
→ Theme Synthesizer 查找跨时间重复与边界
→ 新增、强化、修订、张力、休眠或保持安静
→ 提交 ThemeRevision
→ Self Understanding Gate
→ 从多个 Theme 形成或修订 SelfInsightRevision
```

Theme 和 Self Insight 使用不同的触发条件、证据规则和确认等级

### 4.4 外部 AI 双向链路

```text
外部 AI 请求与当前任务相关的个人 Context
→ ContextBroker 校验授权与范围
→ 检索 Theme、Self Insight、Memory Atom 和原文引用
→ 返回有范围、有来源、有未知项的 ContextPack
→ 记录本次读取审计
→ 外部 AI 可提交本次会话产生的决定、修正或结果
→ 写成 ExternalTraceRevision
→ ExternalTraceRevision 进入常规入口与逐条链路
```

## 5. 存储策略

V2 继续采用本地文件作为事实源：

- 原始内容保持用户可读的 Markdown 和附件
- 领域对象采用 append-only JSON revision
- head index 和 Projection 属于可重建派生数据
- 初期不引入远程数据库
- 本地 SQLite 只可作为可删除的检索索引，不能成为唯一事实源

建议布局：

```text
~/AISecretary/.memento-v2/
├── records/
├── capture-decisions/
├── resources/
├── read-later/
├── interpretations/
├── memory-atoms/
├── relations/
├── themes/
├── self-insights/
├── external/
│   ├── grants/
│   ├── sessions/
│   ├── reads/
│   └── traces/
├── actions/
├── transactions/
├── runs/
├── indexes/
├── projections/
│   ├── staging/
│   ├── bundles/
│   ├── publications/
│   └── current.json
└── locks/
```

R4 已冻结三条持久化可见性边界：

- 正式对象：不可变 revision 文件全部完成后，写入一个完整 transaction manifest，再原子切换 Head Index
- 用户动作：action 与 terminal result 分别 append-only 保存，用户 action watermark 可从 action 文件重建
- 前端读模型：完整 ProjectionBundle 先在 staging 校验并封存，publication 完成后只替换 `projections/current.json`

Head Index、action watermark 和 current pointer 都属于可重建索引。revision、transaction、action、result、sealed bundle 与 publication 属于不可变事实

## 6. 调度与成本边界

| 链路 | 触发 | 模型策略 |
|---|---|---|
| Capture Understanding | 保存后事件触发 | 轻量 Agent 判断语义路由，规则提供安全约束 |
| Record | 保存后事件触发 | 低成本模型优先，严格一次记录范围 |
| Daily | 手动或每日一次 | 仅处理有完整逐条覆盖的日期 |
| Theme | Material Change Gate 通过 | 有跨日新材料时调用 |
| Self | Theme revision 发生实质变化 | 低频调用，允许主动停止 |
| Projection | 任一正式 head 变化 | 0 模型调用 |
| Context read | 外部任务请求 | 默认确定性检索，必要时受限 rerank |
| External writeback | 外部会话显式提交 | 先落 ExternalTraceRevision，后走入口与 Record 链路 |

## 7. 安全边界

- 浏览器和 MCP 客户端均不持 Provider Key
- 模型无法传任意文件路径
- 所有 source quote 在提交前重新逐字核验
- 附件二进制默认不出站
- 敏感推断需要更高确认等级
- 已删除对象正文不进入模型上下文
- 外部 AI 读取与写回均绑定 grant、session、scope 和时间
- 相同输入、Prompt、Policy 和 Provider 版本可回放

## 8. 前端边界

后端只向产品前端提供：

- `home_projection.json`
- `landscape_projection.json`
- `self_projection.json`
- `record_detail_projection/<id>.json`
- `theme_detail_projection/<id>.json`
- `self_insight_detail_projection/<id>.json`
- append-only action inbox 与 terminal result

前端不读取模型 run 原文，不执行 Provider 调用，不直接写正式 revision
