# Memento 认知秘书 MVP 使用说明

> 适用版本：v0.9.0
>
> 更新日期：2026-08-18
>
> 产品定义见 [PRD](PRD.md)，发布验收见 [RELEASE_CHECKLIST](RELEASE_CHECKLIST.md)。

本指南主要覆盖“接住正在发生的意图”和“长期理解你的形状”。“让每个 AI 都从同一个你开始”对应可调用的个人记忆，外部 AI 接入仍需以独立发行与真实任务验收为准。统一文案见 [Memento 产品叙事统一口径](../MEMENTO_PRODUCT_NARRATIVE.md)。

## 1. 它会怎样处理一条记录

认知秘书沿用 Memento 原有的文字、备注、标签、截图 OCR 和语音入口。原文先写入当天的 Markdown；写入成功后，后台才尝试整理。

```text
主动记录
→ 原文写入 YYYY-MM-DD.md
→ 为这一条建立稳定 record_id
→ DeepSeek 生成逐条整理回执
→ 当天 21:00 或手动触发日级归并
→ 形成每日总结、可用记忆与关系
→ 有长期变化时进入 Re:member Agent V1
→ 重建认知地景与主页投影
```

逐条整理与日级归并分开运行：保存后可以较快看到“一条内容被理解成什么”，跨记录的去重、拆分、关系和长期沉淀留到日级任务统一判断。

## 2. 当前能力与边界

v0.9.0 工作区代码已经包含：

- 采集成功后的本地 record 索引与逐条 Worker 唤醒；
- 单条整理、日级归并、每日总结、长期判断与主页投影的统一日流程；
- 手动触发，以及本地 21:00 运行和次日 08:00 恢复检查；
- DeepSeek Provider、严格 JSON action、来源 hash、不可变 revision、原子日级提交和并发保护；
- 认知地景、今日整理记录、形成链路与原文校验读取；
- 主页内的回执确认/完整编辑/仅保留原文、可用记忆和关系编辑/删除，以及长期理解的修改/删除链路；
- 主页“归并今天”的不可变本地 request/result 交接；
- 历史记录索引回填、投影重建和卸载时保留用户数据。

当前产品边界：

- 新主页可查看地景、今日记录与形成链路，也可在详情栏提交逐条回执、可用记忆和正式关系的结构化校正。
- 长期理解的修改与删除继续使用 Agent V1 的理解 action。
- 新主页提供唯一“归并今天”按钮；页面只写入本地请求并核对结果，不持有 Key，也不调用 Provider 或 CLI。
- 认知地景只表达用户主动保存的记录形成的局部理解，不是完整人格判断。
- 打开主页、侧栏、列表或原文不会触发模型调用。
- 工作区 Prompt `remember-agent-v1.22` / Workflow `agentic-workflow-investigation-v1.13` v4 已用真实 DeepSeek 完成两个冻结隔离合成 case：17 calls / 38,453 Token / $0.009711375，`invalid_action=0`；临时目录已清理，合成来源 hash 前后不变。
- 先前已安装 runtime plan `fcdf` 另行通过两个冻结隔离合成 case：两日长期沉淀案 11 calls / 26,647 Token / $0.003545598；`original_only` 撤回案 6 calls / 11,829 Token / $0.001967621；合计 17 calls / 38,476 Token / $0.005513219。它现作为独立历史账本保留。
- 当前最终已安装 runtime plan `2d964129…` 为最新安装证据：两日长期沉淀案 11 calls / 26,544 Token / $0.003468603；`original_only` 撤回案 6 calls / 11,826 Token / $0.001972841；合计 17 calls / 38,370 Token / $0.005441444，`invalid_action=0`。临时目录已清理，两个合成来源 hash 前后不变。
- 三份账本不互相替换或汇总。它们都没有使用真实用户 Vault；真实用户“逐条整理 → 日级归并”、Chrome 人工验收和自然 21:00/08:00 唤醒仍未验收。
- 2026-08-16 的 Prompt v1.20 / Workflow v1.11 已安装 runtime 快照仍作为历史证据保留。

