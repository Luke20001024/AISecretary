# Memento 认知秘书 MVP 数据合同

> 状态：v0.9.0 规范性合同
>
> 日期：2026-08-18
>
> 产品范围：[PRD](PRD.md) · 执行架构：[技术设计](TECHNICAL_DESIGN.md) · 操作与验收：[使用说明](USER_GUIDE.md) / [发布清单](RELEASE_CHECKLIST.md)
>
> 说明：本文件定义 MVP 的正式对象、ID、revision、状态和引用规则。示例值仅用于说明格式，不代表真实用户数据。

## 1. 合同级别

本文使用以下术语：

- **MUST / 必须**：违反即拒绝读取或写入；
- **MUST NOT / 不得**：出现即视为非法对象；
- **SHOULD / 应**：除非记录明确原因，否则遵守；
- **Projection / 投影**：可覆盖、可删除、可从正式对象重建；
- **Candidate / 候选**：Agent 建议，不能作为正式历史或地景输入；
- **Formal / 正式**：通过确定性校验并由合法 commit manifest 或 Agent V1 Committer 提交。

除当前 Agent V1 已有合同外，本文定义的新增 JSON 对象使用 `schema_version: "1.0"`。实现必须将本文转换为机器可执行 JSON Schema，并补充 JSON Schema 无法表达的跨文件校验。

## 2. 通用编码与文件规则

### 2.1 JSON

- UTF-8，无 BOM；
- 顶层必须是 object；
- 未声明字段一律拒绝；
- 字符串不得含 NUL；
- 时间使用带时区 RFC 3339，例如 `2026-08-18T21:00:00+08:00`；
- 日期使用 `YYYY-MM-DD`；
- SHA-256 使用 64 位小写十六进制；
- 相对路径使用 `/`，不得包含 `..`，不得解析到 Vault 外；
- 正式 revision 文件不得覆盖；
- 不可变对象使用安全的单次创建；可覆盖的 checkpoint/head/projection 使用同目录临时文件、`fsync` 和原子 replace。两类写入都必须拒绝符号链接、非当前用户对象和越过 Vault 的路径。

### 2.2 ID

| 对象 | 格式 | 生成规则 |
|---|---|---|
| 原始记录 | `rec_<24 hex>` | 新采集由采集 nonce 分配并持久化一次；历史导入按版本化 locator digest 确定性生成 |
| 逐条请求 | `ireq_<24 hex>` | 由 record ref、feedback watermark、contract、trigger 与可选 nonce 确定性生成 |
| 逐条 run | `irun_<24 hex>` | 由 request ID 与 material run key 确定性生成 |
| 整理回执 | `rcp_<24 hex>` | 由 `record_id` 确定性派生，revision 表示变化 |
| 候选记忆 | `cmem_<24 hex>` | 在 receipt 内生成，只在该候选域有效 |
| 候选关系 | `crel_<24 hex>` | 在 receipt 或 daily run 内生成，只在该候选域有效 |
| 日级请求 | `dreq_<24 hex>` | 由本地日期、contract、trigger 与可选 nonce 确定性生成 |
| 日级 run | `drun_<24 hex>` | 由 request ID 与 material run key 确定性生成 |
| 日级 bundle | `db_<YYYYMMDD>` | 每个本地日期一个稳定 ID，以 revision 追加 |
| 日级摘要 | `dsum_<YYYYMMDD>` | 每个本地日期一个稳定 ID，以 revision 追加 |
| 可用记忆 | `rmem_<24 hex>` | 首次正式提交时生成，后续文案变化不换 ID |
| 正式关系 | `rel_<24 hex>` | 首次正式提交时生成，后续修订不换 ID |
| 新增用户 action | `cact_<24 hex>` | 每次用户提交生成全新 ID |
| action result | `cares_<24 hex>` | 由 action ID 确定性派生 |
| 主页手动归并请求 | `cman_<24 hex>` | 每次合法点击生成全新 ID |
| 主页手动归并结果 | `cmanr_<24 hex>` | `sha256("manual-result:" + request_sha256)` 的前 24 位 |
| 地景快照 | `lnd_<24 hex>` | 每次发布生成全新 ID |
| 地景峰 | `peak_<24 hex>` | 从 Agent V1 `memory_id` 的 24 hex 稳定派生 |
| Agent V1 request/run/memory/action | `arq_` / `arun_` / `mem_` / `uact_` + `24 hex` | 保持当前 Agent V1 合同 |

ID 只表达身份，不表达标题、主题、重要性或语义相似度。

### 2.3 Revision

- 可修改的正式领域对象使用 `revision: integer`，从 `1` 开始连续递增；
- 文件名使用 `<id>.rNNNNNN.json`；
- revision 文件不可覆盖，旧 revision 永久作为历史读取；
- revision 1 的 `previous_revision_sha256` 必须为 `null`；
- revision N 的 `previous_revision_sha256` 必须等于 revision N-1 文件的 SHA-256；
- 当前状态由合法连续链的最新 revision 派生；`formal-head-index.json`、`profile.json` 和 projection head 只保存经验证的可重建索引，不得取代 revision 链；
- 引用正式对象时必须携带 `id + revision + revision_sha256`；
- 修改提交必须携带 expected revision/hash，锁内复查不一致即 `stale` 或 `conflict`。

快照、request、terminal action result 本身不可变，不使用 revision。

## 3. 通用嵌套类型

### 3.1 `ObjectRef`

必须且只能包含：

```json
{
  "kind": "reusable_memory",
  "id": "rmem_111111111111111111111111",
  "revision": 2,
  "revision_sha256": "<64 hex>"
}
```

规则：

- `kind` 允许 `source_record / interpretation_receipt / daily_summary / reusable_memory / relation / understanding / daily_bundle`；
- `id` 前缀必须与 kind 对应；
- `revision >= 1`；
- `revision_sha256` 必须验证目标文件字节。

### 3.2 `SourceSpan`

必须且只能包含：

```json
{
  "record_id": "rec_111111111111111111111111",
  "record_revision": 1,
  "record_revision_sha256": "<64 hex>",
  "source_file": "2026-08-18.md",
  "line_start": 12,
  "line_end": 13,
  "quote": "用户实际留下的逐字片段",
  "quote_sha256": "<64 hex>"
}
```

规则：

