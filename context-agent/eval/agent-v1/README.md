# Re:member Agent V1 轨迹评测

离线入口只做可复现的 mock trajectory 评测，不读取真实 Vault，不调用
DeepSeek，也不写入 Agent memory。本目录保留历史评测 runner，并新增当前四案
Agentic Workflow runner。当前候选为 Prompt `remember-agent-v1.19`、Workflow policy
`agentic-workflow-investigation-v1.9`；commit `e116d4b8a3ff78f608f26d4a2f76186dca37b00e`
的运行时已经打包并安全安装。用户已刷新 Chrome 确认删除结果没有复活，
验收期间临时启用的 Agent gate 已关闭；没有自动 timer。

## Agentic Workflow MVP 四案真实验收（当前候选）

2026-08-15，冻结四案计划已在系统私有的临时合成 Vault 中真实调用 DeepSeek V4 Pro，
四案全部通过预先冻结的质量与安全合同：

- plan SHA-256：`a7062eeae02d7aab53e408712f025fd719eb6cb389df1cf7af4871572dcd73fe`；
- policy SHA-256：`2b610931fd2aac13c02ffcfb0e82c105f3fff40a3b1fed138ce961040c0cbcf9`；
- 用量：7 calls / 16,973 Token / $0.001238851，usage 与成本记录完整；
- 用例：`noise_stop`、`repeated_new`、`history_revise`、`tombstone_protection`；
- 结果：四案均通过，且合成来源文件在运行前后保持不变。

四案只验证这组冻结合成合同：Agent 在候选定位、是否继续调查、历史检索计划和
终态 patch 判断等关键语义节点作决定；确定性 Workflow 负责读取、检索、证据引用
物化、Schema / source / target / CAS 校验与提交。证据必须绑定授权来源的 exact quote、
source hash、目标 revision 与 user-action watermark；用户 tombstone / edit 优先，不能被
Agent 结果覆盖。它不证明 Agent 普遍优于固定 Workflow，也不替代 20 日 live E2、真实
用户影子运行或 production-ready 证据。安装后的真实 Chrome 闭环另有独立证据，不能
倒推四案评测具有更大的统计范围。

默认命令只输出冻结计划，不调用 Provider：

```bash
python3 context-agent/eval/agent-v1/run_live_workflow_mvp.py
```

当前安装没有自动 timer，仍保持手动触发。Chrome 闭环确认后验收 gate 已关闭，
交付状态为 disabled。

```bash
python3 context-agent/eval/agent-v1/run_offline_eval.py \
  --output /tmp/remember-agent-v1-offline-report.json

python3 -m unittest -v tests/test_remember_agent_v1_eval.py
```

`--strict` 会在存在实现级红灯时返回非零。当前 runner 会明确区分：

- `observed`：从当前 `agent_v1.py` 合同和离线执行中直接验证的事实；
- `targets`：预先声明的评测门槛；
- `baselines`：W0 固定单次、W1 固定工具路径、A1 动态路径的同源 mock 对照；
- `daily_replay`：按 2026-07-14 至 2026-08-02 逐日推进的 20 日轨迹；
- `hard_gates`：证据、敏感边界、tombstone、CAS、stale 和故障保留；
- `readiness`：不允许用 mock 通过冒充真实实现就绪。

报告中的 A1 路径是确定性测试向量，只证明控制器合同可测。
[猜测] 真实模型能否稳定优于 W1，需要后续影子评测。

## 隔离的 W1 / A1 真模型配对器

`run_live_pairing.py` 与上面的 mock runner 分开。它只接受仓库内的
`priority_revision` 合成用例，不接受 `--vault`，每个 arm 在新建的 `0700`
临时目录中复制两份 `0600` 记录。默认命令只输出冻结计划，调用数为 0：

```bash
python3 context-agent/eval/agent-v1/run_live_pairing.py
```

只有同时提供下列两个参数才允许读取 `DEEPSEEK_API_KEY` 或 macOS
Keychain 并调用 DeepSeek：

