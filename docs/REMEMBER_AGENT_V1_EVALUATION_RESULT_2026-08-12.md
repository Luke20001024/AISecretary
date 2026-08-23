# Re:member Agent V1 隔离真实模型评测结果

> 初始日期：2026-08-12；更新：2026-08-15；模型：DeepSeek V4 Pro；范围：临时合成 Vault 中的手动触发 Agent V1 探针
> 当前结论：**Prompt `remember-agent-v1.19` / Workflow policy `agentic-workflow-investigation-v1.9` 的冻结四案真实合成 gate 已全过；commit `e116d4b8a3ff78f608f26d4a2f76186dca37b00e` 已打包并安全安装。真实 DeepSeek request、Chrome edit / delete、`r2` tombstone 与刷新后不复活确认已完成，临时 gate 已关闭。无自动 timer；20 日 live E2 未执行。**

## 当前候选：Prompt v1.19 / Workflow v1.9 四案真实验收

| 项目 | 已观察结果 |
| --- | --- |
| 冻结标识 | plan `a7062eeae02d7aab53e408712f025fd719eb6cb389df1cf7af4871572dcd73fe`；policy `2b610931fd2aac13c02ffcfb0e82c105f3fff40a3b1fed138ce961040c0cbcf9` |
| 用例 | `noise_stop`、`repeated_new`、`history_revise`、`tombstone_protection` |
| 结果 | 四案全部通过；7 calls / 16,973 Token / $0.001238851；usage 与成本完整；临时合成来源运行前后不变 |
| 证据边界 | Agent 决定候选、调查计划与终态 patch；Workflow 物化 exact quote / source hash，绑定 target revision / user-action watermark，并执行 Schema、tombstone、CAS 与 commit 校验 |
| 发布边界 | 只证明冻结四案合同；不证明 Agent 普遍优于 Workflow，不是 20 日 live E2，也不等于安装、Chrome 或 production-ready 证据；这些链路须分别验收 |

以下保留旧 Prompt / policy 分支的成功与失败账本，用于回归定位；它们不代表当前 v1.19 / Workflow v1.9 候选结果。

## 历史 0. Prompt / policy v1.6 focused W1 / A1 真实配对

冻结 plan SHA-256：`56ec87ff4e735fd5aaeef2d2b8e8da0e9d8edcdaadbe28b381aba783834a1d82`

本次只使用仓库内 `priority_revision` 合成用例，对同一修订任务运行 W1 强基线与 A1 动态 Agent；报告只输出到 stdout，不接受任意 `--output` 路径。

| Arm | 终态 / 操作 | 真实轨迹 | 质量检查 | 调用 | Token | 成本 USD |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| W1 | `updated / revise` | `read_memory → search_history → finalize_patch` | 15 / 15 | 1 | 2,987 | 0.00138417 |
| A1 | `updated / revise` | `read_memory → finalize_patch` | 15 / 15 | 2 | 5,001 | 0.001286382 |
| **Batch** | 两组都完成 | — | 两组全通过 | **3** | **7,988** | **0.002670552** |

`cost_complete=true`。W1 是预先给定 target memory 和检索短语的 `oracle_assisted_fixed_workflow`；A1 使用真实 Agent V1 loop。A1 本次省掉了历史检索工具，但多用了 1 次模型调用；费用更低来自本次实际 token / cache 结构，不能归因为 Agent 必然更便宜。

**证据边界：**这是单个 focused synthetic revise 的配对成功，不是 20 日数据集的完整真实 W0 / W1 / A1 评测；样本数也不支持稳定成功率、泛化质量、稳态成本或“Agent 普遍优于 Workflow”的结论。

### 0.1 稳定命名与工具轨迹验收边界

