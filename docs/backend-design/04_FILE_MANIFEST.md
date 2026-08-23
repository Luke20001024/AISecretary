# 04 · 后端文件清单

## 1. 实现目录

后端 V2 建议在仓库根目录新增独立包：

```text
backend/
├── pyproject.toml
├── README.md
├── src/memento_backend/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── domain/
│   │   ├── refs.py
│   │   ├── records.py
│   │   ├── capture_decisions.py
│   │   ├── resources.py
│   │   ├── read_later.py
│   │   ├── interpretations.py
│   │   ├── memory_atoms.py
│   │   ├── relations.py
│   │   ├── themes.py
│   │   ├── self_insights.py
│   │   ├── external_context.py
│   │   ├── projection_bundles.py
│   │   └── actions.py
│   ├── agents/
│   │   ├── protocol.py
│   │   ├── capture_understanding_agent.py
│   │   ├── record_interpreter.py
│   │   ├── daily_integrator.py
│   │   ├── theme_synthesizer.py
│   │   ├── self_understanding_agent.py
│   │   ├── context_router.py
│   │   └── resource_reader.py
│   ├── prompts/
│   │   ├── capture_understanding_v1.py
│   │   ├── record_interpreter_v1.py
│   │   ├── daily_integrator_v1.py
│   │   ├── theme_synthesizer_v1.py
│   │   └── self_understanding_v1.py
│   ├── policies/
│   │   ├── evidence_policy.py
│   │   ├── sensitivity_policy.py
│   │   ├── material_gate.py
│   │   ├── budget_policy.py
│   │   └── authorization_policy.py
│   ├── workflows/
│   │   ├── ingest_record.py
│   │   ├── route_capture.py
│   │   ├── interpret_record.py
│   │   ├── consolidate_day.py
│   │   ├── update_themes.py
│   │   ├── update_self_understanding.py
│   │   ├── process_external_trace.py
│   │   ├── read_resource.py
│   │   └── rebuild_projections.py
│   ├── stores/
│   │   ├── atomic_files.py
│   │   ├── source_store.py
│   │   ├── revision_store.py
│   │   ├── head_index.py
│   │   ├── projection_store.py
│   │   ├── action_store.py
│   │   ├── external_store.py
│   │   └── search_index.py
│   ├── providers/
│   │   ├── protocol.py
│   │   ├── deepseek_provider.py
│   │   ├── provider_router.py
│   │   └── usage_ledger.py
│   ├── projections/
│   │   ├── common.py
│   │   ├── bundle_projector.py
│   │   ├── bundle_publisher.py
│   │   ├── home_projector.py
│   │   ├── timeline_projector.py
│   │   ├── landscape_projector.py
│   │   ├── self_projector.py
│   │   ├── detail_projector.py
│   │   └── context_pack_projector.py
│   ├── interfaces/
│   │   ├── read_api.py
│   │   ├── action_api.py
│   │   ├── inbox_worker.py
│   │   ├── scheduler.py
│   │   ├── mcp_server.py
│   │   ├── v2_to_v1_projection_adapter.py
│   │   └── v1_projection_adapter.py
│   ├── migrations/
│   │   ├── inventory_v1.py
│   │   └── migrate_v1_to_v2.py
│   └── schemas/
│       ├── source-record-v2.schema.json
│       ├── capture-decision-v1.schema.json
│       ├── resource-card-v1.schema.json
│       ├── read-later-intent-v1.schema.json
│       ├── agent-action-candidate-v1.schema.json
│       ├── record-interpretation-v2.schema.json
│       ├── memory-atom-v2.schema.json
│       ├── relation-v2.schema.json
│       ├── theme-v2.schema.json
│       ├── self-insight-v2.schema.json
│       ├── context-grant-v1.schema.json
│       ├── external-session-v1.schema.json
│       ├── context-pack-v1.schema.json
│       ├── context-read-audit-v1.schema.json
│       ├── external-trace-v1.schema.json
│       ├── projection-bundle-v1.schema.json
│       ├── home-projection-v2.schema.json
│       ├── timeline-projection-v1.schema.json
│       ├── landscape-projection-v2.schema.json
│       ├── self-projection-v1.schema.json
│       ├── detail-index-projection-v1.schema.json
│       ├── record-detail-projection-v1.schema.json
│       ├── resource-detail-projection-v1.schema.json
│       ├── theme-detail-projection-v1.schema.json
│       ├── self-insight-detail-projection-v1.schema.json
│       ├── external-session-projection-v1.schema.json
│       ├── run-status-projection-v1.schema.json
│       ├── run-request-v1.schema.json
│       └── run-result-v1.schema.json
├── tests/
│   ├── unit/
│   ├── contracts/
│   ├── workflows/
│   ├── agents/
│   ├── projections/
│   ├── mcp/
│   ├── migrations/
│   └── fixtures/
└── eval/
    ├── cases/
    ├── scenarios/
    ├── run_offline.py
    ├── run_shadow.py
    └── reports/
```

