# Memento Context Agent MVP

Context Agent 从每日记录中最多提出一条可复用信息，先把它保存为候选；只有用户做出 `confirm`、`edit` 或 `scope` 决定后，才会写入 `Context/Confirmed/`。Agent 不修改 `YYYY-MM-DD.md` 原始记录。

实现只使用 Python 标准库。DeepSeek API Key 优先从进程环境变量 `DEEPSEEK_API_KEY` 读取；macOS 上未设置环境变量时，会回退到当前用户的系统钥匙串。Key 不会写入配置、候选、决定或 usage 日志。

## 现有 Context / Reflection CLI 节点及必要性

1. `generate`：让模型做语义判断，但在落盘前用确定性代码校验证据、敏感信息、来源 hash 和 JSON 合同。模型输出和用户事实由此分开。
2. `validate`：独立复查模型响应、候选或已确认 Context。Dashboard 和自动化流程可以在展示或使用前重新验证。
3. `decide`：把用户的确认、编辑、限域、仅本次使用或拒绝保存为明确决定。只有前三种会创建长期 Context。
4. `pack`：只读取仍可验证的 `Context/Confirmed/*.json`，生成可交给下游任务的 Markdown Context Pack。
5. `reflect`：处理一条由 Dashboard 写入的 Self Reflection 请求，核对最近 14 个自然日、已确认 Context、反例和逐行证据，再写入同名 response。
6. `self-reflection-worker`：一次处理全部尚无 response 的 pending request。LaunchAgent 监视 requests 目录后运行一次并退出；单条失败会形成 error response，不会阻断后续请求。
7. `profile`：从当前仍能通过严格响应、来源 hash 和 feedback 绑定校验的 Reflection 重建标签画像。这是只读派生视图，不调模型，也不写 `Context/Confirmed/`。
8. `eval`：用同一组合成样例比较合同、证据、预期状态、Token 与成本，避免只凭一次看起来不错的回答判断模型。
9. `agent-*`：运行 Re:member Agentic Workflow，管理总开关、每日 21:00 计划、request / worker 与人物理解投影。手动与 scheduled request 共用同一套调查、校验和入库链路。

## 安装后的调用方式

Installer 会把本目录放在 vault 的 `.context-agent/runtime/` 下。默认 vault 为 `~/AISecretary` 时：

```bash
python3 ~/AISecretary/.context-agent/runtime/context_agent.py --help
```

源码与运行状态彼此分离：

```text
~/AISecretary/.context-agent/runtime/       # CLI 源码
~/AISecretary/.context-agent/candidates/    # 已验证、待决定的候选
~/AISecretary/.context-agent/decisions/     # 用户决定
~/AISecretary/.context-agent/reflections/   # 可重建的 Self Reflection 内部缓存
~/AISecretary/.context-agent/self-queries/requests/   # Dashboard 写入的待处理请求
~/AISecretary/.context-agent/self-queries/responses/  # Worker 写入的不可覆盖响应
~/AISecretary/.context-agent/self-queries/feedback/   # 用户对某条 insight 的校准
~/AISecretary/.context-agent/usage/         # 仅 Token 与成本的 NDJSON
~/AISecretary/Context/Confirmed/            # 用户确认的长期 Context
~/AISecretary/.context-agent/agent-v1/schedule.json  # 可选；缺失即关闭的每日 21:00 配置
```

## 1. 生成候选

API Key 不放在命令参数或普通文件中。macOS 上可用系统钥匙串保存；`-w` 放在末尾后会提示无回显输入，不会把 Key 本身写入 shell history：

```bash
/usr/bin/security add-generic-password \
  -a "$(id -un)" \
  -s "com.memento.context-agent.deepseek-api-key" \
  -l "Memento Context Agent · DeepSeek API Key" \
  -U -w

python3 ~/AISecretary/.context-agent/runtime/context_agent.py generate \
  --vault ~/AISecretary \
  --source 2026-08-09.md \
  --source 2026-08-10.md
```

