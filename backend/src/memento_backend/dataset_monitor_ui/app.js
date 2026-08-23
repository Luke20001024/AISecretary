const captureCount = document.querySelector('#capture-count');
const coverageCount = document.querySelector('#coverage-count');
const checklistProgress = document.querySelector('#checklist-progress');
const requirementsRoot = document.querySelector('#requirements');
const promptsRoot = document.querySelector('#content-prompts');
const recordsRoot = document.querySelector('#records');
const emptyState = document.querySelector('#empty-state');
const updatedAt = document.querySelector('#updated-at');
const refreshButton = document.querySelector('#refresh-button');
const exportButton = document.querySelector('#export-button');
const toast = document.querySelector('#toast');

let refreshing = false;
let toastTimer = null;

function showToast(message) {
  toast.textContent = message;
  toast.classList.add('visible');
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove('visible'), 2200);
}

function text(value) {
  return value == null || value === '' ? '—' : String(value);
}

function formatTime(value) {
  if (!value) return '尚未收取';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `更新于 ${date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
}

function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content != null) node.textContent = content;
  return node;
}

function renderRequirements(items) {
  requirementsRoot.replaceChildren();
  items.forEach(item => {
    const row = element('div', `requirement${item.complete ? ' complete' : ''}`);
    row.append(element('span', 'requirement-state', item.complete ? '✓' : ''));
    const copy = element('div');
    copy.append(element('strong', '', item.title));
    copy.append(element('p', '', item.instruction));
    row.append(copy);
    row.append(element('span', 'shortcut', item.shortcut));
    requirementsRoot.append(row);
  });
}

function renderPrompts(items) {
  promptsRoot.replaceChildren();
  items.forEach(item => {
    const row = element('div', 'content-prompt');
    row.append(element('strong', '', item.title));
    row.append(element('span', '', item.instruction));
    promptsRoot.append(row);
  });
}

function renderRecords(items) {
  recordsRoot.replaceChildren();
  emptyState.hidden = items.length > 0;
  items.forEach(item => {
    const card = element('article', 'record');
    const meta = element('div', 'record-meta');
    meta.append(element('span', 'record-type', item.source_type_label));
    meta.append(element('span', '', `${text(item.local_date)} · ${text(item.time)}`));
    meta.append(element('span', '', text(item.source_app)));
    meta.append(element('span', '', `附件 ${item.attachment_count}`));
    card.append(meta);
    card.append(element('p', 'record-preview', item.preview || '原件已保存，当前没有可显示的文字'));
    const signals = element('div', 'record-signals');
    if (item.note) signals.append(element('span', 'signal', `备注：${item.note}`));
    if (item.tag) signals.append(element('span', 'signal', `#${item.tag}`));
    if (!item.note && !item.tag) signals.append(element('span', 'signal neutral', '未附加个人判断'));
    card.append(signals);
    recordsRoot.append(card);
  });
}

function render(monitor) {
  captureCount.textContent = monitor.capture_count;
  coverageCount.textContent = `${monitor.completed_requirement_count}/${monitor.requirement_count}`;
  checklistProgress.textContent = `${monitor.completed_requirement_count} / ${monitor.requirement_count}`;
  updatedAt.textContent = formatTime(monitor.last_collected_at);
  renderRequirements(monitor.requirements);
  renderPrompts(monitor.content_prompts);
  renderRecords(monitor.captures);
}

async function request(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', ...options });
  const value = await response.json();
  if (!response.ok || !value.ok) throw new Error(value?.error?.message || '请求失败');
  return value;
}

async function refresh({ announce = false } = {}) {
  if (refreshing) return;
  refreshing = true;
  refreshButton.disabled = true;
  refreshButton.textContent = '正在收取';
  try {
    const value = await request('/v1/collect', { method: 'POST' });
    render(value.monitor);
    if (announce) {
      const count = value.collection.new_capture_count;
      showToast(count ? `新增 ${count} 条真实记录` : '当前没有遗漏的新记录');
    }
  } catch (error) {
    showToast(`收取失败：${error.message}`);
  } finally {
    refreshing = false;
    refreshButton.disabled = false;
    refreshButton.textContent = '立即收取';
  }
}

refreshButton.addEventListener('click', () => refresh({ announce: true }));
exportButton.addEventListener('click', async () => {
  try {
    const value = await request('/v1/export', { method: 'POST' });
    showToast(`已生成 ${value.dataset.case_count} 条待确认样本`);
  } catch (error) {
    showToast(`生成失败：${error.message}`);
  }
});

refresh();
window.setInterval(refresh, 5000);
