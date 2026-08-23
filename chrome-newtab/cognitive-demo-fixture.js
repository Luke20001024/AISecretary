// Memento P00 cognitive demo fixture.
// The content in this module is deterministic synthetic product-demo material.
// It is not derived from, or intended to describe, a real person.

(function exposeCognitiveDemoFixture(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MementoCognitiveDemoFixture = api;
})(typeof window !== 'undefined' ? window : globalThis, function createCognitiveDemoFixtureApi() {
  'use strict';

  const FIXTURE_VERSION = 'memento-cognitive-demo-v1';
  const START_DATE = '2026-07-30';
  const END_DATE = '2026-08-18';
  const SYNTHETIC_NOTICE = '以下内容均为合成演示数据，仅用于展示交互与信息层级，不代表任何真实用户。';
  const DATES = Object.freeze([
    '2026-07-30', '2026-07-31', '2026-08-01', '2026-08-02', '2026-08-03',
    '2026-08-04', '2026-08-05', '2026-08-06', '2026-08-07', '2026-08-08',
    '2026-08-09', '2026-08-10', '2026-08-11', '2026-08-12', '2026-08-13',
    '2026-08-14', '2026-08-15', '2026-08-16', '2026-08-17', '2026-08-18',
  ]);
  const DAY_COUNTS = Object.freeze([
    14, 17, 9, 0, 18, 13, 16, 21, 15, 8,
    10, 17, 12, 19, 16, 14, 9, 0, 18, 15,
  ]);
  const WEEKDAYS = Object.freeze(['周日', '周一', '周二', '周三', '周四', '周五', '周六']);
  const WEEKDAY_TIMES = Object.freeze([
    '07:58', '08:21', '08:47', '09:16', '09:54', '10:23', '11:07', '11:42',
    '12:18', '13:06', '13:47', '14:25', '15:02', '15:39', '16:18', '16:56',
    '17:34', '18:22', '19:16', '20:08', '21:02', '22:11',
  ]);
  const WEEKEND_TIMES = Object.freeze([
    '09:26', '10:18', '11:42', '13:46', '15:08',
    '16:37', '17:52', '19:18', '20:43', '22:02',
  ]);
  const TAGS = Object.freeze(['灵感', 'TODO', '下次再读', '灵感', 'TODO']);

  const THEME_DEFINITIONS = Object.freeze([
    {
      key: 'product_decision',
      title: '产品决策',
      statement: '在方案尚未稳定时，会先把目标指标、护栏指标和验证周期写清，再进入功能讨论。',
      boundary: '目前主要出现在需要定义目标与取舍的产品工作中，不外推到所有日常决定。',
      memoryKind: 'decision',
      purpose: 'future_decision',
      insightKind: 'confirmed',
      uncertainty: 'low',
      x: 0.15, y: 0.26, elevation: 0.94,
      terrain: { spreadX: 76, spreadY: 38, angle: -12 },
      subpeaks: [
        { id: 'goal_first', title: '目标先行', x: 0.035, y: 0.095, elevation: 0.53, evidenceCount: 12, spreadX: 46, spreadY: 23, angle: -28, recent: false },
        { id: 'guardrail_awareness', title: '护栏意识', x: 0.285, y: 0.405, elevation: 0.64, evidenceCount: 15, spreadX: 58, spreadY: 28, angle: 18, recent: false },
      ],
      notes: [
        '评审前先写清主指标、护栏指标和验证周期，再讨论功能范围。',
        '这版入口要解决的是判断成本，暂时不扩展到完整工作流。',
        '指标口径仍有分歧：分母需要在进入流程与看到入口之间做一次明确选择。',
        '先把必须成立的用户场景列出来，功能清单随后再排。',
        '如果成功标准无法在两周内观测，这个方案还不适合直接进入开发。',
        '把“大家都想要”拆成使用频率、失败成本和替代方案三个判断项。',
        '这次评审只决定是否继续投入，不把实现细节一起锁死。',
        '先留一个可撤回的决策点，等第一轮数据回来再扩大范围。',
      ],
      memoryStatements: [
        '在产品方案进入评审前，先明确目标指标、护栏指标和验证周期。',
        '当目标边界还未清楚时，优先缩小问题范围，再决定是否增加功能。',
      ],
    },
    {
      key: 'evidence_first',
      title: '证据优先',
      statement: '面对判断分歧时，倾向先追问证据来源、可复现路径与反例，再决定是否采用结论。',
      boundary: '在高不确定或高失败成本的判断中更稳定；低风险探索仍可以先行动再补证据。',
      memoryKind: 'observation',
      purpose: 'continue_thinking',
      insightKind: 'observation',
      uncertainty: 'low',
      x: 0.43, y: 0.17, elevation: 0.91,
      terrain: { spreadX: 82, spreadY: 35, angle: 8 },
      subpeaks: [
        { id: 'source_check', title: '来源核对', x: 0.305, y: 0.335, elevation: 0.60, evidenceCount: 14, spreadX: 55, spreadY: 26, angle: -20, recent: false },
        { id: 'keep_counterexample', title: '保留反例', x: 0.605, y: 0.045, elevation: 0.42, evidenceCount: 9, spreadX: 38, spreadY: 21, angle: 24, recent: false },
      ],
      notes: [
        '搜索结果只留标题不够，需要保留原文位置和可复核链接。',
        '把“体验更好”改成可观察行为，否则评审时每个人理解都不同。',
        '这条判断目前只有一个案例支持，先保留反例入口。',
        '同一指标出现两个版本，先核对数据时间窗与过滤条件。',
        '引用结论前补上样本范围，避免把局部发现写成普遍规律。',
        '先复现一次分析路径，再讨论结论是否值得进入决策。',
        '访谈中的强烈表述不能直接代替行为证据，需要和实际使用记录对照。',
        '如果没有找到原文定位，正文只写“尚未核实”。',
      ],
      memoryStatements: [
        '重要判断需要同时保留来源位置、适用范围和可检查的反例入口。',
        '当口径冲突时，先复现数据路径，再讨论结论是否进入决策。',
      ],
    },
    {
      key: 'long_term_accumulation',
      title: '长期积累',
      statement: '更重视能在未来复用的判断机制，而非一次任务中的临时完成度。',
      boundary: '适用于反复出现且值得复用的问题；一次性小事不强制沉淀为长期机制。',
      memoryKind: 'learning',
      purpose: 'find_later',
      insightKind: 'confirmed',
      uncertainty: 'low',
      x: 0.82, y: 0.25, elevation: 0.88,
      terrain: { spreadX: 86, spreadY: 40, angle: 14 },
      subpeaks: [
        { id: 'reusable_mechanism', title: '复用机制', x: 0.675, y: 0.425, elevation: 0.62, evidenceCount: 15, spreadX: 56, spreadY: 30, angle: -22, recent: false },
        { id: 'version_trace', title: '版本轨迹', x: 0.965, y: 0.105, elevation: 0.36, evidenceCount: 7, spreadX: 34, spreadY: 19, angle: 30, recent: false },
      ],
      notes: [
        '一次性完成不等于形成能力，今天先把可复用的判断步骤留下。',
        '把这次评审的问题整理成检查表，下次不用从空白开始。',
        '记录结论之外，也要留下当时为什么这样取舍。',
        '同类问题第三次出现，应该沉淀成规则或模板。',
        '归档时保留版本变化，方便之后看清判断是怎样修订的。',
        '先把能重复使用的字段固定，再考虑自动生成完整报告。',
        '今天新增的材料先进入主题，再判断是否足以改变长期理解。',
        '能被别人接手并复用，才算从经验变成了系统能力。',
      ],
      memoryStatements: [
        '同类问题反复出现后，将判断过程沉淀为可复用检查表或模板。',
        '归档不仅保留结论，也保留结论发生变化时的理由与版本。',
      ],
    },
    {
      key: 'collaboration_boundary',
      title: '协作边界',
      statement: '在协作中会主动定义产品判断与项目推进的边界，让决策责任可以被追溯。',
      boundary: '强调决策责任清晰，同时保留主动推进和在关键节点补位的空间。',
      memoryKind: 'observation',
      purpose: 'future_decision',
      insightKind: 'tension',
      uncertainty: 'medium',
      x: 0.87, y: 0.69, elevation: 0.82,
      terrain: { spreadX: 80, spreadY: 40, angle: -18 },
      subpeaks: [
        { id: 'clear_ownership', title: '责任清晰', x: 0.725, y: 0.595, elevation: 0.58, evidenceCount: 13, spreadX: 52, spreadY: 29, angle: 16, recent: false },
        { id: 'leave_interfaces', title: '留出接口', x: 0.965, y: 0.885, elevation: 0.40, evidenceCount: 8, spreadX: 36, spreadY: 21, angle: -25, recent: false },
      ],
      notes: [
        '产品负责定义目标与取舍，项目推进负责暴露依赖和风险，两边需要在同一张决策表上对齐。',
        '会议结束前补上负责人和决策截止点，避免讨论结果只停留在口头。',
        '这项依赖目前没有明确所有者，先把风险暴露出来。',
        '需要区分“建议”“待验证假设”和“已经决定”，避免执行中混用。',
        '把跨团队争议收束为两个可选择方案，并写清各自代价。',
        '同步信息不等于承担所有推进，关键判断仍要有清晰责任人。',
        '如果输入条件变化，原决策需要自动回到待确认状态。',
        '方案文档里补一段不做什么，让协作边界可以被检查。',
      ],
      memoryStatements: [
        '协作记录需要区分建议、待验证假设与正式决定，并标注对应责任人。',
        '跨团队讨论应收束为可选择方案及其代价，避免由某个人长期承担全部推进。',
      ],
    },
    {
      key: 'research_method',
      title: '研究方法',
      statement: '做研究时会先固定问题、筛选门槛与证据定位，再扩大材料规模。',
      boundary: '针对需要形成可复核论证的研究任务；探索初期仍允许暂存较宽的线索。',
      memoryKind: 'learning',
      purpose: 'create',
      insightKind: 'change',
      uncertainty: 'medium',
      x: 0.53, y: 0.81, elevation: 0.86,
      terrain: { spreadX: 84, spreadY: 37, angle: 5 },
      subpeaks: [
        { id: 'set_thresholds', title: '先定门槛', x: 0.395, y: 0.625, elevation: 0.63, evidenceCount: 15, spreadX: 56, spreadY: 27, angle: -12, recent: false },
        { id: 'return_to_source', title: '回到原文', x: 0.685, y: 0.935, elevation: 0.46, evidenceCount: 10, spreadX: 43, spreadY: 22, angle: 22, recent: true },
      ],
      notes: [
        '先固定纳入标准，再扩大候选材料，避免看到好结论后反改筛选门槛。',
        '文献卡片必须带页码或图表位置，否则暂时不能进入正文。',
        '这篇材料只支持方法背景，不能直接支持当前结论。',
        '把研究问题拆成发现、筛选、证据定位和写作四个阶段。',
        '命名城市只作为来源案例，不把单个案例扩写成研究范围。',
        '先做一轮小样本盲筛，检查标准是否能被稳定执行。',
        '如果原文没有找到对应表述，卡片明确标记为“原文未找到”。',
        '模型输出只负责发现候选，进入论证仍需回到原始材料。',
      ],
      memoryStatements: [
        '研究材料进入正文前，需要通过预先固定的筛选门槛并保留精确定位。',
        '发现候选与形成论证分开处理，后者必须回到可核查的原始材料。',
      ],
    },
    {
      key: 'iteration_rhythm',
      title: '迭代节奏',
      statement: '会用短周期样品尽早暴露问题，并依据观测结果调整下一轮投入。',
      boundary: '适用于能在短周期获得反馈的工作；基础约束尚不清楚时仍需先补底层验证。',
      memoryKind: 'action',
      purpose: 'action_clue',
      insightKind: 'change',
      uncertainty: 'low',
      x: 0.17, y: 0.74, elevation: 0.89,
      terrain: { spreadX: 78, spreadY: 39, angle: 18 },
      subpeaks: [
        { id: 'small_validation', title: '小步验证', x: 0.035, y: 0.915, elevation: 0.39, evidenceCount: 8, spreadX: 36, spreadY: 20, angle: 28, recent: false },
        { id: 'visible_failure', title: '失败可见', x: 0.305, y: 0.545, elevation: 0.59, evidenceCount: 13, spreadX: 51, spreadY: 28, angle: -24, recent: true },
      ],
      notes: [
        '先用可点击样品检查信息层级，接口自动化等视觉路径稳定后再接入。',
        '这一轮只验证从主题到依据再到深层理解的三层路径。',
        '把失败状态也放进样品，避免顺畅路径掩盖真实问题。',
        '先观察用户是否能找到入口，再判断要不要增加引导文案。',
        '本轮样品保留一个明确边界：所有推断都要带来源和适用范围。',
        '视觉结构确定后再接入长期任务，减少两边同时变化造成的返工。',
        '每轮只改一个核心假设，并保留前后截图用于比较。',
        '先把基本记录跑通，复杂自动化放到后续长期调试。',
      ],
      memoryStatements: [
        '先用可点击样品检验核心信息路径，结构稳定后再接入复杂自动化。',
        '每轮迭代只验证少量核心假设，同时保留失败状态和前后差异。',
      ],
    },
  ]);

  const FOLLOW_UPS = Object.freeze([
    '下一步补一张口径表，再用两条场景检查是否成立。',
    '先保留为待确认项，避免在证据不足时写成正式结论。',
    '明天回看一次，确认这条记录是否仍值得进入长期积累。',
    '先交付可验证的部分，完整范围等反馈回来后再补。',
    '需要找一个反例，检查当前边界是否写得过宽。',
    '把这项加入下轮验证清单，观察入口是否足够清楚。',
    '这次只记录判断依据，不提前承诺最终实现方式。',
    '如果后续没有再次出现，就让它停留在普通记录层。',
    '补充一条失败条件，方便之后判断是否需要撤回。',
    '先和现有规则做一次去重，再决定是否新增长期条目。',
    '保留原始表述，整理结果只作为可修改的第二层。',
    '这条暂时不自动外推到其他主题。',
  ]);

  const MICRO_OPENERS = Object.freeze([
    '评审前想到一个小问题',
    '刚看完上一版原型',
    '会后先记在这里',
    '这条还没完全想清楚',
    '路上补一笔',
    '刚才的数据对齐里又出现了',
    '先留一个判断',
    '准备下一轮评审时发现',
    '翻旧记录时看到同类问题',
    '午间快速核对了一遍',
    '今天先收住范围',
    '和研发过了一遍之后',
    '把口头讨论换成一句可检查的话',
    '先记下这个尚未解决的分叉',
    '刚补完来源定位',
    '这一轮最值得保留的是',
  ]);

  const MICRO_DETAILS = Object.freeze([
    '入口文案、空状态和失败回退需要放在同一条路径里看。',
    '目前样本只覆盖高频用户，低频场景还没验证。',
    '如果两周后仍没有观测信号，就停止扩大投入。',
    '对齐表里还缺负责人、截止点和撤回条件。',
    '先用三条记录试跑，确认字段不会逼着人写长文。',
    '这个结论只适用于当前样品，暂时不延伸到完整产品。',
    '需要同时看一次成功案例和一次失败案例。',
    '下一轮只比较入口位置，不一起改文案和信息结构。',
    '现有材料足以支持观察，还不足以形成稳定结论。',
    '先把原始记录留下，整理层允许之后重做。',
    '这项依赖如果晚两天，主路径仍应当可以单独推进。',
    '真正需要自动化的是重复校验，不是替人写更长的总结。',
    '今天的目标只是确认用户能否理解这三层分别在回答什么。',
    '一个反例已经足以提醒我们缩小表述范围。',
    '先看使用行为，再决定是否补教学说明。',
    '这条和上周的记录相似，但适用边界更窄。',
    '如果协作者无法复述这个决定，说明文档还不够清楚。',
    '保留这个未决点，等下一次真实反馈再判断。',
    '不需要把它升级成每日评价，普通记录已经够用。',
    '先让样品完整可看，底层能力继续按真实约束慢慢接。',
  ]);

  function cloneJson(value) {
    if (Array.isArray(value)) return value.map(cloneJson);
    if (value && typeof value === 'object') {
      const output = {};
      for (const key of Object.keys(value)) output[key] = cloneJson(value[key]);
      return output;
    }
    return value;
  }

  function canonicalJson(value) {
    if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
    if (value && typeof value === 'object') {
      return `{${Object.keys(value).sort().map(key => (
        `${JSON.stringify(key)}:${canonicalJson(value[key])}`
      )).join(',')}}`;
    }
    return JSON.stringify(value);
  }

  // Dependency-free synchronous SHA-256 keeps fixture IDs and aggregate refs
  // identical in browsers and Node contract tests.
  function sha256Hex(input) {
    const bytes = new TextEncoder().encode(String(input));
    const bitLength = bytes.length * 8;
    const paddedLength = Math.ceil((bytes.length + 9) / 64) * 64;
    const padded = new Uint8Array(paddedLength);
    padded.set(bytes);
    padded[bytes.length] = 0x80;
    const view = new DataView(padded.buffer);
    view.setUint32(paddedLength - 8, Math.floor(bitLength / 0x100000000), false);
    view.setUint32(paddedLength - 4, bitLength >>> 0, false);
    const constants = [
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
      0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
      0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
      0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
      0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
      0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
      0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
      0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
      0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
      0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ];
    let h0 = 0x6a09e667;
    let h1 = 0xbb67ae85;
    let h2 = 0x3c6ef372;
    let h3 = 0xa54ff53a;
    let h4 = 0x510e527f;
    let h5 = 0x9b05688c;
    let h6 = 0x1f83d9ab;
    let h7 = 0x5be0cd19;
    const words = new Uint32Array(64);
    const rotate = (value, amount) => (value >>> amount) | (value << (32 - amount));
    for (let offset = 0; offset < padded.length; offset += 64) {
      for (let index = 0; index < 16; index += 1) {
        words[index] = view.getUint32(offset + index * 4, false);
      }
      for (let index = 16; index < 64; index += 1) {
        const first = rotate(words[index - 15], 7)
          ^ rotate(words[index - 15], 18) ^ (words[index - 15] >>> 3);
        const second = rotate(words[index - 2], 17)
          ^ rotate(words[index - 2], 19) ^ (words[index - 2] >>> 10);
        words[index] = (words[index - 16] + first + words[index - 7] + second) >>> 0;
      }
      let a = h0;
      let b = h1;
      let c = h2;
      let d = h3;
      let e = h4;
      let f = h5;
      let g = h6;
      let h = h7;
      for (let index = 0; index < 64; index += 1) {
        const sumOne = rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25);
        const choice = (e & f) ^ (~e & g);
        const first = (h + sumOne + choice + constants[index] + words[index]) >>> 0;
        const sumZero = rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22);
        const majority = (a & b) ^ (a & c) ^ (b & c);
        const second = (sumZero + majority) >>> 0;
        h = g;
        g = f;
        f = e;
        e = (d + first) >>> 0;
        d = c;
        c = b;
        b = a;
        a = (first + second) >>> 0;
      }
      h0 = (h0 + a) >>> 0;
      h1 = (h1 + b) >>> 0;
      h2 = (h2 + c) >>> 0;
      h3 = (h3 + d) >>> 0;
      h4 = (h4 + e) >>> 0;
      h5 = (h5 + f) >>> 0;
      h6 = (h6 + g) >>> 0;
      h7 = (h7 + h) >>> 0;
    }
    return [h0, h1, h2, h3, h4, h5, h6, h7]
      .map(value => value.toString(16).padStart(8, '0')).join('');
  }

  function stableId(prefix, seed) {
    return `${prefix}_${sha256Hex(`memento-demo:${prefix}:${seed}`).slice(0, 24)}`;
  }

  function makeReceiptId(recordId) {
    return `rcp_${sha256Hex(canonicalJson({
      namespace: 'receipt-v1', record_id: recordId,
    })).slice(0, 24)}`;
  }

  function objectRef(kind, id, value, revision = 1) {
    return {
      kind,
      id,
      revision,
      revision_sha256: sha256Hex(canonicalJson(value)),
    };
  }

  function dateWeekday(date) {
    return WEEKDAYS[new Date(`${date}T12:00:00Z`).getUTCDay()];
  }

  function jitteredRecordTime(baseTime, date, ordinal) {
    const [hour, minute] = baseTime.split(':').map(Number);
    const seed = Number.parseInt(sha256Hex(`record-time:${date}:${ordinal}`).slice(0, 4), 16);
    const jitter = seed % 13 - 6;
    const totalMinutes = hour * 60 + minute + jitter;
    return `${String(Math.floor(totalMinutes / 60)).padStart(2, '0')}:${String(totalMinutes % 60).padStart(2, '0')}`;
  }

  function recordStatus(dayIndex, ordinal, globalIndex) {
    if (dayIndex === DATES.length - 1) {
      return [
        'merged', 'merged', 'merged', 'merged', 'merged', 'merged',
        'ready', 'ready', 'needs_review', 'original_only',
        'raw_saved', 'raw_saved', 'processing', 'processing', 'no_candidate',
      ][ordinal];
    }
    if (dayIndex === DATES.length - 2 && ordinal === DAY_COUNTS[dayIndex] - 1) return 'processing';
    if (dayIndex === DATES.length - 2 && ordinal === DAY_COUNTS[dayIndex] - 2) return 'raw_saved';
    if (globalIndex > 0 && globalIndex % 53 === 0) return 'failed';
    if (globalIndex > 0 && globalIndex % 37 === 0) return 'original_only';
    if (globalIndex > 0 && globalIndex % 31 === 0) return 'needs_review';
    if (globalIndex > 0 && globalIndex % 23 === 0) return 'no_candidate';
    if (globalIndex > 0 && globalIndex % 17 === 0) return 'ready';
    return 'merged';
  }

  function recordSource(globalIndex) {
    if (globalIndex % 17 === 0) return { type: 'file_note', app: 'Obsidian' };
    if (globalIndex % 13 === 0) return { type: 'voice_transcript', app: '语音备忘' };
    if (globalIndex % 11 === 0) return { type: 'screenshot_ocr', app: 'Chrome 截图' };
    return { type: 'text', app: ['Memento', 'Chrome', '会议记录', 'Figma'][globalIndex % 4] };
  }

  function makeSourceSpan(rawRecord) {
    const quote = rawRecord.lead;
    return {
      record_id: rawRecord.recordRef.id,
      record_revision: rawRecord.recordRef.revision,
      record_revision_sha256: rawRecord.recordRef.revision_sha256,
      source_file: `${rawRecord.date}.md`,
      line_start: rawRecord.line,
      line_end: rawRecord.line,
      quote,
      quote_sha256: sha256Hex(quote),
    };
  }

  function makeRecordText(lead, dayIndex, ordinal, globalIndex) {
    const opener = MICRO_OPENERS[(dayIndex * 5 + ordinal * 3) % MICRO_OPENERS.length];
    const detail = MICRO_DETAILS[(globalIndex * 7 + dayIndex) % MICRO_DETAILS.length];
    const followUp = FOLLOW_UPS[(globalIndex * 5 + ordinal * 2) % FOLLOW_UPS.length];
    switch (globalIndex % 7) {
      case 0: return lead;
      case 1: return `${opener}：${lead}`;
      case 2: return `${lead} ${followUp}`;
      case 3: return `${opener}${opener.endsWith('是') || opener.endsWith('发现') ? '：' : '。'}${lead} ${detail}`;
      case 4: return `${lead} ${detail} ${followUp}`;
      case 5: return `${opener}${opener.endsWith('是') || opener.endsWith('发现') ? '：' : '。'}${lead}`;
      default: return `${lead} ${detail}`;
    }
  }

  function buildRawRecords() {
    const result = [];
    let globalIndex = 0;
    DATES.forEach((date, dayIndex) => {
      const isWeekend = ['周六', '周日'].includes(dateWeekday(date));
      const times = isWeekend ? WEEKEND_TIMES : WEEKDAY_TIMES;
      let nextTextLine = 9;
      for (let ordinal = 0; ordinal < DAY_COUNTS[dayIndex]; ordinal += 1) {
        // Keep the six themes balanced across the whole 20-day window while
        // shifting the order between days so the feed does not look cyclic.
        const themeIndex = (globalIndex + dayIndex * 2) % THEME_DEFINITIONS.length;
        const theme = THEME_DEFINITIONS[themeIndex];
        const noteIndex = (dayIndex * 3 + ordinal * 5 + Math.floor(globalIndex / 6)) % theme.notes.length;
        const lead = theme.notes[noteIndex];
        const text = makeRecordText(lead, dayIndex, ordinal, globalIndex);
        const time = jitteredRecordTime(times[ordinal], date, ordinal);
        const capturedAt = `${date}T${time}:00+08:00`;
        const id = stableId('rec', `${date}:${ordinal}`);
        const revisionSeed = {
          id, capturedAt, text, source: recordSource(globalIndex), synthetic: true,
        };
        const recordRef = objectRef('source_record', id, revisionSeed);
        const source = recordSource(globalIndex);
        const status = recordStatus(dayIndex, ordinal, globalIndex);
        const themeIndexes = ['raw_saved', 'processing', 'original_only', 'no_candidate', 'failed'].includes(status)
          ? [] : [themeIndex];
        if (themeIndexes.length && (globalIndex % 7 === 0 || globalIndex % 19 === 0)) {
          const related = (themeIndex + 1 + (globalIndex % 3)) % THEME_DEFINITIONS.length;
          if (!themeIndexes.includes(related)) themeIndexes.push(related);
        }
        const note = globalIndex % 7 === 0 ? '回到下一轮样品中核对' : null;
        result.push({
          id,
          date,
          time,
          weekday: dateWeekday(date),
          capturedAt,
          text,
          lead,
          note,
          tag: TAGS[globalIndex % TAGS.length],
          themeIndex,
          themeKey: theme.key,
          status,
          themeIndexes,
          sourceType: source.type,
          sourceApp: source.app,
          contentTypes: [...new Set(themeIndexes.map(index => THEME_DEFINITIONS[index].memoryKind))],
          purposes: [...new Set(themeIndexes.map(index => THEME_DEFINITIONS[index].purpose))],
          recordRef,
          line: nextTextLine,
          synthetic: true,
        });
        nextTextLine += note ? 8 : 6;
        globalIndex += 1;
      }
    });
    return result;
  }

  function makeReceipt(rawRecord) {
    if (!['merged', 'ready', 'needs_review', 'original_only'].includes(rawRecord.status)) return null;
    const inactive = rawRecord.status === 'original_only';
    const value = {
      schema_version: '1.0',
      kind: 'memento_interpretation_receipt_revision',
      receipt_id: makeReceiptId(rawRecord.recordRef.id),
      revision: 1,
      status: inactive ? 'original_only'
        : rawRecord.status === 'needs_review' ? 'needs_review' : 'ready',
      operation: inactive ? 'original_only' : 'interpret',
      created_at: rawRecord.capturedAt,
      request_id: stableId('ireq', rawRecord.recordRef.id),
      run_id: stableId('irun', rawRecord.recordRef.id),
      record_ref: cloneJson(rawRecord.recordRef),
      user_action_id: inactive ? stableId('uact', rawRecord.recordRef.id) : null,
      summary: inactive ? null : `${THEME_DEFINITIONS[rawRecord.themeIndex].title}：${rawRecord.lead}`,
      facets: inactive ? {} : {
        content_types: cloneJson(rawRecord.contentTypes),
        topics: rawRecord.themeIndexes.map(index => THEME_DEFINITIONS[index].title),
        objects: ['产品方案'],
        stance: 'self_observation',
        cognitive_state: rawRecord.status === 'needs_review' ? 'unknown' : 'repeated',
        purposes: cloneJson(rawRecord.purposes),
      },
      memory_candidates: [],
      relation_candidates: [],
      source_spans: inactive ? [] : [makeSourceSpan(rawRecord)],
      contract_version: 'record-interpreter-v1',
      feedback_watermark_sha256: sha256Hex('memento-demo-feedback-watermark'),
      previous_revision_sha256: null,
    };
    return { ref: objectRef('interpretation_receipt', value.receipt_id, value), value };
  }

  function makeAgentMemoryRecord(definition, themeIndex, records) {
    const evidenceRecords = records.filter(record => record.themeIndexes.includes(themeIndex)
      && ['merged', 'ready', 'needs_review'].includes(record.status));
    const evidence = evidenceRecords.slice(0, 6).map(record => ({
      file: `${record.date}.md`, line: record.line, quote: makeSourceSpan(record).quote,
    }));
    const counterRecord = evidenceRecords.at(-1);
    const counterevidence = counterRecord
      ? [{
        file: `${counterRecord.date}.md`,
        line: counterRecord.line,
        quote: makeSourceSpan(counterRecord).quote,
      }] : [];
    const memoryId = stableId('mem', definition.key);
    const revisionSha256 = sha256Hex(canonicalJson({
      memoryId, statement: definition.statement, evidence, counterevidence,
    }));
    return {
      memory_id: memoryId,
      revision: 1,
      revision_sha256: revisionSha256,
      status: 'active',
      title: definition.title,
      statement: definition.statement,
      scope: definition.title,
      insight_kind: definition.insightKind,
      uncertainty: definition.uncertainty,
      evidence,
      counterevidence,
      created_at: `${DATES[Math.max(2, themeIndex * 3)]}T21:06:00+08:00`,
      provenance: {
        origin: 'agent_memory',
        run_id: stableId('arun', definition.key),
        request_id: stableId('arq', definition.key),
        operation: definition.insightKind === 'change' ? 'revise'
          : definition.insightKind === 'tension' ? 'tension' : 'reinforce',
        base_profile_ref: null,
      },
    };
  }

  function normalizeAgentMemory(record) {
    return {
      memoryId: record.memory_id,
      revision: record.revision,
      revisionSha256: record.revision_sha256,
      status: record.status,
      title: record.title,
      statement: record.statement,
      scope: record.scope,
      insightKind: record.insight_kind,
      uncertainty: record.uncertainty,
      evidence: cloneJson(record.evidence),
      counterevidence: cloneJson(record.counterevidence),
      createdAt: record.created_at,
      provenance: {
        origin: record.provenance.origin,
        runId: record.provenance.run_id,
        requestId: record.provenance.request_id,
        operation: record.provenance.operation,
        baseProfileRef: null,
      },
    };
  }

  function buildAgentProfile(records) {
    const memoryRecords = THEME_DEFINITIONS.map((definition, index) => (
      makeAgentMemoryRecord(definition, index, records)
    ));
    const profileHash = sha256Hex(canonicalJson({
      version: FIXTURE_VERSION,
      updatedAt: `${END_DATE}T19:42:00+08:00`,
      memories: memoryRecords,
    }));
    const raw = {
      schema_version: '1.0',
      kind: 'remember_agent_profile',
      projection_version: 'remember-agent-profile-v1.0',
      projection_updated_at: `${END_DATE}T19:42:00+08:00`,
      profile_sha256: profileHash,
      memories: memoryRecords,
      latest_run: null,
      stats: {
        legacy_seen: 0,
        stored_seen: memoryRecords.length,
        stored_active: memoryRecords.length,
        tombstones: 0,
        invalid_excluded: 0,
        user_actions_seen: 0,
        user_actions_valid: 0,
        user_actions_applied: 0,
        active: memoryRecords.length,
      },
    };
    const memories = memoryRecords.map(normalizeAgentMemory);
    return {
      raw,
      normalized: {
        schemaVersion: '1.0',
        kind: raw.kind,
        projectionVersion: raw.projection_version,
        projectionUpdatedAt: raw.projection_updated_at,
        profileSha256: profileHash,
        memories,
        latestRun: null,
        stats: cloneJson(raw.stats),
      },
      memories,
    };
  }

  function buildReusableMemories(records, receiptByRecordId) {
    const items = [];
    THEME_DEFINITIONS.forEach((definition, themeIndex) => {
      const evidenceRecords = records.filter(record => record.themeIndexes.includes(themeIndex)
        && receiptByRecordId.has(record.id)
        && receiptByRecordId.get(record.id).value.status === 'ready');
      definition.memoryStatements.forEach((statement, memoryIndex) => {
        const selected = evidenceRecords.slice(memoryIndex * 2, memoryIndex * 2 + 2);
        const value = {
          schema_version: '1.0',
          kind: 'memento_reusable_memory_revision',
          memory_id: stableId('rmem', `${definition.key}:${memoryIndex}`),
          revision: 1,
          status: 'active',
          operation: 'new',
          created_at: `${selected.at(-1)?.date || END_DATE}T21:10:00+08:00`,
          statement,
          memory_kind: definition.memoryKind,
          topics: [definition.title],
          purposes: [definition.purpose],
          uncertainty: definition.uncertainty,
          source_spans: selected.map(makeSourceSpan),
          origin_receipt_refs: selected.map(record => cloneJson(receiptByRecordId.get(record.id).ref)),
          provenance: {
            origin: 'daily_integrator',
            run_id: stableId('drun', `${definition.key}:${memoryIndex}`),
            bundle_id: `db_${(selected.at(-1)?.date || END_DATE).replaceAll('-', '')}`,
            bundle_revision: 1,
            user_action_id: null,
          },
          previous_revision_sha256: null,
        };
        items.push({ ref: objectRef('reusable_memory', value.memory_id, value), value, themeIndex });
      });
    });
    return items;
  }

  function buildRelations(records, understandingRefs, reusableMemories) {
    const specifications = [];
    reusableMemories.forEach((memory, index) => {
      specifications.push({
        fromRef: memory.ref,
        toRef: understandingRefs[memory.themeIndex],
        type: 'supports',
        statement: `${memory.value.statement} 这条可用记忆支持“${THEME_DEFINITIONS[memory.themeIndex].title}”的当前版本。`,
        themeIndex: memory.themeIndex,
        seed: `memory-to-theme:${index}`,
      });
    });
    specifications.push(
      {
        fromRef: understandingRefs[0], toRef: understandingRefs[1], type: 'same_topic',
        statement: '产品决策与证据优先共享一组可核查的判断基础。', themeIndex: 0, seed: 'decision-evidence',
      },
      {
        fromRef: understandingRefs[1], toRef: understandingRefs[4], type: 'supports',
        statement: '证据定位规则支持研究材料的筛选与写作边界。', themeIndex: 4, seed: 'evidence-research',
      },
      {
        fromRef: understandingRefs[2], toRef: understandingRefs[3], type: 'scope_boundary',
        statement: '长期积累的可复用目标，以协作中可被他人接手为边界。', themeIndex: 3, seed: 'accumulation-collaboration',
      },
      {
        fromRef: understandingRefs[5], toRef: understandingRefs[0], type: 'revises',
        statement: '短周期样品会依据观测结果修订下一轮产品决策。', themeIndex: 5, seed: 'iteration-decision',
      },
    );
    return specifications.map((specification, index) => {
      const source = records.find(record => record.themeIndexes.includes(specification.themeIndex)
        && ['merged', 'ready'].includes(record.status));
      const value = {
        schema_version: '1.0',
        kind: 'memento_relation_revision',
        relation_id: stableId('rel', specification.seed),
        revision: 1,
        status: 'active',
        operation: 'new',
        created_at: `${source?.date || END_DATE}T21:${String(20 + (index % 30)).padStart(2, '0')}:00+08:00`,
        type: specification.type,
        from_ref: cloneJson(specification.fromRef),
        to_ref: cloneJson(specification.toRef),
        direction: specification.type === 'same_topic' ? 'undirected' : 'directed',
        statement: specification.statement,
        uncertainty: index % 5 === 0 ? 'medium' : 'low',
        source_spans: source ? [makeSourceSpan(source)] : [],
        valid_from: source?.date || END_DATE,
        provenance: {
          origin: 'daily_integrator',
          run_id: stableId('drun', specification.seed),
          bundle_id: `db_${(source?.date || END_DATE).replaceAll('-', '')}`,
          bundle_revision: 1,
          user_action_id: null,
        },
        previous_revision_sha256: null,
      };
      return { ref: objectRef('relation', value.relation_id, value), value };
    });
  }

  function recordSummary(rawRecord) {
    if (['raw_saved', 'processing', 'original_only', 'no_candidate', 'failed'].includes(rawRecord.status)) {
      return null;
    }
    return `${THEME_DEFINITIONS[rawRecord.themeIndex].title}：${rawRecord.lead}`;
  }

  function buildRecordView(rawRecord, receiptByRecordId, understandingRefs, reusableMemories) {
    const receipt = receiptByRecordId.get(rawRecord.id) || null;
    const inactive = ['original_only', 'no_candidate'].includes(rawRecord.status);
    const interpreted = !['raw_saved', 'processing', 'failed'].includes(rawRecord.status) && !inactive;
    const merged = rawRecord.status === 'merged';
    const memories = rawRecord.themeIndexes.map(themeIndex => (
      reusableMemories.find(item => item.themeIndex === themeIndex)
    )).filter(Boolean);
    return {
      record_ref: cloneJson(rawRecord.recordRef),
      receipt_ref: receipt ? cloneJson(receipt.ref) : null,
      captured_at: rawRecord.capturedAt,
      source_type: rawRecord.sourceType,
      source_app: rawRecord.sourceApp,
      status: rawRecord.status,
      summary: recordSummary(rawRecord),
      content_types: interpreted ? cloneJson(rawRecord.contentTypes) : [],
      topics: interpreted
        ? rawRecord.themeIndexes.map(index => THEME_DEFINITIONS[index].title) : [],
      purposes: interpreted ? cloneJson(rawRecord.purposes) : [],
      memory_refs: merged ? memories.map(memory => cloneJson(memory.ref)) : [],
      understanding_refs: merged
        ? rawRecord.themeIndexes.map(index => cloneJson(understandingRefs[index])) : [],
      id: rawRecord.id,
      date: rawRecord.date,
      time: rawRecord.time,
      weekday: rawRecord.weekday,
      text: rawRecord.text,
      tag: rawRecord.tag,
      note: rawRecord.note,
      synthetic: true,
    };
  }

  function exactHomeRecord(record) {
    return {
      record_ref: cloneJson(record.record_ref),
      receipt_ref: cloneJson(record.receipt_ref),
      captured_at: record.captured_at,
      source_type: record.source_type,
      source_app: record.source_app,
      status: record.status,
      summary: record.summary,
      content_types: cloneJson(record.content_types),
      topics: cloneJson(record.topics),
      purposes: cloneJson(record.purposes),
      memory_refs: cloneJson(record.memory_refs),
      understanding_refs: cloneJson(record.understanding_refs),
    };
  }

  function markdownFile(date, dayRecords) {
    const frontmatter = `---\nmemento_demo: true\nsynthetic: true\ndate: ${date}\n---\n`;
    const blocks = dayRecords.map(record => {
      const note = record.note ? `\n\n> 备注: ${record.note}` : '';
      return `## ${record.time} · ${record.weekday} · ${record.source_app} · #${record.tag}\n\n${record.text}${note}`;
    });
    return {
      name: `${date}.md`,
      date,
      text: `${frontmatter}${blocks.length ? `\n${blocks.join('\n\n---\n\n')}\n` : ''}`,
      synthetic: true,
    };
  }

  function buildFixture() {
    const rawRecords = buildRawRecords();
    const receiptItems = rawRecords.map(makeReceipt).filter(Boolean);
    const receiptByRecordId = new Map(receiptItems.map(item => [item.value.record_ref.id, item]));
    const profile = buildAgentProfile(rawRecords);
    const understandingRefs = profile.memories.map(memory => ({
      kind: 'understanding',
      id: memory.memoryId,
      revision: memory.revision,
      revision_sha256: memory.revisionSha256,
    }));
    const reusableMemories = buildReusableMemories(rawRecords, receiptByRecordId);
    const relations = buildRelations(rawRecords, understandingRefs, reusableMemories);
    const records = rawRecords.map(record => (
      buildRecordView(record, receiptByRecordId, understandingRefs, reusableMemories)
    ));
    const recordById = new Map(records.map(record => [record.id, record]));

    const themes = THEME_DEFINITIONS.map((definition, index) => {
      const candidates = rawRecords.filter(record => record.themeIndexes.includes(index)
        && ['merged', 'ready', 'needs_review'].includes(record.status));
      const evidenceTarget = 26 + (index % 4) * 2;
      const evidence = candidates.slice(0, evidenceTarget).map(record => record.id);
      const counter = candidates.slice(-(1 + (index % 2))).map(record => record.id);
      const themeMemories = reusableMemories.filter(memory => memory.themeIndex === index);
      return {
        id: `theme_${definition.key}`,
        understandingId: understandingRefs[index].id,
        title: definition.title,
        tendency: definition.statement,
        boundary: definition.boundary,
        terrain: cloneJson(definition.terrain),
        subpeaks: definition.subpeaks.map((subpeak, subpeakIndex) => ({
          ...cloneJson(subpeak),
          memoryId: themeMemories[subpeakIndex]?.ref.id || '',
        })),
        evidenceRecordIds: evidence,
        counterRecordIds: counter,
        relatedPortraitIds: index < 2 ? ['portrait_direction', 'portrait_values']
          : index < 4 ? ['portrait_direction', 'portrait_handoff']
            : ['portrait_revision', 'portrait_handoff'],
        synthetic: true,
      };
    });

    const portrait = [
      {
        id: 'portrait_direction',
        title: '从完成任务走向定义方法',
        maturity: 'forming',
        statement: '正在把“完成一次任务”转成“沉淀一套可复用判断方法”，并让下一次决策可以从已有依据继续。',
        themeIds: ['theme_product_decision', 'theme_long_term_accumulation'],
        boundary: '它描述逐步形成的工作方向，不等于已经完成，也不覆盖生活领域。',
      },
      {
        id: 'portrait_values',
        title: '可验证比漂亮结论更重要',
        maturity: 'stable',
        statement: '价值排序更靠近可验证、可追溯和能够被重新检查的成果，愿意为此保留不确定项。',
        themeIds: ['theme_evidence_first', 'theme_research_method'],
        boundary: '这条理解只适用于需要判断和交付质量的工作场景，仍需由后续记录持续校验。',
      },
      {
        id: 'portrait_revision',
        title: '允许理解随证据修订',
        maturity: 'stable',
        statement: '愿意保留反例和变化轨迹，让当前理解在新证据出现时被修订，而不是追求一次性定型。',
        themeIds: ['theme_evidence_first', 'theme_research_method', 'theme_iteration_rhythm'],
        boundary: '保留反例与修订空间；它描述当前做法，不定义固定人格。',
      },
      {
        id: 'portrait_handoff',
        title: '成果应当可以被接手',
        maturity: 'forming',
        statement: '未来方向更靠近定义系统如何做出好判断，并让协作者能够理解、复用和继续推进。',
        themeIds: ['theme_long_term_accumulation', 'theme_collaboration_boundary', 'theme_iteration_rhythm'],
        boundary: '这条更接近职业方向，不能据此推断所有协作情境。',
      },
    ].map(item => ({ ...item, synthetic: true }));

    const landscapeInput = {
      agent_profile_sha256: profile.normalized.profileSha256,
      reusable_memory_head_sha256: sha256Hex(canonicalJson(
        reusableMemories.map(item => item.ref).sort((left, right) => left.id.localeCompare(right.id))
      )),
      relation_head_sha256: sha256Hex(canonicalJson(
        relations.map(item => item.ref).sort((left, right) => left.id.localeCompare(right.id))
      )),
      user_action_watermark_sha256: sha256Hex('memento-demo-user-action-watermark'),
    };
    const landscape = {
      schema_version: '1.0',
      kind: 'memento_landscape_snapshot',
      snapshot_id: stableId('lnd', END_DATE),
      created_at: `${END_DATE}T19:42:00+08:00`,
      as_of: END_DATE,
      projection_version: 'cognitive-landscape-v1',
      input_hashes: landscapeInput,
      summary: {
        active_understandings: understandingRefs.length,
        recent_changes: 2,
        observing_candidates: 3,
      },
      terrain: {
        algorithm_version: 'stable-anchor-kde-v1',
        grid_size: 48,
        contour_levels: 12,
        coordinate_space: 'normalized_0_1',
      },
      peaks: THEME_DEFINITIONS.map((definition, index) => ({
        peak_id: `peak_${understandingRefs[index].id.slice(4)}`,
        understanding_ref: cloneJson(understandingRefs[index]),
        x: definition.x,
        y: definition.y,
        elevation: definition.elevation,
        evidence_count: themes[index].evidenceRecordIds.length,
        counterevidence_count: themes[index].counterRecordIds.length,
        recent_change: ['change'].includes(definition.insightKind),
        lifecycle: definition.insightKind === 'tension' ? 'tension' : 'active',
      })),
      nodes: reusableMemories.map((memory, index) => {
        const theme = THEME_DEFINITIONS[memory.themeIndex];
        const themeMemoryIndex = reusableMemories.slice(0, index)
          .filter(item => item.themeIndex === memory.themeIndex).length;
        const subpeak = theme.subpeaks[themeMemoryIndex];
        return {
          memory_ref: cloneJson(memory.ref),
          x: subpeak?.x ?? theme.x,
          y: subpeak?.y ?? theme.y,
          state: 'committed',
          recent: Boolean(subpeak?.recent),
        };
      }),
      edges: relations.map(item => ({
        relation_ref: cloneJson(item.ref),
        from_id: item.value.from_ref.id,
        to_id: item.value.to_ref.id,
        type: item.value.type,
      })),
      previous_snapshot_sha256: sha256Hex('memento-demo-landscape-previous'),
    };
    const landscapeSha256 = sha256Hex(canonicalJson(landscape));
    const todayRecords = records.filter(record => record.date === END_DATE).map(exactHomeRecord);
    const todayReceiptRefs = todayRecords.map(record => record.receipt_ref).filter(Boolean)
      .sort((left, right) => left.id.localeCompare(right.id));
    const dailyBundleHeadSha256 = sha256Hex('memento-demo-daily-bundle-head');
    const home = {
      schema_version: '1.0',
      kind: 'memento_home_projection',
      projection_version: 'cognitive-secretary-home-v1',
      generated_at: `${END_DATE}T19:42:00+08:00`,
      local_date: END_DATE,
      input_hashes: {
        record_head_sha256: sha256Hex(canonicalJson(todayRecords.map(record => record.record_ref))),
        receipt_head_sha256: sha256Hex(canonicalJson(todayReceiptRefs)),
        daily_bundle_head_sha256: dailyBundleHeadSha256,
        agent_profile_sha256: profile.normalized.profileSha256,
        landscape_snapshot_sha256: landscapeSha256,
        user_action_watermark_sha256: landscapeInput.user_action_watermark_sha256,
      },
      landscape_ref: {
        snapshot_id: landscape.snapshot_id,
        snapshot_sha256: landscapeSha256,
      },
      landscape_summary: cloneJson(landscape.summary),
      today_status: {
        saved: todayRecords.length,
        interpreted: todayRecords.filter(record => (
          record.receipt_ref !== null || record.status === 'no_candidate'
        )).length,
        merged: todayRecords.filter(record => record.status === 'merged').length,
        needs_review: todayRecords.filter(record => record.status === 'needs_review').length,
        daily_run_status: 'not_started',
      },
      records: todayRecords,
      schedule: {
        enabled: false,
        hour: 21,
        minute: 0,
        next_due_at: `${END_DATE}T21:00:00+08:00`,
        last_run_status: 'committed',
      },
      warnings: [],
    };
    const projectionAuthority = {
      agent_profile_sha256: profile.normalized.profileSha256,
      active_understanding_refs: cloneJson(understandingRefs),
      current_memory_refs: reusableMemories.map(item => cloneJson(item.ref))
        .sort((left, right) => left.id.localeCompare(right.id)),
      current_relation_refs: relations.map(item => cloneJson(item.ref))
        .sort((left, right) => left.id.localeCompare(right.id)),
      user_action_watermark_sha256: landscapeInput.user_action_watermark_sha256,
      today_record_refs: todayRecords.map(record => cloneJson(record.record_ref)),
      today_receipt_refs: cloneJson(todayReceiptRefs),
      daily_bundle_head_sha256: dailyBundleHeadSha256,
    };

    const history = DATES.map(date => {
      const dayRecords = records.filter(record => record.date === date);
      const statuses = {};
      dayRecords.forEach(record => { statuses[record.status] = (statuses[record.status] || 0) + 1; });
      return {
        date,
        weekday: dateWeekday(date),
        records: dayRecords.map(record => record.id),
        count: dayRecords.length,
        empty: dayRecords.length === 0,
        statuses,
        synthetic: true,
      };
    });
    const legacyFiles = history.filter(day => day.count > 0).map(day => (
      markdownFile(day.date, day.records.map(id => recordById.get(id)))
    ));
    const rawRecordsById = Object.fromEntries(rawRecords.map(record => [record.id, {
      text: record.text,
      capturedAt: record.capturedAt,
      date: record.date,
      sourceType: record.sourceType,
      sourceApp: record.sourceApp,
      recordRef: cloneJson(record.recordRef),
      synthetic: true,
    }]));
    const changes = [
      {
        id: 'change_research_boundary',
        date: '2026-08-14',
        kind: 'revision',
        themeId: 'theme_research_method',
        before: '先收集尽可能多的材料，再统一整理。',
        after: '先固定筛选门槛与证据定位，再扩大材料规模。',
        evidenceRecordIds: themes[4].evidenceRecordIds.slice(-3),
        synthetic: true,
      },
      {
        id: 'change_iteration_scope',
        date: '2026-08-17',
        kind: 'revision',
        themeId: 'theme_iteration_rhythm',
        before: '视觉与自动化同步推进。',
        after: '先收束可点击样品，再逐步接入自动化。',
        evidenceRecordIds: themes[5].evidenceRecordIds.slice(-3),
        synthetic: true,
      },
      {
        id: 'change_collaboration_tension',
        date: '2026-08-13',
        kind: 'tension',
        themeId: 'theme_collaboration_boundary',
        before: null,
        after: '主动推进有助于暴露风险，也需要避免责任长期集中在一个人身上。',
        evidenceRecordIds: themes[3].evidenceRecordIds.slice(-2),
        synthetic: true,
      },
    ];

    return {
      fixtureVersion: FIXTURE_VERSION,
      mode: 'synthetic',
      syntheticNotice: SYNTHETIC_NOTICE,
      capabilities: {
        persistence: false,
        filesystem: false,
        externalCalls: false,
        formalWrites: false,
      },
      window: { start: START_DATE, end: END_DATE, days: DATES.length },
      stats: {
        totalRecords: records.length,
        activeDays: history.filter(day => !day.empty).length,
        emptyDays: history.filter(day => day.empty).length,
        todayRecords: todayRecords.length,
      },
      home,
      landscape,
      landscapeSha256,
      projectionAuthority,
      agentProfile: profile.normalized,
      agentProfileRecord: profile.raw,
      agentMemories: profile.memories,
      receipts: receiptItems,
      reusableMemories: reusableMemories.map(({ ref, value }) => ({ ref, value })),
      relations,
      records,
      rawRecordsById,
      legacyFiles,
      history,
      themes,
      portrait,
      changes,
    };
  }

  const BASE_FIXTURE = buildFixture();

  function createFixture() {
    return cloneJson(BASE_FIXTURE);
  }

  function appendLocalRecord(fixture, input) {
    if (!fixture || fixture.mode !== 'synthetic') {
      throw new TypeError('appendLocalRecord 只接受显式 synthetic fixture');
    }
    const text = typeof input?.text === 'string' ? input.text.trim() : '';
    const capturedAt = typeof input?.capturedAt === 'string' ? input.capturedAt : '';
    if (!text || Array.from(text).length > 2000) throw new TypeError('演示记录正文无效');
    if (!/^2026-08-18T\d{2}:\d{2}(?::\d{2})?\+08:00$/.test(capturedAt)) {
      throw new TypeError('演示记录时间必须位于固定演示日 2026-08-18');
    }
    const next = cloneJson(fixture);
    let nonce = 0;
    let id = stableId('rec', `local:${capturedAt}:${text}:${nonce}`);
    while (next.rawRecordsById[id]) {
      nonce += 1;
      id = stableId('rec', `local:${capturedAt}:${text}:${nonce}`);
    }
    const recordRef = objectRef('source_record', id, { id, capturedAt, text, synthetic: true });
    const time = capturedAt.slice(11, 16);
    const view = {
      record_ref: recordRef,
      receipt_ref: null,
      captured_at: capturedAt,
      source_type: 'text',
      source_app: 'Memento',
      status: 'raw_saved',
      summary: null,
      content_types: [],
      topics: [],
      purposes: [],
      memory_refs: [],
      understanding_refs: [],
      id,
      date: END_DATE,
      time,
      weekday: dateWeekday(END_DATE),
      text,
      tag: '灵感',
      note: null,
      synthetic: true,
    };
    next.records.push(view);
    next.records.sort((left, right) => left.captured_at.localeCompare(right.captured_at));
    next.rawRecordsById[id] = {
      text,
      capturedAt,
      date: END_DATE,
      sourceType: 'text',
      sourceApp: 'Memento',
      recordRef: cloneJson(recordRef),
      synthetic: true,
    };
    const todayHistory = next.history.find(day => day.date === END_DATE);
    todayHistory.records.push(id);
    todayHistory.count += 1;
    todayHistory.empty = false;
    todayHistory.statuses.raw_saved = (todayHistory.statuses.raw_saved || 0) + 1;
    const file = next.legacyFiles.find(item => item.date === END_DATE);
    const block = `## ${time} · ${dateWeekday(END_DATE)} · Memento · #灵感\n\n${text}\n`;
    if (file) file.text = `${file.text.trimEnd()}\n\n---\n\n${block}`;
    else next.legacyFiles.push(markdownFile(END_DATE, [view]));
    next.home.records.push(exactHomeRecord(view));
    next.home.records.sort((left, right) => left.captured_at.localeCompare(right.captured_at));
    next.home.generated_at = capturedAt.length === 22
      ? capturedAt.replace('+08:00', ':00+08:00') : capturedAt;
    next.home.today_status.saved = next.home.records.length;
    next.home.input_hashes.record_head_sha256 = sha256Hex(canonicalJson(
      next.home.records.map(record => record.record_ref)
    ));
    next.projectionAuthority.today_record_refs = next.home.records
      .map(record => cloneJson(record.record_ref));
    next.stats.totalRecords += 1;
    next.stats.todayRecords += 1;
    return next;
  }

  return Object.freeze({
    FIXTURE_VERSION,
    START_DATE,
    END_DATE,
    createFixture,
    appendLocalRecord,
  });
});
