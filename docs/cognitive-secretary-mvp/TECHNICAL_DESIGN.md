# Memento 认知秘书 MVP 技术设计

> 状态：v0.9.0 实现合同
>
> 更新日期：2026-08-18
>
> 产品范围见 [PRD](PRD.md)，精确对象见 [数据合同](DATA_CONTRACT.md)，操作步骤见 [使用说明](USER_GUIDE.md)，实测结果见 [发布清单](RELEASE_CHECKLIST.md)。

## 1. 实现目标与发布口径

本版本将四段能力接成一条可审计链路：

```text
逐条整理
→ 每日归并与每日总结
→ 长期理解
→ 认知地景与主页
```

实现遵守六条边界：

1. 原文先成功落盘，AI 后处理；
2. 候选与正式对象分层；
3. Agent 只提出语义 action，本地 Workflow 校验和提交；
4. 用户 action 优先于并发模型结果；
5. 投影可删除、可重建，不成为事实源；
6. 失败保留上一版可信结果，不生成半份正式状态。

当前长期 Agent 合同为 Prompt `remember-agent-v1.22`、Workflow `agentic-workflow-investigation-v1.13`、stable-new identity `stable-new-identity-v1.1` 与 terminal gate `stable-new-terminal-gate-v1.0`。工作区 v4、先前已安装 plan `fcdf` 与当前最终已安装 plan `2d964129…` 的隔离合成证据分开记录；Chrome 人工交互、真实用户 Vault “逐条整理 → 日级归并”和自然 21:00/08:00 日历唤醒仍按发布清单独立验收。

## 2. 当前组件

| 组件 | 实现文件 | 职责 |
|---|---|---|
| 公共 CLI | `context-agent/context_agent.py` | gate、逐条、日级、调度、投影、迁移入口 |
| 核心对象合同 | `context-agent/cognitive_v1.py` | ID、revision、ObjectRef、SourceSpan、地景与主页验证 |
| 原始记录索引 | `context-agent/cognitive_store_v1.py` | 解析日级记录、稳定 record 身份、source revision |
| Agent runtime | `context-agent/cognitive_runtime_v1.py` | 严格 request/run、逐条与日级 action loop、调用预算 |
| Prompt 与 Schema | `context-agent/cognitive_prompts_v1.py`、`context-agent/schemas/` | 模型可见合同与严格 JSON action |
| 用户校正 | `context-agent/cognitive_actions_v1.py` | append-only action、CAS 物化、result |
| 主页手动归并 | `context-agent/cognitive_manual_request_v1.py` | 校验/消费浏览器不可变请求，写入精确绑定的 terminal result |
| 日级正式存储 | `context-agent/cognitive_bundle_store_v1.py` | staging、事务、正式 memory/relation/summary 与 bundle |
| 逐条 Worker | `context-agent/cognitive_record_worker_v1.py` | action reconcile、record reconcile、有界逐条处理、投影钩子 |
| 日级 Pipeline | `context-agent/cognitive_pipeline_v1.py` | 冻结输入、运行 Integrator、校验并提交 bundle |
| 每日总结 | `context-agent/cognitive_daily_review_v1.py` | 从正式日级摘要渲染 Review，并保护用户补充 |
| 长期 Adapter | `context-agent/cognitive_agent_adapter_v1.py` | 把日级材料接入 Agent V1 material gate |
| 日级 Orchestrator | `context-agent/cognitive_day_orchestrator_v1.py` | 逐条补齐、bundle、Review、长期判断、投影的唯一日流程 |
| 统一调度 | `context-agent/cognitive_schedule_v1.py` | 21:00、08:00、手动触发和按日互斥 |
| 投影 | `context-agent/cognitive_projection_v1.py` | 0 模型调用生成地景与主页 JSON |
| 历史迁移 | `context-agent/cognitive_migration_v1.py` | 只读盘点与 record 索引回填 |
| DeepSeek | `context-agent/deepseek_provider.py` | Key、HTTPS JSON 调用、响应和错误收口 |
| Chrome 本地客户端 | `chrome-newtab/cognitive-home-library.js`、`chrome-newtab/dashboard.js` | 验证投影、显示地景/今日记录、按需读取形成链路与原文，只追加 user action 和 manual-day request |

旧 Self Reflection 保留为兼容路径。旧 Agent schedule 命令是统一日调度的别名，不能创建第二条长期整理路径。

