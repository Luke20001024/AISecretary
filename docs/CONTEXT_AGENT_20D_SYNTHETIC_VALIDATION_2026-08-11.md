# Memento Context Agent · 20 天富数据与主动理解验证

日期：2026-08-11
状态：工作区实现、真实本机安装、自动化回归与隔离 DeepSeek 验证已完成；Chrome 最终视觉仍需人工重载后确认

## 结论

- 原有右侧“理解”抽屉已升级为“关于我 · Re:member”，没有新建独立画像后台。
- 用户可以主动问“现在，你怎么看我？”或输入具体问题；结果分为“现在 / 变化 / 记忆”，并展示时间范围、局部理解、证据、反例和未知边界。
- Reflection 只生成可追溯的“当前理解”，不会自动写入长期 Context。长期记忆仍由独立的用户确认链路控制。
- 20 天富数据夹具包含 20 个连续自然日、每天 10 条、共 200 条合成产品经理记录。
- 8 次真实 DeepSeek V4 Pro 调用暴露并验证了三个关键边界：变化问题不能用稳定偏好代答、隐含变化不能冒充明确变化、用户纠正必须压过模型重复输出。
- 本轮只能证明合同、证据、安装与合成场景行为，不能证明对真实用户的画像准确率。

## 20 天富数据

工作区固定夹具：

`context-agent/eval/scenarios/product-manager-20d/`

- 固定日期：2026-07-14 至 2026-08-02。
- 每天 10 条，共 200 条。
- 每日同时包含产品议题、指标、研究、研发、设计、竞品、行政噪声、风险和跨日行为信号。
- `ground-truth.json` 记录可验证的跨日模式、噪声、变化和安全边界。
- `generate_rich_fixture.py` 可把相同夹具平移到指定结束日期，并拒绝覆盖没有合成标记的真实日记。

隔离体验 Vault：

`/Users/luke/Memento-Context-Agent-Synthetic-20D-Rich`

- 日期已平移为 2026-07-23 至 2026-08-11，确保 14 天自然日窗口能在当前日期直接运行。
- 文件夹权限为 `0700`，日级文件为 `0600`。
- 初始工程验证阶段，假数据没有写入 `/Users/luke/AISecretary`；后续经用户明确要求，已增加下述可回滚临时测试层。

## 主动理解链路

1. Dashboard 在本地写入一个严格校验的 request JSON。
2. macOS LaunchAgent 只在 request 目录发生变化时唤醒本地 Worker。
3. Worker 从 macOS 钥匙串读取 DeepSeek Key；Key 不进入 Dashboard、request、日志或模型输入。
4. Worker读取最近 14 个自然日内有记录的日期、有效的已确认 Context，以及经过字节哈希绑定的用户反馈。
5. 模型返回后，本地再次校验逐行原文、来源哈希、Context 引用、敏感推断、固定人格标签和变化证据。
6. Dashboard 只展示通过校验的 response；反馈只影响下一次主动理解，不直接修改长期 Context。

浏览器本身没有 Provider 网络与凭据路径。

## 真实 DeepSeek V4 Pro 结果

全部请求只使用隔离合成数据。

| 次序 | 问题 / 目的 | 结果 | 处置 | Tokens | 估算成本 USD |
|---:|---|---|---|---:|---:|
| 1 | 我做产品判断时有什么规律？ | ready，3 条 observation | 合同与证据通过 | 9,239 | 0.004347390 |
| 2 | 最近两周，我发生了什么变化？ | 错把 3 条稳定 observation 当成变化回答 | 增加变化意图门，只允许 change / tension 或证据不足 | 9,259 | 0.004364790 |
| 3 | 现在，你怎么看我？ | ready，3 条 observation | 作为隔离 Vault 当前默认展示答案 | 9,260 | 0.004145666 |
| 4 | 拒绝一条理解后再次询问 | 模型重复了被拒绝内容，旧实现记录 feedback error | 增加确定性反馈优先与安全降级 | 9,296 | 0.000734454 |
| 5 | 修复意图门后再次问变化 | 模型把相邻但兼容的记录写成 change | 增加显式变化词、张力词和新旧证据顺序门 | 8,878 | 0.003977205 |
| 6 | 加入显式门后再次问变化 | 模型选择 insufficient_evidence，但附带多余 reflection 对象 | 发现并修复 Provider 容错边界 | 8,702 | 0.003693208 |
| 7 | 最终变化用例 | insufficient_evidence，0 条 insight，无错误 | 通过；不再虚构变化 | 8,702 | 0.000104168 |
| 8 | 最终用户拒绝用例 | insufficient_evidence，0 条 insight，无错误 | 通过；用户校准优先于模型 | 9,307 | 0.000744024 |

汇总：

- 真实调用：8 次。
- Prompt tokens：68,435。
- Completion tokens：4,208。
- Total tokens：72,643。
- Cache hit tokens：26,240。
- Cache miss tokens：42,195。
- 已知估算成本：`$0.022110905`。
- usage 缺失：0 次。

