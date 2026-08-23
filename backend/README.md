# Memento Backend V2

Memento Backend V2 是与现有产品前端分离开发的本地认知后端

当前状态：`R9 影子评测基础设施已通过合成合同测试；真实 Vault、产品 Provider 与真实质量门仍未运行`

## 权威设计

- `docs/backend-design/00_BACKEND_MASTER_PLAN.md`
- `docs/backend-design/03_DATA_AND_INTERFACES.md`
- `docs/backend-design/04_FILE_MANIFEST.md`
- `docs/backend-design/08_AI_EXECUTION_PLAN.md`

对象字段、Agent 权限或前端展示发生冲突时，先更新总合同和对应 JSON Schema，再进入实现

## 安全边界

- 后端独立开发期间不修改现有产品前端
- 原文、模型候选、正式对象和 Projection 分层保存
- Agent 只产生 candidate action，Workflow 校验后提交正式 revision
- 未通过影子运行和用户确认前，不写真实 Vault
- 所有 Store 只接受调用方显式传入的隔离目录，没有默认真实 Vault 路径
- 外部 AI 读取 Context 必须经过授权并产生审计记录
- 所有 Workflow 强制接入 append-only `RunLedger`，保存 Prompt、Policy、输入快照 hash、candidate hash、usage 与终态
- Resource Reader 只返回带可复核引用的临时结果，不把资料内容直接提交为个人理解
- Daily Integrator 保留原文、历史证据与当日新增解释，原子提交 MemoryAtom 与 Relation
- Theme Synthesizer 只有在跨日期 material gate 通过后才提交 Theme revision
- 地形生长由不同 `as_of` 的 ProjectionBundle 回放形成，不写回或改动认知语义
- Self Understanding Agent 只有在至少两个长期 Theme 共同支持时才提出 SelfInsight candidate
- 敏感推断停止自动提交，敏感迁移对象在用户动作后仍保持 restricted
- 用户对 SelfInsight 的确认、限定、修订和撤回优先于旧 Agent run
- RevisionStore 只从已发布 head 链读取历史 revision，未发布文件不会进入历史查询
- Context Pack 只返回用户确认且显式授权的最小 Context，最长存活五分钟
- Grant、Session、任务、主题与对象 revision 精确绑定，撤销后旧会话立即停止后续读取
- 外部写回只追加 ExternalTrace、SourceRecord 与审计，再回到 L0，不提供 Theme 或 SelfInsight 修改接口
- R9 快照只以读方式打开来源，发布前后都复核原始 hash，候选与报告只进入隔离 shadow workspace
- 真实质量状态同时要求用户确认、预登记场景集、独立 worker 产物、真实 Provider 尝试、候选 ProjectionBundle 与快照内基线文件
- 用户确认采用带确定性 ID 与 hash 的 `shadow-consent-v1`，精确绑定数据范围、质量阈值、Gate、敏感策略、Agent 频率、Provider 与预算
- 每次影子 run 保存场景集、worker 产物、规范化 observation、计划、快照引用与报告，发布后整体封存为只读

## 本地运行

使用当前环境中已安装 pytest 的 Python：

```bash
cd backend
python -m pytest
```

类型检查：

```bash
cd backend
python -m mypy src tests
```

系统 `/usr/bin/python3` 当前为 Python 3.9.6，且没有安装 pytest。后端最低兼容版本暂定为 Python 3.9

## 真实快捷键测试集旁路

测试样本直接来自当前已经安装的 macOS Services。用户继续使用 `⌃1–⌃5` 完成划字、备注、标签、截图和语音，后端只读收集会话开始后的新增完整记录，并把附件复制到隔离测试集

```bash
cd backend
PYTHONPATH=src python eval/collect_existing_captures.py \
  --workspace .capture-dataset \
  start --source "$HOME/AISecretary" --source-label "example AISecretary"

# 使用现有快捷键完成几条真实记录后
PYTHONPATH=src python eval/collect_existing_captures.py \
  --workspace .capture-dataset collect

PYTHONPATH=src python eval/collect_existing_captures.py \
  --workspace .capture-dataset export
```

收集器不修改产品界面、快捷键、Service 或安装脚本，不调用 Provider，也不写正式 Vault。导出的 `expected` 字段保持空白，等待用户确认后才能进入影子评测 case set

真实样本监看页只展示这条旁路已经收取的内容和待补清单，不提供新的采集控件：

```bash
PYTHONPATH=src python eval/run_capture_monitor.py \
  --workspace .capture-dataset --port 4317
```

打开 `http://127.0.0.1:4317/`。页面每 5 秒收取一次由现有快捷键写入的新记录

完整流程与边界见 `reports/REAL_CAPTURE_DATASET_BRIDGE.md`

## R9 影子评测工具

`backend/eval/run_shadow.py` 先预检真实运行配置，再创建快照、冻结场景集、预注册计划并封存报告。CLI 不调用 Provider。产品调用通过 `ShadowProducer` 协议注入，worker 只能看到场景声明的输入文件和检查分母

```bash
cd backend
PYTHONPATH=src python eval/run_shadow.py --help
```

- `preflight`：只检查授权草案、来源目录元数据和 12—15 条场景，不读来源正文、不签发授权、不创建文件
- `consent`：在用户明确确认后，把审阅配置封成带确定性 ID 与 hash 的正式授权合同
- `snapshot`：创建密封的只读快照，真实 Vault 必须绑定正式 consent
- `case-set`：把标准答案与精确输入范围绑定到一个只读快照
- `plan`：冻结场景集、Provider / Model、Prompt / Policy 版本、质量阈值、预算与 consent hash
- `evaluate`：独立合并场景标准答案和 worker 产物，计算误连、漏连、过度推断、停止质量、证据有效性、成本与延迟，并封存影子证据

真实 Provider worker 通过 `bind_provider_shadow_producer(plan, consent, producer)` 接入。它不携带凭据、不读快照，也不生成通用 Prompt；它只把产品实际的 `ShadowProducer` 绑定到已确认的 Provider、模型、Prompt / Policy 版本和预算。每条 Provider usage 若与已封存的计划不一致，会在指标汇总前停止运行

输入格式参考 `eval/consent-template.json`、`eval/preregistration-template.json`、`eval/case-set-template.json` 和 `eval/observations-template.json`。`observations-template.json` 仅保留给合成基础设施检查；真实质量终态必须来自 case set 与 work product 的独立合并。确认项与操作边界见 `docs/backend-design/09_R9_USER_CONFIRMATION.md`。模板中的阈值仍是待用户确认的草案，不代表已经通过真实质量门

B0—B7 的逐项证据、当前合码边界与唯一剩余授权包见 `reports/BACKEND_MERGE_READINESS.md`

## 稳定本地合码入口

`ProjectionReadApi` 已覆盖 manifest、Home、Timeline、Landscape、Self、四类 Detail、ExternalSession 与 run status。所有 Projection 读取都从 `BundleStore.load_current()` 开始，不存在部分 bundle 读取路径

`ActionApi` 已覆盖 action 提交、terminal result 查询与 run request。`RunRequestInbox` 保存不可变 request / result，并将请求绑定到提交时的 action watermark

当前形态是本地 Python façade。网络 transport、前端 feature flag 和 data-source 接线继续归 B7，在 B6 真实影子质量门关通过前保持关闭

## 状态口径

每个阶段分别报告：

- 文件已创建
- 合同测试通过
- 回归测试通过
- 模型评测通过
- 影子运行通过
- 真实写入启用

只有对应证据存在时才更新状态
