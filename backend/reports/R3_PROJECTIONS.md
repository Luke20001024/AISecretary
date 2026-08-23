# R3 · Projection 与当前前端兼容合同

> 日期：2026-08-23
>
> 状态：Sol high 合同复核通过，B0 可以关闭

## 1. 本轮交付

- ProjectionBundleManifest V1
- HomeProjection V2
- TimelineProjection V1
- LandscapeProjection V2
- SelfProjection V1
- DetailIndexProjection V1
- Record、Resource、Theme、SelfInsight 四类 Detail Projection
- 确定性 `stable-theme-terrain-v1` 地形投影
- V2 → V1 Projection adapter 与旧前端 ProjectionAuthority
- 20 天合成正式对象回放 fixture
- empty、loading、stale、conflict、failed_preserved 五类状态 fixture

## 2. 已冻结的读模型链

```text
正式 revision objects
→ 0 模型确定性 Projector
→ 一个原子 Projection bundle
→ V2 前端合同
→ 过渡期 V1 adapter
→ 当前 JavaScript validator
```

投影过程不重新解释记录。它只读取已提交的正式对象，完成排序、引用归拢、确定性布局和前端字段转换

## 3. 原子发布约束

- Manifest 完整列出 bundle 内每个文件的路径、projection ID 与 SHA-256
- 全部 Projection 共享同一个 `bundle_id`、`as_of`、`generated_at` 与 `input_sha256`
- Home 对 Timeline、Landscape、Self 的引用必须与实际文件 hash 一致
- DetailIndex 必须覆盖全部四类详情文件
- Timeline、Landscape、Self、Home 中的详情入口必须与 DetailIndex 的主体和 projection ID 一致
- 每个 detail 文件的主体引用必须与 DetailIndex 中的主体引用完全一致
- Manifest 内的路径与 projection ID 必须唯一
- 任一文件丢失、hash 不一致或引用陈旧时，bundle 语义校验失败
- `previous_bundle_sha256` 与 `previous_projection_sha256` 为下一阶段原子发布和回滚保留接口

## 4. 三层展示边界

- Timeline 与 Record Detail：记录和逐条理解
- Landscape 与 Theme Detail：跨日关系形成的长期主题
- Self 与 SelfInsight Detail：多个主题共同支持的当前自我理解

地形峰只来自 Theme。SelfInsight 使用独立 ID、独立 Projection 与独立详情入口

## 5. 确定性地形

- 位置由正式 Theme / MemoryAtom ID 的稳定 hash 生成
- 高度只读取证据数、跨日时长和近期变化
- MemoryAtom 围绕所属 Theme 稳定排列
- Relation 只连接当前正式图谱中存在的端点
- 输入对象顺序变化不会改变 bundle hash
- Projector 只读取 `as_of` 当日已经创建的正式对象，历史日期不会混入未来证据
- 视觉坐标不产生新的认知语义

## 6. V1 兼容层

- Theme 映射为 V1 understanding
- MemoryAtom 映射为 V1 reusable memory
- Relation 映射为旧前端可识别关系类型
- RecordInterpretation 映射为稳定 receipt ID
- SelfProjection 映射为现有 portrait 结构
- adapter 生成 V1 Home、Landscape、landscape hash 与 ProjectionAuthority

最终日、历史首日、空数据和同日多记录混合状态均已通过当前 `validateProjectionPair` 与 `validateProjectionAuthority`

## 7. 回溯能力

```text
SelfInsight Detail
→ Theme Detail
→ MemoryAtom
→ RecordInterpretation
→ SourceRecord 与 SourceSpan
```

20 天 fixture 中每一座地形峰都能回到至少两个 MemoryAtom 和两条原始记录

正式对象引用在进入 Projector 前还会验证对象类型、ID、revision 与 revision SHA。SourceSpan 同样必须绑定当前输入集中同一 SourceRecord 的精确 revision

## 8. Sol high 合同复核修复

