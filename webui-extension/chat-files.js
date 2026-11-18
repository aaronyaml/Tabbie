/* Tabbie: "Files in this chat" (plain JS, no build step).
 * A Files button beside Agents opens a sheet with everything attached in the open chat: your uploads
 * ("[Attached files: ...]") and files Tabbie attached to replies (MEDIA: /path). Paths merely mentioned in
 * text are left out. Reads the whole chat from the server (not just the rendered window), newest first.
 * Pictures show as a grid; other files as rows. Opening one uses the file viewer from files.js.
 */
(() => {
  'use strict';
  if (window.__hxChatFiles) return;
  window.__hxChatFiles = true;

  const icon = (name, size) => (typeof window.hxIcon === 'function' ? window.hxIcon(name, size) : '');
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const enc = encodeURIComponent;
  const MARKER = /\[Attached files: ([^\]]+)\]\s*$/;
  const MEDIA = /MEDIA:\s*(\/[^\s)\]`'"<>]+)/g;
  const IMG = /\.(png|jpe?g|gif|webp|bmp|avif|svg)$/i;
  const sid = () => { try { return (S.session && S.session.session_id) || ''; } catch (_) { return ''; } };
  const base = (p) => p.split('/').pop() || p;
  const ext = (p) => ((/\.([a-z0-9]{1,6})$/i.exec(p) || [])[1] || 'file').toUpperCase();
  const textOf = (c) => (typeof c === 'string' ? c : Array.isArray(c) ? c.map((x) => (x && x.text) || '').join(' ') : '');

  // ── data ────────────────────────────────────────────────────────────────
  const cache = new Map(); // sid -> { at, count, items }
  async function load(id) {
    const msgs = [];
    let before = null;
    for (let page = 0; page < 6; page++) {
      const q = `api/session?session_id=${enc(id)}&msg_limit=500&resolve_model=0${before != null ? `&msg_before=${before}` : ''}`;
      const r = await fetch(q, { credentials: 'same-origin' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const s = (await r.json()).session || {};
      msgs.unshift(...(s.messages || []));
      if (!s._messages_truncated || !s._messages_offset) break;
      before = s._messages_offset;
    }
    const seen = new Set();
    const items = [];
    msgs.forEach((m, i) => {
      if (!m || (m.role !== 'user' && m.role !== 'assistant')) return;
      const t = textOf(m.content);
      const paths = [];
      if (m.role === 'user') {
        const k = MARKER.exec(t);
        if (k) paths.push(...k[1].split(/,\s+(?=\/)/).map((x) => x.trim()));
      } else {
        for (const x of t.matchAll(MEDIA)) paths.push(x[1].replace(/[.,;:]+$/, ''));
      }
      paths.forEach((p) => {
        if (!p || seen.has(p)) return;
        seen.add(p);
        items.push({ path: p, name: base(p), who: m.role === 'user' ? 'You' : 'Tabbie', i, ts: m.timestamp });
      });
    });
    return items.reverse();
  }
  const url = (p, id) => `api/media?path=${enc(p)}&session_id=${enc(id)}`;

  // ── button ──────────────────────────────────────────────────────────────
  let btn = null;
  function ensureBtn() {
    if (btn) return btn;
    btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'hx-agents-pill hx-files-pill';
    btn.setAttribute('aria-label', 'Files in this chat');
    btn.title = 'Files in this chat';
    btn.innerHTML = `${icon('Image01Icon', 16)}<span class="hx-pill-tx">Files</span>`;
    btn.addEventListener('click', () => openSheet(true));
    document.body.appendChild(btn);
    return btn;
  }
  function place() {
    const b = ensureBtn();
    const on = !!sid() && !!document.getElementById('messages');
    b.hidden = !on;
    if (!on) return;
    const agents = document.querySelector('.hx-agents-pill:not(.hx-files-pill)');
    const agentsShown = agents && !agents.hidden;
    const nb = document.getElementById('btnTitlebarNewChat');
    const bar = nb && nb.closest('.app-titlebar');
    if (bar && bar.offsetParent !== null && getComputedStyle(bar).display !== 'none') { // phone: in the top bar
      const after = agentsShown && agents.parentElement === bar ? agents : nb;
      if (b.nextElementSibling !== after) after.before(b);
      b.classList.add('is-inbar'); b.style.top = b.style.right = '';
      return;
    }
    if (b.parentElement !== document.body) document.body.appendChild(b);
    b.classList.remove('is-inbar');
    const msgs = document.getElementById('messages');
    const r = msgs.getBoundingClientRect();
    let right = Math.max(12, window.innerWidth - r.right + 16);
    if (agentsShown) right = window.innerWidth - agents.getBoundingClientRect().left + 8;
    b.style.top = Math.max(10, r.top + 12) + 'px';
    b.style.right = right + 'px';
  }

  // ── sheet ───────────────────────────────────────────────────────────────
  let sheet = null;
  function openSheet(on) {
    if (!sheet) {
      sheet = document.createElement('div');
      sheet.className = 'hx-as hx-fs';
      sheet.innerHTML = '<div class="hx-as-scrim"></div><aside class="hx-as-panel" role="dialog" aria-modal="true" aria-label="Files in this chat">'
        + `<header class="hx-as-head"><button type="button" class="hx-back hx-as-close" aria-label="Back to chat">${icon('ArrowLeft01Icon', 18)}<span>Back</span></button>`
        + '<div class="hx-as-title">Files</div><div class="hx-as-sub"></div></header><div class="hx-as-body"></div></aside>';
      sheet.querySelector('.hx-as-scrim').addEventListener('click', () => openSheet(false));
      sheet.querySelector('.hx-as-close').addEventListener('click', () => openSheet(false));
      sheet.addEventListener('click', (e) => {
        const o = e.target.closest('[data-open]');
        if (!o || e.target.closest('a')) return;
        const p = o.getAttribute('data-open');
        openSheet(false);
        if (typeof window.hxOpenFile === 'function') window.hxOpenFile(p, sid());
      });
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && sheet.classList.contains('is-open')) openSheet(false); });
      document.body.appendChild(sheet);
    }
    sheet.classList.toggle('is-open', on);
    if (on) render();
  }

  async function render() {
    const id = sid();
    const body = sheet.querySelector('.hx-as-body');
    const sub = sheet.querySelector('.hx-as-sub');
    const c = cache.get(id);
    let count = null;
    try { count = S.session && S.session.message_count; } catch (_) {}
    if (c) paint(c.items, id);
    else {
      sub.textContent = '';
      body.innerHTML = '<div class="hx-fs-grid">' + '<span class="hx-fs-sk"></span>'.repeat(6) + '</div>'
        + '<div class="hx-fs-list">' + '<span class="hx-fs-sk row"></span>'.repeat(3) + '</div>';
    }
    if (c && c.count === count) return;
    try {
      const items = await load(id);
      cache.set(id, { items, count });
      if (sid() === id && sheet.classList.contains('is-open')) paint(items, id);
    } catch (_) {
      if (!c) body.innerHTML = '<div class="hx-as-empty"><p>Couldn’t load the files for this chat. Try again in a moment.</p></div>';
    }
  }

  function paint(items, id) {
    const body = sheet.querySelector('.hx-as-body');
    const sub = sheet.querySelector('.hx-as-sub');
    sub.textContent = items.length ? `${items.length} ${items.length === 1 ? 'file' : 'files'}` : '';
    if (!items.length) {
      body.innerHTML = '<div class="hx-as-empty"><p>No files in this chat yet. Pictures and files you or Tabbie attach show up here.</p></div>';
      return;
    }
    const pics = items.filter((x) => IMG.test(x.name));
    const rest = items.filter((x) => !IMG.test(x.name));
    let html = '';
    if (pics.length) {
      html += `<section class="hx-ad-sec"><h3 class="hx-ad-h">Pictures<span>${pics.length}</span></h3><div class="hx-fs-grid">`
        + pics.map((x) => `<button type="button" class="hx-fs-tile" data-open="${esc(x.path)}" aria-label="${esc(x.name)}" title="${esc(x.name)}"><img alt="" loading="lazy" src="${esc(url(x.path, id))}"></button>`).join('')
        + '</div></section>';
    }
    if (rest.length) {
      html += `<section class="hx-ad-sec"><h3 class="hx-ad-h">Files<span>${rest.length}</span></h3><div class="hx-fs-list">`
        + rest.map((x) => `<div class="hx-fs-row" role="button" tabindex="0" data-open="${esc(x.path)}"><span class="hx-fs-ic">${icon('File01Icon', 18)}</span>`
          + `<span class="hx-fs-tx"><span class="hx-fs-nm" title="${esc(x.name)}">${esc(x.name)}</span><span class="hx-fs-mt">${esc(ext(x.name))} · ${esc(x.who)}</span></span>`
          + `<a class="hx-fs-dl" href="${esc(url(x.path, id))}" download="${esc(x.name)}" aria-label="Download ${esc(x.name)}" title="Download">${icon('Download04Icon', 16)}</a></div>`).join('')
        + '</div></section>';
    }
    body.innerHTML = html;
    body.querySelectorAll('.hx-fs-tile img').forEach((img) => img.addEventListener('error', () => {
      img.closest('.hx-fs-tile').classList.add('is-missing');
      img.replaceWith(Object.assign(document.createElement('span'), { textContent: 'Unavailable' }));
    }, { once: true }));
  }

  // ── wiring ──────────────────────────────────────────────────────────────
  let raf = 0;
  const schedule = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; place(); }); };
  const mo = new MutationObserver(schedule);
  function start() {
    place();
    mo.observe(document.body, { childList: true, subtree: false, attributes: true, attributeFilter: ['class'] });
    const agentsWatch = new MutationObserver(schedule);
    const hook = () => { const a = document.querySelector('.hx-agents-pill:not(.hx-files-pill)'); if (a && !a.__hxFw) { a.__hxFw = 1; agentsWatch.observe(a, { attributes: true, attributeFilter: ['hidden', 'style', 'class'] }); } };
    setInterval(() => { hook(); place(); }, 1500);
    window.addEventListener('resize', schedule);
    window.addEventListener('popstate', schedule);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true }); else start();
})();