- `new` 只在唯一、合格的完整证据句跨至少两个不同日期文件逐字重复时派生稳定命名；`statement` 逐字复制该句，`scope` 只能来自版本化的 canonical trigger 映射。候选多于一个、命中不唯一、缺少 scope 或命中不安全文本时必须 `finish`，不允许同义改写补齐。
- 这个合同只稳定特定 `new` 的命名；它不证明两个近义句语义等价，也不能确定性阻止近义新 key。
- W0 / W1 仍按冻结 action、target、query / date / limit、`result_kind` 和 `result_count` 精确验收。A1 的自主 `search_history` 不要求复制 oracle 的 query 或日期参数；只有在读取正确 target、检索结果非空且不超过 5 条、response / run 的完整 `source_hashes` 与该 case 授权 source 集精确相等，且最终 evidence / counterevidence 仍逐字命中时，才算语义工具验收通过。

### 0.2 三次 6-case live preflight

三次都在临时合成 Vault 中执行，都未全绿：

| 批次 | 已观察结果 | 不能如何解读 |
| --- | --- | --- |
| v3 | 到 case 3 时，A1 因 exact tool oracle 产生假阴性 | 不能算作 6-case 通过，也不能把 oracle 假阴性当作 Agent 质量失败 |
| v4 | case 4 三臂均返回 `no_change`，冻结 oracle 却是未定义的 `insufficient` | 该批次无法提供有效的 case 4 质量判定 |
| v5 | 前 4 个 case 全部通过；case 5 的 W0 patch 被 evidence / security 门拦截，W1 / A1 未运行 | 不能声称 case 5 / 6 A1 已验证，也不能声称 6-case 全绿 |

三次执行前后，真实 Vault 的 hash 均零变化。这只证明这三次执行没有改写真实 Vault，不代表 preflight 质量门通过。

### 0.3 历史 API 账本截点

计入旧四次 manual gate 与 thinking probe 后，该历史截点为 **101 次调用 / 277,551 Token / $0.039353928**。另有 **1 次已尝试调用**的 usage 与成本未知；该截点不包含当前四案批次，不能写成项目当前累计总账。当前四案批次单独记录为 7 calls / 16,973 Token / $0.001238851。

20 日 live E2 **未执行**；本次 MVP 不用 focused、四案或 Chrome 小样本替代。当前仍没有 20 日真模型纵向稳定性、完整 W0 / W1 / A1 对照或稳态成本结论。

### 0.4 真实打包、安装与 Chrome 边界

commit `e116d4b8a3ff78f608f26d4a2f76186dca37b00e` 的 Prompt v1.19 / Workflow v1.9 运行时已打包并安全安装。安装验收包的 SHA-256 为 `227a82fd86ec05beae70cfa990b595433115445a8448b457c02fdbc74c84b29d`，checksum 已验证；69 / 69 个受管 Agent / Chrome 文件与提交逐字一致，36 / 36 个原始内容文件与安装前备份逐字一致。文档收口后的最终包以 `dist/Memento-macOS.zip.sha256` 为准。

Re:member Agent plist 只有 requests / user-actions `WatchPaths`，不含 `RunAtLoad`、`KeepAlive` 或 timer。验收 gate 已关闭，当前与 fresh install 一样为 disabled。不能仅凭 plist 字段声称进程在登录或 bootstrap 绝不会被唤醒。

安装环境已完成一个真实 DeepSeek `no_change` request（1 call / 4,075 Token / $0.001406123），Chrome 完成两次 `r0 → r1` edit；fresh `base_revision=1` delete 已生成 `r2` tombstone。Worker 重跑后 tombstone 字节不变且确定性投影未复活，用户刷新 Chrome 后也确认该理解未复活。本次 user-action reconcile 没有新增 DeepSeek 调用。

### 0.5 四次手动启用 gate 真实失败

