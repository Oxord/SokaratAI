/* B2B-кабинет: групповая аналитика по сессиям для вузов/карьерных центров/HR.
   Отдельная страница вне SPA-роутера app.js — просит войти (переиспользует cookie-сессию
   обычного логина) и, если логин есть в SOKRAT_ADMIN_USERS на сервере, показывает дашборд
   по /api/admin/analytics и /api/admin/stats. Текст — только через textContent. */
(() => {
  'use strict';

  const root = document.getElementById('app');
  const toastEl = document.getElementById('toast');

  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, String(v));
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  function toast(message, isError) {
    toastEl.textContent = message;
    toastEl.className = 'toast' + (isError ? ' err' : '');
    toastEl.hidden = false;
    setTimeout(() => { toastEl.hidden = true; }, isError ? 6500 : 3500);
  }

  async function api(path, opts) {
    const res = await fetch(path, {
      credentials: 'same-origin',
      headers: opts && opts.body ? { 'Content-Type': 'application/json' } : undefined,
      ...opts,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* пустой ответ */ }
    if (!res.ok) {
      const err = new Error((data && data.error) || `Ошибка ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function loading(text) {
    root.replaceChildren(h('div', { class: 'loading-screen' },
      h('div', {}, h('span', { class: 'spinner' }),
        h('p', { class: 'muted', style: 'margin-top:12px' }, text || 'Загрузка…'))));
  }

  // ── форма входа ──────────────────────────────────────────────────────────
  function loginPanel(message) {
    const username = h('input', { placeholder: 'Логин', autocomplete: 'username', maxlength: 80 });
    const password = h('input', { type: 'password', placeholder: 'Пароль', autocomplete: 'current-password', maxlength: 200 });
    const btn = h('button', { type: 'button', class: 'btn btn-primary' }, 'Войти');
    const onSubmit = async () => {
      if (!username.value || !password.value) { toast('Заполни логин и пароль.', true); return; }
      btn.disabled = true;
      try {
        await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ username: username.value, password: password.value }) });
        boot();
      } catch (e) { toast(e.message, true); btn.disabled = false; }
    };
    btn.addEventListener('click', onSubmit);
    [username, password].forEach((inp) => inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') onSubmit(); }));
    return h('div', { class: 'narrow', style: 'max-width:380px;margin:40px auto' },
      h('div', { class: 'panel stack' },
        h('h2', { style: 'margin:0' }, 'B2B-кабинет'),
        message ? h('p', { class: 'muted', style: 'margin:0' }, message) : null,
        username, password, h('div', {}, btn)));
  }

  function deniedPanel(user) {
    return h('div', { class: 'narrow', style: 'max-width:480px;margin:40px auto' },
      h('div', { class: 'panel stack' },
        h('h2', { style: 'margin:0' }, 'B2B-кабинет'),
        h('p', { class: 'muted', style: 'margin:0' },
          `Аккаунт «${user}» вошёл, но не имеет доступа к этому кабинету.`),
        h('p', { class: 'muted', style: 'margin:0' },
          'Добавь логин в SOKRAT_ADMIN_USERS в .env на сервере и перезапусти контейнер.')));
  }

  // ── виджеты дашборда ─────────────────────────────────────────────────────
  function statCard(label, value, hint) {
    return h('div', { class: 'card' },
      h('div', { class: 'muted', style: 'font-size:.85rem' }, label),
      h('div', { style: 'font-size:1.7rem;font-weight:800;margin-top:4px' }, value),
      hint ? h('div', { class: 'muted', style: 'font-size:.82rem;margin-top:2px' }, hint) : null);
  }

  function barRow(label, count, total, suffix) {
    const pct = total ? Math.round((100 * count) / total) : 0;
    return h('div', { style: 'display:flex;align-items:center;gap:10px;margin:8px 0' },
      h('div', { style: 'min-width:150px' }, label),
      h('div', { class: 'bar' }, h('i', { style: `width:${pct}%` })),
      h('div', { class: 'muted', style: 'min-width:60px;text-align:right' }, `${count}${suffix || ''} (${pct}%)`));
  }

  function tagList(items, cls) {
    if (!items || !items.length) return h('p', { class: 'muted', style: 'margin:0' }, 'Нет данных.');
    return h('div', {}, items.map(([name, count]) => h('span', { class: `tag ${cls || ''}` }, `${name} · ${count}`)));
  }

  function roleOptions(roles, current) {
    const sel = h('select', {}, h('option', { value: '' }, 'Все роли'));
    for (const [name] of roles || []) {
      const opt = h('option', { value: name }, name);
      if (name === current) opt.setAttribute('selected', '');
      sel.append(opt);
    }
    return sel;
  }

  async function renderDashboard(role) {
    loading('Собираю аналитику…');
    // Ошибки (401/404) намеренно не глушатся здесь — их разбирает boot().
    const [data, stats] = await Promise.all([
      api(`/api/admin/analytics${role ? `?role=${encodeURIComponent(role)}` : ''}`),
      api('/api/admin/stats'),
    ]);

    const sel = roleOptions(data.roles, role || '');
    sel.addEventListener('change', () => { renderDashboard(sel.value).catch((e) => toast(e.message, true)); });

    const header = h('div', { class: 'stack' },
      h('div', { style: 'display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px' },
        h('h1', { style: 'margin:0' }, 'B2B-кабинет'),
        h('div', {}, sel)),
      h('p', { class: 'muted', style: 'margin:0' },
        'Групповая аналитика по всем сессиям. Без ответов и персональных данных пользователей — только агрегаты.'));

    if (!data.sessions) {
      root.replaceChildren(header, h('p', { class: 'muted' }, 'Пока нет завершённых сессий для выбранного фильтра.'));
      return;
    }

    const cards = h('div', { class: 'grid-3' },
      statCard('Сессий', data.sessions, data.unique_users ? `${data.unique_users} уникальных пользователей` : null),
      statCard('Средний результат', `${data.avg_percentage}%`, `медиана ${data.median_percentage}%`),
      statCard('Индекс готовности', data.avg_readiness != null ? `${data.avg_readiness}/100` : '—',
        data.avg_star_pct != null ? `STAR-полнота: ${data.avg_star_pct}%` : null));

    const distTotal = Object.values(data.distribution).reduce((a, b) => a + b, 0);
    const distPanel = h('div', { class: 'panel stack' },
      h('h3', { style: 'margin:0' }, 'Распределение результатов'),
      ...Object.entries(data.distribution).map(([label, count]) => barRow(label, count, distTotal)));

    const dims = Object.entries(data.dimensions || {});
    const dimsPanel = dims.length ? h('div', { class: 'panel stack' },
      h('h3', { style: 'margin:0' }, 'Средний профиль навыков общения (0–10)'),
      ...dims.sort((a, b) => a[1] - b[1]).map(([label, value]) => barRow(label, value, 10, '/10'))) : null;

    const skillsPanel = h('div', { class: 'panel stack' },
      h('h3', { style: 'margin:0' }, 'Навыки: где чаще всего проседают'),
      tagList(data.weak_skills, 'bad'),
      h('h3', { style: 'margin:16px 0 0' }, 'Требуемые навыки, которые не раскрыли'),
      tagList(data.unseen_skills, 'warn'));

    const rolesPanel = h('div', { class: 'panel stack' },
      h('h3', { style: 'margin:0' }, 'Популярные роли'),
      h('table', { class: 'table' },
        h('tbody', {}, (data.roles || []).map(([name, count]) =>
          h('tr', {}, h('td', {}, name), h('td', {}, String(count)))))));

    const routes = Object.entries((stats.metrics && stats.metrics.routes) || {});
    const techPanel = h('details', { class: 'panel' },
      h('summary', {}, '⚙️ Технические метрики пилота'),
      h('div', { class: 'stack', style: 'margin-top:12px' },
        h('p', { class: 'muted', style: 'margin:0' },
          `Аптайм: ${Math.round((stats.metrics.uptime_s || 0) / 60)} мин · ` +
          `Отзывы: ${stats.feedback.count}` +
          (stats.feedback.avg_rating != null ? ` · средняя оценка ${stats.feedback.avg_rating}/5` : '') +
          (stats.feedback.avg_realism != null ? ` · реалистичность ${stats.feedback.avg_realism}/5` : '')),
        routes.length ? h('table', { class: 'table' },
          h('thead', {}, h('tr', {}, h('th', {}, 'Эндпоинт'), h('th', {}, 'Запросов'),
            h('th', {}, '4xx'), h('th', {}, '5xx'), h('th', {}, 'Ср. время'))),
          h('tbody', {}, routes.map(([name, r]) =>
            h('tr', {}, h('td', {}, name), h('td', {}, String(r.count)),
              h('td', {}, String(r['4xx'])), h('td', {}, String(r['5xx'])), h('td', {}, `${r.avg_s}s`))))
        ) : h('p', { class: 'muted', style: 'margin:0' }, 'Запросов пока не было.')));

    root.replaceChildren(header, cards, distPanel, dimsPanel, skillsPanel, rolesPanel, techPanel);
  }

  async function boot() {
    let config;
    try { config = await api('/api/config'); } catch (e) {
      root.replaceChildren(h('p', { class: 'muted' }, 'Сервер недоступен.'));
      return;
    }
    if (!config.user) {
      root.replaceChildren(loginPanel());
      return;
    }
    try {
      await renderDashboard();
    } catch (e) {
      if (e.status === 404) { root.replaceChildren(deniedPanel(config.user)); return; }
      if (e.status === 401) { root.replaceChildren(loginPanel('Сессия истекла, войди снова.')); return; }
      toast(e.message, true);
    }
  }

  boot();
})();