## 2. 第一阶段参考文件与实际落地映射

下列清单是编码开始前的参考拆分，用来确认职责覆盖，不要求每个建议文件都一一保留。实际实现按可复核边界合并了纯数据模块，并把 `stores/` 统一命名为 `storage/`

```text
backend/pyproject.toml
backend/README.md
backend/src/memento_backend/domain/refs.py
backend/src/memento_backend/domain/records.py
backend/src/memento_backend/domain/capture_decisions.py
backend/src/memento_backend/domain/resources.py
backend/src/memento_backend/domain/read_later.py
backend/src/memento_backend/domain/memory_atoms.py
backend/src/memento_backend/domain/themes.py
backend/src/memento_backend/domain/self_insights.py
backend/src/memento_backend/stores/atomic_files.py
backend/src/memento_backend/stores/revision_store.py
backend/src/memento_backend/agents/protocol.py
backend/src/memento_backend/agents/capture_understanding_agent.py
backend/src/memento_backend/providers/protocol.py
backend/src/memento_backend/projections/landscape_projector.py
backend/src/memento_backend/projections/self_projector.py
backend/src/memento_backend/projections/bundle_publisher.py
backend/src/memento_backend/interfaces/read_api.py
backend/src/memento_backend/interfaces/action_api.py
backend/src/memento_backend/interfaces/v1_projection_adapter.py
backend/src/memento_backend/schemas/source-record-v2.schema.json
backend/src/memento_backend/schemas/capture-decision-v1.schema.json
backend/src/memento_backend/schemas/theme-v2.schema.json
backend/src/memento_backend/schemas/resource-card-v1.schema.json
backend/src/memento_backend/schemas/read-later-intent-v1.schema.json
backend/src/memento_backend/schemas/agent-action-candidate-v1.schema.json
backend/src/memento_backend/schemas/record-interpretation-v2.schema.json
backend/src/memento_backend/schemas/memory-atom-v2.schema.json
backend/src/memento_backend/schemas/relation-v2.schema.json
backend/src/memento_backend/schemas/self-insight-v2.schema.json
backend/src/memento_backend/schemas/context-grant-v1.schema.json
backend/src/memento_backend/schemas/external-session-v1.schema.json
backend/src/memento_backend/schemas/context-pack-v1.schema.json
backend/src/memento_backend/schemas/context-read-audit-v1.schema.json
backend/src/memento_backend/schemas/external-trace-v1.schema.json
backend/src/memento_backend/schemas/projection-bundle-v1.schema.json
backend/src/memento_backend/schemas/home-projection-v2.schema.json
backend/src/memento_backend/schemas/timeline-projection-v1.schema.json
backend/src/memento_backend/schemas/landscape-projection-v2.schema.json
backend/src/memento_backend/schemas/self-projection-v1.schema.json
backend/src/memento_backend/schemas/detail-index-projection-v1.schema.json
backend/src/memento_backend/schemas/record-detail-projection-v1.schema.json
backend/src/memento_backend/schemas/resource-detail-projection-v1.schema.json
backend/src/memento_backend/schemas/theme-detail-projection-v1.schema.json
backend/src/memento_backend/schemas/self-insight-detail-projection-v1.schema.json
backend/tests/contracts/test_theme_contract.py
backend/tests/contracts/test_capture_decision_contract.py
backend/tests/contracts/test_resource_card_contract.py
backend/tests/contracts/test_self_insight_contract.py
backend/tests/contracts/test_external_context_contract.py
backend/tests/projections/test_landscape_projector.py
backend/tests/projections/test_self_projector.py
backend/tests/projections/test_v1_projection_adapter.py
backend/tests/projections/test_projection_bundle.py
backend/tests/contracts/test_read_api_contract.py
backend/tests/contracts/test_action_api_contract.py
```

