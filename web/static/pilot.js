/* Блоки для пилота, подключаемые отдельно от app.js: форма отзыва в конце отчёта
   и удаление данных в кабинете. Следит за DOM и достраивает страницы.
   Текст выводится только через textContent. */
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

  async function post(path, json) {
    const res = await fetch(path, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(json),
    });
    let data = {};
    try { data = await res.json(); } catch (e) { /* пустой ответ */ }
    if (!res.ok) throw new Error(data.error || 'Не получилось. Попробуй ещё раз.');
    return data;
  }

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* приватный режим */ } },
  };

  // ── оценка 1–5 ────────────────────────────────────────────────────────────
  function ratingRow(label, onPick) {
    const btns = [];
    const row = h('div', { class: 'pilot-rate', role: 'group', 'aria-label': label });
    for (let i = 1; i <= 5; i++) {
      const b = h('button', { type: 'button', class: 'btn btn-ghost btn-sm', 'aria-label': `${i} из 5` }, String(i));
      b.addEventListener('click', () => {
        btns.forEach((x, j) => x.classList.toggle('btn-primary', j === i - 1));
        btns.forEach((x, j) => x.classList.toggle('btn-ghost', j !== i - 1));
        onPick(i);
      });
      btns.push(b);
      row.append(b);
    }
    return h('div', {}, h('div', { style: 'margin-bottom:6px' }, label), row);
  }

  function feedbackPanel(sessionId) {
    const key = `pilot-feedback-${sessionId}`;
    const box = h('div', { class: 'panel stack', id: 'pilot-feedback' });
    if (store.get(key)) {
      box.append(h('h3', {}, '💬 Спасибо за отзыв!'),
        h('p', { class: 'muted', style: 'margin:0' }, 'Он помогает нам делать Сократа лучше.'));
      return box;
    }
    const v = { rating: 0, realism: 0 };
    const comment = h('textarea', { placeholder: 'Что было непонятно, неточно или не хватило? (необязательно)', rows: 3, maxlength: 2000 });
    const send = h('button', { type: 'button', class: 'btn btn-primary' }, 'Отправить отзыв');
    send.addEventListener('click', async () => {
      if (!v.rating) { toast('Поставь общую оценку от 1 до 5.', true); return; }
      send.disabled = true;
      try {
        await post('/api/feedback', {
          session_id: sessionId, rating: v.rating,
          realism: v.realism || null, comment: comment.value,
        });
        store.set(key, '1');
        box.replaceChildren(h('h3', {}, '💬 Спасибо за отзыв!'),
          h('p', { class: 'muted', style: 'margin:0' }, 'Он помогает нам делать Сократа лучше.'));
      } catch (e) { toast(e.message, true); send.disabled = false; }
    });
    box.append(
      h('h3', { style: 'margin:0' }, '💬 Как тебе интервью?'),
      ratingRow('Насколько полезным был разбор? (1 — совсем нет, 5 — очень)', (n) => { v.rating = n; }),
      ratingRow('Вопросы были как на реальном собеседовании?', (n) => { v.realism = n; }),
      comment, h('div', {}, send));
    return box;
  }

  // ── удаление данных ───────────────────────────────────────────────────────
  function privacyPanel() {
    const pass = h('input', { type: 'password', placeholder: 'Пароль для подтверждения', autocomplete: 'current-password', maxlength: 200 });
    const account = h('input', { type: 'checkbox', id: 'pilot-del-account' });
    const btn = h('button', { type: 'button', class: 'btn btn-ghost' }, 'Удалить мои данные');
    btn.addEventListener('click', async () => {
      if (!pass.value) { toast('Введи пароль.', true); return; }
      const withAccount = account.checked;
      if (!confirm(withAccount
        ? 'Удалить все интервью, отзывы и сам аккаунт? Это необратимо.'
        : 'Удалить все сохранённые интервью и отзывы? Это необратимо.')) return;
      btn.disabled = true;
      try {
        const r = await post('/api/account/delete-data', { password: pass.value, delete_account: withAccount });
        toast(`Удалено интервью: ${r.sessions}.` + (r.account ? ' Аккаунт удалён.' : ''));
        setTimeout(() => { location.hash = '#/'; location.reload(); }, 900);
      } catch (e) { toast(e.message, true); btn.disabled = false; }
    });
    return h('details', { class: 'panel', id: 'pilot-privacy' },
      h('summary', {}, '🔒 Мои данные'),
      h('div', { class: 'stack', style: 'margin-top:12px' },
        h('p', { class: 'muted', style: 'margin:0' },
          'Можно удалить все сохранённые интервью (ответы и данные резюме) и твои отзывы. Это необратимо.'),
        h('label', {}, account, ' Удалить и аккаунт'),
        pass, h('div', {}, btn)));
  }

  // ── продолжить прерванное интервью ────────────────────────────────────────
  let activeLoaded = false;
  async function resumeBanner(page) {
    if (activeLoaded) return;
    activeLoaded = true;
    try {
      const res = await fetch('/api/interviews/active', { credentials: 'same-origin' });
      const list = res.ok ? await res.json() : [];
      if (list.length && !page.querySelector('#pilot-resume') && location.hash === '#/cabinet') {
        const iv = list[0];
        page.prepend(h('div', { class: 'info-box', id: 'pilot-resume' },
          `У тебя есть незавершённое интервью: ${iv.role}${iv.level ? ' · ' + iv.level : ''}, отвечено ${iv.answered}. `,
          h('a', { href: `#/interview/${iv.id}` }, 'Продолжить')));
      }
    } catch (e) { /* баннер необязателен */ }
    activeLoaded = false;
  }

  // ── сохранить отчёт в PDF (печать браузера) ───────────────────────────────
  function printButton(page) {
    const bar = page.querySelector('.sticky-actions');
    if (!bar || bar.querySelector('#pilot-print')) return;
    bar.append(h('button', { type: 'button', class: 'btn btn-ghost', id: 'pilot-print', onclick: () => {
      page.querySelectorAll('details.acc').forEach((d) => { d.open = true; });
      window.print();
    } }, '🖨 PDF'));
  }

  // ── достройка страниц ─────────────────────────────────────────────────────
  function enhance() {
    const page = root.querySelector('.container.report');
    if (!page) return;
    const [, a, b] = (location.hash || '').split('/');
    if ((a === 'report' || a === 'saved') && b) printButton(page);
    if ((a === 'report' || a === 'saved') && b && !page.querySelector('#pilot-feedback')) {
      page.append(feedbackPanel(b));
    } else if (a === 'cabinet') {
      if (!page.querySelector('#pilot-privacy')) page.append(privacyPanel());
      if (!page.querySelector('#pilot-resume')) resumeBanner(page);
    }
  }

  new MutationObserver(enhance).observe(root, { childList: true });
  window.addEventListener('hashchange', enhance);
  enhance();
})();
