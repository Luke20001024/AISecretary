# Backend V2 · 合码就绪审计

> 日期：2026-08-23
>
> 当前结论：B0—B5 的实现与离线合同已经闭合；B6 的安全执行基础设施已闭合，真实质量门等待用户授权与真实影子运行；B7 保持未启动

## 1. 审计口径

本报告从以下权威文件反向核对交付物：

- `docs/backend-design/00_BACKEND_MASTER_PLAN.md`
- `docs/backend-design/03_DATA_AND_INTERFACES.md`
- `docs/backend-design/04_FILE_MANIFEST.md`
- `docs/backend-design/05_IMPLEMENTATION_AND_MERGE.md`
- `docs/backend-design/08_AI_EXECUTION_PLAN.md`
- `docs/backend-design/09_R9_USER_CONFIRMATION.md`

“可合码”在这里表示后端对象、Projection、Store、Agent、Context 与评测合同已经稳定，当前前端可以继续依赖 V1 adapter 和固定 fixture。它不代表已经切换前端数据源，也不代表真实用户数据质量已经通过

## 2. B0—B7 证据矩阵

| Gate | 状态 | 已完成证据 | 仍需完成 |
|---|---|---|---|
| B0 · 合同与样本 | 已关闭 | 正式对象 Schema、20 天 fixture、五类顶层 Projection、四类 Detail、同 manifest 原子引用、确定性 ID / hash、V2→V1 adapter、Sol high 合同复核 | 无 |
| B1 · Store | 已关闭 | AtomicFileStore、RevisionStore、HeadIndex、BundleStore、ActionInbox、CAS、tombstone、事务恢复、publication 回滚 | 无 |
| B2 · L0 / L1 | 已关闭，真实语义待 B6 验证 | Capture Understanding、Record Interpreter、Resource Reader、八类合成入口、资料与用户判断隔离、RunLedger | 真实样本上的 Provider 质量 |
| B3 · L2 / L3 | 已关闭，真实语义待 B6 验证 | Daily Integrator、Relation、Theme 生命周期、四节点 20 天地形回放、来源与反例保留 | 真实样本上的召回、误连与漏连质量 |
| B4 · L4 | 已关闭，真实语义待 B6 验证 | Self material gate、敏感停止、用户确认 / 修订 / 撤回、Self→Theme→Memory→Source 全链回溯、过度推断复核 | 真实样本上的推断质量 |
| B5 · L5 | 已关闭 | scoped grant、Context Router、Context Pack、八个本地 allow-list 工具、读取审计、ExternalTrace 双向回流、独立安全复核 | 网络 transport 继续保持关闭，不影响本地合同 |
| B6 · 真实影子运行 | 基础设施完成，质量门打开 | 无副作用 preflight、consent、只读 snapshot、case set、无 gold worker、work product、预算硬门、独立评估、密封 report、合成端到端测试 | 用户确认、12—15 条匿名化真实案例、真实只读快照、选定 Provider / Model worker、真实 run 达标 |
| B7 · 前后端合码 | 前端接线未启动 | V1 adapter 已证明 Projection 数据兼容；`ProjectionReadApi`、`ActionApi`、`RunRequestInbox` 已提供稳定本地合码边界；当前前端回归持续通过 | B6 关闭后增加 feature flag、V2 data source、详情 / action / session 接线与集成测试 |

## 3. 前后端当前已经对齐的合同

### 读取

- 一份 `ProjectionBundleManifest` 原子发布 Home、Timeline、Landscape、Self、Detail Index
- Record、Resource、Theme、SelfInsight 分别拥有独立详情 Projection
- Theme 与 SelfInsight 使用不同对象类型、ID 空间与详情入口
- 所有详情可以沿正式引用回到 SourceRecord 与 SourceSpan
- `BundleStore.load_current()` 只返回通过完整跨文件语义校验的 current bundle
- `ProjectionReadApi` 从同一 current manifest 读取五类顶层 Projection、四类 Detail、外部会话与 run status，返回值与 Store 内部对象隔离
- V2 bundle 可以转换为通过现有 `validateProjectionPair` 与 `validateProjectionAuthority` 的 V1 数据