CI 或临时会话可使用 `DEEPSEEK_API_KEY` 环境变量，它会覆盖钥匙串中的值。

不提供 `--source` 时，CLI 读取 vault 根目录下日期最新的 7 个 `YYYY-MM-DD.md`。默认模型是 `deepseek-v4-pro`；可显式使用 Flash：

```bash
python3 ~/AISecretary/.context-agent/runtime/context_agent.py generate \
  --vault ~/AISecretary \
  --model deepseek-v4-flash
```

结构化提取默认 `thinking=disabled`、`temperature=0`。需要测试 thinking 时可使用：

```bash
python3 ~/AISecretary/.context-agent/runtime/context_agent.py generate \
  --vault ~/AISecretary \
  --thinking enabled \
  --reasoning-effort high
```

## 2. 校验

```bash
python3 ~/AISecretary/.context-agent/runtime/context_agent.py validate \
  --vault ~/AISecretary \
  --input ~/AISecretary/.context-agent/candidates/ctx_xxxxxxxxxxxxxxxxxxxxxxxx.json
```

严格合同位于：

- `schemas/model-response.schema.json`
- `schemas/stored-candidate.schema.json`

运行时还会执行 JSON Schema 文件无法表达的检查：

- `evidence.file` 必须是 vault 根目录的 `YYYY-MM-DD.md`，且不能通过符号链接越界；
- `line` 从 1 开始，`quote` 必须和该行逐字一致；
- `work_preference` 至少需要两个不同日期文件的证据；
- 类别只允许 `project_decision`、`constraint`、`work_preference`；
- 模型被明确要求对敏感推断返回 `no_candidate`；确定性校验会拒绝 `sensitive=true`、内置敏感模式和 `uncertainty=high`。词法后备明确覆盖常见情绪/心理状态词，但它仍不是完整的敏感信息分类器；
- 模型调用前后的全部来源 SHA-256 必须一致。

## 3. 用户决定

```bash
# 原样确认
python3 ~/AISecretary/.context-agent/runtime/context_agent.py decide \
  --vault ~/AISecretary --candidate ctx_xxx --action confirm

# 用户修改后确认
python3 ~/AISecretary/.context-agent/runtime/context_agent.py decide \
  --vault ~/AISecretary --candidate ctx_xxx --action edit \
  --statement '先给结论，再补充必要细节。'

# 限定使用范围后确认
python3 ~/AISecretary/.context-agent/runtime/context_agent.py decide \
  --vault ~/AISecretary --candidate ctx_xxx --action scope \
  --scope 'Memento MVP'

# 仅本次使用，不写入 Context/Confirmed
python3 ~/AISecretary/.context-agent/runtime/context_agent.py decide \
  --vault ~/AISecretary --candidate ctx_xxx --action just_once

# 拒绝，不写入 Context/Confirmed
python3 ~/AISecretary/.context-agent/runtime/context_agent.py decide \
  --vault ~/AISecretary --candidate ctx_xxx --action reject
```

决定按 candidate 加锁。若进程在 confirmed 文件写完、decision 写入前中断，重试相同操作会核对已有内容、沿用原 `confirmed_at` 并补齐 decision；不一致的重试会被拒绝。

## 4. 生成 Context Pack

```bash
python3 ~/AISecretary/.context-agent/runtime/context_agent.py pack \
  --vault ~/AISecretary \
  --scope 'Memento MVP' \
  --output /tmp/memento-context-pack.md
```

指定 scope 时会包含该 scope 及 `global` Context。来源 hash 或引用失效的 Context 会跳过并计入 `invalid_skipped`。

## 5. 按需 Self Reflection

Self Reflection 不是“人格判决”。它只回答所选记录和已确认 Context 能够支持的局部工作理解，例如近期关注、项目判断、约束和协作偏好。回答是可重建的派生视图，**不会自动写入 `Context/Confirmed/`**。

Dashboard 在下列路径创建请求：

```text
~/AISecretary/.context-agent/self-queries/requests/srq_<24 hex>.json
```

