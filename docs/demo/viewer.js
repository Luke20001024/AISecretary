(function initMementoArchiveViewer() {
  'use strict';

  let rendered = false;

  function showRenderError(error) {
    document.body.replaceChildren();
    const message = document.createElement('p');
    message.style.cssText = 'padding:2em;color:#9a3b2e';
    message.textContent = `渲染失败:${error}`;
    document.body.appendChild(message);
  }

  window.addEventListener('message', event => {
    if (!window.opener || event.source !== window.opener) return;
    const data = event.data;
    if (!data || data.type !== 'memento-html' || rendered) return;
    rendered = true;

    try {
      const safeDocument = window.MementoArchiveSanitizer.sanitizeArchiveDocument(data.html);
      const safeRoot = document.importNode(safeDocument.documentElement, true);
      document.replaceChild(safeRoot, document.documentElement);
    } catch (error) {
      showRenderError(error);
    }
  });

  // 反复向 opener 报“我已就绪”，直到收到 HTML，规避主页监听器尚未挂上的竞态。
  let tries = 0;
  const timer = setInterval(() => {
    if (rendered || tries++ > 40 || !window.opener) {
      clearInterval(timer);
      return;
    }
    window.opener.postMessage({ type: 'memento-viewer-ready' }, '*');
  }, 50);
})();
