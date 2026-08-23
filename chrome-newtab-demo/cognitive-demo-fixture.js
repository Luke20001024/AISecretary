(() => {
  'use strict';

  window.MementoDemoFixture = Object.freeze({
    window: '2026-08-01 — 2026-08-20',
    recordCount: 261,
    themes: Object.freeze([
      { id: 'product', label: '产品决策', evidence: 26, edge: 1, x: 22, y: 40, summary: '把决策留在可验证的边界内，让后续讨论有可回到的依据。' },
      { id: 'evidence', label: '证据优先', evidence: 28, edge: 2, x: 47, y: 30, summary: '优先保留能被复核的判断条件，也为反例留下入口。' },
      { id: 'accumulation', label: '长期积累', evidence: 30, edge: 1, x: 76, y: 40, summary: '每次记录都保留形成原因，累积成以后能调用的理解。' },
      { id: 'iteration', label: '迭代节奏', evidence: 28, edge: 2, x: 23, y: 72, summary: '在一次次小范围验证中调整问题与方法，而不急于定型。' },
      { id: 'method', label: '研究方法', evidence: 26, edge: 1, x: 52, y: 78, summary: '把个案经验写成可检验范围，分清结论、样本与假设。' },
      { id: 'collaboration', label: '协作边界', evidence: 32, edge: 2, x: 78, y: 69, summary: '同步信息时明确谁决策、谁推进，以及哪些内容需要被检查。' }
    ]),
    understandings: Object.freeze([
      { no: '01', state: '形成中', title: '从完成任务走向定义方法', links: '产品决策 · 长期积累' },
      { no: '02', state: '已稳定', title: '可验证比漂亮结论更重要', links: '证据优先 · 研究方法' },
      { no: '03', state: '已稳定', title: '允许理解随证据修订', links: '证据优先 · 研究方法 · 迭代节奏' },
      { no: '04', state: '形成中', title: '成果应当可以被接手', links: '长期积累 · 协作边界 · 迭代节奏' }
    ]),
    records: Object.freeze([
      { time: '08:02', source: '会议记录', title: '长期积累：记录结论之外，也要留下当时为什么这样取舍。', theme: '长期积累' },
      { time: '08:20', source: '语音备忘', title: '协作边界：方案文档里补一段不做什么，让协作边界可以被检查。', theme: '协作边界' },
      { time: '08:44', source: 'Memento', title: '研究方法：命名城市只作为来源案例，不把单个案例扩写成研究范围。', theme: '研究方法' },
      { time: '09:12', source: 'Chrome', title: '迭代节奏：这一轮只验证从主题到依据再到理解的三层路径。', theme: '迭代节奏' },
      { time: '10:00', source: '会议记录', title: '产品决策：本次评审只决定是否继续投入，不把实现细节一起锁死。', theme: '产品决策' },
      { time: '10:24', source: 'Figma', title: '证据优先：同一指标出现两个版本，先核对数据时间窗与过滤条件。', theme: '证据优先' }
    ])
  });
})();