## 3. 首次启用

### 3.1 完成基础安装

先按仓库根目录 [README](../../README.md) 安装 Memento，并在 Chrome 加载：

```text
~/AISecretary/.chrome-newtab/
```

打开新标签页后，选择并授权：

```text
~/AISecretary
```

认知秘书 CLI 安装在：

```text
~/AISecretary/.context-agent/runtime/context_agent.py
```

先确认命令存在：

```bash
python3 ~/AISecretary/.context-agent/runtime/context_agent.py --help
```

### 3.2 保存 DeepSeek API Key

手动执行可以临时使用当前终端的 `DEEPSEEK_API_KEY`。21:00 LaunchAgent 不继承普通终端变量，macOS 自动运行应使用当前用户钥匙串。

下面的命令会在终端内安全提示输入，不把 Key 写进命令参数：

```bash
/usr/bin/security add-generic-password \
  -U \
  -a "$(id -un)" \
  -s com.memento.context-agent.deepseek-api-key \
  -w
```

只检查条目是否存在，不打印 Key：

```bash
/usr/bin/security find-generic-password \
  -a "$(id -un)" \
  -s com.memento.context-agent.deepseek-api-key \
  >/dev/null
```

### 3.3 开启认知秘书总开关

全新安装默认关闭。先检查，再显式开启：

```bash
CLI="$HOME/AISecretary/.context-agent/runtime/context_agent.py"
VAULT="$HOME/AISecretary"

python3 "$CLI" agent-status --vault "$VAULT"
python3 "$CLI" agent-enable \
  --vault "$VAULT" \
  --confirm enable-remember-agent-v1
python3 "$CLI" agent-status --vault "$VAULT"
```

只有状态返回 `enabled`，逐条 Worker 和日级任务才有权调用 Provider。

## 4. 保存后会看到什么

采集入口先写原文，再以 best-effort 方式唤醒逐条整理。原文保存不依赖 DeepSeek 成功。

| 页面状态 | 含义 | 建议处理 |
|---|---|---|
| 原文已保存 | 原文已经落盘，逐条整理尚未完成 | 可以先离开页面 |
| 正在整理这一条 | Worker 已开始处理 | 等待刷新后读取投影 |
| 已初步整理，等待今日归并 | 逐条回执有效，尚未进入正式日级结果 | 查看整理结果和原文 |
| 有一处需要你确认 | Agent 给出了需人工核对的结果 | 进入详情核对 |
| 已检查，本条没有形成可归并内容 | 当前材料已得到可信 `no_candidate` 终态，没有伪造回执 | 查看原文 |
| 仅保留原文 | 该记录不再进入自动归并 | 原文仍可查找 |
| 原文已保存，整理尚未完成 | Provider、合同或运行步骤失败 | 按第 8 节恢复 |
| 已进入今日归并 | 当前 record/receipt 已被正式日级 bundle 引用 | 可查看记忆、关系和原文 |

Dashboard 在页面加载时读取本地文件。后台刚完成整理时，重新加载新标签页即可读取新投影。

## 5. 手动归并今天

手动归并不要求开启自动计划，但总开关必须已开启。在认知主页点击“归并今天”：

1. Chrome 在已授权的 Vault 写入一条当地当日、append-only 的手动请求；
2. 页面显示“请求已保存”，当前地景继续保留上一份已验证结果；
3. 本地事件 Worker 复读总开关，校验请求所有权、权限、链接数、规范化字节和日期，再进入统一日流程；
4. 页面只接受与该请求 ID、精确字节 SHA-256 和日期均一致的结果，然后显示终态并刷新投影。

常见页面结果：

| 页面提示 | 含义 |
|---|---|
| 今日归并已完成/已提交 | 本地 day runner 已返回完成结果 |
| 本次没有形成新的长期变化 | `runner_status=no_change` |
| 还没有候选内容/记录/合法回执 | `no_candidate / no_records / no_receipts` |
| 本地认知整理尚未启用 | 总 gate 未开启，本次没有执行 |
| 请求日期已过期 | Worker 拒绝了非当天请求 |
| 归并未完成/已暂停/需重新核对 | `runner_failed / error / budget_exhausted / stale` |

