# Context Agent MVP 评测结果（2026-08-10）

> 结论：工程合同通过；DeepSeek V4 Pro 与 V4 Flash 都完成了真实 API 集成。最终代码复验中 Flash 与 Pro 同为 7/7，按本轮 Token 与 2026-08-09 价格快照计算的成本低 74.32%。真实用户价值仍为 `not_run`。

## 1. 运行身份与边界

- 日期：2026-08-10（Asia/Shanghai）
- 分支：`codex/context-agent-mvp`
- 基线 commit：`4b90e20cd163`
- 运行状态：未提交工作区；本报告对应当前工作区内容，不声称对应一个已提交 commit
- Provider：DeepSeek
- 模型：`deepseek-v4-pro`、`deepseek-v4-flash`
- 推理配置：`thinking=disabled`、`temperature=0`
- 数据：仅仓库内合成记录，没有发送用户真实 Vault 内容
- Key：配对评测时由临时 CLI 环境提供；后续由用户明确写入 macOS 系统钥匙串，并通过 1 次不设环境变量的 Flash 合成样例验证回退读取；候选、报告、Dashboard 与 usage 日志均不保存 Key 或请求正文
- 自动重试：0；P1.0 不实现 Provider 自动重试
- 最终 9-case 数据集摘要：`59e8ea572fff5c03e7c649a6de2bb87a97349df72ad42c6c451562d2245`