- `source_file` 必须是授权根目录内的日级 Markdown；
- 行号从 1 开始，`line_end >= line_start`；
- `quote` 必须与当前指向的 source record revision 逐字一致；
- `quote_sha256` 是 quote UTF-8 字节的 SHA-256；
- 同一 source revision 内不得出现完全重复 span；
- 日级文件后续 append 不会使旧 record revision 自动失效；目标记录片段发生编辑、删除或无法唯一解析时必须创建新 source revision，旧下游 run 进入 stale。

### 3.3 `Usage`

必须且只能包含：

```json
{
  "model_calls": 1,
  "prompt_tokens": 1000,
  "completion_tokens": 200,
  "total_tokens": 1200,
  "reasoning_tokens": 0,
  "prompt_cache_hit_tokens": 0,
  "prompt_cache_miss_tokens": 1000,
  "usage_missing": false,
  "cost_usd": 0.001,
  "cost_complete": true
}
```

Token 字段为非负整数。Provider 未返回完整 usage 时，`usage_missing=true`、未知字段值使用 `null`，不得以 0 冒充已知。

### 3.4 `TargetRef`

用于候选和关系端点，必须且只能包含：

```json
{
  "kind": "understanding",
  "id": "mem_111111111111111111111111",
  "revision": 3,
  "revision_sha256": "<64 hex>"
}
```

`kind` 允许 `candidate_memory / reusable_memory / understanding`。`candidate_memory` 的 `revision` 和 `revision_sha256` 必须为 `null`；正式对象两者必须非空。

### 3.5 `AgentResultRef`

用于 Daily Bundle 引用已完成的 Agent V1 结果，必须且只能包含：

```json
{
  "request_id": "arq_111111111111111111111111",
  "run_id": "arun_111111111111111111111111",
  "response_sha256": "<64 hex>",
  "status": "updated",
  "memory_ref": { "kind": "understanding", "id": "mem_111111111111111111111111", "revision": 3, "revision_sha256": "<64 hex>" }
}
```

`status` 允许 `updated / no_change / insufficient_evidence / budget_exhausted / stale / error`。只有 `updated` 的 `memory_ref` 非空，其他状态必须为 `null`。

## 4. 不可变源：`SourceRecordRevision`

### 4.1 目的

`SourceRecordRevision` 是原始日记中一条用户记录的只读索引。它不复制完整原文，不允许 AI 写入 source_file。

### 4.2 精确字段

```json
{
  "schema_version": "1.0",
  "kind": "memento_source_record_revision",
  "record_id": "rec_111111111111111111111111",
  "revision": 1,
  "status": "active",
  "operation": "ingest",
  "created_at": "2026-08-18T10:50:01+08:00",
  "captured_at": "2026-08-18T10:50:00+08:00",
  "local_date": "2026-08-18",
  "source_type": "voice_transcript",
  "source_app": "Memento Voice Capture",
  "source_file": "2026-08-18.md",
  "line_start": 12,
  "line_end": 16,
  "entry_sha256": "<64 hex>",
  "source_snapshot_sha256": "<64 hex>",
  "attachments": [
    {
      "path": "assets/2026-08-18-105000-voice.m4a",
      "mime_type": "audio/mp4",
      "byte_size": 12345,
      "sha256": "<64 hex>"
    }
  ],
  "ingest_origin": "capture_service",
  "previous_revision_sha256": null
}
```

枚举：

- `status`: `active / tombstone`；
- `operation`: `ingest / source_edit / user_delete`；
- `source_type`: `text / screenshot_ocr / voice_transcript / image_note / file_note`；
- `ingest_origin`: `capture_service / reconciler / legacy_import`。

约束：

- 最新 revision 为 `tombstone` 时，记录不再进入新 AI 处理；
- `user_delete` 必须对应 `tombstone`；其他 operation 必须对应 `active`；
- AI 只能引用该对象，不能创建 `source_edit` 或 `user_delete`；
- 附件相对路径与 hash 仅供本地定位，默认不进入 Provider input；
- `entry_sha256` 绑定该记录块；`source_snapshot_sha256` 只记录索引时整份文件快照，不作为日后 append 的失效条件。

## 5. 逐条整理对象

### 5.1 `InterpretationRequest`

必须且只能包含：

```json
{
  "schema_version": "1.0",
  "kind": "memento_interpretation_request",
  "id": "ireq_111111111111111111111111",
  "status": "pending",
  "created_at": "2026-08-18T10:50:02+08:00",
  "trigger": "capture",
  "record_ref": { "kind": "source_record", "id": "rec_111111111111111111111111", "revision": 1, "revision_sha256": "<64 hex>" },
  "contract_version": "record-interpreter-v1",
  "feedback_watermark_sha256": "<64 hex>"
}
```

- `status` 固定为 `pending`；
- `trigger`: `capture / reconcile / retry / source_changed`；
- request 创建后不可修改；
- 同一 material run key 已有合法 terminal receipt 或可信 `no_candidate` run 时必须复用，不新调 Provider；
- 只有“已持久化、且完整绑定唯一 Provider completion 的已知 Schema 拒绝”可创建一个确定性 `retry` request。同一 material run key 最多只允许这 1 次附加 Schema 重试；重试再失败、`unknown_attempt`、非 Schema 错误或来源/action watermark 已变化时都不得发起第三次调用。

### 5.2 `InterpretationRun`

Run 是可覆盖 checkpoint，必须且只能包含：

```json
{
  "schema_version": "1.0",
  "kind": "memento_interpretation_run",
  "run_id": "irun_111111111111111111111111",
  "request_id": "ireq_111111111111111111111111",
  "request_sha256": "<64 hex>",
  "run_key": "irk_111111111111111111111111",
  "status": "running",
  "started_at": "2026-08-18T10:50:03+08:00",
  "updated_at": "2026-08-18T10:50:04+08:00",
  "completed_at": null,
  "provider": "deepseek-agentic-workflow",
  "model": "deepseek-v4-pro",
  "contract_version": "record-interpreter-v1",
  "input_hashes": {
    "record_revision_sha256": "<64 hex>",
    "feedback_watermark_sha256": "<64 hex>",
    "policy_sha256": "<64 hex>"
  },
  "steps": [],
  "usage": null,
  "receipt_ref": null,
  "error_kind": null
}
```

`steps[]` 每项必须且只能含 `turn / action / reason_code / arguments_sha256 / result_kind / result_count / error_kind`，不得保存 CoT 或完整 ToolResult。

状态：

```text
pending request
  → running
  → completed | no_candidate | stale | error | budget_exhausted
```