### 写入

- 前端动作只进入 append-only ActionInbox
- `ActionApi` 提供 action 提交、terminal result 查询与 run request；`RunRequestInbox` 保留不可变 request / result 和 action watermark 绑定
- action 绑定目标 revision 与用户 action watermark
- 每条 action 最终拥有 applied、rejected 或 conflict terminal result
- Agent candidate 无直接 Store 权限，正式提交由 Workflow 重新校验
- 外部写回只能追加 ExternalTrace 与 SourceRecord，再回到 L0 / L1

### 回滚

- Projection 使用 staging、publication 与单一 current pointer
- 无效 current 可以从 publication 链恢复
- feature flag 的 fixture / v1_adapter / v2_shadow / v2_live 顺序已经冻结
- 当前 fixture 与前端文件持续保留

## 4. 早期文件清单差异

审计发现早期建议清单中有若干路径按职责合并落地。它们均已在 `04_FILE_MANIFEST.md` 建立等价映射：

- 纯对象模块由 JSON Schema、ObjectRef 与 revision 原语承载
- `stores/` 统一为 `storage/`
- bundle publisher 合并到负责原子 publication 的 BundleStore
- read / action 的本地 façade 已落地，网络 transport 与前端 data-source 接线仍属于 B7
- 对应合同测试按认知对象、Store、Projection 和 Workflow 重新分组

这组差异不改变对象合同、Projection 输出或前端兼容结果

## 5. 唯一剩余授权包

B6 需要用户一次性确认：

1. 允许读取的真实 Vault 绝对路径与文件后缀
2. 误连、漏连、过度推断、停止 F1、成本与延迟阈值
3. Theme 与 SelfInsight material gate
4. 敏感信息默认策略
5. 六级 Agent 的评测频率
6. 产品 Provider、精确模型名称与预算上限
7. 12—15 条匿名化真实案例及期望结果

确认后严格按以下顺序执行：

```text
元数据 preflight
→ 用户确认并签发正式 consent
→ 密封只读 snapshot
→ snapshot-bound case set
→ provider_shadow plan
→ 无 gold worker work product
→ 独立指标评估与 sealed report
→ passed 后关闭 B6
→ 才允许进入 B7
```

## 6. 安全状态

- 产品模型调用：0
- 真实 Vault 读取：0
- 真实 Vault 写入：0
- 正式 revision 写入：0
- 前端修改：0
- R10 / B7 接线：未启动
- 本次清理：仅移除可再生成的 Python cache，并新增后端 `.gitignore`

## 7. 当前决策

后端已经达到“可以开始真实只读验收”的状态。当前不能宣称达到“真实质量通过”或“已经完成前后端合码”

在用户确认 B6 授权包之前，继续增加产品 Provider、读取真实 Vault 或修改前端都会越过已冻结的安全门

## 8. 本次验证证据

```text
cd backend
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider
204 passed

/opt/anaconda3/bin/mypy --cache-dir=/tmp/memento-backend-mypy-preflight-final src tests eval/run_shadow.py
Success: no issues found in 123 source files

/opt/anaconda3/bin/python -c '使用 ast.parse(feature_version=(3, 9)) 检查 src、tests 与 eval/run_shadow.py'
Python 3.9 AST parse passed: 123 files

/opt/anaconda3/bin/python -c '使用 Draft202012Validator.check_schema 检查全部 schemas'
JSON Schema Draft 2020-12 self-check passed: 44 schemas

PYTHONPATH=src /opt/anaconda3/bin/python eval/run_shadow.py --help
passed

node tests/test_cognitive_home_library.js
cognitive-home-library contract tests passed

node tests/test_cognitive_demo_fixture.js
cognitive demo fixture tests passed

git diff --check -- backend docs/backend-design
passed
```

测试期间使用 `PYTHONDONTWRITEBYTECODE=1`，后端目录当前没有 `__pycache__` 或 `.pyc` 残留
