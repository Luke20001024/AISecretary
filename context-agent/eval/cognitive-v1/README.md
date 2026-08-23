# Cognitive Secretary V1 隔离验收

这个 harness 用于在全新临时 Vault 中验证认知秘书 MVP 的正式链路。它不接受 `--vault`，不会读取 `~/AISecretary` 的原文。

v4 计划同时绑定以下合同：

- Agent prompt `remember-agent-v1.22`
- Agentic Workflow `agentic-workflow-investigation-v1.13`
- stable-new identity `stable-new-identity-v1.1`
- stable-new terminal gate `stable-new-terminal-gate-v1.0`
- Agent 历史窗口、Cognitive authorization、Day Orchestrator 与 Record Store 中的 `as_of` 相关源码哈希

上述版本、指令哈希或 `as_of` 相关源码发生变化后，计划 SHA 会跟着变化；Agent 版本与 v4 预期不一致时，harness 直接拒绝执行。

默认只输出计划，Provider 调用为 0：

```bash
python3 context-agent/eval/cognitive-v1/run_live_acceptance.py
```

本地假 Provider 验证用同一条生产存储、Worker、日级 Orchestrator、Daily Review、Agent Adapter 和 Projection 链路：

```bash
PLAN_SHA="$(python3 context-agent/eval/cognitive-v1/run_live_acceptance.py | python3 -c 'import json,sys; print(json.load(sys.stdin)["plan_sha256"])')"
python3 context-agent/eval/cognitive-v1/run_live_acceptance.py \
  --run-fake \
  --expect-plan-sha256 "$PLAN_SHA"
```

真实 DeepSeek 只在根任务审阅当前计划后显式运行。命令不把 Key 放入 argv；此 harness 在真实模式下拒绝 `DEEPSEEK_API_KEY` 环境变量，Provider 只会读取当前用户 Keychain：

```bash
PLAN_SHA="$(python3 context-agent/eval/cognitive-v1/run_live_acceptance.py | python3 -c 'import json,sys; print(json.load(sys.stdin)["plan_sha256"])')"
umask 077
env -u DEEPSEEK_API_KEY python3 context-agent/eval/cognitive-v1/run_live_acceptance.py \
  --execute-live \
  --confirm execute-cognitive-v1-live-on-isolated-synthetic-vault \
  --expect-plan-sha256 "$PLAN_SHA" \
  > cognitive-v1-live-report.json
```

真实模式只包含两个合成 case，每个 case 最多尝试一次；第一个失败后立即停止。Harness 不调 prompt、不重跑 case。Agent V1 内部的正式有界工具回合仍属于一次 case 执行。

## 2026-08-18 v4 有限报告

Prompt `remember-agent-v1.22`、Workflow `agentic-workflow-investigation-v1.13`、stable-new identity `stable-new-identity-v1.1` 与 terminal gate `stable-new-terminal-gate-v1.0` 的冻结计划完成一次真实 DeepSeek 运行，顶层结果为 `all_passed`：

| Case | 调用 | Token | 估算成本 |
|---|---:|---:|---:|
| `two_day_positive_with_negative` | 11 | 26,602 | $0.007166364 |
| `original_only_retraction` | 6 | 11,851 | $0.002545011 |
| 合计 | 17 | 38,453 | $0.009711375 |

本次 `invalid_action=0`；公开报告确认临时目录已清理，两个合成 case 的来源 hash 前后不变。成本来自报告采用的费率口径，不等同于 Provider 最终账单。

这份报告只覆盖全新临时 Vault 中的两个冻结合成 case。它没有读取真实用户 Vault，也不覆盖发行包安装、Chrome 人工交互、21:00/08:00 实际日历触发、20 日纵向稳定或真实用户理解质量。历史 v3 失败仍保留为回归背景，不能被本次成功从账本中删除。

公开 JSON 的 case 明细只包含标识、终态、调用数、Token、成本、哈希前缀、有限错误类型，以及有限枚举的失败阶段和未通过检查名；顶层另有模式、计划哈希、合计 usage 与临时目录清理状态。它不输出原文、模型文本、Provider 原始请求/响应、Key、完整路径或原始 ref。

Agent 诊断增加了 `stable_identity_status`、`eligible_evidence_ref_count`、`candidate_date_count`、`structure_ready` 和 `missing_requirement_codes`。这些字段只从生产 Agent 已经构建的结构化 evidence bundle，或已提交的正式 memory 中降维得到。当这两种材料都没有形成时，`stable_identity_status` 为 `unavailable`，其余四个材料字段为 `null`；harness 不会为了诊断重跑 Agent、重建 evidence bundle 或复制生产 Workflow 的缺口判断。