## 3. 总体架构

```text
macOS Capture Services
        │ 原文 append 成功
        ▼
record-ingest（无 Provider）
        │
        ▼
record-worker（总 gate）
        ├── reconcile 用户 action
        ├── reconcile 当日记录
        ├── Record Interpreter / DeepSeek
        └── 重建主页投影

manual / 21:00 / 08:00 recovery
        │
        ▼
Daily Orchestrator（按日锁）
        ├── 补齐逐条回执
        ├── Daily Integrator / DeepSeek
        ├── 原子提交 daily bundle
        ├── 生成并绑定 Daily Review
        ├── material gate → Agent V1
        └── 发布 landscape + home projection
                          │
                          ▼
                    Chrome 新标签页
```

浏览器读取本地 projection 和权威对象，且只能向受管目录追加两类输入：绑定 target revision/hash 的用户 action，以及当地当日的 manual-day request。Dashboard 不持 API Key，也不直接调用 Provider 或 CLI。事件 Worker 消费这些不可变输入，并把 terminal result 写回 Vault；页面只接受 hash、ID、日期和状态均精确绑定的结果。

## 4. 逐条链路

### 4.1 采集接线

正式采集脚本只在原文 append 成功后唤醒 `run_cognitive_record_once.sh`。runner 依次执行：

1. `record-ingest`：建立或修订当日 record sidecar/index，不调用 Provider；
2. `record-worker --once --limit 16`：总 gate 开启时处理有界批次；
3. 任一步后处理失败均不回滚已成功保存的原文。

这里没有独立常驻 Reconciler。丢失采集唤醒时，可由后续 record Worker、统一日流程或公共 `record-ingest`/迁移命令重新扫描合法日级文件。

### 4.2 记录身份

- 新采集入口先根据 capture nonce 分配稳定 `rec_<24 hex>`；
- 历史条目按 `source_file + locator-v1 anchor` 确定性生成；entry hash 不参与 record ID；
- 原文内容变化时创建新的 source revision，ID 保持不变；
- append 到同一日文件不会让旧 record 因整文件 hash 变化而失效。

### 4.3 Record Interpreter

一次只处理一个精确 `record_ref`。模型可返回：

- `propose_receipt`：一句摘要、facets、候选记忆、候选关系和 source refs；
- `finish`：reason code 只允许 `original_only / insufficient_signal`；Runtime 将两者都收口为无回执的可信 `no_candidate`，且 `receipt_ref=null`。模型 reason code `original_only` 不会写入同名的用户 receipt 终态。

Runtime 只把当前 record 的已物化 SourceSpan 和显式目标对象交给模型，不开放任意 Vault 浏览。提交前重新核对来源 revision、逐字 quote、hash、用户 feedback watermark、Schema 和预算。

已知 Schema 拒绝只允许一个确定性 retry request。它必须从已持久化的单次 Provider attempt 与 completion 中验证，并继续绑定相同 source revision、feedback watermark 和 policy。重试再失败、attempt 结果不明或材料已变化时不发起第三次 Provider 调用。

当前 `no_candidate` 可以被后续 Worker 与日流程零调用复用；source revision 变化会使它失效。用户已提交的 `original_only / tombstone` 是同一 record 的自动处理终态，后续 source edit 不重启该 record 的自动整理。

逐条 receipt 仍是候选层；它不能直接创建 reusable memory、formal relation 或 Agent V1 memory。

## 5. 日级链路

### 5.1 唯一入口

`daily-run` 是统一日流程。三种 trigger：

- `manual`：用户显式运行；只要求总 gate；
- `scheduled`：本地 21:00 或睡眠后补发的最近时段；要求总 gate 和 schedule；
- `recovery`：08:00 检查昨天；要求总 gate 和 schedule。

三种 trigger 使用相同 Orchestrator、Pipeline、正式存储和投影，request 身份仍保留 trigger，便于审计。

### 5.2 Daily Integrator

进入 Integrator 前，Pipeline 必须对目标日所有当前 active record head 执行覆盖闸门。每条必须已有绑定当前 source revision 的 `ready / needs_review` receipt、精确当前的可信 `no_candidate`，或用户终态 `original_only / tombstone`。任一条未覆盖就返回 `no_receipts`，不创建 Daily request，不部分提交。

覆盖完整后的分流为：