公共 CLI 保留为诊断和恢复入口：

```bash
CLI="$HOME/AISecretary/.context-agent/runtime/context_agent.py"
VAULT="$HOME/AISecretary"

python3 "$CLI" daily-run \
  --vault "$VAULT" \
  --once \
  --date "$(date +%F)" \
  --trigger manual
```

页面请求与这条 CLI 路径最终进入同一统一日流程：补齐逐条整理、归并当天内容、生成或绑定每日总结、判断长期变化并重建投影。同一冻结输入会复用幂等结果，不重复提交正式对象。

日级归并不使用“已成功的部分记录”先行提交。它先核对当天所有当前 active records：每条必须已有合法 `ready / needs_review` 回执、可信 `no_candidate`，或用户终态 `original_only / tombstone`。有一条未覆盖就返回 `no_receipts`，不生成部分 Daily Review、memory、relation 或 bundle。

- 全部为 `no_candidate`：返回 `no_candidate`；
- 全部为 `original_only / tombstone`：返回 `no_change`；
- `ready / needs_review` 与 `no_candidate` 混合：只归并有合法回执的前者。

常见终态：

| `status` | 含义 |
|---|---|
| `committed` | 日级结果已经正式提交 |
| `committed_with_warnings` | 日级结果可用，但每日总结、长期判断或地景有未完成步骤 |
| `no_change` | 当前有效输入没有形成新的日级提交 |
| `no_candidate` | 当前记录已检查，但没有形成可归并回执 |
| `no_records` | 目标日没有可处理记录 |
| `no_receipts` | 有记录，但没有可进入归并的合法回执 |
| `stale` | 运行期间来源、反馈或目标版本变化，旧结果未提交 |
| `budget_exhausted` | 已达到本次模型预算，未继续调用 |
| `error` | 流程未完成；查看 `stage`、`warnings` 和 `error_kind` |

`committed_with_warnings` 不等于整条链路失败。以输出中的 `review_status`、`long_term_status`、`projection_status` 判断是哪一段仍沿用旧结果。

## 6. 开启每日 21:00 与 08:00 恢复

安装器会安装统一日调度 LaunchAgent，但全新安装不会自动创建计划开关。

```bash
CLI="$HOME/AISecretary/.context-agent/runtime/context_agent.py"
VAULT="$HOME/AISecretary"

python3 "$CLI" daily-schedule-status --vault "$VAULT"
python3 "$CLI" daily-schedule-enable \
  --vault "$VAULT" \
  --confirm enable-remember-agent-daily-21
python3 "$CLI" daily-schedule-status --vault "$VAULT"
```

调度语义：

- 21:00 检查并处理今天；
- 08:00 根据昨天原文与 record index 重新核对当前 heads；晚到记录、source edit、未终态或失败的记录都会触发恢复，旧 bundle 不会遮蔽它们；
- 昨天无 bundle，但当前 heads 已由可信 `no_candidate`、用户终态或精确当前输入的可信 daily `no_change` 完整覆盖时，视为已完成；
- 昨天已有旧 bundle 时，它仍需要与当前 Daily Summary 精确绑定且 hash 有效的 Daily Review；可信 daily `no_change` 不会补齐无效 Review；
- Mac 睡眠后，launchd 可能在唤醒时补发最近一个日历事件；系统最多选择今天或昨天，不批量补跑更早日期；
- 错过某个时间点后才开启计划，不会追补已经错过的时段；
- `daily-schedule-status` 显示已开启，只证明本地 `schedule.json` 合法，不证明 launchd 已加载或 21:00 已真实执行。

诊断时可以手动执行一次同样的调度判断：

```bash
python3 "$CLI" daily-schedule-tick --vault "$VAULT" --once
```

`master_gate_disabled`、`schedule_disabled` 和 `not_due` 都是明确的未执行原因；`completed` 表示调度器调用了日流程，真实日流程终态在 `runner_status`。

