# Memento Visual Compass · Codex 风格调用协议

> 面向 Codex 的长期视觉上下文。后续为 Memento 制作海报、产品故事图、品牌页面、演示封面或其他意向视觉时，先读取本文件，再调用指定 Skill 和参考图。

## 1. 当前视觉系统状态

- 有效方向：3 套。
- 每套现有参考图：5 张，共 15 张。
- 当前参考图尺寸：`1536 × 1024`，横向 `3:2`。
- 默认展示环境：电脑横屏。
- 总览图：[Memento-Visual-Compass.png](Memento-Visual-Compass.png)
- 第 4 套 `Recursive Proof` 已退出当前交付系统。后续任务不要自动恢复或引用该方向，除非用户再次明确要求。

## 2. 产品语义上下文

Memento 是一位运行在电脑里的自动笔记与认知秘书。它接住正在发生的意图，让跨时间的记录逐渐形成可追溯、可修订的长期理解，再以可调用的个人记忆让每个 AI 都从同一个你开始。

视觉表达应优先传达这些感受：

- 记录发生得轻，不打断当前工作；
- 原始事实属于用户，来源始终能够找回；
- AI 的理解是可见、暂时、允许修订的；
- 用户拥有确认、限定、修改和删除的控制权；
- 产品最终价值来自旧 Context 的真实复用；
- 产品气质安静、克制、有人味，带有纸面档案与个人时间痕迹。

视觉上避免把 Memento 表达成传统笔记 App、待办清理器、后台监控系统、人格画像工具或通用 SaaS Dashboard。界面截图可以作为内容材料，整体气质仍需服从本视觉罗盘。

## 3. 指定 Skill

### 已安装 Skill

- 调用名：`$gc-minimal-zine-poster-v0-3`
- 版本：Minimal Zine Poster v0.3.1
- Skill 文件：已安装的 `$gc-minimal-zine-poster-v0-3` 的 `SKILL.md`
- 风格系统：该 Skill 的 `references/style-system.md`
- Prompt 编译器：该 Skill 的 `references/prompt-compiler.md`
- 变化引擎：该 Skill 的 `references/variation-engine.md`
- 质量检查：该 Skill 的 `references/quality-gate.md`

### 调用顺序

1. 读取本文件，确认产品目标和视觉方向。
2. 读取 `$gc-minimal-zine-poster-v0-3` 的 `SKILL.md`。
3. 至少读取 `style-system.md`、`prompt-compiler.md`、`variation-engine.md` 和 `quality-gate.md`。
4. 根据任务选择本文件中的一个方向。
5. 将该方向的 5 张现有图片作为 `reference image`，保存级别设为 `low`：学习视觉语法，不锁定具体物件或单张构图。
6. 生成全新的横屏画面；保持方向家族感，同时改变构图、焦点结构和文字分布。
7. 查看真实生成图并执行质量检查；偏离方向时收紧 Prompt 后重做一次。
8. 返回生成图、最终 Prompt、所选方向、Recipe 和简短解释。

当实际传入参考图时，使用 `referenced_image_paths`。不要只在 Prompt 里描述参考图而不传入文件。

## 4. 全局画布与生成规则

### 画布

- 默认：横屏 `3:2` 或 `16:10`。
- 电脑端产品故事、演示文稿和网页 Hero 优先使用横屏。
- 用户可自由指定其他比例；本系统不强制竖版 `3:5`。
- 参考分辨率：`1536 × 1024` 或更高。

### 共同视觉基因

- 纸张、扫描、复印、相纸或印刷颗粒必须可见。
- 画面保持较高留白，通常建议 `65%–85%`。
- 每张图聚焦一个主要视觉关系，避免堆叠大量物件。
- 平面扫描视角、漫射光、低到中等对比，避免强烈 3D Mockup。
- 字体与图形属于同一个印刷世界，可使用衬线、打字机、等宽、粗黑体、手写批注或编辑标记。
- 允许中文、英文、短句、引用、标签、注释和较长文字。不要为了遵循早期实验而限制为少量单词。
- 生图阶段的文字可作为排版意向；正式上线前再做准确文案与字体定稿。
- 后续复审不要仅因画面多出文字而重做；只有文字破坏构图、误导产品含义或用户明确要求准确排版时才处理。

### 共用语义词汇

后续可以自由选用下列语义，但无需把每套 5 张固定解释成逐张流程：

