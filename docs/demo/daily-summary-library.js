// Memento · 每日总结纯数据层
// 负责解析 Reviews/Daily/*.md，并按每日文件名把当天第一帧、总结和处理状态配成日卡。

(function exposeDailySummaryLibrary(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MementoDailySummaries = api;
})(typeof window !== 'undefined' ? window : globalThis, function createDailySummaryLibrary() {
  'use strict';

  const REVIEW_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
  const REVIEW_STATE_VALUES = new Set(['pending', 'success', 'failed']);
  const REQUIRED_FRONTMATTER_FIELDS = [
    'date',
    'type',
    'period',
    'source',
    'source_hash',
    'source_mock',
    'prompt',
    'prompt_hash',
    'generated_at',
  ];
  const REQUIRED_SECTIONS = [
    { title: '工作与生活现场', key: 'scene' },
    { title: '行动线索', key: 'actionClues' },
    { title: '灵感与想法', key: 'insights' },
    { title: '个人记录/情绪', key: 'personal' },
    { title: '已忽略', key: 'ignored' },
    { title: '来源索引', key: 'sources' },
    { title: '我的补充', key: 'supplement' },
  ];
  const REQUIRED_SECTION_HEADINGS = REQUIRED_SECTIONS.map(section => `## ${section.title}`);
  const REQUIRED_SECTION_HEADING_SET = new Set(REQUIRED_SECTION_HEADINGS);
  const SECTION_ALIASES = new Map([
    ['工作与生活现场', 'scene'],
    ['工作事项', 'scene'],
    ['行动线索', 'actionClues'],
    ['TODO 清单', 'actionClues'],
    ['灵感与想法', 'insights'],
    ['个人记录/情绪', 'personal'],
    ['已忽略', 'ignored'],
    ['来源索引', 'sources'],
    ['我的补充', 'supplement'],
  ]);

  function normalizeNewlines(text) {
    return String(text || '').replace(/\r\n?/g, '\n');
  }

  function unquote(value) {
    const text = String(value || '').trim();
    if (text.length >= 2 && ((text[0] === '"' && text.at(-1) === '"') || (text[0] === "'" && text.at(-1) === "'"))) {
      return text.slice(1, -1);
    }
    return text;
  }

  function isValidCalendarDate(value) {
    const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) return false;
    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    if (year < 1 || month < 1 || month > 12 || day < 1) return false;
    const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    return day <= days[month - 1];
  }

  function isValidGeneratedAt(value) {
    const match = String(value || '').match(
      /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})\+08:00$/
    );
    if (!match || !isValidCalendarDate(match[1])) return false;
    return Number(match[2]) <= 23 && Number(match[3]) <= 59 && Number(match[4]) <= 59;
  }

  function parseFrontmatter(text) {
    const original = String(text || '');
    const normalized = normalizeNewlines(original);
    const lines = normalized.split('\n');
    const issues = [];
    const meta = {};
    const rawMeta = {};
    const counts = {};

    if (original.includes('\r')) issues.push('总结必须使用标准换行格式');
    if (lines[0] !== '---') {
      issues.push('缺少总结元数据');
      return { meta, rawMeta, counts, body: normalized, issues, issue: issues[0] };
    }

    const closingLine = lines.indexOf('---', 1);
    if (closingLine < 0) {
      issues.push('总结元数据未闭合');
      return { meta, rawMeta, counts, body: '', issues, issue: issues[0] };
    }

    for (const line of lines.slice(1, closingLine)) {
      const separator = line.indexOf(':');
      if (separator < 0) {
        issues.push(`总结元数据含无效行: ${line}`);
        continue;
      }
      const key = line.slice(0, separator);
      const rawValue = line.slice(separator + 1).replace(/^\s*/, '');
      counts[key] = (counts[key] || 0) + 1;
      if (counts[key] > 1) issues.push(`总结元数据字段重复: ${key}`);
      rawMeta[key] = rawValue;
      meta[key] = unquote(rawValue);
    }

    return {
      meta,
      rawMeta,
      counts,
      body: lines.slice(closingLine + 1).join('\n'),
      issues,
      issue: issues[0] || '',
    };
  }

  function parseSections(body) {
    const sections = {
      scene: '',
      actionClues: '',
      insights: '',
      personal: '',
      ignored: '',
      sources: '',
      supplement: '',
    };
    const unknownSections = [];
    let currentKey = null;
    let currentTitle = '';
    let buffer = [];

    const flush = () => {
      if (!currentTitle) return;
      const content = buffer.join('\n').trim();
      if (currentKey) {
        sections[currentKey] = [sections[currentKey], content].filter(Boolean).join('\n\n');
      } else if (content) {
        unknownSections.push({ title: currentTitle, content });
      }
    };

    let inFence = false;
    normalizeNewlines(body).split('\n').forEach(line => {
      if (/^\s*(?:```|~~~)/.test(line)) {
        if (currentTitle) buffer.push(line);
        inFence = !inFence;
        return;
      }

      if (inFence) {
        if (currentTitle) buffer.push(line);
        return;
      }

      const heading = line.match(/^##\s+(.+?)\s*$/);
      if (heading) {
        const title = heading[1].trim();
        // 「我的补充」属于用户。进入补充区后，剩余正文一律保持 opaque tail；
        // 固定章节的重复仍由独立合同校验器报告，但展示层不得吞掉或重排内容。
        if (currentKey === 'supplement') {
          buffer.push(line);
          return;
        }
        flush();
        currentTitle = title;
        currentKey = SECTION_ALIASES.get(currentTitle) || null;
        buffer = [];
        return;
      }
      if (currentTitle) buffer.push(line);
    });
    flush();

    return { sections, unknownSections };
  }

  function validateFrontmatter(parsed, fileDate) {
    const issues = [...(parsed.issues || [])];
    const allowed = new Set(REQUIRED_FRONTMATTER_FIELDS);
    Object.keys(parsed.counts || {}).forEach(key => {
      if (!allowed.has(key)) issues.push(`总结元数据含未知字段: ${key}`);
    });
    REQUIRED_FRONTMATTER_FIELDS.forEach(key => {
      if ((parsed.counts && parsed.counts[key]) !== 1) issues.push(`总结元数据缺少字段: ${key}`);
    });

    const raw = parsed.rawMeta || {};
    if (raw.date !== fileDate) issues.push('总结日期与文件名不一致');
    if (raw.type !== 'memento-review') issues.push('总结类型不受支持');
    if (raw.period !== 'daily') issues.push('总结周期不受支持');
    if (raw.source !== `"[[${fileDate}]]"`) issues.push('总结来源必须精确指向当天记录');
    if (!/^"[a-f0-9]{64}"$/.test(raw.source_hash || '')) issues.push('总结来源哈希格式无效');
    if (raw.source_mock !== 'true' && raw.source_mock !== 'false') issues.push('总结模拟来源标记无效');
    if (raw.prompt !== 'memento-comprehensive') issues.push('总结 Prompt 标识不受支持');
    if (!/^"[a-f0-9]{64}"$/.test(raw.prompt_hash || '')) issues.push('总结 Prompt 哈希格式无效');
    if (!isValidGeneratedAt(raw.generated_at)) issues.push('总结生成时间格式或日期无效');
    return issues;
  }

  function validateBody(body, fileDate) {
    const issues = [];
    const lines = normalizeNewlines(body).split('\n');
    let bodyStarted = false;
    let inFence = false;
    let sectionIndex = 0;
    let currentSection = 0;
    let h1Count = 0;
    let sourceIndexLink = false;
    const sectionHasContent = Array(REQUIRED_SECTIONS.length + 1).fill(false);

    for (const line of lines) {
      if (!bodyStarted && !/\S/.test(line)) continue;
      if (!bodyStarted) {
        if (line !== `# Daily Review · ${fileDate}`) issues.push('缺少或错误的 Daily Review 一级标题');
        else h1Count++;
        bodyStarted = true;
        continue;
      }

      if (/^\s*(?:```|~~~)/.test(line)) {
        inFence = !inFence;
        if (currentSection > 0) sectionHasContent[currentSection] = true;
        continue;
      }
      if (!inFence && /^# /.test(line)) {
        h1Count++;
        issues.push('不允许额外或重复的一级标题');
        continue;
      }
      if (!inFence && /^## /.test(line)) {
        if (sectionIndex < REQUIRED_SECTION_HEADINGS.length) {
          const expected = REQUIRED_SECTION_HEADINGS[sectionIndex];
          if (line !== expected) {
            issues.push(`总结章节缺失或顺序错误，期望: ${expected}`);
            // 保持原索引，后续不能因一个错误标题而误报完整合同。
            continue;
          }
          sectionIndex++;
          currentSection = sectionIndex;
          continue;
        }
        if (REQUIRED_SECTION_HEADING_SET.has(line)) issues.push(`我的补充中不允许重复固定章节: ${line}`);
        sectionHasContent[currentSection] = true;
        continue;
      }
      if (currentSection === 0) {
        if (/\S/.test(line)) issues.push('一级标题与首个章节之间存在未归组内容');
        continue;
      }
      if (/\S/.test(line)) sectionHasContent[currentSection] = true;
      if (currentSection === 6 && line === `- [[${fileDate}]]`) sourceIndexLink = true;
    }

    if (h1Count !== 1) issues.push('一级标题数量错误');
    if (sectionIndex !== REQUIRED_SECTIONS.length) issues.push('必需总结章节不完整');
    for (let index = 1; index <= REQUIRED_SECTIONS.length; index++) {
      if (!sectionHasContent[index]) issues.push(`总结章节不能为空: ${REQUIRED_SECTION_HEADINGS[index - 1]}`);
    }
    if (!sourceIndexLink) issues.push(`来源索引缺少精确链接: [[${fileDate}]]`);
    return issues;
  }

  function sourceDateFrom(value) {
    const match = String(value || '').match(/\[\[(\d{4}-\d{2}-\d{2})\]\]/);
    return match ? match[1] : '';
  }

  function parseReviewFile(file) {
    const fileDate = file && isValidCalendarDate(file.date || '') ? file.date : '';
    const issues = [];
    if (!fileDate) issues.push('总结文件名日期无效');
    if (file && file.readIssue) issues.push(file.readIssue);

    const parsed = parseFrontmatter(file && file.text);
    issues.push(...validateFrontmatter(parsed, fileDate));
    issues.push(...validateBody(parsed.body, fileDate));
    const { meta } = parsed;
    const parsedSections = parseSections(parsed.body);
    const declaredDate = meta.date || '';
    const sourceDate = sourceDateFrom(meta.source);

    return {
      id: `${fileDate || 'unknown'}#review`,
      fileDate,
      date: declaredDate || fileDate,
      type: meta.type || '',
      period: meta.period || '',
      sourceDate,
      sourceHash: meta.source_hash || '',
      promptHash: meta.prompt_hash || '',
      generatedAt: meta.generated_at || '',
      prompt: meta.prompt || '',
      sourceMock: meta.source_mock === 'true',
      sourceMockValue: meta.source_mock === 'true' || meta.source_mock === 'false'
        ? meta.source_mock === 'true'
        : null,
      sections: parsedSections.sections,
      unknownSections: parsedSections.unknownSections,
      issues,
      mtime: Number(file && file.mtime) || 0,
    };
  }

  function collectReviewRecords(files) {
    return (files || [])
      .map(parseReviewFile)
      .filter(record => record.fileDate)
      .sort((a, b) => b.fileDate.localeCompare(a.fileDate) || b.mtime - a.mtime);
  }

  function parseReviewStateFile(file) {
    const fileDate = file && REVIEW_DATE_RE.test(file.date || '') ? file.date : '';
    const issues = [];
    let payload = {};
    if (!fileDate) issues.push('总结状态文件名日期无效');
    if (file && file.readIssue) issues.push(file.readIssue);

    try {
      const parsed = JSON.parse(String(file && file.text || '{}'));
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) payload = parsed;
      else issues.push('总结状态文件格式无效');
    } catch {
      issues.push('总结状态文件无法解析');
    }

    const declaredDate = REVIEW_DATE_RE.test(payload.date || '') ? payload.date : '';
    const status = REVIEW_STATE_VALUES.has(payload.status) ? payload.status : '';
    if (payload.date && !declaredDate) issues.push('总结状态日期无效');
    if (declaredDate && fileDate && declaredDate !== fileDate) issues.push('总结状态日期与文件名不一致');
    if (payload.status && !status) issues.push('总结状态不受支持');

    return {
      fileDate,
      date: declaredDate || fileDate,
      status,
      updatedAt: String(payload.updated_at || payload.updatedAt || ''),
      message: String(payload.message || ''),
      issues,
      mtime: Number(file && file.mtime) || 0,
    };
  }

  function collectReviewStates(files) {
    return Object.fromEntries((files || [])
      .map(parseReviewStateFile)
      .filter(record => record.fileDate)
      .sort((a, b) => a.fileDate.localeCompare(b.fileDate) || a.mtime - b.mtime)
      .map(record => [record.fileDate, record]));
  }

  function reviewFreshness(review, sourceHash, promptHash, sourceMock) {
    if (!review) return 'missing';
    if (!review.sourceHash || !sourceHash) return 'unknown';
    if (review.sourceHash !== sourceHash) return 'stale';
    if (!review.promptHash || !promptHash || typeof sourceMock !== 'boolean') return 'unknown';
    if (review.promptHash !== promptHash || review.sourceMockValue !== sourceMock) return 'outdated';
    return 'current';
  }

  function summaryStatus(review, freshness, reviewState) {
    if (reviewState && reviewState.status === 'failed') return 'failed';
    if (reviewState && reviewState.status === 'pending') return 'pending';
    if (!review) return 'pending';
    if (freshness === 'stale') return 'stale';
    if (freshness === 'current' && (!review.issues || review.issues.length === 0)) return 'current';
    return 'rebuild';
  }

  function buildDayCards(snapshotRecords, reviewRecords, sourceHashes = {}, reviewStates = {}, validation = {}) {
    sourceHashes = sourceHashes || {};
    reviewStates = reviewStates || {};
    validation = validation || {};
    const sourceMocks = validation.sourceMocks || {};
    const promptHash = validation.promptHash || '';
    const promptIssue = validation.promptIssue || '';
    const days = new Map();
    const ensureDay = dayKey => {
      if (!days.has(dayKey)) days.set(dayKey, { dayKey, photos: [], reviews: [] });
      return days.get(dayKey);
    };

    (snapshotRecords || []).forEach(photo => {
      const dayKey = photo && REVIEW_DATE_RE.test(photo.fileDate || '') ? photo.fileDate : '';
      if (dayKey) ensureDay(dayKey).photos.push(photo);
    });
    (reviewRecords || []).forEach(review => {
      const dayKey = review && REVIEW_DATE_RE.test(review.fileDate || '') ? review.fileDate : '';
      if (dayKey) ensureDay(dayKey).reviews.push(review);
    });
    Object.keys(sourceHashes || {}).forEach(dayKey => {
      if (REVIEW_DATE_RE.test(dayKey)) ensureDay(dayKey);
    });

    return [...days.values()].map(day => {
      day.photos.sort((a, b) => String(a.sortKey || '').localeCompare(String(b.sortKey || '')));
      day.reviews.sort((a, b) => b.mtime - a.mtime);
      const photo = day.photos.find(item => item.assetName) || day.photos[0] || null;
      const review = day.reviews[0] || null;
      const reviewState = reviewStates[day.dayKey] || null;
      const expectedSourceMock = Object.prototype.hasOwnProperty.call(sourceMocks, day.dayKey)
        ? sourceMocks[day.dayKey]
        : undefined;
      const freshness = reviewFreshness(review, sourceHashes[day.dayKey], promptHash, expectedSourceMock);
      const contractIssues = [];
      if (review) {
        if (promptIssue) contractIssues.push(promptIssue);
        else if (!promptHash) contractIssues.push('当前总结 Prompt 无法校验');
        else if (review.promptHash && review.promptHash !== promptHash) contractIssues.push('总结 Prompt 已更新');
        if (!sourceHashes[day.dayKey]) contractIssues.push('原始记录哈希暂时无法校验');
        if (typeof expectedSourceMock !== 'boolean') contractIssues.push('原始记录来源类型暂时无法校验');
        else if (review.sourceMockValue !== null && review.sourceMockValue !== expectedSourceMock) {
          contractIssues.push('总结模拟来源标记与原始记录不一致');
        }
      }
      const issues = [
        ...(photo ? photo.issues : []),
        ...(review ? review.issues : []),
        ...contractIssues,
        ...(reviewState && Array.isArray(reviewState.issues) ? reviewState.issues : []),
      ];
      if (day.photos.length > 1) issues.push(`同一天检测到 ${day.photos.length} 张开场照片`);
      if (day.reviews.length > 1) issues.push(`同一天检测到 ${day.reviews.length} 份总结`);

      return {
        id: `day:${day.dayKey}`,
        dayKey: day.dayKey,
        photo,
        photos: day.photos,
        additionalPhotoCount: Math.max(0, day.photos.length - 1),
        review,
        reviewState,
        freshness,
        summaryStatus: summaryStatus(review, freshness, reviewState),
        contractIssues,
        issues,
        sortKey: day.dayKey,
      };
    }).sort((a, b) => b.sortKey.localeCompare(a.sortKey));
  }

  function monthKey(dayCard) {
    return /^\d{4}-\d{2}/.test(dayCard && dayCard.dayKey || '') ? dayCard.dayKey.slice(0, 7) : '';
  }

  return {
    buildDayCards,
    collectReviewStates,
    collectReviewRecords,
    monthKey,
    parseFrontmatter,
    parseReviewFile,
    parseReviewStateFile,
    parseSections,
    reviewFreshness,
    summaryStatus,
  };
});
