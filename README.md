# Memento

> Memento 不是另一个笔记 App，而是一层覆盖 macOS 当前窗口的个人 Context 记录层。

Memento 以 **AI friendly、轻量本地化、0 摩擦和模块化**为产品哲学，把搜索、聊天、文档、屏幕和声音里的即时想法，在原场景保存为本地、可追溯、可被 AI 回看的个人事实。

## 在线体验 · Memento 3.0

[打开 Memento 3.0 产品故事与可操作演示](https://luke20001024.github.io/Memento/Memento-3.0.html?v=20260720)

- [阅读产品命题：一种轻度理解](https://luke20001024.github.io/Memento/Memento-Product-Thesis.html)
- [查看产品演进规划](docs/PRODUCT_ROADMAP.md)

进入封面后，点击任意位置、滚动或按键即可开始。

3.0 从散落在不同窗口里的意图出发，沿着三个问题展开：意图在哪里发生，如何在用户允许的边界内被承接，以及怎样沉淀为长期 Context。故事最后在 **Re:member** 收束——让新的记录不断补充、修正已有理解，逐渐拼出一个持续变化的人。Re:member 属于 Memento 的最后一层，不是独立产品。

## 为什么存在

### 意图在每个窗口发生

搜索框里的问题、聊天中的表达、文档与代码里的判断、屏幕上正在关注的内容，以及随口说出的想法，都在暴露一个人此刻想理解、表达、判断或继续做什么。问题不是缺少输入框，而是这些意图分散、易逝，还没有成为同一个人的连续 Context。

### 在原场景留下证据

Memento 不要求切到另一个 App 重新复述，也不持续监听所有窗口。用户在意图发生处主动触发记录，通过文字、备注、标签、截图与 OCR、语音和每日第一帧保留表达与现场。多模态不是为了扩大监控范围，而是为了用更低摩擦保存更完整的证据。

### 事实先沉淀，理解再发生

不同入口的记录汇入同一个本地事实层，保留时间、来源和原始资产。AI Review 在事实之上提出可核对、可删除、可重建的解释；用户拥有原始事实，并保留补充与修正 AI 理解的权利。

## 产品哲学

Memento 用四条原则约束同一件事：让个人 Context 对 AI 可理解、对用户可掌控，并且在日常工作里足够轻。

| 原则 | 产品约束 |
|---|---|
| **AI friendly** | 让事实可读，也让理解可校准。记录保留开放文本、时间、来源与原始资产；事实、AI 解释和用户反馈彼此分层，能够持续读取、引用与重建。 |
| **轻量本地化** | 原始事实先属于用户。系统优先使用 macOS 原生能力、本地 Markdown 与原始文件，不要求账号或常驻云端；联网只发生在用户明确启用的能力上。 |
| **0 摩擦** | 0 摩擦不等于 0 确认。不要求离开当前窗口、切换 App 或重新复述；一次主动触发完成记录，同时保留必要的授权与控制。 |
| **模块化** | 采集、事实、Review、回看与反馈彼此解耦。文字、截图、语音和每日第一帧可以按条件启用；单个模块停用或替换，不影响已有事实。 |

四条原则形成一条约束链：以接近 0 摩擦的方式留下 AI friendly 的 Context，以轻量本地化守住数据边界，再用模块化持续扩展能力。

## 当前产品闭环

`原窗口主动记录 → 本地按日事实 → Daily Review → 每日总结回看`

1. **记录**：不离开当前 App，通过 macOS Service 留下文字、截图或语音。
2. **落档**：正文进入按日 Markdown，截图、照片和录音保留原件。
3. **在场**：当天第一次真实记录后，可主动拍下一张“每日第一帧”，补充一次时间与天气。
4. **理解**：启用后，Codex 基于目标日文本生成 Daily Review，不改写原始记录。
5. **回看**：Obsidian 负责检索与编辑；Chrome 新标签页负责轻量浏览、复制给 AI 和进入每日总结。

当前已经形成**记录闭环**：内容能留下、找到、整理并回看。下一步要补的是**理解闭环**：让“准确 / 有误读 / 值得记住 / 补充 Context”被独立保存，并真正影响后续 Review。

## 产品由两层组成

### 记录层

基础安装提供 4 个 macOS Service；语音在系统与本地编译条件满足时成为第 5 个。

| 能力 | 触发与结果 |
|---|---|
| 直接记录 | 选中文字后原样写入当天 Markdown，并保留时间、星期和来源 App |
| 加备注 | 正文与用户补充的判断一起保存；取消则不写入 |
| 选标签 | 使用“行动线索 / 灵感 / 下次再读”帮助检索，没有完成态、优先级或催办 |
| 截图 | 调起系统选区；保存原始 PNG，OCR 超过阈值时同时写入可检索文本 |
| 语音 | 用户主动开始与停止；保留原始 M4A，并尝试 Apple 本地转写；要求 macOS 26 或更高版本 |

当前这台 Mac 的绑定为 `⌃1` 至 `⌃5`。安装器不会替其他设备设置系统快捷键。

### 回看层

- **Obsidian**：`Memento.md` 是首页，原始 Markdown 可继续搜索、关联与编辑。
- **Chrome 新标签页**：让今天的记录在任务切换的自然间隙出现，不再增加一个需要记住的新入口。
- **每日总结**：以所有有原始记录的日期为底表，组合每日第一帧、Daily Review 与处理状态。已有总结从本机缓存直接出现，打开侧栏后只在后台核对所选月份，单个文件完成便更新对应日期。
- **HTML 归档**：可把 AI 生成的单文件 HTML 手动放入归档抽屉；当前仍是半自动流程。

每日总结状态：

| 状态 | 含义 |
|---|---|
| 待总结 | 有原始记录，但还没有可用 Review，或本轮正在生成 |
| 总结已更新 | Review 与当前原始记录一致 |
| 记录有更新 | Review 生成后，原始记录又发生变化 |
| 生成失败 | 一次真实生成、写入或校验已经开始，并实际失败 |

没有状态文件、还没到运行时间或当天没有记录，都不等于生成失败。

## 每日第一帧

每日第一帧不是为了多保存一张照片，而是让记录里留下“正在生活和判断的我”。文字说明发生了什么，照片把抽象日期重新连接到具体的人、状态和时刻，让时间线从资料库变成一个人持续生活的痕迹。

它是一项独立的在场感能力：

- 仅在当天第一次记录真实落盘后邀请一次。
- 用户可以拍摄或跳过，当天不重复打扰。
- 只有照片成功后才补充一次拍摄时间与天气。
- 成功落档保持静默，不再额外发送延迟出现的系统通知；失败仍会提示。
- 照片保存在本地；当前 Daily Review 不直接理解照片。

## 产品边界

- **不是另一个输入 App**：入口仍属于搜索、聊天、文档和当前窗口，Memento 只负责跨入口承接。
- **不是后台监控系统**：记录由用户主动触发或明确授权，不持续开启摄像头、麦克风或屏幕。
- **不是待办清理器**：行动线索可以被记录，但记录本身就是价值，不制造“未完成”压力。
- **不是隐藏的人格判断**：AI Review 是可核对的派生解释，不做人脸识别，也不根据单张照片推断情绪、健康或动机。

隐私边界：

- 每日记录、Dashboard、语音转写和原始资产保存在本地。
- Dashboard 会在扩展独立的本机 IndexedDB 中保存约 480 px 的 WebP 照片缩略图，供后续新标签页复用；持久缓存查询最多等待 120 ms，超时即回退读取原图，不让缓存拖慢照片显示。原始照片仍只保存在用户选择的数据目录中，缩略图不会上传。
- Dashboard 也会保存与当前授权目录绑定的 Daily Review 本机快照，用来立即恢复已有总结；切换数据目录时会同步清除旧目录缓存，核对过程只读取用户当前选择的月份。
- Dashboard 核心功能不主动联网；HTML 归档预览会先移除脚本、事件属性、外链、刷新和嵌入内容，再由严格 sandbox CSP 阻断远程资源与连接。预览保留静态 HTML/CSS、页内锚点和 `details/summary`。
- 安装器将 Vault 目录收紧为仅当前用户可访问（目录 `0700`、文件 `0600`）；并发图片、录音和截图使用唯一文件名及原子提交，不再互相覆盖。
- 启用 Daily Review 时，目标日文本会交给已配置的 Codex 模型。
- 照片成功后才向 Open-Meteo 查询一次天气；位置先降到约 11 km 粒度，经纬度不落盘。

## 快速开始

基础记录要求：macOS、`python3` 与系统自带的 `plutil`。全新的 Mac 如果没有 `python3`，先运行 `xcode-select --install` 安装 Apple Command Line Tools。截图 OCR 与每日第一帧需要可用的 `swiftc`；语音还要求 macOS 26 或更高版本。

**Obsidian、Codex 都不是基础记录的前置条件。** 不安装它们时，文字、截图和其他原始文件仍会直接保存在 `~/AISecretary/`。Obsidian 只提供更方便的本地搜索与编辑；Codex 只用于可选的 Daily Review。

### 让 AI 帮你安装

把本仓库链接和下面这段话交给具备网页与本机终端能力的 AI。完整的机器可读安装合同见 [`INSTALL_WITH_AI.md`](INSTALL_WITH_AI.md)。

```text
请阅读 https://github.com/Luke20001024/Memento 和仓库根目录的 INSTALL_WITH_AI.md，
在我的 Mac 上安装最新版 Memento。先检查系统与依赖，只启用基础记录和 Chrome Dashboard；
Obsidian 与 Codex 都作为可选能力，不要强制安装。需要系统权限、覆盖已有组件或删除数据时，
必须先征求我确认。安装后写入一条测试记录，确认它保存在 ~/AISecretary，
并验证 Chrome Dashboard 能够读取。最后告诉我哪些基础能力已可用，哪些增强能力尚未启用。
```

普通聊天 AI 可以把它转成逐步指导；拥有本机终端权限的 Agent 可以执行命令和验收，但快捷键、Chrome 开发者模式、目录、相机与麦克风授权仍由用户亲自确认。

### 下载发行包

[下载最新 macOS 安装源码包](https://github.com/Luke20001024/Memento/releases/latest/download/Memento-macOS.zip) · [SHA-256](https://github.com/Luke20001024/Memento/releases/latest/download/Memento-macOS.zip.sha256)

当前版本：**v0.8.9**。发行包包含安装器、Dashboard、Daily Review 协议、AI 安装指南及本地 Swift 源码；安装时会在当前 Mac 上编译需要的原生组件。

```bash
unzip Memento-macOS.zip
cd Memento-v0.8.9
chmod +x install_aisecretary.sh uninstall_aisecretary.sh
./install_aisecretary.sh
```

### 从仓库安装

```bash
chmod +x install_aisecretary.sh uninstall_aisecretary.sh
./install_aisecretary.sh
```

基础安装后：

1. 在“系统设置 → 键盘 → 键盘快捷键 → 服务”中绑定需要的 Service。
2. Chrome 打开 `chrome://extensions`，启用开发者模式，加载 `~/AISecretary/.chrome-newtab/`。
3. 打开新标签页，授权数据目录 `~/AISecretary`。

可选增强：

- **Obsidian**：安装后选择“打开本地文件夹作为仓库”，选择 `~/AISecretary`。不安装也不影响记录与 Chrome Dashboard。
- **Codex Daily Review**：只在需要 AI 每日总结时配置；安装器不会默认创建自动任务，未启用时原始记录保持可用。

```bash
./uninstall_aisecretary.sh
```

卸载默认不删除记录、资产和已生成 Review，只移除执行脚本、App、Dashboard、Review worker 及带 Memento 归属标记的 Workflow；同名但不属于 Memento 的 Service 会保留。产品名是 **Memento**；为兼容既有数据，内部目录、脚本和 Workflow 仍保留技术名 `AISecretary`。

---

## 面向 Codex / AI

### 阅读顺序

1. 本 README：产品语义、边界与已实现能力。
2. [`INSTALL_WITH_AI.md`](INSTALL_WITH_AI.md)：给安装 Agent 的依赖、权限、降级和验收合同。
3. [`daily-review/DAILY_REVIEW.md`](daily-review/DAILY_REVIEW.md)：Daily Review 唯一机器执行协议。
4. [`daily-review/README.md`](daily-review/README.md)：状态查看、手动运行与排障。
5. [`chrome-newtab/README.md`](chrome-newtab/README.md)：Dashboard 和交互约定。
6. [`docs/Memento-cache-acceleration-design.md`](docs/Memento-cache-acceleration-design.md)：缓存、渐进加载与多标签页约束。
7. [`chrome-newtab/prompts.js`](chrome-newtab/prompts.js)：实际 Prompt 源；Daily Review 使用 `id: 'comprehensive'`。

不要复制第二份完整 Review Prompt。协议和 Prompt 必须各自只有一个事实源。

### 不可违反的约束

- 不修改原始 `YYYY-MM-DD.md`；安装升级只能补兼容元数据，不能重写正文。
- 单日 Review 只读取目标日记录、`comprehensive` Prompt，以及旧 Review 中的 `## 我的补充`。
- 不读取其他日期补事实，不联网补背景，不生成跨日观察。
- 不把模糊表达强制转成任务，不补 deadline、人名、优先级或项目背景。
- `## 我的补充` 必须原样保留。
- 先写同目录临时文件，校验通过后经 `commit_review.sh` 做基线 hash + 原子 CAS；生成期间若人工补充变化，必须冲突退出，不能覆盖正式 Review。
- 只有真实开始生成后才能写 `pending`；只有真实运行失败才能写 `failed`。
- 只有原始内容持久化成功，记录入口才可以通知成功并触发每日第一帧。

### 数据与状态契约

| 路径 | 角色 |
|---|---|
| `~/AISecretary/YYYY-MM-DD.md` | 原始事实层 |
| `~/AISecretary/assets/` | 截图、照片与录音原件 |
| `~/AISecretary/Reviews/Daily/YYYY-MM-DD.md` | 可重建的日级 AI Review |
| `~/AISecretary/.review/status/YYYY-MM-DD.json` | 最近一次真实 Review 执行状态 |
| `~/AISecretary/.chrome-newtab/` | 已安装 Dashboard |
| `~/AISecretary/.archives/` | 用户主动放入的 HTML 归档 |

Review worker 与 Dashboard 都以 `source_hash`、整个 Prompt 文件的 `prompt_hash`、`source_mock` 和严格结构校验共同判断是否可复用；截断文件、空章节、错误来源或旧 Prompt 都会进入按需重建。Dashboard 展示“未生成 / 最新 / 记录有更新 / 需重建 / 失败”；状态缺失不能推断失败。

### Daily Review 入口

```bash
# 上午：只复核昨天
~/AISecretary/.review/review_cycle.sh previous

# 晚间：按“昨天 → 今天”检查
~/AISecretary/.review/review_cycle.sh

# 状态与结果校验
~/AISecretary/.review/review_status.sh today
~/AISecretary/.review/verify_review.sh YYYY-MM-DD
```

`review_cycle.sh` 只做确定性检查，不调用模型。安装器会铺设 Review 协议，但不会替新机器创建 Codex 自动任务；08:00 与 21:00 调度需要单独配置。

### 修改后的最低验证

```bash
node tests/test_daily_summary_library.js
node tests/test_photo_library.js
node tests/test_photo_asset_loader.js
node tests/test_photo_viewport_loader.js
node tests/test_photo_persistent_cache.js
node tests/test_directory_access_library.js
node tests/test_dashboard_cache_library.js
node tests/test_dashboard_operations_library.js
node tests/test_today_first_refresh.js
node tests/test_archive_security.js
node tests/test_archive_read_pool.js
node tests/test_archive_fast_path.js
node tests/test_optional_read_pool.js
bash tests/test_daily_review_recovery.sh
bash tests/test_capture_lifecycle.sh
bash tests/test_daily_snapshot.sh
bash tests/test_photo_drawer.sh
bash tests/test_record_dashboard.sh
bash tests/test_installation_contract.sh
bash tests/test_release_contract.sh
```

修改安装器后，还要确认源码与 `~/AISecretary/.scripts/`、`.review/`、`.chrome-newtab/` 中的已安装副本一致。

## 现状与后续

### 已知限制

- 自动循环只检查昨天与今天，不会批量回填更早历史记录。
- Dashboard 在页面加载时读取文件，没有实时文件监听；新 Review 生成后需要刷新。
- 每日第一帧失败、跳过或权限拒绝后当天不重试。
- 语音依赖 macOS 26、`swiftc` 与打包源码，部分非原生 App 不支持从 Services 菜单启动。
- HTML 归档仍需“复制给 AI → 保存 HTML → 手动放入归档库”。
- 当前没有多设备同步、原生多模态 Review 或反馈学习。

### TODO

近期先补逻辑漏洞：

- 完成 08:00 晨间复核的真实定时验证，并支持扫描最近 N 天的漏跑记录。
- 增加 Dashboard 手动刷新，或明确提示重新加载后读取最新文件。
- 为 Daily Review 增加“准确 / 有误读 / 值得记住 / 补充 Context”。
- 将用户反馈与 AI 结果分开保存，支持查看、撤回与删除，并让确认 Context 参与后续 Review。

后续体验：

- 支持照片原比例全屏、键盘切换与多端自适应。
- 增加连续 30 天横向照片时间线，并回到当天原始记录。
- 结合照片、天气、记录和 Review 生成可校准的月度反思。
- 将 HTML 生成与归档升级为可确认的一键流程。

### v0.8.9 · 发布一致性与 AI 安装入口 · 2026-07-22

- 将 7 月 18 日后已合入 `main` 的缓存恢复、每日总结渐进更新与热力条日期导航正式纳入下载包。
- 版本号、Dashboard 页脚、安装目录、Git 标签和 Release ZIP 统一为 `v0.8.9`，避免同名版本包含不同代码。
- 增加 `INSTALL_WITH_AI.md` 与可复制提示词，让用户可以把仓库链接交给 AI 完成环境检查、安装和验收。
- 明确基础记录不依赖 Obsidian 或 Codex：前者是本地搜索编辑器，后者只用于可选 Daily Review。
- 补充 `python3` / Apple Command Line Tools 的真实安装前置，并修正随产品演进失效的回归断言。

### 最近改动 · 2026-07-14

- 重构产品叙事为“为什么 → 记录 → 回看 → 理解 → 架构 → 闭环 → 方向”。
- 为 5 个 Service 增加可操作微型演示，并明确语音的条件启用边界。
- 将每日第一帧并入记录后的在场感能力，补充触发、天气与打扰边界。
- Service 入口与产品演示使用“行动线索”；数据层继续兼容历史 `#TODO`，但已移除勾选、完成和催办语义。
- 区分当前已成立的记录闭环与尚未成立的理解闭环。

### 稳定性修复 · 2026-07-17

- Chrome Dashboard 增加授权后的 last-known-good 快速缓存；暖启动先显示上次完整快照。每个新标签页随后在完整历史锁外直接点读今天的单个 Markdown，立即合并新增记录并开放“今天”复制；历史仍在后台协调核对，周 / 月等待完整结果。核心快照与目录句柄保存在扩展自己的本机 IndexedDB，最多复制 5 MiB 根目录日报正文；切换目录或明确撤权会令旧快照失效。
- 根日报改为固定 4 路读取，今天优先；单个 Chrome 文件请求卡住只占一个槽，其余文件继续。文件被 Obsidian 等外部编辑器刚好替换时，`NotReadableError` / `NotFoundError` 只对该文件从父目录重新取得句柄并重试一次，不做整轮重试。
- 在 Web Locks 与 BroadcastChannel 可用时，同一时刻只允许一个 Memento 标签页执行完整历史扫描；其他页面仍会各自完成一次锁外的今日点读，但不会排队或自动接管全量扫描。扫描页未发布完整结果时，当前页仍能显示最新的今天和缓存历史；部分结果不会覆盖完整缓存，旧目录任务也不能在切换后回写。
- Chrome 目录恢复增加阶段提示和统一选择代次；自动恢复、跨页同步和手动选目录不能彼此晚到覆盖。仅 IndexedDB 授权记录读写保留有界保护，保存失败不再阻断本次已授权目录，单个坏文件也不会拖垮整页。
- Chrome 文件扫描不再给不可取消的本地读取套硬超时；偶发慢响应不会被误判为已取消。主记录与缓存先显示，Review/状态/Prompt 随后补充；归档写入与删除继续跨标签页串行。
- 目录选择提交与归档写入共用跨标签页 Web Lock；归档在锁内再次核对当前目录，同名文件自动改名。预览改为脚本零执行、外链零放行的 DOM 安全化路径。
- 每日记录、资产复制和元数据升级改为锁内原子提交；修复同秒覆盖、迁移丢记录、失败后伪成功及已提交链接被清理的问题。
- Daily Review 同时校验源记录、Prompt 版本和完整输出合同；提交使用按日锁与原子 CAS，继续把“我的补充”视为用户拥有的原样区域。
- 语音和每日第一帧增加有界超时、整组 helper 进程终止和私密恢复路径；成功的每日第一帧不再延迟弹出重复通知。
- 安装、升级和卸载增加归属标记、staging/回滚、幂等与保留用户数据的合同测试；升级会清理 owned 旧截图 LaunchAgent，App 复用使用与安装路径无关的内容 hash。

### v0.8.8 · Dashboard 性能优化 · 2026-07-18

- 暖启动改为真正的 cache-first：授权通过后，有效快照是首个记录画面，不再先闪过空 waiting；快照得到一次绘制机会前，后台根目录遍历不会启动。自动恢复期间快照校验与权限检查并行；快速命中只查一次权限。若缓存校验 250 ms 内没有结论，实时读取会独立启动，不取消底层请求；迟到缓存显示前会再核对权限。今日点读仍 pending 时，主按钮不再变成无限期加载状态：用户可复制当前显示版本，剪贴板会携带“仍在核对”的数据状态，点读成功后按钮自动升级为最新状态。
- 每个新标签页在完整历史 Web Lock 之外直接读取 `${scanDate}.md`，无需先枚举根目录；新增记录会先合并进已显示缓存。今日点读完成后才探测后台历史锁，leader 的完整扫描直接复用已读 today 文件，follower 也能更新今天而不等待另一页面发布全量快照。
- 每日总结先立即显示文字卡片，只读取滚动区域附近（前后 600 px）的照片；继续滚动时才补载后面的卡片。照片读取、缩略与解码共用当前 Tab 的 3 路受控并发，完成一张立即显示，不再一次性读取整月原图。
- 照片首次读出后生成宽约 480 px、质量 72% 的 WebP 衍生预览；原始照片不修改、不复制。合格缩略图写入独立本机 IndexedDB，按“目录 binding + 文件名 + 缩略规格”隔离，最多 96 项、合计 32 MiB、单项 512 KiB。持久缓存查询有 120 ms 的决策窗口，超时立即按 miss 回退实时原图；浏览器不支持缩略能力或单张处理失败时也自动退回原图，且不会把原图写入缩略缓存。
- 同一照片的重叠请求会合并，当前 Tab 最多保留 32 个临时 object URL。首次打开仍需读取并转换原图，后续新标签页可直接命中持久缩略图；`pagehide` 只回收当前页面的 object URL，不删除 IndexedDB 中的衍生缩略图，也不上传任何照片。
- 正常采集为每张 asset 使用唯一文件名，并按不可变资源缓存。若要替换一张图片，应写入新文件名并更新 Markdown 引用；同名原位替换不属于常规写入路径，缓存命中也不承诺重新读取源文件做验证。
- Daily Review 与运行状态共用 3 路读取池，Prompt 同时读取；全部真实请求收敛后一次提交。文字补齐只更新 Review 区，不重建照片，切换目录会停止旧目录尚未启动的读取。
- 归档侧栏改为缓存先显：同 Tab 再打开不重复扫描，新 Tab 的归档数量与主记录快照在同一个 IndexedDB 事务中恢复，并在侧栏首次绘制前显示；不访问 `.archives`，也不为徽标另做一次查询。打开侧栏时先显示仅含文件名、标题和修改时间的本机索引；后台以 3 路受控并发核对文件，未变化的标题不再读取正文，新增或变化的标题只扫描 HTML 前 256 KiB。单个读取永久 pending 也不会挡住其余条目出现。
- 无快速缓存时只在“今天”完成和最终扫描完成时更新主界面，不再为每一个历史文件重复解析和整页渲染。根日报的 4 路读取、完整快照与目录安全校验保持不变。
