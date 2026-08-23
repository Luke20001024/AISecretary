# R9 · 只读影子评测执行闭环

> 日期：2026-08-23
>
> 状态：合成基础设施、独立 worker 执行闭环与高强度合同复核通过；R9 真实质量门仍未运行，R10 未开始

## 1. 已实现范围

- 只读打开来源、拷贝前后复核 hash 的密封快照
- 真实 Vault 快照必须保存用户授权引用
- 用户审阅的数据范围、质量阈值、Gate、敏感策略、Agent 频率、Provider 与预算封为确定性 `shadow-consent-v1`
- consent 同时绑定规范来源路径、snapshot authorization、plan confirmation、plan hash 与 sealed report
- 预注册数据集、执行模式、Provider / Model、Prompt / Policy 版本、阈值与预算
- `shadow-case-set-v1` 将每个案例的精确输入文件、hash、标准答案与检查分母绑定到快照
- `ShadowProducer` 边界只暴露 case ID、输入字节与检查分母，不暴露 expected links、allowed inferences 和 should stop
- `shadow-work-product-v1` 绑定 plan、case set、snapshot、Provider 配置、逐案预测、usage 与 candidate Bundle hash
- worker 执行时只打开 case set 声明的快照文件，逐文件复核 hash，并逐步执行 token / cost / latency budget
- 误连、漏连、过度推断、停止质量、证据有效性、Self 全链路回溯、资源误归因、旧对象复活、adapter、原文 hash、成本和延迟指标
- 完整 ProjectionBundle 候选与快照内基线文件的精确绑定
- plan、snapshot ref、case set、work product、规范化 observations、candidate Bundle 和 report 的单目录原子发布
- 发布后整个 run 目录改为只读，幂等重试时重新验证计划、快照、observation、指标和候选 Bundle
- `preflight` / `consent` / `snapshot` / `case-set` / `plan` / `evaluate` 六个本地 CLI 子命令
- `preflight` 元数据预检：一次报告 consent 草案、真实目录形态、12—15 条场景、输入绑定、停止正例和质量分母的全部阻塞项；不读来源正文、不签发授权、不产生写入

## 2. 状态语义

`infrastructure_only`：只证明合成评测链路能运行，不代表真实产品质量

`passed` / `failed`：只有以下条件同时满足才能产生：

1. dataset 是经明确授权的 `real_vault_snapshot`
2. execution mode 是已固定 Provider / Model / Prompt / Policy / 预算的 `provider_shadow`
3. plan 保存结构化 consent ID 与 SHA-256，阈值和 Provider 配置与 consent 完全一致
4. plan 精确绑定一份 snapshot-bound `shadow-case-set-v1`
5. 预测来自通过 `ShadowProducer` 边界生成的 `shadow-work-product-v1`
6. work product 中每案都记录真实 Provider attempt 与 usage
7. 完整 candidate ProjectionBundle 存在，其 hash 与 work product 一致
8. baseline path 存在于同一只读快照，且 hash 一致
9. 所有预注册指标都有分母并完成评估

任一指标没有分母时状态为 `not_evaluated`，整体质量门不会通过

## 3. 本次复核发现与修复

1. **缺少候选也可进入真实质量终态**：现在必须提供完整且通过合同的 ProjectionBundle
2. **基线 hash 可以任意传入**：现在要求 baseline path 与 hash 成对出现，并精确命中快照 manifest 内的文件
3. **评分样本没有作为证据封存**：现在写入规范化 `observations.json`，其 SHA-256 进入 run identity 和 report
4. **已完成 run 保持可写**：现在发布前密封子文件，原子 rename 后密封根目录；重新读取会拒绝可写文件或被改动的指标
5. **重复读取只检查 Schema**：现在会重算 observation metrics / gates，核对 plan、snapshot ref、candidate pointer 与 Bundle hash
6. **CLI 候选路径在合同验证前被读取**：现在先验证 manifest，禁止 `..`，再检查每个 projection 解析后仍位于 Bundle root
7. **NaN / Infinity 和未绑定 Provider attempt 的 usage**：现在禁止非有限数字，token / cost / latency 必须属于明确的 Provider attempt
8. **自由文本确认无法证明用户接受过哪些配置**：现在必须生成 `shadow-consent-v1`，并把六组确认项封入确定性身份
9. **同一语义因列表顺序生成不同身份**：文件后缀、Prompt 版本和 Policy 版本在生成 consent 与 plan 时统一排序
10. **source label 无法精确约束真实目录**：consent 保存规范绝对路径，snapshot manifest 保存 `source_root_sha256`，创建与运行阶段都会核对
11. **确认、快照、计划和完成时间可能倒置**：现在强制 `confirmed_at ≤ snapshot.created_at ≤ plan.created_at ≤ finished_at`
12. **模板占位值可能被误封为正式授权**：生成 consent 时拒绝仍含“请填写”的来源、Provider 或模型字段
13. **标准答案和预测由同一份手工 observation 提供**：新增 case set / work product 双合同，生产者执行期间无法读取 gold
14. **手工 Provider usage 可让真实 run 进入通过状态**：真实 `passed` / `failed` 现在强制要求已绑定的 case set 和 work product
15. **worker 可能读取案例外文件**：执行器仅打开 case set 的 exact path，传入不可变 bytes mapping，再次校验大小和 SHA-256
16. **预算只在全部生成完后检查**：每个 case 完成后立即累加 prompt / completion / cost，并检查单次 latency
17. **Provider cost 的 NaN 可绕过大小比较**：`ProviderUsage` 统一拒绝非有限 cost
18. **真实运行配置只能逐步试错**：新增 `preflight`，在签发 consent 前完成配置、路径和场景集的无副作用检查
19. **英文占位符可能被误封为授权**：consent 与预检同时拒绝 `TODO`、`TBD`、`placeholder`、`replace-me` 等常见未填写值