请求是严格 JSON 合同，只允许下列字段：

```json
{
  "schema_version": "1.0",
  "id": "srq_111111111111111111111111",
  "kind": "self_reflection_request",
  "status": "pending",
  "created_at": "2026-08-11T10:00:00+08:00",
  "question": "现在，你怎么看我？",
  "as_of": "2026-08-11",
  "window_days": 14
}
```

可人工处理单条请求：

```bash
python3 ~/AISecretary/.context-agent/runtime/context_agent.py reflect \
  --vault ~/AISecretary \
  --request srq_111111111111111111111111
```

Worker 一次处理 requests 目录中全部尚无同名 response 的请求，这也是 LaunchAgent 调用的稳定命令：

```bash
python3 ~/AISecretary/.context-agent/runtime/context_agent.py self-reflection-worker \
  --vault ~/AISecretary \
  --once
```

响应写入：

```text
~/AISecretary/.context-agent/self-queries/responses/<同一 srq id>.json
```

公共 response 顶层是单一的 16 字段严格合同：

```text
schema_version, request_id, kind, status, created_at, cache_hit,
question, as_of, window_days, record_days, source_hashes,
confirmed_contexts, reflection, usage, error, error_kind
```

- `status` 只能是 `ready`、`insufficient_evidence` 或 `error`。
- `source_hashes` 覆盖本次时间窗口内全部发给模型的记录；所有 `evidence` 和 `counterevidence` 的 `file`、`line`、`quote` 必须被其覆盖并和原文逐字一致。
- `confirmed_contexts` 是本次纳入 prompt 的有效已确认 Context 数量，不是新写入数量。
- `usage` 为 `null` 或完整的现有 `model_usage` 事件；缓存命中不会伪造新 usage。Provider、model 和内部 generation key 不会重复暴露在公共顶层。
- `reflection` 包含 `summary / scope_note / unknown / insights`。每条 insight 只能是 `confirmed / observation / change / tension`；`observation` 至少要两个不同记录日，`change/tension` 必须同时给出正反证据，`confirmed` 必须引用已验证 Context id。
- 明确询问“变化 / 改变 / recent change / changed”时，只允许返回 `change` 或 `tension`；`change` 的新证据必须晚于旧证据，并且逐字证据中至少出现一个明确变化表达（如“不再 / 改为 / 转向 / 替代 / 修订 / 调整为 / 已变化”或英文等价词）。`tension` 的逐字证据中至少要出现“冲突 / 不一致 / 相反 / 一方面…另一方面 / 但同时”等明确张力表达或英文等价词。没有这些显式信号、成对的新旧证据或并存张力时，固定返回 `insufficient_evidence`，不会用稳定的 `observation` 填充答案。这是一道保守词法门，可能漏掉只有通过语义才能识别的隐含变化或张力，但不会尝试把范围兼容的两条记录强行解释成变化。

时间窗口按自然日计算：`as_of` 当日与向前 13 日，而不是“最近 14 个有记录的文件”。无记录日不会被当作反例。日记中匹配敏感边界或密钥形状的行会在发送给 DeepSeek 前移除，也不能成为证据。模型输出若出现敏感推断、固定人格标签、伪造 quote、越界文件或高不确定性，确定性代码会拒绝并写入 error response。

内部缓存位于 `.context-agent/reflections/`，缓存键绑定问题、时间窗口、模型、每日记录 hash、已确认 Context hash、有效 feedback hash 和 prompt 合同版本。原记录、Context 或本次纳入的有效校准变化后不会复用旧缓存。缓存不包含 usage 或 API Key。

用户校准写入 `.context-agent/self-queries/feedback/srf_<24 hex>.json`，严格字段为：

```text
schema_version, id, kind, status, created_at, request_id,
insight_index, action, note, response_sha256
```

`action` 只能是 `accurate / scope / edit / changed / reject`。`accurate/reject` 的 `note` 固定为 `null`；`scope/edit/changed` 必须有非空文本。`response_sha256` 将反馈绑定到用户当时看到的响应字节。后端只纳入能同时绑定现存 request、response hash 和合法 insight 索引的校准；篡改或非法文件会在本地静默跳过，不会发给模型。