这一阶段不调用模型，先冻结对象、revision、投影和兼容合同

完成实现后的等价映射如下：

| 早期建议路径 | 当前权威实现 | 原因 |
|---|---|---|
| `domain/records.py`、`capture_decisions.py`、`resources.py`、`read_later.py` | 对应 JSON Schema + `domain/refs.py` + `domain/revisions.py` | 正式对象保持 JSON 合同，避免再维护一套可漂移的数据类 |
| `domain/memory_atoms.py`、`themes.py`、`self_insights.py` | 对应 JSON Schema + Workflow material gate | 长期对象的提交权限由 Workflow 校验，Schema 冻结字段 |
| `stores/atomic_files.py`、`stores/revision_store.py` | `storage/atomic.py`、`storage/revision_store.py` | 全部持久化实现统一进入 `storage/` 包 |
| `projections/bundle_publisher.py` | `storage/bundle_store.py` | bundle staging、publication、current pointer 与 recovery 必须在同一原子边界 |
| `interfaces/read_api.py` | `interfaces/read_api.py` + `storage/bundle_store.py` | 本地稳定读取 façade 已落地；网络 transport 和前端接线留在 B7 |
| `interfaces/action_api.py` | `interfaces/action_api.py` + `storage/action_inbox.py` + `storage/run_request_inbox.py` | action、terminal result 与 run request 已有 append-only 权威入口 |
| `test_theme_contract.py`、`test_self_insight_contract.py` | `test_cognitive_object_contracts.py` + R6 / R7 Workflow 测试 | 合同反例与 material gate 分层验证 |
| `test_external_context_contract.py` | `test_external_context_contracts.py` | 文件名采用复数，覆盖五类外部 Context 合同 |
| `test_read_api_contract.py` | `tests/contracts/test_read_api_contract.py` | 验证同 manifest 读取、四类 Detail、session / run status、找不到时 fail-closed 与返回值隔离 |
| `test_action_api_contract.py` | `tests/contracts/test_action_api_contract.py` | 验证 append-only action、terminal result、陈旧 watermark、run request 与 result hash 绑定 |

因此，早期清单中的路径不能单独用于判定缺文件。当前交付范围以本节映射、2.1—2.6 的实际文件清单、阶段报告和通过的合同测试共同为准

## 2.1 R4 已落地的独立存储包

实现采用 `storage/` 作为当前包名，对应上方规划中的 `stores/`：