## 4. 安全与写入边界

- 产品模型调用：0
- 真实 Vault 读取：0
- 真实 Vault 写入：0
- 正式 revision 写入：0
- 前端修改：0
- 本轮写入范围：`backend/` 与 `docs/backend-design/`
- 影子候选与报告：只允许位于创建快照的隔离 workspace

## 5. 合成测试覆盖

- 来源字节与 mode 在快照和影子 run 前后一致
- 真实快照授权引用、symlink、特殊文件和输出路径重叠边界
- 拷贝期间来源改动会中止发布并清理 staging
- 快照文件、manifest、权限与 hash 篡改检测
- 误连、漏连、过度推断和停止质量的独立计算
- 缺少分母、不可能计数、重复 case ID、非法集合和非有限 cost 拒绝
- 确定性 0 模型模式零 Provider usage
- 真实 plan 待确认时禁止运行
- case set 的输入路径、hash、排序、唯一性和 snapshot binding
- producer 输入不包含 gold，且只含预登记文件
- work product 覆盖全部 case，预测计数不能超过分母
- work product 的 plan / case set / snapshot / candidate / Provider 配置精确绑定
- 逐案预算超限会立即停止，非有限 cost 会拒绝
- 手工 observation 无法产生真实质量终态
- 自由文本确认无法替代结构化 consent
- consent 篡改、阈值偏移、Provider 偏移、来源路径偏移与时间倒置全部拒绝
- consent 的文件范围和版本列表具有规范化确定性身份
- preflight 不读来源正文且不创建文件，能拒绝路径不一致、symlink、11/16 条场景、重复 ID、路径穿越、缺失输入、零质量分母、占位 Provider 与预算偏移
- CLI 能从 reviewed draft 生成正式 consent，并将它绑定到临时真实数据合同 fixture 的只读快照
- CLI 能冻结 case set 并把其 ID + hash 绑入预注册 plan
- 确认后的真实终态仍必须具有 candidate 和 snapshot-bound baseline
- plan / snapshot 精确绑定与隔离 output root
- 已封存 run 的权限篡改、指标篡改与幂等重试
- ProjectionBundle parent path segment 拒绝

## 6. 完成口径

- 文件已创建：是
- 合同测试通过：是
- 后端回归通过：是
- Python 3.9 兼容检查：是
- 现有前端回归：是
- 合成影子基础设施：通过
- 合成 worker 端到端演练：通过
- 用户质量阈值确认：未进行
- 结构化确认合同与用户确认单：已完成
- 真实运行无副作用预检能力：已完成
- 真实 Vault 快照：未创建
- 产品 Provider 评测：未运行
- 真实质量影子 run：未运行
- 正式写入：未启用

R9 尚不满足关闭条件，R10 不能开始

## 7. 验证证据

```text
cd backend
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider
204 passed

/opt/anaconda3/bin/mypy --cache-dir=/tmp/memento-backend-mypy-preflight-final src tests eval/run_shadow.py
Success: no issues found in 123 source files

/opt/anaconda3/bin/python -c '使用 ast.parse(feature_version=(3, 9)) 检查 src、tests 与 eval/run_shadow.py'
Python 3.9 AST parse passed: 123 files

/opt/anaconda3/bin/python -c '使用 Draft202012Validator.check_schema 检查全部 Schema'
JSON Schema self-check passed: 44 schemas

PYTHONPATH=src /opt/anaconda3/bin/python eval/run_shadow.py --help
passed

node tests/test_cognitive_home_library.js
cognitive-home-library contract tests passed

node tests/test_cognitive_demo_fixture.js
cognitive demo fixture tests passed

git diff --check
passed
```

工作树中后端范围外的修改均为本轮开始前已有的用户改动，本轮没有改写这些文件
## 8. Provider worker binding

`evaluation/provider_shadow_binding.py` 已提供真实 worker 的 fail-closed 注入边界。它在零模型调用状态下验证 plan 与 structured consent 的精确绑定，并在每个 case 返回后校验 usage 的 mode、Provider 和 model。真实 worker 仍由已选 Provider 与当前 Agent / Workflow 图实现，原因是只有该图能生成真实 candidate ProjectionBundle。通用 Prompt wrapper 会脱离产品行为，不能作为真实质量证据
