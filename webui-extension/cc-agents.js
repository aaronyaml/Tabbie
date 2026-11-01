/* Hermes — Command Centre agents + questions (plain JS, no build step).
 *  1. Live "Agents" block under each Hermes reply that ran Claude Code / Astra: every agent (main
 *     worker + subagents) with status, latest step, tokens; settles to a one-line summary.
 *  2. Agents sheet (pill, top right of the chat): every agent across this session's requests.
 *  3. Question cards: ```hermes-question blocks (Claude, Astra executor, Astra planner) render like
 *     Claude Code's AskUserQuestion: header chips, options with descriptions, multi-select, Other.
 *     Answers are sent as a normal chat message; the plugin resumes the same worker session.
 * Data: the cc-agents sidecar, reached only through the WebUI's authenticated, consent-gated
 * extension proxy (/api/extensions/cc-agents/sidecar/*).
 */
(() => {
  'use strict';
  if (window.__hxAgents) return;
  window.__hxAgents = true;

  const API = 'api/extensions/cc-agents/sidecar/';
  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const sid = () => { try { return (typeof S !== 'undefined' && S && S.session && S.session.session_id) || ''; } catch (_) { return ''; } };

  // ── data ────────────────────────────────────────────────────────────────
  let data = null;          // last /session payload for `dataSid`
  let dataSid = '';
  let state = 'idle';       // idle | ok | consent | down
  let inflight = null;
  let lastFetch = 0;

  async function refresh(force) {
    const s = sid();
    if (!s) { data = null; dataSid = ''; render(); return; }
    if (inflight) return inflight.then(() => (sid() !== dataSid ? refresh(true) : null));
    if (!force && s === dataSid && Date.now() - lastFetch < 1200) return;
    inflight = (async () => {
      try {
        const r = await fetch(API + 'session?id=' + encodeURIComponent(s), { credentials: 'same-origin', cache: 'no-store' });
        lastFetch = Date.now();
        if (r.status === 403) { state = 'consent'; data = null; }
        else if (!r.ok) { state = 'down'; }
        else { const j = await r.json(); if (sid() === s) { data = j; dataSid = s; state = 'ok'; } }
      } catch (_) { state = 'down'; }
      finally { inflight = null; }
      render();
    })();
    return inflight;
  }

  async function grantConsent() {
    try {
      const body = JSON.stringify({ id: 'cc-agents', approved: true });
      if (typeof window.api === 'function') await window.api('/api/extensions/sidecar-proxy-consent', { method: 'POST', body });
      else await fetch('api/extensions/sidecar-proxy-consent', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body });
    } catch (_) {}
    refresh(true);
  }

  const allAgents = () => (data ? data.requests.flatMap((r) => (r.agents_list || []).map((a) => Object.assign({ request: r.id }, a))) : []);

  // ── formatting ──────────────────────────────────────────────────────────
  function fmtTokens(n) {
    n = Number(n) || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e5 ? 0 : 1).replace(/\.0$/, '') + 'k';
    return String(n);
  }
  function fmtDur(s) {
    s = Math.max(0, Math.round(Number(s) || 0));
    if (s < 60) return s + 's';
    const m = Math.floor(s / 60);
    return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
  }
  const elapsed = (a) => (a.duration_s != null ? a.duration_s : (Date.now() / 1000 - a.started));
  const STATUS = { running: 'Running', done: 'Done', failed: 'Failed', waiting: 'Needs you' };

  function agentRow(a) {
    const sub = a.parent ? ' hx-ag-sub' : '';
    const meta = [a.tokens ? fmtTokens(a.tokens) + ' tokens' : '', a.status === 'running' ? fmtDur(elapsed(a)) : (a.duration_s != null ? fmtDur(a.duration_s) : '')].filter(Boolean).join(' · ');
    const step = a.status === 'running' || a.status === 'waiting' || a.status === 'failed' ? a.step : (a.description && a.description !== 'subagent' ? a.description : '');
    return `<div class="hx-ag${sub}" data-status="${esc(a.status)}">
      <span class="hx-ag-dot" title="${esc(STATUS[a.status] || a.status)}"></span>
      <div class="hx-ag-tx"><div class="hx-ag-nm">${esc(a.name)}</div>${step ? `<div class="hx-ag-step">${esc(step)}</div>` : ''}</div>
      <div class="hx-ag-meta">${esc(meta)}</div></div>`;
  }

  function summaryText(list) {
    const run = list.filter((a) => a.status === 'running').length;
    const tokens = list.reduce((t, a) => t + (a.tokens || 0), 0);
    const starts = list.map((a) => a.started).filter(Boolean);
    const ends = list.map((a) => a.ended).filter(Boolean);
    const span = run ? Date.now() / 1000 - Math.min(...starts) : (ends.length ? Math.max(...ends) - Math.min(...starts) : 0);
    const n = list.length;
    const parts = [`${n} agent${n === 1 ? '' : 's'}`];
    if (run) parts.push(`${run} running`);
    if (list.some((a) => a.status === 'failed')) parts.push(`${list.filter((a) => a.status === 'failed').length} failed`);
    if (tokens) parts.push(fmtTokens(tokens) + ' tokens');
    if (span) parts.push(fmtDur(span));
    return parts.join(' · ');
  }

  // ── 1. per-reply Agents block ───────────────────────────────────────────
  const userTs = (row) => {
    const k = row.getAttribute('data-message-anchor-key') || (row.querySelector('[data-message-anchor-key^="user|"]') || {}).getAttribute?.('data-message-anchor-key') || '';
    const m = /^user\|([\d.]+)/.exec(k);
    return m ? parseFloat(m[1]) : null;
  };
  const expanded = new Set();

  function turnWindows() {
    const rows = [...document.querySelectorAll('#messages .msg-row')];
    const out = [];
    let start = null;
    rows.forEach((row, i) => {
      if (row.getAttribute('data-role') === 'user') { const t = userTs(row); if (t) start = t; else if (start != null) start = start + 0.001; return; }
      if (!row.classList.contains('assistant-turn')) return;
      let end = Infinity;
      for (let j = i + 1; j < rows.length; j++) {
        if (rows[j].getAttribute('data-role') === 'user') { const t = userTs(rows[j]); if (t) { end = t; break; } }
      }
      out.push({ row, start: start == null ? 0 : start, end });
    });
    return out;
  }

  function renderBlocks() {
    const agents = allAgents();
    for (const w of turnWindows()) {
      const list = agents.filter((a) => a.started >= w.start - 1 && a.started < w.end);
      let el = w.row.querySelector(':scope .hx-agents');
      if (!list.length) { if (el) el.remove(); continue; }
      const host = w.row.querySelector('.assistant-turn-blocks') || w.row;
      if (!el) {
        el = document.createElement('div');
        el.className = 'hx-agents';
        el.addEventListener('click', (e) => {
          if (!e.target.closest('.hx-agents-head')) return;
          const key = el.dataset.key;
          expanded.has(key) ? expanded.delete(key) : expanded.add(key);
          renderBlocks();
        });
      }
      const group = host.querySelector(':scope > .agent-activity-group, :scope > .tool-call-group');
      const anchor = group ? group.nextSibling : host.firstChild;
      if (el.parentNode !== host || (group && el.previousSibling !== group)) host.insertBefore(el, anchor);
      const key = `${list[0].request}:${list[0].id}`;
      el.dataset.key = key;
      const live = list.some((a) => a.status === 'running');
      const open = live || expanded.has(key);
      const html = `<button type="button" class="hx-agents-head" aria-expanded="${open}">
          ${icon('UserMultiple02Icon', 14)}<span>${esc(summaryText(list))}</span>${live ? '' : `<span class="hx-agents-caret">${icon('ArrowDown01Icon', 12)}</span>`}
        </button>${open ? `<div class="hx-agents-list">${list.map(agentRow).join('')}</div>` : ''}`;
      el.classList.toggle('is-live', live);
      el.classList.toggle('is-open', open);
      if (el.__html !== html) { el.innerHTML = html; el.__html = html; }
      tighten(el, group);
    }
  }

  // Sit 4px under the activity row ("Completed – 36s"), as one meta block: the group keeps empty
  // slack below its visible content plus a bottom margin, so measure instead of guessing.
  function tighten(el, group) {
    if (!group || !group.offsetParent) { el.style.marginTop = ''; return; }
    let bottom = 0;
    for (const c of group.children) { const r = c.getBoundingClientRect(); if (r.height > 0) bottom = Math.max(bottom, r.bottom); }
    if (!bottom) return;
    const cur = parseFloat(el.style.marginTop) || 0;
    const want = Math.round(cur + bottom - el.getBoundingClientRect().top);
    if (Math.abs(want - cur) >= 1) el.style.marginTop = want + 'px';
  }

  // ── 2. session sheet ────────────────────────────────────────────────────
  let pill = null, sheet = null;
  function ensurePill() {
    if (pill && document.body.contains(pill)) return pill;
    pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'hx-agents-pill';
    pill.setAttribute('aria-label', 'Agents in this chat');
    pill.addEventListener('click', () => openSheet(true));
    document.body.appendChild(pill);
    return pill;
  }

  function placePill() {
    const msgs = document.getElementById('messages');
    if (!pill || !msgs) return;
    const nb = document.getElementById('btnTitlebarNewChat');
    const bar = nb && nb.closest('.app-titlebar');
    if (bar && bar.offsetParent !== null && getComputedStyle(bar).display !== 'none') { // phone: dock in the top bar
      if (pill.nextElementSibling !== nb) nb.before(pill);
      pill.classList.add('is-inbar'); pill.style.top = pill.style.right = '';
      return;
    }
    if (pill.parentElement !== document.body) document.body.appendChild(pill);
    pill.classList.remove('is-inbar');
    const r = msgs.getBoundingClientRect();
    pill.style.top = Math.max(10, r.top + 12) + 'px';
    pill.style.right = Math.max(12, window.innerWidth - r.right + 16) + 'px';
  }

  function renderPill() {
    const agents = allAgents();
    const show = !!sid() && (agents.length > 0 || state === 'consent');
    ensurePill().hidden = !show;
    if (!show) return;
    const run = agents.filter((a) => a.status === 'running').length;
    const wait = agents.some((a) => a.status === 'waiting');
    const html = `${icon('UserMultiple02Icon', 16)}<span class="hx-pill-tx">Agents</span>${agents.length ? `<span class="hx-pill-n${run ? ' is-live' : ''}${wait && !run ? ' is-wait' : ''}">${run || agents.length}</span>` : ''}`;
    if (pill.__html !== html) { pill.innerHTML = html; pill.__html = html; }
    placePill();
  }

  function openSheet(on) {
    if (!sheet) {
      sheet = document.createElement('div');
      sheet.className = 'hx-as';
      sheet.innerHTML = '<div class="hx-as-scrim"></div><aside class="hx-as-panel" role="dialog" aria-modal="true" aria-label="Agents in this chat"><header class="hx-as-head"><div class="hx-as-title">Agents</div><div class="hx-as-sub"></div><button type="button" class="hx-as-close" aria-label="Close">' + icon('Cancel01Icon', 18) + '</button></header><div class="hx-as-body"></div></aside>';
      sheet.querySelector('.hx-as-scrim').addEventListener('click', () => openSheet(false));
      sheet.querySelector('.hx-as-close').addEventListener('click', () => openSheet(false));
      sheet.addEventListener('click', (e) => { if (e.target.closest('.hx-as-allow')) grantConsent(); });
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && sheet.classList.contains('is-open')) openSheet(false); });
      document.body.appendChild(sheet);
    }
    sheet.classList.toggle('is-open', on);
    if (on) { refresh(true); renderSheet(); }
  }

  function renderSheet() {
    if (!sheet || !sheet.classList.contains('is-open')) return;
    const body = sheet.querySelector('.hx-as-body');
    const sub = sheet.querySelector('.hx-as-sub');
    let html;
    if (state === 'consent') {
      sub.textContent = '';
      html = '<div class="hx-as-empty"><p>Allow this panel to read Command Centre agent state through Tabbie (signed-in only).</p><button type="button" class="hx-as-allow">Allow</button></div>';
    } else if (state === 'down' && !data) {
      sub.textContent = '';
      html = '<div class="hx-as-empty"><p>Agent data is unavailable. The cc-sidecar service may be stopped.</p></div>';
    } else {
      const reqs = (data ? data.requests : []).filter((r) => (r.agents_list || []).length).slice().reverse();
      sub.textContent = reqs.length ? summaryText(allAgents()) : '';
      html = reqs.length ? reqs.map((r) => `<section class="hx-as-req">
          <div class="hx-as-req-head"><span class="hx-as-req-p">${esc(r.prompt || r.id)}</span><span class="hx-as-chip" data-s="${esc(r.status)}">${esc(reqLabel(r.status))}</span></div>
          <div class="hx-agents-list">${r.agents_list.map(agentRow).join('')}</div></section>`).join('')
        : '<div class="hx-as-empty"><p>No agents have run in this chat yet.</p></div>';
    }
    if (body.__html !== html) { body.innerHTML = html; body.__html = html; }
  }
  const reqLabel = (s) => ({ executing: 'Running', planning: 'Planning', done: 'Done', failed: 'Failed', awaiting_answer: 'Needs you', awaiting_answers: 'Needs you', awaiting_approval: 'Awaiting approval', answered: 'Resuming', approved: 'Approved', cancelled: 'Cancelled', claude_limited: 'Usage limit', unanswered: 'Skipped' }[s] || s || '');

  // ── 3. question cards ───────────────────────────────────────────────────
  const drafts = new Map(); // `${rid}:${round}` -> {sel: [[...labels]], other: [...]}

  function roundInfo(rid, round) {
    const r = data && data.requests.find((x) => x.id === rid);
    return r ? (r.questions || []).find((q) => Number(q.round) === Number(round)) : null;
  }

  function isLatestReply(node) {
    const row = node.closest('.msg-row');
    const rows = [...document.querySelectorAll('#messages .msg-row')];
    const i = rows.indexOf(row);
    return i >= 0 && !rows.slice(i + 1).some((r) => r.getAttribute('data-role') === 'user');
  }

  function nextUserText(node) {
    const rows = [...document.querySelectorAll('#messages .msg-row')];
    const i = rows.indexOf(node.closest('.msg-row'));
    const u = rows.slice(i + 1).find((r) => r.getAttribute('data-role') === 'user');
    return u ? (u.getAttribute('data-raw-text') || u.textContent || '').trim() : null;
  }

  // The user turn right after a card, if it is that card's own answer ("Header: choice" lines).
  function answerRow(card) {
    const rows = [...document.querySelectorAll('#messages .msg-row')];
    const i = rows.indexOf(card.closest('.msg-row'));
    const u = i < 0 ? null : rows.slice(i + 1).find((r) => r.getAttribute('data-role') === 'user');
    if (!u) return null;
    const heads = card.__q.questions.map((x) => String(x.header || '').trim().toLowerCase());
    const text = (u.getAttribute('data-raw-text') || (u.querySelector('.msg-body') || u).textContent || '').trim();
    const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);
    const ok = lines.length && lines.every((l) => heads.some((h) => l.toLowerCase().startsWith(h + ':')));
    return ok ? { row: u, text: lines.join('\n') } : null;
  }

  function scanQuestions() {
    document.querySelectorAll('#messages code.language-hermes-question').forEach((code) => {
      const pre = code.closest('pre');
      if (!pre) return;
      const wrap = pre.parentElement && pre.parentElement.querySelector(':scope > .pre-header') ? pre.parentElement : null;
      const head = (wrap || pre.parentElement).querySelector(':scope > .pre-header');
      let q;
      try { q = JSON.parse(code.textContent); } catch (_) { return; } // still streaming
      if (!q || !Array.isArray(q.questions) || !q.questions.length) return;
      let card = pre.__hxCard;
      if (!card || !card.isConnected) {
        card = document.createElement('div');
        card.className = 'hx-q';
        pre.before(card);
        pre.__hxCard = card;
        pre.classList.add('hx-hidden');
        if (head) head.classList.add('hx-hidden');
        card.addEventListener('click', (e) => onCardClick(e, card));
        card.addEventListener('input', (e) => onCardInput(e, card));
      }
      card.__q = q;
      // Seamless: the answer lives inside the card, not as a separate chat bubble.
      const ans = answerRow(card);
      card.__answer = ans ? ans.text : null;
      if (ans) ans.row.classList.add('hx-q-ans-row');
      renderCard(card);
    });
  }

  function cardState(card) {
    const q = card.__q;
    const info = roundInfo(q.request_id, q.round || 1);
    if (info) return { pending: !!info.pending && !card.__answer, answer: info.answer };
    // No sidecar data: the newest unanswered card is the open one.
    const ans = card.__answer || nextUserText(card);
    return { pending: ans == null && isLatestReply(card), answer: ans };
  }

  function draftFor(card) {
    const q = card.__q;
    // Two asks can share request + round (a steer resumed Claude before the first was answered).
    const k = `${q.request_id}:${q.round || 1}:${q.questions.map((x) => x.question).join('|')}`;
    if (!drafts.has(k)) drafts.set(k, { sel: q.questions.map(() => []), other: q.questions.map(() => ''), sent: false });
    return drafts.get(k);
  }

  function renderCard(card) {
    const q = card.__q;
    const st = cardState(card);
    const d = draftFor(card);
    const who = q.source === 'astra-plan' ? 'Astra' : q.source === 'astra' ? 'Astra' : 'Claude';
    const pending = st.pending && !d.sent;
    let html = `<div class="hx-q-top">${icon('BubbleChatQuestionIcon', 16)}<span>${esc(who)} ${pending ? 'needs your input' : 'asked'}</span>
      ${pending ? '' : `<div class="hx-q-chips">${q.questions.map((x) => `<span class="hx-q-chip">${esc(x.header)}</span>`).join('')}</div>`}</div>`;
    if (!pending) {
      const answer = card.__answer || st.answer || (d.sent ? d.sentText : null);
      html += q.questions.map((x) => `<div class="hx-q-done-q">${esc(x.question)}</div>`).join('')
        + `<div class="hx-q-done-a">${answer ? esc(answer).replace(/\n/g, '<br>') : (d.sent ? 'Sending…' : 'Not answered')}</div>`;
    } else {
      html += q.questions.map((x, qi) => {
        const multi = !!x.multiSelect;
        const opts = (x.options || []).map((o, oi) => {
          const on = d.sel[qi].includes(o.label);
          return `<button type="button" class="hx-q-opt${on ? ' is-on' : ''}" data-q="${qi}" data-o="${oi}" role="${multi ? 'checkbox' : 'radio'}" aria-checked="${on}">
            <span class="hx-q-mark${multi ? ' is-multi' : ''}">${on ? icon('Tick02Icon', 12) : ''}</span>
            <span class="hx-q-ot"><span class="hx-q-ol">${esc(o.label)}</span>${o.description ? `<span class="hx-q-od">${esc(o.description)}</span>` : ''}</span></button>`;
        }).join('');
        return `<fieldset class="hx-q-item"><legend class="hx-q-text"><span class="hx-q-chip">${esc(x.header)}</span>${esc(x.question)}${multi ? '<span class="hx-q-hint">Choose any</span>' : ''}</legend>
          <div class="hx-q-opts">${opts}</div>
          <input class="hx-q-other" data-q="${qi}" type="text" placeholder="Other…" autocomplete="off" value="${esc(d.other[qi])}"></fieldset>`;
      }).join('');
      const ready = q.questions.every((_, i) => d.sel[i].length || d.other[i].trim());
      html += `<div class="hx-q-foot"><span class="hx-q-note">Your answer resumes ${esc(who)}</span><button type="button" class="hx-q-send"${ready ? '' : ' disabled'}>Send answer</button></div>`;
    }
    card.classList.toggle('is-pending', pending);
    if (card.__html !== html) {
      const focused = document.activeElement && card.contains(document.activeElement) && document.activeElement.matches('.hx-q-other') ? document.activeElement.dataset.q : null;
      card.innerHTML = html; card.__html = html;
      if (focused != null) { const inp = card.querySelector(`.hx-q-other[data-q="${focused}"]`); if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); } }
    }
  }

  function onCardInput(e, card) {
    const inp = e.target.closest('.hx-q-other');
    if (!inp) return;
    const d = draftFor(card); const qi = +inp.dataset.q;
    d.other[qi] = inp.value;
    if (inp.value.trim() && !card.__q.questions[qi].multiSelect) d.sel[qi] = [];
    renderCard(card);
  }

  function onCardClick(e, card) {
    const d = draftFor(card); const q = card.__q;
    const opt = e.target.closest('.hx-q-opt');
    if (opt) {
      const qi = +opt.dataset.q; const label = q.questions[qi].options[+opt.dataset.o].label;
      if (q.questions[qi].multiSelect) d.sel[qi] = d.sel[qi].includes(label) ? d.sel[qi].filter((x) => x !== label) : [...d.sel[qi], label];
      else { d.sel[qi] = d.sel[qi][0] === label ? [] : [label]; d.other[qi] = ''; }
      renderCard(card);
      return;
    }
    if (e.target.closest('.hx-q-send')) send(card);
  }

  function send(card) {
    const d = draftFor(card); const q = card.__q;
    const lines = q.questions.map((x, i) => {
      const parts = [...d.sel[i]]; if (d.other[i].trim()) parts.push(d.other[i].trim());
      return `${x.header}: ${parts.join(', ')}`;
    });
    const text = lines.join('\n');
    const box = document.getElementById('msg');
    if (!box || typeof window.send !== 'function') return;
    box.value = text;
    box.dispatchEvent(new Event('input', { bubbles: true }));
    d.sent = true; d.sentText = text;
    renderCard(card);
    window.send();
  }

  // ── loop ────────────────────────────────────────────────────────────────
  function render() { try { renderBlocks(); renderPill(); renderSheet(); scanQuestions(); } catch (e) { console.warn('cc-agents', e); } }

  const liveTurn = () => !!document.querySelector('#messages [data-live-tool-worklog-group="1"], #messages [data-live-tool-call-group="1"], #messages [data-tool-worklog-running="1"]');
  const anyRunning = () => allAgents().some((a) => a.status === 'running');
  let lastSid = '';
  setInterval(() => {
    const s = sid();
    if (s !== lastSid) { lastSid = s; data = null; dataSid = ''; drafts.clear(); refresh(true); return; }
    if (document.hidden) return;
    const busy = liveTurn() || anyRunning() || (sheet && sheet.classList.contains('is-open'));
    if (busy || dataSid !== s || Date.now() - lastFetch > 15000) refresh(dataSid !== s); else render();
  }, 1500);

  let queued = false;
  const mo = new MutationObserver((muts) => {
    if (muts.every((m) => { const t = m.target.nodeType === 1 ? m.target : m.target.parentElement; return t && t.closest('.hx-agents, .hx-q'); })) return;
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; render(); if (Date.now() - lastFetch > 3000) refresh(); });
  });
  function start() {
    const msgs = document.getElementById('messages');
    if (msgs) mo.observe(msgs, { childList: true, subtree: true });
    window.addEventListener('resize', placePill, { passive: true });
    let sq = false; // rows off screen skip layout (content-visibility): re-measure as they scroll in
    if (msgs) msgs.addEventListener('scroll', () => { if (sq) return; sq = true; requestAnimationFrame(() => { sq = false; renderBlocks(); }); }, { passive: true });
    refresh(true);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
