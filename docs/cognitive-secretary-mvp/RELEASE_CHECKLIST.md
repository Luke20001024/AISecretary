# Memento v0.9.0 认知秘书 MVP 发布清单

> 发布目标：把“逐条整理 → 每日归并 → 长期沉淀 → 认知主页”作为一条可恢复、可校正、可验收的本地链路交付。
>
> 更新日期：2026-08-18
>
> 当前结论：本清单是发布门，未执行项目保持“待实测”；不得用代码存在代替真机结果。

## 1. 结果口径

每项验收使用以下四级口径：

1. **代码已实现**：仓库中存在实现和相应测试；
2. **文件已安装**：发行包中的目标文件已落到安装目录；
3. **功能已开启**：总 gate、计划、目录权限和 Key 均满足运行条件；
4. **真实运行通过**：在安装副本、真实 Chrome 和真实 DeepSeek 上观察到预期终态。

只有第 4 级通过，才能对外写“已实际可用”或“自动运行通过”。

## 2. 冻结范围

发布候选应同时冻结：

- `context-agent/` 的认知秘书运行时、Schema 和 DeepSeek adapter；
- `chrome-newtab/` 的认知主页、验证器和详情链路；
- 采集脚本、安装器、卸载器与三个 LaunchAgent；
- `README.md`、本目录三份合同、使用说明和本清单；
- `scripts/package_release.sh` 的发行输入及根版本号。

冻结后如修改实现、合同、安装路径、版本号或调度语义，相关测试与真机步骤必须重跑。

## 3. 工作区自动验证

### 3.1 认知秘书核心

```bash
python3 -m unittest \
  tests.test_cognitive_v1 \
  tests.test_cognitive_store_v1 \
  tests.test_cognitive_runtime_v1 \
  tests.test_cognitive_actions_v1 \
  tests.test_cognitive_manual_request_v1 \
  tests.test_cognitive_bundle_store_v1 \
  tests.test_cognitive_pipeline_v1 \
  tests.test_cognitive_record_worker_v1 \
  tests.test_cognitive_daily_review_v1 \
  tests.test_cognitive_agent_adapter_v1 \
  tests.test_cognitive_day_orchestrator_v1 \
  tests.test_cognitive_schedule_v1 \
  tests.test_cognitive_projection_v1 \
  tests.test_cognitive_migration_v1 \
  tests.test_cognitive_cli \
  tests.test_cognitive_prompts_v1 \
  tests.test_cognitive_p1_regressions
```

- [x] 命令退出码为 0。
- [x] 没有跳过会阻塞发布的合同、恢复、幂等或安全用例。
- [x] `no_candidate` 是绑定精确当前输入的 run 终态，`receipt_ref=null`，重放和投影重建都不新调 Provider。
- [x] 已知 Schema 拒绝只允许最多 1 次附加重试；重试再失败、`unknown_attempt`、非 Schema 错误都没有第三次调用。
- [x] `original_only / tombstone` 在同一 record source edit 后仍为自动处理终态，不产生新 interpretation run。
- [x] 日级覆盖闸门检查全部当前 active heads；任一失败/未终态时不创建 Daily request，不部分提交。
- [x] 日级分流为：全 `no_candidate → no_candidate`；全用户终态 `→ no_change`；`ready + no_candidate` 只归并 ready receipt。
- [x] 结果、用例数、耗时和提交 SHA 已记录。

实现冻结 `797a5c4` 的自动回归结果为 Python 605/605、Node 22/22；同一冻结的 shell 门禁通过。主页 authority 另以顺序无关的精确集合校验 active understanding 与正式山峰，缺峰、多峰或 revision/hash 不一致均 fail-closed。

### 3.2 Chrome 主页

```bash
node tests/test_cognitive_home_library.js
node tests/test_cognitive_home_dashboard.js
node tests/test_remember_agent_v1_library.js
bash tests/test_record_dashboard.sh
```