四次都只运行 `history_search_revise` 第一案；该案失败后 runner 按合同停止，因此 `revision_conflict` 第二案均未运行。
v1.9 历史 live 使用冻结 policy SHA-256 `5a24b5e01b32815d5aa881dccd300ed2be1a61abc38469c95061a02831dc2575`，原始公开结果为 `error_code=agent_error`。预算错误投影与 post-call Token 预算合同修正后，当前离线候选 plan-only SHA-256 为 `86d97e0967bd46eedf90f5cd98e2aae6c990338bd6ef2bd356d83a322c656346`，policy SHA-256 为 `ba388d00e2e9c9399c89f9bce87b6a8839aa7052da6ab1e0485dbbcd26176e02`，公开合同为 `error_code=budget`；新计划双跑一致、0 calls，尚未 live，不能改变本节历史失败结论。该候选的 production / manual 预算为 5 回合 / 20,000 Token，通用 Agent / preflight 仍为 3 回合 / 12,000 Token；20,000 是基于已执行样本选择的工程阈值 [猜测]，不是总 Token 绝不越过该值的保证。超额 completion 会被解析并公开审计，但 action 不执行、不写 memory、不再调用 Provider。

| Prompt / policy | Plan SHA-256 | 结果 / 轨迹 | 调用 | Token | 成本 USD | 已通过检查 | 能力结论 |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| v1.6 | `06b518611a263a3514e1a4451802b5dfc5c605ec13112973d8c0b47c32626265` | `finish / no_change` | 1 | 2,062 | 0.00036482 | 安全审计、usage 完整、source clone 不变 | 第一案失败；第二案未运行 |
| v1.7 | `c4f41a34b48f2781a656c6366912d0785b5f0f1af6e71c004fb4e197f42bebf3` | `finish / no_change` | 1 | 2,307 | 0.001023555 | 安全审计、usage 完整、source clone 不变 | 第一案失败；第二案未运行 |
| v1.8 | `8c2aecab9caeec3d9f38434c6f7d20b2b1c592b238c00844c4594f4749705323` | `no_change`；`finish → read_memory → finish`；质量 4 / 14 | 3 | 8,077 | 0.001467922 | 安全审计、usage 完整、source clone 不变 | 第一案失败；第二案未运行 |
| v1.9 | `aced8fc17de4e7c15de3c33c578ad02b6e2e6fdf4e95e6dc1862f803b2a37110` | `status=budget_exhausted`；`error_code=agent_error`；`finish → read_memory → finish`；`scaffolded`，2 次复核；质量 3 / 14 | 4 | 12,096 | 0.001351458 | usage 完整、source clone 不变 | 第一案失败；第二案未运行 |

第一案的冻结目标是 `updated / revise` 与 `read_memory → search_history → finalize_patch`。v1.9 的已解析 action 轨迹为 `finish → read_memory → finish`：两次 `finish` 分别触发 pre-read 与 post-read 复核，因此分类为 `scaffolded`。第四次 Provider 调用的返回使累计 Token 达到 12,096，控制器在解析 action 前按 12,000 Token 上限停止；该次 action 未知，不能猜成 `search_history`、`finalize_patch` 或 `finish`。第一案最终为 `status=budget_exhausted`、`error_code=agent_error`，质量 3 / 14。usage 完整和来源副本不变不能证明 Agent 的调查能力通过；该历史 gate 当时继续为红灯，第二案未运行、未重试、未安装，后续结果不反向追认。

### 0.6 Thinking probe 真实配对仍未通过

冻结 plan SHA-256：`923ebb14d8e1dd2fc314153946f89c98a6f06a25629aa4826d7d2696836fde88`

| Arm | 轨迹 | bounded finish | 质量 | 调用 | Token | reasoning tokens | 成本 USD |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| `disabled` | `finish → read_memory → finish` | `true` | 8 / 19 | 3 | 8,077 | 0 | 0.00025317 |
| `thinking_high` | `read_memory → finish` | `false` | 9 / 19 | 2 | 6,925 | 1,405 | 0.002615336 |
| **Paired** | 两臂完整执行；`neither_pass` | — | 两臂均未通过 | **5** | **15,002** | **1,405** | **0.002868506** |

两臂的安全审计、usage 完整和 source clone 不变检查均通过，但都没有完成目标调查与修订，因此不能把分数差异写成 thinking 带来能力改善。该历史分支当时保持 disabled、未重试、未安装，后续结果不反向追认。

## 1. 证据范围与分类方法

历史首轮证据来自对四个本地隔离目录的只读审计；新增 v1.6 focused 配对证据来自冻结 runner 的 stdout 公共报告，plan SHA-256 为 `56ec87ff4e735fd5aaeef2d2b8e8da0e9d8edcdaadbe28b381aba783834a1d82`：