```text
backend/src/memento_backend/storage/__init__.py
backend/src/memento_backend/storage/atomic.py
backend/src/memento_backend/storage/head_index.py
backend/src/memento_backend/storage/revision_store.py
backend/src/memento_backend/storage/action_inbox.py
backend/src/memento_backend/storage/bundle_store.py
backend/src/memento_backend/storage/run_ledger.py

backend/src/memento_backend/schemas/formal-head-index-v1.schema.json
backend/src/memento_backend/schemas/revision-transaction-v1.schema.json
backend/src/memento_backend/schemas/user-action-v1.schema.json
backend/src/memento_backend/schemas/action-result-v1.schema.json
backend/src/memento_backend/schemas/action-watermark-v1.schema.json
backend/src/memento_backend/schemas/projection-publication-v1.schema.json
backend/src/memento_backend/schemas/projection-current-v1.schema.json
backend/src/memento_backend/schemas/agent-run-v1.schema.json
backend/src/memento_backend/schemas/resource-read-result-v1.schema.json

backend/tests/storage/test_atomic_file_store.py
backend/tests/storage/test_revision_store.py
backend/tests/storage/test_action_inbox.py
backend/tests/storage/test_bundle_store.py
backend/reports/R4_STORES.md
```

R4 的 `storage/` 不依赖 V1 Store，也不写入 `~/AISecretary`。后续 Workflow 通过这些窄接口提交正式对象和发布 Projection

## 2.2 R5 已落地的入口、理解与按需阅读包

```text
backend/src/memento_backend/agents/protocol.py
backend/src/memento_backend/agents/capture_understanding_agent.py
backend/src/memento_backend/agents/record_interpreter.py
backend/src/memento_backend/agents/resource_reader.py
backend/src/memento_backend/providers/protocol.py
backend/src/memento_backend/policies/capture_policy.py
backend/src/memento_backend/policies/interpretation_policy.py
backend/src/memento_backend/policies/resource_policy.py
backend/src/memento_backend/workflows/route_capture.py
backend/src/memento_backend/workflows/interpret_record.py
backend/src/memento_backend/workflows/read_resource.py
backend/src/memento_backend/workflows/ingest_record.py
backend/src/memento_backend/interfaces/v1_source_adapter.py
backend/eval/scenarios/manifest.json
backend/tests/agents/test_capture_understanding_agent.py
backend/tests/agents/test_record_interpreter.py
backend/tests/agents/test_resource_reader.py
backend/tests/workflows/test_route_capture.py
backend/tests/workflows/test_interpret_record.py
backend/tests/workflows/test_ingest_v1_record.py
backend/tests/eval/test_r5_scenario_manifest.py
backend/reports/R5_CAPTURE_AND_INTERPRETATION.md
```

R5 的场景集当前全部是脱敏合成 fixture。真实用户样本、产品 Provider 选择与 shadow Vault 运行保持关闭

## 2.3 R6 已落地的日级整理与长期主题包

```text
backend/src/memento_backend/agents/daily_integrator.py
backend/src/memento_backend/agents/theme_synthesizer.py
backend/src/memento_backend/policies/memory_policy.py
backend/src/memento_backend/workflows/consolidate_day.py
backend/src/memento_backend/workflows/update_theme.py
backend/src/memento_backend/schemas/daily-integration-candidate-v1.schema.json
backend/tests/workflows/test_daily_and_theme_workflows.py
backend/tests/projections/test_terrain_growth_replay.py
backend/reports/R6_MEMORY_AND_THEMES.md
```

R6 使用确定性 0 模型 Agent 实现合同与 Workflow 基线。DailyIntegrationCandidate 只作为单次原子提交的临时包；正式 revision 仍为 MemoryAtom、Relation 与 Theme。20 天回放直接读取四个时间点的 ProjectionBundle，不额外保存动画语义

## 2.4 R7 已落地的第三层理解包

```text
backend/src/memento_backend/agents/self_understanding_agent.py
backend/src/memento_backend/policies/self_policy.py
backend/src/memento_backend/workflows/update_self_understanding.py
backend/src/memento_backend/workflows/apply_self_action.py
backend/src/memento_backend/storage/revision_store.py
backend/src/memento_backend/schemas/self-insight-v2.schema.json
backend/src/memento_backend/schemas/self-projection-v1.schema.json
backend/src/memento_backend/schemas/self-insight-detail-projection-v1.schema.json
backend/tests/workflows/test_self_understanding_workflows.py
backend/tests/projections/test_self_projector.py
backend/reports/R7_SELF_UNDERSTANDING.md
```