```bash
python3 context-agent/eval/agent-v1/run_live_pairing.py \
  --live \
  --confirm-live LIVE_SYNTHETIC_ONLY \
  --expect-plan-sha256 <上一步计划输出的 plan_sha256>
```

`--expect-plan-sha256` 必须与事先审阅的 plan 完全一致；合成来源、Prompt、
模型、预算、W1 固定 query/终态约束或 runner 质量合同有任一变化都会
在构造 provider 前拒绝执行。冻结合同同时包含 runner 整文件字节摘要和
关键运行时符号清单摘要；修改源码或在运行时替换隔离、计量、provider、
质量门、报告脱敏或 CLI 边界任一关键实现，都会使 plan hash 失效。
同一计划也绑定 `agent_v1.py`、`core.py`、`reflection.py` 和
`deepseek_provider.py` 的完整文件摘要与项目运行时 namespace 闭包：
每个模块自定义的全部顶层函数/类、类的运行时方法、全部大写合同常量，
以及 Agent 在自身 namespace 实际解引用的 29 个 `core.py` /
`reflection.py` 外部别名和 Reflection 实际解引用的 19 个 `core.py` 外部别名。
编译正则会绑定 pattern 和 flags，定价 dataclass
会绑定逐字段值；不可稳定序列化的新常量会 fail closed。标准库模块不在
这一项目代码威胁模型内。
项目模块及其 `from ... import ...` 本地别名还必须与 `sys.modules` 中的
canonical 对象保持 identity：Agent 从 Core/Reflection 导入的 29 个别名、
Reflection 从 Core 导入的 19 个别名会同时核对 AST 来源与当前对象身份；
即使把同一源码重复加载为另一个 module，也不能沿用旧计划。
Agent loop、证据验证、usage/成本或密钥读取
实现变化同样会在构造 provider 前拒绝旧 plan。
live 函数的 provider factory 默认值固定为 `None`；只有精确 plan SHA
校验通过后，才会解析到已冻结的 module-global factory。
三套真模型 runner 也各自绑定本模块定义的全部顶层函数/类（包括类的当前
运行时方法）与全部大写行为常量；`Path` 只以路径字符串摘要进入内部 hash，
不会进入公开计划。这个边界用于正常 CLI 中检测项目代码漂移，不宣称抵抗
任意进程内代码执行者同时替换 freeze/assert/verifier，也不覆盖标准库 monkeypatch。

首轮默认仅1对：W1 固定执行 `read_memory → search_history`，然后让同一
DeepSeek 模型输出终态 patch；A1 使用真实 Agent V1 loop，最多3回合。
批次硬上限为4次调用、8万 tokens 和 0.10 USD；usage 缺失、provider
错误、超预算或安全异常都会立即停止后续 arm。单个 arm 仅质量不达标
时仍会跑完同一对的另一个 arm，并将 `batch_quality=false`；这不会被冒充为
安全故障。公共报告只保留质量
检查、trajectory、tokens 和成本，不写入提示词、日记、记忆正文、本地路径、
request/run id 或 API Key。live pairing CLI 不接受任意 `--output` 路径，严格
公共 projection 只写到 stdout。

这个 focused gate 只比较同一修订任务的真实完成质量与成本。W1 是一个
预先给定 target memory 和检索短语的 `oracle_assisted_fixed_workflow`，只是该
修订 case 的强基线，不代表通用 Workflow。因此不能把 W1/A1 调用数差
直接解读为 Agent 增益。

已执行的 focused 配对中，W1 和 A1 均为 `updated / revise`、
15 / 15：W1 为 1 次调用 / 2,987 Token / $0.00138417，A1 为
2 次调用 / 5,001 Token / $0.001286382。这是一个 focused synthetic
revise 对，不是 Agent 优于 Workflow 的证据。

离线安全合同：

```bash
python3 -m unittest -v tests/test_remember_agent_v1_live_pairing.py
```

## 20 日 W0 / W1 / A1 E2 冻结计划

