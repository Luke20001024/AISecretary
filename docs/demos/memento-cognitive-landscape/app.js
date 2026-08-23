(function () {
  'use strict';

  const SVG_NS = 'http://www.w3.org/2000/svg';

  const snapshots = [
    {
      id: '2026-04-28',
      short: '04/28',
      title: '初次形成',
      event: '两项理解完成首次提交',
      summary: { understandings: 2, changes: 2, observing: 1 }
    },
    {
      id: '2026-05-10',
      short: '05/10',
      title: '范围收窄',
      event: '记录方式形成，系统边界完成一次范围修订',
      summary: { understandings: 3, changes: 1, observing: 2 }
    },
    {
      id: '2026-05-18',
      short: '05/18',
      title: '新增与修订',
      event: '长期成长形成，产品判断增加一条反例并完成修订',
      summary: { understandings: 4, changes: 2, observing: 2 }
    },
    {
      id: '2026-08-17',
      short: '今天',
      title: '当前',
      event: '今天新增 3 个待归并记忆点，长期理解没有变化',
      summary: { understandings: 4, changes: 1, observing: 2 }
    }
  ];

  const peaks = [
    {
      id: 'peak_system',
      name: '系统边界',
      x: 255,
      y: 205,
      rx: 139,
      ry: 88,
      phase: .4,
      born: 0,
      changed: 1,
      recentAtCurrent: false,
      levels: [4, 6, 6, 7],
      evidence: [6, 11, 14, 18],
      counter: [0, 1, 1, 1],
      scope: 'AI 产品与自动化边界',
      statement: '在设计 AI 产品时，你会先划清系统负责什么、用户保留什么，再决定自动化程度。',
      change: '05/10 将“本地存储”扩展为“用户决策权与系统执行权分离”。',
      versions: [
        { version: 'v3', date: '05/10', text: '用户决策权与系统执行权需要分开。' },
        { version: 'v2', date: '05/02', text: '写入前需要来源校验与用户确认。' },
        { version: 'v1', date: '04/28', text: '长期记忆只保存在本地。' }
      ]
    },
    {
      id: 'peak_product',
      name: '产品判断',
      x: 705,
      y: 196,
      rx: 154,
      ry: 96,
      phase: 1.7,
      born: 0,
      changed: 2,
      recentAtCurrent: true,
      levels: [4, 6, 8, 8],
      evidence: [5, 10, 17, 24],
      counter: [0, 0, 1, 1],
      scope: '产品策略与方案评审',
      statement: '做产品判断时，你会优先辨认长期价值和系统影响，再决定是否接受短期收益。',
      change: '05/18 新增“可验证的小步快跑”作为不确定场景下的适用边界。',
      versions: [
        { version: 'v3', date: '05/18', text: '长期价值与系统影响优先，并保留小步验证。' },
        { version: 'v2', date: '05/10', text: '先看长期价值，再讨论功能方案。' },
        { version: 'v1', date: '04/28', text: '产品决策前先明确目标与护栏。' }
      ]
    },
    {
      id: 'peak_record',
      name: '记录方式',
      x: 315,
      y: 465,
      rx: 142,
      ry: 91,
      phase: 2.5,
      born: 1,
      changed: 1,
      recentAtCurrent: false,
      levels: [0, 4, 6, 7],
      evidence: [0, 7, 12, 16],
      counter: [0, 1, 1, 1],
      scope: '个人记录与信息整理',
      statement: '你希望记录带来秩序，同时保留随手留下内容的轻松感。',
      change: '05/10 将“每条记录都要行动”收窄为“先被接住，再决定是否继续处理”。',
      versions: [
        { version: 'v2', date: '05/10', text: '先接住记录，再决定是否继续处理。' },
        { version: 'v1', date: '05/06', text: '记录需要变成下一步才有价值。' }
      ]
    },
    {
      id: 'peak_growth',
      name: '长期成长',
      x: 770,
      y: 475,
      rx: 142,
      ry: 90,
      phase: 3.2,
      born: 2,
      changed: 2,
      recentAtCurrent: false,
      levels: [0, 0, 4, 6],
      evidence: [0, 0, 8, 13],
      counter: [0, 0, 0, 1],
      scope: '职业成长与能力验证',
      statement: '你更关注能力能否迁移和复用，而不是团队是否离不开你。',
      change: '05/18 从“证明贡献”修订为“验证可迁移能力与可复用机制”。',
      versions: [
        { version: 'v2', date: '05/18', text: '用可迁移能力和可复用机制验证成长。' },
        { version: 'v1', date: '05/12', text: '希望团队看见自己的长期贡献。' }
      ]
    }
  ];

  const observationZones = [
    { id: 'observe_expression', x: 520, y: 360, rx: 78, ry: 48, phase: 1.1, born: 1, name: '表达与创作', detail: '3 个候选记忆' },
    { id: 'observe_relationships', x: 1010, y: 380, rx: 72, ry: 52, phase: 2.1, born: 2, name: '关系边界', detail: '2 个候选记忆' }
  ];

  const memories = [
    {
      id: 'mem_source_local', x: 150, y: 126, born: 0, status: 'committed', corrected: false,
      date: '04/24', time: '09:18', source: '文字', type: '我的决定', topic: '本地存储', use: '系统设计',
      summary: '长期记忆只保存在本地，写入前需要用户确认。',
      raw: '长期记忆仅保存在本地 JSON 文件，且写入前需要用户确认。'
    },
    {
      id: 'mem_source_check', x: 333, y: 132, born: 0, status: 'committed', corrected: false,
      date: '04/26', time: '14:10', source: 'Chrome', type: '我的观察', topic: '来源校验', use: '方案评审',
      summary: '所有人物理解都需要回到可核对的原始记录。',
      raw: '所有关于我的理解都需要能回到原始记录，不能只靠 AI 的概括。'
    },
    {
      id: 'mem_metric_guardrail', x: 635, y: 112, born: 0, status: 'committed', corrected: true,
      date: '04/27', time: '16:40', source: 'Chrome', type: '我的方法', topic: '指标设计', use: '产品决策',
      summary: '先明确目标指标、护栏指标与验证周期。',
      raw: '做产品决策前，先明确目标指标、护栏指标和验证周期，再讨论功能方案。'
    },
    {
      id: 'mem_long_value', x: 780, y: 116, born: 0, status: 'committed', corrected: false,
      date: '04/28', time: '11:05', source: '语音', type: '我的判断', topic: '长期价值', use: '方案选择',
      summary: '短期收益需要放在长期价值与系统影响中判断。',
      raw: '我不想只因为短期收益做一个决定，还要看它对长期价值和整个系统的影响。'
    },
    {
      id: 'mem_user_priority', x: 360, y: 246, born: 1, status: 'committed', corrected: true,
      date: '05/02', time: '20:12', source: 'Chrome', type: '我的边界', topic: '用户控制', use: '产品原则',
      summary: '用户修改和删除后的版本，应成为后续判断基线。',
      raw: '用户修改或者删除过的理解，后续 Agent 必须尊重，不要重新覆盖。'
    },
    {
      id: 'mem_agent_boundary', x: 492, y: 214, born: 1, status: 'committed', corrected: false,
      date: '05/04', time: '10:46', source: 'Chrome', type: '我的判断', topic: 'Agent 边界', use: '架构设计',
      summary: 'Workflow 控制流程，Agent 在重要节点做判断。',
      raw: '重要的节点由 Agent 来做，其他的由 Workflow 来做，最后再调用模型完成。'
    },
    {
      id: 'mem_record_relief', x: 226, y: 386, born: 1, status: 'committed', corrected: false,
      date: '05/06', time: '22:20', source: '语音', type: '我的感受', topic: '记录压力', use: '个人工具',
      summary: '记录本身应当有价值，不必每次都变成任务。',
      raw: '我希望记录下来本身就有价值，不要让我每次记完都像多了一个任务。'
    },
    {
      id: 'mem_record_order', x: 365, y: 404, born: 1, status: 'committed', corrected: false,
      date: '05/08', time: '08:45', source: 'Chrome', type: '我的观察', topic: '秩序感', use: '个人复盘',
      summary: '记录需要被整理，才能逐渐形成可回看的秩序。',
      raw: '感觉自己少了一些秩序感，我希望记录能逐渐被整理出来。'
    },
    {
      id: 'mem_small_scope', x: 815, y: 272, born: 2, status: 'committed', corrected: false,
      date: '05/10', time: '15:24', source: 'Chrome', type: '反例', topic: '小范围方案', use: '适用边界',
      summary: '范围很小时，可以先搭出完整方案再统一检查。',
      raw: '这次范围很小，我先搭出完整方案再统一检查。'
    },
    {
      id: 'mem_review_failure', x: 621, y: 267, born: 2, status: 'committed', corrected: false,
      date: '05/12', time: '09:36', source: 'Chrome', type: '我的方法', topic: '方案评审', use: '评审准备',
      summary: '评审前先检查反例与失败条件。',
      raw: '在评审方案时，我会先检查反例和失败条件，再评估完整方案。'
    },
    {
      id: 'mem_transferable_growth', x: 679, y: 391, born: 2, status: 'committed', corrected: true,
      date: '05/14', time: '18:10', source: '语音', type: '我的追问', topic: '能力迁移', use: '职业复盘',
      summary: '成长需要通过可迁移能力与可复用机制判断。',
      raw: '我不想沉溺于大家都觉得没我不行，我希望看到自己的成长。'
    },
    {
      id: 'mem_team_without_me', x: 844, y: 410, born: 2, status: 'committed', corrected: false,
      date: '05/16', time: '11:32', source: 'Chrome', type: '我的标准', topic: '团队机制', use: '能力验证',
      summary: '团队能否在没有我时继续运转，是机制是否成立的证据。',
      raw: '我想看团队没有我以后是不是仍然能运转，这比大家都依赖我更能说明成长。'
    },
    {
      id: 'mem_reusable_mechanism', x: 745, y: 545, born: 2, status: 'committed', corrected: false,
      date: '05/18', time: '14:40', source: 'Chrome', type: '我的结论', topic: '可复用机制', use: '年度复盘',
      summary: '用可复用机制和决策质量验证长期贡献。',
      raw: '应该用可复用机制、决策质量和结果证据来验证长期贡献。'
    },
    {
      id: 'mem_ui_understanding', x: 555, y: 318, born: 3, status: 'committed', corrected: false,
      date: '08/17', time: '09:42', source: 'Chrome', type: '我的想法', topic: '产品首页', use: 'UI 重构',
      summary: '首页应该先展示 Memento 如何理解我。',
      raw: '首页应该先让我看见 Memento 如何理解我，同时保留今天的记录。'
    },
    {
      id: 'mem_pending_map', x: 936, y: 288, born: 3, status: 'pending', corrected: false,
      date: '08/17', time: '10:50', source: '语音', type: '初步整理', topic: '认知地图', use: '等待今日归并',
      summary: '用地形表达理解与证据的长期聚类。',
      raw: '认知地图更像一个知识图谱，峰代表认知取向，证据都来自我的记录。'
    },
    {
      id: 'mem_pending_realtime', x: 515, y: 530, born: 3, status: 'pending', corrected: false,
      date: '08/17', time: '11:08', source: 'Chrome', type: '初步整理', topic: '即时回执', use: '等待今日归并',
      summary: '记录后需要立即看见 AI 把它整理成了什么。',
      raw: '如果每天整理以后才看到结果，我会不知道每条原始记录被整理成什么样。'
    }
  ];

  const relations = [
    { id: 'rel_local_system', memoryId: 'mem_source_local', peakId: 'peak_system', type: 'support', born: 0, date: '04/28', note: '支持系统边界的本地存储原则' },
    { id: 'rel_check_system', memoryId: 'mem_source_check', peakId: 'peak_system', type: 'support', born: 0, date: '04/28', note: '支持来源可追溯要求' },
    { id: 'rel_metric_product', memoryId: 'mem_metric_guardrail', peakId: 'peak_product', type: 'support', born: 0, date: '04/28', note: '支持先明确目标与护栏的判断' },
    { id: 'rel_value_product', memoryId: 'mem_long_value', peakId: 'peak_product', type: 'support', born: 0, date: '04/28', note: '支持长期价值优先' },
    { id: 'rel_user_system', memoryId: 'mem_user_priority', peakId: 'peak_system', type: 'revision', born: 1, date: '05/10', note: '将系统边界从本地存储修订到用户决策权' },
    { id: 'rel_agent_system', memoryId: 'mem_agent_boundary', peakId: 'peak_system', type: 'support', born: 1, date: '05/10', note: '支持 Workflow 与 Agent 的职责分离' },
    { id: 'rel_agent_product', memoryId: 'mem_agent_boundary', peakId: 'peak_product', type: 'boundary', born: 1, date: '05/10', note: '为产品判断补充执行边界' },
    { id: 'rel_relief_record', memoryId: 'mem_record_relief', peakId: 'peak_record', type: 'support', born: 1, date: '05/10', note: '支持轻量记录原则' },
    { id: 'rel_order_record', memoryId: 'mem_record_order', peakId: 'peak_record', type: 'counter', born: 1, date: '05/10', note: '提醒记录仍需要形成秩序' },
    { id: 'rel_small_product', memoryId: 'mem_small_scope', peakId: 'peak_product', type: 'counter', born: 2, date: '05/18', note: '小范围方案构成适用边界' },
    { id: 'rel_failure_product', memoryId: 'mem_review_failure', peakId: 'peak_product', type: 'support', born: 2, date: '05/18', note: '支持先检查反例与失败条件' },
    { id: 'rel_transfer_growth', memoryId: 'mem_transferable_growth', peakId: 'peak_growth', type: 'revision', born: 2, date: '05/18', note: '将贡献证明修订为能力迁移' },
    { id: 'rel_team_growth', memoryId: 'mem_team_without_me', peakId: 'peak_growth', type: 'support', born: 2, date: '05/18', note: '支持机制可独立运转的成长标准' },
    { id: 'rel_mechanism_growth', memoryId: 'mem_reusable_mechanism', peakId: 'peak_growth', type: 'support', born: 2, date: '05/18', note: '支持可复用机制的判断' },
    { id: 'rel_ui_product', memoryId: 'mem_ui_understanding', peakId: 'peak_product', type: 'support', born: 3, date: '08/17', note: '支持理解成为产品首页第一信息' },
    { id: 'rel_ui_system', memoryId: 'mem_ui_understanding', peakId: 'peak_system', type: 'boundary', born: 3, date: '08/17', note: '提醒理解展示仍需保留产品边界' },
    { id: 'rel_ui_record', memoryId: 'mem_ui_understanding', peakId: 'peak_record', type: 'support', born: 3, date: '08/17', note: '支持理解与日常记录同时存在' }
  ];

  memories.push(
    {
      id: 'mem_permission_control', x: 112, y: 228, born: 0, status: 'committed', corrected: false,
      date: '04/22', time: '18:06', source: '文字', type: '我的边界', topic: '权限控制', use: '系统设计',
      summary: '长期理解需要由用户明确开启，并可随时关闭。',
      raw: '我希望对长期理解有明确的开关，不想它在我不知道的时候自动运行。'
    },
    {
      id: 'mem_failure_boundary', x: 202, y: 314, born: 1, status: 'committed', corrected: false,
      date: '05/01', time: '21:12', source: 'Chrome', type: '我的原则', topic: '失败边界', use: '安全设计',
      summary: '整理失败时应保留上一版理解和原始记录。',
      raw: '如果这次整理失败，继续保留上一版，不要让用户看到一片空白。'
    },
    {
      id: 'mem_read_only_sources', x: 438, y: 154, born: 1, status: 'committed', corrected: false,
      date: '05/03', time: '08:42', source: 'Chrome', type: '我的方法', topic: '只读原文', use: '证据核对',
      summary: '模型只读取原文，由本地工作流负责校验和写入。',
      raw: 'Agent 可以做判断，原文应该保持只读，最后的校验和写入由本地工作流来完成。'
    },
    {
      id: 'mem_decision_cycle', x: 548, y: 118, born: 0, status: 'committed', corrected: false,
      date: '04/25', time: '12:20', source: '语音', type: '我的判断', topic: '决策周期', use: '产品方案',
      summary: '功能价值需要和验证周期一起讨论。',
      raw: '我不想只讨论功能本身，还需要知道它什么时候能被验证，如何判断有没有用。'
    },
    {
      id: 'mem_guardrail_first', x: 944, y: 152, born: 1, status: 'committed', corrected: false,
      date: '05/06', time: '14:36', source: 'Chrome', type: '我的方法', topic: '护栏优先', use: '决策评审',
      summary: '评审前先明确不能被牺牲的指标。',
      raw: '一个方案能够提升主指标，也要先看它是否伤害了我们不想牺牲的护栏。'
    },
    {
      id: 'mem_small_experiment', x: 1020, y: 244, born: 2, status: 'committed', corrected: false,
      date: '05/15', time: '17:08', source: 'Chrome', type: '我的决定', topic: '小步验证', use: '不确定决策',
      summary: '不确定时先选择可逆、可观察的小步验证。',
      raw: '这个决策还有不确定，可以先做一个可以撤回、一周内能看到结果的小步尝试。'
    },
    {
      id: 'mem_context_counter', x: 972, y: 342, born: 2, status: 'committed', corrected: false,
      date: '05/17', time: '10:15', source: '文字', type: '反例', topic: '情境例外', use: '范围校准',
      summary: '高风险情境下，完整审查可以优先于快速试错。',
      raw: '如果这次改动会直接影响用户数据，我宁愿先做完整审查，不会为了速度先上线。'
    },
    {
      id: 'mem_capture_relief', x: 126, y: 454, born: 1, status: 'committed', corrected: false,
      date: '05/07', time: '23:02', source: '语音', type: '我的感受', topic: '随手留下', use: '记录习惯',
      summary: '记录入口需要先接住当下，整理可以稍后发生。',
      raw: '我留下这条的时候不想做分类，希望先把它接住，整理的事情可以交给后面。'
    },
    {
      id: 'mem_daily_merge', x: 258, y: 565, born: 2, status: 'committed', corrected: false,
      date: '05/13', time: '21:06', source: 'Chrome', type: '我的方法', topic: '每日归并', use: '日常整理',
      summary: '单条记录先即时整理，当天结束后再统一归并长期关系。',
      raw: '我希望每条记录先知道被 AI 整理成了什么，峰和长期理解可以到晚上再统一归并。'
    },
    {
      id: 'mem_keep_original', x: 452, y: 546, born: 2, status: 'committed', corrected: true,
      date: '05/14', time: '09:26', source: '文字', type: '我的原则', topic: '原文保留', use: '记录底稿',
      summary: 'AI 整理结果可以修改，原始记录需要始终保留。',
      raw: '每次整理都可以迭代，但我当时真正写下来的那条原文不要被覆盖。'
    },
    {
      id: 'mem_skill_reuse', x: 626, y: 540, born: 2, status: 'committed', corrected: false,
      date: '05/16', time: '19:40', source: 'Chrome', type: '我的追问', topic: '能力复用', use: '职业复盘',
      summary: '一项能力的价值需要看它能否在新情境中复用。',
      raw: '我想知道这次做成了什么，其中哪一部分能够在下一个团队或下一个产品里继续用。'
    },
    {
      id: 'mem_outcome_evidence', x: 888, y: 542, born: 2, status: 'committed', corrected: false,
      date: '05/18', time: '20:18', source: '语音', type: '我的标准', topic: '结果证据', use: '成长验证',
      summary: '成长需要同时有决策、行动和结果证据。',
      raw: '我不想只因为参与过一个项目就说自己长大了，需要看我做了什么决策，带来了什么结果。'
    },
    {
      id: 'mem_shared_boundary', x: 474, y: 350, born: 2, status: 'committed', corrected: false,
      date: '05/11', time: '13:52', source: 'Chrome', type: '我的边界', topic: '整理与判断', use: '产品定义',
      summary: 'Workflow 负责稳定整理，Agent 在需要选择关系和修订时做判断。',
      raw: '每条记录的接入和校验应该稳定，真正需要判断它和哪个理解有关、要不要修订时，再让 Agent 决定。'
    },
    {
      id: 'mem_pending_language', x: 1042, y: 470, born: 3, status: 'pending', corrected: false,
      date: '08/17', time: '12:06', source: '文字', type: '初步整理', topic: '表达方式', use: '等待今日归并',
      summary: '认知地景需要先给人一种感觉，精确解释收进详情。',
      raw: '我希望主页上先看到一个整体的感觉，点开以后才看具体的理解和证据。'
    }
  );

  relations.push(
    { id: 'rel_permission_system', memoryId: 'mem_permission_control', peakId: 'peak_system', type: 'support', born: 0, date: '04/28', note: '支持显式授权与可关闭的系统边界' },
    { id: 'rel_failure_system', memoryId: 'mem_failure_boundary', peakId: 'peak_system', type: 'support', born: 1, date: '05/10', note: '支持失败时保留上一版的安全边界' },
    { id: 'rel_readonly_system', memoryId: 'mem_read_only_sources', peakId: 'peak_system', type: 'support', born: 1, date: '05/10', note: '支持原文只读与本地写入的职责分离' },
    { id: 'rel_cycle_product', memoryId: 'mem_decision_cycle', peakId: 'peak_product', type: 'support', born: 0, date: '04/28', note: '支持将功能价值与验证周期同时讨论' },
    { id: 'rel_guardrail_product', memoryId: 'mem_guardrail_first', peakId: 'peak_product', type: 'support', born: 1, date: '05/10', note: '支持产品决策前先明确护栏' },
    { id: 'rel_experiment_product', memoryId: 'mem_small_experiment', peakId: 'peak_product', type: 'revision', born: 2, date: '05/18', note: '为不确定决策增加可逆小步验证' },
    { id: 'rel_context_product', memoryId: 'mem_context_counter', peakId: 'peak_product', type: 'counter', born: 2, date: '05/18', note: '高风险情境对快速试错构成适用边界' },
    { id: 'rel_capture_record', memoryId: 'mem_capture_relief', peakId: 'peak_record', type: 'support', born: 1, date: '05/10', note: '支持先接住、后整理的记录体验' },
    { id: 'rel_merge_record', memoryId: 'mem_daily_merge', peakId: 'peak_record', type: 'revision', born: 2, date: '05/18', note: '将单条即时整理与每日长期归并分层' },
    { id: 'rel_original_record', memoryId: 'mem_keep_original', peakId: 'peak_record', type: 'support', born: 2, date: '05/18', note: '支持保留原文底稿' },
    { id: 'rel_original_system', memoryId: 'mem_keep_original', peakId: 'peak_system', type: 'boundary', born: 2, date: '05/18', note: '为 AI 整理增加不覆盖原文的系统边界' },
    { id: 'rel_reuse_growth', memoryId: 'mem_skill_reuse', peakId: 'peak_growth', type: 'support', born: 2, date: '05/18', note: '支持用新情境复用验证能力' },
    { id: 'rel_outcome_growth', memoryId: 'mem_outcome_evidence', peakId: 'peak_growth', type: 'support', born: 2, date: '05/18', note: '支持用决策、行动和结果证据验证成长' },
    { id: 'rel_shared_system', memoryId: 'mem_shared_boundary', peakId: 'peak_system', type: 'support', born: 2, date: '05/18', note: '支持 Workflow 与 Agent 的职责分层' },
    { id: 'rel_shared_record', memoryId: 'mem_shared_boundary', peakId: 'peak_record', type: 'boundary', born: 2, date: '05/18', note: '为单条整理与长期理解归并划分边界' },
    { id: 'rel_shared_product', memoryId: 'mem_shared_boundary', peakId: 'peak_product', type: 'boundary', born: 2, date: '05/18', note: '重要关系和修订仍由 Agent 判断' }
  );

  memories.push({
    id: 'mem_tension_new', x: 1048, y: 316, born: 3, status: 'committed', corrected: false, demoOnly: 'tension',
    date: '08/17', time: '13:18', source: '文字', type: '新反例', topic: '决策时效', use: '范围校准',
    summary: '当时间窗口极短时，先做可逆决定可能更合适。',
    raw: '这次只有一天时间，我会先做一个可以撤回的决定，不会等所有长期影响都分析完。'
  });
  relations.push({
    id: 'rel_tension_new_product', memoryId: 'mem_tension_new', peakId: 'peak_product', type: 'counter', born: 3,
    demoOnly: 'tension', date: '08/17', note: '极短时间窗口对长期影响优先构成新的适用边界'
  });

  const state = {
    stage: 3,
    view: 'overview',
    selected: null,
    demoState: 'normal',
    lastTrigger: null,
    playing: false,
    playTimer: null,
    animateStage: null,
    editedStatements: new Map(),
    hiddenPeakIds: new Set(),
    hiddenMemoryIds: new Set(),
    correctedMemoryIds: new Set(),
    hovered: null,
    lens: {
      active: false,
      currentX: -300,
      currentY: -300,
      targetX: -300,
      targetY: -300,
      frame: null
    }
  };

  const refs = {
    shell: document.getElementById('app-shell'),
    workspace: document.getElementById('map-workspace'),
    svg: document.getElementById('cognitive-map'),
    mapCanvas: document.querySelector('[data-map-canvas]'),
    mapPanel: document.querySelector('.map-panel'),
    timeControls: document.querySelector('.time-controls'),
    terrainLayer: document.querySelector('[data-layer="terrain"]'),
    terrainResponseLayer: document.querySelector('[data-layer="terrain-response"]'),
    terrainLens: document.querySelector('[data-terrain-lens]'),
    shadowLayer: document.querySelector('[data-layer="shadows"]'),
    observationLayer: document.querySelector('[data-layer="observation-zones"]'),
    relationLayer: document.querySelector('[data-layer="relations"]'),
    peakLayer: document.querySelector('[data-layer="peaks"]'),
    memoryLayer: document.querySelector('[data-layer="memories"]'),
    tooltip: document.querySelector('[data-map-tooltip]'),
    empty: document.querySelector('[data-map-empty]'),
    listView: document.querySelector('[data-list-view]'),
    listRows: document.querySelector('[data-list-rows]'),
    drawer: document.querySelector('[data-context-drawer]'),
    drawerEyebrow: document.querySelector('[data-drawer-eyebrow]'),
    drawerTitle: document.querySelector('[data-drawer-title]'),
    drawerBody: document.querySelector('[data-drawer-body]'),
    drawerFoot: document.querySelector('[data-drawer-foot]'),
    notice: document.querySelector('[data-system-notice]'),
    noticeText: document.querySelector('[data-system-notice-text]'),
    returnToday: document.querySelector('[data-return-today]'),
    summaryUnderstandings: document.querySelector('[data-summary-understandings]'),
    summaryChanges: document.querySelector('[data-summary-changes]'),
    summaryObserving: document.querySelector('[data-summary-observing]'),
    visibleSnapshot: document.querySelector('[data-visible-snapshot]'),
    timelineEvent: document.querySelector('[data-timeline-event]'),
    snapshotTrack: document.querySelector('[data-snapshot-track]'),
    playButton: document.querySelector('[data-play-timeline]'),
    liveRegion: document.querySelector('[data-live-region]')
  };

  function svgElement(name, attributes) {
    const element = document.createElementNS(SVG_NS, name);
    Object.entries(attributes || {}).forEach(([key, value]) => {
      if (value !== null && value !== undefined) element.setAttribute(key, String(value));
    });
    return element;
  }

  function clearElement(element) {
    while (element.firstChild) element.firstChild.remove();
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function polarPath(item, scale, phaseOffset, closePath) {
    const points = [];
    const total = closePath ? 72 : 54;
    const start = closePath ? 0 : Math.PI * .1;
    const end = closePath ? Math.PI * 2 : Math.PI * 1.86;
    for (let index = 0; index <= total; index += 1) {
      const angle = start + (end - start) * (index / total);
      const wobble = 1
        + Math.sin(angle * 3 + item.phase + phaseOffset) * .055
        + Math.sin(angle * 5 - item.phase * .7 + phaseOffset) * .027;
      const x = item.x + Math.cos(angle) * item.rx * scale * wobble;
      const y = item.y + Math.sin(angle) * item.ry * scale * (1 + Math.cos(angle * 4 + item.phase) * .035);
      points.push(`${index === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`);
    }
    return `${points.join(' ')}${closePath ? ' Z' : ''}`;
  }

  function committedRelationsForMemory(memoryId) {
    return visibleRelations().filter((relation) => relation.memoryId === memoryId);
  }

  function connectedPeakIds(memoryId) {
    return [...new Set(committedRelationsForMemory(memoryId).map((relation) => relation.peakId))];
  }

  function isCrossPeakMemory(memory) {
    return memory.status === 'committed' && connectedPeakIds(memory.id).length >= 2;
  }

  function peakTerrainContribution(peak, x, y) {
    const evidence = peak.evidence[state.stage] || 1;
    const amplitude = .3 + Math.log1p(evidence) * .075;
    const dx = (x - peak.x) / (peak.rx * .92);
    const dy = (y - peak.y) / (peak.ry * 1.02);
    return amplitude * Math.exp(-.5 * (dx * dx + dy * dy));
  }

  function distanceToSegmentSquared(x, y, fromX, fromY, toX, toY) {
    const dx = toX - fromX;
    const dy = toY - fromY;
    const lengthSquared = dx * dx + dy * dy || 1;
    const ratio = Math.max(0, Math.min(1, ((x - fromX) * dx + (y - fromY) * dy) / lengthSquared));
    const nearestX = fromX + ratio * dx;
    const nearestY = fromY + ratio * dy;
    const offsetX = x - nearestX;
    const offsetY = y - nearestY;
    return offsetX * offsetX + offsetY * offsetY;
  }

  function buildTerrainContext() {
    const stagePeaks = visiblePeaks();
    const stageMemories = visibleMemories().filter((memory) => memory.status === 'committed');
    const stageRelations = visibleRelations();
    const relationsByMemory = new Map();
    stageRelations.forEach((relation) => {
      if (!relationsByMemory.has(relation.memoryId)) relationsByMemory.set(relation.memoryId, []);
      relationsByMemory.get(relation.memoryId).push(relation);
    });
    const bridges = [];
    stageMemories.forEach((memory) => {
      const memoryRelations = relationsByMemory.get(memory.id) || [];
      if (new Set(memoryRelations.map((relation) => relation.peakId)).size < 2) return;
      memoryRelations.forEach((relation) => {
        const peak = stagePeaks.find((item) => item.id === relation.peakId);
        if (!peak) return;
        bridges.push({ fromX: memory.x, fromY: memory.y, toX: peak.x, toY: peak.y });
      });
    });
    return { peaks: stagePeaks, memories: stageMemories, bridges };
  }

  function crossPeakBridgeContribution(x, y, bridges) {
    let value = 0;
    bridges.forEach((bridge) => {
      const distanceSquared = distanceToSegmentSquared(
        x, y, bridge.fromX, bridge.fromY, bridge.toX, bridge.toY
      );
      value += .06 * Math.exp(-distanceSquared / (2 * 27 * 27));
    });
    return value;
  }

  function terrainValue(x, y, context) {
    const broadX = (x - 560) / 470;
    const broadY = (y - 326) / 250;
    let value = .035 * Math.exp(-.5 * (broadX * broadX + broadY * broadY));

    context.peaks.forEach((peak) => { value += peakTerrainContribution(peak, x, y); });

    context.memories.forEach((memory) => {
      const dx = (x - memory.x) / 44;
      const dy = (y - memory.y) / 32;
      value += .082 * Math.exp(-.5 * (dx * dx + dy * dy));
    });
    return value + crossPeakBridgeContribution(x, y, context.bridges);
  }

  function interpolatePoint(x1, y1, v1, x2, y2, v2, threshold) {
    const denominator = v2 - v1;
    const ratio = Math.abs(denominator) < .00001 ? .5 : (threshold - v1) / denominator;
    return {
      x: x1 + (x2 - x1) * ratio,
      y: y1 + (y2 - y1) * ratio
    };
  }

  function terrainSegments(values, columns, rows, step, originX, originY, threshold) {
    const segments = [];
    const edgePoint = (edge, column, row, corners) => {
      const x = originX + column * step;
      const y = originY + row * step;
      if (edge === 'top') return interpolatePoint(x, y, corners.tl, x + step, y, corners.tr, threshold);
      if (edge === 'right') return interpolatePoint(x + step, y, corners.tr, x + step, y + step, corners.br, threshold);
      if (edge === 'bottom') return interpolatePoint(x, y + step, corners.bl, x + step, y + step, corners.br, threshold);
      return interpolatePoint(x, y, corners.tl, x, y + step, corners.bl, threshold);
    };

    for (let row = 0; row < rows - 1; row += 1) {
      for (let column = 0; column < columns - 1; column += 1) {
        const index = row * columns + column;
        const corners = {
          tl: values[index],
          tr: values[index + 1],
          bl: values[index + columns],
          br: values[index + columns + 1]
        };
        const states = {
          tl: corners.tl >= threshold,
          tr: corners.tr >= threshold,
          br: corners.br >= threshold,
          bl: corners.bl >= threshold
        };
        const crossings = [];
        if (states.tl !== states.tr) crossings.push('top');
        if (states.tr !== states.br) crossings.push('right');
        if (states.bl !== states.br) crossings.push('bottom');
        if (states.tl !== states.bl) crossings.push('left');
        if (crossings.length === 2) {
          segments.push(crossings.map((edge) => edgePoint(edge, column, row, corners)));
        } else if (crossings.length === 4) {
          const center = (corners.tl + corners.tr + corners.br + corners.bl) / 4;
          const tlHigh = states.tl;
          const centerHigh = center >= threshold;
          const pairs = tlHigh === centerHigh
            ? [['top', 'right'], ['bottom', 'left']]
            : [['top', 'left'], ['right', 'bottom']];
          pairs.forEach((pair) => segments.push(pair.map((edge) => edgePoint(edge, column, row, corners))));
        }
      }
    }
    return segments;
  }

  function terrainPointKey(point) {
    return `${Math.round(point.x * 2)},${Math.round(point.y * 2)}`;
  }

  function stitchTerrainSegments(segments) {
    const edges = segments.map(([from, to]) => ({ from, to }));
    const adjacency = new Map();
    edges.forEach((edge, edgeIndex) => {
      [edge.from, edge.to].forEach((point) => {
        const key = terrainPointKey(point);
        if (!adjacency.has(key)) adjacency.set(key, []);
        adjacency.get(key).push(edgeIndex);
      });
    });

    const visited = new Set();
    const lines = [];
    const walk = (firstEdgeIndex, firstKey) => {
      const points = [];
      let edgeIndex = firstEdgeIndex;
      let currentKey = firstKey;
      let guard = 0;
      while (!visited.has(edgeIndex) && guard < edges.length + 1) {
        guard += 1;
        visited.add(edgeIndex);
        const edge = edges[edgeIndex];
        const fromKey = terrainPointKey(edge.from);
        const forward = fromKey === currentKey;
        const start = forward ? edge.from : edge.to;
        const end = forward ? edge.to : edge.from;
        if (!points.length) points.push(start);
        points.push(end);
        currentKey = terrainPointKey(end);
        const next = (adjacency.get(currentKey) || []).find((candidate) => !visited.has(candidate));
        if (next === undefined) break;
        edgeIndex = next;
      }
      return points;
    };

    adjacency.forEach((edgeIndices, key) => {
      if (edgeIndices.length !== 1 || visited.has(edgeIndices[0])) return;
      const points = walk(edgeIndices[0], key);
      if (points.length > 1) lines.push(points);
    });
    edges.forEach((edge, edgeIndex) => {
      if (visited.has(edgeIndex)) return;
      const points = walk(edgeIndex, terrainPointKey(edge.from));
      if (points.length > 1) lines.push(points);
    });
    return lines;
  }

  function terrainLinePath(points) {
    if (points.length < 2) return '';
    const first = points[0];
    const last = points[points.length - 1];
    const closed = terrainPointKey(first) === terrainPointKey(last) && points.length > 4;
    const line = closed ? points.slice(0, -1) : points;
    const midpoint = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
    if (closed) {
      const start = midpoint(line[line.length - 1], line[0]);
      const parts = [`M ${start.x.toFixed(1)} ${start.y.toFixed(1)}`];
      line.forEach((point, index) => {
        const next = line[(index + 1) % line.length];
        const mid = midpoint(point, next);
        parts.push(`Q ${point.x.toFixed(1)} ${point.y.toFixed(1)} ${mid.x.toFixed(1)} ${mid.y.toFixed(1)}`);
      });
      return `${parts.join(' ')} Z`;
    }
    const parts = [`M ${line[0].x.toFixed(1)} ${line[0].y.toFixed(1)}`];
    for (let index = 1; index < line.length - 1; index += 1) {
      const mid = midpoint(line[index], line[index + 1]);
      parts.push(`Q ${line[index].x.toFixed(1)} ${line[index].y.toFixed(1)} ${mid.x.toFixed(1)} ${mid.y.toFixed(1)}`);
    }
    parts.push(`L ${line[line.length - 1].x.toFixed(1)} ${line[line.length - 1].y.toFixed(1)}`);
    return parts.join(' ');
  }

  function renderTerrain() {
    const step = 7;
    const originX = 20;
    const originY = 24;
    const width = 1080;
    const height = 588;
    const columns = Math.floor(width / step) + 1;
    const rows = Math.floor(height / step) + 1;
    const context = buildTerrainContext();
    const values = [];
    for (let row = 0; row < rows; row += 1) {
      for (let column = 0; column < columns; column += 1) {
        values.push(terrainValue(originX + column * step, originY + row * step, context));
      }
    }

    const maxValue = Math.max(...values);
    const minThreshold = Math.max(.105, maxValue * .115);
    const maxThreshold = maxValue * .92;
    const contourCount = 22;
    const thresholds = Array.from({ length: contourCount }, (_, index) => {
      const ratio = index / (contourCount - 1);
      return minThreshold + (maxThreshold - minThreshold) * Math.pow(ratio, .92);
    });
    thresholds.forEach((threshold, index) => {
      const segments = terrainSegments(values, columns, rows, step, originX, originY, threshold);
      const lines = stitchTerrainSegments(segments);
      const d = lines.map(terrainLinePath).filter(Boolean).join(' ');
      if (!d) return;
      refs.terrainLayer.appendChild(svgElement('path', {
        d,
        class: `terrain-contour${index >= 14 ? ' is-upper' : ''}${index % 4 === 0 ? ' is-major' : ''}`,
        'data-level': index,
        opacity: (.34 + index * .022).toFixed(2)
      }));

      context.peaks.forEach((peak) => {
        const localLines = lines.filter((line) => {
          const stride = Math.max(1, Math.floor(line.length / 8));
          let contribution = 0;
          let samples = 0;
          for (let pointIndex = 0; pointIndex < line.length; pointIndex += stride) {
            contribution += peakTerrainContribution(peak, line[pointIndex].x, line[pointIndex].y);
            samples += 1;
          }
          return contribution / Math.max(1, samples) >= Math.max(.045, threshold * .13);
        });
        if (!localLines.length) return;
        const localD = localLines.map(terrainLinePath).filter(Boolean).join(' ');
        refs.terrainResponseLayer.appendChild(svgElement('path', {
          d: localD,
          class: `terrain-response${index % 4 === 0 ? ' is-major' : ''}`,
          'data-terrain-peak': peak.id,
          'data-level': index
        }));
      });
    });
  }

  function relationPath(memory, peak, relationIndex) {
    const dx = peak.x - memory.x;
    const dy = peak.y - memory.y;
    const length = Math.max(1, Math.hypot(dx, dy));
    const unitX = dx / length;
    const unitY = dy / length;
    const endX = peak.x - unitX * Math.min(peak.rx * .5, length * .34);
    const endY = peak.y - unitY * Math.min(peak.ry * .5, length * .34);
    const midpointX = (memory.x + endX) / 2;
    const midpointY = (memory.y + endY) / 2;
    const curve = ((relationIndex % 3) - 1) * 13;
    const controlX = midpointX - unitY * curve;
    const controlY = midpointY + unitX * curve;
    return `M ${memory.x} ${memory.y} Q ${controlX.toFixed(1)} ${controlY.toFixed(1)} ${endX.toFixed(1)} ${endY.toFixed(1)}`;
  }

  function visiblePeaks() {
    return peaks.filter((peak) => peak.born <= state.stage && !state.hiddenPeakIds.has(peak.id));
  }

  function visibleMemories() {
    return memories.filter((memory) => memory.born <= state.stage
      && (!memory.demoOnly || memory.demoOnly === state.demoState)
      && !state.hiddenMemoryIds.has(memory.id));
  }

  function visibleRelations() {
    const peakIds = new Set(visiblePeaks().map((peak) => peak.id));
    const memoryIds = new Set(visibleMemories().map((memory) => memory.id));
    return relations.filter((relation) => relation.born <= state.stage
      && (!relation.demoOnly || relation.demoOnly === state.demoState)
      && peakIds.has(relation.peakId)
      && memoryIds.has(relation.memoryId));
  }

  function peakStatement(peak) {
    const activeVersion = activePeakVersion(peak);
    const isCurrentSnapshot = state.stage === snapshots.length - 1;
    if (isCurrentSnapshot && state.editedStatements.has(peak.id)) return state.editedStatements.get(peak.id);
    return activeVersion === peak.versions[0] ? peak.statement : activeVersion.text;
  }

  function activePeakVersion(peak) {
    const snapshotDate = snapshots[state.stage].id.slice(5).replace('-', '/');
    return peak.versions.find((version) => version.date <= snapshotDate) || peak.versions[peak.versions.length - 1];
  }

  function visiblePeakVersions(peak) {
    const snapshotDate = snapshots[state.stage].id.slice(5).replace('-', '/');
    return peak.versions.filter((version) => version.date <= snapshotDate);
  }

  function peakChangeAtSnapshot(peak) {
    const activeVersion = activePeakVersion(peak);
    if (activeVersion === peak.versions[0]) return peak.change;
    return `${activeVersion.date} 形成 ${activeVersion.version}：${activeVersion.text}`;
  }

  function peakCounterCount(peak) {
    return peak.counter[state.stage] + (state.demoState === 'tension' && peak.id === 'peak_product' ? 1 : 0);
  }

  function renderMap() {
    clearElement(refs.terrainLayer);
    clearElement(refs.terrainResponseLayer);
    clearElement(refs.shadowLayer);
    clearElement(refs.observationLayer);
    clearElement(refs.relationLayer);
    clearElement(refs.peakLayer);
    clearElement(refs.memoryLayer);

    const isEmpty = state.demoState === 'empty' || visiblePeaks().length === 0;
    refs.empty.hidden = !isEmpty;
    refs.svg.hidden = isEmpty || state.view === 'list';
    refs.listView.hidden = state.view !== 'list' || isEmpty;
    refs.timeControls.hidden = isEmpty;
    refs.mapPanel.classList.toggle('is-empty', isEmpty);

    if (isEmpty) {
      renderList();
      return;
    }

    refs.svg.dataset.view = state.view;

    renderTerrain();
    visiblePeaks().forEach((peak) => renderShadow(peak));
    observationZones.filter((zone) => zone.born <= state.stage).forEach(renderObservationZone);
    visibleRelations().forEach(renderRelation);
    visiblePeaks().forEach(renderPeak);
    visibleMemories().forEach(renderMemory);

    applySelectionClasses();
    if (!state.selected && state.hovered) applyHoverContext(state.hovered.type, state.hovered.id);
    renderList();
  }

  function renderShadow(peak) {
    const selected = state.selected && state.selected.type === 'peak' && state.selected.id === peak.id;
    const recent = state.stage === snapshots.length - 1
      ? peak.recentAtCurrent
      : peak.changed === state.stage;
    const path = svgElement('path', {
      d: polarPath(peak, .96, .15, true),
      class: 'terrain-shadow',
      opacity: selected ? .68 : recent ? .48 : .26,
      'data-shadow-peak': peak.id,
      'aria-hidden': 'true'
    });
    refs.shadowLayer.appendChild(path);
  }

  function renderObservationZone(zone) {
    const group = svgElement('g', {
      class: 'observation-zone',
      'aria-label': `${zone.name}，仍在观察，${zone.detail}`
    });
    [1, .78, .57].forEach((scale, index) => {
      group.appendChild(svgElement('path', {
        d: polarPath(zone, scale, index * .23, false),
        class: 'observation-path',
        opacity: 1 - index * .18
      }));
    });
    const name = svgElement('text', { x: zone.x, y: zone.y - 3, class: 'observation-label' });
    name.textContent = zone.name;
    const detail = svgElement('text', { x: zone.x, y: zone.y + 15, class: 'observation-detail' });
    detail.textContent = `仍在观察 / ${zone.detail}`;
    group.append(name, detail);
    refs.observationLayer.appendChild(group);
  }

  function renderPeakIcon(peak, group) {
    const icon = svgElement('g', {
      class: 'peak-icon',
      transform: `translate(${peak.x} ${peak.y - 43})`,
      'aria-hidden': 'true'
    });
    if (peak.id === 'peak_system') {
      icon.append(
        svgElement('path', { d: 'M -8 -5 L 0 -10 L 8 -5 L 8 5 L 0 10 L -8 5 Z' }),
        svgElement('path', { d: 'M -8 -5 L 0 0 L 8 -5 M 0 0 L 0 10' })
      );
    } else if (peak.id === 'peak_product') {
      icon.append(
        svgElement('circle', { cx: 0, cy: 0, r: 10 }),
        svgElement('path', { d: 'M -4 5 L -1 -4 L 5 -7 L 2 2 Z' })
      );
    } else if (peak.id === 'peak_record') {
      icon.append(
        svgElement('path', { d: 'M -7 6 L -4 -3 L 4 -10 L 8 -6 L 1 2 Z M -7 6 L -9 10 L -5 8' }),
        svgElement('path', { d: 'M -10 11 L 8 11' })
      );
    } else {
      icon.append(
        svgElement('path', { d: 'M 0 10 L 0 -2 M 0 3 C -2 -5 -10 -7 -11 -7 C -10 1 -6 5 0 5 M 0 1 C 2 -6 9 -8 11 -8 C 10 -1 6 3 0 4' })
      );
    }
    group.appendChild(icon);
  }

  function renderPeak(peak) {
    const recent = state.stage === snapshots.length - 1
      ? peak.recentAtCurrent
      : peak.changed === state.stage;
    const group = svgElement('g', {
      class: `peak${recent ? ' is-recent' : ''}${peak.born === state.animateStage ? ' is-entering' : ''}${state.demoState === 'tension' && peak.id === 'peak_product' ? ' is-tension' : ''}`,
      tabindex: '0',
      role: 'button',
      'data-entity-type': 'peak',
      'data-entity-id': peak.id,
      'aria-label': `${peak.name}，${peakStatement(peak)}，${peak.evidence[state.stage]} 条依据，${peakCounterCount(peak)} 条反例`
    });

    group.appendChild(svgElement('ellipse', {
      cx: peak.x,
      cy: peak.y,
      rx: peak.rx * 1.02,
      ry: peak.ry * 1.02,
      class: 'peak-hit'
    }));

    if (state.demoState === 'revised' && peak.id === 'peak_product') {
      group.appendChild(svgElement('path', {
        d: polarPath(peak, .84, -.42, true),
        class: 'revision-ghost'
      }));
    }
    renderPeakIcon(peak, group);

    const name = svgElement('text', { x: peak.x, y: peak.y - 2, class: 'peak-name' });
    name.textContent = peak.name;
    const meta = svgElement('text', { x: peak.x, y: peak.y + 18, class: 'peak-meta' });
    meta.textContent = `依据 ${peak.evidence[state.stage]} · 近期变化 ${recent ? 1 : 0}`;
    group.append(name, meta);
    group.appendChild(svgElement('line', {
      x1: peak.x - 28,
      x2: peak.x + 28,
      y1: peak.y + 29,
      y2: peak.y + 29,
      class: 'peak-focus-line'
    }));

    const statusText = peakStatusText(peak);
    if (statusText) {
      const badge = svgElement('text', { x: peak.x, y: peak.y - 66, class: 'peak-badge' });
      badge.textContent = statusText;
      group.appendChild(badge);
    }

    bindEntity(group, 'peak', peak.id, () => peakTooltip(peak));
    refs.peakLayer.appendChild(group);
  }

  function renderMemory(memory) {
    const corrected = memory.corrected || state.correctedMemoryIds.has(memory.id);
    const crossPeak = isCrossPeakMemory(memory);
    const connectedPeakCount = crossPeak
      ? new Set(visibleRelations().filter((relation) => relation.memoryId === memory.id).map((relation) => relation.peakId)).size
      : 0;
    const group = svgElement('g', {
      class: `memory-node is-${memory.status}${corrected ? ' is-corrected' : ''}${crossPeak ? ' is-cross-peak' : ''}`,
      tabindex: '0',
      role: 'button',
      'data-entity-type': 'memory',
      'data-entity-id': memory.id,
      'aria-label': `${memory.date} ${memory.summary}，${memory.status === 'pending' ? '等待归并' : '已归并'}${crossPeak ? `，跨峰记忆，连接 ${connectedPeakCount} 项理解` : ''}`
    });
    group.appendChild(svgElement('circle', { cx: memory.x, cy: memory.y, r: 9, class: 'memory-hit' }));
    if (crossPeak) group.appendChild(svgElement('circle', { cx: memory.x, cy: memory.y, r: 6.4, class: 'memory-bridge' }));
    group.appendChild(svgElement('circle', { cx: memory.x, cy: memory.y, r: memory.status === 'pending' ? 4.3 : 3.4, class: 'memory-mark' }));
    if (corrected) {
      group.appendChild(svgElement('circle', { cx: memory.x, cy: memory.y, r: 7, class: 'memory-correction' }));
    }
    bindEntity(group, 'memory', memory.id, () => memoryTooltip(memory));
    refs.memoryLayer.appendChild(group);
  }

  function renderRelation(relation, relationIndex) {
    const memory = memories.find((item) => item.id === relation.memoryId);
    const peak = peaks.find((item) => item.id === relation.peakId);
    if (!memory || !peak) return;
    const d = relationPath(memory, peak, relationIndex || relations.indexOf(relation));
    const group = svgElement('g', {
      class: `relation is-${relation.type}`,
      tabindex: state.view === 'relations' ? '0' : '-1',
      role: 'button',
      'data-entity-type': 'relation',
      'data-entity-id': relation.id,
      'aria-label': `${relationTypeLabel(relation.type)}关系，${memory.summary}，关联${peak.name}`
    });
    group.appendChild(svgElement('path', { d, class: 'relation-visible' }));
    group.appendChild(svgElement('path', { d, class: 'relation-hit' }));
    bindEntity(group, 'relation', relation.id, () => relationTooltip(relation));
    refs.relationLayer.appendChild(group);
  }

  function entityContext(type, id) {
    const peakIds = new Set();
    const memoryIds = new Set();
    const relationIds = new Set();
    const stageRelations = visibleRelations();
    const addMemoryNetwork = (memoryId) => {
      memoryIds.add(memoryId);
      stageRelations.filter((relation) => relation.memoryId === memoryId).forEach((relation) => {
        relationIds.add(relation.id);
        peakIds.add(relation.peakId);
      });
    };

    if (type === 'peak') {
      peakIds.add(id);
      stageRelations.filter((relation) => relation.peakId === id).forEach((relation) => {
        relationIds.add(relation.id);
        addMemoryNetwork(relation.memoryId);
      });
    } else if (type === 'memory') {
      addMemoryNetwork(id);
    } else if (type === 'relation') {
      const relation = stageRelations.find((item) => item.id === id);
      if (relation) {
        relationIds.add(relation.id);
        addMemoryNetwork(relation.memoryId);
        peakIds.add(relation.peakId);
      }
    }
    return { peakIds, memoryIds, relationIds };
  }

  function entityPosition(type, id) {
    if (type === 'peak') {
      const peak = peaks.find((item) => item.id === id);
      return peak ? { x: peak.x, y: peak.y } : null;
    }
    if (type === 'memory') {
      const memory = memories.find((item) => item.id === id);
      return memory ? { x: memory.x, y: memory.y } : null;
    }
    const relation = relations.find((item) => item.id === id);
    const memory = relation ? memories.find((item) => item.id === relation.memoryId) : null;
    const peak = relation ? peaks.find((item) => item.id === relation.peakId) : null;
    return memory && peak ? { x: (memory.x + peak.x) / 2, y: (memory.y + peak.y) / 2 } : null;
  }

  function pointerToSvgPoint(event) {
    const point = refs.svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const matrix = refs.svg.getScreenCTM();
    return matrix ? point.matrixTransform(matrix.inverse()) : { x: -300, y: -300 };
  }

  function animateTerrainLens() {
    state.lens.frame = null;
    const factor = prefersReducedMotion() ? 1 : .18;
    state.lens.currentX += (state.lens.targetX - state.lens.currentX) * factor;
    state.lens.currentY += (state.lens.targetY - state.lens.currentY) * factor;
    refs.terrainLens.setAttribute('cx', state.lens.currentX.toFixed(1));
    refs.terrainLens.setAttribute('cy', state.lens.currentY.toFixed(1));
    const distance = Math.hypot(
      state.lens.targetX - state.lens.currentX,
      state.lens.targetY - state.lens.currentY
    );
    if (distance > .45) state.lens.frame = requestAnimationFrame(animateTerrainLens);
  }

  function setTerrainLensTarget(x, y, active) {
    state.lens.active = active;
    state.lens.targetX = active ? x : -300;
    state.lens.targetY = active ? y : -300;
    if (prefersReducedMotion()) {
      state.lens.currentX = state.lens.targetX;
      state.lens.currentY = state.lens.targetY;
    }
    if (!state.lens.frame) state.lens.frame = requestAnimationFrame(animateTerrainLens);
  }

  function terrainPeakIdsForEntity(type, id, context) {
    if (type === 'peak') return new Set([id]);
    if (type === 'relation') {
      const relation = visibleRelations().find((item) => item.id === id);
      return new Set(relation ? [relation.peakId] : []);
    }
    return new Set(context.peakIds);
  }

  function setContextClasses(context, mode, terrainPeakIds) {
    const className = mode === 'selection' ? 'is-related' : 'is-hover-related';
    context.peakIds.forEach((peakId) => refs.svg.querySelector(`[data-entity-type="peak"][data-entity-id="${peakId}"]`)?.classList.add(className));
    context.memoryIds.forEach((memoryId) => refs.svg.querySelector(`[data-entity-type="memory"][data-entity-id="${memoryId}"]`)?.classList.add(className));
    context.relationIds.forEach((relationId) => {
      const relation = refs.svg.querySelector(`[data-entity-type="relation"][data-entity-id="${relationId}"]`);
      relation?.classList.add(className);
      if (mode === 'selection') relation?.classList.add('is-contextual');
    });
    refs.terrainResponseLayer.querySelectorAll('[data-terrain-peak]').forEach((path) => {
      const active = terrainPeakIds.has(path.dataset.terrainPeak);
      path.classList.toggle(mode === 'selection' ? 'is-selection-response' : 'is-hover-response', active);
    });
    refs.shadowLayer.querySelectorAll('[data-shadow-peak]').forEach((path) => {
      const active = terrainPeakIds.has(path.dataset.shadowPeak);
      path.classList.toggle(mode === 'selection' ? 'is-selection-response' : 'is-hover-response', active);
    });
  }

  function bindEntity(element, type, id, tooltipFactory) {
    element.addEventListener('click', (event) => {
      event.stopPropagation();
      selectEntity(type, id, element);
    });
    element.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        selectEntity(type, id, element);
      }
    });
    element.addEventListener('pointerenter', (event) => {
      applyHoverContext(type, id);
      const position = pointerToSvgPoint(event);
      setTerrainLensTarget(position.x, position.y, true);
      if (type !== 'peak') showTooltipForElement(event.currentTarget, tooltipFactory());
    });
    element.addEventListener('pointermove', (event) => {
      if (state.selected) return;
      const position = pointerToSvgPoint(event);
      setTerrainLensTarget(position.x, position.y, true);
    });
    element.addEventListener('pointerleave', () => {
      clearHoverContext();
      hideTooltip();
      if (!state.selected) setTerrainLensTarget(-300, -300, false);
    });
    element.addEventListener('focus', (event) => {
      applyHoverContext(type, id);
      const position = entityPosition(type, id);
      if (position) setTerrainLensTarget(position.x, position.y, true);
      if (type !== 'peak') showTooltipForElement(event.currentTarget, tooltipFactory());
    });
    element.addEventListener('blur', () => {
      clearHoverContext();
      hideTooltip();
      if (!state.selected) setTerrainLensTarget(-300, -300, false);
    });
  }

  function applyHoverContext(type, id) {
    if (state.selected) return;
    clearHoverContext();
    state.hovered = { type, id };
    refs.svg.classList.add('has-hover');
    const context = entityContext(type, id);
    setContextClasses(context, 'hover', terrainPeakIdsForEntity(type, id, context));
  }

  function clearHoverContext() {
    state.hovered = null;
    refs.svg.classList.remove('has-hover');
    refs.svg.querySelectorAll('.is-hover-related').forEach((element) => element.classList.remove('is-hover-related'));
    refs.terrainResponseLayer.querySelectorAll('.is-hover-response').forEach((element) => element.classList.remove('is-hover-response'));
    refs.shadowLayer.querySelectorAll('.is-hover-response').forEach((element) => element.classList.remove('is-hover-response'));
  }

  function peakTooltip(peak) {
    return `<strong>${escapeHtml(peak.name)}</strong>${peak.evidence[state.stage]} 条依据 / ${peakCounterCount(peak)} 条反例<br>${escapeHtml(peakChangeAtSnapshot(peak))}`;
  }

  function memoryTooltip(memory) {
    return `<strong>${escapeHtml(memory.date)} / ${escapeHtml(memory.type)}</strong>${escapeHtml(memory.summary)}<br>${memory.status === 'pending' ? '等待今日归并' : '已归并并可回到原文'}`;
  }

  function relationTooltip(relation) {
    return `<strong>${escapeHtml(relationTypeLabel(relation.type))}</strong>${escapeHtml(relation.note)}<br>形成于 ${escapeHtml(relation.date)}`;
  }

  function showTooltip(event, content) {
    refs.tooltip.innerHTML = content;
    refs.tooltip.hidden = false;
    positionTooltip(event);
  }

  function showTooltipForElement(element, content) {
    const rect = element.getBoundingClientRect();
    showTooltip({ clientX: rect.left + rect.width / 2, clientY: rect.top }, content);
  }

  function positionTooltip(event) {
    if (refs.tooltip.hidden) return;
    const padding = 14;
    const width = refs.tooltip.offsetWidth || 230;
    const height = refs.tooltip.offsetHeight || 90;
    let left = event.clientX + 14;
    let top = event.clientY + 14;
    if (left + width + padding > window.innerWidth) left = event.clientX - width - 14;
    if (top + height + padding > window.innerHeight) top = event.clientY - height - 14;
    refs.tooltip.style.left = `${Math.max(padding, left)}px`;
    refs.tooltip.style.top = `${Math.max(padding, top)}px`;
  }

  function hideTooltip() {
    refs.tooltip.hidden = true;
  }

  function selectEntity(type, id, trigger) {
    hideTooltip();
    clearHoverContext();
    state.selected = { type, id };
    state.lastTrigger = trigger || null;
    setTerrainLensTarget(-300, -300, false);
    applySelectionClasses();
    openDrawer(type, id);
  }

  function applySelectionClasses() {
    const selection = state.selected;
    refs.svg.classList.toggle('has-selection', Boolean(selection));
    refs.svg.querySelectorAll('[data-entity-type]').forEach((element) => {
      element.classList.remove('is-selected', 'is-related', 'is-contextual');
    });
    refs.terrainResponseLayer.querySelectorAll('.is-selection-response').forEach((element) => element.classList.remove('is-selection-response'));
    refs.shadowLayer.querySelectorAll('.is-selection-response').forEach((element) => element.classList.remove('is-selection-response'));

    if (!selection) {
      refs.terrainResponseLayer.setAttribute('mask', 'url(#terrain-lens-mask)');
      refs.terrainResponseLayer.classList.remove('is-pinned');
      return;
    }

    const context = entityContext(selection.type, selection.id);
    refs.terrainResponseLayer.removeAttribute('mask');
    refs.terrainResponseLayer.classList.add('is-pinned');
    setContextClasses(context, 'selection', terrainPeakIdsForEntity(selection.type, selection.id, context));

    refs.svg.querySelectorAll('[data-entity-type]').forEach((element) => {
      const elementType = element.dataset.entityType;
      const elementId = element.dataset.entityId;
      const selected = elementType === selection.type && elementId === selection.id;
      const related = (elementType === 'peak' && context.peakIds.has(elementId))
        || (elementType === 'memory' && context.memoryIds.has(elementId))
        || (elementType === 'relation' && context.relationIds.has(elementId));
      element.classList.toggle('is-selected', selected);
      element.classList.toggle('is-related', related);
      if (elementType === 'relation') element.classList.toggle('is-contextual', related);
    });
  }

  function openDrawer(type, id) {
    refs.drawer.hidden = false;
    refs.workspace.classList.add('drawer-open');
    refs.drawer.dataset.entityType = type;

    if (type === 'peak') renderPeakDrawer(id);
    if (type === 'memory') renderMemoryDrawer(id);
    if (type === 'relation') renderRelationDrawer(id);

    requestAnimationFrame(() => refs.drawer.querySelector('[data-drawer-close]')?.focus({ preventScroll: true }));
  }

  function closeDrawer(options) {
    const restoreFocus = !options || options.restoreFocus !== false;
    const previousSelection = state.selected ? { ...state.selected } : null;
    const previousListPeak = state.lastTrigger?.dataset?.listPeak || null;
    refs.drawer.hidden = true;
    refs.workspace.classList.remove('drawer-open');
    state.selected = null;
    applySelectionClasses();
    if (restoreFocus) {
      const target = previousListPeak
        ? refs.listRows.querySelector(`[data-list-peak="${previousListPeak}"]`)
        : previousSelection
          ? refs.svg.querySelector(`[data-entity-type="${previousSelection.type}"][data-entity-id="${previousSelection.id}"]`)
          : null;
      target?.focus();
    }
    state.lastTrigger = null;
  }

  function formationItemsForPeak(peakId) {
    const items = visibleRelations()
      .filter((relation) => relation.peakId === peakId)
      .map((relation) => ({
        relation,
        memory: memories.find((memory) => memory.id === relation.memoryId)
      }))
      .filter((item) => item.memory)
      .sort((left, right) => right.relation.date.localeCompare(left.relation.date));
    const selected = [];
    ['revision', 'counter', 'boundary', 'support'].forEach((type) => {
      const item = items.find((candidate) => candidate.relation.type === type);
      if (item && !selected.includes(item)) selected.push(item);
    });
    items.forEach((item) => {
      if (selected.length < 4 && !selected.includes(item)) selected.push(item);
    });
    return selected.slice(0, 3);
  }

  function crossPeakLinks(memoryId, currentPeakId) {
    return visibleRelations()
      .filter((relation) => relation.memoryId === memoryId && relation.peakId !== currentPeakId)
      .map((relation) => ({
        relation,
        peak: peaks.find((peak) => peak.id === relation.peakId)
      }))
      .filter((item) => item.peak);
  }

  function renderFormationChain(item, currentPeak) {
    const { relation, memory } = item;
    const currentVersion = activePeakVersion(currentPeak);
    const crossLinks = crossPeakLinks(memory.id, currentPeak.id);
    const relationClass = relation.type === 'counter' || relation.type === 'boundary' ? 'is-counter' : `is-${relation.type}`;
    return `
      <article class="formation-chain ${relationClass}" role="listitem" aria-label="${escapeHtml(memory.date)} 的原始记录，经整理形成${escapeHtml(relationTypeLabel(relation.type))}关系，进入${escapeHtml(currentPeak.name)}">
        <div class="formation-flow">
          <button class="formation-node is-source" type="button" data-open-memory="${memory.id}">
            <span>原始记录</span>
            <time>${escapeHtml(memory.date)} · ${escapeHtml(memory.source)}</time>
            <q>${escapeHtml(memory.raw)}</q>
          </button>
          <i class="logic-arrow" aria-hidden="true">→</i>
          <button class="formation-node is-memory" type="button" data-open-memory="${memory.id}">
            <span>AI 整理</span>
            <strong>${escapeHtml(memory.topic)}</strong>
            <p>${escapeHtml(memory.summary)}</p>
          </button>
          <i class="logic-arrow" aria-hidden="true">→</i>
          <button class="formation-node is-relation" type="button" data-open-relation="${relation.id}">
            <span>${escapeHtml(relationTypeLabel(relation.type))}</span>
            <p>${escapeHtml(relation.note)}</p>
          </button>
          <i class="logic-arrow" aria-hidden="true">→</i>
          <div class="formation-node is-peak">
            <span>当前理解</span>
            <strong>${escapeHtml(currentPeak.name)}</strong>
            <small>${escapeHtml(currentVersion.version)}</small>
          </div>
        </div>
        ${crossLinks.length ? `
          <div class="cross-peak-links">
            <span>同一条记忆也进入</span>
            ${crossLinks.map(({ relation: crossRelation, peak }) => `
              <button type="button" data-open-peak="${peak.id}">${escapeHtml(relationTypeLabel(crossRelation.type))} · ${escapeHtml(peak.name)}</button>
            `).join('')}
          </div>` : ''}
      </article>`;
  }

  function renderMemoryFormation(memory, memoryRelations) {
    return `
      <div class="memory-formation">
        <button class="memory-source-node" type="button" data-open-memory="${memory.id}">
          <span>原始记录</span>
          <time>${escapeHtml(memory.date)} ${escapeHtml(memory.time)} · ${escapeHtml(memory.source)}</time>
          <q>${escapeHtml(memory.raw)}</q>
        </button>
        <i class="formation-down" aria-hidden="true"></i>
        <button class="memory-result-node" type="button" data-open-memory="${memory.id}">
          <span>AI 拆解后的可用记忆</span>
          <strong>${escapeHtml(memory.summary)}</strong>
        </button>
        ${memoryRelations.length ? `
          <div class="memory-branches" role="list" aria-label="这条记忆进入的长期理解">
            ${memoryRelations.map((relation) => {
              const peak = peaks.find((item) => item.id === relation.peakId);
              const activeVersion = activePeakVersion(peak);
              return `<div class="memory-branch" role="listitem">
                <button type="button" data-open-relation="${relation.id}">${escapeHtml(relationTypeLabel(relation.type))}</button>
                <i aria-hidden="true"></i>
                <button type="button" data-open-peak="${peak.id}">${escapeHtml(peak.name)} · ${escapeHtml(activeVersion.version)}</button>
              </div>`;
            }).join('')}
          </div>` : '<p class="formation-empty">这条内容暂时没有进入长期理解。</p>'}
      </div>`;
  }

  function renderRelationFormation(relation, memory, peak) {
    const activeVersion = activePeakVersion(peak);
    return `
      <div class="single-formation-chain" aria-label="这条关系的完整形成链">
        <button type="button" data-open-memory="${memory.id}">
          <span>原始记录</span>
          <q>${escapeHtml(memory.raw)}</q>
        </button>
        <i aria-hidden="true"></i>
        <button type="button" data-open-memory="${memory.id}">
          <span>可用记忆</span>
          <strong>${escapeHtml(memory.summary)}</strong>
        </button>
        <i aria-hidden="true"></i>
        <div class="single-relation-node">
          <span>${escapeHtml(relationTypeLabel(relation.type))}</span>
          <p>${escapeHtml(relation.note)}</p>
        </div>
        <i aria-hidden="true"></i>
        <button type="button" data-open-peak="${peak.id}">
          <span>当前理解</span>
          <strong>${escapeHtml(peak.name)} · ${escapeHtml(activeVersion.version)}</strong>
        </button>
      </div>`;
  }

  function renderPeakDrawer(id) {
    const peak = peaks.find((item) => item.id === id);
    if (!peak) return;
    const activeVersion = activePeakVersion(peak);
    const versionsAtSnapshot = visiblePeakVersions(peak);
    const evidenceRelations = visibleRelations().filter((relation) => relation.peakId === peak.id);
    const evidenceItems = evidenceRelations.map((relation) => ({
      relation,
      memory: memories.find((memory) => memory.id === relation.memoryId)
    })).filter((item) => item.memory);
    const formationItems = formationItemsForPeak(peak.id);

    refs.drawerEyebrow.textContent = peakStatusText(peak) || '当前理解';
    refs.drawerTitle.textContent = peak.name;
    refs.drawerBody.innerHTML = `
      <section class="drawer-section">
        <p class="current-statement">${escapeHtml(peakStatement(peak))}</p>
        <div class="context-tags">
          <span>${escapeHtml(peak.scope)}</span>
          <span>${escapeHtml(activeVersion.version)} / ${escapeHtml(activeVersion.date)}</span>
          <span>${peak.evidence[state.stage]} 条依据</span>
          <span>${peakCounterCount(peak)} 条反例</span>
        </div>
      </section>
      <section class="drawer-section">
        <h3>最近变化</h3>
        <div class="change-note">${escapeHtml(peakChangeAtSnapshot(peak))}</div>
      </section>
      <section class="drawer-section formation-section">
        <div class="formation-heading">
          <div><h3>它怎样形成</h3><p>原始记录经整理、关联和版本校验，才进入当前理解。</p></div>
          <span>${formationItems.length} 条关键链路</span>
        </div>
        <div class="formation-map" role="list">
          ${formationItems.map((item) => renderFormationChain(item, peak)).join('')}
        </div>
        ${evidenceItems.length > formationItems.length ? `
          <details class="all-evidence-details">
            <summary>查看全部 ${evidenceItems.length} 条已保存关系</summary>
            <ol class="evidence-stack">${renderEvidenceItems(evidenceItems)}</ol>
          </details>` : ''}
      </section>
      <section class="drawer-section">
        <h3>版本记录</h3>
        <ol class="version-stack">
          ${versionsAtSnapshot.map((version) => `
            <li class="version-item">
              <div><span>${escapeHtml(version.version)}</span><time>${escapeHtml(version.date)}</time></div>
              <p>${escapeHtml(version.text)}</p>
            </li>`).join('')}
        </ol>
      </section>`;
    refs.drawerFoot.innerHTML = state.stage < snapshots.length - 1
      ? `<button type="button" data-peak-originals="${peak.id}">查看原文</button>
         <button type="button" class="is-primary" data-return-today>回到今天</button>`
      : `<button type="button" data-peak-originals="${peak.id}">查看原文</button>
         <button type="button" class="is-primary" data-edit-peak="${peak.id}">改一下</button>
         <button type="button" class="is-danger" data-delete-peak="${peak.id}">删除</button>`;
    bindDrawerActions();
  }

  function renderEvidenceItems(items) {
    if (!items.length) return '<li class="evidence-item">当前快照没有展示可用证据。</li>';
    return items.map(({ relation, memory }) => `
      <li class="evidence-item is-${relation.type === 'counter' || relation.type === 'boundary' ? 'counter' : 'support'}">
        <button type="button" data-open-memory="${memory.id}">
          <div><span>${escapeHtml(relationTypeLabel(relation.type))}</span><time>${escapeHtml(memory.date)}</time></div>
          <blockquote>“${escapeHtml(memory.raw)}”</blockquote>
        </button>
      </li>`).join('');
  }

  function renderMemoryDrawer(id) {
    const memory = memories.find((item) => item.id === id);
    if (!memory) return;
    const memoryRelations = visibleRelations().filter((relation) => relation.memoryId === memory.id);

    refs.drawerEyebrow.textContent = memory.status === 'pending' ? '等待今日归并' : '已归并记忆';
    refs.drawerTitle.textContent = memory.topic;
    refs.drawerBody.innerHTML = `
      <section class="drawer-section">
        <h3>AI 整理结果</h3>
        <p class="current-statement">${escapeHtml(memory.summary)}</p>
        <div class="context-tags">
          <span>${escapeHtml(memory.type)}</span>
          <span>${escapeHtml(memory.topic)}</span>
          <span>${escapeHtml(memory.use)}</span>
          <span>${memory.status === 'pending' ? '等待归并' : '已归并'}</span>
        </div>
      </section>
      <section class="drawer-section">
        <div class="formation-heading">
          <div><h3>这条记录去了哪里</h3><p>同一条可用记忆可以以不同关系进入多项长期理解。</p></div>
          <span>${memoryRelations.length} 项关系</span>
        </div>
        ${renderMemoryFormation(memory, memoryRelations)}
      </section>`;
    refs.drawerFoot.innerHTML = state.stage < snapshots.length - 1
      ? `<button type="button" class="is-primary" data-return-today>回到今天</button>`
      : memory.status === 'pending'
        ? `<button type="button" class="is-primary" data-correct-memory="${memory.id}">正确</button>
           <button type="button" data-edit-memory="${memory.id}">改一下</button>
           <button type="button" class="is-danger" data-raw-only="${memory.id}">仅保存原文</button>`
        : `<button type="button" data-edit-memory="${memory.id}">改一下</button>
           <button type="button" class="is-danger" data-raw-only="${memory.id}">仅保存原文</button>`;
    bindDrawerActions();
  }

  function renderRelationDrawer(id) {
    const relation = relations.find((item) => item.id === id);
    if (!relation) return;
    const memory = memories.find((item) => item.id === relation.memoryId);
    const peak = peaks.find((item) => item.id === relation.peakId);
    refs.drawerEyebrow.textContent = '已保存关系';
    refs.drawerTitle.textContent = relationTypeLabel(relation.type);
    refs.drawerBody.innerHTML = `
      <section class="drawer-section">
        <div class="formation-heading">
          <div><h3>完整形成链</h3><p>只展示已保存的关系和可追溯原文。</p></div>
          <span>${escapeHtml(relation.date)}</span>
        </div>
        ${renderRelationFormation(relation, memory, peak)}
      </section>
      <section class="drawer-section">
        <h3>这条关系说明什么</h3>
        <p>${escapeHtml(relation.note)}</p>
        <div class="context-tags"><span>形成于 ${escapeHtml(relation.date)}</span><span>${escapeHtml(memory.source)}</span></div>
      </section>
      <section class="drawer-section">
        <h3>对应原文</h3>
        <div class="raw-source">${escapeHtml(memory.raw)}</div>
      </section>`;
    refs.drawerFoot.innerHTML = `
      <button type="button" data-open-memory="${memory.id}">查看记忆点</button>
      <button type="button" class="is-primary" data-open-peak="${peak.id}">查看理解</button>`;
    bindDrawerActions();
  }

  function bindDrawerActions() {
    refs.drawer.querySelectorAll('[data-open-memory]').forEach((button) => {
      button.addEventListener('click', () => selectEntity('memory', button.dataset.openMemory, button));
    });
    refs.drawer.querySelectorAll('[data-open-peak]').forEach((button) => {
      button.addEventListener('click', () => selectEntity('peak', button.dataset.openPeak, button));
    });
    refs.drawer.querySelectorAll('[data-open-relation]').forEach((button) => {
      button.addEventListener('click', () => selectEntity('relation', button.dataset.openRelation, button));
    });
    refs.drawer.querySelector('[data-edit-peak]')?.addEventListener('click', (event) => renderPeakEditor(event.currentTarget.dataset.editPeak));
    refs.drawer.querySelector('[data-delete-peak]')?.addEventListener('click', (event) => renderDeleteConfirmation(event.currentTarget.dataset.deletePeak));
    refs.drawer.querySelector('[data-peak-originals]')?.addEventListener('click', () => {
      refs.drawerBody.querySelector('.formation-section')?.scrollIntoView({ behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
    });
    refs.drawer.querySelector('[data-return-today]')?.addEventListener('click', () => setStage(snapshots.length - 1, true));
    refs.drawer.querySelector('[data-correct-memory]')?.addEventListener('click', (event) => {
      const id = event.currentTarget.dataset.correctMemory;
      state.correctedMemoryIds.add(id);
      const memory = memories.find((item) => item.id === id);
      memory.status = 'committed';
      announce('已在当前页面标记为正确，等待今日归并');
      renderMap();
      renderMemoryDrawer(id);
    });
    refs.drawer.querySelector('[data-edit-memory]')?.addEventListener('click', (event) => renderMemoryEditor(event.currentTarget.dataset.editMemory));
    refs.drawer.querySelector('[data-raw-only]')?.addEventListener('click', (event) => {
      const id = event.currentTarget.dataset.rawOnly;
      state.hiddenMemoryIds.add(id);
      closeDrawer({ restoreFocus: false });
      announce('已在当前页面仅保留原文，整理结果已移除');
      renderAll();
    });
  }

  function renderPeakEditor(id) {
    const peak = peaks.find((item) => item.id === id);
    refs.drawerBody.innerHTML = `
      <form class="edit-inline" data-peak-edit-form>
        <label for="peak-statement-edit">修改当前理解</label>
        <textarea id="peak-statement-edit" required>${escapeHtml(peakStatement(peak))}</textarea>
        <p>修改只在当前离线页面模拟。旧版本会继续保留。</p>
        <div class="edit-actions">
          <button type="button" data-cancel-edit>取消</button>
          <button type="submit" class="is-primary">保存修改</button>
        </div>
      </form>`;
    refs.drawerFoot.innerHTML = '';
    refs.drawerBody.querySelector('[data-cancel-edit]').addEventListener('click', () => renderPeakDrawer(id));
    refs.drawerBody.querySelector('[data-peak-edit-form]').addEventListener('submit', (event) => {
      event.preventDefault();
      const value = event.currentTarget.querySelector('textarea').value.trim();
      if (!value) return;
      state.editedStatements.set(id, value);
      renderMap();
      renderPeakDrawer(id);
      announce(`${peak.name}已在当前页面修改`);
    });
    refs.drawerBody.querySelector('textarea').focus();
  }

  function renderMemoryEditor(id) {
    const memory = memories.find((item) => item.id === id);
    refs.drawerBody.innerHTML = `
      <form class="edit-inline" data-memory-edit-form>
        <label for="memory-summary-edit">修改整理结果</label>
        <textarea id="memory-summary-edit" required>${escapeHtml(memory.summary)}</textarea>
        <p>原始记录保持不变。修改只在当前离线页面模拟。</p>
        <div class="edit-actions">
          <button type="button" data-cancel-edit>取消</button>
          <button type="submit" class="is-primary">保存修改</button>
        </div>
      </form>`;
    refs.drawerFoot.innerHTML = '';
    refs.drawerBody.querySelector('[data-cancel-edit]').addEventListener('click', () => renderMemoryDrawer(id));
    refs.drawerBody.querySelector('[data-memory-edit-form]').addEventListener('submit', (event) => {
      event.preventDefault();
      const value = event.currentTarget.querySelector('textarea').value.trim();
      if (!value) return;
      memory.summary = value;
      state.correctedMemoryIds.add(id);
      renderMap();
      renderMemoryDrawer(id);
      announce('整理结果已在当前页面修改');
    });
    refs.drawerBody.querySelector('textarea').focus();
  }

  function renderDeleteConfirmation(id) {
    const peak = peaks.find((item) => item.id === id);
    refs.drawerFoot.innerHTML = `
      <span>确认从当前地景移除“${escapeHtml(peak.name)}”？原始记录仍会保留。</span>
      <button type="button" data-cancel-delete>取消</button>
      <button type="button" class="is-danger" data-confirm-delete>确认删除</button>`;
    refs.drawerFoot.querySelector('[data-cancel-delete]').addEventListener('click', () => renderPeakDrawer(id));
    refs.drawerFoot.querySelector('[data-confirm-delete]').addEventListener('click', () => {
      state.hiddenPeakIds.add(id);
      closeDrawer({ restoreFocus: false });
      announce(`${peak.name}已从当前页面移除，刷新页面可以恢复`);
      renderAll();
    });
  }

  function renderList() {
    const items = visiblePeaks();
    refs.listRows.innerHTML = items.map((peak) => `
      <button class="table-row" type="button" data-list-peak="${peak.id}" aria-label="${escapeHtml(peak.name)}，${escapeHtml(peakStatement(peak))}">
        <span><strong>${escapeHtml(peakStatement(peak))}</strong><small>${escapeHtml(peak.name)}</small></span>
        <span>${escapeHtml(peak.scope)}</span>
        <span>${peak.evidence[state.stage]} 条</span>
        <span>${peakCounterCount(peak)} 条</span>
        <span>${escapeHtml(peakChangeAtSnapshot(peak))}</span>
      </button>`).join('');
    refs.listRows.querySelectorAll('[data-list-peak]').forEach((button) => {
      button.addEventListener('click', () => selectEntity('peak', button.dataset.listPeak, button));
    });
  }

  function renderSnapshotTrack() {
    refs.snapshotTrack.innerHTML = snapshots.map((snapshot, index) => `
      <button class="snapshot-button${index < 3 ? ' has-change' : ''}" type="button" data-stage="${index}" aria-pressed="${index === state.stage}">
        <strong>${escapeHtml(snapshot.short)}</strong>
        <span>${escapeHtml(snapshot.title)}</span>
      </button>`).join('');
    refs.snapshotTrack.querySelectorAll('[data-stage]').forEach((button) => {
      button.addEventListener('click', () => setStage(Number(button.dataset.stage), true));
    });
  }

  function setStage(stage, animate) {
    stopPlayback();
    state.stage = Math.max(0, Math.min(snapshots.length - 1, stage));
    state.demoState = 'normal';
    state.animateStage = animate ? state.stage : null;
    closeDrawer({ restoreFocus: false });
    renderAll();
    if (state.stage < snapshots.length - 1) {
      showNotice(`正在查看 ${snapshots[state.stage].id} 的地景`, false, true);
    } else {
      hideNotice();
    }
    announce(`已切换到${snapshots[state.stage].short}的认知地景。${snapshots[state.stage].event}`);
    window.setTimeout(() => { state.animateStage = null; }, 520);
  }

  function renderSummary() {
    const summary = snapshots[state.stage].summary;
    const isEmpty = state.demoState === 'empty';
    refs.summaryUnderstandings.textContent = String(isEmpty ? 0 : visiblePeaks().length);
    refs.summaryChanges.textContent = String(isEmpty ? 0 : summary.changes);
    refs.summaryObserving.textContent = String(isEmpty ? 0 : summary.observing);
    refs.visibleSnapshot.textContent = state.stage === snapshots.length - 1 ? '今天的地景' : `${snapshots[state.stage].short} 的地景`;
    refs.timelineEvent.textContent = snapshots[state.stage].event;
  }

  function setView(view) {
    state.view = view;
    document.querySelectorAll('button[data-view]').forEach((button) => {
      const selected = button.dataset.view === view;
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    renderMap();
    announce(view === 'list' ? '已切换到认知地景列表视图' : `已切换到${view === 'overview' ? '概览' : '关系'}视图`);
  }

  function playTimeline() {
    if (state.playing) {
      stopPlayback();
      return;
    }
    state.playing = true;
    refs.playButton.textContent = '停止播放';
    state.stage = 0;
    state.animateStage = 0;
    closeDrawer({ restoreFocus: false });
    renderAll();
    announce(snapshots[0].event);
    scheduleNextStage();
  }

  function scheduleNextStage() {
    if (!state.playing) return;
    if (state.stage >= snapshots.length - 1) {
      stopPlayback();
      return;
    }
    state.playTimer = window.setTimeout(() => {
      state.stage += 1;
      state.animateStage = state.stage;
      renderAll();
      announce(snapshots[state.stage].event);
      scheduleNextStage();
    }, prefersReducedMotion() ? 650 : 1350);
  }

  function stopPlayback() {
    state.playing = false;
    if (state.playTimer) window.clearTimeout(state.playTimer);
    state.playTimer = null;
    if (refs.playButton) refs.playButton.textContent = '播放变化';
  }

  function applyDemoState(nextState) {
    stopPlayback();
    state.demoState = nextState;
    state.stage = 3;
    state.animateStage = null;
    closeDrawer({ restoreFocus: false });

    if (nextState === 'normal') hideNotice();
    if (nextState === 'processing') showNotice('正在核对近期理解，继续显示上一版地景', false, false);
    if (nextState === 'no-change') showNotice('长期理解没有变化，3 条新记录仍等待今日归并', false, false);
    if (nextState === 'new-peak') {
      state.stage = 2;
      state.animateStage = 2;
      showNotice('长期成长完成校验，一座新认知峰已经形成', false, false);
    }
    if (nextState === 'revised') {
      showNotice('产品判断已修订，峰的位置与历史版本继续保留', false, false);
      state.selected = { type: 'peak', id: 'peak_product' };
    }
    if (nextState === 'tension') {
      showNotice('产品判断新增 1 条反例，当前理解继续保留并标记张力', false, false);
      state.selected = { type: 'peak', id: 'peak_product' };
    }
    if (nextState === 'failed') showNotice('本次核对未完成，继续显示上一版地景。原始记录没有丢失', true, false);
    if (nextState === 'empty') hideNotice();

    renderAll();
    if (nextState === 'revised' || nextState === 'tension') openDrawer('peak', 'peak_product');
    announce(`已切换到${demoStateLabel(nextState)}状态`);
  }

  function peakStatusText(peak) {
    if (state.demoState === 'new-peak' && peak.id === 'peak_growth') return '新形成';
    if (state.demoState === 'revised' && peak.id === 'peak_product') return '已修订';
    if (state.demoState === 'tension' && peak.id === 'peak_product') return '存在张力';
    if (peak.changed === state.stage && state.stage < 3) return peak.born === state.stage ? '新形成' : '本次变化';
    if (peak.recentAtCurrent && state.stage === 3) return '近期变化';
    return '';
  }

  function relationTypeLabel(type) {
    return {
      support: '支持',
      counter: '反例',
      revision: '修订来路',
      boundary: '适用边界'
    }[type] || '关联';
  }

  function demoStateLabel(value) {
    return {
      normal: '当前', processing: '核对中', 'no-change': '无变化', 'new-peak': '新峰',
      revised: '已修订', tension: '存在张力', failed: '校验失败', empty: '空地图'
    }[value] || value;
  }

  function showNotice(message, isError, showReturn) {
    refs.notice.hidden = false;
    refs.notice.classList.toggle('is-error', Boolean(isError));
    refs.noticeText.textContent = message;
    refs.returnToday.hidden = !showReturn;
  }

  function hideNotice() {
    refs.notice.hidden = true;
    refs.notice.classList.remove('is-error');
    refs.returnToday.hidden = true;
  }

  function announce(message) {
    refs.liveRegion.textContent = '';
    window.setTimeout(() => { refs.liveRegion.textContent = message; }, 20);
  }

  function prefersReducedMotion() {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  function renderAll() {
    renderSummary();
    renderSnapshotTrack();
    renderMap();
  }

  document.querySelectorAll('button[data-view]').forEach((button) => {
    button.addEventListener('click', () => setView(button.dataset.view));
    button.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const tabs = [...document.querySelectorAll('button[data-view]')];
      const current = tabs.indexOf(event.currentTarget);
      const next = event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? tabs.length - 1
          : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      tabs[next].focus();
      setView(tabs[next].dataset.view);
    });
  });

  document.querySelector('[data-drawer-close]').addEventListener('click', () => closeDrawer());
  refs.playButton.addEventListener('click', playTimeline);
  refs.returnToday.addEventListener('click', () => setStage(3, false));
  refs.svg.addEventListener('click', (event) => {
    if (!event.target.closest('[data-entity-type]')) closeDrawer();
  });
  refs.svg.addEventListener('pointermove', (event) => {
    if (state.selected || refs.svg.hidden || state.view === 'list') return;
    const position = pointerToSvgPoint(event);
    setTerrainLensTarget(position.x, position.y, true);
  });
  refs.svg.addEventListener('pointerleave', () => {
    if (!state.selected) setTerrainLensTarget(-300, -300, false);
  });

  document.querySelectorAll('[data-dialog-open]').forEach((button) => {
    button.addEventListener('click', () => {
      const dialog = document.getElementById(button.dataset.dialogOpen);
      if (!dialog) return;
      dialog.dataset.returnFocus = button === document.activeElement ? 'true' : 'false';
      dialog._returnFocus = button;
      dialog.showModal();
    });
  });

  document.querySelectorAll('[data-dialog-close]').forEach((button) => {
    button.addEventListener('click', () => button.closest('dialog')?.close());
  });

  document.querySelectorAll('dialog').forEach((dialog) => {
    dialog.addEventListener('close', () => {
      if (dialog._returnFocus && document.contains(dialog._returnFocus)) dialog._returnFocus.focus();
    });
  });

  document.querySelectorAll('[data-demo-state]').forEach((button) => {
    button.addEventListener('click', () => {
      applyDemoState(button.dataset.demoState);
      button.closest('dialog')?.close();
    });
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !refs.drawer.hidden && !document.querySelector('dialog[open]')) closeDrawer();
  });

  window.addEventListener('resize', hideTooltip);

  renderAll();
}());
