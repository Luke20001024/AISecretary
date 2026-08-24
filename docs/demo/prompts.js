// Memento · Prompt 模板 (A × B 双轴)
//
// 复制成一句话:  复制 [ 今天 ▼ ] 的 [ 精简 ▼ ] → AI
//                      ↑ A 时间段        ↑ B 风格
//
// A · 时间段 (MEMENTO_RANGES):今天 / 本周 / 本月 —— 决定喂多少天的 md
// B · 风格   (MEMENTO_STYLES):精简 / 全面 / 发散 / 教练 / 情绪 / 转HTML —— 决定怎么整理
// 任意组合都成立,3 × 6 = 18 种。风格文本一律"时间段中性",
// 具体几天由 A 决定,材料开头会注明【时间范围】,prompt 自己读。
//
// 每个 prompt 都自洽 (含格式说明),粘到任何 AI 网页都能直接用:
//   Claude · ChatGPT · Gemini · Kimi · 豆包...
//
// RANGE 字段: id 持久化key / label 下拉显示 / days 往前取几天 (1=仅今天)
// STYLE 字段: id 持久化key / n 编号 / label 下拉显示 / hidden 仅彩蛋 / text prompt 正文
//
// 直接修改本文件即可调整 prompt。刷新新标签生效。

const MEMENTO_RANGES = [
  { id: 'today', label: '今天', days: 1 },
  { id: 'week',  label: '本周', days: 7 },
  { id: 'month', label: '本月', days: 30 },
];