`run_live_e2.py` 把上述 focused pairing 扩展为 20 日按日推进的独立
W0/W1/A1 状态实验。默认只会输出冻结计划，不构造 provider、
不读 API Key、不读写真实 Vault：

```bash
python3 context-agent/eval/agent-v1/run_live_e2.py
python3 -m unittest -v tests/test_remember_agent_v1_live_e2.py
```

三臂的强公平定义已冻结：

- W0：当日有唯一 active target 时，确定性预处理器把完整 current
  revision 与 CAS binding 放入 prompt，模型只做 1 次终态决策；
- W1：确定性执行可选 `read_memory` 和一次 literal search，模型只做
  1 次终态决策；
- A1：真实 Agent V1 动态 loop，在同一日记录和同一模型上自主选路。

每臂都从空白合成 Vault 开始，只累积自己前一日的结果；不用 oracle
修复错误状态。日级 oracle 唯一来源是 `run_offline_eval.py` 的
`DAILY_TARGETS`，该离线 runner 的完整源码与项目 runtime namespace（包括
`TOPICS`、`DAILY_TARGETS`）也进入 E2 依赖合同。执行顺序由冻结的
`date_order` 明确给出：必须恰好 20 个连续 ISO 日期并按日期升序，不能依赖
mapping 插入顺序。报告输出 operation macro-F1、路由、路径、安全和成本。
E2 还绑定 pairing runner 的完整项目 runtime namespace，并要求本地
pairing/Agent/Core/provider/offline aliases 及 `offline.agent_v1` 都指向已审阅
的 canonical module 对象。
普通质量失败会完成同日三臂后停止；安全、usage、provider、
budget、runtime、tombstone、feedback 或 identity-label 异常立即停止，
不再构造后续 arm 的 provider。
报告保留 `first_error_day` 和 cascade 观察是否被停机截断。

provider 的 `timeout_seconds` 与 model、max tokens、Agent budget 一样进入
frozen public contract 和 `plan_sha256`；合同还直接记录
`make_agent_policy_sha256()` 的当前结果，包括 conflict-investigation
instruction、stable-new identity instruction 与 scope rules。修改 timeout 或该
Agent policy 后，旧 SHA 都会在构造
provider 前被拒绝。一旦已有计量调用，后续 fixture drift 或报告安全
失败仍返回 `executed: true` 的有限停机报告并保留 calls/tokens/cost；
只有 CLI 无法判断是否已执行时才使用 `executed: null`，不伪报 false。
公共报告验证器会用冻结公开合同、limits、daily oracle 和 arm 执行周期按
同一公式重算 `plan_sha256`；仅替换成另一个合法 64 位摘要也会被拒绝。

冻结计划中的硬上限为 100 次调用、120 万 tokens 和 1 USD。[猜测]
它们只是安全熔断值，不是实际成本预测。真实执行接口需要两次显式确认和
已审阅计划的 SHA：

```bash
python3 context-agent/eval/agent-v1/run_live_e2.py \
  --live \
  --confirm-live LIVE_20D_SYNTHETIC_ONLY \
  --expect-plan-sha256 <上一步的 plan_sha256>
```

当前 20 日逐日 operation 只覆盖 `new`、`reinforce`、`no_change`，
不覆盖 `revise`/`tension`。因此 E2 通过也不能独立证明全部 Agent
patch 空间或 Agent 优于强 Workflow。`material_gate_probe` 是对已处理
的完全相同状态做重放，它只验证该重放为 0 调用，不能证明当日
首次 `no_change` 判断可以免调用。真实 API 命令必须在独立 QA
再次审阅新的 `plan_sha256` 后才能执行。
当前 20 日 live E2 **未执行**；本次 disabled RC 已决定跳过。该决定
不产生纵向证据；不得把 focused pairing、mock 回放或 preflight 当作
纵向稳定性证据。

## 六路径真模型 preflight

`run_live_preflight.py` 是独立的 6 case × 3 arm 发布前门禁。它复用
pairing runner 的 trusted scratch、provider 计量、model 核对和严格 usage
边界，但不读取真实 Vault，也不接受 `--vault` 或 `--output`。默认只输出
冻结计划，不读取 Key，不调用模型：