- 全部为 `no_candidate` 时返回 `no_candidate`；
- 全部为 `original_only / tombstone` 时返回 `no_change`；
- `ready / needs_review` 与 `no_candidate` 混合时，仅将前者的 receipt 送入 Integrator，后者不生成伪 receipt。

模型允许动作固定为：

```text
inspect_memory
search_history
propose_daily_bundle
finish
```

它可以去重、拆分多主题内容、提出 memory/relation operations 和日级摘要。`inspect_memory` 与 `search_history` 的结果由本地代码选择、限量并物化；模型不能提供任意文件路径。

`finish` 可明确表示 `no_change` 或 `insufficient_evidence`。没有可提交内容时不制造长期变化。

### 5.3 原子日级提交

Pipeline 先冻结 source refs、receipt refs、当前正式对象、Agent profile hash 和 user action watermark，再把候选写入 staging。Bundle Store 完成：

1. 校验 memory/relation operation 与全部引用；
2. 重新核对 source、receipt、target revision、profile 和 action watermark；
3. 在 transaction staging 中物化新的不可变 revision；
4. 提交到 `daily-bundles/committed/`；
5. 更新经验证的 formal head index。

Reader 只读取合法 committed bundle 和完整 revision 链。候选目录、未完成事务和 quarantine 内容不进入主页或长期判断。

### 5.4 每日总结与长期判断

日级 bundle 提交后，Orchestrator：

1. 从正式 DailySummary 与原始来源渲染 `Reviews/Daily/YYYY-MM-DD.md`；
2. 使用 CAS 绑定 Review 路径和 SHA-256，并保留 `## 我的补充`；
3. 计算 daily material gate；
4. 只有存在实质材料时，创建受限 Agent V1 输入并调用现有长期 Workflow；
5. 长期层仍可选择 finish，不必新增或修改理解；
6. 最后从正式对象发布地景和主页。

日级 bundle 已提交而后续步骤失败时，正式日级结果保留，Orchestrator 返回 `committed_with_warnings`。

## 6. 调度

### 6.1 三个 LaunchAgent

| Label | 角色 | 触发 |
|---|---|---|
| `com.memento.context-agent` | 旧 Self Reflection 兼容 Worker | 兼容 watch path、RunAtLoad/失败恢复 |
| `com.memento.remember-agent-v1` | Re:member 事件 Worker | `agent-v1/requests`、`agent-v1/user-actions`、认知 user-actions 与 manual-day-requests；无 RunAtLoad、KeepAlive、日历 |
| `com.memento.remember-agent-v1-schedule` | 统一认知秘书日流程 | 本地 21:00 和 08:00 |

全新安装铺设 runner 和 plist，但不创建：

```text
~/AISecretary/.context-agent/agent-v1/enabled
~/AISecretary/.context-agent/agent-v1/schedule.json
```

因此安装后默认不会调用 DeepSeek。升级只在既有 gate/schedule 通过安全校验时保留它们。

### 6.2 21:00 与 08:00

Schedule Core 在按日 owner-only 锁内：

1. 复读总 gate；
2. 复读共用的 Agent V1 `schedule.json`；
3. 选择最多一个目标日；
4. 进入统一 `day_runner(local_date, trigger)`；
5. 对外只返回有限状态，不暴露原文、路径或 Provider 响应。

时间与完成规则：

- 21:00 之后选择今天的 scheduled；
- 08:00 之后、21:00 之前选择昨天的 recovery；
- 08:00 之前如收到睡眠后合并的日历事件，选择昨天的 scheduled；
- 08:00 每次依据昨天原文与 record index 重新核对当前 heads；晚到记录、source edit、未终态或失败的逐条整理都使 recovery 到期，旧 bundle 不得遮蔽它们；
- 无 bundle 时，若当前所有 heads 已由可信 `no_candidate`、用户终态或精确当前输入的可信 daily `no_change` 完整覆盖，该日可视为完成；
- 已有旧 bundle 时，旧 bundle 仍必须有当前 Daily Summary 精确绑定且字节 hash 匹配的 Review；可信 daily `no_change` 可证明新 heads 无新日级变化，不会把旧 bundle 的无效 Review 变成有效；
- schedule 的 `updated_at` 晚于目标时段时返回 `not_due`；
- 不扫描或补跑更早 backlog。

## 7. 存储布局