`completed` 必须携带合法 terminal `receipt_ref`。`no_candidate` 是可信 run 终态，但必须为 `receipt_ref=null`；它只覆盖该 request 精确绑定的当前 `record_ref`、feedback watermark、目标对象目录与 policy。`stale / error / budget_exhausted` 也是 0 receipt 写入。

### 5.3 `CandidateMemory`

必须且只能包含：

```json
{
  "candidate_id": "cmem_111111111111111111111111",
  "statement": "一条可以单独引用的记忆表述",
  "memory_kind": "observation",
  "topics": ["产品设计"],
  "purposes": ["future_decision"],
  "uncertainty": "medium",
  "source_spans": [
    {
      "record_id": "rec_111111111111111111111111",
      "record_revision": 1,
      "record_revision_sha256": "<64 hex>",
      "source_file": "2026-08-18.md",
      "line_start": 12,
      "line_end": 13,
      "quote": "用户实际留下的逐字片段",
      "quote_sha256": "<64 hex>"
    }
  ]
}
```

枚举：

- `memory_kind`: `quote / own_idea / observation / question / decision / action / experience / fact / learning`；
- `purposes`: `find_later / continue_thinking / create / future_decision / action_clue / preserve_only`；
- `uncertainty`: `low / medium / high`。

`source_spans` 至少 1 条，且只能来自当前 receipt 授权记录。

### 5.4 `CandidateRelation`

必须且只能包含：

```json
{
  "candidate_id": "crel_111111111111111111111111",
  "type": "supports",
  "from_ref": { "kind": "candidate_memory", "id": "cmem_111111111111111111111111", "revision": null, "revision_sha256": null },
  "to_ref": { "kind": "understanding", "id": "mem_222222222222222222222222", "revision": 2, "revision_sha256": "<64 hex>" },
  "direction": "directed",
  "statement": "这条候选如何支持当前理解",
  "uncertainty": "medium",
  "source_spans": [
    {
      "record_id": "rec_111111111111111111111111",
      "record_revision": 1,
      "record_revision_sha256": "<64 hex>",
      "source_file": "2026-08-18.md",
      "line_start": 12,
      "line_end": 13,
      "quote": "用户实际留下的逐字片段",
      "quote_sha256": "<64 hex>"
    }
  ]
}
```

`type`: `supports / counterexample / revises / scope_boundary / same_topic`。`same_topic` 必须 `undirected`；其他类型必须 `directed`。

### 5.5 `InterpretationReceiptRevision`

必须且只能包含：

```json
{
  "schema_version": "1.0",
  "kind": "memento_interpretation_receipt_revision",
  "receipt_id": "rcp_111111111111111111111111",
  "revision": 1,
  "status": "ready",
  "operation": "interpret",
  "created_at": "2026-08-18T10:50:05+08:00",
  "request_id": "ireq_111111111111111111111111",
  "run_id": "irun_111111111111111111111111",
  "record_ref": { "kind": "source_record", "id": "rec_111111111111111111111111", "revision": 1, "revision_sha256": "<64 hex>" },
  "user_action_id": null,
  "summary": "完备性可能推迟真实反馈",
  "facets": {
    "content_types": ["observation"],
    "topics": ["产品设计"],
    "objects": ["方案评审"],
    "stance": "self_observation",
    "cognitive_state": "repeated",
    "purposes": ["future_decision"]
  },
  "memory_candidates": [],
  "relation_candidates": [],
  "source_spans": [
    {
      "record_id": "rec_111111111111111111111111",
      "record_revision": 1,
      "record_revision_sha256": "<64 hex>",
      "source_file": "2026-08-18.md",
      "line_start": 12,
      "line_end": 13,
      "quote": "用户实际留下的逐字片段",
      "quote_sha256": "<64 hex>"
    }
  ],
  "contract_version": "record-interpreter-v1",
  "feedback_watermark_sha256": "<64 hex>",
  "previous_revision_sha256": null
}
```

枚举：

- `status`: `ready / needs_review / original_only / tombstone`；
- `operation`: `interpret / user_confirm / user_edit / original_only / source_superseded / tombstone`；
- `content_types` 与 `CandidateMemory.memory_kind` 同值域；
- `stance`: `agree / doubt / reject / inspired / self_observation / unresolved / unknown`；
- `cognitive_state`: `first_seen / repeated / supports_existing / conflicts_existing / revises_existing / verified / unknown`。

约束：

- `ready / needs_review`：summary、facets 非空；
- `original_only / tombstone`：`memory_candidates` 与 `relation_candidates` 必须为空；
- `user_confirm / user_edit / original_only / tombstone` 的 `user_action_id` 必须非空并绑定合法 user action；`interpret / source_superseded` 时必须为 `null`；
- `original_only / tombstone` 是同一 `record_id` 的用户权威终态。后续 source edit 会追加新的 source revision，但不恢复自动整理，不为该 record 新建 interpretation run，也不让它阻塞日级完整性闸门；
- receipt 是逐条解释，不是正式可用记忆；Daily Reader 只能把它作为候选输入。

## 6. 日级归并对象

### 6.1 `DailyIntegrationRequest`

```json
{
  "schema_version": "1.0",
  "kind": "memento_daily_integration_request",
  "id": "dreq_111111111111111111111111",
  "status": "pending",
  "created_at": "2026-08-18T21:00:00+08:00",
  "trigger": "scheduled",
  "local_date": "2026-08-18",
  "contract_version": "daily-integrator-v1"
}
```

必须且只能包含上述字段。`trigger`: `manual / scheduled / recovery`。公共 CLI 在没有 nonce 时，同一日期、同一 trigger 复用同一 request；内部显式提供 nonce 时可以创建新的审计 request，但相同 material run key 仍只能产生一份候选/正式结果。

创建 Daily request 前必须对目标日所有当前 active record head 执行完整覆盖闸门。每条 head 必须且只能落入下列之一：

1. 有精确绑定当前 `record_ref` 的 `ready / needs_review` receipt；
2. 有精确绑定当前材料身份的可信 `no_candidate` run；
3. 该 `record_id` 的当前 receipt 为用户终态 `original_only / tombstone`。

任一 active head 不在上述集合内时，日流程返回 `no_receipts`；不得创建 Daily request，不得把 receipt 子集送入 Integrator，不得部分提交 summary、memory、relation 或 bundle。

### 6.2 `DailyIntegrationRun`

必须且只能包含：