- `memento-agent-v1-live-16yFC7`：旧 Prompt 的预算失败，外加一次直接 Provider 诊断；
- `memento-agent-v1-live2-b1UAlW`：v1.1 单步 `updated` 成功；
- `memento-agent-v1-search-WzDwa0`：保守 `finish / no_change`；
- `memento-agent-v1-revise-DLkO6X`：一次 mock 初始记忆建立，以及三次真实多步修订尝试。

以下计数规则用于历史首轮目录；focused 配对按第 0 节的 batch 公共报告单列：

1. `response / run` 中 `provider=deepseek`、`model=deepseek-v4-pro` 的汇总 usage 计为“真实 Agent 运行”；
2. 用量日志中不属于任何 Agent run 时间段的一条 Provider usage 单列为“诊断直调”；
3. `provider=mock / model=fixture` 的 setup 虽在响应中记为一次 mock planner 回合，但没有真实 API 调用、Token 或费用；
4. 同一调用不在 response 汇总与 NDJSON 中重复计数。真实 Agent 合计来自 response，全部付费调用合计来自 NDJSON。

报告不包含 request ID、API Key、Prompt 正文或日记正文。

## 2. 历史首轮真实 Token 与成本总表

下表只统计 focused v1.6 配对之前的首轮真实探针；新配对的 3 次调用、7,988 Token 和 $0.002670552 在第 0 节单列，不并入本历史表。它也不包含后续 preflight；当前累计账本以第 0.3 节为准。

| 类别 | 运行数 | 付费模型调用 | Prompt Token | 输出 Token | 总 Token | Cache hit | Cache miss | 成本 USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 真实 Agent | 6 | 13 | 42,004 | 1,422 | 43,426 | 18,304 | 23,700 | 0.011612992 |
| 诊断直调 | 不计为 Agent run | 1 | 8,437 | 84 | 8,521 | 8,320 | 117 | 0.000154135 |
| Mock setup | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **全部付费 API 合计** | — | **14** | **50,441** | **1,506** | **51,947** | **26,624** | **23,817** | **0.011767127** |

六次真实 Agent run 的终态：

- `updated`：1 次；
- `no_change`：1 次；
- `budget_exhausted`：3 次；
- `stale`（CAS 拦截）：1 次。

直接产生 `updated` 的那次 run 成本为 **$0.004042455**。全部真实 Agent 试验总成本除以一次付费 `updated` 结果为 **$0.011612992 / updated outcome**。这两个数只描述本次小样本，不是稳态产品成本。

成本按 2026-08-12 的 DeepSeek V4 Pro 价格快照重算：cache-hit 输入 **$0.003625 / 1M Token**、cache-miss 输入 **$0.435 / 1M Token**、输出 **$0.87 / 1M Token**。价格来源为 [DeepSeek 官方 Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)。所有 usage 均完整，`usage_missing=false`，reasoning Token 均为 0。

## 3. 逐类运行结果

### 3.1 旧 Prompt：第二回合超出累计 Token 预算

| 终态 | 真实调用 | Prompt | 输出 | 总 Token | Cache hit | Cache miss | 成本 USD | Memory 写入 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `budget_exhausted` | 2 | 16,931 | 134 | 17,065 | 8,320 | 8,611 | 0.003892525 | 0 |

第一回合产出了一个未通过 Schema 的 action；第二回合返回后，累计 17,065 Token 超过该 run 的 12,000 Token 上限，控制器停止且没有写 memory。

此外还有一次不属于 Agent run 的诊断直调：8,521 Token，$0.000154135。它不得合并到 Agent 运行成功率中。

### 3.2 v1.1 单步更新：成功

| 终态 | 动作路径 | 真实调用 | 总 Token | 成本 USD | Memory 写入 |
| --- | --- | ---: | ---: | ---: | ---: |
| `updated` | `finalize_patch` | 1 | 8,896 | 0.004042455 | 1 revision |