R7 使用确定性 0 模型基线验证跨 Theme material gate、敏感停止、第三层创建与修订、用户确认优先、撤回不可复活以及 SelfInsight → Theme → MemoryAtom → Source 回溯。真实 Provider、真实 Vault 与外部 Context 读取仍保持关闭

## 2.5 R8 已落地的双向 Context 包

```text
backend/src/memento_backend/agents/context_router.py
backend/src/memento_backend/policies/context_policy.py
backend/src/memento_backend/storage/external_context_store.py
backend/src/memento_backend/workflows/manage_context_grant.py
backend/src/memento_backend/workflows/open_external_session.py
backend/src/memento_backend/workflows/context_audit.py
backend/src/memento_backend/workflows/create_context_pack.py
backend/src/memento_backend/workflows/append_external_trace.py
backend/src/memento_backend/interfaces/context_tools.py
backend/src/memento_backend/interfaces/mcp_server.py
backend/src/memento_backend/schemas/context-grant-v1.schema.json
backend/src/memento_backend/schemas/external-session-v1.schema.json
backend/src/memento_backend/schemas/context-pack-v1.schema.json
backend/src/memento_backend/schemas/context-read-audit-v1.schema.json
backend/src/memento_backend/schemas/external-trace-v1.schema.json
backend/tests/contracts/test_external_context_contracts.py
backend/tests/workflows/test_external_context_workflows.py
backend/reports/R8_EXTERNAL_CONTEXT.md
```

R8 暴露八个本地 allow-list 工具。读取端只返回授权范围内的短期 Context Pack；写回端只追加 ExternalTrace、对应 SourceRecord 与审计，随后回到 L0。实现不包含网络 transport、产品模型和真实 Vault 默认路径

## 2.6 R9 已落地的只读影子评测基础设施

```text
backend/src/memento_backend/evaluation/__init__.py
backend/src/memento_backend/evaluation/shadow_consent.py
backend/src/memento_backend/evaluation/shadow_preflight.py
backend/src/memento_backend/evaluation/shadow_snapshot.py
backend/src/memento_backend/evaluation/shadow_metrics.py
backend/src/memento_backend/evaluation/shadow_runner.py
backend/src/memento_backend/evaluation/shadow_worker.py
backend/src/memento_backend/schemas/shadow-consent-v1.schema.json
backend/src/memento_backend/schemas/shadow-snapshot-v1.schema.json
backend/src/memento_backend/schemas/shadow-plan-v1.schema.json
backend/src/memento_backend/schemas/shadow-report-v1.schema.json
backend/src/memento_backend/schemas/shadow-case-set-v1.schema.json
backend/src/memento_backend/schemas/shadow-work-product-v1.schema.json
backend/eval/consent-template.json
backend/eval/preregistration-template.json
backend/eval/observations-template.json
backend/eval/case-set-template.json
backend/eval/run_shadow.py
backend/tests/eval/test_shadow_consent.py
backend/tests/eval/test_shadow_preflight.py
backend/tests/eval/test_shadow_snapshot.py
backend/tests/eval/test_shadow_metrics.py
backend/tests/eval/test_shadow_runner.py
backend/tests/eval/test_shadow_worker.py
backend/reports/R9_SHADOW_INFRASTRUCTURE.md
docs/backend-design/09_R9_USER_CONFIRMATION.md
```

R9 CLI 不包含 Provider 调用。`preflight` 在签发授权前只检查配置、目录元数据和 12—15 条场景，明确保持 `authorization_issued=false`，不读来源正文且不产生文件。正式 `shadow-consent-v1` 将用户确认的数据范围、阈值、Gate、敏感策略、Agent 频率、Provider 与预算绑定到 snapshot、plan 和 sealed report。`shadow-case-set-v1` 将标准答案与快照文件精确绑定；`ShadowProducer` 只能收到无标准答案的 bounded input；`shadow-work-product-v1` 保存预测、usage 与候选 Bundle hash。评估器独立合并 case set 和 work product 后计算指标并封存报告。真实质量状态还要求二者、候选 Bundle、快照内基线文件、真实 Provider 尝试和结构化用户确认同时存在

