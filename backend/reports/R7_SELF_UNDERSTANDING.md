# R7 · 她理解的我

> 日期：2026-08-23
>
> 状态：离线合成合同、Workflow 与过度推断质量门通过

## 1. 本轮交付

- L4 `SelfUnderstandingAgent` 与 `UpdateSelfUnderstandingWorkflow`
- 版本化 Self material gate、Prompt 标识与敏感推断策略
- SelfInsight 的 `draft / observed / user_confirmed / restricted` 确认等级
- `ApplySelfActionWorkflow` 处理确认、限定范围、直接修订与撤回
- SelfProjection 与 SelfInsight Detail 展示确认等级、Theme 来源、支持、边界和变化原因
- RevisionStore 的已发布 revision 历史读取
- user revision 的 `committing_action_id` 与 terminal result 中断恢复
- 基于固定 20 天正式对象的第三层形成、修订与回溯测试

## 2. 正式链路

```text
两个以上 active / tension Theme heads
→ Self material gate 检查不同 Theme 与正式证据数量
→ 敏感推断检查
→ SelfUnderstandingAgent 生成 candidate
→ Workflow 复核引用、Prompt、Policy、watermark 与当前 heads
→ SelfInsightRevision 原子提交
→ SelfProjection / SelfInsightDetailProjection
→ 用户确认、限定、修订或撤回
→ 新的 user revision
```

第三层的可审计来源保持：

```text
SelfInsight
→ Theme
→ Relation / MemoryAtom
→ RecordInterpretation
→ SourceRecord / SourceSpan
```

## 3. Material gate 与推断边界

新 SelfInsight 必须满足：

1. 至少两个不同的 active / tension Theme
2. 每个 Theme 已经拥有至少两条正式 evidence refs
3. 所有 Theme、MemoryAtom 和既有 SelfInsight 都是当前已提交 head
4. 输入对象创建时间不晚于 `as_of`
5. 内容未触发敏感推断停止策略

单个主题、局部偶发行为、资料页内容和缺少长期依据的判断不会进入第三层。涉及人格、身份、政治、宗教、健康、情绪、家庭与亲密关系的自动推断返回 `stop`，不会创建正式 SelfInsight

## 4. 确认等级与权限

| 状态 | 形成方式 | 可见范围 |
|---|---|---|
| draft | Agent 首次形成的低风险当前理解 | local_only |
| observed | 后续 Provider 经同一合同形成的已观察理解 | local_only |
| user_confirmed | 用户确认、限定或修订的普通内容 | grant_only |
| restricted | 敏感迁移对象或持续受限内容 | restricted |

Agent 不能把理解标为用户已确认，也不能覆盖用户确认后的 revision。用户动作绑定目标 revision hash；目标已变化时保留 conflict 终态，不套用旧修改

## 5. 修订与撤回

- Theme 新 revision 改变支持、边界或张力时，SelfInsight 追加新 revision
- 每次修订保存旧 revision hash、当前 change reason、Theme refs、support refs 与 boundary refs
- `RevisionStore.list_revisions` 只沿当前已发布 head 链读取完整历史
- 中断事务中的未发布文件不会出现在历史查询中
- 用户撤回后 `maturity=tombstone`、`visibility=restricted`
- tombstone 后任何 Agent、Workflow 或直接 Store 提交都不能复活该对象

## 6. 过度推断复核

本轮复核确认：

- SelfInsight 与 Theme 使用独立 ID、Schema 和 Projection
- 地形峰只来自 Theme，SelfInsight 只进入第三层理解
- Agent 输入保持为显式受限 Theme 与支持材料，不扫描真实 Vault
- Agent candidate 不能扩大 Workflow 授权的来源集合
- 敏感内容默认无法进入外部 Context
- 用户确认和修订对旧 Agent run 具有优先权
- 0 模型输出只组合已提交 Theme 的 statement、scope 与边界，不添加新的个人属性

## 7. 测试覆盖

- 一个 Theme：`no_change`
- 三个长期 Theme：创建 draft SelfInsight
- 敏感推断：`stop` 且 0 正式写入
- Theme 进入 tension：SelfInsight 新 revision 增加边界与不确定性
- SelfProjection 与 SelfInsight Detail：回溯三个 Theme 及正式支持
- 用户确认：普通内容变为 `user_confirmed + grant_only`
- 用户确认后旧 Agent 更新：authorization 拒绝
- 用户撤回：tombstone 且不可复活
- 目标 revision 已变化的 action：保留 conflict 结果且 0 覆盖
- formal revision 已提交而 terminal result 中断：重试恢复同一 applied 结果且不产生重复 revision
- 敏感迁移对象收到用户动作：持续 `restricted`
- revision 1 与 revision 2 的 statement / change_reason 可按已发布历史查询

## 8. 安全状态

- 产品模型调用：0
- 正式 Vault 写入：0
- 前端修改：0
- V1 后端修改：0
- 隔离临时目录写入：仅测试运行期间
- 产品 Provider 选择：未开始
- 真实样本评测：未开始
- 影子运行：未开始

## 9. 完成口径

- 文件已创建：是
- 合同测试通过：是
- 现有后端回归：是
- 现有前端回归：是
- 模型评测：未运行
- 影子运行：未运行
- 真实写入：未启用

## 10. 验证证据

```text
cd backend
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider
105 passed

/opt/anaconda3/bin/mypy --cache-dir=/tmp/memento-backend-mypy-r7-final src tests
Success: no issues found in 90 source files

/usr/bin/python3 -c '使用 ast.parse(feature_version=(3, 9)) 检查 src 与 tests'
Python 3.9 AST parse passed: 90 files

/opt/anaconda3/bin/python -c '使用 Draft202012Validator.check_schema 检查全部 Schema'
JSON Schema self-check passed: 30 schemas

node tests/test_cognitive_home_library.js
cognitive-home-library contract tests passed

node tests/test_cognitive_demo_fixture.js
cognitive demo fixture tests passed

git diff --check
passed
```

R7 的离线实现范围可以关闭。R8 可以在这些确认等级、可追溯 SelfInsight 和用户动作语义上实现授权、Context Pack、读取审计与外部写回