下一次主动 Reflection 最多读取最近 20 条有效校准；同一 `request_id + insight_index` 只使用 `created_at` 最新的一条，同时间按 feedback id 决定。Prompt 只接收 `action / note / 原 statement / 原 scope`，并要求模型遵守 `reject / edit / scope / changed`。确定性代码会拦截被校准过的原 `statement + scope` 逐字回流；对语义改写后的重复误解，当前还不能宣称完全拦截。**有效 feedback 会立即改变可重建的主动标签画像，也会影响下一次主动理解；Daily Review 和长期记忆尚未接入，不会自动修改 `Context/Confirmed/`。**

如果模型忽略一条已验证校准，后端不会重试付费，也不会缓存违规模型响应；本次 `usage` 会如实保留，用户侧安全降级为 `insufficient_evidence`，并显示“你的校准已生效，但当前材料还没有形成新的可靠理解。”

## 6. Self Reflection 与 Context 的边界

- Reflection 是只读观察，不是用户事实。
- 用户提问一次不会扩大长期记忆。
- `confirmed` 只表示它逐字引用了本次仍有效的已确认 Context，不表示该理解永久正确。
- `observation / change / tension` 在本次回答中仍是观察，不会混入 Context Pack。
- 能否把某条 insight 转成长期 Context，仍需要单独的候选、证据验证与用户确认链路。

## 7. 重建主动标签画像

JSON 是给 Dashboard 和其他本地消费者的默认输出：

```bash
python3 ~/AISecretary/.context-agent/runtime/context_agent.py profile \
  --vault ~/AISecretary
```

也可输出带 response digest、原始来源 hash 和逐行引文的 Markdown 安全包：

```bash
python3 ~/AISecretary/.context-agent/runtime/context_agent.py profile \
  --vault ~/AISecretary \
  --format markdown
```

投影只纳入 `status=ready` 且当前仍可严格校验的 response。来源文件已变、非法/敏感 response、无法绑定原 response 字节的 feedback 都会被排除。标签 id 的精确键为 `normalize(statement) + "\n" + normalize(scope)`。`normalize` 固定为 `pinned-ws-ascii-lower-statement-scope-fnv96-v1`：仅将 `U+0009–U+000D / U+0020 / U+00A0 / U+1680 / U+2000–U+200A / U+2028 / U+2029 / U+202F / U+205F / U+3000 / U+FEFF` 的连续字符折叠为一个 ASCII 空格并去除首尾 ASCII 空格，仅把 ASCII `A–Z` 转为 `a–z`，其他 Unicode 原样保留。它不依赖运行时 NFKC、Unicode lowercase 或 `\s`，避免 Python/Node 的 Unicode 版本差异改变标签身份。`ptag_` 后的 24 位 hex 由三组不同 seed 的无符号 32-bit FNV 派生 hash 拼接，按 JavaScript UTF-16 code unit 处理 emoji。只改变展示标题仍会合并；模型改写 statement 或 scope 的近义文本不做模糊合并。Python 与 JavaScript 共用 `tests/fixtures/self_reflection_tag_id_vectors.json`，其中包含 `Straße`、emoji 和 `U+1C89` 向量，锁定跨运行时结果。

每个 tag 的状态只会是 `system_observation / continuing / changing / user_edited`。重复提问并不代表持续成立：只有原 insight 引用有效 confirmed Context、用户做过 `accurate`，或聚合后的支持证据来自至少 3 个不同日期文件时，才会是 `continuing`。无操作且证据不足该阈值的观察是 `system_observation`，绝不会变成长期确认。

Feedback 使用分维度 reducer，不是简单 latest-only。任一合法 `reject` 都是该 exact tag 的终态 tombstone，后续 `edit / scope / accurate / changed` 不能复活。在未删除的 tag 上，最新 `edit` 独立保留 statement，最新 `scope` 独立保留范围；后续 `accurate`、`changed` 或另一维度的修改只变更状态，不回退已修订内容。完整 feedback history 保留在 provenance 中。