成本由运行时内置的 2026-08-09 DeepSeek 价格快照计算，不等同于 Provider 最终账单。第 7 次相同问题命中大量 Prompt Cache，因此明显便宜。

## 最终安全行为

- 宽泛问题只允许输出有范围的工作观察，不输出完整人格、能力等级或心理状态。
- observation 至少需要两个不同日期的逐字证据。
- change 必须同时具备较新的 evidence、较旧的 counterevidence，且至少一条原文包含明确变化表达。
- tension 必须同时有两侧证据，且至少一条原文包含明确冲突或张力表达。
- 如果模型忽略用户的 reject / edit / scope / changed 反馈，最终输出降级为证据不足，不重试、不缓存违规正文，但保留 usage。
- Provider 只有在顶层字段、版本和 status 均严格正确，且 `insufficient_evidence` 附带的是 JSON object 时，才会丢弃该多余对象并归一为 `reflection=null`；未知字段、错版本、字符串、数组和数字仍被拒绝。
- 该显式变化门是保守设计，可能漏掉没有明确措辞的隐含变化；不会把这种漏检包装成已证明的“不存在变化”。

## 自动化结果

- Python：44 / 44 通过。
- 离线 Context Agent eval：9 / 9 通过，0 次 Provider 调用。
- Node：Context library、Self Reflection library、Dashboard 合同均通过。
- Dashboard 总合同：12 组通过。
- 安全脚本：通过；仓库无 Key-shaped 值，浏览器无 Provider credential path。
- 隔离安装升级与数据保留合同：通过。
- `pyflakes`、Python 编译、JavaScript 语法、Shell 语法、`git diff --check`：通过。

## 本机安装状态

- 最终 runtime 与工作区 `context-agent/` 内容一致。
- 最终 Dashboard 与工作区 `chrome-newtab/` 内容一致。
- `com.memento.context-agent` LaunchAgent 已加载。
- macOS 钥匙串目标条目存在；验证只检查存在性，没有读取或回显 Key。
- 已安装 runtime 离线 eval：9 / 9 通过。
- 安装验收时，真实 Vault 的 6 份日级文件、assets、Reviews、Context、候选、决定、usage 和 self-query 用户状态在安装前后逐字节一致。
- 安装锁、stage 和临时 backup 残留为 0。
- 最终安装前快照：`/Users/luke/Memento-self-reflection-final-backup-5NfR54`。

## 隔离体验状态

隔离 Vault 当前保留 3 组通过最终合同的 request / response：

- “我做产品判断时有什么规律？”：ready。
- “现在，你怎么看我？”：ready，并保持为前端最新答案。
- “最近 14 天，我的产品判断方式发生了什么变化？”：insufficient_evidence。

修复前发现的问题及其 response 均移入：

`/Users/luke/Memento-Context-Agent-Synthetic-20D-Rich/.context-agent/test-artifacts/`

因此它们不会成为 Dashboard 当前答案，但仍可供工程复盘。

## 用户要求的真实 Vault 临时测试层

用户明确要求在当前 Chrome 已授权的真实 Vault 中直接体验。2026-08-11 已把同一套富数据以可回滚临时区块注入 `/Users/luke/AISecretary`：

- Fixture ID：`rvctx_20260811T040716Z_43156c`。
- 日期：2026-07-23 至 2026-08-11，共 20 天、200 条。
- 6 个既有日记只在原字节之后追加唯一 BEGIN / END 标记区块，原有前缀逐字节保持不变。
- 14 个缺失日期创建为临时合成文件。
- 每个区块都显示“合成测试，不代表真实记录”的提示。
- 注入前完整外部快照：`/Users/luke/Memento-real-vault-fixture-backup-xbLubs/AISecretary`。
- 精确 manifest：`/Users/luke/Memento-real-vault-fixture-backup-xbLubs/fixture-manifest.json`。
- 冲突感知回滚脚本：`/Users/luke/Memento-real-vault-fixture-backup-xbLubs/rollback_fixture.py`；dry run 已验证 20 / 20 可回滚。
- 注入没有创建 request、没有触发 Worker、没有新增 DeepSeek usage，其他 Agent 用户状态与备份一致。

Self Reflection 仍只读取最近 14 个自然日；20 天全部存在是为了同时验证窗口内理解和较早记录的时间边界。

## 尚未证明与下一步

- 尚未在本轮直接观察 Chrome 重载后的最终像素级界面、按钮点击和剪贴板行为。
- 本次临时数据注入没有调用 DeepSeek；真实 Vault 已保留注入前存在的 self-query 与 usage。点击新的主动理解会把界面披露范围内的真实记录与合成区块一起发送给 DeepSeek。
- 合成结果不能代表真实用户长期画像准确率，需要用户逐条判断“准确 / 有条件 / 已变化 / 不准确”。
- 反馈已进入下一次主动理解，但尚未接入 Daily Review，也不会自动修改长期记忆。
- LaunchAgent 只监听正式 Vault `/Users/luke/AISecretary`。隔离测试 Vault 已有预计算答案可看，但在其中新提问不会被自动 Worker 处理。
- 变化词法门需要继续用真实纠错样本评测漏检率。