- [x] 合法 projection 正常进入认知主页。
- [x] 缺失、损坏或不匹配 projection 回退到记录主页。
- [x] `original_only` 不携带 AI 整理内容或下游引用。
- [x] `no_candidate` 显示为“已检查，本条没有形成可归并内容”，不携带 receipt、AI 摘要或下游引用。
- [x] 原文只在点击后按本地 locator 和 hash 加载。
- [x] 回执确认/完整编辑/仅保留原文、可用记忆与关系编辑/删除都只追加用户 action，不直改权威对象。
- [x] “归并今天”只写入六字段本地请求，不从页面调用 Provider 或 CLI；页面拒绝未精确绑定的结果。

### 3.3 安装、安全与发行包

```bash
bash tests/test_installation_contract.sh
bash tests/test_context_installation_security.sh
bash tests/test_release_contract.sh
```

- [x] 三个 LaunchAgent 的职责、watch path 与 21:00/08:00 日历配置符合合同；事件 Worker 同时监视认知 user-actions 和 manual-day-requests。
- [x] 全新安装不创建 Agent 总 gate 或 `schedule.json`。
- [x] 合法升级保留已有 gate 与 schedule。
- [x] 不安全目录、符号链接、非当前用户文件均 fail-closed。
- [x] 卸载保留原始记录、附件、Reviews、认知状态和 Agent V1 数据。
- [x] 实现冻结候选发行包的版本、目录名、文档和校验和一致为 v0.9.0。

Node 主页测试、记录 Dashboard shell 验收、安装合同、安装安全合同和发行合同均已通过。

### 3.4 文档静态检查

```bash
# 相对 Markdown 链接、JSON 示例、代码围栏和工作区差异检查
python3 - <<'PY'
from pathlib import Path
import json, re

root = Path.cwd()
files = [
    root / "README.md",
    root / "context-agent/README.md",
    *sorted((root / "docs/cognitive-secretary-mvp").glob("*.md")),
]
missing = []
for path in files:
    text = path.read_text(encoding="utf-8")
    fence = "`" * 3
    if text.count(fence) % 2:
        raise SystemExit(f"unclosed fence: {path}")
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
        if target.startswith(("http://", "https://", "#")):
            continue
        clean = target.split("#", 1)[0]
        if clean and not (path.parent / clean).resolve().exists():
            missing.append(f"{path}: {target}")
    pattern = re.escape(fence) + r"json\n(.*?)\n" + re.escape(fence)
    for block in re.findall(pattern, text, flags=re.S):
        json.loads(block)
if missing:
    raise SystemExit("\n".join(missing))
print(f"checked {len(files)} markdown files")
PY

git diff --check -- README.md context-agent/README.md docs/cognitive-secretary-mvp
```

- [x] Markdown 相对链接均存在。
- [x] JSON 示例可解析。
- [x] 代码围栏成对闭合。
- [x] `git diff --check` 无尾随空白或冲突标记。

## 4. 隔离 Vault 端到端验证

使用全新临时 Vault 和测试专用 Key，不在真实个人记录上做首轮破坏性验证。

### 4.1 原文与逐条整理

- [ ] 通过正式采集 Service 保存一条文字记录。
- [ ] 原始 Markdown 先成功落盘，随后才出现认知 Worker 活动。
- [ ] record sidecar 具有稳定 `rec_` ID；重复 ingest 不产生第二条记录。
- [ ] DeepSeek 返回严格 action，产生合法 receipt 或明确终止状态。
- [ ] 页面显示逐条状态、整理摘要、主题/用途，并能回到准确原文。
- [ ] 断网、超时和非法模型输出不会修改原始 Markdown。

### 4.2 手动日级链路

先在认知主页点击“归并今天”：

- [ ] `manual-day-requests/<cman_id>.json` 必须且只能包含 `schema_version / kind / id / created_at / local_date / status`，且为规范化 `0600` 单链接普通文件。
- [ ] 事件 Worker 校验当前 owner、权限、symlink/hardlink、规范化字节、当地当日和总 gate；任一不合法时 0 day-runner 调用。
- [ ] `manual-day-results/<cmanr_id>.json` 必须且只能包含十个合同字段，并精确绑定 request ID、文件 SHA-256 与日期。
- [ ] 相同请求已有合法 result 时只返回 `already_resolved`，0 新 day-runner/Provider 调用。
- [ ] 过期/未来日期、总 gate 关闭、合同错误和运行错误的 status/runner_status/error_kind 组合均符合数据合同。

下面的 CLI 用于对照诊断同一日流程：

```bash
CLI="$HOME/AISecretary/.context-agent/runtime/context_agent.py"
VAULT="$HOME/AISecretary"

