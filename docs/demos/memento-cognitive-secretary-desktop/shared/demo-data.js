(function () {
  'use strict';

  window.MementoDemoData = Object.freeze({
    meta: Object.freeze({
      date: '2026-08-17',
      weekday: '周一',
      recordCount: 5,
      snapshot: '完全离线 · 演示数据 · 操作只在当前页面模拟'
    }),
    run: Object.freeze({
      status: 'no_change',
      title: '本次核对没有更新',
      detail: '计划已保存；这不表示今天 21:00 已经真实运行。',
      scheduleEnabled: true
    }),
    understandings: Object.freeze([
      Object.freeze({
        id: 'mem_review_failures',
        group: '反复出现的我',
        statement: '在评审方案时，你倾向先检查反例和失败条件，再判断方案是否完整。',
        scope: '产品方案评审',
        updatedAt: '2026-08-16',
        version: 3,
        evidence: Object.freeze([
          Object.freeze({ date: '2026-08-15', source: 'Chrome', relation: '支持', quote: '在评审方案时，我会先检查反例和失败条件，再评估完整方案。' }),
          Object.freeze({ date: '2026-08-10', source: 'Chrome', relation: '支持', quote: '产品决策前，先明确目标指标、护栏指标和验证周期，再讨论功能方案。' }),
          Object.freeze({ date: '2026-08-06', source: '语音', relation: '支持', quote: '演示原文：我想先看失败会发生在哪里，再讨论完整路径。' }),
          Object.freeze({ date: '2026-08-03', source: 'Chrome', relation: '反例', quote: '演示原文：这次范围很小，我先搭出完整方案再统一检查。' })
        ])
      }),
      Object.freeze({
        id: 'mem_calibration_first',
        group: '最近正在变化',
        statement: '在设计个人工具时，你开始把“理解能否被校准”放在功能数量之前。',
        scope: '个人产品设计',
        updatedAt: '2026-08-16',
        version: 2,
        evidence: Object.freeze([
          Object.freeze({ date: '2026-08-16', source: 'Chrome', relation: '支持', quote: '我希望理解这件事易于看懂，也能随时回到证据。' }),
          Object.freeze({ date: '2026-08-15', source: 'Chrome', relation: '支持', quote: '先把 Workflow 的控制和 Agent 的判断区分清楚。' }),
          Object.freeze({ date: '2026-08-12', source: '语音', relation: '边界', quote: '演示原文：我仍希望先尽快用起来，后面再逐步完善。' })
        ])
      }),
      Object.freeze({
        id: 'mem_order_without_pressure',
        group: '仍在权衡',
        statement: '你希望记录带来秩序，同时不想让它变成新的完成压力。',
        scope: '个人记录方式',
        updatedAt: '2026-08-15',
        version: 2,
        evidence: Object.freeze([
          Object.freeze({ date: '2026-08-16', source: 'Chrome', relation: '支持', quote: '好烦恼，感觉自己少了一些秩序感。' }),
          Object.freeze({ date: '2026-08-14', source: '语音', relation: '支持', quote: '演示原文：记录下来本身就应该有价值。' }),
          Object.freeze({ date: '2026-08-11', source: 'Chrome', relation: '反例', quote: '演示原文：有些事情如果不明确下一步，很容易再次消失。' })
        ])
      })
    ]),
    records: Object.freeze([
      Object.freeze({ id: 'r1050', time: '10:50', source: '语音', tag: '想法', text: '感觉长期来看，我需要对整个 UI 做一个重构，把复制能力收进一个点，不要再占据主页。' }),
      Object.freeze({ id: 'r1020', time: '10:20', source: 'Chrome', tag: '想法', text: '好烦恼，感觉自己少了一些秩序感。' }),
      Object.freeze({ id: 'r0942', time: '09:42', source: 'Chrome', tag: '灵感', text: '首页应该先让我看见 Memento 如何理解我，同时保留今天的记录。' }),
      Object.freeze({ id: 'r0855', time: '08:55', source: 'Chrome', tag: '产品', text: '复制给 AI 的能力可以收到导出里，不再占主页第一位。' }),
      Object.freeze({ id: 'r0730', time: '07:30', source: '文字', tag: '决定', text: '关于我放在最上面，下面是今天留下的记录。' })
    ]),
    heat: Object.freeze([
      0,0,0,1,0,0,1,0,2,0,0,1,0,0,0,1,1,0,0,0,1,2,0,0,1,0,0,0,0,1,
      0,1,0,0,2,0,1,0,0,1,0,0,0,1,2,0,0,1,0,1,0,0,0,2,0,1,0,0,1,0,
      0,1,0,2,1,0,0,1,2,1,0,3,1,0,2,1,4,0,1,2,1,3,1,2,0,2,1,3,2,4
    ])
  });
}());