- 修复 `as_of` 只写元数据却继续读取未来对象的问题
- 修复不同时区 `created_at` 依赖字符串排序的问题
- 修复同一输入接入不同上一版链路时仍复用同一 bundle / projection ID 的问题
- Bundle 语义校验现在会重新执行每个 Projection 的 JSON Schema，不接受仅重算 manifest hash 的畸形文件
- 收紧 Timeline、Landscape、Self 与四类 Detail 中各字段允许引用的对象类型
- Resource 与 Theme Detail 的 SourceSpan 改为完整合同，禁止任意对象混入
- 状态 fixture 明确为上一份合法 Home 的展示覆盖层，避免同一 bundle ID 指向多份内容
- 增加第 1 天、第 5 天、第 20 天增长回放、空数据、同日多记录、陈旧 revision、错位详情与重复 projection ID 反例
- 修复同时间戳资源和同一记录多 interpretation 候选依赖输入数组顺序的问题；相同正式对象集合现在始终生成相同 bundle bytes
- Resource 的 `user_selected_spans` 现在重新校验 quote hash、当前 SourceRecord revision、所属记录、来源文件和行号范围
- V1 adapter 转换前重新执行完整 ProjectionBundle 语义校验，陈旧 manifest 或任一文件被改写时拒绝转换
- V1 `ProjectionAuthority` 的 action watermark 改为由调用方显式传入，禁止用 Projection 输入 hash 冒充当前 Action Store watermark
- 独立反例复测发现 Projector 仍有三处按 ISO 文本选择“最新对象”；现已统一按带时区的真实时刻排序，并以原始时间文本和对象 ID 稳定破除并列
- Bundle validator 重新计算 `bundle_id`、五类顶层 projection ID 与四类 detail projection ID；即使同步改写全部 `bundle_id` 并重算文件 hash，也不能伪造另一份确定性 bundle 身份
- 增加跨 Projection 当前引用清单：Timeline、Landscape、Self、Home 与 Detail 中重复出现的引用必须绑定同一个 kind、ID、revision 和 revision SHA
- SelfProjection 中的 Theme、Landscape node 的 Theme、Landscape edge 端点、Home recent change 与详情内部引用都必须存在于同一 bundle 的当前主体集合
- Theme Detail 的 Record 列表必须由其 MemoryAtom SourceSpan 精确推导；重新封装后指向一条存在但无关的记录同样会被拒绝
- Home 今日计数、Landscape summary 与 Self detail 数量必须和对应顶层内容一致，防止单文件重新封装造成 UI 汇总漂移
- 新增跨时区最新解释、伪造确定性 bundle 身份、孤立 Self→Theme 引用、Theme 详情回到无关记录四类回归反例
- Home 资料卡引用现在必须与 Resource Detail 的 kind、ID、revision 和 revision SHA 完全一致，拒绝只保留同 ID 的陈旧引用
- BundleStore 读取密封 bundle 时会递归盘点全部文件，Manifest 未列出的隐藏或普通文件都会使整份 bundle fail-closed

## 9. 本轮复核安全状态

- 产品模型调用：0
- 正式 Vault 写入：0
- 前端修改：0
- 已有 R4 BundleStore 变更：仅收紧密封 bundle 读取校验，未新增写入能力
- Projection 持久化测试：仅使用 pytest 隔离临时目录

## 10. 完成状态

- 文件已创建：是
- JSON Schema 自检通过：是，44 份 schema
- 合同与投影测试通过：是，当前后端全量 201 passed；其中 R3 Projection 聚焦测试 29 passed
- 类型检查通过：是，mypy strict 0 issues，120 个 Python 文件
- Python 3.9 兼容检查通过：是，120 个 Python 文件
- 当前前端回归通过：是
- 本次复核模型调用：未运行
- 本次复核真实写入：未启用

## 11. 测试证据

```text
cd backend
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider
201 passed

PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider tests/projections
29 passed

/opt/anaconda3/bin/mypy --cache-dir=/tmp/memento-backend-mypy-r3-review src tests
Success: no issues found in 122 source files

/opt/anaconda3/bin/python -c '使用 ast.parse(feature_version=(3, 9)) 检查 src 与 tests'
Python 3.9 AST parse passed: 122 files

/opt/anaconda3/bin/python -c '使用 Draft202012Validator.check_schema 检查全部 schemas'
JSON Schema Draft 2020-12 self-check passed: 44 schemas

node tests/test_cognitive_home_library.js
cognitive-home-library contract tests passed

node tests/test_cognitive_demo_fixture.js
cognitive demo fixture tests passed

git diff --check -- backend docs/backend-design
passed
```

## 12. B0 结论

B0 可以关闭。R3 已满足完整 Projection 合同、确定性历史回放、跨文件原子一致性、Theme / Self ID 隔离、详情回溯与 V1 权威校验要求

当前工作树已经包含后续 R4–R9 实现。本轮复核修改 R3 bundle 语义 validator、BundleStore 密封文件盘点、对应反例测试与本报告；这些变更只收紧 R3 读模型完整性，未开始新的 R4 开发轮次

## 13. 复核边界

R3 的职责边界保持为“从已提交正式对象生成可重建读模型，并通过 V1 兼容层交给当前前端”。Store、Agent、外部 Context 与真实合码继续由各自阶段负责，本次没有向这些阶段扩展
