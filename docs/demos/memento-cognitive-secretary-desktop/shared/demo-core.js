(function () {
  'use strict';

  const data = window.MementoDemoData;
  if (!data) throw new Error('MementoDemoData missing');

  const state = {
    activeMemoryId: data.understandings[0].id,
    scheduleEnabled: data.run.scheduleEnabled,
    runStatus: data.run.status,
    editedStatements: new Map(),
    hiddenMemoryIds: new Set(),
    toastTimer: 0
  };

  function all(selector, root = document) {
    return Array.from(root.querySelectorAll(selector));
  }

  function activeMemory() {
    const visible = data.understandings.filter(item => !state.hiddenMemoryIds.has(item.id));
    return visible.find(item => item.id === state.activeMemoryId) || visible[0] || null;
  }

  function statementFor(memory) {
    return memory ? (state.editedStatements.get(memory.id) || memory.statement) : '';
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[char]));
  }

  function showToast(message) {
    const toast = document.querySelector('[data-toast]');
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('is-visible');
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => toast.classList.remove('is-visible'), 2200);
  }

  function openDialog(dialog) {
    if (!dialog || typeof dialog.showModal !== 'function') return;
    dialog.showModal();
  }

  function closeDialog(dialog) {
    if (dialog?.open) dialog.close();
  }

  function evidenceCounts(memory) {
    return memory.evidence.reduce((counts, item) => {
      if (item.relation === '反例') counts.counter += 1;
      else if (item.relation === '支持') counts.support += 1;
      else counts.boundary += 1;
      return counts;
    }, { support: 0, counter: 0, boundary: 0 });
  }

  function renderEvidence(memory) {
    const list = document.querySelector('[data-evidence-list]');
    const title = document.querySelector('[data-evidence-title]');
    if (!list || !title || !memory) return;
    title.textContent = memory.group;
    list.innerHTML = memory.evidence.map(item => `
      <li class="evidence-item${item.relation === '反例' ? ' is-counter' : ''}">
        <div class="evidence-meta">
          <time>${escapeHtml(item.date)}</time>
          <span>${escapeHtml(item.source)}</span>
          <span class="evidence-relation">${escapeHtml(item.relation)}</span>
        </div>
        <p class="evidence-quote">“${escapeHtml(item.quote)}”</p>
      </li>`).join('');
  }

  function renderMemory() {
    const memory = activeMemory();
    const empty = !memory || state.runStatus === 'empty';
    all('[data-memory-content]').forEach(node => { node.hidden = empty; });
    all('[data-memory-empty]').forEach(node => { node.hidden = !empty; });
    if (!memory || state.runStatus === 'empty') {
      document.dispatchEvent(new CustomEvent('memento:memory-rendered', {
        detail: { memory: null, counts: { support: 0, counter: 0, boundary: 0 } }
      }));
      return;
    }
    const counts = evidenceCounts(memory);
    all('[data-current-statement]').forEach(node => { node.textContent = statementFor(memory); });
    all('[data-current-group]').forEach(node => { node.textContent = memory.group; });
    all('[data-current-scope]').forEach(node => { node.textContent = memory.scope; });
    all('[data-current-updated]').forEach(node => { node.textContent = memory.updatedAt; });
    all('[data-current-version]').forEach(node => { node.textContent = `REV ${String(memory.version).padStart(2, '0')}`; });
    all('[data-current-evidence-count]').forEach(node => { node.textContent = `${counts.support} 条依据`; });
    all('[data-current-counter-count]').forEach(node => { node.textContent = `${counts.counter} 条反例`; });
    all('[data-memory-select]').forEach(button => {
      const selected = button.dataset.memorySelect === memory.id;
      button.classList.toggle('is-active', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
    renderEvidence(memory);
    document.dispatchEvent(new CustomEvent('memento:memory-rendered', { detail: { memory, counts } }));
  }

  function renderMemoryPicker() {
    const picker = document.querySelector('[data-memory-picker]');
    if (!picker) return;
    picker.innerHTML = data.understandings.filter(item => !state.hiddenMemoryIds.has(item.id)).map(item => `
      <button type="button" data-memory-picker-select="${escapeHtml(item.id)}">
        <strong>${escapeHtml(item.group)}</strong>
        <small>${escapeHtml(statementFor(item))}</small>
      </button>`).join('');
    all('[data-memory-picker-select]', picker).forEach(button => {
      button.addEventListener('click', () => {
        state.activeMemoryId = button.dataset.memoryPickerSelect;
        renderMemory();
        closeDialog(document.getElementById('memories-dialog'));
      });
    });
  }

  function renderRecords(filter = '全部') {
    const list = document.querySelector('[data-record-list]');
    if (!list) return;
    const visible = filter === '全部' ? data.records : data.records.filter(item => item.tag === filter);
    list.innerHTML = visible.map(item => `
      <li class="record-row">
        <time class="record-time">${escapeHtml(item.time)}</time>
        <div class="record-source">${escapeHtml(item.source)}<br><span class="record-tag"># ${escapeHtml(item.tag)}</span></div>
        <p class="record-copy">${escapeHtml(item.text)}</p>
      </li>`).join('');
  }

  function renderHeat() {
    all('[data-record-trace]').forEach(trace => {
      trace.setAttribute('role', 'img');
      trace.setAttribute('aria-label', '最近 90 天每日记录数量。颜色越深表示当天保留的记录越多，不表示情绪或理解强度。');
      trace.innerHTML = data.heat.map((level, index) => `
        <span class="trace-cell" data-level="${level}" aria-hidden="true"
              data-day-index="${index + 1}"></span>`).join('');
    });
  }

  function renderStatus() {
    const map = {
      no_change: ['本次核对没有更新', '保留上一版理解'],
      processing: ['正在核对近期记录', '完成前继续保留当前理解'],
      updated: ['理解更新通知', '仅演示通知，当前理解数据未改写'],
      error: ['本次核对没有完成', '演示状态：旧版理解继续保留'],
      empty: ['还没有可保留的理解', '演示状态：原始记录仍然存在']
    };
    const [title, detail] = map[state.runStatus] || map.no_change;
    all('[data-run-status-title]').forEach(node => { node.textContent = title; });
    all('[data-run-status-detail]').forEach(node => { node.textContent = detail; });
    document.body.dataset.runStatus = state.runStatus;
    renderMemory();
  }

  function wireDialogs() {
    all('[data-dialog-open]').forEach(button => {
      button.addEventListener('click', () => {
        const dialog = document.getElementById(button.dataset.dialogOpen);
        const parentDialog = button.closest('dialog');
        if (button.dataset.dialogOpen === 'memories-dialog') renderMemoryPicker();
        if (button.dataset.dialogOpen === 'edit-dialog') {
          const textarea = dialog?.querySelector('textarea');
          if (textarea) textarea.value = statementFor(activeMemory());
        }
        if (parentDialog?.open && parentDialog !== dialog) parentDialog.close();
        openDialog(dialog);
      });
    });
    all('[data-dialog-close]').forEach(button => {
      button.addEventListener('click', () => closeDialog(button.closest('dialog')));
    });
    all('dialog').forEach(dialog => {
      dialog.addEventListener('click', event => {
        if (event.target === dialog) closeDialog(dialog);
      });
      dialog.addEventListener('close', () => {
        all('[data-delete-memory]', dialog).forEach(button => {
          delete button.dataset.armed;
          button.textContent = button.dataset.defaultLabel || '删除理解';
        });
      });
    });
  }

  function wireControls() {
    all('[data-memory-select]').forEach(button => {
      button.addEventListener('click', () => {
        state.activeMemoryId = button.dataset.memorySelect;
        renderMemory();
      });
    });

    all('[data-record-filter]').forEach(button => {
      button.addEventListener('click', () => {
        all('[data-record-filter]').forEach(peer => {
          const selected = peer === button;
          peer.classList.toggle('is-active', selected);
          peer.setAttribute('aria-pressed', String(selected));
        });
        renderRecords(button.dataset.recordFilter);
      });
    });

    all('[data-run-now]').forEach(button => {
      button.addEventListener('click', () => {
        if (state.runStatus === 'processing') return;
        state.runStatus = 'processing';
        renderStatus();
        all('[data-run-now]').forEach(peer => { peer.disabled = true; });
        window.setTimeout(() => {
          state.runStatus = 'no_change';
          renderStatus();
          all('[data-run-now]').forEach(peer => { peer.disabled = false; });
          showToast('演示完成：没有实质变化，保留当前理解。');
        }, 1200);
      });
    });

    all('[data-schedule-toggle]').forEach(input => {
      input.checked = state.scheduleEnabled;
      input.addEventListener('change', () => {
        state.scheduleEnabled = input.checked;
        all('[data-schedule-label]').forEach(node => {
          node.textContent = state.scheduleEnabled
            ? '21:00 计划已保存 · 尚未验证自动成功'
            : '自动整理已关闭';
        });
        showToast(state.scheduleEnabled ? '已在当前页面模拟开启计划。' : '已在当前页面模拟关闭计划。');
      });
    });

    const form = document.querySelector('[data-edit-form]');
    form?.addEventListener('submit', event => {
      event.preventDefault();
      const memory = activeMemory();
      const textarea = form.querySelector('textarea');
      const next = textarea?.value.trim();
      if (!memory || !next) return;
      state.editedStatements.set(memory.id, next);
      renderMemory();
      closeDialog(document.getElementById('edit-dialog'));
      showToast('修改已在当前页面模拟保存。');
    });

    all('[data-delete-memory]').forEach(button => {
      button.dataset.defaultLabel = button.textContent.trim();
      button.addEventListener('click', () => {
        const memory = activeMemory();
        if (!memory) return;
        if (button.dataset.armed !== 'true') {
          button.dataset.armed = 'true';
          button.textContent = '再次点击确认删除';
          return;
        }
        state.hiddenMemoryIds.add(memory.id);
        delete button.dataset.armed;
        button.textContent = button.dataset.defaultLabel;
        if (!activeMemory()) {
          state.runStatus = 'empty';
          renderStatus();
        } else {
          renderMemory();
        }
        closeDialog(button.closest('dialog'));
        showToast('这条理解已在当前页面模拟隐藏，原始记录仍保留。');
      });
    });

    all('[data-state-demo]').forEach(button => {
      button.addEventListener('click', () => {
        state.runStatus = button.dataset.stateDemo;
        renderStatus();
      });
    });

    all('[data-export-simulate]').forEach(button => {
      button.addEventListener('click', () => showToast('离线 Demo 未访问剪贴板或写入文件。'));
    });
  }

  function init() {
    all('[data-demo-flag]').forEach(node => { node.textContent = data.meta.snapshot; });
    all('[data-date]').forEach(node => { node.textContent = data.meta.date; });
    all('[data-weekday]').forEach(node => { node.textContent = data.meta.weekday; });
    all('[data-record-count]').forEach(node => { node.textContent = String(data.meta.recordCount); });
    all('[data-schedule-label]').forEach(node => { node.textContent = '21:00 计划已保存 · 尚未验证自动成功'; });
    all('[data-record-filter]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.classList.contains('is-active')));
    });
    renderHeat();
    renderRecords();
    renderMemory();
    renderStatus();
    wireDialogs();
    wireControls();
  }

  window.MementoDemo = Object.freeze({ init, renderMemory, showToast, data, state });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
}());