```json
{
  "schema_version": "1.0",
  "kind": "memento_daily_integration_run",
  "run_id": "drun_111111111111111111111111",
  "request_id": "dreq_111111111111111111111111",
  "request_sha256": "<64 hex>",
  "run_key": "drk_111111111111111111111111",
  "status": "running",
  "stage": "integrating",
  "started_at": "2026-08-18T21:00:01+08:00",
  "updated_at": "2026-08-18T21:00:03+08:00",
  "completed_at": null,
  "provider": "deepseek-agentic-workflow",
  "model": "deepseek-v4-pro",
  "contract_version": "daily-integrator-v1",
  "input_manifest": {
    "source_refs": [],
    "receipt_refs": [],
    "source_manifest_sha256": "<64 hex>",
    "receipt_manifest_sha256": "<64 hex>",
    "profile_sha256": "<64 hex>",
    "user_action_watermark_sha256": "<64 hex>",
    "policy_sha256": "<64 hex>"
  },
  "steps": [],
  "usage": null,
  "bundle_ref": null,
  "review_status": "not_started",
  "long_term_status": "not_started",
  "landscape_status": "not_started",
  "warnings": [],
  "error_kind": null
}
```

`stage`: `preparing / completing_receipts / integrating / validating / committing_bundle / generating_review / judging_long_term / projecting / finished`。

Run 状态：

```text
pending request
  → running
  → committed | committed_with_warnings | no_change | stale | error | budget_exhausted
```

`committed_with_warnings` 表示 daily bundle 已正式提交，但 Review、长期判断或地景至少一项失败。`warnings` 只允许 `review_failed / long_term_failed / landscape_failed / partial_source_unavailable`。

### 6.3 `DailySummaryRevision`

必须且只能包含：

```json
{
  "schema_version": "1.0",
  "kind": "memento_daily_summary_revision",
  "summary_id": "dsum_20260818",
  "revision": 1,
  "status": "active",
  "operation": "generate",
  "created_at": "2026-08-18T21:00:10+08:00",
  "local_date": "2026-08-18",
  "overview": "今天反复回到验证标准与最小闭环。",
  "themes": ["验证标准", "最小闭环"],
  "changes": [],
  "unresolved_questions": ["怎样更早暴露方案？"],
  "action_clues": ["下一次评审先给出最早可验证部分"],
  "source_refs": [],
  "receipt_refs": [],
  "review_file": "Reviews/Daily/2026-08-18.md",
  "review_sha256": null,
  "user_supplement_sha256": null,
  "previous_revision_sha256": null
}
```

枚举：

- `status`: `active / tombstone`；
- `operation`: `generate / regenerate / user_supplement_changed / tombstone`。

摘要只能引用当前日期的合法 source/receipt refs。`action_clues` 是用户原文中明确存在的行动倾向，不是任务催办。

`review_file` 是计划写入位置。Daily Bundle 可以先提交 `review_sha256=null` 的结构化摘要；Markdown Review 校验成功后追加 summary revision，并写入 `review_sha256`。没有“我的补充”时 `user_supplement_sha256=null`。

### 6.4 `DailyBundleRevision`

`manifest.json` 的精确字段：

```json
{
  "schema_version": "1.0",
  "kind": "memento_daily_bundle_revision",
  "bundle_id": "db_20260818",
  "revision": 1,
  "status": "committed",
  "operation": "initial_commit",
  "created_at": "2026-08-18T21:00:12+08:00",
  "committed_at": "2026-08-18T21:00:13+08:00",
  "local_date": "2026-08-18",
  "request_id": "dreq_111111111111111111111111",
  "run_id": "drun_111111111111111111111111",
  "input_hashes": {
    "source_manifest_sha256": "<64 hex>",
    "receipt_manifest_sha256": "<64 hex>",
    "profile_sha256": "<64 hex>",
    "user_action_watermark_sha256": "<64 hex>",
    "policy_sha256": "<64 hex>"
  },
  "source_refs": [],
  "receipt_refs": [],
  "memory_refs": [],
  "relation_refs": [],
  "summary_ref": { "kind": "daily_summary", "id": "dsum_20260818", "revision": 1, "revision_sha256": "<64 hex>" },
  "candidate_materializations": [],
  "long_term_result_ref": null,
  "warnings": [],
  "previous_revision_sha256": null
}
```

`candidate_materializations[]` 每项必须且只能包含：

```json
{
  "candidate_kind": "memory",
  "candidate_id": "cmem_111111111111111111111111",
  "formal_ref": { "kind": "reusable_memory", "id": "rmem_111111111111111111111111", "revision": 1, "revision_sha256": "<64 hex>" }
}
```

规则：

- `status` 固定为 `committed`；staging 中不得出现可被 Reader 接受的 manifest；
- `operation`: `initial_commit / append_review_result / append_long_term_result / feedback_recompute`；
- revision 1 必须为 `initial_commit`；
- Agent V1 后续成功后，可追加 bundle revision，将 `long_term_result_ref` 和新建 relation refs 纳入当前日级头；revision 1 在此前仍完整有效；
- `long_term_result_ref` 只允许使用 `AgentResultRef` 或为 `null`；
- manifest 列出的所有对象必须存在、hash 正确、属于本批次或被明确复用；
- Reader 只接受完整连续链的最新合法 committed revision。

## 7. 正式可用记忆与关系

### 7.1 `ReusableMemoryRevision`

必须且只能包含：

```json
{
  "schema_version": "1.0",
  "kind": "memento_reusable_memory_revision",
  "memory_id": "rmem_111111111111111111111111",
  "revision": 1,
  "status": "active",
  "operation": "new",
  "created_at": "2026-08-18T21:00:10+08:00",
  "statement": "评审前先定义最早可验证部分。",
  "memory_kind": "decision",
  "topics": ["产品设计"],
  "purposes": ["future_decision"],
  "uncertainty": "low",
  "source_spans": [
    {
      "record_id": "rec_111111111111111111111111",
      "record_revision": 1,
      "record_revision_sha256": "<64 hex>",
      "source_file": "2026-08-18.md",
      "line_start": 12,
      "line_end": 13,
      "quote": "用户实际留下的逐字片段",
      "quote_sha256": "<64 hex>"
    }
  ],
  "origin_receipt_refs": [],
  "provenance": {
    "origin": "daily_integrator",
    "run_id": "drun_111111111111111111111111",
    "bundle_id": "db_20260818",
    "bundle_revision": 1,
    "user_action_id": null
  },
  "previous_revision_sha256": null
}
```

枚举：

