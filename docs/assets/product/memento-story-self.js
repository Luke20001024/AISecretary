(() => {
  const stage = document.getElementById('self-interpretation')
  if (!stage) return

  const judgmentButtons = Array.from(stage.querySelectorAll('.self-judgment'))
  const evidencePins = Array.from(stage.querySelectorAll('.self-pin'))
  const evidenceLinks = Array.from(stage.querySelectorAll('.self-links path[data-self-link]'))
  const resetButton = stage.querySelector('.self-synthesis-reset')
  const eyebrow = document.getElementById('self-synthesis-eyebrow')
  const title = document.getElementById('current-self-title')
  const copy = document.getElementById('self-synthesis-copy')
  const meta = document.getElementById('self-synthesis-meta')

  const defaultView = {
    eyebrow: 'MEMENTO · 当前对我的理解',
    title: '我看到的你',
    copy: '你会先确认边界再推进 重视能够回到原文的证据 也在把一次次经验变成能够复用和接手的方法',
    meta: '由多个长期主题共同支持 · 每句话都能回到来源'
  }

  const judgments = {
    action: {
      eyebrow: '理解 01 · 行动方式',
      title: '确认边界后 你会快速推进',
      copy: '产品判断 协作记录与边界反例共同指向这一模式 新证据让 Memento 将早期的「谨慎」修订为「先确认边界 随后快速行动」',
      meta: '主要依据 01 02 08 · 已被新证据更新',
      evidence: ['e1', 'e2', 'e8']
    },
    evidence: {
      eyebrow: '理解 02 · 判断方式',
      title: '你信任能够回到原文的结论',
      copy: '研究记录 证据核对与体验判断反复出现同一种要求 每个结论都要留下来源 位置和可复核的形成过程',
      meta: '主要依据 03 04 09 10 · 当前稳定',
      evidence: ['e3', 'e4', 'e9', 'e10']
    },
    accumulation: {
      eyebrow: '理解 03 · 积累方式',
      title: '你在把经验变成可复用的方法',
      copy: '相似问题第二次出现时 你会开始寻找共同机制 希望它能够被重复调用 被别人接手 也能继续迭代',
      meta: '主要依据 05 06 10 · 持续形成',
      evidence: ['e5', 'e6', 'e10']
    },
    direction: {
      eyebrow: '理解 04 · 关系与方向',
      title: '你希望成果可以被接手 判断权仍属于自己',
      copy: '你愿意让记录和方法进入新的协作关系 同时坚持原始内容留在本地 重要理解能够由你确认 修订或撤回',
      meta: '主要依据 01 06 07 08 · 近期更新',
      evidence: ['e1', 'e6', 'e7', 'e8']
    }
  }

  const evidence = {
    e1: { eyebrow: 'MEMORY 01 · AI 对话 · 今天', title: 'Context 应该跟着人走', copy: '原始记录：新的窗口不该重新认识我　Agent 理解：你在意的是跨窗口延续同一个人的关系', meta: '支持判断：行动方式 · 关系与方向 · 可回到原始对话' },
    e2: { eyebrow: 'MEMORY 02 · 产品判断 · 08.18', title: '先确认边界 随后快速推进', copy: '原始记录：方向明确以后就开始做　Agent 理解：确认边界是为了减少返工 随后的行动节奏很快', meta: '支持理解：行动方式 · 强证据 · 已更新' },
    e3: { eyebrow: 'MEMORY 03 · 研究记录 · 08.09', title: '找到原文位置 再写进结论', copy: '原始记录：没有页码就写原文未找到　Agent 理解：你把可复核性看作结论成立的前提', meta: '支持判断：判断方式 · 强证据 · 当前稳定' },
    e4: { eyebrow: 'MEMORY 04 · 证据核对 · 08.12', title: '每个判断都保留来源', copy: '原始记录：这句话要能回到哪一页　Agent 理解：你需要知道结论如何形成 也需要保留推翻它的可能', meta: '支持判断：判断方式 · 形成依据 04' },
    e5: { eyebrow: 'MEMORY 05 · 长期积累 · 08.14', title: '把重复问题沉淀成机制', copy: '原始记录：同类问题第二次出现就该形成方法　Agent 理解：你追求能够跨项目复用的能力积累', meta: '支持判断：积累方式 · 持续形成' },
    e6: { eyebrow: 'MEMORY 06 · 协作反馈 · 08.16', title: '成果应该能够被接手', copy: '原始记录：团队能够继续运行才说明机制成立　Agent 理解：你衡量成长时更看重可迁移能力和可接手性', meta: '支持判断：积累方式 · 关系与方向' },
    e7: { eyebrow: 'MEMORY 07 · 本地原则 · 08.17', title: '原始记录必须留在本地', copy: '原始记录：理解可以被调用 数据和定义自己的权力要留在手里　Agent 理解：你愿意开放使用权 同时坚持数据主权', meta: '支持判断：关系与方向 · 长期边界' },
    e8: { eyebrow: 'MEMORY 08 · 反例 · 08.18', title: '审慎不等于迟缓', copy: '原始记录：风险操作前先核对目标 之后立即执行　Agent 理解：新证据修正了早期对行动节奏的判断', meta: '更新理解：行动方式 · 新证据修正了原来的判断' },
    e9: { eyebrow: 'MEMORY 09 · 体验判断 · 08.18', title: '已有内容应该立即出现', copy: '原始记录：等待加载完成不算完成　Agent 理解：你对完成的要求包含用户此刻能否直接使用', meta: '支持判断：判断方式 · 产品质量定义' },
    e10: { eyebrow: 'MEMORY 10 · 协作边界 · 08.19', title: '保留自己的判断权', copy: '原始记录：AI 可以整理和解释 最终确认仍然由我完成　Agent 理解：你接受外部智能参与 同时保留关键解释权', meta: '支持判断：判断方式 · 积累方式 · 边界 02' }
  }

  const render = (view) => {
    eyebrow.textContent = view.eyebrow
    title.textContent = view.title
    copy.textContent = view.copy
    meta.textContent = view.meta
  }

  const clearState = () => {
    stage.classList.remove('has-selection')
    judgmentButtons.forEach((button) => {
      button.classList.remove('is-active')
      button.setAttribute('aria-pressed', 'false')
    })
    evidencePins.forEach((pin) => {
      pin.classList.remove('is-active')
      pin.setAttribute('aria-pressed', 'false')
    })
    evidenceLinks.forEach((link) => link.classList.remove('is-active'))
  }

  const selectJudgment = (key) => {
    const view = judgments[key]
    if (!view) return
    clearState()
    stage.classList.add('has-selection')
    judgmentButtons.forEach((button) => {
      const active = button.dataset.selfJudgment === key
      button.classList.toggle('is-active', active)
      button.setAttribute('aria-pressed', String(active))
    })
    evidencePins.forEach((pin) => {
      const active = view.evidence.includes(pin.dataset.selfEvidence)
      pin.classList.toggle('is-active', active)
      pin.setAttribute('aria-pressed', String(active))
    })
    evidenceLinks.forEach((link) => link.classList.toggle('is-active', link.dataset.selfLink === key))
    render(view)
  }

  const selectEvidence = (id, judgmentKey) => {
    const view = evidence[id]
    if (!view) return
    clearState()
    stage.classList.add('has-selection')
    evidencePins.forEach((pin) => {
      const active = pin.dataset.selfEvidence === id
      pin.classList.toggle('is-active', active)
      pin.setAttribute('aria-pressed', String(active))
    })
    judgmentButtons.forEach((button) => {
      const active = button.dataset.selfJudgment === judgmentKey
      button.classList.toggle('is-active', active)
      button.setAttribute('aria-pressed', String(active))
    })
    evidenceLinks.forEach((link) => link.classList.toggle('is-active', link.dataset.selfEvidence === id))
    render(view)
  }

  const reset = () => {
    clearState()
    render(defaultView)
  }

  judgmentButtons.forEach((button) => {
    button.addEventListener('click', () => selectJudgment(button.dataset.selfJudgment))
  })

  evidencePins.forEach((pin) => {
    pin.addEventListener('click', () => selectEvidence(pin.dataset.selfEvidence, pin.dataset.selfJudgment))
  })

  resetButton?.addEventListener('click', reset)
  stage.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return
    reset()
    stage.focus({ preventScroll: true })
  })
})()
