# R6 · 日级记忆、长期主题与地形生长

> 日期：2026-08-23
>
> 状态：离线合成合同与 Workflow 质量门通过，B3 实现范围可以关闭

## 1. 本轮交付

- L2 `DailyIntegrator` 与 `ConsolidateDayWorkflow`
- L3 `ThemeSynthesizer` 与 `UpdateThemeWorkflow`
- 版本化 daily / theme policy、Prompt 标识与 material gate
- 临时 `DailyIntegrationCandidate` 合同
- MemoryAtom 新建、精确重复强化与同主题关系
- Theme 新建、强化、修订、范围收窄、张力、休眠、恢复与 no_change
- 第 1、5、11、20 天四阶段 ProjectionBundle 地形生长回放
- Agent candidate、RunLedger、Action watermark、当前 head 与原子提交复核

## 2. 正式对象链

```text
当日 SourceRecord + RecordInterpretation heads
→ DailyIntegrator 生成事务 Candidate
→ Workflow 复核日期、来源、SourceSpan、Prompt、Policy 与 watermark
→ MemoryAtom + Relation 原子提交
→ 跨日期 material gate
→ ThemeSynthesizer 生成 Theme candidate
→ Workflow 复核支持、反例、关系、当前 revision 与变化原因
→ ThemeRevision 提交
→ 确定性 Landscape / Theme Detail Projection
```

`DailyIntegrationCandidate` 只负责一次原子提交，不进入正式认知对象集合。正式引用链保持：

```text
Theme
→ Relation / MemoryAtom
→ RecordInterpretation
→ SourceRecord / SourceSpan
```

## 3. Daily Integrator 的取舍

- `ready` 与 `needs_review` 且存在主题和摘要的逐条解释才可物化
- 新解释先形成 MemoryAtom，原文定位和解释引用一起保留
- 完全相同的记忆再次出现时追加证据，保留 first_seen、旧引用和旧 SourceSpan
- 新 MemoryAtom 与当前历史 MemoryAtom 共享规范化 topic 时生成正式 `same_topic` Relation
- 一次运行可以同时提交多条 MemoryAtom 与 Relation，RevisionStore 只在完整事务后更新 heads
- 无可用记忆时输出 `no_change` 并写入 RunLedger

当前 0 模型基线只识别精确规范化 topic。语义近似关系、同义词和复杂支持关系需要后续 Provider 场景评测，正式提交仍经过相同 Workflow

## 4. Theme material gate

新 Theme 必须同时满足：

1. 至少两个 active MemoryAtom
2. 支持证据至少覆盖两个不同日期
3. 至少一条连接这些证据的当前正式 Relation
4. 所有输入都处于 `as_of` 当日或更早

单日偶发记录、只有一个 MemoryAtom、缺少正式关系或包含未来证据时均停止，不创建 Theme

## 5. Theme 生命周期

| 状态变化 | 触发证据 | 保留内容 |
|---|---|---|
| new | 跨日证据首次通过 material gate | title、statement、scope、支持证据与关系 |
| reinforce | 新的正式 MemoryAtom 加入 | 旧证据与新增证据 |
| revise | 新 `revises` Relation | 旧 revision hash、新表述与变化原因 |
| scope | 新 `scope_boundary` Relation | 原 scope、新边界与变化原因 |
| tension | 支持与 counterexample 同时存在 | 支持证据、反例与关系 |
| dormant | 30 天没有新增支持证据 | 历史 revision 与当前休眠状态 |
| recover | 休眠后出现新的跨日支持证据 | 休眠历史、新证据与恢复原因 |
| no_change | 没有实质变化 | 0 正式写入，RunLedger 记录终态 |

旧的 `scope_boundary` 和 `revises` Relation 只作为历史依据保留，不会在后续 revision 中重复应用

## 6. 20 天地形生长证据

同一组合成正式对象按 `as_of` 生成四份只读 Projection：

| 快照 | MemoryAtom nodes | Relation edges | Theme peaks | 展示含义 |
|---|---:|---:|---:|---|
| 第 1 天 | 1 | 0 | 0 | 一个记录点 |
| 第 5 天 | 2 | 1 | 1 | 跨日关系形成第一块局部地形 |
| 第 11 天 | 3 | 1 | 1 | 新点继续积累，既有局部地形保持 |
| 第 20 天 | 6 | 3 | 3 | 三组长期关系形成完整认知地形 |

每份 Landscape 保存上一份 Projection SHA。相同输入与发布链元数据会得到相同 bundle hash、节点、边和峰；动画只切换这些快照，不修改 Theme 或 MemoryAtom

## 7. 本轮修复的边界问题

- 强化 MemoryAtom 时允许旧证据继续存在，同时只允许追加当日授权解释
- 禁止强化 revision 删除历史 evidence refs 或 SourceSpan
- Theme 只把本次新增的 `revises` 与 `scope_boundary` Relation 应用到文本，避免旧边界重复叠加
- Theme 输入拒绝未来 MemoryAtom，并要求既有 Theme 的证据和关系完整出现在受限输入窗口
- 包导出统一为单一 import / `__all__` 结构

## 8. 安全状态

- 产品模型调用：0
- 正式 Vault 写入：0
- 前端修改：0
- V1 后端修改：0
- 隔离临时目录写入：仅测试运行期间
- 产品 Provider 选择：未开始
- 真实样本评测：未开始
- 影子运行：未开始

## 9. 验证证据

```text
cd backend
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider
95 passed

python -m mypy --cache-dir=/tmp/memento-backend-mypy-r6 src tests
Success: no issues found in 85 source files

python -c '使用 ast.parse(feature_version=(3, 9)) 检查 src 与 tests'
Python 3.9 AST parse passed: 85 files

python -c '使用 Draft202012Validator.check_schema 检查全部 Schema'
JSON Schema self-check passed: 30 schemas

node tests/test_cognitive_home_library.js
cognitive-home-library contract tests passed

node tests/test_cognitive_demo_fixture.js
cognitive demo fixture tests passed

git diff --check
passed
```

## 10. B3 结论

B3 的离线实现与合同范围可以关闭。隔离目录已经完成记录 → 解释 → MemoryAtom → Relation → Theme → Landscape 的可审计链路，固定场景覆盖新增、强化、修订、收窄、张力、休眠、恢复与 no_change

文件完成和离线合成测试已通过。产品模型的语义召回质量、匿名化真实样本、影子运行和真实 Vault 写入继续保持未验证状态

R7 可以在当前 Theme heads 与完整证据链上实现 Self Understanding Agent、SelfInsight material gate、敏感策略和第三层 Projection；R7 暂不需要打开真实 Vault