- `status`: `active / tombstone`；
- `operation`: `new / revise / user_edit / tombstone`；
- `memory_kind`、`purposes`、`uncertainty` 与 CandidateMemory 相同；
- `provenance.origin`: `daily_integrator / agent_v1_adapter / feedback_recompute / user`。

约束：

- active revision 至少 1 条有效 SourceSpan；
- revision 1 必须 `operation=new`；
- tombstone 保留最后已知 statement 与来源，但不进入 active projection；
- user_edit/tombstone 必须绑定 `user_action_id`；
- 一个 `record_id` 可以支持多个 `rmem_`；一个 `rmem_` 可以引用多个记录日。

### 7.2 `RelationRevision`

必须且只能包含：

```json
{
  "schema_version": "1.0",
  "kind": "memento_relation_revision",
  "relation_id": "rel_111111111111111111111111",
  "revision": 1,
  "status": "active",
  "operation": "new",
  "created_at": "2026-08-18T21:00:11+08:00",
  "type": "supports",
  "from_ref": { "kind": "reusable_memory", "id": "rmem_111111111111111111111111", "revision": 1, "revision_sha256": "<64 hex>" },
  "to_ref": { "kind": "understanding", "id": "mem_222222222222222222222222", "revision": 2, "revision_sha256": "<64 hex>" },
  "direction": "directed",
  "statement": "这条记忆为该理解提供一次明确支持。",
  "uncertainty": "low",
  "source_spans": [
    {
      "record_id": "rec_111111111111111111111111",
      "record_revision": 1,
      "record_revision_sha256": "<64 hex>",
      "source_file": "2026-08-18.md",
      "line_start": 12,
      "line_end": 13,
      "quote": "用户实际留下的逐字片段",
      "quote_sha256": "<64 hex>"
    }
  ],
  "valid_from": "2026-08-18",
  "provenance": {
    "origin": "daily_integrator",
    "run_id": "drun_111111111111111111111111",
    "bundle_id": "db_20260818",
    "bundle_revision": 1,
    "user_action_id": null
  },
  "previous_revision_sha256": null
}
```

枚举：

- `status`: `active / tombstone`；
- `operation`: `new / revise / user_edit / tombstone`；
- `type`: `supports / counterexample / revises / scope_boundary / same_topic`；
- `direction`: `directed / undirected`；
- `provenance.origin`: `daily_integrator / agent_v1_adapter / feedback_recompute / user`。

约束：

- 正式端点只允许 `reusable_memory` 或 `understanding`；
- `same_topic` 必须无向，其他类型必须有向；
- `from_ref` 与 `to_ref` 不得完全相同；
- active relation 至少 1 条 SourceSpan；
- relation 指向的精确 revision 失效、tombstone 或不再是当前版本时，不得继续出现在 current projection；应由下一日归并或 feedback recompute 生成新的 relation revision；
- 一条可用记忆可通过多条 relation 连接多个长期理解。

## 8. 长期理解：复用 Agent V1

### 8.1 `RememberMemoryRevision`

当前 Agent V1 正式 revision 保持原字段，不新增地景字段：

```json
{
  "schema_version": "1.0",
  "kind": "remember_memory_revision",
  "memory_id": "mem_111111111111111111111111",
  "revision": 3,
  "status": "active",
  "created_at": "2026-08-18T21:00:20+08:00",
  "run_id": "arun_111111111111111111111111",
  "request_id": "arq_111111111111111111111111",
  "operation": "revise",
  "previous_revision_sha256": "<64 hex>",
  "base_profile_ref": null,
  "user_action_id": null,
  "title": "先验证最小闭环",
  "statement": "在产品判断中，倾向先明确最早可验证部分，再逐步补全方案。",
  "scope": "产品方案评审",
  "insight_kind": "change",
  "uncertainty": "medium",
  "evidence": [
    { "file": "2026-08-18.md", "line": 12, "quote": "逐字证据" }
  ],
  "counterevidence": [],
  "source_hashes": [
    { "file": "2026-08-18.md", "sha256": "<64 hex>" }
  ]
}
```

当前枚举：

- `status`: `active / tombstone`；
- `operation`: `new / reinforce / revise / tension / user_edit / tombstone / bootstrap_reject`；
- `insight_kind`: 正式新 revision 为 `observation / change / tension`；兼容 profile 可能出现 `confirmed`；
- `uncertainty`: `low / medium`。

规则继续沿用 Agent V1：

- active revision 必须有有效 evidence 与完全对应的 source hashes；
- `new` 至少两个不同记录日支持且无反例；
- `reinforce` 不改 statement/scope；
- `revise` 必须有新方向证据和旧方向依据；
- `tension` 必须同时有支持与反例；
- user edit/delete 通过现有 `remember_agent_user_action`；
- Committer 在锁内执行 target revision、profile、feedback、source 和 user-action watermark CAS；
- 地景字段、坐标、高度和颜色不得写进该 revision。

### 8.2 候选与正式长期理解

- Agent V1 Candidate Scout、investigation bundle 和 patch 均是候选；
- 只有 `remember_memory_revision` 已原子写入、response/run 为合法终态且 profile 可重建后，才是正式长期理解；
- Daily Integrator 不得绕过 Agent V1 Committer创建 `mem_`；
- Agent V1 摘要不能替代其原始 evidence。

## 9. 认知地景

### 9.1 `LandscapeSnapshot`

快照不可变，必须且只能包含：

```json
{
  "schema_version": "1.0",
  "kind": "memento_landscape_snapshot",
  "snapshot_id": "lnd_111111111111111111111111",
  "created_at": "2026-08-18T21:00:25+08:00",
  "as_of": "2026-08-18",
  "projection_version": "cognitive-landscape-v1",
  "input_hashes": {
    "agent_profile_sha256": "<64 hex>",
    "reusable_memory_head_sha256": "<64 hex>",
    "relation_head_sha256": "<64 hex>",
    "user_action_watermark_sha256": "<64 hex>"
  },
  "summary": {
    "active_understandings": 4,
    "recent_changes": 1,
    "observing_candidates": 2
  },
  "terrain": {
    "algorithm_version": "stable-anchor-kde-v1",
    "grid_size": 96,
    "contour_levels": 18,
    "coordinate_space": "normalized_0_1"
  },
  "peaks": [],
  "nodes": [],
  "edges": [],
  "previous_snapshot_sha256": null
}
```

`peaks[]` 每项必须且只能包含：

