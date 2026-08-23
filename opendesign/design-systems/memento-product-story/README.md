# Memento Product Story Design System

这套设计系统服务于 Memento 的产品讲述页。它把真实产品代码、当前认知地景、形成证据视图与现有海报整理成一套可复用的网页规则，让讲述页与产品拥有同一套视觉血缘。

## 事实源优先级

1. `chrome-newtab/dashboard.css` 中的 `cognitive-home` 样式和设计变量
2. `chrome-newtab/dashboard.html` 中真实组件的结构与命名
3. `docs/MEMENTO_PRODUCT_FINAL_STATE.md` 中产品边界、三层认知结构与视觉原则
4. `docs/assets/product/memento-cognitive-landscape-current.png` 等真实产品截图
5. `docs/assets/posters/memento-cognitive-horizon-hero-explicit-v2.png` 的纸面与地平线图像语言

代码决定颜色、字体、控件、密度与状态。截图用于确认构图、比例和真实气质。海报只补充封面图像语言。

## 核心视觉判断

- 暖灰纸面承担时间感，微弱横纹提供持续记录的背景
- 墨黑负责主要信息，冷蓝负责来源、关系、变化和选择状态
- 等高线、证据点和关系线是 Memento 的识别性语言
- 大面积留白围绕一个强画面展开，正文区域保持紧凑
- 细线和空间层级优先，避免连续卡片墙
- 产品截图保持真实窗口结构，讲述页只做裁切、遮罩、标注和组合
- 浮层与系统窗口允许轻阴影，产品主界面维持近乎平面的纸面质感

## 内容与叙事

讲述结构从一个可感知的问题进入：意图散落在聊天、网页、文档、语音和 AI 窗口中。随后展示 Memento 如何接住真实片段，让片段进入记录流、逐渐长成地形，最终形成可追溯且会持续修订的自我理解，再以可调用的个人记忆回到真实工作。

三项价值固定为“接住正在发生的意图”“长期理解你的形状”“让每个 AI 都从同一个你开始”。Memory、MCP、本地工具和调用范围放在功能与机制说明中，不替代价值标题。完整口径见 `docs/MEMENTO_PRODUCT_NARRATIVE.md`。

页面文案遵循短句、短标题、少段落。高级章节名承担气质，功能性副标题解释“这是什么、为什么需要”。页面展示文案不使用句末句号。

## 资源

- `assets/imagery/memento-cognitive-landscape-current.png`：当前产品认知地景，最高优先级
- `assets/imagery/landscape-formation-chain-1280.jpg`：主题如何形成的真实产品视图
- `assets/imagery/memento-cognitive-horizon-hero-explicit-v2.png`：封面与地平线基调

仓库里暂未发现可直接使用的真实微信、AI 对话和文档窗口截图。优先使用用户提供或现场截取并脱敏的真实材料。在素材到位前，可以使用明确标注的 HTML 场景重构验证叙事构图；它必须保持可替换，也不能被描述为真实个人记录或已经上线的产品交互。

## 文件

- `SKILL.md`：使用规则和执行清单
- `tokens/colors_and_type.css`：颜色、字体、间距、阴影与动效变量
- `brand/voice-and-tone.md`：文案语气与表达边界
- `brand/style-notes.md`：布局、图像、组件和动效语言

## 已确认与待确认

已确认：主色、字体角色、纸面质感、等高线语言、控件密度、真实截图优先、产品能力边界。

待确认：第一幕要使用哪些真实窗口截图，以及窗口中允许露出的具体内容。拿到素材后即可做脱敏、裁切和组合。
