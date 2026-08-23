(function () {
  'use strict';

  const allowedScenes = new Set(['normal', 'empty', 'partial']);
  const queryScene = new URLSearchParams(window.location.search).get('state');
  const scene = allowedScenes.has(queryScene) ? queryScene : 'normal';

  const statusMeta = {
    raw_saved: { label: '原文已保存', tone: 'neutral' },
    processing: { label: '正在整理', tone: 'blue' },
    ready: { label: '待归并', tone: 'blue' },
    merged: { label: '已归并', tone: 'success' },
    needs_review: { label: '需校准', tone: 'alert' },
    failed: { label: '整理失败', tone: 'alert' },
    original_only: { label: '仅保存原文', tone: 'neutral' }
  };

  const baseRecords = [
    {
      id: 'rec_1050', time: '09:42', source: 'Chrome', status: 'merged', type: '我的想法', topic: '产品首页', purpose: 'UI 重构',
      summary: '首页应该先展示 Memento 如何理解我。',
      raw: '首页应该先让我看见 Memento 如何理解我，同时保留今天的记录。',
      memories: [
        { id: 'mem_ui_understanding', type: '我的想法', topic: '产品首页', text: '首页应该先展示 Memento 如何理解我。' }
      ],
      peaks: [
        { id: 'peak_product', name: '产品判断', relation: '支持', state: '已归并' },
        { id: 'peak_system', name: '系统边界', relation: '适用边界', state: '已归并' },
        { id: 'peak_record', name: '记录方式', relation: '支持', state: '已归并' }
      ],
      mapMemories: ['mem_ui_understanding']
    },
    {
      id: 'rec_1108', time: '10:50', source: '语音', status: 'ready', type: '初步整理', topic: '认知地图', purpose: '等待今日归并',
      summary: '用地形表达理解与证据的长期聚类。',
      raw: '认知地图更像一个知识图谱，峰代表认知取向，证据都来自我的记录。',
      memories: [{ id: 'mem_pending_map', type: '初步整理', topic: '认知地图', text: '用地形表达理解与证据的长期聚类。' }],
      peaks: [
        { id: 'peak_product', name: '产品判断', relation: '候选支持', state: '待归并' }
      ],
      mapMemories: ['mem_pending_map']
    },
    {
      id: 'rec_1422', time: '14:22', source: '网页选文', status: 'needs_review', type: '我的想法', topic: '记忆拆解', purpose: '整理规则',
      summary: '一条原始记录可以被拆成多条可用记忆，并分别进入不同的长期理解。',
      raw: '一条记录也可能在多个峰上都有体现，这样才代表系统对笔记有一定的拆解能力。',
      memories: [
        { id: 'mem_1422_a', type: '产品原则', topic: '一对多拆解', text: '原始记录与可用记忆不必保持一对一。' },
        { id: 'mem_1422_b', type: '关系判断', topic: '跨峰记忆', text: '同一条可用记忆可以同时支持多项理解。' },
        { id: 'mem_1422_c', type: '用户权利', topic: '校准', text: '多重去向需要向用户暴露并允许修改。' }
      ],
      peaks: [
        { id: 'peak_record', name: '记录方式', relation: '候选支持', state: '需校准' },
        { id: 'peak_system', name: '系统边界', relation: '候选边界', state: '需校准' }
      ],
      mapMemories: []
    },
    {
      id: 'rec_1647', time: '12:06', source: '文字', status: 'ready', type: '初步整理', topic: '表达方式', purpose: '等待今日归并',
      summary: '认知地景需要先给人一种感觉，精确解释收进详情。',
      raw: '我希望主页上先看到一个整体的感觉，点开以后才看具体的理解和证据。',
      memories: [{ id: 'mem_pending_language', type: '初步整理', topic: '表达方式', text: '认知地景需要先给人一种感觉，精确解释收进详情。' }],
      peaks: [],
      mapMemories: ['mem_pending_language']
    },
    {
      id: 'rec_1810', time: '11:08', source: 'Chrome', status: 'ready', type: '初步整理', topic: '即时回执', purpose: '等待今日归并',
      summary: '记录后需要立即看见 AI 把它整理成了什么。',
      raw: '如果每天整理以后才看到结果，我会不知道每条原始记录被整理成什么样。',
      memories: [{ id: 'mem_pending_realtime', type: '初步整理', topic: '即时回执', text: '记录后需要立即看见 AI 把它整理成了什么。' }],
      peaks: [{ id: 'peak_record', name: '记录方式', relation: '候选修订', state: '待归并' }],
      mapMemories: ['mem_pending_realtime']
    },
    {
      id: 'rec_1842', time: '18:42', source: '语音 · 42.1 秒', status: 'processing', type: '已识别为语音思考', topic: '正在识别主题', purpose: '待判断',
      summary: '原文和录音已保存，正在拆解这条记录中的多个意图。',
      raw: '我还想确认，整理回执需要足够轻，不应该打断我继续记录下一件事。',
      memories: [], peaks: [], mapMemories: []
    },
    {
      id: 'rec_1904', time: '19:04', source: '截图 · OCR', status: 'failed', type: '来源已保留', topic: '尚未确定', purpose: '待重试',
      summary: '原图和 OCR 文本已保存，本次整理没有完成。',
      raw: '展示如何在单条整理失败时，仍然保留原始内容和可以重试的入口。',
      memories: [], peaks: [], mapMemories: []
    },
    {
      id: 'rec_2002', time: '20:02', source: '文字', status: 'raw_saved', type: '原文已接住', topic: '等待整理', purpose: '尚未判断',
      summary: '这条记录刚刚留下，原文已经保存。',
      raw: '将实时反馈做得足够轻，让用户知道它被接住了，也不迫使他立刻处理。',
      memories: [], peaks: [], mapMemories: []
    }
  ];

  let records = baseRecords.map(cloneRecord);
  let activeFilter = 'all';
  let selectedRecordId = null;
  let activePeakId = null;
  let drawerMode = null;
  let lastFocusRecordId = null;
  let lastExternalTrigger = null;
  let lastMergeOutcome = null;
  let toastTimer = null;
  const mergedMapMemoryIds = new Set(
    records.filter((record) => record.status === 'merged').flatMap((record) => record.mapMemories)
  );

  if (scene === 'empty') records = [];
  if (scene === 'partial') records = records.filter((record) => ['rec_1050', 'rec_1810', 'rec_1904', 'rec_2002'].includes(record.id));

  const peakNames = {
    peak_system: '系统边界',
    peak_product: '产品判断',
    peak_record: '记录方式',
    peak_growth: '长期成长'
  };

  const mapMemoryToRecord = new Map();
  records.forEach((record) => record.mapMemories.forEach((id) => mapMemoryToRecord.set(id, record.id)));

  const dom = {
    shell: document.querySelector('#app-shell'),
    workspace: document.querySelector('#map-workspace'),
    svg: document.querySelector('#cognitive-map'),
    peakLayer: document.querySelector('[data-layer="peaks"]'),
    memoryLayer: document.querySelector('[data-layer="memories"]'),
    recordList: document.querySelector('[data-record-list]'),
    recordsEmpty: document.querySelector('[data-records-empty]'),
    selectionNote: document.querySelector('[data-record-selection-note]'),
    drawer: document.querySelector('[data-context-drawer]'),
    drawerEyebrow: document.querySelector('[data-drawer-eyebrow]'),
    drawerTitle: document.querySelector('[data-drawer-title]'),
    drawerBody: document.querySelector('[data-drawer-body]'),
    drawerFoot: document.querySelector('[data-drawer-foot]'),
    drawerClose: document.querySelector('[data-drawer-close]'),
    mergeButton: document.querySelector('[data-merge-today]'),
    mergeTitle: document.querySelector('[data-merge-title]'),
    mergeDetail: document.querySelector('[data-merge-detail]'),
    scheduleStatus: document.querySelector('[data-schedule-status]'),
    summaryRecords: document.querySelector('[data-summary-records]'),
    summaryMemories: document.querySelector('[data-summary-memories]'),
    summaryUnderstandings: document.querySelector('[data-summary-understandings]'),
    summaryChanges: document.querySelector('[data-summary-changes]'),
    summaryObserving: document.querySelector('[data-summary-observing]'),
    notice: document.querySelector('[data-system-notice]'),
    noticeText: document.querySelector('[data-system-notice-text]'),
    mapEmpty: document.querySelector('[data-map-empty]'),
    mapCanvas: document.querySelector('[data-map-canvas]'),
    mapTooltip: document.querySelector('[data-map-tooltip]'),
    toast: document.querySelector('[data-toast]'),
    liveRegion: document.querySelector('[data-live-region]')
  };

  init();

  function init() {
    bindEvents();
    renderRecords();
    updateSummary();
    updateMergeStatus();
    applyScene();
    observeMapRenders();
    applyMergedMapMemoryStates();
  }

  function cloneRecord(record) {
    return {
      ...record,
      memories: record.memories.map((memory) => ({ ...memory })),
      peaks: record.peaks.map((peak) => ({ ...peak })),
      mapMemories: [...record.mapMemories]
    };
  }

  function bindEvents() {
    document.querySelectorAll('[data-filter]').forEach((button) => {
      button.addEventListener('click', () => setFilter(button.dataset.filter));
    });

    document.querySelectorAll('[data-secondary-view]').forEach((button) => {
      button.addEventListener('click', () => openSecondaryDrawer(button.dataset.secondaryView, button));
    });

    document.addEventListener('click', (event) => {
      const listPeak = event.target.closest('[data-list-peak]');
      if (!listPeak) return;
      selectedRecordId = null;
      activePeakId = listPeak.dataset.listPeak;
      drawerMode = 'map';
      renderRecords();
    });

    dom.recordList.addEventListener('click', (event) => {
      const row = event.target.closest('[data-record-id]');
      if (row) openRecordDrawer(row.dataset.recordId, row);
    });

    dom.mergeButton.addEventListener('click', mergeToday);

    dom.drawerClose.addEventListener('click', (event) => {
      if (drawerMode === 'record' || drawerMode === 'secondary') {
        event.preventDefault();
        event.stopImmediatePropagation();
        closeHomeDrawer();
      }
    }, true);

    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape' || !['record', 'secondary'].includes(drawerMode) || dom.drawer.hidden) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      closeHomeDrawer();
    }, true);

    dom.drawer.addEventListener('click', (event) => {
      const action = event.target.closest('[data-record-action]');
      if (action) handleRecordAction(action.dataset.recordAction);

      const peakButton = event.target.closest('[data-jump-peak]');
      if (peakButton) jumpToPeak(peakButton.dataset.jumpPeak);
    });

    dom.drawer.addEventListener('submit', (event) => {
      const form = event.target.closest('[data-record-edit-form]');
      if (!form) return;
      event.preventDefault();
      saveRecordEdit(form);
    });

    dom.svg.addEventListener('click', (event) => {
      const entity = event.target.closest('[data-entity-type]');
      if (!entity) {
        activePeakId = null;
        if (!selectedRecordId) renderRecords();
        return;
      }

      selectedRecordId = null;
      activePeakId = null;
      lastFocusRecordId = null;
      drawerMode = 'map';
      if (entity.dataset.entityType === 'peak') activePeakId = entity.dataset.entityId;
      if (entity.dataset.entityType === 'memory') {
        const recordId = mapMemoryToRecord.get(entity.dataset.entityId);
        if (recordId) {
          event.preventDefault();
          event.stopImmediatePropagation();
          openRecordDrawer(recordId, entity);
          return;
        }
      }
      renderRecords();
    }, true);

    dom.svg.addEventListener('keydown', (event) => {
      if (!['Enter', ' '].includes(event.key)) return;
      const entity = event.target.closest('[data-entity-type="memory"]');
      if (!entity) return;
      const recordId = mapMemoryToRecord.get(entity.dataset.entityId);
      if (!recordId) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      openRecordDrawer(recordId, entity);
    }, true);

    const refreshMappedTooltip = (event) => {
      const entity = event.target.closest('[data-entity-type="memory"]');
      if (!entity) return;
      const record = records.find((item) => item.id === mapMemoryToRecord.get(entity.dataset.entityId));
      if (!record || record.status !== 'merged') return;
      requestAnimationFrame(() => {
        if (!dom.mapTooltip || dom.mapTooltip.hidden) return;
        dom.mapTooltip.innerHTML = `<strong>${escapeHtml(record.time)} / ${escapeHtml(record.type)}</strong>${escapeHtml(record.summary)}<br>已归并并可回到原文`;
      });
    };
    dom.svg.addEventListener('pointerover', refreshMappedTooltip, true);
    dom.svg.addEventListener('focusin', refreshMappedTooltip, true);

    const drawerObserver = new MutationObserver(() => {
      if (!dom.drawer.hidden) return;
      if (drawerMode === 'map') {
        drawerMode = null;
        activePeakId = null;
        selectedRecordId = null;
        renderRecords();
        return;
      }
      if (drawerMode === 'record' || drawerMode === 'secondary') {
        const focusId = lastFocusRecordId;
        const trigger = lastExternalTrigger;
        drawerMode = null;
        selectedRecordId = null;
        activePeakId = null;
        clearMapHighlights();
        renderRecords();
        window.setTimeout(() => {
          const row = focusId ? dom.recordList.querySelector(`[data-record-id="${focusId}"]`) : null;
          if (row) row.focus();
          else if (trigger && document.contains(trigger)) trigger.focus();
        }, 0);
      }
    });
    drawerObserver.observe(dom.drawer, { attributes: true, attributeFilter: ['hidden'] });
  }

  function observeMapRenders() {
    let frame = null;
    const observer = new MutationObserver(() => {
      if (frame) cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        frame = null;
        applyMergedMapMemoryStates();
        if (selectedRecordId) applyRecordMapHighlights(records.find((record) => record.id === selectedRecordId));
      });
    });
    observer.observe(dom.peakLayer, { childList: true });
    observer.observe(dom.memoryLayer, { childList: true });
  }

  function applyScene() {
    if (scene === 'partial') {
      dom.noticeText.textContent = '部分记录暂未完成读取；当前只展示 4 条已确认原文，缺失内容不会被当作“今天没有记录”。';
      dom.notice.classList.add('is-error');
      dom.notice.hidden = false;
    }

    if (scene === 'empty') {
      dom.summaryUnderstandings.textContent = '0';
      dom.summaryChanges.textContent = '0';
      dom.summaryObserving.textContent = '0';
      dom.svg.hidden = true;
      dom.mapEmpty.hidden = false;
      dom.mapCanvas.classList.add('is-empty-scene');
      document.querySelector('.landscape-section')?.classList.add('is-empty');
      document.querySelector('.time-controls').hidden = true;
    }
  }

  function closeHomeDrawer() {
    const focusId = lastFocusRecordId;
    const trigger = lastExternalTrigger;
    drawerMode = null;
    selectedRecordId = null;
    activePeakId = null;
    clearMapHighlights();
    dom.drawer.hidden = true;
    dom.workspace.classList.remove('drawer-open');
    renderRecords();
    window.setTimeout(() => {
      const row = focusId ? dom.recordList.querySelector(`[data-record-id="${focusId}"]`) : null;
      if (row) row.focus();
      else if (trigger && document.contains(trigger)) trigger.focus();
    }, 0);
  }

  function setFilter(filter) {
    activeFilter = filter;
    activePeakId = null;
    document.querySelectorAll('[data-filter]').forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.filter === filter));
    });
    renderRecords();
  }

  function visibleRecords() {
    let visible = records;
    if (activeFilter === 'pending') visible = records.filter((record) => ['raw_saved', 'processing', 'ready'].includes(record.status));
    if (activeFilter === 'review') visible = records.filter((record) => ['needs_review', 'failed'].includes(record.status));
    return [...visible].sort((a, b) => a.time.localeCompare(b.time, 'zh-CN'));
  }

  function renderRecords() {
    const items = visibleRecords();
    dom.recordList.innerHTML = '';
    dom.recordsEmpty.hidden = records.length !== 0;
    dom.recordList.hidden = records.length === 0;
    dom.recordList.classList.toggle('has-evidence-filter', Boolean(activePeakId));

    if (!records.length) {
      dom.selectionNote.hidden = true;
      return;
    }

    if (!items.length) {
      dom.recordList.innerHTML = '<p class="filter-empty">当前筛选下没有记录，原始内容没有被删除。</p>';
      dom.selectionNote.hidden = true;
      return;
    }

    const activePeakName = activePeakId ? peakNames[activePeakId] : '';
    dom.selectionNote.hidden = !activePeakName;
    if (activePeakName) dom.selectionNote.textContent = `正在显示与“${activePeakName}”有已建立或候选关系的今日记录。`;

    items.forEach((record) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'record-row';
      row.dataset.recordId = record.id;
      row.setAttribute('aria-label', `${record.time}，${statusMeta[record.status].label}，${record.summary}`);
      const supportsPeak = activePeakId && record.peaks.some((peak) => peak.id === activePeakId);
      if (supportsPeak) row.classList.add('is-evidence');
      if (selectedRecordId === record.id) row.classList.add('is-selected');

      const processing = record.status === 'processing' ? '<span class="processing-line" aria-hidden="true"></span>' : '';
      const peakLinks = record.peaks.length
        ? record.peaks.map((peak) => {
            const committed = peak.state === '已归并';
            return `<span class="peak-link ${committed ? 'is-committed' : 'is-candidate'}"><span>${escapeHtml(peak.name)}</span><em>${committed ? '正式' : '候选'}</em></span>`;
          }).join('')
        : `<span>${['processing', 'raw_saved'].includes(record.status) ? '待整理后判断' : '暂未建立关系'}</span>`;
      const status = statusMeta[record.status];

      row.innerHTML = `
        <span class="record-time"><strong>${escapeHtml(record.time)}</strong><span>${escapeHtml(record.source)}</span></span>
        <span class="receipt-summary"><strong>${escapeHtml(record.summary)}</strong><span class="record-meta"><span>${escapeHtml(record.type)}</span><span>${escapeHtml(record.topic)}</span><span>${escapeHtml(record.purpose)}</span></span>${processing}</span>
        <span class="memory-count">${renderMemoryDots(record.memories.length)}<span>${memoryCountLabel(record)}</span></span>
        <span class="peak-links">${peakLinks}</span>
        <span class="status-label" data-tone="${status.tone}">${escapeHtml(status.label)}</span>`;
      dom.recordList.appendChild(row);
    });
  }

  function renderMemoryDots(count) {
    const total = Math.max(1, Math.min(count, 3));
    return `<span class="memory-dots" aria-hidden="true">${Array.from({ length: total }, (_, index) => `<i style="opacity:${count ? .74 : .2};${index >= count ? 'visibility:hidden' : ''}"></i>`).join('')}</span>`;
  }

  function memoryCountLabel(record) {
    if (!record.memories.length) return '尚未拆解';
    return record.status === 'merged' ? `${record.memories.length} 条可用记忆` : `${record.memories.length} 条候选记忆`;
  }

  function updateSummary() {
    dom.summaryRecords.textContent = String(records.length);
    dom.summaryMemories.textContent = String(records.reduce((total, record) => total + record.memories.length, 0));
  }

  function updateMergeStatus() {
    const ready = records.filter((record) => record.status === 'ready');
    const reviews = records.filter((record) => record.status === 'needs_review').length;
    const failures = records.filter((record) => record.status === 'failed').length;

    if (!records.length) {
      dom.mergeTitle.textContent = '今日暂无可归并内容';
      dom.mergeDetail.textContent = '新记录会先在此完成逐条整理';
      dom.mergeButton.disabled = true;
      return;
    }

    if (lastMergeOutcome) {
      dom.mergeTitle.textContent = `本次归并完成 · ${lastMergeOutcome.memories} 条记忆已可用`;
      dom.mergeDetail.textContent = reviews || failures ? `${reviews} 条需校准 · ${failures} 条可独立重试` : '已确认记忆会参与下一轮地景关系重算';
    } else {
      dom.mergeTitle.textContent = ready.length ? `${ready.length} 条记录可归并` : '今日可用记忆已完成归并';
      dom.mergeDetail.textContent = reviews || failures ? '需校准与失败内容不会被强制写入' : '归并后才会稳定进入认知地景';
    }
    dom.mergeButton.disabled = !ready.length;
  }

  function openRecordDrawer(recordId, trigger) {
    const record = records.find((item) => item.id === recordId);
    if (!record) return;

    dom.svg.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    selectedRecordId = recordId;
    activePeakId = null;
    drawerMode = 'record';
    lastFocusRecordId = recordId;
    lastExternalTrigger = trigger;
    dom.drawer.hidden = false;
    dom.workspace.classList.add('drawer-open');
    dom.drawer.dataset.entityType = 'record';
    renderRecordDrawer(record);
    renderRecords();
    applyRecordMapHighlights(record);
    requestAnimationFrame(() => dom.drawerClose.focus({ preventScroll: true }));
  }

  function renderRecordDrawer(record, editing) {
    const status = statusMeta[record.status];
    dom.drawerEyebrow.textContent = `整理回执 · ${record.time}`;
    dom.drawerTitle.textContent = record.memories.length
      ? `被整理成 ${record.memories.length} 条${record.status === 'merged' ? '可用' : '候选'}记忆`
      : status.label;

    if (editing) {
      dom.drawerBody.innerHTML = `
        <form class="edit-form" data-record-edit-form data-record-id="${record.id}">
          <div><label for="edit-summary">修改整理结果</label><textarea id="edit-summary" name="summary">${escapeHtml(record.summary)}</textarea></div>
          <div><label for="edit-type">内容类型</label><input id="edit-type" name="type" value="${escapeHtml(record.type)}"></div>
          <div><label for="edit-topic">主题</label><input id="edit-topic" name="topic" value="${escapeHtml(record.topic)}"></div>
          <p class="drawer-boundary">修改只作用于 AI 整理结果，原始记录保持不变。</p>
          <div class="edit-actions"><button type="button" data-record-action="cancel-edit">取消</button><button class="confirm-action" type="submit">保存修改</button></div>
        </form>`;
      dom.drawerFoot.innerHTML = '';
      requestAnimationFrame(() => dom.drawerBody.querySelector('textarea')?.focus());
      return;
    }

    const memoryItems = record.memories.length
      ? record.memories.map((memory) => `<li class="memory-item"><span>${escapeHtml(memory.type)} · ${escapeHtml(memory.topic)}</span><strong>${escapeHtml(memory.text)}</strong></li>`).join('')
      : `<li class="memory-item"><span>${record.status === 'failed' ? '本次未形成记忆' : '尚未完成拆解'}</span><strong>原始内容仍然安全保留。</strong></li>`;
    const peakItems = record.peaks.length
      ? record.peaks.map((peak) => `<li><button type="button" data-jump-peak="${peak.id}">${escapeHtml(peak.name)}</button><span>${escapeHtml(peak.relation)} · ${escapeHtml(peak.state)}</span></li>`).join('')
      : '<li><strong>暂未进入认知地景</strong><span>这不影响原文被保留</span></li>';

    dom.drawerBody.innerHTML = `
      <section class="drawer-section">
        <div class="receipt-status"><span>${escapeHtml(record.source)} · 原文已保存</span><strong>${escapeHtml(status.label)}</strong></div>
        <p class="receipt-lead">${escapeHtml(record.summary)}</p>
        <div class="receipt-tags"><span>${escapeHtml(record.type)}</span><span>${escapeHtml(record.topic)}</span><span>用于 ${escapeHtml(record.purpose)}</span></div>
      </section>
      <section class="drawer-section"><h3>${record.status === 'merged' ? '已归并的可用记忆' : '当前候选拆解'}</h3><ol class="memory-list">${memoryItems}</ol></section>
      <section class="drawer-section"><h3>进入哪些理解</h3><ul class="relation-list">${peakItems}</ul><p class="drawer-boundary">一条原始记录可以拆成多条记忆，也可以同时支持多项理解。归并前的候选关系不会直接改变地景。</p></section>
      <section class="drawer-section"><details class="raw-disclosure"><summary>查看原始记录</summary><p class="raw-text">${escapeHtml(record.raw)}</p></details></section>`;

    renderRecordFooter(record);
  }

  function renderRecordFooter(record) {
    if (record.status === 'failed') {
      dom.drawerFoot.innerHTML = '<div class="drawer-actions"><button type="button" data-record-action="original">仅保存原文</button><button class="is-primary" type="button" data-record-action="retry">重试整理</button></div>';
      return;
    }
    if (['processing', 'raw_saved'].includes(record.status)) {
      dom.drawerFoot.innerHTML = '<div class="drawer-actions"><button type="button" data-record-action="original">仅保存原文</button><span class="drawer-boundary">整理完成前无需停留在本页</span></div>';
      return;
    }
    if (record.status === 'original_only') {
      dom.drawerFoot.innerHTML = '<div class="drawer-actions"><span class="drawer-boundary">这条记录不会进入长期理解。</span><button type="button" data-record-action="retry">重新整理</button></div>';
      return;
    }
    dom.drawerFoot.innerHTML = '<div class="drawer-actions"><button type="button" data-record-action="original">仅保存原文</button><button type="button" data-record-action="edit">改一下</button><button class="is-primary" type="button" data-record-action="correct">整理正确</button></div>';
  }

  function handleRecordAction(action) {
    const record = records.find((item) => item.id === selectedRecordId);
    if (!record) return;

    if (action === 'edit') renderRecordDrawer(record, true);
    if (action === 'cancel-edit') renderRecordDrawer(record, false);
    if (action === 'correct') {
      if (record.status !== 'merged') record.status = 'ready';
      renderRecordDrawer(record, false);
      renderRecords();
      updateMergeStatus();
      showToast('已确认本条整理结果，等待每日归并。');
    }
    if (action === 'original') {
      record.mapMemories.forEach((id) => {
        mergedMapMemoryIds.delete(id);
        dom.svg.querySelector(`[data-entity-type="memory"][data-entity-id="${id}"]`)?.classList.remove('home-merged', 'home-memory-highlight');
      });
      record.status = 'original_only';
      record.memories = [];
      record.peaks = [];
      record.mapMemories = [];
      record.summary = '这条内容仅保留原始记录，不参与后续归并。';
      renderRecordDrawer(record, false);
      renderRecords();
      clearMapHighlights();
      updateSummary();
      updateMergeStatus();
      showToast('已移除整理结果，原始记录仍然保留。');
    }
    if (action === 'retry') retryRecord(record);
  }

  function saveRecordEdit(form) {
    const record = records.find((item) => item.id === form.dataset.recordId);
    if (!record) return;
    const data = new FormData(form);
    record.summary = String(data.get('summary')).trim() || record.summary;
    record.type = String(data.get('type')).trim() || record.type;
    record.topic = String(data.get('topic')).trim() || record.topic;
    if (record.status === 'needs_review') record.status = 'ready';
    renderRecordDrawer(record, false);
    renderRecords();
    updateMergeStatus();
    showToast('已更新整理结果，原始记录未改动。');
  }

  function retryRecord(record) {
    record.status = 'processing';
    record.summary = '原文已保存，正在重新拆解这条记录。';
    renderRecordDrawer(record, false);
    renderRecords();
    updateMergeStatus();

    window.setTimeout(() => {
      record.status = 'ready';
      record.type = '产品边界';
      record.topic = '失败恢复';
      record.purpose = '可靠性设计';
      record.summary = '单条整理失败时，原始内容会继续保留，并可以独立重试。';
      record.memories = [{ id: `${record.id}_retry`, type: '系统边界', topic: '失败恢复', text: '单条处理失败不影响原文保留与其他记录。' }];
      record.peaks = [{ id: 'peak_system', name: '系统边界', relation: '候选支持', state: '待归并' }];
      if (selectedRecordId === record.id) renderRecordDrawer(record, false);
      renderRecords();
      updateSummary();
      updateMergeStatus();
      showToast('重试完成，新的整理结果已回到今日队列。');
    }, 1200);
  }

  function mergeToday() {
    const ready = records.filter((record) => record.status === 'ready');
    if (!ready.length) {
      showToast('当前没有可直接归并的记录。');
      return;
    }

    dom.mergeButton.disabled = true;
    dom.mergeButton.textContent = '归并中…';
    dom.mergeTitle.textContent = '正在整理今日关系';
    dom.mergeDetail.textContent = '去重、对齐已有理解，并保留反例与适用边界';

    window.setTimeout(() => {
      const memoryTotal = ready.reduce((total, record) => total + record.memories.length, 0);
      ready.forEach((record) => {
        record.status = 'merged';
        if (record.purpose === '等待今日归并') record.purpose = '已进入可用记忆';
        record.mapMemories.forEach((id) => {
          mergedMapMemoryIds.add(id);
          const node = dom.svg.querySelector(`[data-entity-type="memory"][data-entity-id="${id}"]`);
          node?.classList.add('home-merged');
        });
      });
      applyMergedMapMemoryStates();
      lastMergeOutcome = { records: ready.length, memories: memoryTotal };
      dom.mergeButton.textContent = '归并今天';
      dom.scheduleStatus.textContent = '今日已手动归并 · 21:00 仍会检查未完成项';
      renderRecords();
      updateSummary();
      updateMergeStatus();
      if (selectedRecordId) {
        const selected = records.find((record) => record.id === selectedRecordId);
        if (selected) renderRecordDrawer(selected, false);
      }
      showToast(`已归并 ${ready.length} 条记录；需校准、失败和仅原文内容没有被强制写入。`);
    }, 950);
  }

  function openSecondaryDrawer(mode, trigger) {
    dom.svg.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    drawerMode = 'secondary';
    selectedRecordId = null;
    activePeakId = null;
    lastFocusRecordId = null;
    lastExternalTrigger = trigger;
    clearMapHighlights();
    renderRecords();

    const views = {
      history: {
        eyebrow: '每日历史', title: '记录日的整理进度', intro: '每一天都保留初次记录、逐条整理结果与归并后版本。',
        items: [['8 月 17 日', `${records.length} 条记录 · 今日队列`, '今天'], ['8 月 16 日', '5 条记录 · 7 条可用记忆', '已归并'], ['8 月 15 日', '3 条记录 · 1 条待校准', '已保留']]
      },
      library: {
        eyebrow: '资料库', title: '长期资料与整理设置', intro: '跨日原文、附件、已整理记忆与版本在这里集中管理。',
        items: [['打开资料库', '按主题、时间、来源和状态查找', '资料'], ['归档与导入', '查看最近导入、失败恢复和本地归档', '管理'], ['回收站', '恢复已移除的整理结果和理解', '可恢复']]
      },
      export: {
        eyebrow: '输出与导出', title: '把已整理的内容带走', intro: '导出保留原文、AI 整理、记忆拆解、关系和版本。本 Demo 不会生成文件。',
        items: [['整理后的 HTML', '适合长期阅读与存档', 'HTML'], ['原始记录包', '包含文字、图像和录音索引', 'ZIP'], ['可用记忆', '包含版本、关系和校准状态', 'JSON']]
      },
      settings: {
        eyebrow: '设置', title: '自动整理与本地边界', intro: '记录可以逐条实时整理；长期关系默认在每日归并阶段更新。',
        items: [['每日 21:00', '自动检查待归并、失败和需校准项', '已开启'], ['本地优先', '原文、整理结果和理解均留在本地', '已开启'], ['模型与权限', '查看处理模型、来源访问和失败恢复', '可管理']]
      },
      'all-records': {
        eyebrow: '全部记录', title: '跨日找回原始内容', intro: '全部记录承担跨日检索和查找，不使用三个粗标签概括所有内容。',
        items: [['按内容类型', '观察、判断、方法、问题、行动', '可筛选'], ['按主题', '项目、产品、成长与自定义主题', '可筛选'], ['按处理状态', '待归并、需校准、已归并、仅原文', '可筛选']]
      }
    };

    const view = views[mode];
    if (!view) return;
    dom.drawerEyebrow.textContent = view.eyebrow;
    dom.drawerTitle.textContent = view.title;
    dom.drawerBody.innerHTML = `<div class="secondary-intro"><strong>${escapeHtml(view.title)}</strong><span>${escapeHtml(view.intro)}</span></div><ul class="secondary-list">${view.items.map((item) => `<li><div><strong>${escapeHtml(item[0])}</strong><span>${escapeHtml(item[1])}</span></div><em>${escapeHtml(item[2])}</em></li>`).join('')}</ul>`;
    dom.drawerFoot.innerHTML = '<span class="drawer-boundary">此处保留原有能力，不抢占“理解 → 整理 → 记录”的主流程。</span>';
    dom.drawer.hidden = false;
    dom.drawer.dataset.entityType = 'secondary';
    dom.workspace.classList.add('drawer-open');
    requestAnimationFrame(() => dom.drawerClose.focus({ preventScroll: true }));
  }

  function applyRecordMapHighlights(record) {
    clearMapHighlights();
    if (!record) return;
    dom.svg.classList.add('has-home-record');
    record.peaks.forEach((peak) => {
      dom.svg.querySelector(`[data-entity-type="peak"][data-entity-id="${peak.id}"]`)?.classList.add('home-evidence-highlight');
    });
    record.mapMemories.forEach((id) => {
      dom.svg.querySelector(`[data-entity-type="memory"][data-entity-id="${id}"]`)?.classList.add('home-memory-highlight');
    });
  }

  function applyMergedMapMemoryStates() {
    mergedMapMemoryIds.forEach((id) => {
      const node = dom.svg.querySelector(`[data-entity-type="memory"][data-entity-id="${id}"]`);
      if (!node) return;
      node.classList.remove('is-pending');
      node.classList.add('is-committed', 'home-merged');
      node.setAttribute('aria-label', node.getAttribute('aria-label')?.replace('等待归并', '已归并') || '已归并记忆');
      node.querySelector('.memory-mark')?.setAttribute('r', '3.4');
    });
  }

  function clearMapHighlights() {
    dom.svg.classList.remove('has-home-record');
    dom.svg.querySelectorAll('.home-evidence-highlight, .home-memory-highlight').forEach((node) => node.classList.remove('home-evidence-highlight', 'home-memory-highlight'));
  }

  function jumpToPeak(peakId) {
    const target = dom.svg.querySelector(`[data-entity-type="peak"][data-entity-id="${peakId}"]`);
    if (!target) {
      showToast('该关系还没有形成正式认知峰。');
      return;
    }
    target.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    document.querySelector('.landscape-section')?.scrollIntoView({ behavior: prefersReducedMotion() ? 'auto' : 'smooth', block: 'start' });
  }

  function showToast(message) {
    dom.toast.textContent = message;
    dom.toast.hidden = false;
    announce(message);
    clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { dom.toast.hidden = true; }, 3200);
  }

  function announce(message) {
    dom.liveRegion.textContent = '';
    window.setTimeout(() => { dom.liveRegion.textContent = message; }, 20);
  }

  function prefersReducedMotion() {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }
}());