```bash
python3 context-agent/eval/agent-v1/run_live_preflight.py
```

冻结矩阵覆盖：

- `direct_stop`：提示注入记录只能 `finish`；
- `profile_only_reinforce`：先 `read_memory` 再强化已有理解；
- `history_search_new`：通过 `search_history` 获取 14 日窗口外的第二个证据日；
- `current_boundary_stop`：激活优先的 active memory 来自专用 07-14
  fixture；14 日窗口内的 07-19 只说需要核对周中讨论、尚不能判断变化。
  W0 物化冻结证据后直接 `finish`；W1 保留固定的
  `read_memory → search_history(query=激活优先级, date_to<=2026-07-18) → finish`，
  并找到窗口外 07-17 的“重新讨论但无新决定、指标口径未确认”；A1 则在
  `read_memory → finish` 主动停止，不做一次无必要的历史检索。三臂都以
  `no_change` 结束。该案不再使用普通日记中的用户确认或权限命令作为 oracle；
- `history_search_revise`：以 07-14 的激活优先为 seed，07-31 的 14 日窗口只直接暴露
  07-26 的留存支持；`read_memory → search_history → finalize_patch`必须找到
  窗口外 07-17 的新方向与明确替代信号，并以 07-14 原方向作为 counterevidence
  才允许 `revise`；
- `revision_conflict`：模型返回终态 action 后、提交前写入同形用户事件，三臂均必须 stale/CAS，不得新增 Agent revision。

W0 是 `oracle_assisted_one_shot`，W1 是
`oracle_assisted_fixed_workflow`，A1 才是真实动态 Agent loop。W0 与 W1 先共享
`build_agent_messages` 生成的同一 recent window/active profile；W0 仅把 W1 原本会得到的
`read_memory` 结果和 literal `search_history` matches 各物化一次，然后做一次
终态调用。它不重放 recent records，不重放 raw seed 文件，也不另外注入
active memory；如果该案没有 W1 工具，W0/W1 都只追加同一个 terminal constraint。
六案的 prompt occurrence 合同会检查 W0/W1 中每条 fixture 文本
出现次数一致；`current_boundary_stop` 的 07-19 recent quote 在两臂各一次，
07-14 raw seed 标题在两臂都不会被额外物化。这个矩阵只能验证
六类工程路径与质量/成本对照，不宣称 Agent 相对 Workflow 已产生增益。
普通质量失败会跑完当前 case 的三臂后停批；安全、usage、provider 或预算失败
会立即停批。两个基线合计固定 12 次调用，A1 理想轨迹合计 12 次，理想总数 24；
为覆盖 6 个 A1 各自全部 3-turn 的合法路径，硬上限是 30 次调用、25 万 tokens 和
0.20 USD。[猜测]

质量结果与工程错误分开记录：若 A1 产生合法、完整审计的终态
决策，但与冻结 oracle 的预期结果不同，
run 仍为 `error_code=none` 和 `audit_clean=true`，由 quality checks 标记
`outcome_expected=false` 并触发 `quality_gate`。只有本地审计、Provider、
usage、预算或安全路径真正失败时，才使用非 `none` 的工程错误码。

`new` 的 stable identity 是一个保守的命名合同，不是语义分类器：
只有一条合格完整句跨至少两个不同日期文件逐字重复时，
`statement` 才能逐字复制该句；`scope` 只能由版本化 canonical trigger
映射得到。候选句或最长 trigger 映射不唯一、缺少 scope、命中排除语义
或不安全文本时必须 `finish`，不得同义改写。近义新文案仍可能形成不同
exact key，不能据此宣称语义去重已解决。