## 2.7 已落地的合码 façade 与 run request 合同

```text
backend/src/memento_backend/interfaces/read_api.py
backend/src/memento_backend/interfaces/action_api.py
backend/src/memento_backend/storage/run_request_inbox.py
backend/src/memento_backend/schemas/run-request-v1.schema.json
backend/src/memento_backend/schemas/run-result-v1.schema.json
backend/src/memento_backend/schemas/run-status-projection-v1.schema.json
backend/tests/contracts/test_read_api_contract.py
backend/tests/contracts/test_action_api_contract.py
```

这一层只提供本地 Python 合同，不包含网络 transport、前端 data source、模型 worker 或真实 Vault 路径。`ProjectionReadApi` 每次从单一 current bundle 读取；`ActionApi` 只向 append-only inbox 提交；`RunRequestInbox` 用 request hash 和 action watermark 绑定运行结果

## 3. 现有文件复用表

| 现有文件 | 决定 | 复用内容 |
|---|---|---|
| `context-agent/cognitive_v1.py` | adapter 复用 | ID、ObjectRef、SourceSpan、持久化 hash |
| `context-agent/cognitive_store_v1.py` | adapter 复用 | Markdown 解析、稳定 record identity |
| `context-agent/cognitive_runtime_v1.py` | 提取 | bounded action loop、run、budget、attempt marker |
| `context-agent/cognitive_bundle_store_v1.py` | 提取 | staging、事务、revision 和 head index |
| `context-agent/cognitive_actions_v1.py` | 提取 | append-only action、CAS、tombstone |
| `context-agent/cognitive_projection_v1.py` | 参考后重写 | 稳定布局和 0 模型投影 |
| `context-agent/deepseek_provider.py` | 包装复用 | Provider 调用、错误收口和 usage |
| `context-agent/cognitive_schedule_v1.py` | 包装复用 | 本地计划、补发和按日互斥 |
| `context-agent/agent_v1.py` | 兼容保留 | 旧 understanding revision 与迁移来源 |
| `chrome-newtab/cognitive-home-library.js` | 冻结为 V1 合同 | 合码前的前端兼容目标 |
| `chrome-newtab/cognitive-demo-fixture.js` | 固定测试 fixture | themes、portrait 与 20 天场景 |

## 4. 现有文件暂不修改

后端独立开发阶段保持以下文件不动：

- `docs/index.html`
- `docs/assets/product/`
- `chrome-newtab/dashboard.html`
- `chrome-newtab/dashboard.css`
- `chrome-newtab/dashboard.js`
- `docs/Memento-Cognitive-Home-Standalone.html`

需要合码时，只通过新 Projection adapter 和 feature flag 接入

## 5. 合码阶段才新增的前端文件

```text
chrome-newtab/cognitive-v2-contract.js
chrome-newtab/cognitive-v2-data-source.js
chrome-newtab/cognitive-v2-actions.js
tests/test_cognitive_v2_contract.js
tests/test_cognitive_v2_integration.js
```

已有视觉组件继续使用，避免重新制作地形、抽屉和第三层理解区域

## 6. 文档权威关系

- `docs/MEMENTO_PRODUCT_FINAL_STATE.md` 管产品终态
- 本设计包管后端 V2 的目标结构和开发顺序
- `docs/cognitive-secretary-mvp/` 保留 V1 已实现合同和发布证据
- `docs/REMEMBER_AGENT_V1_TECHNICAL_DESIGN.md` 保留旧长期 Agent 的实现和评测证据
- 实现后由 `backend/README.md` 记录真实可用命令和完成状态
