/* Сократ — одностраничный интерфейс без сборки. Все пользовательские данные
   выводятся через textContent (h()), innerHTML не используется. */
(() => {
  'use strict';

  // ── утилиты ────────────────────────────────────────────────────────────────
  const root = document.getElementById('app');
  const toastEl = document.getElementById('toast');
  const S = { config: null, user: null, first: {} };   // first: id -> первый вопрос
  let toastTimer = null;

  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
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
  const svgNS = 'http://www.w3.org/2000/svg';
  function s(tag, attrs, ...kids) {
    const el = document.createElementNS(svgNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v));
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined) {
      el.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  function toast(message, isError) {
    toastEl.textContent = message;
    toastEl.className = 'toast' + (isError ? ' err' : '');
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, isError ? 6500 : 3500);
  }

  async function api(path, opts = {}) {
    const init = { method: opts.method || 'GET', credentials: 'same-origin', headers: {} };
    if (opts.json !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.json);
    } else if (opts.form) {
      init.body = opts.form;
    } else if (opts.raw) {
      init.headers['Content-Type'] = 'application/octet-stream';
      init.body = opts.raw;
    }
    let res;
    try {
      res = await fetch(path, init);
    } catch (e) {
      throw new Error('Нет связи с сервером. Проверь подключение.');
    }
    if (opts.blob && res.ok) return res.blob();
    let data = null;
    try { data = await res.json(); } catch (e) { /* не JSON */ }
    if (!res.ok) {
      const err = new Error((data && data.error) || `Ошибка ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function pctClass(p) { return p >= 75 ? 'g' : p >= 50 ? '' : p >= 30 ? 'a' : 'r'; }
  function questionsWord(n) {
    const d = n % 10, dd = n % 100;
    if (d === 1 && dd !== 11) return 'вопрос';
    if (d >= 2 && d <= 4 && (dd < 12 || dd > 14)) return 'вопроса';
    return 'вопросов';
  }
  // Сессии до summary_version 2 хранят индекс без precision/range — показываем как раньше.
  function readinessTitle(rd) {
    if (rd.range) return 'предварительно';
    if (rd.precision === 'rough') return `≈${rd.index}/100 — оценка по ${rd.answered_count} ответам, грубая`;
    if (rd.precision === 'normal') return `≈${rd.index}/100 — ${rd.label} (оценка)`;
    return `${rd.index}/100 — ${rd.label}`;
  }
  function fmtDate(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' });
  }
  function fmtClock(sec) {
    const t = Math.max(0, Math.floor(sec));
    return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
  }
  function fmtDuration(sec) {
    const t = Math.round(sec || 0);
    if (t < 60) return `${t} сек`;
    const m = Math.floor(t / 60), r = t % 60;
    return r ? `${m} мин ${r} сек` : `${m} мин`;
  }
  const label = (list, key) => (list.find((x) => x.key === key) || {}).label || key || '';

  function busyButton(btn, text) {
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = '';
    btn.append(h('span', { class: 'spinner' }), ' ', text);
    return () => { btn.disabled = false; btn.textContent = old; };
  }

  // ── графики (SVG) ──────────────────────────────────────────────────────────
  function ring(pct, size = 150) {
    const r = 62, c = 2 * Math.PI * r;
    const color = pct >= 75 ? 'var(--green)' : pct >= 50 ? 'var(--blue)' : pct >= 30 ? 'var(--amber)' : 'var(--red)';
    return h('div', { class: 'ring', style: `width:${size}px;height:${size}px` },
      s('svg', { viewBox: '0 0 150 150' },
        s('circle', { cx: 75, cy: 75, r, fill: 'none', stroke: 'var(--blue-50)', 'stroke-width': 14 }),
        s('circle', {
          cx: 75, cy: 75, r, fill: 'none', stroke: color, 'stroke-width': 14, 'stroke-linecap': 'round',
          'stroke-dasharray': c, 'stroke-dashoffset': c * (1 - Math.max(0, Math.min(100, pct)) / 100),
        })),
      h('div', { class: 'val' }, `${Math.round(pct)}%`));
  }

  function radar(dims) {
    const n = dims.length, cx = 240, cy = 150, R = 100;
    const short = {
      role_fit: 'Релевантность', structure: 'Структура', literacy: 'Грамотность',
      oratory: 'Ораторство', depth: 'Глубина', pace: 'Темп',
    };
    const pt = (i, v) => {
      const a = -Math.PI / 2 + (2 * Math.PI * i) / n;
      return [cx + Math.cos(a) * R * (v / 10), cy + Math.sin(a) * R * (v / 10)];
    };
    const svg = s('svg', { viewBox: '0 0 480 300', class: 'svg-chart', role: 'img', 'aria-label': 'Профиль интервью', style: 'width:100%;max-width:480px' });
    for (const lvl of [2, 4, 6, 8, 10]) {
      svg.appendChild(s('polygon', {
        points: dims.map((_, i) => pt(i, lvl).join(',')).join(' '),
        fill: 'none', stroke: 'var(--line)', 'stroke-width': 1,
      }));
    }
    dims.forEach((d, i) => {
      const [x, y] = pt(i, 10), [lx, ly] = pt(i, 11.9);
      svg.appendChild(s('line', { x1: cx, y1: cy, x2: x, y2: y, stroke: 'var(--line)' }));
      const anchor = Math.abs(lx - cx) < 8 ? 'middle' : lx > cx ? 'start' : 'end';
      svg.appendChild(s('text', { x: lx, y: ly + 4, 'text-anchor': anchor }, short[d.key] || d.label));
    });
    svg.appendChild(s('polygon', {
      points: dims.map((d, i) => pt(i, d.value || 0).join(',')).join(' '),
      fill: 'rgba(0,107,237,.18)', stroke: 'var(--blue)', 'stroke-width': 2.5,
    }));
    dims.forEach((d, i) => {
      const [x, y] = pt(i, d.value || 0);
      svg.appendChild(s('circle', { cx: x, cy: y, r: 4, fill: 'var(--blue)' }));
    });
    return svg;
  }

  function lineChart(points, opts = {}) {
    const W = 560, H = 220, L = 40, Rm = 16, T = 16, B = 30;
    const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'svg-chart', role: 'img', 'aria-label': opts.title || 'График', style: 'width:100%' });
    for (const g of [0, 25, 50, 75, 100]) {
      const y = T + (H - T - B) * (1 - g / 100);
      svg.appendChild(s('line', { x1: L, x2: W - Rm, y1: y, y2: y, stroke: 'var(--line)' }));
      svg.appendChild(s('text', { x: L - 8, y: y + 4, 'text-anchor': 'end' }, g));
    }
    const n = points.length;
    const xs = (i) => (n === 1 ? (L + W - Rm) / 2 : L + ((W - L - Rm) * i) / (n - 1));
    const ys = (v) => T + (H - T - B) * (1 - Math.max(0, Math.min(100, v)) / 100);
    if (n > 1) {
      svg.appendChild(s('polyline', {
        points: points.map((p, i) => `${xs(i)},${ys(p.value)}`).join(' '),
        fill: 'none', stroke: 'var(--blue)', 'stroke-width': 3, 'stroke-linejoin': 'round', 'stroke-linecap': 'round',
      }));
    }
    points.forEach((p, i) => {
      svg.appendChild(s('circle', { cx: xs(i), cy: ys(p.value), r: 5, fill: '#fff', stroke: 'var(--blue)', 'stroke-width': 3 }));
      svg.appendChild(s('text', { x: xs(i), y: ys(p.value) - 11, 'text-anchor': 'middle', style: 'fill:var(--ink);font-weight:700' }, Math.round(p.value)));
      svg.appendChild(s('text', { x: xs(i), y: H - 8, 'text-anchor': 'middle' }, p.label || String(i + 1)));
    });
    return svg;
  }

  // ── каркас ────────────────────────────────────────────────────────────────
  function logo() {
    return h('a', { class: 'logo', href: '#/' },
      h('img', { src: '/static/mascot.png', alt: '' }),
      h('span', {}, 'So', h('span', { class: 'k' }, 'krat')));
  }

  function nav() {
    const links = h('div', { class: 'nav-links' });
    links.append(h('a', { class: 'link hide-sm', href: '#/new' }, 'Новое интервью'));
    if (S.user) {
      links.append(
        h('a', { class: 'link', href: '#/cabinet' }, 'Кабинет'),
        h('button', { class: 'btn btn-plain btn-sm', onclick: logout, 'aria-label': `Выйти (${S.user})` },
          h('span', {}, 'Выйти', h('span', { class: 'nav-user' }, ` (${S.user})`))));
    } else {
      links.append(
        h('a', { class: 'link', href: '#/login' }, 'Войти'),
        h('a', { class: 'btn btn-primary btn-sm', href: '#/register' }, 'Регистрация'));
    }
    return h('header', { class: 'nav' }, h('div', { class: 'container nav-inner' }, logo(), links));
  }

  function page(...content) {
    root.replaceChildren(nav(), ...content);
    window.scrollTo(0, 0);
  }

  function loading(text) {
    page(h('div', { class: 'loading-screen' },
      h('div', {}, h('span', { class: 'spinner' }), h('p', { class: 'muted', style: 'margin-top:12px' }, text || 'Загрузка…'))));
  }

  async function logout() {
    try { await api('/api/auth/logout', { method: 'POST' }); } catch (e) { /* ignore */ }
    S.user = null;
    location.hash = '#/';
    route();
  }

  // ── лендинг ───────────────────────────────────────────────────────────────
  function landing() {
    const features = [
      ['📎', 'Вакансия → интервью', 'Вставь ссылку или текст вакансии: Сократ вытащит стек, обязанности и построит вопросы под них.'],
      ['🏢', 'Профиль компании', 'Продукты и технологии компании из открытых источников — вопросы про её реальные задачи.'],
      ['📄', 'Резюме и пробелы', 'Загрузи резюме — увидишь, что подтверждено опытом, а чего не хватает для вакансии.'],
      ['🧩', 'Разбор по STAR', 'Поведенческие ответы по Situation–Task–Action–Result с конкретным советом, как усилить.'],
      ['🎯', 'Индекс готовности', 'Одно число «готов ли ты к этой вакансии» с прозрачной формулой, а не магией.'],
      ['📅', 'План подготовки', 'Расписание на 7 дней по твоим пробелам с проверкой на каждый день.'],
      ['📈', 'Динамика', 'История сессий, рост по навыкам и сравнение готовности к разным вакансиям.'],
      ['🔥', 'Стресс-режим и персоны', 'Банк, стартап или Big Tech: тон и глубина как у настоящего интервьюера.'],
      ['🧪', 'Практика', 'Задача на код и мини-system design с разбором по критериям.'],
    ];
    page(
      h('section', { class: 'hero' }, h('div', { class: 'container hero-grid' },
        h('div', {},
          h('span', { class: 'eyebrow' }, 'ИИ-ассистент для подготовки к собеседованиям'),
          h('h1', {}, 'Репетиция именно того собеседования, ', h('span', { class: 'accent' }, 'на которое ты идёшь')),
          h('p', { class: 'lead' }, 'Вакансия, компания и твоё резюме — в персональное интервью, честный разбор и план подготовки. Не абстрактные вопросы про Python, а твоя реальная ситуация.'),
          h('div', { class: 'hero-cta' },
            h('a', { class: 'btn btn-primary btn-lg', href: '#/new' }, 'Начать подготовку'),
            S.user
              ? h('a', { class: 'btn btn-ghost btn-lg', href: '#/cabinet' }, 'Личный кабинет')
              : h('a', { class: 'btn btn-ghost btn-lg', href: '#/register' }, 'Создать аккаунт')),
          h('p', { class: 'hero-note' }, 'Без регистрации можно пройти первое интервью. Аккаунт нужен, чтобы сохранять историю и видеть прогресс.')),
        h('div', { class: 'hero-art' },
          h('img', { src: '/static/mascot.png', alt: 'Сократ в пиксельных очках с кофе' }),
          h('div', { class: 'bubble b1' }, '✓ Готовность 82/100'),
          h('div', { class: 'bubble b2' }, 'Знаю, ты справишься')))),

      h('section', { class: 'section' }, h('div', { class: 'container' },
        h('div', { class: 'section-head' }, h('h2', {}, 'Как это работает'),
          h('p', { class: 'muted' }, 'Три шага от вакансии до уверенного ответа.')),
        h('div', { class: 'steps' },
          h('div', { class: 'step' }, h('h3', {}, 'Расскажи, куда идёшь'), h('p', { class: 'muted' }, 'Вставь вакансию, выбери роль и при желании загрузи резюме.')),
          h('div', { class: 'step' }, h('h3', {}, 'Пройди интервью'), h('p', { class: 'muted' }, 'Адаптивные вопросы под компанию и твои пробелы, голосом или текстом.')),
          h('div', { class: 'step' }, h('h3', {}, 'Получи разбор и план'), h('p', { class: 'muted' }, 'Радар навыков, индекс готовности, эталонные ответы и план на неделю.'))))),

      h('section', { class: 'section soft' }, h('div', { class: 'container' },
        h('div', { class: 'section-head' }, h('h2', {}, 'Что внутри'),
          h('p', { class: 'muted' }, 'Всё, чтобы подготовка была похожа на настоящее собеседование.')),
        h('div', { class: 'cards' }, features.map(([ico, title, text]) =>
          h('div', { class: 'card' }, h('div', { class: 'ico' }, ico), h('h3', {}, title), h('p', { class: 'muted', style: 'margin:0' }, text)))))),

      h('section', { class: 'section' }, h('div', { class: 'container' },
        h('div', { class: 'cta-band' },
          h('h2', {}, 'Готов попробовать?'),
          h('p', {}, 'Первое интервью — за 10 минут. Результат и план — сразу после.'),
          h('a', { class: 'btn btn-primary btn-lg', href: '#/new' }, 'Начать подготовку')))),

      h('footer', { class: 'footer' }, h('div', { class: 'container row' },
        h('span', {}, '© Сократ — тренажёр собеседований'),
        h('span', { class: 'grow' }), h('span', {}, 'Оценки ИИ — ориентир для подготовки, а не гарантия результата.'))));
  }

  // ── авторизация ───────────────────────────────────────────────────────────
  function authPage(mode) {
    const isReg = mode === 'register';
    const err = h('div', { class: 'error-box hidden' });
    const user = h('input', { type: 'text', autocomplete: 'username', placeholder: 'например, ivan.petrov', required: true });
    const pass = h('input', { type: 'password', autocomplete: isReg ? 'new-password' : 'current-password', placeholder: 'не короче 6 символов', required: true });
    const btn = h('button', { class: 'btn btn-primary', style: 'width:100%', type: 'submit' }, isReg ? 'Создать аккаунт' : 'Войти');
    const form = h('form', {
      onsubmit: async (e) => {
        e.preventDefault();
        err.classList.add('hidden');
        const done = busyButton(btn, 'Подожди…');
        try {
          const res = await api(isReg ? '/api/auth/register' : '/api/auth/login', { method: 'POST', json: { username: user.value, password: pass.value } });
          S.user = res.user;
          const back = sessionStorage.getItem('after-login');
          sessionStorage.removeItem('after-login');
          location.hash = back || '#/cabinet';
        } catch (ex) {
          err.textContent = ex.message;
          err.classList.remove('hidden');
          done();
        }
      },
    },
      err,
      h('label', { class: 'field' }, h('span', {}, 'Логин'), user),
      h('label', { class: 'field' }, h('span', {}, 'Пароль'), pass),
      btn);
    page(h('div', { class: 'auth-wrap' }, h('div', { class: 'panel auth-card' },
      h('h2', {}, isReg ? 'Создай аккаунт' : 'С возвращением'),
      h('p', { class: 'muted' }, isReg ? 'Чтобы сохранять историю интервью и видеть прогресс.' : 'Войди, чтобы увидеть историю и динамику.'),
      form,
      h('p', { class: 'muted center', style: 'margin:16px 0 0' },
        isReg ? 'Уже есть аккаунт? ' : 'Нет аккаунта? ',
        h('a', { href: isReg ? '#/login' : '#/register' }, isReg ? 'Войти' : 'Зарегистрироваться')))));
  }

  // ── мастер подготовки ─────────────────────────────────────────────────────
  const W = { iv: null, step: 1, tab: 'vacancy' };

  async function wizard() {
    if (!S.config.llm_ready) {
      page(h('div', { class: 'narrow wizard' }, h('div', { class: 'panel' },
        h('h2', {}, 'Модель не настроена'),
        h('p', { class: 'muted' }, 'На сервере не задан ключ LLM-провайдера (DEEPSEEK_API_KEY). Добавь его в .env и перезапусти сервер.'))));
      return;
    }
    if (!W.iv || W.iv.step !== 'setup') {
      loading('Готовлю сессию…');
      try { W.iv = await api('/api/interviews', { method: 'POST' }); W.step = 1; W.tab = 'vacancy'; }
      catch (e) { toast(e.message, true); return; }
    }
    renderWizard();
  }

  function stepsBar() {
    const names = ['Куда идёшь', 'Резюме', 'Настройки'];
    return h('div', { class: 'progress-steps' }, names.map((n, i) =>
      h('div', { class: 'ps' + (W.step === i + 1 ? ' active' : W.step > i + 1 ? ' done' : '') }, `${i + 1}. ${n}`)));
  }

  function renderWizard() {
    const body = W.step === 1 ? wizStepGoal() : W.step === 2 ? wizStepResume() : wizStepParams();
    page(h('div', { class: 'narrow wizard' }, stepsBar(), body));
  }

  function vacancySummary(iv) {
    const v = iv.vacancy;
    if (!v) return null;
    const c = iv.company;
    return h('div', { class: 'stack' },
      h('div', { class: 'info-box' }, '✅ Вакансия разобрана: ', h('b', {}, v.title || iv.role), v.company ? ` — ${v.company}` : ''),
      v.skills.length ? h('div', {}, h('div', { class: 'muted' }, 'Стек из вакансии'), v.skills.map((k) => h('span', { class: 'tag' }, k))) : null,
      v.responsibilities.length ? h('ul', { style: 'margin:0;padding-left:20px' }, v.responsibilities.slice(0, 4).map((r) => h('li', {}, r))) : null,
      c ? h('div', { class: 'company-card' },
        h('h4', {}, `🏢 О компании ${c.name}`),
        c.industry ? h('div', {}, h('span', { class: 'muted' }, 'Сфера: '), c.industry) : null,
        c.products.length ? h('div', {}, h('span', { class: 'muted' }, 'Продукты: '), c.products.join(', ')) : null,
        c.tech_stack.length ? h('div', {}, h('span', { class: 'muted' }, 'Технологии: '), c.tech_stack.join(', ')) : null,
        c.sources.length ? h('div', { class: 'muted', style: 'font-size:.88rem;margin-top:6px' }, `Источники: ${c.sources.join('; ')}`) : null)
        : (v.company ? h('p', { class: 'muted' }, 'Открытых данных о компании не нашлось — вопросы построим по тексту вакансии.') : null));
  }

  function wizStepGoal() {
    const iv = W.iv;
    const tabs = [['vacancy', '📎 Вакансия'], ['preset', '🎯 Готовая роль'], ['custom', '✏️ Своя роль']];
    const tabBar = h('div', { class: 'tabs' }, tabs.map(([k, t]) =>
      h('button', { class: 'tab' + (W.tab === k ? ' active' : ''), onclick: () => { W.tab = k; renderWizard(); } }, t)));

    let panel;
    if (W.tab === 'vacancy') {
      const ta = h('textarea', { placeholder: 'Вставь ссылку (Хабр Карьера, сайт компании…) или целиком скопируй текст вакансии', rows: 7 });
      const btn = h('button', { class: 'btn btn-primary' }, 'Разобрать вакансию');
      btn.addEventListener('click', async () => {
        if (ta.value.trim().length < 10) { toast('Вставь ссылку или текст вакансии', true); return; }
        const done = busyButton(btn, 'Разбираю вакансию и компанию…');
        try { W.iv = await api(`/api/interviews/${iv.id}/vacancy`, { method: 'POST', json: { text: ta.value } }); renderWizard(); }
        catch (e) { toast(e.message, true); done(); }
      });
      panel = h('div', { class: 'stack' }, ta, h('div', { class: 'row' }, btn,
        h('span', { class: 'muted', style: 'font-size:.9rem' }, 'Совет: если ссылка не открывается, вставь текст вакансии.')));
    } else if (W.tab === 'preset') {
      panel = h('div', { class: 'chips' }, S.config.roles.map((r) =>
        h('button', { class: 'chip' + (iv.role === r && !iv.vacancy ? ' active' : ''), onclick: async () => {
          try { W.iv = await api(`/api/interviews/${iv.id}/role`, { method: 'POST', json: { role: r, custom: false } }); renderWizard(); }
          catch (e) { toast(e.message, true); }
        } }, r)));
    } else {
      const inp = h('input', { type: 'text', placeholder: 'например, Middle DevOps Engineer', value: iv.vacancy ? '' : iv.role });
      const btn = h('button', { class: 'btn btn-primary' }, 'Использовать');
      btn.addEventListener('click', async () => {
        const done = busyButton(btn, 'Определяю тип роли…');
        try { W.iv = await api(`/api/interviews/${iv.id}/role`, { method: 'POST', json: { role: inp.value, custom: true } }); renderWizard(); }
        catch (e) { toast(e.message, true); done(); }
      });
      panel = h('div', { class: 'row' }, h('div', { class: 'grow' }, inp), btn);
    }

    const chosen = iv.role
      ? h('div', { class: 'info-box' }, '🎯 Роль: ', h('b', {}, iv.role))
      : null;
    return h('div', { class: 'panel stack' },
      h('h2', {}, 'К какому собеседованию готовимся?'),
      h('p', { class: 'muted' }, 'Чем точнее данные, тем ближе интервью к настоящему.'),
      tabBar, panel,
      vacancySummary(iv) || chosen,
      h('div', { class: 'row', style: 'justify-content:flex-end' },
        h('button', { class: 'btn btn-primary', disabled: !iv.role, onclick: () => { W.step = 2; renderWizard(); } }, 'Далее →')));
  }

  function gapsView(g) {
    if (!g) return null;
    return h('div', { class: 'stack' },
      h('div', { class: 'info-box' }, 'Совпадение резюме с вакансией: ', h('b', {}, `${g.coverage_pct}%`)),
      g.matched.length ? h('div', {}, h('div', { class: 'muted' }, '✅ Подтверждено опытом'), g.matched.map((k) => h('span', { class: 'tag ok' }, k))) : null,
      g.weak_evidence.length ? h('div', {}, h('div', { class: 'muted' }, '🟡 Указано, но без примеров в опыте'), g.weak_evidence.map((k) => h('span', { class: 'tag warn' }, k))) : null,
      g.missing.length ? h('div', {}, h('div', { class: 'muted' }, '⚠️ Нет в резюме'), g.missing.map((k) => h('span', { class: 'tag bad' }, k))) : null,
      h('p', { class: 'muted', style: 'margin:0' }, 'Интервью сделает упор на пробелы и слабо подтверждённое.'));
  }

  function wizStepResume() {
    const iv = W.iv;
    const input = h('input', { type: 'file', accept: '.pdf,.docx,.txt,.md', class: 'hidden' });
    const zone = h('div', { class: 'dropzone', tabindex: 0, role: 'button' },
      h('div', { style: 'font-size:2rem' }, '📄'),
      h('b', {}, 'Загрузи резюме'),
      h('div', { class: 'muted' }, 'PDF, DOCX или TXT, до 5 МБ. Контакты вырезаются до обработки, файл не сохраняется.'));
    const upload = async (file) => {
      if (!file) return;
      const fd = new FormData();
      fd.append('file', file);
      zone.replaceChildren(h('span', { class: 'spinner' }), h('p', { class: 'muted' }, 'Разбираю резюме…'));
      try { W.iv = await api(`/api/interviews/${iv.id}/resume`, { method: 'POST', form: fd }); renderWizard(); }
      catch (e) { toast(e.message, true); renderWizard(); }
    };
    zone.addEventListener('click', () => input.click());
    zone.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') input.click(); });
    input.addEventListener('change', () => upload(input.files[0]));
    zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('drag'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag'));
    zone.addEventListener('drop', (e) => { e.preventDefault(); zone.classList.remove('drag'); upload(e.dataTransfer.files[0]); });

    const r = iv.resume;
    return h('div', { class: 'panel stack' },
      h('h2', {}, 'Резюме (необязательно)'),
      h('p', { class: 'muted' }, 'С резюме вопросы строятся по твоему реальному опыту и пробелам относительно вакансии.'),
      r ? h('div', { class: 'stack' },
        h('div', { class: 'info-box' }, '✅ Резюме учтено: ', h('b', {}, r.headline || 'загружено')),
        r.skills.length ? h('div', {}, r.skills.slice(0, 14).map((k) => h('span', { class: 'tag' }, k))) : null,
        gapsView(iv.gaps),
        h('button', { class: 'btn btn-plain btn-sm', onclick: async () => {
          try { W.iv = await api(`/api/interviews/${iv.id}/resume`, { method: 'DELETE' }); renderWizard(); } catch (e) { toast(e.message, true); }
        } }, 'Убрать резюме'))
        : h('div', {}, zone, input),
      h('div', { class: 'row', style: 'justify-content:space-between' },
        h('button', { class: 'btn btn-plain', onclick: () => { W.step = 1; renderWizard(); } }, '← Назад'),
        h('button', { class: 'btn btn-primary', onclick: () => { W.step = 3; renderWizard(); } }, r ? 'Далее →' : 'Пропустить →')));
  }

  function choiceGroup(title, items, current, onPick) {
    return h('div', {}, h('div', { class: 'muted', style: 'font-weight:600;margin-bottom:8px' }, title),
      h('div', { class: 'chips' }, items.map((it) =>
        h('button', { class: 'chip' + (current === it.key ? ' active' : ''), type: 'button', onclick: () => onPick(it.key) },
          it.label, it.hint ? h('small', {}, it.hint) : null))));
  }

  function wizStepParams() {
    const iv = W.iv, cfg = S.config;
    const set = async (patch) => {
      try { W.iv = await api(`/api/interviews/${iv.id}/params`, { method: 'POST', json: patch }); renderWizard(); }
      catch (e) { toast(e.message, true); }
    };
    const skills = h('input', { type: 'text', value: (iv.skills || []).join(', '), placeholder: 'Docker, Kubernetes, Terraform' });
    skills.addEventListener('change', () => set({ skills: skills.value }));
    const ready = iv.level && iv.interview_type;
    const startBtn = h('button', { class: 'btn btn-primary btn-lg', disabled: !ready }, 'Начать интервью →');
    startBtn.addEventListener('click', async () => {
      const done = busyButton(startBtn, 'Готовлю вопросы…');
      try {
        await api(`/api/interviews/${iv.id}/params`, { method: 'POST', json: { skills: skills.value } });
        const res = await api(`/api/interviews/${iv.id}/start`, { method: 'POST' });
        S.first[iv.id] = res.question;
        const id = iv.id;
        W.iv = null;
        location.hash = `#/interview/${id}`;
      } catch (e) { toast(e.message, true); done(); }
    });
    return h('div', { class: 'panel stack' },
      h('h2', {}, 'Настрой интервью'),
      iv.vacancy ? h('p', { class: 'muted' }, 'Уровень, тип и навыки подобраны по вакансии — поправь, если нужно.') : null,
      choiceGroup('Уровень', cfg.levels, iv.level, (k) => set({ level: k })),
      choiceGroup('Тип интервью', cfg.types, iv.interview_type, (k) => set({ interview_type: k })),
      choiceGroup('Длина', cfg.modes, iv.mode, (k) => set({ mode: k })),
      choiceGroup('Интервьюер', cfg.personas, iv.persona, (k) => set({ persona: k })),
      h('div', { class: 'row' },
        h('button', { class: 'chip' + (iv.stress ? ' active' : ''), onclick: () => set({ stress: !iv.stress }) }, '🔥 Стресс-режим', h('small', {}, '90 сек, без уточнений')),
        h('button', { class: 'chip' + (iv.lang === 'en' ? ' active' : ''), onclick: () => set({ lang: iv.lang === 'en' ? 'ru' : 'en' }) }, '🌐 English', h('small', {}, 'интервью на английском'))),
      h('label', { class: 'field' }, h('span', {}, 'Навыки в фокусе (через запятую, необязательно)'), skills),
      h('div', { class: 'row', style: 'justify-content:space-between' },
        h('button', { class: 'btn btn-plain', onclick: () => { W.step = 2; renderWizard(); } }, '← Назад'),
        startBtn));
  }

  // ── интервью ──────────────────────────────────────────────────────────────
  let timerHandle = null;
  let recorder = null;

  function stopTimer() { if (timerHandle) { clearInterval(timerHandle); timerHandle = null; } }

  function starView(star) {
    if (!star) return null;
    const names = { situation: 'Ситуация', task: 'Задача', action: 'Действия', result: 'Результат' };
    return h('div', {},
      h('div', { class: 'muted', style: 'font-weight:700;margin-top:8px' }, '🧩 Разбор по STAR'),
      h('div', { class: 'star-grid' }, Object.keys(names).map((k) => {
        const p = (star.parts || {})[k] || {};
        return h('div', { class: 'star-cell ' + (p.present ? 'yes' : 'no') },
          h('b', {}, `${p.present ? '✅' : '❌'} ${names[k]}`), p.comment || '');
      })),
      star.tip ? h('div', { class: 'info-box' }, '💡 ', star.tip) : null);
  }

  function feedbackCard(res) {
    const head = h('div', { class: 'fb-head' },
      res.score > 0 ? h('span', { class: 'score-pill' }, `${res.score}/10`) : null,
      h('b', {}, res.verdict),
      res.elapsed_seconds ? h('span', { class: 'muted' }, `⏱ ${fmtDuration(res.elapsed_seconds)}`) : null);
    return h('div', { class: `fb ${res.verdict_key}` }, head,
      h('p', { style: 'margin:0 0 8px' }, res.feedback),
      res.strengths && res.strengths.length ? h('div', {}, h('b', {}, '👍 Сильно'), h('ul', {}, res.strengths.map((x) => h('li', {}, x)))) : null,
      res.weaknesses && res.weaknesses.length ? h('div', {}, h('b', {}, '🛠 Над чем поработать'), h('ul', {}, res.weaknesses.map((x) => h('li', {}, x)))) : null,
      starView(res.star),
      res.ideal_answer ? h('div', { class: 'ideal' }, h('b', {}, '💡 Как ответил бы сильный кандидат\n'), res.ideal_answer) : null);
  }

  async function interviewPage(id) {
    stopTimer();
    let q = S.first[id];
    if (!q) {
      loading('Открываю интервью…');
      try {
        const res = await api(`/api/interviews/${id}/question`);
        if (res.finished) { location.hash = `#/report/${id}`; return; }
        q = res.question;
      } catch (e) { toast(e.message, true); location.hash = '#/new'; return; }
    }
    delete S.first[id];

    const thread = h('div', { class: 'thread' });
    const topBar = h('div', { class: 'iv-top' });
    const bar = h('i', { style: 'width:0%' });
    const counter = h('b', {}, '');
    const timer = h('span', { class: 'timer' }, '0:00');
    topBar.append(counter, h('div', { class: 'bar' }, bar), timer);

    const ta = h('textarea', { placeholder: 'Напиши ответ… (Ctrl+Enter — отправить)', rows: 3 });
    const sendBtn = h('button', { class: 'btn btn-primary' }, 'Отправить');
    const skipBtn = h('button', { class: 'btn btn-ghost' }, 'Пропустить');
    const finishBtn = h('button', { class: 'btn btn-plain' }, '🏁 Завершить');
    const micBtn = S.config.stt ? h('button', { class: 'btn btn-ghost mic', title: 'Ответить голосом' }, '🎤') : null;
    const actions = h('div', { class: 'composer-actions' }, sendBtn, skipBtn, micBtn, h('span', { class: 'grow' }), finishBtn);
    const composer = h('div', { class: 'composer' }, h('div', { class: 'composer-inner' }, ta, actions));

    let current = q, startedAt = Date.now(), busy = false;

    // Прокрутка к новому сообщению так, чтобы оно целиком было над панелью ввода.
    function scrollDown(el) {
      const target = el || thread.lastElementChild;
      if (!target) return;
      target.style.scrollMarginBottom = `${composer.offsetHeight + 20}px`;
      requestAnimationFrame(() => target.scrollIntoView({ behavior: 'smooth', block: 'end' }));
    }
    function setBusy(v) { busy = v; for (const b of [sendBtn, skipBtn, finishBtn, micBtn]) if (b) b.disabled = v; ta.disabled = v; }

    function showQuestion(question) {
      current = question;
      startedAt = Date.now();
      counter.textContent = `Вопрос ${question.number} из ${question.max}`;
      bar.style.width = `${Math.min(100, Math.round(((question.number - 1) / question.max) * 100))}%`;
      const qMsg = h('div', { class: 'msg' },
        h('div', { class: 'avatar' }, h('img', { src: '/static/mascot.png', alt: '' })),
        h('div', { class: 'bubble-msg' },
          h('div', { class: 'q-head' }, `Вопрос ${question.number}`, ' · ', question.type_label),
          h('div', { class: 'q-text' }, question.text)));
      thread.append(qMsg);
      if (S.config.tts) speak(question.text);
      stopTimer();
      timer.classList.remove('over');
      timerHandle = setInterval(() => {
        const sec = (Date.now() - startedAt) / 1000;
        if (question.time_limit) {
          timer.textContent = `${fmtClock(sec)} / ${fmtClock(question.time_limit)}`;
          timer.classList.toggle('over', sec > question.time_limit);
        } else {
          timer.textContent = fmtClock(sec);
        }
      }, 500);
      scrollDown(qMsg);
    }

    function addUser(text) {
      thread.append(h('div', { class: 'msg user' }, h('div', { class: 'bubble-msg' }, text)));
    }
    function addBot(...kids) {
      thread.append(h('div', { class: 'msg' },
        h('div', { class: 'avatar' }, h('img', { src: '/static/mascot.png', alt: '' })),
        h('div', { class: 'bubble-msg' }, ...kids)));
    }

    async function finishAndGo() {
      stopTimer();
      composer.remove();
      thread.append(h('div', { class: 'msg' }, h('div', { class: 'bubble-msg' },
        h('span', { class: 'spinner' }), ' Интервью завершено. Формирую отчёт, подбираю материалы…')));
      scrollDown();
      try {
        await api(`/api/interviews/${id}/finish`, { method: 'POST' });
        location.hash = `#/report/${id}`;
      } catch (e) { toast(e.message, true); }
    }

    function handleResult(res) {
      if (res.kind === 'clarification') {
        addBot(h('p', { style: 'margin:0 0 6px' }, res.feedback), res.explanation ? h('p', { style: 'margin:0' }, res.explanation) : null);
      } else if (res.kind === 'dont_know') {
        addBot(res.feedback, h('div', { class: 'muted', style: 'margin-top:6px' }, 'Подумай вслух или нажми «Пропустить».'));
      } else if (res.kind === 'meta') {
        addBot(res.feedback);
      } else if (res.kind === 'scored') {
        thread.append(feedbackCard(res));
        if (res.finished) { finishAndGo(); return; }
        showQuestion(res.question);
      }
      scrollDown();
    }

    async function send(text, skip) {
      if (busy) return;
      if (!skip && !text.trim()) { toast('Напиши ответ или нажми «Пропустить»', true); return; }
      addUser(skip ? '⏭ Пропустить вопрос' : text.trim());
      ta.value = '';
      setBusy(true);
      const typing = h('div', { class: 'msg' }, h('div', { class: 'bubble-msg' }, h('span', { class: 'typing' }, h('i'), h('i'), h('i'))));
      thread.append(typing);
      scrollDown();
      try {
        const res = await api(`/api/interviews/${id}/${skip ? 'skip' : 'answer'}`, skip ? { method: 'POST' } : { method: 'POST', json: { text } });
        typing.remove();
        handleResult(res);
      } catch (e) {
        typing.remove();
        toast(e.message, true);
      } finally { setBusy(false); if (!ta.disabled) ta.focus(); }
    }

    sendBtn.addEventListener('click', () => send(ta.value, false));
    skipBtn.addEventListener('click', () => send('', true));
    finishBtn.addEventListener('click', async () => {
      if (!confirm('Завершить интервью и получить отчёт по уже данным ответам?')) return;
      setBusy(true);
      finishAndGo();
    });
    ta.addEventListener('keydown', (e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') send(ta.value, false); });
    if (micBtn) micBtn.addEventListener('click', () => toggleRecording(micBtn, ta));

    page(h('div', { class: 'container interview narrow' }, topBar, thread), composer);
    const syncPad = () => document.documentElement.style.setProperty('--composer-h', `${composer.offsetHeight}px`);
    syncPad();
    if (window.ResizeObserver) new ResizeObserver(syncPad).observe(composer);
    showQuestion(q);
    ta.focus();
  }

  // Озвучка вопросов (если включена на сервере)
  let currentAudio = null;
  async function speak(text) {
    try {
      const res = await fetch('/api/tts', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
      if (!res.ok) return;
      const url = URL.createObjectURL(await res.blob());
      if (currentAudio) currentAudio.pause();
      currentAudio = new Audio(url);
      currentAudio.play().catch(() => { /* автоплей может быть запрещён */ });
    } catch (e) { /* озвучка best-effort */ }
  }

  // Запись голоса: PCM16 16 kHz → /api/stt
  async function toggleRecording(btn, ta) {
    if (recorder) { recorder.stop(); return; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch (e) { toast('Нет доступа к микрофону. Разреши его в браузере (нужен HTTPS или localhost).', true); return; }
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    const chunks = [];
    proc.onaudioprocess = (ev) => chunks.push(new Float32Array(ev.inputBuffer.getChannelData(0)));
    src.connect(proc);
    proc.connect(ctx.destination);
    btn.classList.add('rec');
    btn.textContent = '⏹';
    const limit = setTimeout(() => recorder && recorder.stop(), 55000);
    recorder = {
      stop: async () => {
        recorder = null;
        clearTimeout(limit);
        proc.disconnect(); src.disconnect();
        stream.getTracks().forEach((t) => t.stop());
        const rate = ctx.sampleRate;
        ctx.close();
        btn.classList.remove('rec');
        btn.textContent = '🎤';
        const total = chunks.reduce((n, c) => n + c.length, 0);
        if (!total) return;
        const flat = new Float32Array(total);
        let off = 0;
        for (const c of chunks) { flat.set(c, off); off += c.length; }
        const ratio = rate / 16000, outLen = Math.floor(flat.length / ratio);
        const pcm = new Int16Array(outLen);
        for (let i = 0; i < outLen; i++) {
          const v = Math.max(-1, Math.min(1, flat[Math.floor(i * ratio)]));
          pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
        }
        btn.disabled = true;
        try {
          const res = await api('/api/stt', { method: 'POST', raw: pcm.buffer });
          if (res.text) ta.value = (ta.value ? ta.value + ' ' : '') + res.text;
          else toast('Речь не распознана — попробуй ещё раз', true);
        } catch (e) { toast(e.message, true); }
        btn.disabled = false;
      },
    };
  }

  // ── отчёт ─────────────────────────────────────────────────────────────────
  async function reportPage(id, saved) {
    loading('Собираю отчёт…');
    let r;
    try { r = await api(saved ? `/api/sessions/${id}` : `/api/interviews/${id}/report`); }
    catch (e) {
      if (e.status === 401) { sessionStorage.setItem('after-login', `#/saved/${id}`); location.hash = '#/login'; return; }
      toast(e.message, true); location.hash = S.user ? '#/cabinet' : '#/'; return;
    }

    const cls = pctClass(r.percentage);
    const verdictText = r.verdict_label
      || { Strong: 'Сильный результат', Competent: 'Уверенная база', 'Needs Work': 'Есть над чем поработать' }[r.verdict] || r.verdict;
    const readiness = r.readiness;

    const hero = h('div', { class: 'hero-score' },
      ring(r.percentage),
      h('div', {},
        h('h2', { style: 'margin-bottom:4px' }, verdictText),
        h('p', { class: 'muted', style: 'margin:0 0 8px' }, `${r.role} · ${r.level} · ${r.type_label}${r.company ? ' · ' + r.company : ''}`),
        h('div', {}, h('span', { class: 'tag' }, `${r.total_score}/${r.max_possible} баллов`),
          h('span', { class: r.answered_count < r.asked_count ? 'tag warn' : 'tag' }, `отвечено ${r.answered_count} из ${r.asked_count}`),
          r.answered_count ? h('span', { class: 'tag' }, `средний по отвеченным: ${r.avg_score_answered}/10`) : null,
          r.total_time_seconds ? h('span', { class: 'tag' }, `⏱ ${fmtDuration(r.total_time_seconds)}`) : null,
          r.stress ? h('span', { class: 'tag bad' }, '🔥 стресс-режим') : null)));

    const blocks = [hero];

    if (readiness) {
      blocks.push(h('div', { class: 'panel' },
        h('h3', {}, `🎯 Индекс готовности к вакансии: ${readinessTitle(readiness)}`),
        readiness.range
          ? h('p', { class: 'muted' }, `Ответь ещё на ${readiness.answers_needed} ${questionsWord(readiness.answers_needed)}, чтобы получить индекс. Сейчас он может быть от ${readiness.range[0]} до ${readiness.range[1]}.`)
          : null,
        readiness.components.map((c) => h('div', { class: 'dim-row' },
          h('span', {}, c.name, h('span', { class: 'muted' }, `  вес ${c.weight_pct}%`)),
          h('div', { class: 'meter ' + pctClass(c.value) }, h('i', { style: `width:${c.value}%` })),
          h('span', {}, `${Math.round(c.value)}%`))),
        readiness.untested_skills && readiness.untested_skills.length
          ? h('p', { style: 'margin:8px 0 0' }, h('span', { class: 'muted' }, 'Не проверено: '), readiness.untested_skills.map((k) => h('span', { class: 'tag warn' }, k)))
          : null,
        readiness.resume_coverage_pct !== null && readiness.resume_coverage_pct !== undefined
          ? h('p', { style: 'margin:8px 0 0' }, `📄 Резюме покрывает ${readiness.resume_coverage_pct}% требуемых навыков`)
          : null));
    }

    const row2 = [];
    if (r.has_dimensions) {
      row2.push(h('div', { class: 'panel' }, h('h3', {}, '📊 Профиль интервью'), h('div', { class: 'center' }, radar(r.dimensions)),
        r.dimensions.map((d) => h('div', { class: 'dim-row' }, h('span', {}, d.label),
          h('div', { class: 'meter ' + pctClass(d.value * 10) }, h('i', { style: `width:${d.value * 10}%` })), h('span', {}, d.value)))));
    }
    if (r.skills_coverage && r.skills_coverage.length) {
      row2.push(h('div', { class: 'panel' }, h('h3', {}, '🎯 Покрытие навыков'),
        r.skills_coverage.map((e) => e.questions_count
          ? h('div', { class: 'dim-row' }, h('span', {}, e.skill),
            h('div', { class: 'meter ' + pctClass(e.avg_score * 10) }, h('i', { style: `width:${e.avg_score * 10}%` })), h('span', {}, e.avg_score))
          : h('div', { class: 'dim-row' }, h('span', {}, e.skill), h('span', { class: 'muted' }, 'не проверено ⚠️'), h('span', {}, '—')))));
    }
    if (row2.length) blocks.push(h('div', { class: row2.length > 1 ? 'grid-2' : '' }, row2));

    if (r.gap_analysis) blocks.push(h('div', { class: 'panel' }, h('h3', {}, '📄 Резюме ↔ вакансия'), gapsView(r.gap_analysis)));

    if (r.star) {
      const missed = { situation: 'Ситуация', task: 'Задача', action: 'Действия', result: 'Результат' }[r.star.most_missed];
      blocks.push(h('div', { class: 'panel' }, h('h3', {}, '🧩 Поведенческие ответы (STAR)'),
        h('p', { style: 'margin:0' }, `Полнота структуры: `, h('b', {}, `${r.star.avg_completeness_pct}%`), ` по ${r.star.answers_count} ответам.`,
          missed ? [' Чаще всего не хватает: ', h('b', {}, missed), '.'] : '')));
    }

    if (r.dynamics) {
      const d = r.dynamics;
      const delta = (v) => (v > 0.5 ? `📈 +${v}` : v < -0.5 ? `📉 ${v}` : '➖ 0');
      blocks.push(h('div', { class: 'panel' }, h('h3', {}, '📈 Динамика по этой роли'),
        h('p', {}, `Сессий: `, h('b', {}, d.sessions_count), ` · к прошлой: `, h('b', {}, `${delta(d.delta_vs_last)} п.п.`),
          d.sessions_count > 2 ? [` · с первой: `, h('b', {}, `${delta(d.delta_vs_first)} п.п.`)] : ''),
        lineChart(d.points.map((v, i) => ({ value: v, label: i === d.points.length - 1 ? 'сейчас' : String(i + 1) })), { title: 'Прогресс по роли' })));
    }
    if (r.other_vacancies && r.other_vacancies.length) {
      blocks.push(h('div', { class: 'panel' }, h('h3', {}, '🆚 Готовность к другим вакансиям'),
        r.other_vacancies.map((o) => h('div', { class: 'dim-row' }, h('span', {}, `${o.company}`, h('span', { class: 'muted' }, ` · ${o.role}`)),
          h('div', { class: 'meter ' + pctClass(o.readiness) }, h('i', { style: `width:${o.readiness}%` })), h('span', {}, o.readiness)))));
    }

    if (r.recommendation) blocks.push(h('div', { class: 'info-box', style: 'font-size:1.02rem' }, '💡 ', r.recommendation));

    // Действия
    const extra = h('div', { class: 'stack' });
    const actions = h('div', { class: 'sticky-actions' });
    if (!saved) {
      const planBtn = h('button', { class: 'btn btn-primary' }, '📅 План подготовки на 7 дней');
      planBtn.addEventListener('click', async () => {
        const done = busyButton(planBtn, 'Составляю план…');
        try {
          const p = await api(`/api/interviews/${id}/plan`, { method: 'POST', json: { days: 7 } });
          extra.prepend(planView(p));
          planBtn.remove();
        } catch (e) { toast(e.message, true); done(); }
      });
      actions.append(planBtn);
      for (const k of S.config.practice) {
        const b = h('button', { class: 'btn btn-ghost' }, k.key === 'code' ? '🧪 Задача на код' : '🏗 System design');
        b.addEventListener('click', async () => {
          const done = busyButton(b, 'Готовлю задание…');
          try { extra.prepend(practiceView(id, await api(`/api/interviews/${id}/practice`, { method: 'POST', json: { kind: k.key } }))); }
          catch (e) { toast(e.message, true); }
          done();
        });
        actions.append(b);
      }
      actions.append(h('a', { class: 'btn btn-ghost', href: `/api/interviews/${id}/report.md`, download: '' }, '📄 Скачать отчёт'));
    }
    actions.append(h('a', { class: 'btn btn-plain', href: '#/new' }, '🔄 Новое интервью'));
    if (S.user) actions.append(h('a', { class: 'btn btn-plain', href: '#/cabinet' }, 'Кабинет'));
    blocks.push(actions, extra);

    if (!saved && !S.user) {
      blocks.push(h('div', { class: 'info-box' }, 'Это гостевая сессия. ',
        h('a', { href: '#/register' }, 'Создай аккаунт'), ', чтобы сохранять историю и видеть прогресс между интервью.'));
    }

    // Материалы
    if (r.resources && r.resources.length) {
      blocks.push(h('div', { class: 'panel' }, h('h3', {}, '📚 Что подтянуть'),
        r.resources.map((t) => h('div', { style: 'margin-bottom:14px' },
          h('b', {}, t.topic), t.why_to_study ? h('div', { class: 'muted' }, t.why_to_study) : null,
          h('ul', { style: 'margin:6px 0 0;padding-left:20px' }, (t.resources || []).map((x) => h('li', {},
            x.url ? h('a', { href: x.url, target: '_blank', rel: 'noopener noreferrer' }, x.title)
              : h('a', { href: 'https://www.google.com/search?q=' + encodeURIComponent(x.search_query || x.title), target: '_blank', rel: 'noopener noreferrer' }, x.title),
            x.type ? h('span', { class: 'muted' }, ` · ${x.type}`) : null)))))));
    }

    // По вопросам
    blocks.push(h('div', {}, h('h3', {}, '📋 По вопросам'),
      r.items.map((it) => h('details', { class: 'acc' },
        h('summary', {},
          h('span', { class: 'score-pill' }, it.score === 0 ? '—' : `${it.score}/10`),
          h('span', {}, `${it.number}. ${it.question}`)),
        h('div', { class: 'body' },
          it.answer ? h('p', {}, h('b', {}, 'Твой ответ: '), it.answer) : null,
          it.feedback ? h('p', {}, it.feedback) : null,
          it.strengths && it.strengths.length ? h('div', {}, h('b', {}, '👍 Сильно'), h('ul', {}, it.strengths.map((x) => h('li', {}, x)))) : null,
          it.weaknesses && it.weaknesses.length ? h('div', {}, h('b', {}, '🛠 Поработать'), h('ul', {}, it.weaknesses.map((x) => h('li', {}, x)))) : null,
          starView(it.star),
          it.ideal_answer ? h('div', { class: 'ideal' }, h('b', {}, '💡 Эталон\n'), it.ideal_answer) : null)))));

    page(h('div', { class: 'container report stack' }, blocks));
  }

  function planView(p) {
    return h('div', { class: 'panel' },
      h('h3', {}, `📅 План подготовки · ~${Math.round(p.total_minutes / 60 * 10) / 10} ч`),
      p.days.map((d) => h('div', { class: 'plan-day' },
        h('b', {}, `День ${d.day} — ${d.focus}`), h('span', { class: 'muted' }, `  ⏱ ${d.minutes} мин`),
        h('ul', { style: 'margin:6px 0' }, d.tasks.map((t) => h('li', {}, t))),
        d.checkpoint ? h('div', {}, '✅ ', h('b', {}, 'Проверка: '), d.checkpoint) : null)),
      h('p', { class: 'muted', style: 'margin:0' }, 'После плана пройди интервью заново — индекс готовности и динамика покажут прогресс.'));
  }

  function practiceView(id, task) {
    const sol = h('textarea', { placeholder: task.kind === 'code' ? 'Вставь решение (код)…' : 'Опиши решение: компоненты, потоки данных, хранилища, узкие места…', rows: 9 });
    const out = h('div', {});
    const btn = h('button', { class: 'btn btn-primary' }, 'Проверить решение');
    btn.addEventListener('click', async () => {
      const done = busyButton(btn, 'Разбираю решение…');
      try {
        const ev = await api(`/api/interviews/${id}/practice/solution`, { method: 'POST', json: { solution: sol.value } });
        out.replaceChildren(h('div', { class: 'fb ' + (ev.score >= 8 ? 'excellent' : ev.score >= 6 ? 'good' : 'needs_work') },
          h('div', { class: 'fb-head' }, h('span', { class: 'score-pill' }, `${ev.score}/10`), h('b', {}, `Критериев выполнено: ${ev.met}/${ev.criteria.length}`)),
          ev.summary ? h('p', {}, ev.summary) : null,
          h('ul', {}, ev.criteria.map((c) => h('li', {}, `${c.met ? '✅' : '❌'} `, h('b', {}, c.name), c.comment ? ` — ${c.comment}` : ''))),
          ev.improvement ? h('div', { class: 'ideal' }, h('b', {}, 'Как усилить решение\n'), ev.improvement) : null));
      } catch (e) { toast(e.message, true); }
      done();
    });
    return h('div', { class: 'panel stack' },
      h('h3', {}, `🧪 ${task.kind_label}: ${task.title}`),
      h('p', { style: 'white-space:pre-wrap;margin:0' }, task.statement),
      h('div', {}, h('b', {}, 'Критерии оценки'), h('ul', { style: 'margin:6px 0' }, task.rubric.map((c) => h('li', {}, c)))),
      task.hints.length ? h('p', { class: 'muted' }, 'Подсказки: ' + task.hints.join('; ')) : null,
      sol, h('div', {}, btn), out);
  }

  // ── личный кабинет ────────────────────────────────────────────────────────
  async function cabinetPage() {
    if (!S.user) { sessionStorage.setItem('after-login', '#/cabinet'); location.hash = '#/login'; return; }
    loading('Загружаю кабинет…');
    let c;
    try { c = await api('/api/cabinet'); } catch (e) { toast(e.message, true); location.hash = '#/'; return; }

    const st = c.stats;
    const roles = Object.keys(c.progress);
    const chartHolder = h('div', {});
    const drawChart = (role) => {
      const pts = (c.progress[role] || []).map((p, i) => ({ value: p.percentage, label: String(i + 1) }));
      chartHolder.replaceChildren(lineChart(pts, { title: `Прогресс: ${role}` }));
    };
    const select = h('select', { onchange: (e) => drawChart(e.target.value), 'aria-label': 'Роль' }, roles.map((r) => h('option', { value: r }, r)));
    if (roles.length) drawChart(roles[0]);

    const sessions = c.sessions.length
      ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
        h('thead', {}, h('tr', {}, ['Дата', 'Роль', 'Компания', 'Результат', 'Готовность'].map((t) => h('th', {}, t)))),
        h('tbody', {}, c.sessions.map((x) => h('tr', { class: 'click', tabindex: 0, onclick: () => { location.hash = `#/saved/${x.session_id}`; },
          onkeydown: (e) => { if (e.key === 'Enter') location.hash = `#/saved/${x.session_id}`; } },
        h('td', {}, fmtDate(x.ts)), h('td', {}, `${x.role}${x.level ? ' · ' + x.level : ''}`), h('td', {}, x.company || '—'),
        h('td', {}, h('b', {}, `${Math.round(x.percentage)}%`), x.summary_version < 2 ? h('span', { class: 'muted' }, ' · старая формула') : null), h('td', {}, x.readiness === null || x.readiness === undefined ? '—' : `${x.readiness}/100`))))))
      : h('p', { class: 'muted' }, 'Пока нет завершённых интервью. Самое время начать!');

    page(h('div', { class: 'container report stack' },
      h('div', { class: 'row' }, h('h2', { style: 'margin:0' }, `Привет, ${c.user}!`), h('span', { class: 'grow' }),
        h('a', { class: 'btn btn-primary', href: '#/new' }, '＋ Новое интервью')),
      h('div', { class: 'grid-3' },
        h('div', { class: 'card stat' }, h('div', { class: 'num' }, st.sessions), h('div', { class: 'muted' }, 'интервью')),
        h('div', { class: 'card stat' }, h('div', { class: 'num' }, `${st.average}%`), h('div', { class: 'muted' }, 'средний результат')),
        h('div', { class: 'card stat' }, h('div', { class: 'num' }, st.trend === null ? '—' : (st.trend > 0 ? `+${st.trend}` : st.trend)),
          h('div', { class: 'muted' }, 'динамика п.п. (с первой сессии)'))),
      roles.length ? h('div', { class: 'panel' }, h('div', { class: 'row' }, h('h3', { style: 'margin:0' }, '📈 Прогресс'), h('span', { class: 'grow' }), select), chartHolder) : null,
      h('div', { class: 'grid-2' },
        h('div', { class: 'panel' }, h('h3', {}, '🎯 Готовность к вакансиям'),
          c.companies.length ? c.companies.map((x) => h('div', { class: 'dim-row' },
            h('span', {}, x.company, h('span', { class: 'muted' }, ` · ${x.role}`)),
            h('div', { class: 'meter ' + pctClass(x.readiness) }, h('i', { style: `width:${x.readiness}%` })), h('span', {}, x.readiness)))
            : h('p', { class: 'muted' }, 'Пройди интервью по вакансии — здесь появится индекс готовности по компаниям.')),
        h('div', { class: 'panel' }, h('h3', {}, '🔁 Стоит повторить'),
          c.weak_topics.length ? h('ul', { style: 'margin:0;padding-left:20px' }, c.weak_topics.map((w) => h('li', {}, w.topic, h('span', { class: 'muted' }, ` · ${w.role}`))))
            : h('p', { class: 'muted' }, 'Слабых тем пока нет — отлично!'))),
      h('div', { class: 'panel' }, h('h3', {}, '🗂 История интервью'), sessions)));
  }

  // ── роутинг ───────────────────────────────────────────────────────────────
  async function route() {
    stopTimer();
    const hash = location.hash || '#/';
    const [, a, b] = hash.split('/');
    try {
      if (a === undefined || a === '') return landing();
      if (a === 'login') return authPage('login');
      if (a === 'register') return authPage('register');
      if (a === 'new') return wizard();
      if (a === 'interview' && b) return interviewPage(b);
      if (a === 'report' && b) return reportPage(b, false);
      if (a === 'saved' && b) return reportPage(b, true);
      if (a === 'cabinet') return cabinetPage();
      return landing();
    } catch (e) {
      toast(e.message || 'Что-то пошло не так', true);
    }
  }

  async function boot() {
    try {
      S.config = await api('/api/config');
      S.user = S.config.user;
    } catch (e) {
      root.replaceChildren(h('div', { class: 'narrow', style: 'padding:60px 0' }, h('h2', {}, 'Сервер недоступен'), h('p', { class: 'muted' }, e.message)));
      return;
    }
    window.addEventListener('hashchange', route);
    route();
  }

  boot();
})();