```json
{
  "peak_id": "peak_111111111111111111111111",
  "understanding_ref": { "kind": "understanding", "id": "mem_111111111111111111111111", "revision": 3, "revision_sha256": "<64 hex>" },
  "x": 0.62,
  "y": 0.31,
  "elevation": 0.74,
  "evidence_count": 8,
  "counterevidence_count": 1,
  "recent_change": true,
  "lifecycle": "active"
}
```

`nodes[]` 每项必须且只能包含：

```json
{
  "memory_ref": { "kind": "reusable_memory", "id": "rmem_111111111111111111111111", "revision": 1, "revision_sha256": "<64 hex>" },
  "x": 0.58,
  "y": 0.36,
  "state": "committed",
  "recent": true
}
```

`edges[]` 每项必须且只能包含：

```json
{
  "relation_ref": { "kind": "relation", "id": "rel_111111111111111111111111", "revision": 1, "revision_sha256": "<64 hex>" },
  "from_id": "rmem_111111111111111111111111",
  "to_id": "mem_222222222222222222222222",
  "type": "supports"
}
```

规则：

- `x/y/elevation` 范围为 0–1；
- `x/y` 只表示可重建的稳定排版坐标；峰间远近不表示关系、相似性、语义矛盾或重要程度，不得作为事实对象、长期判断或用户画像的输入；
- 稳定 hash 只能作为新峰初始坐标的种子。Projector 必须对无正式关联的峰执行确定性避碰，并优先保留上一版仍满足避碰边界的安全坐标；
- 两座峰只有在存在直接 current active formal relation，或共同连接到同一条 current active reusable memory 且两条边均为 current active formal relation 时，才允许局部接近；允许接近不赋予视觉距离事实语义；
- 正式关系被撤回、失效或 tombstone 后，如果原坐标使已无正式关联的峰产生点击热区或 KDE 布局碰撞，Projector 必须按稳定对象 ID 确定性重排碰撞点，并保留其他安全坐标；
- `elevation` 只表示经校验证据积累，不表示人格强度、重要性或真实性；
- `lifecycle`: `active / tension / dormant`；MVP 不自动 merge/split；
- `nodes.state` 正式快照只允许 `committed`；v0.9.0 不在地景上叠加等待归并的候选点，这些内容只在今日记录区表达；
- `summary.observing_candidates` 为保留字段，v0.9.0 Projector 固定写 `0`；
- peak 必须引用 current active Agent V1 memory；
- node 必须引用 current active reusable memory；
- edge 必须引用 current active formal relation，且其端点必须存在于当前快照；
- Projector 失败不得写半份快照；Reader 保留上一合法快照。

## 10. 用户反馈

### 10.1 新增 `CognitiveUserAction`

逐条回执、可用记忆、关系和现实结果使用本对象。必须且只能包含：

```json
{
  "schema_version": "1.0",
  "kind": "memento_cognitive_user_action",
  "id": "cact_111111111111111111111111",
  "created_at": "2026-08-18T21:05:00+08:00",
  "action": "confirm_receipt",
  "target_ref": { "kind": "interpretation_receipt", "id": "rcp_111111111111111111111111", "revision": 1, "revision_sha256": "<64 hex>" },
  "payload": null
}
```

允许 action 与 payload：

| action | target kind | payload 精确字段 |
|---|---|---|
| `confirm_receipt` | interpretation_receipt | `null` |
| `edit_receipt` | interpretation_receipt | `summary`, `facets` |
| `original_only` | interpretation_receipt | `null` |
| `edit_reusable_memory` | reusable_memory | `statement`, `topics`, `purposes` |
| `delete_reusable_memory` | reusable_memory | `null` |
| `edit_relation` | relation | `type`, `statement` |
| `delete_relation` | relation | `null` |
| `report_outcome` | reusable_memory | `outcome`, `occurred_at` |

动作文件：

- 文件名、id 与内容必须绑定；
- 创建后不可覆盖；
- 必须绑定 base revision/hash；
- Dashboard 只能写 action，不能直接写 receipt、memory 或 relation revision；
- `original_only` 对同一 record 的后续自动整理具有终态优先，MVP 不实现自动恢复；
- `report_outcome` 作为后续归并输入，不直接改变长期理解。

### 10.2 `CognitiveActionResult`

terminal result 不可变，必须且只能包含：

```json
{
  "schema_version": "1.0",
  "kind": "memento_cognitive_action_result",
  "id": "cares_111111111111111111111111",
  "action_id": "cact_111111111111111111111111",
  "action_sha256": "<64 hex>",
  "status": "applied",
  "completed_at": "2026-08-18T21:05:01+08:00",
  "materialized_refs": [],
  "error_kind": null
}
```

`status`: `applied / rejected / conflict`。`conflict` 表示 base revision/hash 已变化；不得把 action 自动套到新版本。

### 10.3 Agent V1 `RememberAgentUserAction`

长期理解 edit/delete 保持当前精确字段：

```json
{
  "schema_version": "1.0",
  "id": "uact_111111111111111111111111",
  "kind": "remember_agent_user_action",
  "created_at": "2026-08-18T21:05:00+08:00",
  "action": "edit",
  "memory_id": "mem_111111111111111111111111",
  "base_revision": 3,
  "base_revision_sha256": "<64 hex>",
  "statement": "用户认可的新表述",
  "scope": "适用范围"
}
```

`action` 只允许 `edit / delete`；delete 时 statement/scope 为 `null`。合法 delete 为终态优先，后续 edit 不得复活。

当前 v0.9.0 认知主页提供 `confirm_receipt / edit_receipt / original_only / edit_reusable_memory / delete_reusable_memory / edit_relation / delete_relation`。`report_outcome` 已有底层合同，本版主页不提供入口。

### 10.4 `ManualDayRequest`

“归并今天”使用独立的浏览器→Worker 交接对象。请求必须且只能包含六个字段：

```json
{
  "created_at": "2026-08-18T21:05:00.000+08:00",
  "id": "cman_111111111111111111111111",
  "kind": "memento_cognitive_manual_day_request",
  "local_date": "2026-08-18",
  "schema_version": "1.0",
  "status": "pending"
}
```

规则：

