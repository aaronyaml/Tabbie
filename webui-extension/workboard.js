/* Hermes — workboard embeds (plain JS, no build step).
 *  Store: Hermes' kanban DB through the WebUI's own /api/kanban/* routes. Kanban cards live on the
 *  "default" board, todos on the "todos" board (both written by the workboard plugin as well), so
 *  the WebUI Kanban panel shows the same items live.
 *  1. Chat: `[[kanban:<id>]]` / `[[todo:<id>]]` (optional `|label`) in a reply -> a compact live card
 *     (title, status, column, priority, due). Todo cards have a check-off circle.
 *  2. Clicking a card opens an editor sheet: title, column/status, priority, due, tags, description,
 *     notes, done/reopen, archive. Saves go straight to the store.
 *  3. Todo panel: while the session's Todo panel has no list of its own (or shows a workboard list),
 *     it mirrors the todos board; its rows open the editor.
 *  Polls /api/kanban/board?since=<event id> every few seconds while anything is on screen.
 */
(() => {
  'use strict';
  if (window.__hxWorkboard) return;
  window.__hxWorkboard = true;

  const BOARD = { kanban: 'default', todo: 'todos' };
  const TOKEN_RX = /\[\[(kanban|todo):(t_[0-9a-f]{4,})(?:\|[^\]\n]*)?\]\]/g;
  const LABEL = { triage: 'Triage', todo: 'To do', scheduled: 'Scheduled', ready: 'Ready', running: 'Running',
    blocked: 'Blocked', done: 'Done', archived: 'Archived' };
  // Writable columns are ready/scheduled/blocked/done/archived: on Hermes' kanban DB `todo` auto-promotes
  // to ready, `triage` is auto-decomposed and `running` belongs to the dispatcher (see store.py).
  const TODO_LABEL = { todo: 'To do', triage: 'To do', scheduled: 'Later', blocked: 'Blocked', ready: 'To do',
    running: 'Doing', done: 'Done', archived: 'Cancelled' };
  const TODO_STATUS = { todo: 'pending', triage: 'pending', scheduled: 'pending', blocked: 'pending',
    ready: 'pending', running: 'in_progress', done: 'completed', archived: 'cancelled' };
  const PRIO = [[0, 'None'], [1, 'Low'], [2, 'Medium'], [3, 'High'], [4, 'Urgent']];
  const POLL_MS = 4000;

  const cache = { kanban: new Map(), todo: new Map() };
  const latest = { kanban: null, todo: null };
  const loaded = { kanban: false, todo: false };
  const missing = { kanban: false, todo: false };

  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const toast = (m, k) => { if (typeof showToast === 'function') showToast(m, 2600, k); };

  function call(path, method, body) {
    const opts = { method: method || 'GET' };
    if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers = { 'Content-Type': 'application/json' }; }
    return fetch(path.replace(/^\//, ''), { credentials: 'same-origin', ...opts }).then(async (r) => {
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { const e = new Error(j.error || r.statusText); e.status = r.status; throw e; }
      return j;
    });
  }
  const q = (kind) => 'board=' + encodeURIComponent(BOARD[kind]);

  // ── body footer: "Due: YYYY-MM-DD" / "Tags: a, b" (mirrors plugin/workboard/store.py) ───
  const FOOT = /^(Due|Tags):[ \t]*(.*)$/i;
  function splitBody(body) {
    const lines = String(body || '').replace(/\s+$/, '').split('\n');
    let due = null, tags = [];
    while (lines.length && FOOT.test(lines[lines.length - 1].trim())) {
      const [, k, v] = FOOT.exec(lines.pop().trim());
      if (k.toLowerCase() === 'due') due = v.trim() || null;
      else tags = v.split(/[,\s]+/).map((t) => t.trim().replace(/^#/, '')).filter(Boolean);
    }
    return { description: lines.join('\n').replace(/\s+$/, ''), due, tags };
  }
  function joinBody(description, due, tags) {
    const foot = [];
    if (due) foot.push('Due: ' + due);
    if (tags && tags.length) foot.push('Tags: ' + tags.join(', '));
    const d = String(description || '').replace(/\s+$/, '');
    return d + (foot.length ? (d ? '\n\n' : '') + foot.join('\n') : '');
  }

  // ── data ─────────────────────────────────────────────────────────────────
  async function loadBoard(kind, force) {
    let path = 'api/kanban/board?' + q(kind) + '&include_archived=1';
    if (!force && latest[kind] != null) path += '&since=' + latest[kind];
    let data;
    try { data = await call(path); } catch (e) {
      if (e.status === 404) { missing[kind] = true; loaded[kind] = true; return false; }
      throw e;
    }
    missing[kind] = false;
    if (data.latest_event_id != null) latest[kind] = data.latest_event_id;
    if (data.changed === false && loaded[kind]) return false;
    const m = new Map();
    for (const col of data.columns || []) for (const t of col.tasks || []) m.set(t.id, t);
    cache[kind] = m;
    loaded[kind] = true;
    return true;
  }
  async function item(kind, id) {
    if (!loaded[kind] || (!cache[kind].has(id) && !missing[kind])) await loadBoard(kind, true).catch(() => {});
    return cache[kind].get(id) || null;
  }

  async function patch(kind, id, body) {
    const r = await call('api/kanban/tasks/' + encodeURIComponent(id) + '?' + q(kind), 'PATCH', body);
    if (r.task) cache[kind].set(id, { ...(cache[kind].get(id) || {}), ...r.task });
    return r.task;
  }
  // Status changes follow the Agent's verbs: done/blocked need a ready card first.
  async function setStatus(kind, id, target) {
    const t = await item(kind, id);
    const cur = t ? t.status : null;
    if (!t || cur === target) return t;
    if (target === 'done' && !['ready', 'running', 'blocked', 'review'].includes(cur)) await patch(kind, id, { status: 'ready' });
    if (target === 'blocked' && !['ready', 'running'].includes(cur)) await patch(kind, id, { status: 'ready' });
    if (target === 'scheduled' && ['done', 'archived'].includes(cur)) await patch(kind, id, { status: 'ready' });
    const body = { status: target };
    if (target === 'done') body.result = 'Marked done in the WebUI';
    if (target === 'blocked') body.reason = 'Blocked in the WebUI';
    return patch(kind, id, body);
  }

  // ── rendering: chat embeds ───────────────────────────────────────────────
  function statusLabel(kind, s) { return (kind === 'todo' ? TODO_LABEL : LABEL)[s] || s || ''; }
  function metaHtml(kind, t) {
    const { due, tags } = splitBody(t.body);
    const bits = [`<span class="hx-wb-pill" data-s="${esc(t.status)}">${esc(statusLabel(kind, t.status))}</span>`];
    if (t.priority) bits.push(`<span class="hx-wb-prio" data-p="${Math.min(4, t.priority)}">${icon('Flag02Icon', 12)}${esc((PRIO.find((p) => p[0] === Math.min(4, t.priority)) || [0, 'P' + t.priority])[1])}</span>`);
    if (due) bits.push(`<span class="hx-wb-due${overdue(due, t) ? ' is-late' : ''}">${icon('Calendar03Icon', 12)}${esc(fmtDue(due))}</span>`);
    if (tags.length) bits.push(`<span class="hx-wb-tags">${tags.slice(0, 3).map((x) => '#' + esc(x)).join(' ')}</span>`);
    return bits.join('');
  }
  function overdue(due, t) {
    if (t.status === 'done' || t.status === 'archived') return false;
    const d = new Date(due.slice(0, 10) + 'T23:59:59');
    return !isNaN(d) && d < new Date();
  }
  function fmtDue(due) {
    const d = new Date(due.slice(0, 10) + 'T12:00:00');
    if (isNaN(d)) return due;
    const today = new Date(); today.setHours(12, 0, 0, 0);
    const diff = Math.round((d - today) / 86400000);
    if (diff === 0) return 'Today';
    if (diff === 1) return 'Tomorrow';
    if (diff === -1) return 'Yesterday';
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', ...(d.getFullYear() !== today.getFullYear() ? { year: 'numeric' } : {}) });
  }
  function paint(el) {
    const kind = el.dataset.kind, id = el.dataset.id;
    const t = cache[kind].get(id);
    const label = el.dataset.label || '';
    const key = t ? [t.title, t.status, t.priority, t.body].join('\x1f') : (loaded[kind] ? '__gone' : '__load');
    if (el.dataset.key === key) return;
    el.dataset.key = key;
    el.classList.toggle('is-done', !!t && (t.status === 'done' || t.status === 'archived'));
    el.classList.toggle('is-gone', !t && loaded[kind]);
    const lead = kind === 'todo'
      ? `<button type="button" class="hx-wb-check" aria-label="${t && t.status === 'done' ? 'Reopen' : 'Check off'}">${icon(t && t.status === 'done' ? 'CheckmarkCircle02Icon' : 'CircleIcon', 20)}</button>`
      : `<span class="hx-wb-ic">${icon('KanbanIcon', 18)}</span>`;
    const title = t ? t.title : (label || id);
    const meta = t ? metaHtml(kind, t) : `<span>${loaded[kind] ? 'Not found' : 'Loading…'}</span>`;
    el.innerHTML = `${lead}<span class="hx-wb-tx"><span class="hx-wb-tt">${esc(title)}</span><span class="hx-wb-mt">${meta}</span></span>`
      + `<span class="hx-wb-go">${icon('ArrowRight01Icon', 16)}</span>`;
    el.setAttribute('aria-label', `${kind === 'todo' ? 'Todo' : 'Kanban card'}: ${title}${t ? ', ' + statusLabel(kind, t.status) : ''}`);
  }
  function embedFor(kind, id, label) {
    const el = document.createElement('div');
    el.className = 'hx-wb';
    el.dataset.kind = kind; el.dataset.id = id;
    if (label) el.dataset.label = label;
    el.tabIndex = 0;
    el.setAttribute('role', 'button');
    paint(el);
    return el;
  }
  function tokens(body) {
    if (!body.textContent.includes('[[')) return;
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.nodeValue.includes('[[') && !(n.parentElement && n.parentElement.closest('pre, code, textarea, .hx-wb'))
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
    });
    const hits = [];
    while (walker.nextNode()) hits.push(walker.currentNode);
    const need = new Set();
    hits.forEach((node) => {
      const text = node.nodeValue;
      TOKEN_RX.lastIndex = 0;
      if (!TOKEN_RX.test(text)) return;
      TOKEN_RX.lastIndex = 0;
      const frag = document.createDocumentFragment();
      let last = 0, m;
      while ((m = TOKEN_RX.exec(text))) {
        if (m.index > last) frag.append(text.slice(last, m.index));
        const label = (/\|([^\]]*)\]\]$/.exec(m[0]) || [])[1];
        frag.append(embedFor(m[1], m[2], label));
        need.add(m[1]);
        last = m.index + m[0].length;
      }
      const rest = text.slice(last);
      if (rest.trim()) frag.append(rest);
      const parent = node.parentElement;
      node.replaceWith(frag);
      // A list item / paragraph holding only embeds loses its bullet and margins.
      if (parent && /^(LI|P)$/.test(parent.tagName) && ![...parent.childNodes].some((c) => (c.nodeType === 3 && c.nodeValue.trim()) || (c.nodeType === 1 && !c.classList.contains('hx-wb') && c.tagName !== 'BR'))) {
        parent.classList.add('hx-wb-host');
        const list = parent.parentElement;
        if (parent.tagName === 'LI' && list && /^(UL|OL)$/.test(list.tagName) && [...list.children].every((li) => li.classList.contains('hx-wb-host'))) list.classList.add('hx-wb-list');
      }
    });
    need.forEach((k) => { if (!loaded[k]) refresh(k, true); });
  }
  function repaintAll() {
    document.querySelectorAll('.hx-wb').forEach(paint);
    if (E && !E.hidden && E.dataset.id) fillHead();
  }

  // ── phone: pull the editor down to dismiss it (the model picker's drag, copied). Only once the body is
  // scrolled to the top, never from a field or the status buttons, and only on a mostly vertical pull.
  // Wired once: the editor element is created a single time and reused.
  const phoneW = () => window.matchMedia('(max-width: 640px)').matches;
  const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function swipeDown(panel, body, onClose) {
    let y0 = null, x0 = 0, dy = 0, live = false;
    const reset = () => { panel.style.transition = ''; panel.style.transform = ''; };
    panel.addEventListener('touchstart', (e) => {
      y0 = null; live = false; dy = 0;
      if (!phoneW() || e.touches.length !== 1) return;
      const t = e.target;
      if (t.closest && t.closest('input, textarea, select, [contenteditable], .hx-wb-seg')) return;
      if (body && body.contains(t) && body.scrollTop > 0) return;
      y0 = e.touches[0].clientY; x0 = e.touches[0].clientX;
    }, { passive: true });
    panel.addEventListener('touchmove', (e) => {
      if (y0 == null) return;
      const t = e.touches[0];
      dy = Math.max(0, t.clientY - y0);
      if (!live) {
        if (Math.abs(t.clientX - x0) > 12 && Math.abs(t.clientX - x0) > dy) { y0 = null; return; }
        if (dy < 8) return;
        if (body && body.scrollTop > 0) { y0 = null; return; }
        live = true;
      }
      if (reduced()) return;
      panel.style.transition = 'none';
      panel.style.transform = `translateY(${dy}px)`;
    }, { passive: true });
    panel.addEventListener('touchend', () => {
      if (y0 == null) return;
      y0 = null;
      const go = live && dy > 90;
      live = false;
      if (!go) { reset(); return; }
      if (reduced()) { onClose(); reset(); return; }
      panel.style.transition = 'transform .2s var(--hx-ease, ease)';
      panel.style.transform = 'translateY(100%)';
      setTimeout(() => { onClose(); reset(); }, 180);
    });
    panel.addEventListener('touchcancel', () => { y0 = null; live = false; reset(); });
  }

  // ── editor sheet ─────────────────────────────────────────────────────────
  let E = null, cur = null; // cur = {kind, id}
  function sheet() {
    if (E) return E;
    E = document.createElement('div');
    E.className = 'hx-wb-ed';
    E.hidden = true;
    E.setAttribute('role', 'dialog');
    E.setAttribute('aria-modal', 'true');
    E.setAttribute('aria-labelledby', 'hxWbTitle');
    E.innerHTML = `<div class="hx-wb-scrim"></div><form class="hx-wb-sheet" novalidate>
      <header class="hx-wb-head"><span class="hx-wb-hic"></span><div class="hx-wb-htx"><div class="hx-wb-hk" id="hxWbTitle"></div><div class="hx-wb-hm"></div></div>
        <button type="button" class="hx-wb-btn hx-wb-x" aria-label="Close">${icon('Cancel01Icon', 18)}</button></header>
      <div class="hx-wb-body">
        <label class="hx-wb-f hx-wb-f--title"><span>Title</span><input name="title" autocomplete="off" required></label>
        <div class="hx-wb-f"><span>Status</span><div class="hx-wb-seg" role="radiogroup"></div></div>
        <div class="hx-wb-row">
          <label class="hx-wb-f hx-wb-f--prio"><span>Priority</span><select name="priority">${PRIO.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select></label>
          <label class="hx-wb-f"><span>Due</span><input name="due" type="date"></label>
        </div>
        <label class="hx-wb-f"><span>Tags</span><input name="tags" placeholder="Comma separated" autocomplete="off"></label>
        <label class="hx-wb-f"><span>Description</span><textarea name="description" rows="4" placeholder="Add details"></textarea></label>
        <div class="hx-wb-f"><span>Notes</span><ol class="hx-wb-notes"></ol>
          <div class="hx-wb-add"><input name="note" placeholder="Add a note" autocomplete="off"><button type="button" class="hx-wb-btn hx-wb-addbtn" aria-label="Add note">${icon('SentIcon', 18)}</button></div></div>
      </div>
      <footer class="hx-wb-foot">
        <button type="button" class="hx-wb-ghost hx-wb-del">${icon('Delete02Icon', 16)}<span>Delete</span></button>
        <span class="hx-wb-sp"></span>
        <button type="button" class="hx-wb-ghost hx-wb-done"></button>
        <button type="submit" class="hx-wb-cta">Save</button>
      </footer></form>`;
    document.body.appendChild(E);
    E.querySelector('.hx-wb-scrim').addEventListener('click', closeEd);
    E.querySelector('.hx-wb-x').addEventListener('click', closeEd);
    E.querySelector('form').addEventListener('submit', (e) => { e.preventDefault(); save(); });
    E.querySelector('.hx-wb-del').addEventListener('click', () => act('archived', 'Deleted (archived)'));
    E.querySelector('.hx-wb-done').addEventListener('click', () => {
      const t = cache[cur.kind].get(cur.id);
      act(t && (t.status === 'done' || t.status === 'archived') ? 'ready' : 'done', null);
    });
    E.querySelector('.hx-wb-addbtn').addEventListener('click', addNote);
    E.querySelector('input[name="note"]').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addNote(); } });
    swipeDown(E.querySelector('.hx-wb-sheet'), E.querySelector('.hx-wb-body'), closeEd);
    E.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); closeEd(); }
      if (e.key === 'Tab') {
        const f = [...E.querySelectorAll('button, input, select, textarea')].filter((x) => x.offsetParent && !x.disabled);
        if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
      }
    });
    return E;
  }
  function segOptions(kind) {
    return kind === 'todo'
      ? [['ready', 'To do'], ['scheduled', 'Later'], ['done', 'Done']]
      : [['ready', 'Ready'], ['scheduled', 'Scheduled'], ['blocked', 'Blocked'], ['done', 'Done']];
  }
  function fillHead() {
    const t = cache[cur.kind].get(cur.id);
    if (!t) return;
    E.querySelector('.hx-wb-hm').textContent = `${cur.kind === 'todo' ? 'Todo' : 'Kanban'} · ${statusLabel(cur.kind, t.status)} · ${t.id}`;
    const done = t.status === 'done' || t.status === 'archived';
    E.querySelector('.hx-wb-done').innerHTML = done ? `${icon('Undo02Icon', 16)}<span>Reopen</span>` : `${icon('Tick02Icon', 16)}<span>${cur.kind === 'todo' ? 'Check off' : 'Mark done'}</span>`;
    E.querySelector('.hx-wb-del').hidden = t.status === 'archived';
  }
  async function openEd(kind, id, trigger) {
    sheet();
    cur = { kind, id, trigger };
    const t = await item(kind, id);
    if (!t) { toast('That item no longer exists', 'error'); return; }
    const f = E.querySelector('form');
    const { description, due, tags } = splitBody(t.body);
    E.querySelector('.hx-wb-hic').innerHTML = icon(kind === 'todo' ? 'CheckListIcon' : 'KanbanIcon', 20);
    E.querySelector('.hx-wb-hk').textContent = kind === 'todo' ? 'Todo' : 'Kanban card';
    f.title.value = t.title || '';
    f.priority.value = String(Math.min(4, t.priority || 0));
    f.querySelector('.hx-wb-f--prio').hidden = kind === 'todo';
    f.due.value = (due || '').slice(0, 10);
    f.tags.value = tags.join(', ');
    f.description.value = description;
    f.note.value = '';
    const seg = E.querySelector('.hx-wb-seg');
    const opts = segOptions(kind);
    const sel = opts.some(([v]) => v === t.status) ? t.status : ({ todo: 'ready', triage: 'ready', running: 'ready' }[t.status] || t.status);
    seg.innerHTML = opts.map(([v, l]) => `<button type="button" role="radio" data-v="${v}" aria-checked="${v === sel}">${l}</button>`).join('')
      + (opts.some(([v]) => v === sel) ? '' : `<button type="button" role="radio" data-v="${esc(sel)}" aria-checked="true">${esc(statusLabel(kind, sel))}</button>`);
    seg.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
      seg.querySelectorAll('button').forEach((x) => x.setAttribute('aria-checked', String(x === b)));
    }));
    fillHead();
    E.hidden = false;
    document.documentElement.classList.add('hx-wb-open');
    loadNotes();
    requestAnimationFrame(() => { if (window.matchMedia('(hover: hover)').matches) f.title.focus(); else E.querySelector('.hx-wb-x').focus(); });
  }
  function closeEd() {
    if (!E || E.hidden) return;
    E.hidden = true;
    document.documentElement.classList.remove('hx-wb-open');
    if (cur && cur.trigger && document.contains(cur.trigger)) cur.trigger.focus();
  }
  async function loadNotes() {
    const ol = E.querySelector('.hx-wb-notes');
    ol.innerHTML = '';
    try {
      const d = await call('api/kanban/tasks/' + encodeURIComponent(cur.id) + '?' + q(cur.kind));
      const notes = d.comments || [];
      ol.innerHTML = notes.length ? notes.map((c) => `<li><div class="hx-wb-nb">${esc(c.body)}</div><div class="hx-wb-nm">${esc(c.author || '')} · ${esc(new Date((c.created_at || 0) * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }))}</div></li>`).join('')
        : '<li class="hx-wb-none">No notes yet</li>';
    } catch (_) { ol.innerHTML = '<li class="hx-wb-none">Notes unavailable</li>'; }
  }
  async function addNote() {
    const inp = E.querySelector('input[name="note"]');
    const body = inp.value.trim();
    if (!body) return;
    try {
      await call('api/kanban/tasks/' + encodeURIComponent(cur.id) + '/comments?' + q(cur.kind), 'POST', { body, author: 'you' });
      inp.value = '';
      loadNotes();
    } catch (e) { toast('Could not add note: ' + e.message, 'error'); }
  }
  async function save() {
    const f = E.querySelector('form');
    const t = cache[cur.kind].get(cur.id);
    const title = f.title.value.trim();
    if (!title) { f.title.focus(); toast('Title is required', 'error'); return; }
    const tags = f.tags.value.split(/[,\s]+/).map((x) => x.trim().replace(/^#/, '')).filter(Boolean);
    const body = joinBody(f.description.value, f.due.value || null, tags);
    const fields = {};
    if (title !== t.title) fields.title = title;
    if (body !== (t.body || '')) fields.body = body;
    if (cur.kind === 'kanban' && Number(f.priority.value) !== Math.min(4, t.priority || 0)) fields.priority = Number(f.priority.value);
    const btn = f.querySelector('.hx-wb-cta');
    btn.disabled = true;
    try {
      if (Object.keys(fields).length) await patch(cur.kind, cur.id, fields);
      const chosen = (E.querySelector('.hx-wb-seg [aria-checked="true"]') || {}).dataset;
      if (chosen && chosen.v && chosen.v !== t.status) await setStatus(cur.kind, cur.id, chosen.v);
      await afterWrite(cur.kind);
      closeEd();
      toast('Saved');
    } catch (e) { toast('Could not save: ' + e.message, 'error'); }
    finally { btn.disabled = false; }
  }
  async function act(target, msg) {
    try {
      await setStatus(cur.kind, cur.id, target);
      await afterWrite(cur.kind);
      if (target === 'archived') closeEd(); else { fillHead(); openEd(cur.kind, cur.id, cur.trigger); }
      if (msg) toast(msg);
    } catch (e) { toast('Could not update: ' + e.message, 'error'); }
  }
  async function afterWrite(kind) {
    await loadBoard(kind, true).catch(() => {});
    repaintAll();
    if (kind === 'todo') syncTodoPanel();
    if (kind === 'kanban' && typeof loadKanban === 'function' && document.querySelector('#panelKanban.active, .panel-view.active #kanbanBoard')) {
      try { loadKanban(); } catch (_) {}
    }
  }

  // ── Todo panel mirror ────────────────────────────────────────────────────
  function workboardTodos() {
    return [...cache.todo.values()].filter((t) => t.status !== 'archived')
      .sort((a, b) => (a.status === 'done') - (b.status === 'done') || (a.created_at || 0) - (b.created_at || 0))
      .map((t) => ({ id: t.id, content: t.title, status: TODO_STATUS[t.status] || 'pending', source: 'workboard' }));
  }
  function syncTodoPanel() {
    if (typeof S !== 'object' || !S || !loaded.todo) return;
    const curTodos = Array.isArray(S.todos) ? S.todos : [];
    const ours = (S.todoStateMeta && S.todoStateMeta.source === 'workboard') || (curTodos.length && curTodos.every((t) => t && t.source === 'workboard'));
    let legacy = [];
    if (!S.todoStateMeta && !curTodos.length && typeof _legacyTodosFromMessages === 'function') {
      try { legacy = _legacyTodosFromMessages() || []; } catch (_) { legacy = []; }
    }
    const empty = !curTodos.length && !legacy.length;
    if (!ours && !empty && !legacy.every((t) => t && t.source === 'workboard')) return;
    const next = workboardTodos();
    const hash = JSON.stringify(next);
    if (S.__hxWbHash === hash && S.todoStateMeta && S.todoStateMeta.source === 'workboard') return;
    S.todos = next;
    S.todoStateMeta = { ts: Date.now() / 1000, source: 'workboard', version: 1 };
    S.__hxWbHash = hash;
    if (typeof _resetTodosRenderCache === 'function') _resetTodosRenderCache();
    if (typeof scheduleTodosRefresh === 'function') scheduleTodosRefresh();
  }

  // ── polling ──────────────────────────────────────────────────────────────
  const busy = { kanban: false, todo: false };
  async function refresh(kind, force) {
    if (busy[kind]) return;
    busy[kind] = true;
    try { if (await loadBoard(kind, force)) repaintAll(); else document.querySelectorAll(`.hx-wb[data-kind="${kind}"]`).forEach(paint); }
    catch (_) { /* offline / logged out: keep last state */ }
    finally { busy[kind] = false; }
    if (kind === 'todo') syncTodoPanel();
  }
  setInterval(() => {
    if (document.visibilityState !== 'visible') return;
    if (document.querySelector('.hx-wb[data-kind="kanban"]') || (E && !E.hidden && cur && cur.kind === 'kanban')) refresh('kanban');
    refresh('todo');
  }, POLL_MS);

  // ── events ───────────────────────────────────────────────────────────────
  document.addEventListener('click', (e) => {
    const chk = e.target.closest && e.target.closest('.hx-wb-check');
    const card = e.target.closest && e.target.closest('.hx-wb');
    if (chk && card) {
      e.preventDefault(); e.stopPropagation();
      const t = cache.todo.get(card.dataset.id);
      if (!t) return;
      cur = { kind: 'todo', id: t.id };
      setStatus('todo', t.id, t.status === 'done' ? 'ready' : 'done').then(() => afterWrite('todo'))
        .catch((err) => toast('Could not update: ' + err.message, 'error'));
      return;
    }
    if (card) { e.preventDefault(); openEd(card.dataset.kind, card.dataset.id, card); return; }
    const row = e.target.closest && e.target.closest('#todoPanel .todos-row, .todos-row');
    if (row) {
      const meta = row.querySelector('.todos-meta');
      const id = meta && (meta.textContent.split('·')[0] || '').trim();
      if (id && cache.todo.has(id)) { e.preventDefault(); openEd('todo', id, row); }
    }
  });
  document.addEventListener('keydown', (e) => {
    const card = e.target.closest && e.target.closest('.hx-wb');
    if (card && e.target === card && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openEd(card.dataset.kind, card.dataset.id, card); }
  });

  // ── wiring ───────────────────────────────────────────────────────────────
  function scan(root) {
    if (!root) return;
    root.querySelectorAll('.msg-row:not([data-role="user"]) .msg-body').forEach(tokens);
  }
  let queued = false;
  const mo = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; scan(document.getElementById('messages')); syncTodoPanel(); });
  });
  function start() {
    const msgs = document.getElementById('messages');
    scan(msgs);
    if (msgs) mo.observe(msgs, { childList: true, subtree: true, characterData: true });
    refresh('todo', true);
  }
  window.hxWorkboard = { open: openEd, refresh, cache };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
