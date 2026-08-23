# 07 · 记录场景集与标注规范

## 1. 场景集的用途

首轮不训练或微调模型。场景集用于：

- 固定 Capture Understanding Agent 的分流标准
- 评测 Record Interpreter 是否过度理解
- 评测资料、用户判断和长期证据是否被正确区分
- 比较不同 Prompt、Policy 和模型的成本与质量
- 在前后端合码前复现真实用户输入

它是一套可版本化的产品测试数据，不是一次性收集的示例库

## 2. 推荐规模

首版建议 36 个场景：

| 类别 | 数量 | 要验证什么 |
|---|---:|---|
| 明确判断、提问、决定 | 10 | 进入 `interpret` 的质量 |
| 链接、文章、截图、PDF | 8 | 资料保存与按需阅读 |
| 带高亮或备注的资料 | 5 | 用户信号与资料正文的边界 |
| 待会再看、稍后处理、收藏 | 4 | Read Later 不形成认知结论 |
| 语音、会议和对话片段 | 4 | 原话、转写和判断的区分 |
| 外部 AI 会话与写回 | 3 | 双向 Context 链路 |
| 模糊、敏感、注入和失败输入 | 2 | 安全停止与降级 |

先由你提供 12 到 15 个真实但可脱敏的典型样本，我补齐同结构的边界样本。每轮产品试用再将误判样本加入下一版场景集

## 3. 每个场景的标注结构

```yaml
id: capture_link_read_later_001
input:
  source_type: url
  source_app: browser
  raw_content: https://example.com/article
  user_note: 待会再看
  attachment: null
expected_route: ask_on_use
expected_objects:
  - SourceRecordRevision
  - ResourceCardRevision
  - ReadLaterIntentRevision
forbidden_objects:
  - RecordInterpretationRevision
  - MemoryAtomRevision
  - ThemeRevision
why: 用户只表达了保存与稍后阅读意图
follow_up:
  event: 用户打开并询问“这篇文章和当前项目有什么关系”
  expected_route: resource_reader
```

必须字段：

- 原始输入与附件类型
- 用户在当时显式表达的内容
- 预期处理路由
- 允许创建的对象
- 禁止创建的对象
- 可接受的模型输出
- 不可接受的过度推断
- 后续事件及预期变化

## 4. 首批必备场景

### S01 · 链接 + 待会再看

```text
输入：一个 URL + “待会再看”
路由：ask_on_use
结果：SourceRecord、ResourceCard、ReadLaterIntent
禁区：主题、个人理解、文章总结
```

### S02 · 长网页截图，无备注

```text
输入：网页整页截图，OCR 文字很多
路由：resource_index
结果：原截图、完整 OCR、本地检索索引、ResourceCard
禁区：自动全文总结、自动进入可用记忆
```

### S03 · 网页高亮 + 一句自己的判断

```text
输入：网页截图，高亮一句话，备注“这个和当前的 Context 问题有关”
路由：resource_index + interpret
结果：资料卡完整保留；高亮与备注进入 Interpretation candidate
禁区：将整篇网页观点写成用户长期理解
```

### S04 · AI 对话中的判断

```text
输入：与 AI 对话后写下“先把变化发生的理由留下”
路由：interpret
结果：记录解释、候选 Memory Atom、候选主题连接
禁区：单次记录直接形成 Theme
```

### S05 · 外部 AI 使用后写回

```text
输入：外部 AI 在授权范围内读取 Context 后，用户确认“这次决定采用先确认边界的方案”
路由：ExternalTraceRevision → L0 → interpret
结果：可追溯会话痕迹与新的 SourceRecord
禁区：外部 AI 直接修改 Self Insight
```

### S06 · 语音片段

```text
输入：18 秒语音“刚才那个变化应该留下来，回去以后再整理”
路由：interpret
结果：音频原件、转写、用户表达的判断候选
禁区：将未说出的背景补成事实
```

## 5. 路由判定表

| 用户输入 | 首要产物 | Agent 行为 |
|---|---|---|
| URL、网页、PDF、截图 | Resource Card | 先索引和保存 |
| “待会再看”“收藏一下” | Read Later intent | 延后理解 |
| 资料中的高亮 + 用户备注 | 资料卡 + interpretation | 只解释高亮和备注 |
| 自己的判断、问题、决定 | interpretation request | L1 逐条理解 |
| 纯转发消息、无主张 | archive_only | 保存即可 |
| 长文阅读请求 | Resource Reader request | 用户提出问题后按需读取 |
| 外部 AI 会话结果 | ExternalTraceRevision | 回到常规记录链路 |

## 6. 评测规则

Capture Understanding Agent 的首要指标是避免把资料误写成用户认知

关键指标：

- `route_accuracy`
- `false_interpret_rate`
- `resource_preservation_rate`
- `user_signal_precision`
- `read_later_false_memory_rate`
- `long_document_over_summary_rate`
- `follow_up_usefulness`

每条不符合预期的真实记录都进入 `regressions/`，并在下一次 Prompt 或路由改动前回放

## 7. 建议的目录

```text
backend/eval/scenarios/
├── manifest.yaml
├── capture/
│   ├── link-read-later-001.yaml
│   ├── long-webpage-001.yaml
│   └── screenshot-highlight-001.yaml
├── interpretation/
├── theme/
├── self-insight/
├── external-context/
└── regressions/
```

原始附件用脱敏版本保存在对应场景目录，hash 写入 manifest。真实用户 Vault 不进入 Git，也不作为默认自动化测试输入

## 8. R5 当前落地状态

首批八类场景已经以 `backend/eval/scenarios/manifest.json` 落地，并由 `backend/tests/eval/test_r5_scenario_manifest.py` 在 0 产品模型条件下逐条回放。每条场景同时断言预期路由、允许产生的入口对象和禁止产生的认知对象

这组 fixture 用于守住路由和权限边界。12—15 条匿名化真实样本仍用于后续 Prompt、Provider 与阈值质量校准，不会自动进入 Git 或正式 Vault