- 文件名必须是 `<id>.json`，`id` 必须匹配 `^cman_[0-9a-f]{24}$`；当前浏览器 Writer 使用 Web Crypto 生成 12 个随机字节并编码为 24 位小写 hex；
- `created_at` 必须带时区，其日期部分必须等于 `local_date`；`local_date` 是点击时的浏览器当地今天；
- `status` 只能是 `pending`；
- 按 UTF-8、key 字典序、2 空格缩进、末尾单个换行序列化；`request_sha256` 必须对这些精确文件字节计算；
- 请求位于 `.context-agent/cognitive-secretary-v1/manual-day-requests/`，目录必须为当前用户所有的 `0700` 普通目录，文件必须为当前用户所有、`0600`、单链接普通文件；
- 符号链接、硬链接、字节非规范化、文件名/ID 不一致、超过 16 KiB 或目录中出现未授权文件时，Worker 必须 fail-closed，不进入 day runner；
- 页面是 request 的唯一产品 Writer，只能追加请求；页面不得直接调用 Provider、CLI 或写 result、daily bundle/projection。`daily-manual-worker` 只消费请求，不改写请求字节。

### 10.5 `ManualDayResult`

Worker 每个 request hash 最多写一个 terminal result。必须且只能包含十个字段：

```json
{
  "completed_at": "2026-08-18T21:05:02+08:00",
  "error_kind": null,
  "id": "cmanr_222222222222222222222222",
  "kind": "memento_cognitive_manual_day_result",
  "local_date": "2026-08-18",
  "request_id": "cman_111111111111111111111111",
  "request_sha256": "<64 hex>",
  "runner_status": "committed",
  "schema_version": "1.0",
  "status": "completed"
}
```

规则：

- `id = "cmanr_" + sha256(("manual-result:" + request_sha256).utf8)[0:24]`，文件名必须是 `<id>.json`；
- `request_id`、`request_sha256` 和 `local_date` 必须精确绑定被消费请求；`completed_at` 必须带时区；
- `status` 只允许 `completed / master_gate_disabled / rejected_date / runner_failed`；
- `completed` 时 `runner_status` 必须是 `completed / committed / committed_with_warnings / no_change / no_candidate / no_records / no_receipts / stale / error / budget_exhausted` 之一，`error_kind` 必须为 `null`；
- `master_gate_disabled` 时 `runner_status=null`、`error_kind=null`；
- `rejected_date` 时 `runner_status=null`、`error_kind="date"`；Worker 只接受等于自身当地今天的 request `local_date`；
- `runner_failed` 时 `runner_status=null`，`error_kind` 只允许 `contract / runtime`；
- `daily-manual-worker` 是 result 的唯一 Writer；结果位于 `.context-agent/cognitive-secretary-v1/manual-day-results/`，权限与不可变字节规则与 request 一致，页面不得创建或修改 result；
- 已有合法结果时，后续消费只计为 `already_resolved`，不再调用 day runner；同 ID 但字节不同必须报 `conflict`；
- Dashboard 必须先验证精确字段集、result ID 公式和状态组合，再核对 request ID/hash 与日期。与当前 pending request 绑定不一致的结果不得作为它的完成凭据。

## 11. Projection 合同

### 11.1 `HomeProjection`

`home_projection.json` 可覆盖、可删除、可重建。必须且只能包含：

```json
{
  "schema_version": "1.0",
  "kind": "memento_home_projection",
  "projection_version": "cognitive-secretary-home-v1",
  "generated_at": "2026-08-18T21:00:26+08:00",
  "local_date": "2026-08-18",
  "input_hashes": {
    "record_head_sha256": "<64 hex>",
    "receipt_head_sha256": "<64 hex>",
    "daily_bundle_head_sha256": "<64 hex>",
    "agent_profile_sha256": "<64 hex>",
    "landscape_snapshot_sha256": "<64 hex>",
    "user_action_watermark_sha256": "<64 hex>"
  },
  "landscape_ref": {
    "snapshot_id": "lnd_111111111111111111111111",
    "snapshot_sha256": "<64 hex>"
  },
  "landscape_summary": {
    "active_understandings": 4,
    "recent_changes": 1,
    "observing_candidates": 2
  },
  "today_status": {
    "saved": 5,
    "interpreted": 4,
    "merged": 3,
    "needs_review": 1,
    "daily_run_status": "committed_with_warnings"
  },
  "records": [],
  "schedule": {
    "enabled": true,
    "hour": 21,
    "minute": 0,
    "next_due_at": "2026-08-19T21:00:00+08:00",
    "last_run_status": "committed_with_warnings"
  },
  "warnings": ["long_term_failed"]
}
```

`records[]` 每项必须且只能包含：

```json
{
  "record_ref": { "kind": "source_record", "id": "rec_111111111111111111111111", "revision": 1, "revision_sha256": "<64 hex>" },
  "receipt_ref": { "kind": "interpretation_receipt", "id": "rcp_111111111111111111111111", "revision": 1, "revision_sha256": "<64 hex>" },
  "captured_at": "2026-08-18T10:50:00+08:00",
  "source_type": "voice_transcript",
  "source_app": "Memento Voice Capture",
  "status": "ready",
  "summary": "完备性可能推迟真实反馈",
  "content_types": ["observation"],
  "topics": ["产品设计"],
  "purposes": ["future_decision"],
  "memory_refs": [],
  "understanding_refs": []
}
```

`raw_saved / processing / no_candidate / failed` 可以在没有合法逐条整理回执时使用 `"receipt_ref": null`；其中 `no_candidate` 必须为 `null`。`ready / needs_review / original_only / merged` 必须绑定当前合法 `interpretation_receipt`；`raw_saved / processing` 不得提前绑定回执。主页不得用伪造引用填充未完成状态。

`original_only / no_candidate` 的 `summary` 必须为 `null`，`content_types / topics / purposes / memory_refs / understanding_refs` 必须为空；浏览器在展示前再次执行该约束。`no_candidate` 计入 `today_status.interpreted`，但不计入 `merged`。

主页 projection 不包含完整原文或附件内容。点击后由受限 Reader 按 ObjectRef 加载 source span 和本地原文。

## 12. 状态机总表

### 12.1 原始记录

```text
revision 1 active/ingest
  → revision N active/source_edit
  → revision N+1 tombstone/user_delete
```

AI 无权触发 `source_edit` 或 `user_delete`。

### 12.2 逐条整理

持久 run：

```text
pending → running → completed | no_candidate | stale | error | budget_exhausted
```

页面派生状态：

```text
raw_saved
  → processing
  → ready | needs_review | no_candidate | failed
ready | needs_review
  → original_only
  → tombstone
  → merged
```

`merged` 由当前 record/receipt 被合法 daily bundle 引用派生，不写回 receipt。