```text
~/AISecretary/
├── YYYY-MM-DD.md
├── assets/
├── Reviews/Daily/YYYY-MM-DD.md
└── .context-agent/
    ├── runtime/
    ├── usage/
    ├── agent-v1/
    │   ├── enabled
    │   ├── schedule.json
    │   ├── requests/ responses/ runs/
    │   ├── memories/ user-actions/ action-results/
    │   └── profile.json
    └── cognitive-secretary-v1/
        ├── records/
        ├── interpretation-requests/ interpretation-runs/ receipts/
        ├── daily-requests/ daily-runs/
        ├── daily-bundles/
        │   ├── staging/candidates/
        │   ├── staging/transactions/
        │   ├── committed/
        │   ├── quarantine/
        │   ├── journals/
        │   └── feedback-journals/
        ├── memory-revisions/
        ├── relation-revisions/
        ├── daily-summary-revisions/
        ├── formal-head-index.json
        ├── user-actions/ action-results/
        ├── manual-day-requests/ manual-day-results/
        ├── day-orchestrator/status/
        ├── landscape-snapshots/
        ├── projections/
        │   ├── landscape-head.json
        │   └── home_projection.json
        └── locks/
```

所有受管状态位于 Vault 内。关键目录与文件执行 owner、类型、权限、单链接和 no-follow 校验；非法路径 fail-closed。

## 8. 身份、幂等与并发

| 对象/任务 | 当前身份规则 |
|---|---|
| 新记录 | capture nonce → 稳定 record ID |
| 历史记录 | source file + locator anchor → 稳定 record ID |
| 逐条 request | record ref + feedback watermark + contract + trigger + 可选 nonce |
| 逐条 run | request ID + material run key |
| receipt | record ID → 稳定 receipt ID，变化追加 revision |
| 日级 request | local date + contract + trigger + 可选 nonce |
| 日级 run | request ID + 冻结 material run key |
| daily bundle/summary | 每个本地日期稳定 ID，变化追加 revision |
| memory/relation | materialization key → 稳定正式 ID，变化追加 revision |
| action result | action ID → 确定性 result ID |
| 主页手动归并 request | 每次点击生成 `cman_<24 hex>`，并固定绑定浏览器当地当日 |
| 主页手动归并 result | request 规范化字节 SHA-256 → 确定性 `cmanr_<24 hex>` |
| landscape snapshot | 输入 hashes + publication nonce → 每次发布新 ID |
| peak | Agent V1 memory ID → 稳定 peak ID |

Interpreter、Daily Integrator 和 Agent V1 共用 Provider 调用锁，防止多入口同时无界调用。run 保存 provider attempt marker；崩溃后无法证明调用结果时进入有限的 unknown/recovery 状态，不把未知当成功。

用户 action 在同一正式提交锁内生成 watermark。模型运行后到正式提交前如 watermark、来源或目标 revision 变化，提交返回 stale/conflict，0 越权写入。

## 9. 用户 action 与 revision

认知 action 支持：

- `confirm_receipt / edit_receipt / original_only`；
- `edit_reusable_memory / delete_reusable_memory`；
- `edit_relation / delete_relation`；
- `report_outcome`。

Action 文件不可覆盖，并精确绑定 target revision/hash。Worker 物化新的 receipt/memory/relation revision 或 tombstone，再写 terminal action result。

长期理解继续使用 Agent V1 `edit/delete` action。合法 tombstone 是同一 `memory_id` 的终态；后续模型结果不得复活它。

当前 Chrome 认知主页已实现：回执确认、完整编辑、仅保留原文；可用记忆和正式关系的编辑/删除；长期理解的 Agent V1 修改/删除。认知 action 只追加到 `user-actions/`，页面轮询精确绑定的 `action-results/`；事件 Worker 使用 CAS 物化新 revision 或 tombstone 并立即重投影。

### 9.1 主页“归并今天”

1. 页面完成目录身份、读写权限和当地日期复查后，向 `manual-day-requests/` 安全追加一个六字段请求；
2. `com.memento.remember-agent-v1` 被 watch path 唤起，runner 先校验总 gate，再依次处理认知 action、manual-day request 和 Agent V1 inbox；
3. `daily-manual-worker` 只接受 owner-only、单链接、规范化字节且日期等于 Worker 当地今天的请求，然后以 `manual` 进入统一 day runner；
4. Worker 向 `manual-day-results/` 写入一个不可变 terminal result；相同 request hash 已有合法 result 时不再运行 day runner；
5. 页面轮询结果，严格校验 result ID、request ID/hash、日期、status 和状态组合。结果为 `completed` 时刷新已验证主页投影。

