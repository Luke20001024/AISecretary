# R5 · 入口判断、逐条理解与按需资料阅读

> 日期：2026-08-23
>
> 状态：离线合成合同与 Workflow 质量门通过，B2 实现范围可以关闭

## 1. 本轮交付

- L0 `CaptureUnderstandingAgent` 与保守分流 policy
- L1 `RecordInterpreter` 与逐条解释 policy
- 只在用户主动提问后运行的 `ResourceReader`
- Provider-neutral request、response、failure 与 usage 协议
- 强制接入 `RunLedger` 的三个 trusted Workflow
- 新 V1 parser revision 到 V2 SourceRecord 的窄 adapter 与 ingest Workflow
- 8 类脱敏合成输入场景及 0 产品模型回放
- `AgentRun` 与 `ResourceReadResult` JSON Schema
- Agent、Workflow、场景、引用回溯和失败降级测试

## 2. 当前处理链

```text
V1 parser fresh revision
→ V2 SourceRecord 先提交
→ L0 判断输入角色与路线
→ Workflow 校验来源、watermark、Prompt、Policy 与 usage
→ CaptureDecision + ResourceCard / ReadLaterIntent 原子提交
→ 只有 interpret 路由进入 L1
→ L1 只解释 CaptureDecision 授权的用户 SourceSpan
→ Workflow 再次核对当前 head 与原文引用
→ RecordInterpretation 提交
```

资料阅读使用独立支路：

```text
用户打开资料并提出问题
→ ResourceReader 读取受限文本
→ 返回回答、未知项与精确引用
→ Workflow 重新核对每条引用
→ 结果返回调用方，正式认知对象保持不变
```

## 3. 输入取舍已经固化

| 输入 | L0 路由 | 本轮正式结果 |
|---|---|---|
| 自己写下的判断、问题或决定 | `interpret` | CaptureDecision，随后可形成 RecordInterpretation |
| URL + “待会再看” | `ask_on_use` | CaptureDecision、ResourceCard、ReadLaterIntent |
| 长网页或整页截图，无备注 | `resource_index` | CaptureDecision、ResourceCard |
| 网页高亮 + 自己的备注 | `resource_index_and_interpret` | 资料完整保存，只把用户备注授权给 L1 |
| 纯资料、文件或截图 | `resource_index` | 资料索引，不形成个人理解 |
| 语音中的明确用户表达 | `interpret` | 精确转写 span 可进入 L1 |
| 外部 AI 的可追溯回流 | `interpret` | 先回到 SourceRecord 和 CaptureDecision |
| 模糊或低置信度输入 | `needs_confirmation` | 保存与询问，停止自动解释 |

## 4. Agent 权限与运行审计

- Agent 只返回 `AgentActionCandidate`
- L0 只能提出 CaptureDecision
- L1 只能提出 RecordInterpretation
- ResourceReader 只能提出临时 ResourceReadResult
- Candidate 绑定 Prompt、Policy、冻结输入 hash、source refs、source spans、action watermark 与 usage
- Workflow 强制写入 append-only AgentRun 终态，记录 candidate hash 和实际 committed refs
- AgentRun 不复制原文，不保存 Provider key
- Provider attempt 为 failed 或 unknown 时保持 0 正式认知写入
- 用户 action 或 source head 在运行中变化时，旧 candidate 以 conflict 结束

## 5. 证据边界

- CaptureWorkflow 要求 SourceRecord 已经是当前正式 head，原始记录因此先于 Agent 存在
- 资料正文不被当作用户表达
- 高亮资料只有明确用户备注进入 L1；作者原文仍留在 ResourceCard / SourceRecord
- L1 的 source spans 完全继承 L0 授权，Provider 不能扩大引用范围
- ResourceReader 的 citation quote 必须能够在本次授权文本中重新定位
- ResourceReadResult 不进入 RevisionStore，不创建 MemoryAtom、Theme 或 SelfInsight
- V1 adapter 只接受全新 revision 1；既有编辑链留给迁移阶段处理，避免重写历史 SHA 链

## 6. 场景集状态

`backend/eval/scenarios/manifest.json` 已覆盖八类必备场景，并在每次测试中验证：

- 预期 content role
- 预期 processing route
- 入口阶段允许创建的对象
- 禁止创建的长期认知对象

当前场景全部为脱敏合成 fixture。12—15 条匿名化真实样本仍用于后续 Prompt、Provider 和阈值校准；它们没有进入仓库，也没有触发真实 Vault 读取

## 7. 安全状态

- 产品模型调用：0
- 正式 Vault 写入：0
- 前端修改：0
- V1 后端修改：0
- 隔离临时目录写入：仅测试运行期间
- 产品 Provider 选择：未开始
- 真实样本评测：未开始
- 影子运行：未开始

## 8. 验证证据

```text
cd backend
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider
89 passed

python -m mypy --cache-dir=/tmp/memento-backend-mypy-r5 src tests
Success: no issues found in 78 source files

python -c '使用 ast.parse(feature_version=(3, 9)) 检查 src 与 tests'
Python 3.9 AST parse passed: 78 files

python -c '使用 Draft202012Validator.check_schema 检查全部 Schema'
JSON Schema self-check passed: 29 schemas

node tests/test_cognitive_home_library.js
cognitive-home-library contract tests passed

node tests/test_cognitive_demo_fixture.js
cognitive demo fixture tests passed

git diff --check
passed
```

## 9. B2 结论

B2 的实现与合同范围可以关闭。链接、长网页、带备注资料、用户原话、语音、外部痕迹与模糊输入已经进入各自路线；资料内容无法越过 L0 / L1 的 SourceSpan 边界写成长期认知

这项结论只覆盖文件完成与离线合成测试通过。产品模型质量、真实样本质量、影子运行和真实写入继续保持未验证状态

R6 可以在固定正式 fixture 上实现 Daily Integrator、MemoryAtom、Relation、Theme Synthesizer 与分时 Projection。R6 不需要打开真实 Vault 或选择产品 Provider