冻结合同同时绑定 runner/dependency 源文件、实际 runtime symbols、
stable-new identity instruction/rules 的 policy SHA，以及
`derive_stable_new_identity`、`_validate_patch_semantics`、
`_evidence_patch_guidance` 等实际判定函数；其中任一项改变都会使旧
plan SHA 在 provider 构造前失效。Public report 另会从每个 run
重算 quality score/visible checks、usage completeness、已完成 case 数、
status/stop code，以及 calls/tokens/cost 的 arm 和 batch 聚合；
内部不自洽的报告不会输出为可用验收结果。

W0/W1 的固定工具轨迹继续精确核对 action、query/date/limit 或 memory target、
`result_kind` 与 `result_count`，因此强基线与物化公平合同不变。A1 的
预期工具合同由该案 `a1_trajectory` 的非终态步骤生成，`read_memory` 仍须命中冻结
target；当轨迹包含 `search_history` 时，A1 允许自主选择
query 与日期范围，只要结果非空且不超过 5 条，并且 response/run 的完整
`source_hashes` 都精确等于该 case 的授权 source 集合。最终 memory 的
evidence、counterevidence 与 source hashes 仍执行原有 exact 门禁；缺少预期
source、读错 memory 或引入额外 source 都会触发 `quality_gate`。公开报告只输出
`tool_contract_valid` 布尔值，不输出查询正文、memory ID 或 Provider 回包。
公开报告继续使用 `remember_agent_live_preflight.v4`；本轮 matrix 升为
`remember-agent-preflight-6case-v5`，A1 工具验收策略升为
`a1-trajectory-semantic-search-authorized-sources-v2`。旧 matrix v4 的计划、报告和
真实 run 不追认为本版结果。

每臂会在进入隔离 clone 前检查一次冻结合同，并在 clone 完成后、构造
Provider 前再检查一次；fixture 在 clone 期间发生变化时，不会进入计费调用。
真实入口的 `provider_factory` 默认值是 `None`；只有上述检查全部通过后，
runner 才从已绑定的 module global 解析 `default_provider_factory`。函数默认值或
global factory 被替换都会让旧 plan SHA 在 Provider 构造前失效。
冻结运行时使用 dependency-grade fingerprint，除了函数本身，也绑定
`FrozenContract.__eq__`、`ConflictDelegate.__init__` 等 class runtime members；这些方法改动后，
旧 plan SHA 会在 Provider 构造前被拒绝。

`current_boundary_stop` 的三个 Markdown 只存在
`agent-v1/fixtures/history-ambiguous-stop/`，不修改共用 20 日数据，因此不会
改变 E2 样本。fixture set 名会进入 matrix hash，每个 fixture 文件的 SHA
会进入 `fixture_sha256`，两者连同 runner/runtime 一起进入 plan SHA。发布
归档包含整个 `context-agent` 目录，未跟踪的 fixture 会被现有发布检查拒绝。
旧 `counterevidence_search` 的真模型结果不追认为新案结果；新的 matrix/fixture/plan
SHA 必须重新通过独立审阅和 live preflight。

严格 usage 字段仍是成功调用的硬门槛。若 Provider 已返回部分、非零 token
小计但缺少严格字段，runner 会停止后续调用，把可归一化的 token 同时计入
run 与 batch，并保持 `cost_complete=false`、`cost_usd=null`，不会把它当成
完整 usage 或零成本。若已发生 Provider 调用后，常规 public report 又在末端
校验失败，CLI 只输出 `executed=true`、有限 stop code 和纯数字 batch meter
的应急投影；调用前的确认或 plan mismatch 仍输出 `executed=false`。应急投影
不包含响应正文、实际 model 字符串、本地路径或标识符，也不算验收通过。

真模型命令必须同时提供专用确认词和当前 plan SHA；在 QA 批准前不应执行：

```bash
python3 context-agent/eval/agent-v1/run_live_preflight.py \
  --live \
  --confirm-live LIVE_SYNTHETIC_PREFLIGHT_ONLY \
  --expect-plan-sha256 <审阅过的-plan-sha256>
```

离线 fake-provider 和安全合同：

```bash
python3 -m unittest -v tests/test_remember_agent_v1_live_preflight.py
```

## 历史：手动启用前的两案 A1 gate

