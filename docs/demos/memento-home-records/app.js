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
      id: 'rec_1050',
      time: '10:50',
      source: '语音 · 18.6 秒',
      status: 'ready',
      type: '我的观察',
      topic: '产品首页',
      purpose: '界面重构',
      summary: '首页需要先让用户看见 Memento 如何理解他，当日记录继续作为理解的来路。',
      raw: '感觉长期来看，需要对整个 UI 做一个重构。理解这件事应该放在首页上，但是今天留下的记录也仍然重要。',
      memories: [
        { id: 'mem_1050_a', type: '我的判断', topic: '信息层级', text: '理解应成为首页第一视觉重心。' },
        { id: 'mem_1050_b', type: '我的边界', topic: '记录价值', text: '今日记录仍需在首页直接可见。' }
      ],
      peaks: [
        { name: '产品判断', relation: '候选支持', state: '待归并' },
        { name: '记录方式', relation: '适用边界', state: '待归并' }
      ]
    },
    {
      id: 'rec_1108',
      time: '11:08',
      source: '截图 · OCR',
      status: 'merged',
      type: '我的方法',
      topic: '认知地景',
      purpose: '交互设计',
      summary: '认知地景中的位置和地形应由已建立的证据关系塑形，不让装饰性距离暗示虚假含义。',
      raw: '地形图并不只是背景，它需要有真正的含义。词条之间的距离和连接都应该能回到原始内容。',
      memories: [
        { id: 'mem_1108_a', type: '我的原则', topic: '空间语义', text: '地形与节点位置只表达系统已建立的关系。' }
      ],
      peaks: [
        { name: '系统边界', relation: '支持', state: '已归并' },
        { name: '产品判断', relation: '支持', state: '已归并' }
      ]
    },
    {
      id: 'rec_1422',
      time: '14:22',
      source: '网页选文',
      status: 'needs_review',
      type: '我的想法',
      topic: '记忆拆解',
      purpose: '整理规则',
      summary: '一条原始记录可以被拆成多条可用记忆，并分别进入不同的长期理解。',
      raw: '一条记录也可能在多个峰上都有体现，这样才代表系统对笔记有一定的拆解能力。',
      memories: [
        { id: 'mem_1422_a', type: '产品原则', topic: '一对多拆解', text: '原始记录与可用记忆不必保持一对一。' },
        { id: 'mem_1422_b', type: '关系判断', topic: '跨峰记忆', text: '同一条可用记忆可以同时支持多项理解。' },
        { id: 'mem_1422_c', type: '用户权利', topic: '校准', text: '多重去向需要向用户暴露并允许修改。' }
      ],
      peaks: [
        { name: '记录方式', relation: '候选支持', state: '需校准' },
        { name: '系统边界', relation: '候选边界', state: '需校准' }
      ]
    },
    {
      id: 'rec_1647',
      time: '16:47',
      source: '文字',
      status: 'ready',
      type: '我的判断',
      topic: '视觉层级',
      purpose: '界面表达',
      summary: '首页负责让人感受到理解的变化；证据、版本和修改操作收进侧边详情。',
      raw: '认知地图在首页应该先呈现一种感觉，点击以后才看具体证据、修改和版本。',
      memories: [
        { id: 'mem_1647_a', type: '交互原则', topic: '渐进披露', text: '首页呈现整体感受，细节通过侧边详情逐步展开。' }
      ],
      peaks: []
    },
    {
      id: 'rec_1810',
      time: '18:10',
      source: '语音 · 42.1 秒',
      status: 'processing',
      type: '已识别为语音思考',
      topic: '正在识别主题',
      purpose: '待判断',
      summary: '原文和录音已保存，正在拆解这条记录中的多个意图。',
      raw: '我想再看一下每条内容整理完成以后，用户要怎么立即感觉到 AI 把它变成了什么，但这个回执不能打断记录。',
      memories: [],
      peaks: []
    },
    {
      id: 'rec_1904',
      time: '19:04',
      source: '截图 · OCR',
      status: 'failed',
      type: '来源已保留',
      topic: '尚未确定',
      purpose: '待重试',
      summary: '原图和 OCR 文本已保存，本次整理没有完成。',
      raw: '展示如何在单条整理失败时，仍然保留原始内容和可以重试的入口。',
      memories: [],
      peaks: []
    },
    {
      id: 'rec_2002',
      time: '20:02',
      source: '文字',
      status: 'raw_saved',
      type: '原文已接住',
      topic: '等待整理',
      purpose: '尚未判断',
      summary: '这条记录刚刚留下，原文已经保存。',
      raw: '将实时反馈做得足够轻，让用户知道它被接住了，也不迫使他立刻处理。',
      memories: [],
      peaks: []
    }
  ];

  let records = baseRecords.map(cloneRecord);
  let activeFilter = 'all';
  let selectedRecordId = null;
  let lastTrigger = null;
  let drawerMode = null;
  let toastTimer = null;
  let lastMergeOutcome = null;
  let lastTriggerRecordId = null;

  if (scene === 'empty') records = [];
  if (scene === 'partial') {
    records = records.filter((record) => ['rec_1050', 'rec_1810', 'rec_1904', 'rec_2002'].includes(record.id));
  }

  const dom = {
    shell: document.querySelector('#app-shell'),
    workspace: document.querySelector('#records-workspace'),
    recordList: document.querySelector('[data-record-list]'),
    recordsEmpty: document.querySelector('[data-records-empty]'),
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
    summaryPeaks: document.querySelector('[data-summary-peaks]'),
    trackBars: document.querySelector('[data-track-bars]'),
    sceneNotice: document.querySelector('[data-scene-notice]'),
    toast: document.querySelector('[data-toast]'),
    liveRegion: document.querySelector('[data-live-region]')
  };

  init();

  function init() {
    renderTrack();
    bindEvents();
    renderRecords();
    updateSummary();
    updateMergeStatus();
    if (scene === 'empty') announce('已加载无记录空态演示。');
    if (scene === 'partial') {
      dom.sceneNotice.hidden = false;
      announce('部分记录暂未完成读取。当前只展示已确认原文。');
    }
  }

  function cloneRecord(record) {
    return {
      ...record,
      memories: record.memories.map((memory) => ({ ...memory })),
      peaks: record.peaks.map((peak) => ({ ...peak }))
    };
  }

  function bindEvents() {
    document.querySelectorAll('[data-filter]').forEach((button) => {
      button.addEventListener('click', () => setFilter(button.dataset.filter));
    });

    document.querySelectorAll('[data-secondary-view]').forEach((button) => {
      button.addEventListener('click', () => openSecondaryDrawer(button.dataset.secondaryView, button));
    });

    dom.drawerClose.addEventListener('click', closeDrawer);
    dom.mergeButton.addEventListener('click', mergeToday);

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !dom.drawer.hidden) closeDrawer();
    });

    dom.recordList.addEventListener('click', (event) => {
      const row = event.target.closest('[data-record-id]');
      if (row) openRecordDrawer(row.dataset.recordId, row);
    });
  }

  function renderTrack() {
    const activeDays = new Map([
      [12, 1], [18, 2], [24, 1], [28, 3], [36, 1], [43, 2], [49, 1],
      [57, 2], [64, 3], [70, 1], [74, 2], [78, 3], [82, 2], [85, 3], [87, 1], [88, 2]
    ]);
    dom.trackBars.innerHTML = '';
    for (let index = 0; index < 90; index += 1) {
      const bar = document.createElement('span');
      const density = activeDays.get(index) || 0;
      bar.className = 'track-bar';
      bar.setAttribute('aria-hidden', 'true');
      bar.style.setProperty('--bar-height', `${7 + density * 4}px`);
      if (density) bar.classList.add('has-records');
      if (density >= 3) bar.classList.add('is-busy');
      if (index === 89) bar.classList.add('is-today');
      dom.trackBars.appendChild(bar);
    }
  }

  function setFilter(filter) {
    activeFilter = filter;
    document.querySelectorAll('[data-filter]').forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.filter === filter));
    });
    renderRecords();
  }

  function getVisibleRecords() {
    if (activeFilter === 'pending') return records.filter((record) => ['raw_saved', 'processing', 'ready'].includes(record.status));
    if (activeFilter === 'review') return records.filter((record) => ['needs_review', 'failed'].includes(record.status));
    return records;
  }

  function renderRecords() {
    const visibleRecords = getVisibleRecords();
    dom.recordList.innerHTML = '';
    dom.recordsEmpty.hidden = records.length !== 0;

    if (!records.length) {
      document.querySelector('.ledger').hidden = true;
      return;
    }

    document.querySelector('.ledger').hidden = false;

    if (!visibleRecords.length) {
      const note = document.createElement('p');
      note.className = 'receipt-note';
      note.textContent = '当前筛选下没有记录。';
      dom.recordList.appendChild(note);
      return;
    }

    visibleRecords.forEach((record) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'record-row';
      row.dataset.recordId = record.id;
      row.setAttribute('aria-label', `${record.time}，${statusMeta[record.status].label}，${record.summary}`);
      if (selectedRecordId === record.id && drawerMode === 'record') row.classList.add('is-selected');

      const processing = record.status === 'processing' ? '<span class="processing-line" aria-hidden="true"></span>' : '';
      const memories = renderMemoryDots(record.memories.length);
      const peakLinks = record.peaks.length
        ? record.peaks.map((peak) => {
            const committed = peak.state === '已归并';
            return `<span class="peak-link ${committed ? 'is-committed' : 'is-candidate'}"><span>${escapeHtml(peak.name)}</span><em>${committed ? '正式' : '候选'}</em></span>`;
          }).join('')
        : `<span>${record.status === 'processing' || record.status === 'raw_saved' ? '待整理后判断' : '暂未建立关系'}</span>`;
      const status = statusMeta[record.status];

      row.innerHTML = `
        <span class="record-time"><strong>${escapeHtml(record.time)}</strong><span>${escapeHtml(record.source)}</span></span>
        <span class="receipt-summary"><strong>${escapeHtml(record.summary)}</strong><span class="record-meta"><span>${escapeHtml(record.type)}</span><span>${escapeHtml(record.topic)}</span><span>${escapeHtml(record.purpose)}</span></span>${processing}</span>
        <span class="memory-count">${memories}<span>${memoryCountLabel(record)}</span></span>
        <span class="peak-links${record.peaks.length ? '' : ' is-empty'}">${peakLinks}</span>
        <span class="status-label" data-tone="${status.tone}">${status.label}</span>
      `;
      dom.recordList.appendChild(row);
    });
  }

  function renderMemoryDots(count) {
    if (!count) return '<span class="memory-dots" aria-hidden="true"><i style="opacity:.22"></i></span>';
    const dots = Array.from({ length: Math.min(count, 3) }, () => '<i></i>').join('');
    return `<span class="memory-dots" aria-hidden="true">${dots}</span>`;
  }

  function memoryCountLabel(record) {
    if (!record.memories.length) return '尚未拆解';
    return record.status === 'merged'
      ? `${record.memories.length} 条可用记忆`
      : `${record.memories.length} 条候选记忆`;
  }

  function updateSummary() {
    const memoryCount = records.reduce((total, record) => total + record.memories.length, 0);
    const peaks = new Set(records.flatMap((record) => record.peaks.map((peak) => peak.name)));
    dom.summaryRecords.textContent = String(records.length);
    dom.summaryMemories.textContent = String(memoryCount);
    dom.summaryPeaks.textContent = String(peaks.size);
  }

  function updateMergeStatus() {
    const pending = records.filter((record) => ['raw_saved', 'processing', 'ready'].includes(record.status)).length;
    const mergeable = records.some((record) => record.status === 'ready');
    const reviews = records.filter((record) => record.status === 'needs_review').length;
    const failures = records.filter((record) => record.status === 'failed').length;

    if (!records.length) {
      dom.mergeTitle.textContent = '今日暂无可归并内容';
      dom.mergeDetail.textContent = '新记录会在这里显示整理进度';
      dom.mergeButton.disabled = true;
      return;
    }

    if (lastMergeOutcome) {
      const remaining = pending + reviews + failures;
      dom.mergeButton.disabled = !mergeable;
      dom.mergeTitle.textContent = '本次归并完成 · 未生成新认知峰';
      dom.mergeDetail.textContent = `${lastMergeOutcome.memories} 条记忆成为可用记忆${remaining ? ` · ${remaining} 条仍待处理` : ''}`;
      return;
    }

    dom.mergeButton.disabled = !mergeable;
    dom.mergeTitle.textContent = pending ? `${pending} 条记录仍在今日队列` : '今日可用记忆已完成归并';
    dom.mergeDetail.textContent = reviews || failures
      ? `${reviews} 条待校准 · ${failures} 条可重试`
      : '整理结果可继续修改，原文保持不变';
  }

  function openRecordDrawer(recordId, trigger) {
    const record = records.find((item) => item.id === recordId);
    if (!record) return;
    selectedRecordId = recordId;
    drawerMode = 'record';
    lastTrigger = trigger || document.activeElement;
    lastTriggerRecordId = recordId;
    dom.drawerEyebrow.textContent = `记录详情 · ${record.time}`;
    dom.drawerTitle.textContent = record.memories.length
      ? `被整理成 ${record.memories.length} 条${record.status === 'merged' ? '可用' : '候选'}记忆`
      : statusMeta[record.status].label;
    renderRecordDrawer(record);
    showDrawer();
    renderRecords();
  }

  function renderRecordDrawer(record, editing) {
    const status = statusMeta[record.status];
    const memoryItems = record.memories.length
      ? record.memories.map((memory) => `
          <li class="memory-item">
            <strong>${escapeHtml(memory.text)}</strong>
            <span>${escapeHtml(memory.type)} · ${escapeHtml(memory.topic)}</span>
          </li>`).join('')
      : `<li class="receipt-note">${record.status === 'failed' ? '整理失败后没有写入可用记忆。' : '拆解完成后会在这里展示。'}</li>`;
    const peakItems = record.peaks.length
      ? record.peaks.map((peak) => `
          <li class="mini-branch"><strong>${escapeHtml(peak.name)}</strong><span>${escapeHtml(peak.relation)} · ${escapeHtml(peak.state)}</span></li>`).join('')
      : '<li class="receipt-note">当前没有进入任何认知峰。这不会影响原始记录被保留。</li>';

    dom.drawerBody.innerHTML = `
      <div class="drawer-meta">
        <span>${escapeHtml(record.source)} · 原文已保存</span>
        <span class="drawer-status"><i aria-hidden="true"></i>${escapeHtml(status.label)}</span>
      </div>
      <section class="drawer-section">
        <h3>AI 整理回执</h3>
        <p class="receipt-quote">${escapeHtml(record.summary)}</p>
        <p class="receipt-note">${escapeHtml(record.type)} · ${escapeHtml(record.topic)} · 用于 ${escapeHtml(record.purpose)}</p>
      </section>
      ${editing ? renderEditForm(record) : ''}
      <section class="drawer-section"${editing ? ' hidden' : ''}>
        <h3>${record.status === 'merged' ? '已归并的可用记忆' : '当前候选拆解'}</h3>
        <ol class="memory-list">${memoryItems}</ol>
      </section>
      <section class="drawer-section"${editing ? ' hidden' : ''}>
        <h3>归并去向</h3>
        <ul class="relation-list">${peakItems}</ul>
        <p class="drawer-boundary">一条原始记录可以拆成多条记忆，也可以同时支持多项理解。归并之前的候选关系不会直接改变认知地景。</p>
      </section>
      <section class="drawer-section">
        <details class="raw-disclosure">
          <summary>查看原始记录</summary>
          <p class="raw-text">${escapeHtml(record.raw)}</p>
        </details>
      </section>
    `;

    if (editing) bindEditForm(record);
    renderRecordFooter(record, editing);
  }

  function renderEditForm(record) {
    return `
      <form class="edit-form" data-edit-form>
        <label for="edit-summary">修改整理后的表述</label>
        <textarea id="edit-summary" name="summary">${escapeHtml(record.summary)}</textarea>
        <div class="field-row">
          <div><label for="edit-type">内容类型</label><input id="edit-type" name="type" value="${escapeHtml(record.type)}"></div>
          <div><label for="edit-topic">主题</label><input id="edit-topic" name="topic" value="${escapeHtml(record.topic)}"></div>
        </div>
        <div class="edit-actions">
          <button type="button" data-cancel-edit>取消</button>
          <button class="confirm-action" type="submit">保存修改</button>
        </div>
      </form>`;
  }

  function bindEditForm(record) {
    const form = dom.drawerBody.querySelector('[data-edit-form]');
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const data = new FormData(form);
      record.summary = String(data.get('summary')).trim() || record.summary;
      record.type = String(data.get('type')).trim() || record.type;
      record.topic = String(data.get('topic')).trim() || record.topic;
      if (record.status === 'needs_review') record.status = 'ready';
      lastMergeOutcome = null;
      renderRecordDrawer(record, false);
      renderRecords();
      updateMergeStatus();
      showToast('修改已保存，后续归并会使用这一版。');
    });
    form.querySelector('[data-cancel-edit]').addEventListener('click', () => renderRecordDrawer(record, false));
    form.querySelector('textarea').focus();
  }

  function renderRecordFooter(record, editing) {
    if (editing) {
      dom.drawerFoot.innerHTML = '<span class="receipt-note">修改只作用于整理结果，原始记录保持不变。</span>';
      return;
    }

    if (record.status === 'failed') {
      dom.drawerFoot.innerHTML = `<div class="drawer-actions"><button type="button" data-action="original">仅保存原文</button><button class="confirm-action" type="button" data-action="retry">重试整理</button></div>`;
    } else if (record.status === 'processing' || record.status === 'raw_saved') {
      dom.drawerFoot.innerHTML = `<div class="drawer-actions"><button type="button" data-action="original">仅保存原文</button><span class="receipt-note">整理完成前无需停留在本页</span></div>`;
    } else if (record.status === 'original_only') {
      dom.drawerFoot.innerHTML = `<div class="drawer-actions"><span class="receipt-note">这条记录不会进入长期理解。</span><button type="button" data-action="retry">重新整理</button></div>`;
    } else {
      dom.drawerFoot.innerHTML = `
        <div class="drawer-actions">
          <button type="button" data-action="original">仅保存原文</button>
          <button type="button" data-action="edit">改一下</button>
          <button class="confirm-action" type="button" data-action="correct">整理正确</button>
        </div>`;
    }

    dom.drawerFoot.querySelectorAll('[data-action]').forEach((button) => {
      button.addEventListener('click', () => handleRecordAction(record, button.dataset.action));
    });
  }

  function handleRecordAction(record, action) {
    if (action === 'edit') {
      renderRecordDrawer(record, true);
      return;
    }
    if (action === 'correct') {
      if (record.status !== 'merged') record.status = 'ready';
      lastMergeOutcome = null;
      renderRecordDrawer(record, false);
      renderRecords();
      updateMergeStatus();
      showToast('已记下“整理正确”，下一次归并会沿用这一版。');
      return;
    }
    if (action === 'original') {
      record.status = 'original_only';
      lastMergeOutcome = null;
      record.memories = [];
      record.peaks = [];
      record.summary = '这条内容仅保留原始记录，不参与后续归并。';
      dom.drawerTitle.textContent = '仅保存原文';
      renderRecordDrawer(record, false);
      renderRecords();
      updateSummary();
      updateMergeStatus();
      showToast('已仅保存原文，本次 AI 整理结果已撤回。');
      return;
    }
    if (action === 'retry') retryRecord(record);
  }

  function retryRecord(record) {
    lastMergeOutcome = null;
    record.status = 'processing';
    record.summary = '原文已保存，正在重新拆解这条记录。';
    renderRecordDrawer(record, false);
    renderRecords();
    updateMergeStatus();
    showToast('已开始重试，可以离开该详情。');

    window.setTimeout(() => {
      record.status = 'ready';
      record.summary = '失败内容已重新整理：当单条处理失败时，原始内容仍然安全保留并支持独立重试。';
      record.type = '产品边界';
      record.topic = '失败恢复';
      record.purpose = '可靠性设计';
      record.memories = [
        { id: `${record.id}_retry`, type: '系统原则', topic: '失败恢复', text: '单条整理失败不影响原始内容保留，并支持独立重试。' }
      ];
      record.peaks = [{ name: '系统边界', relation: '候选支持', state: '待归并' }];
      if (selectedRecordId === record.id && drawerMode === 'record') {
        dom.drawerTitle.textContent = '被整理成 1 条候选记忆';
        renderRecordDrawer(record, false);
      }
      renderRecords();
      updateSummary();
      updateMergeStatus();
      showToast('重试完成，整理结果已回到今日队列。');
    }, 1400);
  }

  function mergeToday() {
    const mergeable = records.filter((record) => record.status === 'ready');
    if (!mergeable.length) {
      showToast('当前没有可直接归并的记录。需校准和失败内容会继续保留。');
      return;
    }

    dom.mergeButton.disabled = true;
    dom.mergeButton.textContent = '归并中…';
    dom.mergeTitle.textContent = '正在整理今日关系';
    dom.mergeDetail.textContent = '正在去重、对齐已有理解并保留候选边界';

    window.setTimeout(() => {
      const memoryTotal = mergeable.reduce((total, record) => total + record.memories.length, 0);
      mergeable.forEach((record) => {
        record.status = 'merged';
        record.peaks.forEach((peak) => {
          peak.state = '已归并';
          peak.relation = peak.relation.replace(/^候选/, '');
        });
      });
      lastMergeOutcome = { records: mergeable.length, memories: memoryTotal };
      dom.mergeButton.textContent = '归并今天';
      dom.scheduleStatus.textContent = '今日已手动归并 · 21:00 仍会检查未完成项';
      renderRecords();
      updateSummary();
      updateMergeStatus();
      if (selectedRecordId && drawerMode === 'record') {
        const selected = records.find((record) => record.id === selectedRecordId);
        if (selected) renderRecordDrawer(selected, false);
      }
      showToast(`已归并 ${mergeable.length} 条记录。需校准、失败和仅保存原文的内容未被强制写入。`);
    }, 1250);
  }

  function openSecondaryDrawer(mode, trigger) {
    drawerMode = mode;
    selectedRecordId = null;
    lastTrigger = trigger || document.activeElement;
    lastTriggerRecordId = null;
    renderRecords();

    const views = {
      history: {
        eyebrow: '每日历史',
        title: '记录日的整理进度',
        intro: '日历史保留每个记录日的第一帧、整理结果和归并状态。',
        items: [
          ['8 月 17 日', `${records.length} 条记录 · 今日队列`, '今天'],
          ['8 月 16 日', '5 条记录 · 7 条可用记忆', '已归并'],
          ['8 月 15 日', '3 条记录 · 1 条待校准', '已保留']
        ]
      },
      export: {
        eyebrow: '输出与导出',
        title: '把已整理内容带走',
        intro: '导出保留原文、整理结果、记忆拆解和可追溯关系。本离线 Demo 只展示入口，不会生成文件。',
        items: [
          ['整理后的 HTML', '适合阅读与长期存档', '次级入口'],
          ['原始记录包', '包含文字、图像和录音索引', '次级入口'],
          ['可用记忆 JSON', '包含版本、关系和校准状态', '次级入口']
        ]
      },
      library: {
        eyebrow: '资料库',
        title: '长期资料与整理设置',
        intro: '归档、导入和计划设置继续保留，并从主页主流程降为资料库入口。',
        items: [
          ['最近归档与导入', '查看最近一次导入、归档和失败恢复状态', '状态入口'],
          ['打开资料库', '跨日浏览原文、附件、可用记忆与版本', '资料入口'],
          ['整理计划与设置', '查看 21:00 计划、权限和本地运行状态', '设置入口']
        ]
      },
      'all-records': {
        eyebrow: '全部记录',
        title: '跨日找回原始内容',
        intro: '全部记录承担跨日检索与查找。主页不再用旧的三个粗标签展开所有内容。',
        items: [
          ['按内容类型', '观察、判断、方法、问题、行动', '可筛选'],
          ['按主题', '项目、产品、成长与自定义主题', '可筛选'],
          ['按处理状态', '待归并、需校准、已归并、仅原文', '可筛选']
        ]
      }
    };

    const view = views[mode];
    dom.drawerEyebrow.textContent = view.eyebrow;
    dom.drawerTitle.textContent = view.title;
    dom.drawerBody.innerHTML = `
      <div class="secondary-intro"><strong>${escapeHtml(view.title)}</strong><span>${escapeHtml(view.intro)}</span></div>
      <ul class="secondary-list">
        ${view.items.map((item) => `<li><div><strong>${escapeHtml(item[0])}</strong><span>${escapeHtml(item[1])}</span></div><em>${escapeHtml(item[2])}</em></li>`).join('')}
      </ul>`;
    dom.drawerFoot.innerHTML = '<span class="receipt-note">此入口作为原有能力的迁移位置，不抢占主页核心流程。</span>';
    showDrawer();
  }

  function showDrawer() {
    dom.drawer.hidden = false;
    dom.workspace.classList.add('drawer-open');
    window.requestAnimationFrame(() => dom.drawerClose.focus());
  }

  function closeDrawer() {
    const restoreRecordId = drawerMode === 'record' ? lastTriggerRecordId : null;
    const restoreTrigger = lastTrigger;
    dom.drawer.hidden = true;
    dom.workspace.classList.remove('drawer-open');
    selectedRecordId = null;
    drawerMode = null;
    renderRecords();
    const restoredRecord = restoreRecordId
      ? Array.from(dom.recordList.querySelectorAll('[data-record-id]')).find((row) => row.dataset.recordId === restoreRecordId)
      : null;
    if (restoredRecord) restoredRecord.focus();
    else if (restoreTrigger && document.contains(restoreTrigger)) restoreTrigger.focus();
    lastTrigger = null;
    lastTriggerRecordId = null;
  }

  function showToast(message) {
    dom.toast.textContent = message;
    dom.toast.hidden = false;
    announce(message);
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { dom.toast.hidden = true; }, 3200);
  }

  function announce(message) {
    dom.liveRegion.textContent = '';
    window.setTimeout(() => { dom.liveRegion.textContent = message; }, 20);
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