python3 "$CLI" daily-run \
  --vault "$VAULT" \
  --once \
  --date "$(date +%F)" \
  --trigger manual
```

- [ ] `status`、`stage`、`pipeline_status`、三项子任务状态与 warnings 可解释。
- [ ] 合法结果形成 committed daily bundle 与 hash 绑定的 Daily Review。
- [ ] 全部当前 active records 先通过覆盖闸门；人为留一条失败记录时为 `no_receipts`，且 0 Daily request / 0 bundle / 0 正式对象。
- [ ] 隔离用例分别覆盖全 `no_candidate`、全 `original_only/tombstone` 和 `ready + no_candidate` 三种日级分流。
- [ ] 有实质材料时长期 Adapter 进入 Agent V1；没有实质材料时不制造长期变化。
- [ ] 认知地景与 home projection 从正式对象重建。
- [ ] 同一冻结输入重复运行命中幂等结果，Provider 与正式对象均不重复。

### 4.3 用户校正优先

- [ ] 回执确认、修改和仅保留原文均形成合法 action/result。
- [ ] 可用记忆和关系的修改/删除形成新 revision 或 tombstone。
- [ ] 长期理解修改/删除沿用 Agent V1 action；删除后同一 ID 不复活。
- [ ] 在模型运行期间提交用户 action，旧 run 返回 stale/conflict 且 0 越权提交。
- [ ] 校正后 projection 更新，原始 Markdown 字节不变。

### 4.4 已完成的工作区隔离合成 DeepSeek v4 有限报告

以下事实来自 Prompt `remember-agent-v1.22`、Workflow `agentic-workflow-investigation-v1.13`、stable-new identity `stable-new-identity-v1.1` 与 terminal gate `stable-new-terminal-gate-v1.0` 的冻结 v4 计划：

- [x] 两日长期沉淀案为 `all_passed`：11 calls / 26,602 Token / $0.007166364。
- [x] `original_only` 撤回案为 `all_passed`：6 calls / 11,851 Token / $0.002545011。
- [x] 合计 17 calls / 38,453 Token / $0.009711375，`invalid_action=0`。
- [x] 临时目录已清理，两个合成 case 的来源 hash 前后不变。

该有限报告只覆盖工作区 Prompt v1.22 / Workflow v1.13 源码与两个冻结合成 case。它没有使用真实用户 Vault，也不能代替第 5.3 节的安装副本账本。先前已安装 plan `fcdf` 作为独立历史账本保留，plan `2d964129…` 是当前最终已安装 runtime 的最新证据；三份数字不互相替换或汇总。2026-08-16 的 Prompt v1.20 / Workflow v1.11 已安装 runtime 快照与 Prompt v1.21 / Workflow v1.12 的 v3 两案失败继续保留为历史回归记录。

## 5. 真机安装验收

### 5.1 文件已安装

- [x] `~/AISecretary/.context-agent/runtime/context_agent.py` 可执行。
- [x] cognitive runtime、Schema、record runner 和 schedule runner 均已安装。
- [x] `~/AISecretary/.chrome-newtab/` 包含认知主页所需文件。
- [x] 三个受管 plist 均存在且 ownership/permission 合法：
  - `com.memento.context-agent.plist`：旧 Self Reflection 兼容 Worker；
  - `com.memento.remember-agent-v1.plist`：事件 Worker；
  - `com.memento.remember-agent-v1-schedule.plist`：21:00 与 08:00 统一日流程。
- [x] 安装器的最终摘要没有把“已安装”写成“已开启”或“已实跑”。
- [x] `manual-day-requests/` 和 `manual-day-results/` 已安全创建，升级时其已有内容保留不变。

当前安装对账为 89/89：71 个 runtime 源文件与 18 个 Chrome 文件均与实现冻结 `797a5c4` 字节一致；另有 4 个安装器按合同生成的 runner。对账范围的文件与目录均为当前用户私有路径。

### 5.2 功能已开启

```bash
python3 "$CLI" agent-status --vault "$VAULT"
python3 "$CLI" daily-schedule-status --vault "$VAULT"
launchctl print "gui/$(id -u)/com.memento.remember-agent-v1"
launchctl print "gui/$(id -u)/com.memento.remember-agent-v1-schedule"
```

- [x] Agent 总 gate 为 enabled。
- [x] 统一计划为 enabled，配置为本地 21:00。
- [x] 两个 Re:member LaunchAgent 已由当前用户 bootstrap。
- [x] Keychain 中的 Key 可被当前用户读取，且未出现在日志或命令行参数。
- [ ] Chrome 已重新加载 v0.9.0 扩展并授权真实 Vault。

### 5.3 真实 DeepSeek

本节专门验收发行包中的已安装 runtime。第 4.4 节的工作区隔离合成通过不能代替本节。

- [x] 先前已安装 runtime plan `fcdf` 的独立历史账本已保留：两日长期沉淀案 11 calls / 26,647 Token / $0.003545598；`original_only` 撤回案 6 calls / 11,829 Token / $0.001967621；合计 17 calls / 38,476 Token / $0.005513219。
- [x] 当前最终已安装 runtime plan `2d964129…` 已使用真实 DeepSeek 通过两个冻结隔离合成 case，该账本不引用工作区或先前安装运行数字。
- [x] 两日长期沉淀案：11 calls / 26,544 Token / $0.003468603。
- [x] `original_only` 撤回案：6 calls / 11,826 Token / $0.001972841。
- [x] 当前已安装 runtime 合计：17 calls / 38,370 Token / $0.005441444，`invalid_action=0`，结果通过。
- [x] plan `2d964129…` 临时目录已清理，两个合成来源 hash 前后不变。
- [x] 真实失败记录在已知 Schema 失败的有界重试与 `no_receipts` 覆盖闸门下未产生第三次调用或部分日级提交。这只是真实失败路径的安全验证，不代表真实用户日链路完成。
- [ ] 真实用户 Vault 的逐条整理完成一次真实 API 调用。
- [ ] 真实用户 Vault 的日级归并完成一次真实 API 调用。
- [ ] 若材料触发长期判断，Agent V1 完成或明确 finish；未触发时记录 material gate 证据。
- [ ] usage 只记录允许字段；Provider request/response 正文和隐藏 reasoning 未持久化。
- [ ] 真实测试所用模型、thinking、时间、终态和成本口径已记录。

## 6. 21:00 与 08:00 调度验收

### 6.1 21:00

- [ ] 在 21:00 前确认总 gate、schedule、Keychain 和 launchd 均有效。
- [ ] 等待真实 calendar event，期间不手动执行 tick。
- [ ] 观察 schedule runner 日志与公开 report。
- [ ] report 为 `completed`，并记录 `runner_status`；或记录明确、可复现的未执行原因。
- [ ] 重启 Chrome 后读取当日最新 home projection。

### 6.2 08:00

- [ ] 前一天当前 heads 与 bundle manifest 一致、且 Review 精确绑定并 hash 匹配时，08:00 返回 `not_due`。
- [ ] 在隔离 Vault 分别制造晚到记录、source edit 和逐条失败；即使旧 bundle + Review 有效，08:00 也只恢复昨天。
- [ ] 昨天无 bundle 但有精确当前输入的可信 daily `no_change` 时，08:00 返回 `not_due`。
- [ ] 昨天保留旧 bundle 且新输入为可信 daily `no_change` 时，Review 仍须精确绑定且 hash 有效；缺失/不匹配时继续恢复。
- [ ] 在隔离 Vault 制造“bundle 已提交、Review 缺失/不匹配”状态，08:00 只恢复昨天。
- [ ] 不创建更早 backlog，不为今天提前执行 scheduled 任务。
- [ ] 错过时段后才开启 schedule，结果为 `not_due`，不追补旧时段。

受控修改系统时间或 LaunchAgent 日历只能在隔离环境执行；如用手动 `daily-schedule-tick` 替代，只能证明调度核心，不能宣称真实 launchd 日历已通过。

## 7. Chrome 人工验收

- [ ] 首页上半部显示当前认知地景，下半部显示今天的整理记录。
- [ ] 峰、点、关系和记录的 hover/click 不出现漂移、残留框或越界图标。
- [ ] 点击对象打开统一侧栏，形成链路顺序可读。
- [ ] 地景与等价列表表达同一批正式对象。
- [ ] 原文未在首页投影中预载；点击记录后才读取并通过 hash 校验。
- [ ] 今日无记录、无长期理解、Provider 失败、投影失败和目录权限失效均有专门状态。
- [ ] projection 缺失/损坏时回退旧记录主页，恢复后可重新进入认知主页。
- [ ] 回执确认/完整编辑/仅保留原文、可用记忆与关系编辑/删除、长期理解修改/删除入口均可到达，并有成功、冲突与失败状态。
- [ ] “归并今天”在等待时保留上一版可信地景；完成后刷新投影，失败时不丢原文。
- [ ] 复制给 AI、每日总结、归档、设置等旧能力仍可到达。
- [ ] 1440 × 900、1920 × 1080 及实际常用桌面尺寸完成截图核对。

## 8. 迁移、回滚与数据对账

- [ ] `cognitive-migration-status` 生成迁移前盘点。
- [ ] `cognitive-migration-backfill` 只创建 record sidecar/index，0 Provider 调用。
- [ ] 指定日期与全量回填均幂等。
- [ ] 迁移前后原始日记、Daily Reviews、Agent V1 memories/actions/profile SHA-256 一致。
- [ ] `projection-rebuild` 不调用 Provider，且可从正式对象恢复主页。
- [ ] 关闭 schedule 后不再自动进入日流程；关闭总 gate 后 record/daily Worker fail-closed。
- [ ] 升级失败可恢复旧运行时和 Dashboard；用户数据不回滚到旧字节。
- [ ] 卸载后原始记录、附件、Reviews、认知状态和 Agent V1 数据仍存在。

## 9. 发布记录

当前证据记录如下；未完成项保持“待验收”，不以自动测试或隔离合成结果代替：

| 项目 | 结果 |
|---|---|
| Git commit | 实现冻结 `797a5c4`；最终 docs-only 发行 commit 由 `v0.9.0` tag、ZIP comment 中的完整 commit SHA 与外部 `.sha256` 绑定 |
| 发行版本 | v0.9.0 |
| 自动测试 | 实现冻结 605/605；Node 与 shell 门禁通过 |
| Release ZIP / SHA-256 | 实现冻结候选 ZIP SHA-256：`84ceb8ff8aa7f54f803fe06b7bc45bd1401dca09a11d7ac62579ed1643e41c92`；最终 docs-only 发行物待按下方外部绑定规则生成 |
| 真机安装 | runtime/Chrome 89/89 受管文件与 `797a5c4` 字节一致；总 gate、schedule、三个 plist 与当前用户私有权限已校验 |
| 真实 DeepSeek | 工作区 v4：17 calls / 38,453 Token / $0.009711375；历史安装 plan `fcdf`：17 calls / 38,476 Token / $0.005513219；当前最终安装 plan `2d964129…`：17 calls / 38,370 Token / $0.005441444，`invalid_action=0`；真实用户 Vault “逐条整理 → 日级归并”待验收 |
| Chrome 人工验收 | 待实测 |
| 21:00 日历触发 | 待实测 |
| 08:00 恢复 | 待实测 |
| 数据对账 | 安装受管文件 89/89 已对账；当前隔离合成临时目录已清理且来源 hash 前后不变；真实用户 Vault 全链路对账待验收 |
| 已知限制 | 隔离合成未读取真实用户 Vault；Chrome 人工交互、真实用户日链路和自然 21:00/08:00 唤醒尚未通过 |

最终 docs-only 发行 commit 的 hash 不能写进生成它的同一份文档。提交完成后，以指向该完整 commit 的 `v0.9.0` tag、ZIP comment 中同一完整 commit SHA，以及 ZIP 外部 `.sha256` 三者共同绑定最终发行物；候选 ZIP 的 `84ceb8…` 只作为实现冻结证据，不冒充后续 docs-only 最终发行物。

所有阻塞项关闭、结果有证据、发布口径无夸大后，才可冻结 v0.9.0。
