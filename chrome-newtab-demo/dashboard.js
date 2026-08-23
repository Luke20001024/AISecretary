(() => {
  'use strict';

  const fixture = window.MementoDemoFixture;
  const byId = (id) => document.getElementById(id);
  const themeMap = byId('theme-map');
  const river = byId('record-river');
  const list = byId('understanding-list');
  const drawer = byId('detail-drawer');

  fixture.records.forEach((record) => {
    const article = document.createElement('article');
    article.className = 'record';
    article.innerHTML = `<time>${record.time}</time><span>${record.source}</span><h3>${record.title}</h3><a href="#map-title">${record.theme} · 进入主题地景</a>`;
    river.append(article);
  });

  fixture.understandings.forEach((item) => {
    const li = document.createElement('li');
    const stateClass = item.state === '已稳定' ? 'stable' : '';
    li.innerHTML = `<span class="understanding-no">${item.no}</span><div><h3>${item.title}</h3><p>${item.links}</p></div><span class="understanding-state ${stateClass}">${item.state}</span>`;
    list.append(li);
  });

  fixture.themes.forEach((theme) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'theme';
    button.style.left = `${theme.x}%`;
    button.style.top = `${theme.y}%`;
    button.setAttribute('aria-label', `查看 ${theme.label} 详情`);
    button.innerHTML = `<span class="contours"></span><span class="summit">▲</span><h3>${theme.label}</h3><p>依据 ${theme.evidence} · 边界 ${theme.edge}</p>`;
    button.addEventListener('click', () => openDrawer(theme));
    themeMap.append(button);
  });

  function openDrawer(theme) {
    byId('drawer-title').textContent = theme.label;
    byId('drawer-copy').textContent = theme.summary;
    byId('drawer-evidence').textContent = `${theme.evidence} 条固定汇总依据`;
    byId('drawer-edge').textContent = `${theme.edge} 条演示性边界提示`;
    drawer.classList.add('open');
    byId('drawer-close').focus();
  }
  function closeDrawer() { drawer.classList.remove('open'); }
  byId('drawer-close').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeDrawer(); });
})();