`run_live_manual_gate.py` 只复用上述 preflight 中已冻结的
`history_search_revise` 和 `revision_conflict` 两个 A1 案例，不跑
W0 / W1 或 20 日 E2。默认命令只输出计划，Provider 调用为 0：

```bash
python3 context-agent/eval/agent-v1/run_live_manual_gate.py
```

当前 manual report schema 为 `remember_agent_live_manual_gate.v3`；production 与该 gate 的默认
预算为 `max_turns=5`、`max_total_tokens=20,000`。通用 Agent 与 preflight 继续使用
`max_turns=3`、`max_total_tokens=12,000`，不会把新候选预算追认进历史结果。20,000 是基于已执行
样本选择的五回合工程阈值 [猜测]，不是总 Token 绝不越过该值的保证：Provider 返回后才能累计
本次 usage；若累计超额，completion 会被解析并进入公共审计，但 action 不执行、不写 memory、
不再调用 Provider。两案理想轨迹合计 5 次调用，考虑两类有界终止复核后的 batch hard cap 为 9。
pre-read 复核只在未成功读取且拒绝后至少还有 3 回合时，最多拒绝第一次 `finish`；
post-read 复核只在紧接一次成功 `read_memory`、未尝试其他动作且拒绝后至少还有 2 回合时触发一次；
它的 ToolResult 不含 target、query、日期、patch 或下一动作推荐。两类拒绝都计入模型回合与 usage，
不计工具调用，且同阶段再次 `finish` 可被接受，不会替模型选择 memory、query 或 patch。第一案必须
产生 `updated / revise` 和
`read_memory → search_history → finalize_patch`，并精确通过
revision、evidence、counterevidence、source hashes、usage 和来源副本不变检查。
第二案必须产生 `stale`：旧 revision 不变、Agent 新 revision 数为 0、
用户 action 获胜、来源副本不变。任一案失败就停止。

真模型入口需要两个独立的精确确认词和当前 plan SHA：

```bash
python3 context-agent/eval/agent-v1/run_live_manual_gate.py \
  --live \
  --confirm-live LIVE_SYNTHETIC_MANUAL_GATE_ONLY \
  --confirm-cost ACCEPT_MANUAL_GATE_PROVIDER_COST \
  --expect-plan-sha256 <审阅过的-plan-sha256>
```

该 runner 不接受 `--vault` 或 `--output`；真模型也只能在系统私有临时
scratch 中处理已签入的合成 fixture。定向 fake-provider 验证：

```bash
python3 -m unittest -v tests/test_remember_agent_v1_live_manual_gate.py
```

2026-08-15 的历史 live 使用 plan SHA-256
`aced8fc17de4e7c15de3c33c578ad02b6e2e6fdf4e95e6dc1862f803b2a37110` 和 policy SHA-256
`5a24b5e01b32815d5aa881dccd300ed2be1a61abc38469c95061a02831dc2575`；其原始公开结果为
`error_code=agent_error`。预算错误投影与 post-call Token 预算合同修正后，当前离线候选的 plan-only
SHA-256 为 `86d97e0967bd46eedf90f5cd98e2aae6c990338bd6ef2bd356d83a322c656346`，policy SHA-256 为
`ba388d00e2e9c9399c89f9bce87b6a8839aa7052da6ab1e0485dbbcd26176e02`；
`status=budget_exhausted` 且内部错误类型为 budget 时公开投影 `error_code=budget`。新计划双跑一致、
Provider 调用为 0，尚未 live；历史 plan 不能用于当前 runner。
报告按拒绝次数标记 `unassisted` / `guarded` / `scaffolded`；`task_passed` 只表示任务结果与安全合同通过，
与 release `gate_passed` 分开。单次 pre-read 复核是已审查的历史允许路径；出现 post-read 或双复核时，即使
`task_passed=true`，也不追认 release gate 通过。

2026-08-14 至 2026-08-15 已真实执行四次；四次都在第一案失败后 fail closed，第二案没有运行：