`CAPTURE / EVIDENCE / INTERPRET / CORRECT / RETURN`

对应的产品母题为：捕捉、留证、理解、校准、复用。

## 5. Direction 01 · Quiet Evidence / 安静证据

### 核心意象

一张被轻轻留下的纸、一枚仍然连着来源的档案签、一条进入新空白处的细线。Memento 在这个方向中像一个私密的纸面证据层，安静承接重要 Context，并保留回看与修订的余地。

### 设计风格

- 诗性纸感、档案编辑、微型信息物和大面积留白。
- 撕边纸条、档案签、校样线、透明纸、旧复印件、细箭头和局部标记。
- 焦点通常很小，放在画面三分区、边缘或明显偏离中心的位置。
- 可以出现一次暗色情绪转折，用来表达理解层的不确定性；整组主体仍以明亮纸面为基线。

### 设计调性

安静、克制、可信、私密、编辑感、可修订。它最适合作为 Memento 的默认品牌方向。

### 色彩调性

- 环境色：暖象牙白、旧纸米色、浅灰和炭黑。
- 主色锚：朱红 `#D64022`。
- 朱红承担标注、校准、连接和修订；避免扩张为大面积商业广告红。

### 适用场景

产品首页、安装引导、隐私与证据机制、Context 校准、品牌基础视觉、安静的功能说明。

### 参考图路径

- `docs/poster-intent/01-quiet-evidence/01-capture.png`
- `docs/poster-intent/01-quiet-evidence/02-evidence.png`
- `docs/poster-intent/01-quiet-evidence/03-interpret.png`
- `docs/poster-intent/01-quiet-evidence/04-correct.png`
- `docs/poster-intent/01-quiet-evidence/05-return.png`
- 联系表：[01-quiet-evidence-contact.jpg](review/01-quiet-evidence-contact.jpg)

### Prompt 方向块

```text
Use the supplied Quiet Evidence images only as low-preservation visual references. Create a new desktop-first landscape paper poster with warm ivory archival paper, large quiet negative space, one small evidence-like paper event, restrained serif or typewriter typography, xerox or letterpress defects, and vermilion #D64022 as the single saturated calibration mark. Preserve the private, trustworthy, editable mood. Do not copy any exact object layout or wording from the references.
```

## 6. Direction 02 · Living Mirror / 生长镜面

### 核心意象

几何碎片在空白中靠近、错位、覆盖、留下缺口并重新连接，像一面持续重组的镜子。它表现长期理解会随着新事实、反例与修正继续生长。

### 设计风格

- 至上主义几何、现代主义编辑、粗颗粒黑墨与套色偏移。
- 方块、圆形、圆孔、楔形、框线和方向性长条构成关系。
- 构图可以更大胆，仍需保持纸面留白和单一主事件。
- 文字可成为几何对象的一部分，可使用粗黑体、等宽体和实验性排列。

### 设计调性

理性、先锋、精准、结构化、实验性、具有认知产品和系统设计气质。

### 色彩调性

- 环境色：浅灰白纸面。
- 结构色：高密度黑、炭灰、少量纸白负形。
- 主色锚：番茄红 `#E12A1B`。
- 红色承担冲突、进入、变化与修正，可以比 Quiet Evidence 更有力量。

### 适用场景

产品命题、路线图、Agent 工作流、评审材料、技术架构故事、演示封面和战略表达。

### 参考图路径

- `docs/poster-intent/02-living-mirror/01-capture.png`
- `docs/poster-intent/02-living-mirror/02-evidence.png`
- `docs/poster-intent/02-living-mirror/03-interpret.png`
- `docs/poster-intent/02-living-mirror/04-correct.png`
- `docs/poster-intent/02-living-mirror/05-return.png`
- 联系表：[02-living-mirror-contact.jpg](review/02-living-mirror-contact.jpg)

### Prompt 方向块

```text
Use the supplied Living Mirror images only as low-preservation visual references. Create a new desktop-first landscape paper poster with pale gray-white fibrous paper, large negative space, one clear suprematist relation built from rough black printed geometry, experimental grotesk or monospaced typography, slight xerox wear and misregistration, and tomato red #E12A1B as the single saturated signal of change. Keep the mood precise, evolving and intellectually confident. Do not copy an exact reference composition.
```

## 7. Direction 03 · Time Echo / 时间回声

### 核心意象

褪色照片保存当时的现场，蓝色线与胶带把不同时间里的碎片连接起来。旧 Context 穿过时间重新出现，为今天的判断提供连续性。