## 10. DeepSeek 与隐私

Provider 固定调用：

```text
POST https://api.deepseek.com/chat/completions
response_format = json_object
temperature = 0
Record Interpreter max_tokens = 2400
Daily Integrator max_tokens = 3600
```

这是公共认知秘书 CLI 构造的当前预算；Provider 类的通用默认值仍为 1200。默认模型为 `deepseek-v4-pro`，公共 CLI 也允许 `deepseek-v4-flash`。`thinking` 可为 `disabled/enabled`；`reasoning_effort=high/max` 只允许在 thinking enabled 时使用。

Key 查找顺序：

1. 当前进程 `DEEPSEEK_API_KEY`；
2. macOS 当前用户钥匙串，service `com.memento.context-agent.deepseek-api-key`。

允许出站：当前授权记录文本、有限 receipt/SourceSpan、受限历史检索、相关 active 长期理解摘要、用户明确校正语义、Schema 和预算合同。

默认禁止出站：图片/音频/视频二进制、任意附件、未授权目录、Key/Authorization、完整日志、完整 Provider request、隐藏 CoT 和已删除理解正文。

Provider HTTP/网络/结构错误只保留收口后的错误类别和允许的 usage metadata，不持久化响应正文。模型输出只有通过严格 JSON、来源和合同校验后才可能进入候选或正式层。

### 10.1 当前隔离合成实测边界

上述四个版本合同的 v4 真实 DeepSeek 有限验收在两个全新临时 Vault case 上为 `all_passed`：两日长期沉淀案 11 calls / 26,602 Token / $0.007166364；`original_only` 撤回案 6 calls / 11,851 Token / $0.002545011；合计 17 calls / 38,453 Token / $0.009711375，`invalid_action=0`。报告确认临时目录已清理，合成来源 hash 前后不变。

上述报告是工作区 Prompt v1.22 / Workflow v1.13 的 v4 账本。先前已安装 runtime plan `fcdf` 在安装副本中重跑两个冻结隔离合成 case：两日长期沉淀案 11 calls / 26,647 Token / $0.003545598；`original_only` 撤回案 6 calls / 11,829 Token / $0.001967621；合计 17 calls / 38,476 Token / $0.005513219。该 plan 现作为独立历史账本保留。

当前最终已安装 runtime 证据是 plan `2d964129…`：两日长期沉淀案 11 calls / 26,544 Token / $0.003468603；`original_only` 撤回案 6 calls / 11,826 Token / $0.001972841；合计 17 calls / 38,370 Token / $0.005441444，`invalid_action=0`。临时目录已清理，两个合成来源 hash 前后不变。

三份账本来自不同执行环境，不互相替换或汇总。plan `2d964129…` 没有使用真实用户 Vault；真实用户“逐条整理 → 日级归并”、Chrome 人工交互、自然 21:00/08:00 日历唤醒与长期稳定性仍未验收。2026-08-16 的 Prompt v1.20 / Workflow v1.11 已安装 runtime 快照与 Prompt v1.21 / Workflow v1.12 的 v3 `budget_exhausted` 失败继续保留在历史账本中。

## 11. 投影与 Chrome

Landscape Projector 只读取：

- 当前 active Agent V1 memory；
- 当前 active reusable memory；
- 当前 active formal relation；
- 上一版合法 landscape；
- 当前 action watermark 与输入 hashes。

坐标投影必须按以下合同执行：

1. 稳定 hash 只为新峰提供初始位置种子，不承载关系、相似性、语义矛盾或重要程度语义。
2. 既有峰和点优先保留上一版坐标；只有仍满足当前避碰边界的坐标才是安全坐标。
3. 对没有直接 active formal relation、也没有通过同一条 active reusable memory 和两条 active formal relation 形成两跳关系的峰，Projector 按稳定对象 ID 顺序执行确定性避碰。同一份前序合法快照与同一组正式输入必须得到同一组坐标。
4. 存在直接正式边，或通过同一条 active reusable memory 形成上述两跳关系时，相关峰可以局部接近；是否接近不构成新的关系事实，只有输出到 `edges[]` 的正式连线表示已校验关系。
5. 正式关系撤回后，如果原坐标使已经失去正式关联的峰产生点击热区或 KDE 布局碰撞，Projector 只确定性重排碰撞点，继续保留其他安全坐标。
6. `x/y`、峰间远近、密度和等高线只服务稳定排版与阅读，不回写长期理解、可用记忆或正式关系，也不参与后续事实判断。

