# R8 · 接回工作与双向 Context

> 日期：2026-08-23
>
> 状态：独立权限与 MCP 安全复核通过，R8 已关闭；R9 的合成基础设施已完成，真实质量门等待用户确认

## 1. 本轮交付

- `ContextGrantRevision` 的创建、精确 revision 绑定与撤销
- `ExternalSessionRevision` 的 client、task、topic、time scope 绑定
- 确定性 `ContextRouter` 与最多存活五分钟的 `ContextPackSnapshot`
- read / writeback 两类 `ContextReadAuditRevision`
- `ExternalTraceRevision + SourceRecordRevision + allowed audit` 的正式原子事务
- 五个读取工具与三个写回工具的本地 allow-list façade
- Context Pack 的 JSON 与 Markdown 双格式输出
- Context 引用类型、ID 前缀、敏感级别和原文引用的 Schema 级约束

## 2. 已实现链路

```text
当前 ContextGrant head
→ 当前 ExternalSession head
→ client + task + topic + time + sensitivity 复核
→ 当前正式 ProjectionInputs 复核
→ 最小 Context Pack
→ allowed read audit

外部结果 / 决定 / 新问题 / 用户纠正
→ 旧 Pack 与当前 Grant / Session 精确复核
→ ExternalTrace + SourceRecord + writeback audit 原子提交
→ next_route = L0_capture_understanding
```

外部调用不会直接创建、修改或删除 MemoryAtom、Relation、Theme 与 SelfInsight

## 3. 授权与最小披露

- SelfInsight 必须同时为 `user_confirmed + grant_only`
- 敏感等级不能高于 Grant 的 `max_sensitivity`
- 未授权对象类型不会出现在行数据、嵌套引用或 `selected_refs`
- 原文只在 Grant 和请求都允许时返回，且保留精确 SourceRecord revision 与 quote hash
- Pack 固定写入未知项与禁止推广边界，避免把局部 Context 解释成完整人格
- Grant 过期、撤销、client 不匹配、Session 不匹配、主题或时间越界全部拒绝
- 请求 task 必须与 Session task 逐字一致；请求和完成时间都必须位于 Grant 与 Pack 的有效窗口内
- 读取与写回的 allowed / denied 终态都进入正式 audit

## 4. 双向写回边界

- `correction` 必须带用户明确确认
- `context_refs` 必须精确属于本次 Pack，包含 revision hash
- Pack 过期后拒绝写回
- 只有存在精确 Pack hash、Grant revision 与 Session revision 的 allowed read audit，Pack 才能用于写回
- 新痕迹绑定 client、session、task、grant、pack 和形成时间
- 原始写回文本先保存在隔离 artefact Store；只有 SourceRecord、ExternalTrace 与 audit 同时发布后才进入正式可见 head
- 事务中断留下的未发布 artefact 不进入正式对象索引，重试使用相同确定性 ID 恢复

## 5. MCP 边界

允许的读取工具：

```text
memento.search_context
memento.get_self_insight
memento.get_theme
memento.trace_evidence
memento.create_context_pack
```

允许的写回工具：

```text
memento.append_trace
memento.correct_context
memento.report_outcome
```

当前只实现 transport-neutral 本地 dispatch，不开启 socket、不接收文件路径、不保存外部模型隐藏推理，也没有 Theme / SelfInsight mutation 工具

八个工具各自使用精确参数字段 allow-list。`vault_path`、Store 路径和工具专属字段的越权复用都会在 dispatch 边界拒绝。`requested_at` 与 `completed_at` 是本地组合层注入参数，不属于外部工具 arguments

## 6. 测试覆盖