## 7. 用户校正优先

系统保存派生结果时会绑定目标 revision、来源 hash 和用户 action watermark。用户修改与模型运行并发时，基于旧版本的提交会变成 `stale` 或 `conflict`，不会覆盖用户版本。

- “正确”提交新的确认 revision；
- “改一下”可完整修改回执摘要、内容类型、主题/对象、立场、认知状态和后续用途，然后提交新 revision；
- “仅保存原文”清空 AI 候选，并阻止这条记录进入后续自动归并；
- `original_only / tombstone` 对同一 `record_id` 保持自动处理终态；后续编辑该条原文也不会自动恢复整理；
- 可用记忆与关系的编辑/删除使用新的不可变 action；
- 长期理解的修改/删除沿用 Agent V1 user action 和 tombstone；删除后的同一 `memory_id` 不得被 Agent 复活；
- 删除或修改派生理解不会删除原始 Markdown。

页面写入后会等待与当前 action ID/hash 精确绑定的结果。在 Worker 返回前，页面继续显示上一个已验证版本；不要直接手改 `.context-agent` 下的 JSON。

## 8. 失败恢复

先判断哪一层失败：

1. 原始 `YYYY-MM-DD.md` 是否存在且包含刚才的记录；
2. `agent-status` 是否为 enabled；
3. DeepSeek Key 是否可读取；
4. 手动 `daily-run` 的 `status`、`stage`、`warnings` 和 `error_kind`；
5. 新标签页是否仍有目录权限。

常用恢复命令：

```bash
CLI="$HOME/AISecretary/.context-agent/runtime/context_agent.py"
VAULT="$HOME/AISecretary"

# 只重建某日 record 索引，不调用模型
python3 "$CLI" record-ingest \
  --vault "$VAULT" \
  --source "$(date +%F).md"

# 有界重试当天尚未完成的逐条整理
python3 "$CLI" record-worker \
  --vault "$VAULT" \
  --once \
  --date "$(date +%F)" \
  --limit 8

# 不调用模型，只从已验证本地状态重建主页与地景投影
python3 "$CLI" projection-rebuild \
  --vault "$VAULT" \
  --date "$(date +%F)"
```

`record-worker` 只会对已知且完整持久化的 Schema 拒绝增加最多 1 次确定性重试。重试再失败、Provider attempt 结果不明、非 Schema 错误或来源已变化时，不会发起第三次调用。

恢复原则：

- Provider 失败时，原文和上一版可信地景继续可读；
- `stale` 应基于最新输入重新运行，不应复制旧输出到正式目录；
- 日级 staging 中断不会进入正式 Reader；下一次日流程会恢复或重新计算；
- 投影损坏时 Dashboard 回退到旧记录主页，避免把读取失败呈现成“没有记忆”；
- 目录权限失效时重新授权 `~/AISecretary`，不要新建一个空目录冒充原 Vault。

## 9. 本地目录与隐私

| 路径 | 内容 |
|---|---|
| `~/AISecretary/YYYY-MM-DD.md` | 原始日级记录 |
| `~/AISecretary/assets/` | 截图、照片、音频等原件 |
| `~/AISecretary/Reviews/Daily/` | 每日总结 Markdown |
| `~/AISecretary/.context-agent/runtime/` | 已安装 CLI 与 Worker |
| `~/AISecretary/.context-agent/agent-v1/` | 总开关、统一计划、长期理解、运行与用户操作 |
| `~/AISecretary/.context-agent/cognitive-secretary-v1/` | record、回执、日级 bundle、可用记忆、关系、actions、manual-day requests/results、地景和主页投影 |
| `~/AISecretary/.context-agent/usage/` | 不含日记正文的模型用量记录 |
| `~/AISecretary/.chrome-newtab/` | Chrome Dashboard |

默认出站文本限于当前记录、日级归并所需的受限回执/原文片段、受限历史检索结果、当前相关长期理解摘要和用户明确提交的校正语义。原始图片、音频、视频和附件二进制默认不发送给 DeepSeek。