连接到长期理解的记忆点可围绕相关峰布置。terrain 合同为 `stable-anchor-kde-v1`，等高线具体绘制由前端依据快照数据完成。山峰高度只表示经校验证据积累，不表示人格强度、重要程度或真实性。

每次发布写新的不可变 landscape snapshot，再原子更新 `landscape-head.json`；随后生成可覆盖的 `home_projection.json`。主页投影不包含完整原文，点击记录后才按 ObjectRef、locator 和 hash 从本地加载。

Home 将可信 `no_candidate` 作为已检查的记录状态：`receipt_ref=null`、summary 与下游 refs 为空，计入已整理数，不计入已归并数。投影重建必须从精确当前 run 证据恢复它，不得伪造 receipt。

Chrome 同时校验 projection schema、snapshot hash、对象引用和当前权威来源。任一校验失败时，认知主页不接管，继续使用原有记录主页。

## 12. 失败与恢复

| 失败点 | 正式状态 | 恢复方式 |
|---|---|---|
| 采集后唤醒失败 | 原文已保存，可能尚无 sidecar/receipt | `record-ingest` 或后续统一日流程补建 |
| Provider 超时/网络失败 | run `error`，0 新 receipt/bundle | 显式重跑 Worker/日流程 |
| Provider attempt 结果未知 | unknown/recovery 状态，禁止盲目重复提交 | 只恢复同一 checkpoint 或转人工处置；同一 material 不得用新 nonce/request 绕过调用闸门，只有材料身份真实变化后才可新建 run |
| 模型 action 非法 | 拒绝，不写正式对象 | 只对已知 Schema 拒绝允许唯一一次确定性重试；其他情形须修正合同或输入后新 request |
| 来源、feedback 或 target 变化 | `stale/conflict`，0 越权提交 | 基于最新 revision 重跑 |
| staging 中断 | 候选不可见 | 事务恢复、隔离或重算 |
| Review 失败 | bundle 保留，warning | 单独恢复 Review 阶段 |
| 长期判断失败 | 日级结果保留，旧长期理解保留 | 后续统一日流程再判断 |
| 地景/主页失败 | 旧地景或旧记录主页保留 | `projection-rebuild` |
| Vault 权限失效 | 停止读取/生成，不投影为空 | 用户重新授权原 Vault |

恢复只依据合法不可变对象、checkpoint、commit manifest 和 hash；不会猜测 Provider 是否成功。

## 13. 迁移与回滚

当前公开迁移只有两步：

1. `cognitive-migration-status`：只读统计日级 Markdown、Reviews、Agent V1 memories/actions/profile 的数量与 hash；
2. `cognitive-migration-backfill`：调用 RecordStore reconcile，为全部或指定日记回填 record sidecar/index。

Backfill 不调用 Provider，不创建 receipt、daily bundle、reusable memory、relation、Agent V1 memory 或 projection。执行前后会验证原始日记、Reviews 和既有 Agent V1 资产未变化；无法安全解析时返回 `needs_review`。

关闭顺序：先 `daily-schedule-disable`，再 `agent-disable`。关闭不会删除已提交状态。卸载器删除受管运行时、Dashboard、Workflow、LaunchAgent、gate 和 schedule，保留原始记录、附件、Reviews、认知秘书状态与 Agent V1 数据。

## 14. 测试与发布门

必须覆盖：

- ID、Schema、revision 链、引用和来源逐字验证；
- record 重扫、request/run 幂等和共享 Provider 锁；
- action CAS、tombstone、stale/conflict；
- daily staging/transaction/manifest 各阶段中断；
- Review、长期 Adapter、地景和主页的部分失败；
- 21:00、08:00、睡眠补发、错过后开启和无 backlog；
- 投影损坏回退、稳定 hash 种子、无关系峰确定性避碰、安全坐标保留、关系撤回后冲突点重排、原文按需读取和目录权限恢复；
- 迁移前后 hash、升级保留与卸载保留；
- 真实 DeepSeek、安装副本、Chrome 与 launchd 日历。

自动测试与真机步骤的命令、复核人和结果统一记录在 [RELEASE_CHECKLIST](RELEASE_CHECKLIST.md)。没有执行的项目保持待实测，不在文档中写成已通过。