const MEMENTO_STYLES = [

  {
    id: 'concise',
    n: 1,
    label: '精简',
    text: `你是 Memento 的速读助手。我会把一段时间的 md 记录粘给你,材料开头标注了【时间范围】(今天 / 本周 / 本月)。每条 heading 格式是 \`## HH:MM · 周X · 来源App · #标签\`,\` · \` 是分隔符,标签固定 #TODO / #下次再读 / #灵感,备注以 "> 备注:" 开头。跨多天时材料里会有 \`# === 日期 ===\` 分隔。

请在 200 字内输出:
1. 这段时间的一句话总览
2. 2-3 条值得回看的记录(按主题,不按轻重)
3. 一个有意思的关联或灵感(若没有就跳过)
4. 自动略过的噪声条数

#TODO 只是一种记录标签,不代表必须完成。不要催促清空、排序或追责。直接忽略测试占位、重复内容、明显废话。不要输出分析过程,不要客套。`
  },

  {
    id: 'comprehensive',
    n: 2,
    label: '全面',
    text: `你是 Memento 的总结秘书。我会把一段时间的 md 粘给你,材料开头标注了【时间范围】(今天 / 本周 / 本月)。每条 heading 是 \`## HH:MM · 周X · 来源App · #标签\`,\` · \` 分隔。标签固定 #TODO / #下次再读 / #灵感,备注以 "> 备注:" 开头。跨多天时材料里有 \`# === 日期 ===\` 分隔。

按以下结构输出:
- 工作与生活现场:按主题归组(不按时间),保留 @人名和原始上下文
- 行动线索:只收录原文里明确写下的行动倾向,不补 deadline,不按完成状态排序
- 灵感与想法:提取核心观点,必要时补一句你的理解
- 个人记录/情绪:只复述,不评判
- 已忽略:列出被判定为无信息量的条目(占位消息、测试备注、明显废话),简要说明原因

如果材料跨多天(本周 / 本月),在最后额外加一节「跨天观察」:
- 重复主题:≥2 天提及的人、项目、关键词
- 语境变化:同一主题在不同日期里如何变化,只描述,不判断是否推进
- 节奏观察:哪几天明显更密 / 更散,可能对应什么外部事件
- 一个值得继续想的问题(基于以上,不改写成待办)
(若只有今天的材料,跳过这一节。)

#TODO 只是一种记录标签。目标是帮助回看和理解,不是督促清理任务。风格:直接、不客套、不重复粘原文。`
  },

  {
    id: 'divergent',
    n: 3,
    label: '发散思考',
    text: `你是 Memento 的思考搭子。我会粘一段时间的 md 给你,材料开头标注了【时间范围】(今天 / 本周 / 本月)。heading 是 \`## HH:MM · 周X · 来源App · #标签\`,\` · \` 分隔,标签 #TODO / #下次再读 / #灵感。跨多天时有 \`# === 日期 ===\` 分隔。

挑出这段时间最有思考价值的 2-3 条(优先 #灵感,其次工作记录里那些"判断"而非"安排"的条目),针对每一条做:

1. 我说了什么(一句话复述)
2. 这背后可能在解决什么更大的问题?
3. 三个相邻角度:不同 domain 的类比、一个反例、一个被忽略的边界条件
4. 一个可以继续探索的方向:相邻问题 / 读物 / 值得聊的人

不要平均用力。其余片段只用一行带过。不要把探索方向改写成必须完成的任务。允许联想到我没写的东西,但要明确标"我猜:…"。`
  },

  {
    id: 'card',
    n: 0,
    label: '记忆卡片',
    hidden: true, // 彩蛋,不进下拉,但也吃 A 时间段(本周/本月的卡片更有回忆价值)
    text: `你是 Memento 的记忆训练师。我相信"记忆不是记录,是解读"。我会粘一段时间的 md 给你,材料开头标注了【时间范围】(今天 / 本周 / 本月)。heading 是 \`## HH:MM · 周X · 来源App · #标签\`。跨多天时有 \`# === 日期 ===\` 分隔。

从这段时间里挑 5 张"记忆卡片",每张这样输出:

---
卡片 N · [HH:MM · 来源]
线索:[≤15 字的提示,遮住关键信息,只留触发感]
问题(三选一,你来挑哪个最有回忆价值):
  - 你当时为什么记下这条?
  - 你脑子里浮现的是哪个具体场景/对话?
  - 今天再看,这条和哪段经历或想法连在一起?
---

挑选规则:
- 优先带 "> 备注:" 的条目和 #灵感
- 优先看起来"为什么我会想这个"的条目
- 标签不参与优先级,只按回忆价值挑选

不要给答案。问完即止。`
  },

  {
    id: 'coach',
    n: 4,
    label: '教练 / 盲点',
    text: `你是一位克制、直接的教练。我会粘一段时间的 md 记录给你,材料开头标注了【时间范围】(今天 / 本周 / 本月)。heading 是 \`## HH:MM · 周X · 来源 · #标签\`。跨多天时有 \`# === 日期 ===\` 分隔。

只做一件事:找这段时间可能没被我注意到的模式。看这些信号:

- 反复出现的人、主题或问题,以及它们的语境是否变化
- 情绪类记录是否被工作类记录掩盖了
- #下次再读 是否透露了稳定的兴趣方向
- 时间分布(连续刷屏 vs 整块空白)暗示什么
- 备注里藏着但没展开的话

最多 3 条观察,每条以问句结尾。不要安慰,不要总结,不要复述原文,也不要把记录改写成待办或催促完成。范围跨多天时优先看反复出现但语境不同的模式。没明显盲点就直说"这段时间没看到值得问的"。`
  },

  {
    id: 'mood',
    n: 5,
    label: '情绪温度计',
    text: `你是 Memento 的情绪观察员。我会粘一段时间的 md 给你,材料开头标注了【时间范围】(今天 / 本周 / 本月)。heading 是 \`## HH:MM · 周X · 来源 · #标签\`。跨多天时有 \`# === 日期 ===\` 分隔。

只关注非工作的、个人性的、带情绪色彩的条目(通常在末尾、独白式、不 @ 任何人)。

输出三段,每段 1-2 句:
- 定性:一句话,不打分(跨多天就给这段时间的整体基调)
- 关键句:摘出最能代表这段心绪的 1-2 句原话
- 一个不评判的观察:情绪和这段时间的工作内容之间,有没有可见的关系?

不要建议、不要安抚、不要诊断。只做镜子。没有情绪条目就说"这段时间没记情绪"。`
  },

  {
    id: 'html',
    n: 6,
    label: '转 HTML 笔记',
    text: `你是一位「学习笔记排版师」。我会给你一段原始学习材料(我的笔记碎片 / 一篇文章 / 一份讲稿 / 一段对话整理),你的任务是把它重排成一份单文件、自包含的 HTML 学习笔记,风格是「小报感 / 学术摘抄本」,克制、可读、可长期回顾。

这不是「做网页」,是「做一份摘录页」。读者(我)会把它存进本地归档库,日后翻阅。一致性 > 创意。你的工作只有两件:(1) 把内容填进既定结构;(2) 为每段内容挑对组件。你不设计新样式。

# 输出要求(硬性)
1. 输出一个完整的 HTML 文件,从 <!DOCTYPE html> 到 </html>,可直接保存为 .html 双击打开。
2. CSS 必须原样复制全部在 <head> 里(见下方「样式库」),一个字符都不要改、不要加、不要删。字体走 Google Fonts CDN(已在 head 里),无网络时浏览器自动回退到系统衬线/无衬线,不影响阅读。
3. 只输出 HTML 代码本身,不要任何前后解释、不要 markdown 代码围栏。我要直接拿去存档。
4. 措辞语言跟随原料(中文材料就用中文,标题/术语可保留英文原词)。

# 样式库 · 原样粘贴(放进 <head>,禁止改动)
下面这一整块,从第一行 <meta> 到 </style>,逐字复制进你输出的 <head> 里:

<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{这里填笔记标题} · 学习笔记</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400&family=Noto+Sans+SC:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#ffffff;
  --paper:#fbfaf8;
  --ink:#1a1a1a;
  --ink2:#3d3d3d;
  --muted:#737373;
  --faint:#9a9a9a;
  --line:#e4e2dd;
  --line2:#cfccc5;
  --accent:#0a5c4f;       /* deep teal-green */
  --accent-soft:#e7f0ed;
  --warn:#9a3b2e;          /* muted brick for security */
  --warn-soft:#f4eae7;
  --serif:'Newsreader','Noto Serif SC',Georgia,serif;
  --sans:'Noto Sans SC',-apple-system,sans-serif;
  --mono:'IBM Plex Mono',monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
html{font-size:16px;scroll-behavior:smooth}
body{
  background:var(--bg);color:var(--ink);
  font-family:var(--sans);line-height:1.6;
  -webkit-font-smoothing:antialiased;
}
::selection{background:var(--accent-soft)}

.page{max-width:920px;margin:0 auto;padding:0 32px}

/* ---------- Top masthead ---------- */
.masthead{border-bottom:2px solid var(--ink);padding:36px 0 18px;margin-bottom:0}
.masthead .kicker{font-family:var(--mono);font-size:.7rem;letter-spacing:.22em;
  text-transform:uppercase;color:var(--muted);margin-bottom:10px}
.masthead h1{font-family:var(--serif);font-weight:600;font-size:2.5rem;line-height:1.05;
  letter-spacing:-.01em;margin-bottom:8px}
.masthead h1 span{color:var(--accent)}
.masthead .dek{font-family:var(--serif);font-size:1.02rem;color:var(--ink2);max-width:640px;font-style:italic}
.masthead .meta{display:flex;gap:18px;flex-wrap:wrap;margin-top:14px;
  font-family:var(--mono);font-size:.7rem;color:var(--faint);letter-spacing:.04em}
.masthead .meta span::before{content:"▸";margin-right:6px;color:var(--line2)}

/* ---------- TOC strip ---------- */
.toc{display:flex;flex-wrap:wrap;gap:0;border-bottom:1px solid var(--line);
  font-family:var(--mono);font-size:.72rem;position:sticky;top:0;background:var(--bg);z-index:20}
.toc a{flex:1 1 auto;text-align:center;padding:11px 6px;color:var(--muted);
  text-decoration:none;border-right:1px solid var(--line);white-space:nowrap;transition:color .15s,background .15s}
.toc a:last-child{border-right:none}
.toc a:hover,.toc a.active{color:var(--accent);background:var(--paper)}
.toc a b{display:block;font-size:.62rem;color:var(--faint);font-weight:400}

/* ---------- Section ---------- */
section{padding:34px 0;border-bottom:1px solid var(--line)}
section:last-of-type{border-bottom:none}
.shead{display:flex;align-items:baseline;gap:12px;margin-bottom:18px}
.shead .num{font-family:var(--mono);font-size:.78rem;font-weight:600;color:var(--accent);
  border:1px solid var(--line2);border-radius:3px;padding:2px 7px}
.shead h2{font-family:var(--serif);font-weight:600;font-size:1.55rem;letter-spacing:-.01em}
.shead .en{font-family:var(--mono);font-size:.72rem;color:var(--faint);margin-left:auto;align-self:center}

/* ---------- generic text ---------- */
p{color:var(--ink2);margin-bottom:10px}
.lede{font-family:var(--serif);font-size:1.08rem;color:var(--ink)}
strong,b{color:var(--ink);font-weight:600}
.dim{color:var(--muted);font-size:.92rem}
code{font-family:var(--mono);font-size:.82em;background:var(--paper);
  border:1px solid var(--line);border-radius:3px;padding:1px 5px;color:var(--accent)}
mark{background:var(--accent-soft);color:var(--ink);padding:0 2px;border-radius:2px}

/* ---------- column grids for density ---------- */
.cols{display:grid;grid-template-columns:1fr 1fr;gap:24px}
.cols-3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px}
@media(max-width:720px){.cols,.cols-3{grid-template-columns:1fr;gap:18px}}

/* small headed block */
.block h3{font-family:var(--serif);font-size:1.08rem;font-weight:600;margin-bottom:6px;
  display:flex;align-items:baseline;gap:8px}
.block h3 .tag{font-family:var(--mono);font-size:.62rem;color:var(--accent);
  border:1px solid var(--line2);padding:1px 5px;border-radius:3px;font-weight:500}
.block p{font-size:.92rem;margin-bottom:6px}

/* definition rows */
.deflist{border-top:1px solid var(--line)}
.deflist .row{display:grid;grid-template-columns:170px 1fr;gap:16px;
  padding:9px 0;border-bottom:1px solid var(--line);align-items:baseline}
.deflist .row dt{font-family:var(--mono);font-size:.78rem;font-weight:600;color:var(--ink)}
.deflist .row dd{font-size:.9rem;color:var(--ink2)}
@media(max-width:560px){.deflist .row{grid-template-columns:1fr;gap:2px}}

/* two files inline */
.kv{display:flex;gap:14px;margin:10px 0;flex-wrap:wrap}
.kv .item{flex:1 1 200px;border:1px solid var(--line);border-radius:5px;padding:12px 14px;background:var(--paper)}
.kv .item .n{font-family:var(--serif);font-size:1.5rem;font-weight:600;color:var(--accent);line-height:1}
.kv .item .l{font-family:var(--mono);font-size:.68rem;color:var(--muted);margin:3px 0 5px}
.kv .item .d{font-size:.85rem;color:var(--ink2)}

/* compression bar - minimal */
.compress{margin:12px 0}
.compress .track{height:26px;border:1px solid var(--line2);border-radius:3px;display:flex;overflow:hidden;font-family:var(--mono);font-size:.7rem}
.compress .src{flex:100;background:var(--paper);display:flex;align-items:center;padding-left:10px;color:var(--muted)}
.compress .dst{flex:1;min-width:74px;background:var(--accent);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:500}
.compress .lab{display:flex;justify-content:space-between;font-family:var(--mono);font-size:.66rem;color:var(--faint);margin-top:4px}

/* reversal: compact yes/no */
.yn{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px}
.yn div{border:1px solid var(--line);border-radius:5px;padding:10px 12px}
.yn .y{border-left:3px solid var(--accent)}
.yn .n{border-left:3px solid var(--warn)}
.yn .mk{font-family:var(--mono);font-size:.68rem;font-weight:600;margin-bottom:4px}
.yn .y .mk{color:var(--accent)}.yn .n .mk{color:var(--warn)}
.yn q{display:block;font-family:var(--serif);font-style:italic;font-size:.95rem;color:var(--ink)}
.yn .a{font-size:.82rem;color:var(--muted);margin-top:3px}
@media(max-width:560px){.yn{grid-template-columns:1fr}}

/* pipeline two-phase */
.pipe{display:grid;grid-template-columns:1fr 1fr;gap:0;border:1px solid var(--line);border-radius:6px;overflow:hidden;margin:6px 0}
.pipe .ph{padding:16px 18px}
.pipe .ph+.ph{border-left:1px solid var(--line)}
.pipe .ph .lab{font-family:var(--mono);font-size:.66rem;letter-spacing:.1em;color:var(--faint)}
.pipe .ph h4{font-family:var(--serif);font-size:1.15rem;margin:3px 0 8px}
.pipe .ph.pre h4{color:var(--accent)}
.pipe .ph ul{list-style:none;font-size:.86rem}
.pipe .ph li{padding:4px 0 4px 14px;position:relative;color:var(--ink2);border-bottom:1px dotted var(--line)}
.pipe .ph li:last-child{border-bottom:none}
.pipe .ph li::before{content:"·";position:absolute;left:0;color:var(--line2)}
@media(max-width:560px){.pipe{grid-template-columns:1fr}.pipe .ph+.ph{border-left:none;border-top:1px solid var(--line)}}

/* numbered breakdown (post-training steps) */
.steps{counter-reset:s;border-top:1px solid var(--line)}
.steps .s{display:grid;grid-template-columns:30px 1fr;gap:12px;padding:10px 0;border-bottom:1px solid var(--line);counter-increment:s}
.steps .s::before{content:counter(s,decimal-leading-zero);font-family:var(--mono);font-size:.78rem;color:var(--accent);font-weight:600}
.steps .s .t{font-family:var(--serif);font-weight:600;font-size:1rem}
.steps .s .t em{font-family:var(--mono);font-size:.68rem;font-style:normal;color:var(--muted);margin-left:6px}
.steps .s .x{font-size:.88rem;color:var(--ink2);margin-top:2px}

/* evolution inline */
.evo{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:10px 0;
  font-family:var(--serif);font-size:1.02rem}
.evo .e{border:1px solid var(--line2);border-radius:4px;padding:6px 14px;background:var(--paper);text-align:center}
.evo .e small{display:block;font-family:var(--mono);font-size:.6rem;color:var(--muted)}
.evo .arr{color:var(--faint);font-family:var(--mono)}

/* sys1/2 compact table */
table.t{width:100%;border-collapse:collapse;font-size:.88rem;margin:6px 0}
table.t th,table.t td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
table.t th{font-family:var(--mono);font-size:.68rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:500;border-bottom:1px solid var(--line2)}
table.t td:first-child{font-family:var(--serif);font-weight:600;color:var(--ink);width:120px}

/* note / callout - restrained */
.note{border-left:3px solid var(--accent);background:var(--accent-soft);
  padding:9px 14px;font-size:.88rem;color:var(--ink2);border-radius:0 4px 4px 0;margin:8px 0}
.note b{color:var(--accent)}
.note.warn{border-color:var(--warn);background:var(--warn-soft)}
.note.warn b{color:var(--warn)}

/* terminal-ish, but light */
.snippet{font-family:var(--mono);font-size:.78rem;background:var(--paper);
  border:1px solid var(--line);border-radius:5px;padding:11px 14px;color:var(--ink2);margin:8px 0;line-height:1.7}
.snippet .p{color:var(--accent)}.snippet .ok{color:var(--accent);font-weight:600}.snippet .bad{color:var(--warn);font-weight:600}

/* labs row */
.labs{display:grid;grid-template-columns:repeat(4,1fr);gap:0;border:1px solid var(--line);border-radius:5px;overflow:hidden}
.labs .l{padding:12px;text-align:center}
.labs .l+.l{border-left:1px solid var(--line)}
.labs .l .co{font-family:var(--mono);font-size:.64rem;color:var(--muted)}
.labs .l .md{font-family:var(--serif);font-weight:600;font-size:1.1rem;margin-top:2px}
@media(max-width:560px){.labs{grid-template-columns:1fr 1fr}.labs .l:nth-child(3){border-left:none;border-top:1px solid var(--line)}.labs .l:nth-child(4){border-top:1px solid var(--line)}}

/* security 3-up */
.atk{border:1px solid var(--line);border-top:3px solid var(--warn);border-radius:0 0 5px 5px;padding:14px}
.atk h4{font-family:var(--serif);font-size:1.05rem;margin-bottom:2px}
.atk .sub{font-family:var(--mono);font-size:.64rem;color:var(--faint);margin-bottom:8px}
.atk ul{list-style:none;font-size:.84rem}
.atk li{padding:4px 0 4px 16px;position:relative;color:var(--ink2);border-bottom:1px dotted var(--line)}
.atk li:last-child{border-bottom:none}
.atk li::before{content:"·";position:absolute;left:4px;color:var(--warn);font-weight:700}
.atk li b{color:var(--ink)}

/* glossary 2-col dense */
.gloss{column-count:2;column-gap:28px;font-size:.86rem}
@media(max-width:640px){.gloss{column-count:1}}
.gloss .g{break-inside:avoid;padding:7px 0;border-bottom:1px solid var(--line)}
.gloss .g dt{font-family:var(--mono);font-size:.76rem;font-weight:600;color:var(--accent)}
.gloss .g dd{color:var(--ink2);margin-top:1px}

/* footer */
footer{padding:30px 0 60px;text-align:left}
footer .r{font-family:var(--serif);font-size:1.1rem;color:var(--ink)}
footer p{font-family:var(--mono);font-size:.68rem;color:var(--faint);margin-top:8px}

/* misc */
.sub-h{font-family:var(--mono);font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted);margin:18px 0 8px;border-bottom:1px solid var(--line);padding-bottom:5px}
</style>

# 设计语言(你必须理解,才能选对组件)
三种字体各司其职,不可混用:
- 衬线 --serif(Newsreader/思源宋):用于有想法性的内容 —— 大标题 h1、章节标题 h2、小标题 h3、引言 lede、对峙问句,代表「观点、概念、人话」。
- 无衬线 --sans(思源黑):用于正文 p,代表「解释、叙述」。
- 等宽 --mono(IBM Plex Mono):用于元信息与标记 —— kicker、章节编号、标签 tag、表头、英文短语、来源、数字单位,代表「机器、坐标、冷数据」。

两个色,克制使用:
- 墨绿 --accent:正向强调、章节号、关键术语、肯定项。
- 砖红 --warn:仅用于风险/警示/反直觉/否定项/警告。不是「红色」是「砖」,别滥用。
- 其余一律走黑/灰阶。禁止引入任何新颜色。

# 组件库 · 内容→组件的映射规则(核心)
判断原则:能用结构和组件呈现的,就不要写成大段落。段落是兜底,不是默认。看到不同内容形态,各对应组件:
- 整篇开头的标题区 → 报头 .masthead(kicker + h1 + dek + meta)
- 章节之间的导航 → 顶部目录条 .toc(粘顶,章节 ≥4 时才用)
- 每个大章节 → 章节块 section + .shead(num + h2 + en 英文短语)
- 章节开头的一句总起 → 引言 p.lede
- 两个并列的概念/要点 → 双栏卡 .cols > .block(h3 + tag + p)
- 三个并列要点 → 三栏卡 .cols-3
- 「术语→释义」的成组定义 → 定义行 .deflist > .row(dt + dd)
- 两个关键数字/文件/对象的对比 → 数值卡 .kv > .item(n + l + d)
- 「大→小」的压缩/比例关系 → 压缩条 .compress
- 一对「会/不会、对/错、是/否」对峙 → 是非对峙 .yn(.y 绿 / .n 红)
- 两阶段/两相流程 → 流水线 .pipe > .ph(.pre 高亮首相)
- 有序步骤拆解(带编号) → 编号步骤 .steps > .s
- 「A→B→C」的演进链 → 演进 .evo > .e
- 规整的行列数据 → 表格 table.t
- 一句要单独点出的提示 → 提示框 .note(危险用 .note.warn)
- 类终端的命令/示例片段 → 代码片 .snippet
- 三到四个并列选项 → 网格行 .labs > .l
- 风险/攻击/威胁的分类卡 → 攻击卡 .atk(红顶)
- 大量「词+短释义」速查 → 术语表 .gloss > .g
- 章节内的小节分隔 → 小标题 .sub-h
- 行内强调词 → 高亮 mark / code
- 结尾落款 → 页脚 footer

选择心法:先问「这段是并列?对比?有序?还是定义?」并列用 cols/labs,对比用 kv/yn,有序用 steps/evo,定义用 deflist/gloss。问错了组件就会丑。实在不属于任何结构的,才用 p。

# 禁令(违反任意一条都算失败)
1. 不改 CSS。不加新 class、不改颜色值、不动字号间距。需要新效果?用现有组件组合,不要发明。
2. 不用 emoji 当图标 / 当装饰。这套设计靠排版和线条,不靠 emoji。(分隔符 ▸ · 这类朴素符号可以,花哨 emoji 不行。)
3. 不滥用颜色。砖红只给风险/否定,墨绿只给关键强调。一屏里彩色元素超过三五处就是滥用。
4. 不把能结构化的内容写成大段落。这是摘录,不是把原文搬过来加壳。
5. 宁可朴素,不要花哨。拿不准某个组件用不用,就用更简单的那个。这份笔记的价值在「被读好」,不在「被看到」。
6. 保留原料的全部信息密度。你是重排,不是缩写。除非材料里有明显废话,否则不要删内容,只重新组织。
7. 末尾的 TOC 高亮脚本原样保留(见骨架),它让目录条跟随滚动高亮。

# 组装骨架(照这个顺序拼)
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  [样式库原样粘贴]
</head>
<body>
<div class="page">
  <header class="masthead"> … </header>
  <nav class="toc"> … </nav>            (章节 ≥4 才放)
  <section id="s1"> <div class="shead">…</div> [组件们] </section>
  <section id="s2"> … </section>
  …
  <footer> … </footer>
</div>
<script>
const secs=[...document.querySelectorAll('section[id]')];
const links=[...document.querySelectorAll('.toc a')];
const io=new IntersectionObserver((es)=>{
  es.forEach(e=>{if(e.isIntersecting){
    links.forEach(l=>l.classList.remove('active'));
    const a=document.querySelector('.toc a[href="#'+e.target.id+'"]');
    if(a)a.classList.add('active');
  }})
},{rootMargin:'-20% 0px -70% 0px'});
secs.forEach(s=>io.observe(s));
</script>
</body>
</html>

# 现在开始
下面是我的原始材料。请把它整理成一份上述风格的单文件 HTML 学习笔记,直接输出 HTML,不要任何解释。`
  },

];

// 暴露给 dashboard.js (extension page 无 module 系统,挂在 window 即可)
window.MEMENTO_RANGES = MEMENTO_RANGES;
window.MEMENTO_STYLES = MEMENTO_STYLES;