### 设计风格

- 日记摄影、旧相纸、诗性编辑、透明描摹层和轻微档案感。
- 使用照片碎片、同一场景的不同裁切、双时态画框、蓝色线迹和修补胶带。
- 画面可以有人生活过的痕迹，避免通用图库人物与过度戏剧化的电影场景。
- 字体适合细衬线、打字机、等宽体和低声量注释。

### 设计调性

温柔、有人味、轻微怀旧、时间感、长期陪伴、安静且富有情绪。

### 色彩调性

- 环境色：暖奶油色、旧相纸棕灰、低对比黑白照片。
- 主色锚：群青蓝 `#2456D8`。
- 蓝色承担连接、标记、修补和时间回声；整体色温保持偏暖。

### 适用场景

用户故事、每日第一帧、品牌情绪页、社交传播、长期使用案例和“幸好它记住了”的价值表达。

### 参考图路径

- `docs/poster-intent/03-time-echo/01-capture.png`
- `docs/poster-intent/03-time-echo/02-evidence.png`
- `docs/poster-intent/03-time-echo/03-interpret.png`
- `docs/poster-intent/03-time-echo/04-correct.png`
- `docs/poster-intent/03-time-echo/05-return.png`
- 联系表：[03-time-echo-contact.jpg](review/03-time-echo-contact.jpg)

### Prompt 方向块

```text
Use the supplied Time Echo images only as low-preservation visual references. Create a new desktop-first landscape paper poster with warm cream fibrous paper, generous negative space, one small faded photographic relation, worn photo edges, translucent tracing paper or repair tape, fine serif or typewriter typography, subtle film and scan grain, and ultramarine #2456D8 as the single saturated thread connecting time. Keep the mood intimate, lived-in and quietly hopeful. Do not copy a specific photo subject or composition.
```

## 8. 方向选择逻辑

| 任务目标 | 首选方向 |
|---|---|
| 建立 Memento 默认品牌感、表达可信与用户控制 | Quiet Evidence |
| 讲产品方法、长期理解、Agent 系统和结构变化 | Living Mirror |
| 讲用户故事、时间、陪伴与历史 Context 的复用价值 | Time Echo |

一次输出优先选择一个主方向。允许借用另一方向的一个局部元素，例如 Quiet Evidence 的纸面配合 Time Echo 的蓝线；不要在同一张图中混用三套主色、全部材质和全部构图语法。

当任务同时包含理性说明与情感传播时，推荐输出两版，而非把两种方向压入同一张图。

## 9. Review 与回滚规则

完成一批生成后，先制作联系表或缩略图全览，再统一检查：

1. 是否仍然能识别所选方向；
2. 是否保留横屏体验和足够留白；
3. 是否只有一个清晰视觉事件；
4. 主色是否承担意义，而非随机装饰；
5. 材质、字体和图像是否属于同一印刷世界；
6. 是否漂移成广告、通用 SaaS、赛博科技、3D Mockup 或密集拼贴；
7. 是否准确表达 Memento 的 Context、证据、修订或时间连续性。

中央规则失败时，收紧 Prompt 并重新生成一次。文字数量本身不构成失败原因。用户确认当前方向可用后，停止继续优化，保留现有图作为罗盘。

## 10. 现有交付索引

- [三套视觉全览图](Memento-Visual-Compass.png)
- [三套紧凑联系表](review/all-directions-contact.jpg)
- [Quiet Evidence 原图目录](01-quiet-evidence/)
- [Living Mirror 原图目录](02-living-mirror/)
- [Time Echo 原图目录](03-time-echo/)

## 11. Codex 调用示例

```text
读取仓库内 `docs/poster-intent/README.md`，
使用 $gc-minimal-zine-poster-v0-3，选择 Quiet Evidence 方向，
把该方向的 5 张参考图作为低保真风格参考，
为 Memento 的“用户可以修正 AI 如何理解自己”制作一张 16:10 横屏产品故事图。
允许加入中文短句，生成后按 README 的 Review 规则检查并返回最终图片、Prompt 和 Recipe。
```

```text
读取 Memento Visual Compass，使用 $gc-minimal-zine-poster-v0-3，
分别用 Living Mirror 和 Time Echo 各做一版横屏封面，
表达“过去确认过的 Context 在今天减少重复解释”。
两版保持各自色彩和材质系统，不混合风格。
```