`--format markdown` 只有静态标题与一个单行、紧凑的严格 JSON data block。所有标题、引文、HTML、code fence 和类指令文本都被作为 JSON string，其换行被转义，不能跳出数据边界或新建 Markdown 结构。所有结果均可重建，不会暗写长期 Context。

## 8. 评测与成本

离线评测不会调用 API：

```bash
python3 context-agent/context_agent.py eval
```

实时比较 Pro 与 Flash 使用同一组标记为 `live_eval` 的合成记录，不会发送用户的真实 vault 内容：

```bash
python3 ~/AISecretary/.context-agent/runtime/context_agent.py eval \
  --live \
  --vault ~/AISecretary \
  --model deepseek-v4-pro \
  --model deepseek-v4-flash \
  --output /tmp/context-agent-live-eval.json
```

每个模型分别输出：

- `contract_valid`；
- `evidence_valid`；
- `expected_status_passed`；
- `calls_attempted`、`calls_completed`、`errors_total`、`provider_errors`、`invalid_json_errors`、`contract_errors` 与 `usage_missing`；
- prompt、completion、cache hit、cache miss、reasoning Token；
- 使用该模型费率计算的美元成本。

实时评测按 case 隔离失败：一次 Provider 错误或非法 JSON 会形成 `passed=false` 的结果，但不会中断剩余 case 或另一个模型。Provider 只接受 `finish_reason=stop`；截断、内容过滤、资源不足或工具调用结束都会作为安全的 Provider 错误记录。

若非正常结束的 Provider 响应已经包含 usage，Token 和成本仍会写入本地日志并计入评测；该 case 依然失败。若 usage 整体缺失，日志会明确写 `usage_missing=true` 与 `cost_usd=null`，不会用 `$0` 表示未知成本。