这证明当模型在第一回合提交结构、证据和操作均合法的 patch 时，当前 Provider 、本地 Validator、不可变 revision 和 profile 重建链路可以完成一次真实更新。它不能证明多轮规划已可靠。

### 3.3 保守停止：成功

| 终态 | 动作路径 | 真实调用 | 总 Token | 成本 USD | Memory 写入 |
| --- | --- | ---: | ---: | ---: | ---: |
| `no_change` | `finish` | 1 | 1,230 | 0.000550710 | 0 |

本例只包含 1 个实际记录日。模型直接停止，未搜索历史、未产生新记忆。这是一个保守路由的成功样本，不能据此计算 `no_change` 精确率。

### 3.4 修复前多步修订：三次真实运行均未更新

隔离目录中的基础 memory 由 mock setup 建立，该 setup 不是 DeepSeek 成功样本。

| 尝试 | 终态 | 安全轨迹 | 真实调用 | 总 Token | 成本 USD | Memory 写入 |
| ---: | --- | --- | ---: | ---: | ---: | ---: |
| 1 | `budget_exhausted` | `finalize_patch` 证据被拒 → `search_history` 0 命中 → `read_memory` 后用尽回合 | 3 | 4,577 | 0.000720650 | 0 |
| 2 | `stale` | `read_memory` → `search_history` 1 命中 → `finalize_patch` 被 revision CAS 拦截 | 3 | 5,522 | 0.001226091 | 0 |
| 3 | `budget_exhausted` | `read_memory` → `finalize_patch` 证据被拒 → 重复 `read_memory` 被 loop guard 拦截 | 3 | 6,136 | 0.001180561 | 0 |
| **合计** | 无成功修订 | — | **9** | **16,235** | **0.003127302** | **0** |

这三条轨迹证明模型确实产生了不同的多步动作路径，也证明本地 evidence / CAS / loop 边界在这些样本中阻止了非法写入。它们在修复前没有产生一次可接受的 `revise`；v1.6 focused pairing 后可以说“已有一个真实合成 revise 成功样本”，仍不得写成“多轮 Agent 已稳定可用”。

运行文件没有持久化独立 Prompt 版本字段，因此无法仅从这三份 run 精确区分哪一次属于 v1.2、哪一次属于 v1.3。只能确认它们是本轮 v1.2 / v1.3 多步试验集合。

## 4. 已验证与未验证边界

### 已在真实 V4 Pro 小样本中观察到

- 单回合直接提交合法 patch 可产生不可变 memory revision；
- 材料不足时可直接 `finish / no_change`；
- 模型在不同多步尝试中选择过 `read_memory`、`search_history`、`finalize_patch` 和 `finish` 中的多种路径；
- 无效 Schema、证据不足、revision 不匹配、循环和预算上限都没有越过本地写入边界；
- 全部付费调用都保存了完整 Token 与价格快照。
- v1.6 focused `priority_revision` 中，W1 和 A1 都完成了合法 `revise`，15 项质量检查全通过；
- 同一 focused 任务上，W1 走 `read → search → finalize`，A1 走 `read → finalize`，路径不同。

### 仍未验证

- 多步 `revise` 在更多任务、重复次数与真实用户记录上的成功率与修订质量；
- `tension` 的真实模型路由与产物质量；
- scope、提示注入和并发修改的完整真实矩阵；Chrome edit / delete 与 tombstone 已有安装环境样本，但不能代替该矩阵；
- 逐日 20 天 W0 / W1 / A1 配对真实评测；
- material-change gate 在真实 Provider 配对中的 0-call 账单核对（离线 window aging / unchanged 回归已通过）；
- 真实用户记录的影子模式与人工接受率；
- Flash 是否能在不降低证据和路由质量的情况下替代 Pro。

## 5. 真实发布门