API Key 优先从当前进程环境读取，macOS 可回退到当前用户钥匙串。Key 不写入 Vault、Dashboard 或日志。Provider 错误不会把响应正文写进公开错误信息；模型隐藏思维过程不持久化。

## 10. 历史迁移与回滚

首次升级可以先做只读盘点：

```bash
CLI="$HOME/AISecretary/.context-agent/runtime/context_agent.py"
VAULT="$HOME/AISecretary"

python3 "$CLI" cognitive-migration-status --vault "$VAULT"
```

确认盘点后，回填历史 record 索引：

```bash
python3 "$CLI" cognitive-migration-backfill --vault "$VAULT"
```

也可以只处理指定日期：

```bash
python3 "$CLI" cognitive-migration-backfill \
  --vault "$VAULT" \
  --source 2026-08-17.md \
  --source 2026-08-18.md
```

当前迁移只新增/修订 record sidecar 与索引，不调用 DeepSeek，不生成历史 receipt、daily bundle、长期理解或地景。迁移器会对原始日记、Daily Review、Agent V1 memory/action/profile 做前后 hash 对账；出现 `needs_review` 时停止继续推断，先人工核对来源结构。

关闭与回滚：

```bash
python3 "$CLI" daily-schedule-disable \
  --vault "$VAULT" \
  --confirm disable-remember-agent-daily-21

python3 "$CLI" agent-disable \
  --vault "$VAULT" \
  --confirm disable-remember-agent-v1
```

关闭后，已提交的本地结果仍可读取。卸载器会移除受管运行时、Dashboard、Workflow、LaunchAgent、总 gate 和计划文件；默认保留原始记录、附件、Reviews、认知秘书状态、Agent V1 记忆与用户操作。

## 11. 首次验收清单

以下项目要逐项记录结果。没有实际执行的项目保持未勾选。

- [ ] 安装器完成，且报告“文件已安装”与“功能已开启”是两个状态。
- [ ] `context_agent.py --help` 可执行。
- [ ] Chrome 扩展已加载并重新授权 `~/AISecretary`。
- [ ] Keychain 条目存在，未在命令历史、Vault 或日志发现明文 Key。
- [ ] `agent-status` 返回 enabled。
- [ ] 新建一条隔离测试记录，先看到原文落盘，再看到逐条整理终态。
- [ ] 隔离测试的 `no_candidate` 在 Home 中无 receipt 显示，重放与投影重建后仍保持终态。
- [ ] 整理结果能回到准确原文；原文 hash 校验通过。
- [ ] 在主页点击“归并今天”，只产生六字段本地请求，后台生成与其 ID/hash/日期绑定的十字段结果。
- [ ] 页面手动归并得到可解释终态，并在完成后读取最新主页投影。
- [ ] 同一冻结输入重复手动运行没有重复 Provider 调用或正式对象。
- [ ] 回执的正确/改一下/仅保留原文，可用记忆与关系的编辑/删除都产生合法 action/result，原始 Markdown 字节不变。
- [ ] 开启计划后，`daily-schedule-status` 返回 enabled。
- [ ] LaunchAgent 已实际加载；21:00 或受控日历触发得到 `runner_status`。
- [ ] 08:00 对完整昨天返回不运行，对晚到/编辑/失败或 Review 无效的昨天只恢复昨天；无 bundle 的可信 daily `no_change` 可完成，有旧 bundle 时 Review 仍须有效。
- [ ] 断网或无效 Key 时原文与上一版主页仍可读。
- [ ] 修改/删除长期理解后，刷新和后续 Worker 不覆盖用户版本。
- [ ] 迁移前后原始日记、Reviews 与既有 Agent V1 对象 hash 不变。
- [ ] 卸载/回滚后原始记录、附件、Reviews 与认知状态仍在本地。

工作区自动测试、真实 DeepSeek、真实安装、Chrome 人工验收和实际日历唤醒的最终结论，以同目录 [RELEASE_CHECKLIST](RELEASE_CHECKLIST.md) 的发布记录为准。