内置费率快照的生效日期为 `2026-08-09`：V4 Pro 为 cache hit `$0.003625`、cache miss `$0.435`、output `$0.87` / 1M Token；V4 Flash 为 `$0.0028`、`$0.14`、`$0.28` / 1M Token。价格会变化，运行时可用 `--cache-hit-rate`、`--cache-miss-rate`、`--output-rate` 和 `--pricing-date` 覆盖，并会把实际费率写进报告。费率来源：[DeepSeek Pricing](https://api-docs.deepseek.com/quick_start/pricing/)。

## 9. Re:member Agentic Workflow V1：认知日流程与兼容入口

当前实现是 Workflow 与 Agent 的组合。全新安装默认不创建启用 gate，也不创建 `schedule.json`；安装了每日 21:00 calendar job 也不等于已开启自动整理。v0.9.0 认知主页在原文保存后逐条整理，唯一手动日级入口是“归并今天”；它与 21:00 / 08:00 任务进入统一日流程，只在存在实质长期材料时进入 Re:member Agent V1。旧“现在整理” / `agent-request` 只保留为诊断与兼容入口，它的初始观察窗口仍固定为 `as_of` 当日与向前 13 个自然日；更早记录只能通过有界历史检索取证。

当前 DeepSeek provider 没有在本项目中暴露原生 tool call，因此使用严格 JSON 的分阶段协议：

1. Candidate Scout 由 Agent 判断是否存在值得调查的候选，选择 `investigate` 或 `finish`；
2. 确定性 Workflow 读取目标、物化候选证据，并在需要时让 Search Planner 规划有界历史检索；
3. Workflow 执行检索、绑定 exact quote / source hash / target revision / user-action watermark；
4. fresh Terminal Judge 只基于物化后的 bundle 选择 `finalize_patch` 或 `finish`，确定性程序再完成 Schema、证据、tombstone、CAS 与 commit 校验。

Agent 负责候选、检索意图与终态 patch 等关键语义判断；Workflow 负责权限、工具执行、证据引用、并发和落盘。单次 mission 最多提交一个主题和一个 patch；当前 CLI 默认预算是 5 轮、5 次工具调用、40,000 总 Token 和 180,000 prompt 字符。

Candidate Scout 的人物相关性边界为：候选必须描述用户本人的稳定偏好、判断方式、工作方式，或它们正在发生的变化与张力。纯产品规格、Agent / Workflow 运行行为、存储实现、测试、迁移、发布与维护状态应选择 `finish`；只有原文明确将它表达为用户的长期偏好、个人约束或反复做法时才可候选。这一边界由 Scout 做语义判断，Workflow 不用关键词硬门代替。

当前工作区代码使用 Prompt `remember-agent-v1.22` / Workflow policy `agentic-workflow-investigation-v1.13`；stable-new identity 为 `stable-new-identity-v1.1`，terminal gate 为 `stable-new-terminal-gate-v1.0`，provider 名为 `deepseek-agentic-workflow`。历史 Prompt `remember-agent-v1.19` / Workflow policy `agentic-workflow-investigation-v1.9` 的冻结四案真实合成验收 plan 为 `a7062eeae02d7aab53e408712f025fd719eb6cb389df1cf7af4871572dcd73fe`，policy 为 `2b610931fd2aac13c02ffcfb0e82c105f3fff40a3b1fed138ce961040c0cbcf9`；`noise_stop`、`repeated_new`、`history_revise`、`tombstone_protection` 四案均通过，共 7 calls / 16,973 Token / $0.001238851，usage 完整且临时合成来源不变。该结果是 v1.19 / v1.9 历史基线，不追认当前候选的真实模型质量，也不证明 Agent 普遍优于 Workflow 或 20 日纵向稳定。

Prompt v1.20 / Workflow v1.11 的两个人物相关性真模型探针也保留为历史证据：纯系统说明负例为 `no_change`，但有 4 次 invalid action；显式个人偏好正例在 `required_identity` 修复后一次重跑为 `updated / new`。Prompt v1.21 / Workflow v1.12 的两日隔离合成 DeepSeek v3 也保留为失败账本：8 月 17 日与 18 日都在 Terminal Judge parse 阶段以 `budget_exhausted` 结束，分别有 2 / 4 次 invalid action，合计 15 calls / 32,285 Token / $0.003552094。

当前 v1.22 / v1.13 的证据按执行环境分账。工作区 v4 历史账本为：`two_day_positive_with_negative` 11 calls / 26,602 Token / $0.007166364；`original_only_retraction` 6 calls / 11,851 Token / $0.002545011；合计 17 calls / 38,453 Token / $0.009711375，`invalid_action=0`。先前已安装 runtime plan `fcdf` 也作为独立历史账本保留：合计 17 calls / 38,476 Token / $0.005513219。

当前最终已安装 runtime 证据是 plan `2d964129…`：`two_day_positive_with_negative` 11 calls / 26,544 Token / $0.003468603；`original_only_retraction` 6 calls / 11,826 Token / $0.001972841；合计 17 calls / 38,370 Token / $0.005441444，`invalid_action=0`。临时目录已清理，两个合成来源 hash 前后不变。已安装 runtime/Chrome 89/89 受管文件与实现冻结 `797a5c4` 字节一致；其中主页 authority 已收紧为 active understanding 与正式山峰精确同集。总 gate、schedule 和三个 plist 均已校验。

三份账本不互相替换或汇总。它们都没有读取真实用户 Vault；真实用户“逐条整理 → 日级归并”、Chrome 人工交互、自然 21:00/08:00 调度和真实用户长期质量仍未验收。复现入口与公开报告字段见 [`eval/cognitive-v1/README.md`](eval/cognitive-v1/README.md)。

历史 v1.19 / v1.9 运行时来自 commit `e116d4b8a3ff78f608f26d4a2f76186dca37b00e`，其安装验收 ZIP SHA-256 为 `227a82fd86ec05beae70cfa990b595433115445a8448b457c02fdbc74c84b29d`；69 / 69 个受管 Agent / Chrome 文件和 36 / 36 个原始内容文件当时均逐字一致。这些数字不是当前自动整理版的安装或实跑状态。当前版本的候选包、安装对账和隔离合成账本已在上方独立记录；真实用户日链路、Chrome 人工交互和自然调度仍须另行报告。

历史 v1.19 / v1.9 安装环境中的真实 DeepSeek 请求以 `no_change` 结束（1 call / 4,075 Token / $0.001406123）；Chrome 端完成两次 `r0 → r1` 修改，并从 fresh `base_revision=1` 发起删除，Worker 已生成 `r2` tombstone。再次运行 Worker 后 tombstone 字节不变、投影中未复活；用户刷新 Chrome 后也确认该理解没有复活。本轮没有把这次确定性 user-action reconcile 记成新的 DeepSeek 调用。该记录不当作当前人物相关性 policy 的通过证据。

`new` memory 额外使用版本化的稳定命名边界：如果同一句完整原文在至少两个不同日期文件中逐字重复，`statement` 必须复制这句原文，`scope` 必须从有限的显式领域触发词映射到规范标签；只有一个证据日时模型应先搜索历史。标题、YAML/frontmatter 和记录分隔符不参与身份推导或 evidence authorization。存在多个重复句、领域无法唯一确定，或重复内容包含敏感信息、一次性说明、合成元数据、提示注入时，控制器拒绝写入并要求停止。`stable-new-terminal-gate-v1.0` 对结构完备的稳定新理解只允许合法 `finalize_patch`；致命身份状态只允许 `finish`。没有跨日逐字重复句时仍走普通证据校验，不把这一规则夸大为通用同义识别。

安装后运行时的调用方式：

```bash
# 只检查状态，不修改 Vault
python3 ~/AISecretary/.context-agent/runtime/context_agent.py agent-status \
  --vault ~/AISecretary

# 用户明确启用；安装程序不会自动执行此命令
python3 ~/AISecretary/.context-agent/runtime/context_agent.py agent-enable \
  --vault ~/AISecretary --confirm enable-remember-agent-v1

# 只创建严格 14 日 request，此步不调模型
python3 ~/AISecretary/.context-agent/runtime/context_agent.py agent-request \
  --vault ~/AISecretary --as-of 2026-08-12

# 处理一条 request
python3 ~/AISecretary/.context-agent/runtime/context_agent.py agent-run \
  --vault ~/AISecretary --request arq_111111111111111111111111

# Dashboard 可观察 requests 目录；worker 单次处理 user-actions 和尚无 response 的 request
python3 ~/AISecretary/.context-agent/runtime/context_agent.py agent-worker \
  --vault ~/AISecretary --once

# 确定性重建并持久公共投影
python3 ~/AISecretary/.context-agent/runtime/context_agent.py agent-profile \
  --vault ~/AISecretary

# 阻止后续 request/run/worker 启动；不承诺中止已进入 Provider 调用的进程
python3 ~/AISecretary/.context-agent/runtime/context_agent.py agent-disable \
  --vault ~/AISecretary --confirm disable-remember-agent-v1

# 查看每日 21:00 配置，不调模型
python3 ~/AISecretary/.context-agent/runtime/context_agent.py agent-schedule-status \
  --vault ~/AISecretary

# 开启或关闭每日计划；写配置本身不调模型
python3 ~/AISecretary/.context-agent/runtime/context_agent.py agent-schedule-enable \
  --vault ~/AISecretary --confirm enable-remember-agent-daily-21
python3 ~/AISecretary/.context-agent/runtime/context_agent.py agent-schedule-disable \
  --vault ~/AISecretary --confirm disable-remember-agent-daily-21
```

定时 tick 对每个本地日期使用确定 request id，同日重复执行不会生成第二条 request。存在尚未终结的手动或定时 request 时，当次不新建定时 request。Mac 睡眠错过 21:00 时，如 calendar job 在醒来后被 launchd 补发，tick 只表示最近一个已到期时段，不会批量回填更早日期。在某时段已错过后才开启计划，不补跑该时段。

`agent-request`、`agent-run` 和 `agent-worker` 都会在公开命令入口 fail closed；`agent-profile` 是确定性重建投影，关闭状态下仍可用。启用文件必须是当前 uid 拥有的单链接普通文件，权限精确为 `0600`，内容精确为 `enabled-v1\n`；符号链接、硬链接、多余字节、错误权限或错误 owner 均会被拒绝。无效的已有文件不会被 `agent-enable` 覆盖，也不会被 `agent-disable` 删除。

离线测试可向 `agent-run` 或 `agent-worker` 传入 `--mock-steps <JSON 文件>`。文件必须是严格 action array，这条路径不调 provider。

持久化合同：

```text
~/AISecretary/.context-agent/agent-v1/requests/arq_*.json
~/AISecretary/.context-agent/agent-v1/responses/arq_*.json
~/AISecretary/.context-agent/agent-v1/runs/arun_*.json
~/AISecretary/.context-agent/agent-v1/memories/mem_*.r000001.json
~/AISecretary/.context-agent/agent-v1/user-actions/uact_*.json
~/AISecretary/.context-agent/agent-v1/profile.json
~/AISecretary/.context-agent/agent-v1/schedule.json
```

- `mem_<24 hex>` 是稳定记忆 id；每次变更创建新的不可变 revision，不覆盖旧文件。
- `runs` 保存 action、`reason_code`、arguments hash、结果数量与错误类型，不保存模型的 CoT 正文。
- 每次 Provider 调用前会先持久不含 prompt 的 `provider_attempt_started` marker。如果进程在调用期间被强制终止，重启后会将原 request 终结为 `unknown_attempt`，不会自动再调一次 Provider；用户只能用新 request 显式重试。
- `profile.json` 是 Dashboard 可严格校验的公共投影，包含 active memories、最新完成 run 的审计摘要和 user-action 统计，不包含日记全文或内部 prompt。
- Dashboard 不直接改 memory revision。它只写入不可变 `edit / delete` user-action，并绑定 `memory_id + base_revision + base_revision_sha256`。投影会先叠加合法 action 使其立即可见；只有 worker 持锁生成新 revision。
- `delete` 会生成终态 tombstone。旧 Reflection 中可验证的 `reject` 也会首次迁移为 tombstone。阻止复活的确定性边界是精确归一化语义键；当前代码不声称能识别所有近义改写。
- 日记原文和 `Context/Confirmed/` 在 Agent V1 中都是只读输入，不会被 patch 修改。

`agent_run_key` 绑定近期来源、全部可搜索日历史的 hash、画像、feedback、user-action、prompt / tool / schema 版本、provider / model 和预算。不同 request id 但实质输入相同时，已有安全终态可以 0 次新模型调用复用；只因 14 日滑窗自然淘汰且历史文件字节未变时也会确定性跳过。新增、修改、窗口内删除或任一可搜索日记的字节变化都会触发新 mission。提交前会再核对来源、画像、feedback 与 user-action watermark；不一致时返回 `stale`，不写 memory patch。

## 测试

```bash
python3 -m unittest -v \
  tests/test_context_agent.py \
  tests/test_self_reflection_backend.py \
  tests/test_context_agent_rich_scenario.py \
  tests/test_remember_agent_v1.py \
  tests/test_remember_agent_v1_activation.py \
  tests/test_remember_agent_schedule.py \
  tests/test_agentic_workflow.py \
  tests/test_remember_agent_workflow_mvp_live.py
```

九个合成样例覆盖工作偏好、项目决定、两种约束、无长期价值记录、冲突证据、提示注入、伪造 quote 和敏感推断；其中七个进入 live 模型对比。离线通过只证明代码合同与这些已知样例一致；真实记录上的候选质量需要实时评测和用户反馈验证。

[判断] 关键词与模型自报不能证明所有语义层面的敏感推断都会被识别，因此 MVP 仍要求用户确认，不能把 pending candidate 当作用户事实直接使用。
