/* Панель владельца пилота: /admin. Данные отдаёт /api/admin/*, доступ — SOKRAT_ADMIN_USERS.
   Текст выводится только через textContent. */
(() => {
  'use strict';
  const root = document.getElementById('admin');

  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v; else el.setAttribute(k, String(v));
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  const stat = (num, label) => h('div', { class: 'card stat' }, h('div', { class: 'num' }, num ?? '—'), h('div', { class: 'muted' }, label));
  const list = (rows, fmt) => rows.length ? h('ul', { style: 'margin:0;padding-left:20px' }, rows.map((r) => h('li', {}, fmt(r)))) : h('p', { class: 'muted' }, 'Пока пусто.');

  async function load(path) {
    const res = await fetch(path, { credentials: 'same-origin' });
    if (res.status === 401) throw new Error('Войди в аккаунт владельца на главной странице и обнови эту.');
    if (!res.ok) throw new Error('Нет доступа.');
    return res.json();
  }

  async function main() {
    let a, m;
    try { [a, m] = await Promise.all([load('/api/admin/analytics'), load('/api/admin/stats')]); }
    catch (e) { root.replaceChildren(h('h2', {}, 'Панель владельца'), h('p', { class: 'muted' }, e.message)); return; }

    const s = a.sessions;
    const routes = Object.entries(m.metrics.routes).sort((x, y) => y[1]['5xx'] - x[1]['5xx'] || y[1].count - x[1].count);
    const f = a.funnel, p = a.pilot;
    const pc = (v) => (v === null || v === undefined ? '—' : `${v}%`);
    const funnelPanel = h('div', { class: 'panel' }, h('h3', {}, 'Воронка и метрики пилота'),
      h('div', { class: 'grid-3' },
        stat(f.created, 'интервью создано'),
        stat(`${f.started} (${pc(f.start_rate)})`, 'начато'),
        stat(`${f.finished} (${pc(f.finish_rate)})`, 'завершено')),
      h('div', { class: 'grid-3', style: 'margin-top:12px' },
        stat(pc(p.vacancy_share), 'сессий по реальной вакансии'),
        stat(pc(p.returning_share), 'пользователей с ≥2 сессиями'),
        stat(p.avg_growth_pp === null ? '—' : `${p.avg_growth_pp > 0 ? '+' : ''}${p.avg_growth_pp} п.п.`,
          `прирост балла (1-я → 3-я сессия), n=${p.users_with_growth}`)),
      h('p', { class: 'muted', style: 'margin:12px 0 0' },
        `Средняя сессия: ${p.avg_minutes ?? '—'} мин, ${p.avg_questions ?? '—'} вопросов. Отзыв оставляют ${pc(f.feedback_rate)} завершивших.`));
    root.replaceChildren(
      h('div', { class: 'row' }, h('h2', { style: 'margin:0' }, 'Панель владельца'), h('span', { class: 'grow' }), h('a', { class: 'btn btn-plain', href: '/' }, 'На сайт')),
      funnelPanel,
      h('div', { class: 'grid-3' },
        stat(s.sessions, 'завершённых интервью'),
        stat(s.unique_users, 'пользователей с аккаунтом'),
        stat(s.sessions ? `${s.avg_percentage}%` : '—', 'средний результат')),
      h('div', { class: 'grid-3' },
        stat(a.feedback.count, 'отзывов'),
        stat(a.feedback.avg_rating, 'полезность разбора (1–5)'),
        stat(a.feedback.avg_realism, 'как на реальном собесе (1–5)')),
      h('div', { class: 'grid-2' },
        h('div', { class: 'panel' }, h('h3', {}, 'Где проседают'), list(s.weak_skills, ([k, n]) => `${k} — ${n} сесс.`)),
        h('div', { class: 'panel' }, h('h3', {}, 'Роли'), list(s.roles, ([k, n]) => `${k} — ${n}`))),
      h('div', { class: 'panel' }, h('h3', {}, 'Слабые вопросы (больше 👎, чем 👍)'),
        list(a.weak_questions, (q) => `${q.question} — 👍${q.up} / 👎${q.down}`)),
      h('div', { class: 'panel' }, h('h3', {}, 'Комментарии тестеров'),
        list(a.comments, (c) => `[${c.rating}/5${c.realism ? `, реализм ${c.realism}/5` : ''}] ${c.comment}`)),
      h('div', { class: 'panel' }, h('h3', {}, `Запросы и ошибки · аптайм ${Math.round(m.metrics.uptime_s / 60)} мин`),
        h('table', { class: 'table' },
          h('thead', {}, h('tr', {}, ['Эндпоинт', 'Запросов', '4xx', '5xx', 'Ср. сек', 'Макс. сек'].map((t) => h('th', {}, t)))),
          h('tbody', {}, routes.map(([name, r]) => h('tr', {}, h('td', {}, name), h('td', {}, r.count), h('td', {}, r['4xx']), h('td', {}, r['5xx']), h('td', {}, r.avg_s), h('td', {}, r.max_s)))))));
  }
  main();
})();