| Prompt / policy | Plan SHA-256 | 第一案结果 | 调用 | Token | 成本 USD | 已通过的非能力检查 |
| --- | --- | --- | ---: | ---: | ---: | --- |
| v1.6 | `06b518611a263a3514e1a4451802b5dfc5c605ec13112973d8c0b47c32626265` | `finish / no_change` | 1 | 2,062 | 0.00036482 | 安全审计、usage 完整、source clone 不变 |
| v1.7 | `c4f41a34b48f2781a656c6366912d0785b5f0f1af6e71c004fb4e197f42bebf3` | `finish / no_change` | 1 | 2,307 | 0.001023555 | 安全审计、usage 完整、source clone 不变 |
| v1.8 | `8c2aecab9caeec3d9f38434c6f7d20b2b1c592b238c00844c4594f4749705323` | `no_change`；`finish → read_memory → finish`；质量 4 / 14 | 3 | 8,077 | 0.001467922 | 安全审计、usage 完整、source clone 不变 |
| v1.9 | `aced8fc17de4e7c15de3c33c578ad02b6e2e6fdf4e95e6dc1862f803b2a37110` | `status=budget_exhausted`；`error_code=agent_error`；`finish → read_memory → finish`；`scaffolded`，2 次复核；质量 3 / 14 | 4 | 12,096 | 0.001351458 | usage 完整、source clone 不变 |

第一案要求 `updated / revise` 和
`read_memory → search_history → finalize_patch`。v1.9 的已解析 action 轨迹为
`finish → read_memory → finish`：两次 `finish` 分别触发 pre-read 与 post-read 复核，
因此分类为 `scaffolded`。第四次 Provider 调用的返回使累计 Token 达到 12,096，
控制器在解析 action 前按 12,000 Token 上限停止；该次 action 未知，不能猜成
`search_history`、`finalize_patch` 或 `finish`。第一案最终为
`status=budget_exhausted`、`error_code=agent_error`，质量 3 / 14，能力门失败。usage 完整和来源副本
不变不能写成 Agent 能力通过；第二案未运行。该历史分支当时保持 disabled，未重试、未安装；
不能把后续四案与当前安装反向追认为这次 manual gate 通过。

## 历史：单案 thinking 对照诊断（真实执行）

`run_live_thinking_probe.py` 是独立诊断 runner，不修改也不替代上述手动发布
gate。它只在两个相互隔离的 `history_search_revise` clone 上依次运行
`thinking=disabled` 与 `thinking=enabled, reasoning_effort=high`；两臂除 thinking
配置外共用同一模型、Prompt / policy、fixture、预算和严格 oracle。只有无
bounded-finish 拒绝、且轨迹逐字等于
`read_memory → search_history → finalize_patch` 才计为自主通过。

默认命令只生成冻结计划，不读取 Key 或调用 Provider：

```bash
python3 context-agent/eval/agent-v1/run_live_thinking_probe.py
```

该诊断固定每臂最多 4 calls、总计最多 8 calls、每次 `max_tokens=2000`、batch
最多 30,000 Token / `$0.03`。任一 safety、usage、source、contract 或 model
完整性错误立即停止；单纯质量失败仍完成另一臂，以保留同批对照。真实执行还需
两个专用确认词和当前 plan SHA；该结果不能直接替代两案 release gate。

截至 commit `d0f0137`，两次 plan-only 字节一致且均为 0 calls；随后按冻结
plan SHA
`923ebb14d8e1dd2fc314153946f89c98a6f06a25629aa4826d7d2696836fde88`。
完成真实配对：

| Arm | 轨迹 | bounded finish | 质量 | 调用 | Token | reasoning tokens | 成本 USD |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| `disabled` | `finish → read_memory → finish` | `true` | 8 / 19 | 3 | 8,077 | 0 | 0.00025317 |
| `thinking_high` | `read_memory → finish` | `false` | 9 / 19 | 2 | 6,925 | 1,405 | 0.002615336 |
| **Paired** | 两臂完成；`neither_pass` | — | 两臂均未通过 | **5** | **15,002** | **1,405** | **0.002868506** |