`original_only / tombstone` 在同一 record 后续 source edit 后仍保持自动处理终态。`no_candidate` 只对精确当前 source revision 有效；source edit 必须重新整理。

### 12.3 日级归并

```text
pending
  → running
  → committed | committed_with_warnings | no_change | stale | error | budget_exhausted
```

只有 `committed / committed_with_warnings` 必须携带 bundle_ref。

统一日流程在创建上述 Daily run 之前还可返回预检终态：

- 全部 active records 都是当前 `no_candidate`（或其余只有用户终态）时返回 `no_candidate`，0 receipt，0 Daily request，0 bundle；
- 全部 active records 都是 `original_only / tombstone` 时返回 `no_change`，0 receipt，0 Daily request，0 bundle；
- `ready / needs_review` 与 `no_candidate` 混合时，只将前者的合法 receipt 送入 Daily Integrator；`no_candidate` 不制造伪 receipt，也不阻塞前者的原子归并；
- 存在未被合法 receipt、可信 `no_candidate` 或用户终态覆盖的 active record 时返回 `no_receipts`，且不允许部分提交。

### 12.4 可用记忆与关系

```text
candidate（receipt/daily run 内）
  → active revision 1（合法 manifest 物化）
  → active revision N（revise/user_edit）
  → tombstone revision N+1
```

候选不能被“改名”为正式对象；物化时分配正式 ID，并在 manifest 保存映射。

### 12.5 长期理解

```text
Agent candidate patch
  → validated patch
  → active memory revision: new | reinforce | revise | tension
  → user_edit revision
  → tombstone revision
```

只有 active Agent V1 revision 进入 current profile 与地景。

### 12.6 地景

```text
inputs changed
  → generating（run 状态）
  → published immutable snapshot
     或 stale/error → 继续使用上一 published snapshot
```

## 13. 候选与正式关系

| 层级 | 存放位置 | 可否进入主页默认视图 | 可否进入长期判断 | 可否成为地景线 |
|---|---|---:|---:|---:|
| receipt 候选记忆/关系 | receipt revision | 只在今日记录区表达“等待归并” | 否 | 否 |
| daily run 候选 | run checkpoint / staging | 仅处理详情 | 否 | 否 |
| committed 可用记忆 | 合法 daily bundle + memory revision | 是 | 是，但仍需回到原文 | 是，作为点 |
| committed 正式关系 | 合法 bundle + relation revision | 是 | 是 | 是，作为线 |
| Agent V1 candidate patch | Agent run | 仅运行状态 | 否 | 否 |
| Agent V1 active memory revision | Agent V1 immutable store | 是 | 是 | 是，作为峰 |

候选状态不得通过前端样式伪装成正式结果。

## 14. 跨对象不变量

1. AI 不得修改日级 Markdown 和原始附件。
2. 每个 receipt 必须绑定一个精确 SourceRecord revision。
3. 每个 active reusable memory 和 relation 必须至少有一个可验证 SourceSpan。
4. 每个正式对象必须位于完整 revision 链，且被合法 commit manifest 或 Agent V1 Committer 接纳。
5. Candidate ID 与 Formal ID 分离；候选不得直接成为事实源。
6. Daily Review、整理摘要和模型解释不能独立成为长期理解 evidence。
7. Agent V1 memory 是长期理解唯一事实源；`profile.json` 是投影。
8. LandscapeSnapshot 和 HomeProjection 都是只读投影，删除后可重建。
9. User action watermark 发生变化时，基于旧 watermark 的写入必须 stale/conflict。
10. 合法 tombstone 不得被后续 Agent 输出复活。
11. 任何 source/target hash 不匹配都必须阻止正式写入。
12. 页面打开和投影读取不得触发 Provider。
13. Provider usage 不完整时必须显式标记未知，不能把成本记为 0。
14. 目录权限失效、部分读取失败或投影损坏不得呈现为“用户没有记忆”。
15. 浏览器写入 manual-day request 不得直接进入 Provider；只有 Worker 通过文件、日期与总 gate 校验后才能调用统一 day runner。
16. ManualDayResult 必须绑定请求的精确规范化字节 hash；单独匹配 request ID 不足以表示完成。
17. `no_candidate` 必须由可验证的 request/run/completion sidecar 绑定当前材料身份，且 `receipt_ref=null`。
18. `original_only / tombstone` 对同一 record 的自动处理终态不因 source edit 而解除。
19. Daily Integrator 的输入只能在全部当前 active record heads 已被合法 receipt、可信 `no_candidate` 或用户终态覆盖后冻结；禁止部分提交。
20. 08:00 完成性以当前 heads 为准。晚到、source edit 或失败记录不得被旧 bundle 遮蔽；无 bundle 的可信 daily `no_change` 可作为终态，已有旧 bundle 时其 Review 仍必须精确绑定且 hash 有效。

## 15. Reader 与 Writer 权限矩阵

| 对象 | 唯一 Writer | Reader |
|---|---|---|
| 日级原文/附件 | 现有采集链路与用户 | Ingestor、受限 Reader、Agent 工具 |
| SourceRecord revision | Record Ingestor / Reconciler | Interpreter、Daily Orchestrator、UI Reader |
| Interpretation request/run | Controller / Worker | Worker、诊断、Home Projector |
| Receipt revision | Receipt Committer | Daily Integrator、Home Projector、UI Reader |
| Daily request/run | Daily Controller / Worker | Worker、诊断、Home Projector |
| Daily bundle / summary | Daily Bundle Committer | Agent Adapter、Projectors、UI Reader |
| ReusableMemory revision | Daily/Feedback Committer | Agent Adapter、Projectors、UI Reader |
| Relation revision | Daily/Feedback/Agent Adapter Committer | Projectors、UI Reader |
| Agent V1 memory revision | 现有 Agent V1 Committer | Agent V1、Projectors、UI Reader |
| Cognitive user action | 受信本地 UI/action client | Worker、Projectors |
| Manual day request | Chrome 认知主页 | Manual Day Worker |
| Manual day result | Manual Day Worker | Chrome 认知主页、诊断工具 |
| Agent V1 user action | 受信本地 UI/action client | Agent V1 Worker、profile projector |
| Landscape snapshot | Landscape Projector | Home Projector、Dashboard |
| Home projection | Home Projector | Dashboard |

任何未列入的 Writer 都必须被拒绝。认知主页只能写入上表中的 Cognitive user action、Manual day request 与既有 Agent V1 user action，不得直接编辑 receipt、memory、relation、bundle、result 或 projection JSON。
