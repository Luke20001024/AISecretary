# R4 · Revision、Action 与 Projection Store

> 日期：2026-08-23
>
> 状态：实现与质量门通过，B1 本地事实存储范围可以关闭

## 1. 本轮交付

- owner-only、symlink / hardlink fail-closed 的 `AtomicFileStore`
- 九类正式对象共用的 append-only `RevisionStore`
- 可由完整 transaction manifests 重建的 `FormalHeadIndex`
- 多对象单事务可见性、精确 `ObjectRef` CAS 与 tombstone 终止规则
- append-only `ActionInbox`、单一 terminal result 与可重建 action watermark
- ProjectionBundle staging、完整合同复核、immutable seal、publication、current pointer 与 rollback
- 7 份存储与发布 JSON Schema
- 中断、并发、权限、路径、CAS、恢复和损坏回退测试

## 2. 三条事实链

### 2.1 正式对象

```text
revision files
→ complete RevisionTransaction
→ rebuildable FormalHeadIndex
```

单个或多个对象在同一 transaction manifest 完成后才具备恢复资格。Head Index 发布中断时，`recover()` 会沿 generation 和 previous transaction SHA 重建；只有 revision 文件、没有 transaction manifest 的孤儿不会进入 head

### 2.2 用户动作

```text
UserAction
→ action watermark advances
→ CAS / policy processing
→ one ActionResult
```

提交基准 watermark 过期或目标 revision 变化时，原始 action 仍保持 append-only，结果以 `conflict` 和稳定 reason code 记录。Agent / Workflow 可以在提交前再次调用 `assert_watermark()`，阻止用户操作之后完成的旧 run

### 2.3 前端读模型

```text
staging bundle
→ cross-file validation
→ immutable sealed bundle
→ append-only publication
→ atomic current pointer
```

staging、中断后的孤立 sealed bundle 和不完整 publication 都不会直接成为前端当前读模型。publication 已完整落盘而 current pointer 尚未替换时，`recover_current()` 可以恢复；最新 bundle 的文件或 hash 损坏时会保留上一份通过全部合同校验的 publication

## 3. 原子性与并发结果

- 同一 formal head 的并发 CAS 只有一个提交成功
- 同一 action watermark 的并发 action 只有一个无冲突提交，另一条保留 conflict result
- 同一 current bundle 的两个后继候选只有一个成为 current pointer
- 多对象事务在 transaction manifest 之前中断时，整组对象保持不可见
- publication 之前中断时，前端继续读取上一份 bundle
- publication 之后、pointer 之前中断时，可从 publication 链恢复
- append-only inode 对外可见前已经移除临时 hardlink，读者不会观察到瞬时双链接

## 4. 文件安全

- 所有 Store 只接受显式隔离根目录
- 根目录与子目录要求 owner-only
- 文件要求当前用户、`0600`、普通文件、单 hardlink
- POSIX 相对路径经过规范化，禁止路径逃逸
- symlink、hardlink、FIFO 和不安全权限 fail-closed
- replace 能力限制在 rebuildable `indexes/` 和 `projections/current.json`
- 临时文件与目标位于同一目录，写入后执行文件和目录 `fsync`

## 5. 合同范围

R4 Store 已覆盖：

- SourceRecord
- CaptureDecision
- ResourceCard
- ReadLaterIntent
- RecordInterpretation
- MemoryAtom
- Relation
- Theme
- SelfInsight
- UserAction / ActionResult / Watermark
- ProjectionBundle / Publication / Current Pointer

External Context 的 Grant、Session、ReadAudit 与 Trace Store 按 `08_AI_EXECUTION_PLAN.md` 留在 R8，避免提前扩张 R4 边界

## 6. 安全状态

- 产品模型调用：0
- 正式 Vault 写入：0
- 前端修改：0
- V1 后端修改：0
- 隔离临时目录写入：仅测试运行期间
- Projection 正式发布：未启用；当前只有可复用 Store 实现

## 7. 验证证据

```text
cd backend
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider
74 passed

/opt/anaconda3/bin/mypy --cache-dir=/tmp/memento-backend-mypy-r4 src tests
Success: no issues found in 50 source files

/opt/anaconda3/bin/python -c '使用 ast.parse(feature_version=(3, 9)) 检查 src 与 tests'
Python 3.9 AST parse passed: 50 files

node tests/test_cognitive_home_library.js
cognitive-home-library contract tests passed

node tests/test_cognitive_demo_fixture.js
cognitive demo fixture tests passed

git diff --check
passed
```

## 8. B1 结论

B1 的本地事实存储范围可以关闭。九类正式对象已具备创建、修订、tombstone、CAS、并发互斥、Head 重建和中断恢复；Action 与 Projection 发布同样具有独立事实链和回退路径

R5 进入 L0 / L1 Agent 与 Workflow 时，模型仍只产出 candidate。正式提交继续经过当前 RevisionStore、Action watermark 和 BundleStore，不向 Agent 开放文件写权限

## 9. 尚未验证

- 产品模型评测：R5 才开始
- 真实 Vault 影子运行：R7
- 外部 Context 双向调用：R8
- 前后端合码：R8 质量门之后