两臂的安全审计、usage 完整和 source clone 不变检查均通过，但都没有完成预期
`read_memory → search_history → finalize_patch`，因此不能写成 thinking 改善了能力。
该历史分支当时保持 disabled，未重试、未安装。任何 runner、fixture、Prompt / policy、
Provider 或依赖变化都会使该 SHA 失效，必须重新生成和确认。

```bash
python3 context-agent/eval/agent-v1/run_live_thinking_probe.py \
  --live \
  --confirm-live LIVE_SYNTHETIC_THINKING_PROBE_ONLY \
  --confirm-cost ACCEPT_THINKING_PROBE_PROVIDER_COST \
  --expect-plan-sha256 <审阅过的-plan-sha256>
```

定向 fake-provider 合同：

```bash
python3 -m unittest -v tests/test_remember_agent_v1_live_thinking_probe.py
```

## 当前候选与历史真模型证据状态

当前候选 Prompt `remember-agent-v1.19` / Workflow policy
`agentic-workflow-investigation-v1.9` 的冻结四案已真实全过：plan
`a7062eeae02d7aab53e408712f025fd719eb6cb389df1cf7af4871572dcd73fe`，policy
`2b610931fd2aac13c02ffcfb0e82c105f3fff40a3b1fed138ce961040c0cbcf9`，7 calls /
16,973 Token / $0.001238851。该批次 usage / 成本完整、临时合成来源不变；结论仅限
四个冻结用例与上述严格证据边界。

以下为旧 Prompt / policy 分支的历史失败账本，保留用于回归定位，不代表当前候选结果：

三次 6-case live preflight 都未全绿：

- v3：case 3 的 A1 被 exact tool oracle 假阴性拦截；
- v4：case 4 三臂均为 `no_change`，冻结 oracle 却是未定义的
  `insufficient`；
- v5：前 4 个 case 全部通过；case 5 的 W0 patch 被 evidence / security
  门拦截，W1 / A1 未运行。

因此不得宣称 6-case 全绿或 case 5 / 6 A1 已验证。三次 preflight
前后，真实 Vault hash 均零变化；这只证明该批次没有改写真实 Vault。

截至旧 manual gate 与 thinking probe 的历史账本截点，已知为 **101 次调用 / 277,551 Token /
$0.039353928**，另有 1 次 attempt 的 usage / cost 未知；这不是包含当前四案批次在内的
项目累计总账。当前四案批次单独记录为 7 calls / 16,973 Token / $0.001238851。
现有证据仍不支持 Agent 普遍优于 Workflow、20 日纵向稳定或 production-ready。

2026-08-15，commit `e116d4b8a3ff78f608f26d4a2f76186dca37b00e` 的 Prompt v1.19 /
Workflow v1.9 运行时已打包并安全安装。`dist/Memento-macOS.zip` 的 SHA-256 为
`227a82fd86ec05beae70cfa990b595433115445a8448b457c02fdbc74c84b29d`（安装验收包），checksum 已
验证；69 / 69 个受管 Agent / Chrome 文件与提交逐字一致，36 / 36 个原始内容文件与
安装前备份逐字一致。

安装环境中已完成一个真实 DeepSeek `no_change` request（1 call / 4,075 Token /
$0.001406123），随后通过 Chrome 完成两次 `r0 → r1` edit。fresh `base_revision=1`
delete 已生成 `r2` tombstone；Worker 重跑后 tombstone 字节不变，确定性投影中没有复活，
用户刷新 Chrome 后也确认该理解未复活。该 user-action reconcile 没有新增 DeepSeek 调用。

Agent plist 只监视 requests / user-actions，没有 `RunAtLoad`、`KeepAlive` 或 timer。
验收 gate 已在刷新视觉确认后关闭；fresh install 仍默认不创建 gate。
旧四次 manual gate 与 thinking probe 的能力门均失败，保留为历史失败账本，不因当前四案、
打包、安装或 Chrome user-action 成功而追认。
