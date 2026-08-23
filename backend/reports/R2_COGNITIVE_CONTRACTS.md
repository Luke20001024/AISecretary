# R2 · 认知对象合同

> 日期：2026-08-22
>
> 状态：完成

## 1. 本轮交付

- RecordInterpretationRevision V2
- MemoryAtomRevision V2
- RelationRevision V2
- ThemeRevision V2
- SelfInsightRevision V2
- AgentActionCandidate V1

## 2. 正式对象链

```text
SourceRecordRevision
→ CaptureDecisionRevision
→ RecordInterpretationRevision
→ MemoryAtomRevision
→ RelationRevision
→ ThemeRevision
→ SelfInsightRevision
```

AgentActionCandidate 位于正式链之外。Workflow 读取 candidate、复核来源与 policy、重新验证 proposed object，随后才允许提交正式 revision

## 3. 已冻结的产品门槛

- ready interpretation 至少引用一个精确 SourceSpan
- MemoryAtom 至少引用一条正式 interpretation 和一个 SourceSpan
- Theme 至少由两个 MemoryAtom、两个不同日期形成
- SelfInsight 至少引用两个 Theme 和两条支持依据
- sensitive / restricted SelfInsight 只能保持本地或 restricted
- 正式对象拒绝 candidate 字段

## 4. 本轮安全状态

- 产品模型调用：0
- 正式 Vault 写入：0
- 前端修改：0
- V1 后端修改：0

## 5. 完成状态

- 文件已创建：是
- 合同测试通过：是，33 passed
- 类型检查通过：是，mypy strict 0 issues
- 回归测试通过：是，现有 homepage contract 与 20 天 demo fixture 均通过
- 模型评测通过：未运行
- 影子运行通过：未运行
- 真实写入启用：否

## 6. 测试证据

```text
cd backend && /opt/anaconda3/bin/python -m pytest -q
33 passed

cd backend && /opt/anaconda3/bin/mypy src tests
Success: no issues found in 19 source files

node tests/test_cognitive_home_library.js
cognitive-home-library contract tests passed

node tests/test_cognitive_demo_fixture.js
cognitive demo fixture tests passed
```
