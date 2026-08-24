// Memento · 归档预览安全化
// 预览层只保留静态 HTML/CSS、原生 details/summary 和页内锚点。
// 任意脚本、刷新/基地址、嵌入内容与外部 URL 一律移除。

(function exposeArchiveSanitizer(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MementoArchiveSanitizer = api;
})(typeof window !== 'undefined' ? window : globalThis, function createArchiveSanitizer() {
  'use strict';

  const REMOVE_ELEMENTS = [
    'script', 'base', 'link', 'iframe', 'frame', 'frameset', 'object', 'embed',
    'applet', 'portal', 'fencedframe', 'webview',
    // SVG SMIL 可在无 JS 时改写 href/src 等属性，同样禁用。
    'animate', 'animatemotion', 'animatetransform', 'set', 'discard',
  ];
  const URL_ATTRIBUTES = new Set([
    'href', 'xlink:href', 'src', 'srcset', 'action', 'formaction', 'poster',
    'background', 'cite', 'data', 'codebase', 'longdesc', 'manifest', 'ping',
    'profile', 'usemap', 'icon',
  ]);
  const SAFE_DATA_MEDIA_RE = /^data:(?:image\/(?:png|jpeg|gif|webp|avif)|audio\/(?:mpeg|mp4|ogg|wav)|video\/(?:mp4|webm|ogg));base64,[a-z0-9+/=\s]+$/i;

  function removeNode(node) {
    if (node && node.parentNode) node.parentNode.removeChild(node);
  }

  function isSafeUrlAttribute(element, attributeName, value) {
    const normalized = String(value || '').trim();
    if ((attributeName === 'href' || attributeName === 'xlink:href') && normalized.startsWith('#')) return true;
    if (attributeName === 'src' && ['IMG', 'AUDIO', 'VIDEO', 'SOURCE'].includes(element.tagName)) {
      return SAFE_DATA_MEDIA_RE.test(normalized);
    }
    return false;
  }

  function sanitizeRoot(root) {
    for (const selector of REMOVE_ELEMENTS) {
      root.querySelectorAll(selector).forEach(removeNode);
    }

    root.querySelectorAll('meta[http-equiv]').forEach(removeNode);

    root.querySelectorAll('*').forEach(element => {
      for (const attribute of [...element.attributes]) {
        const name = attribute.name.toLowerCase();
        if (name.startsWith('on') || name === 'srcdoc' || name === 'target' || name === 'download') {
          element.removeAttribute(attribute.name);
          continue;
        }
        if (URL_ATTRIBUTES.has(name) && !isSafeUrlAttribute(element, name, attribute.value)) {
          element.removeAttribute(attribute.name);
        }
      }

      // template.content 是独立 DocumentFragment，普通 querySelectorAll 不会递归进入。
      if (element.tagName === 'TEMPLATE' && element.content) sanitizeRoot(element.content);
    });
  }

  function sanitizeArchiveDocument(html, Parser = globalThis.DOMParser) {
    if (typeof Parser !== 'function') throw new Error('当前环境不支持 DOMParser');
    const document = new Parser().parseFromString(String(html || ''), 'text/html');
    sanitizeRoot(document);
    return document;
  }

  return {
    isSafeUrlAttribute,
    sanitizeArchiveDocument,
  };
});