- 五类 R8 ID 命名空间隔离
- Grant active / revoked 与 denied audit 合同反例
- 只返回用户确认理解，排除 observed / local_only 理解
- 只授权 SelfInsight 时嵌套 Theme / Memory 引用也不会泄漏
- stale 或 unpublished ProjectionInputs 拒绝并审计
- authority missing、Grant expired、Grant revoked、topic out of scope 拒绝并审计
- 外部 outcome 原子进入 SourceRecord 与 ExternalTrace，Theme / Self heads 不变
- 不支持的 writeback 与未确认 correction 不产生 trace/source
- 撤销后未来读取停止
- MCP 工具名精确 allow-list，缺少 direct cognitive mutation
- Session task 精确绑定、旧 Pack 跨 Session 复用拒绝
- Grant / Pack 在请求完成前到期时拒绝写回
- 字符串 `"false"` 不能冒充 correction 的布尔确认
- kind / ID 混淆、Pack 外 Context refs 与无 allowed read audit Pack 拒绝并审计
- 专用对象读取 miss 追加 denied audit
- Source quote 独立服从请求时间范围
- 外部事务在 revision 写入后中断时 Source、Trace、Audit 均不可见，使用同一请求重试可恢复

## 7. 独立安全复核

2026-08-23 完成独立权限与 MCP 攻击性复核。本轮发现并关闭以下缺口：

1. MCP 布尔参数曾使用 Python 真值转换，字符串 `"false"` 会被视为已确认；现改为严格布尔校验，并由 denied writeback audit 留痕
2. Context read 曾只绑定 client、Grant、Session、topic 和 time，未逐字核对 Session task；现已加入 task 精确绑定
3. writeback 曾只在请求开始时检查 Grant / Pack 到期；现同时检查完成时间、Pack 生成时间和操作时间单调性
4. 孤立 Pack artefact 曾可在缺少 allowed read audit 时进入写回；现要求 Pack hash、Grant revision、Session revision 与 allowed read audit 精确一致
5. 专用读取目标 miss 曾在生成 Pack 后抛出未审计异常；现追加 tool-specific denied audit，工具名与目标 ID 进入 request hash
6. Source quote 曾沿 Memory 的时间命中返回，未独立检查 SourceRecord 时间；现对原文引用再次执行时间范围过滤
7. MCP dispatch 曾忽略未知参数；现每个工具使用精确 required / optional 字段集合，路径参数直接拒绝
8. Context refs 现先执行严格 ObjectRef kind、ID 前缀、revision 与 hash 结构校验，再检查是否精确属于 Pack

复核同时确认：八个工具中没有 Theme、SelfInsight、Relation 或 MemoryAtom mutation；外部写回的正式可见边界仍为 `SourceRecord + ExternalTrace + allowed audit` 单事务；故障后未发布 revision 与 source artefact 不进入正式 head，确定性重试可以完成发布

## 8. 安全状态

- 产品模型调用：0
- 正式 Vault 写入：0
- 前端修改：0
- 网络 MCP transport：未启用
- 隔离临时目录写入：仅测试运行期间
- 产品 Provider 选择：未开始
- 真实样本评测：未开始
- 影子运行：未开始

## 9. 完成口径

- 文件已创建：是
- 合同测试通过：是
- 后端回归通过：是
- Python 3.9 兼容检查：是
- 现有前端回归：是
- 独立 Sol high 权限与 MCP 安全复核：通过
- 模型评测：未运行
- 影子运行：未运行
- 真实写入：未启用

R8 已满足关闭条件。当前停止在 R8，不进入 R9；真实样本、影子运行和真实写入仍保持关闭

## 10. 验证证据

```text
cd backend
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python3.12 -m pytest -q -p no:cacheprovider
127 passed

/opt/anaconda3/bin/python3.12 -m mypy --cache-dir=/tmp/memento-backend-mypy-r8 src tests
Success: no issues found in 102 source files

/usr/bin/python3 -c '使用 ast.parse(feature_version=(3, 9)) 检查 src 与 tests'
Python 3.9 AST parse passed: 102 files

/opt/anaconda3/bin/python3.12 -c '使用 Draft202012Validator.check_schema 检查全部 Schema'
JSON Schema self-check passed: 35 schemas

node tests/test_cognitive_home_library.js
cognitive-home-library contract tests passed

node tests/test_cognitive_demo_fixture.js
cognitive demo fixture tests passed

git diff --check
passed
```

工作树中后端范围外的修改均为本轮开始前已有的用户改动，本轮没有改写这些文件
