# R1 · 输入对象合同

> 日期：2026-08-22
>
> 状态：完成

## 1. 本轮交付

- ID、hash、时间与相对路径基础约束
- ObjectRef 与 SourceSpan 精确引用
- append-only revision 基础规则
- SourceRecordRevision V2 Schema
- CaptureDecisionRevision V1 Schema
- ResourceCardRevision V1 Schema
- ReadLaterIntentRevision V1 Schema
- 对应合同与边界测试

## 2. 已冻结的入口分层

```text
真实输入
→ SourceRecordRevision
→ CaptureDecisionRevision
   ├── 个人信号 → 后续逐条理解
   ├── 资料 → ResourceCardRevision
   ├── 稍后阅读 → ResourceCardRevision + ReadLaterIntentRevision
   ├── 混合输入 → 只解释明确的用户 signal span
   └── 模糊输入 → 保存并等待确认
```

ResourceCard 和 ReadLaterIntent 都不能携带 Theme、SelfInsight 或用户信念字段

## 3. 本轮安全状态

- 产品模型调用：0
- 正式 Vault 写入：0
- 前端修改：0
- V1 后端修改：0

## 4. 完成状态

- 文件已创建：是
- 合同测试通过：是，21 passed
- 类型检查通过：是，mypy strict 0 issues
- 回归测试通过：是，现有 homepage contract 与 20 天 demo fixture 均通过
- 模型评测通过：未运行
- 影子运行通过：未运行
- 真实写入启用：否

## 5. 测试证据

```text
cd backend && /opt/anaconda3/bin/python -m pytest -q
21 passed

cd backend && /opt/anaconda3/bin/mypy src tests
Success: no issues found in 17 source files

node tests/test_cognitive_home_library.js
cognitive-home-library contract tests passed

node tests/test_cognitive_demo_fixture.js
cognitive demo fixture tests passed
```
