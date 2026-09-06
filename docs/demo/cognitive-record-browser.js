// Read-only record browsing. Labels organize source material; they do not promote memory.
(function exposeRecordBrowser(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MementoCognitiveRecordBrowser = api;
})(typeof window !== 'undefined' ? window : globalThis, function createRecordBrowser() {
  'use strict';

  const UNTAGGED = '__untagged__';
  const text = value => typeof value === 'string' ? value.trim() : '';
  const list = value => Array.isArray(value) ? value : [];

  function validDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const date = new Date(`${value}T00:00:00Z`);
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
  }

  function preview(value) {
    const chars = Array.from(text(value).replace(/\s+/gu, ' '));
    return chars.length > 120 ? chars.slice(0, 119).join('') + '…' : chars.join('');
  }

  function normalizeRecords(records) {
    const seen = new Set();
    const normalized = [];
    for (const input of list(records)) {
      if (!input || typeof input !== 'object') continue;
      const id = text(input.id || input.recordId || input.record_id || input.record_ref?.id);
      if (!id || seen.has(id) || ['deleted', 'tombstoned'].includes(input.status)) continue;
      seen.add(id);
      const capturedAt = text(input.capturedAt || input.captured_at);
      // An explicit invalid source date stays undated; never replace it with today's date.
      const date = text(input.localDate ?? input.local_date ?? input.date ?? capturedAt.slice(0, 10));
      const localDate = validDate(date) ? date : '';
      const time = text(input.time || capturedAt.slice(11, 16));
      const manualTag = text(input.tag);
      const tags = [...new Set([
        ...list(input.topics), ...list(input.tags),
        ...(manualTag && manualTag !== '记录' ? [manualTag] : []),
      ].map(text).filter(Boolean))];
      const summary = preview(input.summary || input.text);
      const summaryState = text(input.summaryState || input.summary_state)
        || (input.summary_kind === 'pending' ? 'pending'
          : input.summary_kind === 'resource_preview' ? 'preview'
          : text(input.summary) ? 'ready' : summary ? 'preview' : 'pending');
      normalized.push({
        id, localDate, capturedAt, time: /^\d{2}:\d{2}$/.test(time) ? time : '',
        source: text(input.source || input.source_app || input.source_type) || '本地记录',
        summary, summaryState, tags, status: text(input.status),
        themeTitles: [...new Set(list(input.themeTitles).map(text).filter(Boolean))],
        detailAvailable: input.detailAvailable !== false,
      });
    }
    return normalized;
  }

  function buildView(records, options = {}) {
    const today = text(options.today);
    const days = options.range === '7' ? 7 : options.range === '30' ? 30 : 0;
    const end = validDate(today) ? Date.parse(`${today}T00:00:00Z`) : NaN;
    const scoped = normalizeRecords(records).filter(record => {
      if (!days) return true;
      if (!record.localDate || !Number.isFinite(end)) return false;
      const stamp = Date.parse(`${record.localDate}T00:00:00Z`);
      return stamp <= end && stamp >= end - (days - 1) * 86400000;
    }).sort((a, b) => b.localDate.localeCompare(a.localDate)
      || b.time.localeCompare(a.time) || b.capturedAt.localeCompare(a.capturedAt)
      || a.id.localeCompare(b.id));
    const counts = new Map();
    for (const record of scoped) {
      for (const key of record.tags.length ? record.tags : [UNTAGGED]) {
        counts.set(key, (counts.get(key) || 0) + 1);
      }
    }
    const tags = [...counts].map(([key, count]) => ({
      key, label: key === UNTAGGED ? '未分类' : key, count,
    })).sort((a, b) => (a.key === UNTAGGED ? 1 : b.key === UNTAGGED ? -1
      : b.count - a.count || a.label.localeCompare(b.label, 'zh-CN')));
    const selectedTag = options.mode === 'tags' ? text(options.tag) : '';
    const visible = scoped.filter(record => !selectedTag || (selectedTag === UNTAGGED
      ? !record.tags.length : record.tags.includes(selectedTag)));
    const byDay = new Map();
    for (const record of visible) {
      if (!byDay.has(record.localDate)) byDay.set(record.localDate, []);
      byDay.get(record.localDate).push(record);
    }
    return {
      records: visible,
      groups: [...byDay].map(([date, dayRecords]) => ({date, records: dayRecords})),
      tags, totalCount: scoped.length, visibleCount: visible.length,
      undatedCount: scoped.filter(record => !record.localDate).length,
    };
  }

  return Object.freeze({normalizeRecords, buildView});
});