| 发布条件 | 当前证据 | 状态 |
| --- | --- | --- |
| 至少一次真实 `new / updated` | v1.1 单步产生 1 个 revision | 小样本通过 |
| 保守 `no_change` | 1 个记录日样本正常停止 | 小样本通过 |
| focused 多步 `revise` | 修复前 3 次失败；v1.6 冻结 `priority_revision` 中 W1 / A1 各 1 次 `updated / revise`，15 / 15 检查通过 | **focused 样本通过** |
| 动态路由 | focused A1 路径与 W1 不同且完成任务；只有 1 个任务、1 对样本 | **小样本通过；稳定性未证明** |
| 本地安全边界 | 本轮非法 patch、CAS 与 loop 均未写 memory | 小样本通过，全矩阵未完成 |
| 6-case live preflight | v3、v4、v5 都未全绿；v5 只确认前 4 case 通过，case 5 在 W0 停批 | **未通过** |
| 两案手动启用 gate | v1.6、v1.7 都在第一案返回 `finish / no_change`；v1.8 第一案为 `no_change`、质量 4 / 14；v1.9 第一案为 `scaffolded`、`status=budget_exhausted`、`error_code=agent_error`、质量 3 / 14，第四次 action 未知。四次第二案均未运行；v1.9 usage / source clone 检查通过 | **能力未通过** |
| 单案 thinking probe | plan `923ebb14…fde88` 两臂完整执行；`disabled` 为 8 / 19，`thinking_high` 为 9 / 19；paired=`neither_pass`，安全 / usage / source 检查通过 | **两臂能力均未通过** |
| 20 日 W0 / W1 / A1 真实配对 | 未执行；本次 disabled RC 已决定跳过 | **无纵向结果** |
| tombstone / edit / scope / injection 真实矩阵 | Chrome edit / delete 已落盘，fresh base1 delete 生成 r2 tombstone 且 Worker 重跑幂等；scope / injection 与完整矩阵未运行 | **部分通过** |
| 本地无变化 0 调用 gate | public material-change gate 已实现；window aging / unchanged 离线回归为 0 Provider call | **离线通过；真实 Provider 配对未运行** |
| 成本可审计 | 已知累计 101 次 / 277,551 Token / $0.039353928；另有 1 次 attempt 的 usage / cost 未知 | **已知部分可审计；整体不完整** |
| 真实记录影子模式 | 未运行 | **未通过** |
| 安装到真实 Vault | commit `e116d4b8a3ff78f608f26d4a2f76186dca37b00e` 已打包并安装；安装验收包 SHA `227a82…29d` 校验通过，69 / 69 受管文件匹配提交，36 / 36 原始内容匹配备份 | **通过** |
| 安装后运行观察 | 真实 `no_change` request 为 1 call / 4,075 Token / $0.001406123；两次 r0→r1 edit 与 fresh base1 delete 已生成 r2 tombstone，Worker 重跑幂等 | **运行与写入链路通过** |
| Chrome 刷新视觉确认 | 删除后本地投影未复活；用户刷新后的页面也只显示剩余 2 条理解 | **通过** |

**Agentic Workflow MVP 的四案真实合成 gate、打包安装、真实 request、Chrome edit / delete / reload 与 disabled 交付闭环已完成。** 不能说“旧 manual / thinking 失败已被追认通过”、“Agent 普遍优于 Workflow”、“6-case 全绿”、“20 日纵向稳定”或“production-ready”。

本报告的 focused 成功对应 Prompt / policy v1.6；v1.9 手动 gate 第一案已真实失败。v1.4 / 108、v1.5 / 114、Python 136 / 136 与 v1.6 focused 成功均只是各自版本的历史快照，不作为当前 v1.9 的整体通过证据。

## 6. 后续非 MVP 验证集

1. 对每个任务记录目标 memory 命中、证据有效、终态、回合数和成本，按预注册阈值统计而不是只看单例；
2. 扩展到 `tension`，并保留 W1 强基线；
3. 在隔离 Vault 补齐 tombstone、user edit / scope、CAS、source stale 和提示注入矩阵；
4. 后续若要得出纵向结论，必须单独运行 20 日 live E2，不得用 focused 或 preflight 替代。

[猜测] 一个 focused 成功样本不足以判断三回合上限是否适合更多 revise / tension 任务；需要更多预注册配对数据才能调整预算合同。