价格使用 2026-08-09 的 [DeepSeek 官方价格快照](https://api-docs.deepseek.com/quick_start/pricing/)；Token 字段按 [Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion/) 的 usage 合同读取。本文成本是依据 API 返回 Token 和该价格快照计算的结果，不等同于 Provider 最终账单承诺。

## 2. 分层状态

| 层级 | 状态 | 事实证据 |
|---|---|---|
| E0 静态与安全 | pass | Key 扫描、浏览器凭据边界、Python/Node/Shell 语法和 `git diff --check` 通过 |
| E1 离线工程合同 | pass | Python 20/20；离线 runner 9/9；17 个 Node 与 8 个 Shell 测试入口逐项通过 |
| E2 DeepSeek V4 Pro | pass | 最终代码真实 API 复验 7/7；7/7 合同、证据、状态和类别命中 |
| E2 DeepSeek V4 Flash | pass | 最终代码真实 API 复验 7/7；额外钥匙串回退 smoke test 1/1 |
| E3 真实用户价值 | not_run | 未用真实未来任务验证候选价值、复用价值或信任变化 |
| Chrome 手工端到端 | not_run | 数据层与互操作测试通过；本机已安装扩展没有被本任务擅自覆盖或重载 |

`tests/test_record_dashboard.sh` 首次按默认路径运行时，正确发现本机已安装扩展仍是旧版；随后使用隔离安装目录通过。这是“用户安装尚未同步”的事实，不是工作区代码通过了真实 Chrome 手测。

## 3. 第一轮真实 API：暴露分类合同缺口

第一轮使用原始 8-case 集中的 6 个 live case。两模型均完成全部调用，没有 Provider、非法 JSON、证据、合同或 usage 缺失错误，但都把“必须离线、不能依赖网络”的硬边界分为 `project_decision`，而评测合同要求 `constraint`。

| 模型 | 通过 | 合同有效 | 证据有效 | 状态命中 | 失败原因 | 成本 USD |
|---|---:|---:|---:|---:|---|---:|
| V4 Pro | 5/6 | 6/6 | 6/6 | 6/6 | 约束被分为项目决策 | 0.0010246570 |
| V4 Flash | 5/6 | 6/6 | 6/6 | 6/6 | 同一分类错误 | 0.0003026072 |

这一结果没有支持“Pro 比 Flash 更可靠”；它支持的是分类定义不够可执行。随后 Prompt 增加了硬约束与方案选择的判定规则，并新增一个不同措辞的包装约束回归 case。

## 4. 最终代码真实 API 复验

分类规则修正后的中间回归中，两模型已各自达到 7/7；随后集成审查又修复了敏感 false-negative、异常 usage 计费、Dashboard 恢复与 Pack 隐私等问题。最终代码因此再次对两模型运行同一组 7 个 live case。第 7 个样例是在第一轮后新增，所以这仍是修正后回归，不是预注册、独立、盲测 benchmark。

| 指标 | V4 Pro | V4 Flash |
|---|---:|---:|
| 调用完成 | 7/7 | 7/7 |
| case 通过 | 7/7 | 7/7 |
| 合同有效 | 7/7 | 7/7 |
| 证据有效 | 7/7 | 7/7 |
| 预期状态命中 | 7/7 | 7/7 |
| Provider / JSON / 合同错误 | 0 / 0 / 0 | 0 / 0 / 0 |
| usage 缺失 | 0 | 0 |
| 成本是否完整 | true | true |
| Prompt Token | 3,368 | 3,368 |
| Cache-hit Prompt Token | 2,944 | 2,944 |
| Cache-miss Prompt Token | 424 | 424 |
| Completion Token | 745 | 532 |
| Total Token | 4,113 | 3,900 |
| 计算成本 USD | 0.0008432620 | 0.0002165632 |
| 每次调用计算成本 USD | 0.0001204660 | 0.0000309376 |

相对 Pro，Flash 最终复验计算成本降低 `74.3184%`。三轮合计 40 次真实调用全部完成，六个模型轮次的合计计算成本为 `$0.0040265554`。其中中间回归同为 7/7，Pro/Flash 计算成本分别为 `$0.0012832500` / `$0.0003562160`；缓存构成不同会让不同轮次总成本变化，因此模型比较必须使用同一轮配对数据。

钥匙串回退验证明确移除 `DEEPSEEK_API_KEY` 环境变量，仅运行 `02_no_candidate.json` 合成样例。V4 Flash 完成 1/1，usage 完整，计算成本为 `$0.0000161952`。该 smoke test 用于验证本机凭据读取链路，不纳入上述 Pro/Flash 配对成本比较。

## 5. 工程结论

本轮已经证明：

1. 本地 CLI 能使用 DeepSeek 真实 API，在不把 Key 放入仓库、浏览器和正文日志的前提下取得结构化结果与 usage。
2. 来源 hash、逐行引文、有限敏感词法后备、用户确认门和五种决定由确定性代码控制，不依赖模型自觉；该词法后备只覆盖 `statement/scope/why_now`，不是完整敏感分类器。
3. 在这组小型合成回归上，Flash 达到了与 Pro 相同的硬合同结果，并显著降低本轮计算成本。
4. 第一轮共同失败说明 Prompt/产品分类合同会直接影响模型结果；模型档位不是唯一变量。

本轮没有证明：

1. Flash 或 Pro 已达到真实用户对“值得记住”的判断质量。
2. 7/7 能代表长期稳定性、复杂日记、长上下文或敏感信息召回能力。
3. Context Pack 已减少未来任务的重复说明；E3 尚未执行。
4. 浏览器 UI 已在用户当前安装中完成手工端到端验证。

因此，默认模型仍保持 Pro。Flash 已具备进入真实小样本 challenger 的工程依据，但是否切换默认模型要等待用户授权记录上的盲评与未来任务复用证据。

## 6. 下一轮门槛

- 让用户明确选择一批可发送记录，Pro/Flash 隐藏模型名后配对生成；样本规模在取得授权并看到首轮差异后再确定；
- 人工逐条标注“应不应该出现、事实是否准确、是否只是改写、范围是否合适、是否愿意确认”；
- 至少在一个真实未来任务中使用 Context Pack，并记录是否减少重复说明；
- 若 Flash 的全部安全/证据硬门槛不下降，且人工接受率不低于 Pro，再把默认模型切到 Flash；
- 为 Chrome 扩展执行安装/重载、目录授权、五个决定和 Pack 复制的手工验收。
