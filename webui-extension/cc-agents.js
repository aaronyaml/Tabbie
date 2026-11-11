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
  const toast = (m, ms, type) => { try { if (typeof window.showToast === 'function') window.showToast(m, ms || 3200, type); } catch (_) {} };
  const reduced = () => { try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (_) { return false; } };
  const DOWN = "Can't reach Tabbie on your computer right now.";
  async function post(path, body) {
    const r = await fetch(API + path, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (r.status === 401 || r.status === 403) { if (window.hxHelperConsent) window.hxHelperConsent.open(); throw new Error('consent'); }
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }

  // ── data ────────────────────────────────────────────────────────────────
  let data = null;          // last /session payload for `dataSid`
  let dataSid = '';
  let state = 'idle';       // idle | ok | consent | down
  let inflight = null;
  let lastFetch = 0;

  // A run cut off by a restart (computer restarted, Tabbie crashed): resend it once when its chat is opened.
  // The sidecar hands the claim to one tab only and only for the chat's newest request, within 2 h.
  const retryAsked = new Set();
  async function autoRetry(s) {
    if (retryAsked.has(s)) return;
    retryAsked.add(s);
    try {
      const r = await post('retry/claim', { session: s });
      if (!r || !r.ok || sid() !== s || (typeof S !== 'undefined' && S.busy) || typeof window.cmdRetry !== 'function') return;
      toast('Tabbie restarted while this was running. Sending it again.', 4000);
      await window.cmdRetry();
    } catch (_) { /* consent / offline: the reply already says to send it again */ }
  }

  async function refresh(force) {
    const s = sid();
    if (!s) { data = null; dataSid = ''; render(); return; }
    if (!retryAsked.has(s)) autoRetry(s);
    if (inflight) return inflight.then(() => (sid() !== dataSid ? refresh(true) : null));
    if (!force && s === dataSid && Date.now() - lastFetch < 1200) return;
    inflight = (async () => {
      try {
        const r = await fetch(API + 'session?id=' + encodeURIComponent(s), { credentials: 'same-origin', cache: 'no-store' });
        lastFetch = Date.now();
        if (r.status === 403) { state = 'consent'; data = null; }
        else if (!r.ok) { state = 'down'; }
        else { const j = await r.json(); if (sid() === s) { data = j; dataSid = s; state = 'ok'; try { window.dispatchEvent(new CustomEvent('hx-agents-data')); } catch (_) {} } }
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

  window.hxAgentsData = () => (data && dataSid === sid() ? data : null); // cc-narration.js reads the same payload
  const allAgents = () => (data ? data.requests.flatMap((r) => (r.agents_list || []).map((a) => Object.assign({ request: r.id, reqStatus: r.status }, a))) : []);

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
  const STATUS = { running: 'Running', done: 'Done', failed: 'Failed', waiting: 'Needs you', stopped: 'Stopped' };
  // The user's Stop reads "stopped" (neutral), never "failed". Older records stored it as failed + step.
  const isStopped = (a) => a.status === 'stopped' || (a.status === 'failed' && /stopped by (you|the user)/i.test(String(a.step || '')));
  const statusOf = (a) => (isStopped(a) ? 'stopped' : a.status);
  const isBg = (a) => a.kind === 'background';  // a background shell task: muted row, not an agent
  const PROVIDER_LABEL = (p) => (/astra|codex/i.test(String(p || '')) ? 'Codex' : 'Claude');
  const shown = (n) => String(n == null ? '' : n).replace(/(?<!GPT-[\d.]+ )\bAstra\b/g, 'Codex'); // internal planner name → what users know; model names ("GPT-6 Astra") stay

  function agentRow(a, list) {
    // depth: a delegate's own subagents nest one level deeper than the delegate row
    const parent = a.parent && (list || []).find((x) => x.id === a.parent);
    const deep = parent && parent.parent ? ' hx-ag-sub2' : '';
    const sub = a.parent ? ' hx-ag-sub' + deep : '';
    const st = statusOf(a);
    const kind = a.kind === 'delegate' ? ' hx-ag-delegate' : (isBg(a) ? ' hx-ag-bg' : '');
    const meta = [a.tokens ? fmtTokens(a.tokens) + ' tokens' : '', st === 'running' ? fmtDur(elapsed(a)) : (a.duration_s != null && !(isBg(a) && a.duration_s < 1) ? fmtDur(a.duration_s) : '')].filter(Boolean).join(' · ');
    const step = st === 'stopped' ? 'Stopped by you'
      : (st === 'running' || st === 'waiting' || st === 'failed' ? a.step : (a.description && a.description !== 'subagent' ? a.description : ''));
    const badge = a.kind === 'delegate'
      ? `<span class="hx-ag-badge" title="Handed off by ${esc(PROVIDER_LABEL(parent && parent.provider))} to ${esc(PROVIDER_LABEL(a.to || a.provider))}">hand-off · ${esc(PROVIDER_LABEL(a.to || a.provider))}</span>`
      : (isBg(a) ? '<span class="hx-ag-badge">background</span>' : '');
    return `<div class="hx-ag${sub}${kind}" data-status="${esc(st)}">
      <span class="hx-ag-dot" title="${esc(STATUS[st] || st)}"></span>
      <div class="hx-ag-tx"><div class="hx-ag-nm">${esc(shown(a.name))}${badge}</div>${step ? `<div class="hx-ag-step">${esc(step)}</div>` : ''}</div>
      <div class="hx-ag-meta">${esc(meta)}</div></div>`;
  }

  // One status line per reply: outcome first, then time, agents, tokens, files changed.
  //   "Done · 15m 27s · 5 agents · 66k tokens · 3 files"   "Working · 2m 10s · 2 agents · 1 background task"
  // From the agents that did the work (background shell tasks are steps, not outcomes) and the request.
  function outcome(all) {
    const list = all.filter((a) => !isBg(a));
    if (all.some((a) => a.status === 'running')) return 'Working';
    if (list.some((a) => a.status === 'waiting')) return 'Needs you';
    if (all.some((a) => a.reqStatus === 'stopped' || a.reqStatus === 'cancelled') || list.some(isStopped)) return 'Stopped';
    const bad = list.filter((a) => a.status === 'failed');
    if (bad.length) return bad.length === list.length ? 'Failed' : 'Done with errors';
    return 'Done';
  }
  function summaryText(all, nFiles) {
    const list = all.filter((a) => !isBg(a));
    const run = list.filter((a) => a.status === 'running').length;
    const tokens = list.reduce((t, a) => t + (a.tokens || 0), 0);
    const starts = all.map((a) => a.started).filter(Boolean);
    const ends = all.map((a) => a.ended).filter(Boolean);
    const span = run ? Date.now() / 1000 - Math.min(...starts) : (ends.length ? Math.max(...ends) - Math.min(...starts) : 0);
    const n = list.length;
    const parts = [outcome(all)];
    if (span) parts.push(fmtDur(span));
    parts.push(`${n} agent${n === 1 ? '' : 's'}`);
    if (run > 1) parts.push(`${run} running`);
    const failed = list.filter((a) => a.status === 'failed' && !isStopped(a)).length;
    if (failed && failed < n) parts.push(`${failed} failed`);
    const handoffs = list.filter((a) => a.kind === 'delegate').length;
    if (handoffs) parts.push(`${handoffs} hand-off${handoffs === 1 ? '' : 's'}`);
    const bg = all.filter((a) => isBg(a) && a.status === 'running').length;
    if (bg) parts.push(`${bg} background task${bg === 1 ? '' : 's'}`);
    if (tokens) parts.push(fmtTokens(tokens) + ' tokens');
    if (nFiles) parts.push(`${nFiles} file${nFiles === 1 ? '' : 's'} changed`);
    return parts.join(' · ');
  }
  const OUTCOME_ICON = { Working: 'Loading03Icon', Done: 'CheckmarkCircle02Icon', 'Done with errors': 'InformationCircleIcon', Failed: 'CancelCircleIcon', Stopped: 'StopIcon', 'Needs you': 'HelpCircleIcon' };
  const filesCount = (rids) => rids.map((r) => files.get(r)).filter((f) => f && f.state === 'ok').reduce((t, f) => t + (f.d.total || 0), 0);

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
    out.slice(-3).forEach((w) => { w.recent = true; });
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
        el.addEventListener('click', (e) => onBlockClick(e, el));
      }
      const group = host.querySelector(':scope > .agent-activity-group, :scope > .tool-call-group');
      const anchor = group ? group.nextSibling : host.firstChild;
      if (el.parentNode !== host || (group && el.previousSibling !== group)) host.insertBefore(el, anchor);
      const key = `${list[0].request}:${list[0].id}`;
      el.dataset.key = key;
      const live = list.some((a) => a.status === 'running');
      const open = live || expanded.has(key);
      if (!live) stopUi.delete(key);
      const rids = [...new Set(list.map((a) => a.request))];
      el.dataset.rids = rids.join(',');
      // Files-changed count on the line: open blocks, and the newest few replies (each is one cached lookup).
      if (!live && (open || w.recent)) rids.forEach(loadFiles);
      const html = `<div class="hx-agents-bar"><button type="button" class="hx-agents-head" aria-expanded="${open}">
          ${icon(OUTCOME_ICON[outcome(list)] || 'UserMultiple02Icon', 14)}<span>${esc(summaryText(list, live ? 0 : filesCount(rids)))}</span>${live ? '' : `<span class="hx-agents-caret">${icon('ArrowDown01Icon', 12)}</span>`}
        </button>${live && stoppable(list) ? stopHtml(key, list) : ''}</div>${open ? `<div class="hx-agents-list">${list.map((a) => agentRow(a, list)).join('')}</div>${live ? '' : extraHtml(rids)}` : ''}`;
      el.classList.toggle('is-live', live);
      el.classList.toggle('is-open', open);
      el.dataset.outcome = outcome(list).toLowerCase().replace(/\s+/g, '-');
      el.classList.toggle('is-stopped', !live && list.some(isStopped) && !list.some((a) => a.status === 'failed' && !isStopped(a)));
      if (el.__html !== html) { el.innerHTML = html; el.__html = html; }
      tighten(el, group);
    }
  }

  // Stop (live block): tap -> inline confirm -> POST /worker/stop. The worker's own result then
  // settles the block to Failed with "stopped by you".
  const stopUi = new Map(); // block key -> 'confirm' | 'busy'
  // A running worker (Claude Code or the Codex executor); the planner can't be stopped from here.
  const stoppable = (list) => list.some((a) => !a.parent && a.status === 'running' && a.description !== 'planner');
  function stopHtml(key, list) {
    const main = list.find((a) => !a.parent && a.status === 'running') || list[0];
    const who = /codex|astra/i.test(String(main && `${main.provider} ${main.name}`)) ? 'Codex' : 'Claude';
    const st = stopUi.get(key);
    if (st === 'confirm') {
      return `<span class="hx-agents-confirm" role="group" aria-label="Stop ${who}?"><span class="hx-agents-ask">Stop ${who}? Work so far stays.</span>
        <button type="button" class="hx-agents-btn" data-stop="no">Keep going</button><button type="button" class="hx-agents-btn is-danger" data-stop="yes">Stop</button></span>`;
    }
    return `<button type="button" class="hx-agents-btn hx-agents-stop" data-stop="ask"${st === 'busy' ? ' disabled' : ''} aria-label="Stop ${who}">${icon('StopIcon', 12)}<span>${st === 'busy' ? 'Stopping…' : 'Stop'}</span></button>`;
  }

  async function stopWorker(key) {
    stopUi.set(key, 'busy'); renderBlocks();
    try {
      const j = await post('worker/stop', { session: sid() });
      if (!j.ok) { stopUi.delete(key); toast('Nothing is running to stop.'); }
      else refresh(true);
    } catch (e) {
      stopUi.delete(key);
      if (String(e && e.message) !== 'consent') toast(DOWN, 4000, 'error');
    }
    renderBlocks();
  }

  // The composer's Stop (and /stop, and the sidebar's "Stop response") only cancels the hub's turn
  // upstream; the Claude Code / Codex worker it is waiting on kept running with nobody to report to.
  // Stop means stop: after an explicit stop, end this chat's worker too (no-op when none is running).
  // Interrupts (/interrupt, sending while busy in interrupt mode) are left alone: a new message follows.
  const STOP_REASONS = new Set(['explicit-cancel', 'slash-stop']);
  function stopWorkerOf(s) {
    if (!s) return;
    post('worker/stop', { session: s })
      .then((j) => { if (j && j.ok && s === sid()) refresh(true); })
      .catch(() => {});
  }
  function wrapCancel() {
    let done = 0;
    if (typeof window.cancelStream === 'function' && !window.cancelStream.__hxStop) {
      const orig = window.cancelStream;
      window.cancelStream = async function (reason) {
        const s = sid();
        const out = await orig.apply(this, arguments);
        if (STOP_REASONS.has(reason || 'explicit-cancel')) stopWorkerOf(s);
        return out;
      };
      window.cancelStream.__hxStop = true;
    }
    if (typeof window.cancelStream === 'function') done++;
    if (typeof window.cancelSessionStream === 'function' && !window.cancelSessionStream.__hxStop) {
      const orig = window.cancelSessionStream;
      window.cancelSessionStream = async function (session) {
        const s = session && session.session_id;
        const out = await orig.apply(this, arguments);
        stopWorkerOf(s);
        return out;
      };
      window.cancelSessionStream.__hxStop = true;
    }
    if (typeof window.cancelSessionStream === 'function') done++;
    return done === 2;
  }
  if (!wrapCancel()) { let n = 0; const t = setInterval(() => { if (wrapCancel() || ++n > 100) clearInterval(t); }, 100); }

  // Settled block, expanded: files the worker edited + "Copy resume command" (Claude Code).
  const files = new Map(); // request id -> {state: 'loading'|'ok'|'err', d}
  function loadFiles(rid) {
    if (files.has(rid)) return;
    files.set(rid, { state: 'loading' });
    fetch(API + 'request/files?id=' + encodeURIComponent(rid), { credentials: 'same-origin', cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => { if (d.running) files.delete(rid); else files.set(rid, { state: 'ok', d }); })
      .catch(() => files.set(rid, { state: 'err' }))
      .then(() => renderBlocks());
  }
  const baseName = (p) => String(p).split('/').filter(Boolean).pop() || p;
  function extraHtml(rids) {
    const got = rids.map((r) => files.get(r)).filter((f) => f && f.state === 'ok').map((f) => f.d);
    if (!got.length) return '';
    const paths = [...new Set(got.flatMap((d) => d.files || []))];
    const total = got.reduce((t, d) => t + (d.total || 0), 0);
    const resume = got.slice().reverse().find((d) => d.resume_cmd);
    if (!paths.length && !resume) return '';
    const more = total > paths.length ? `<span class="hx-agents-more">+${total - paths.length} more</span>` : '';
    return `<div class="hx-agents-extra">${paths.length ? `<div class="hx-agents-fh">Files changed (${total})</div>
        <div class="hx-agents-files">${paths.map((p) => `<button type="button" class="hx-agents-file" data-path="${esc(p)}" title="${esc(p)}">${icon('FileEditIcon', 12)}<span>${esc(baseName(p))}</span></button>`).join('')}${more}</div>` : ''}
        ${resume ? `<button type="button" class="hx-agents-btn hx-agents-resume" data-cmd="${esc(resume.resume_cmd)}" title="${esc(resume.resume_cmd)}">${icon('Copy01Icon', 12)}<span>Copy resume command</span></button>` : ''}</div>`;
  }

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (_) {}
    try {
      const ta = document.createElement('textarea');
      ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      const ok = document.execCommand('copy'); ta.remove(); return ok;
    } catch (_) { return false; }
  }

  function onBlockClick(e, el) {
    const key = el.dataset.key;
    const stop = e.target.closest('[data-stop]');
    if (stop) {
      const what = stop.dataset.stop;
      if (what === 'ask') stopUi.set(key, 'confirm');
      else if (what === 'no') stopUi.delete(key);
      else if (what === 'yes') { stopWorker(key); return; }
      renderBlocks();
      const back = el.querySelector(what === 'ask' ? '[data-stop="no"]' : '[data-stop="ask"]');
      if (back) back.focus({ preventScroll: true });
      return;
    }
    const f = e.target.closest('.hx-agents-file');
    if (f) { if (typeof window.hxOpenFile === 'function') window.hxOpenFile(f.dataset.path, sid()); return; }
    const c = e.target.closest('.hx-agents-resume');
    if (c) { copyText(c.dataset.cmd).then((ok) => toast(ok ? 'Copied' : "Couldn't copy. The command is in the button's tooltip.")); return; }
    if (!e.target.closest('.hx-agents-head') || el.classList.contains('is-live')) return;
    expanded.has(key) ? expanded.delete(key) : expanded.add(key);
    renderBlocks();
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
    const list = agents.filter((a) => !isBg(a)); // same count as the sheet's "N agents · N running"
    const run = list.filter((a) => a.status === 'running').length;
    const wait = list.filter((a) => a.status === 'waiting').length;
    // The number is what needs attention now (running, or waiting on you); a settled chat shows none.
    const n = run || wait;
    const html = `${icon('UserMultiple02Icon', 16)}<span class="hx-pill-tx">Agents</span>${n ? `<span class="hx-pill-n${run ? ' is-live' : ' is-wait'}">${n}</span>` : ''}`;
    if (pill.__html !== html) { pill.innerHTML = html; pill.__html = html; }
    placePill();
  }

  function openSheet(on) {
    if (!sheet) {
      sheet = document.createElement('div');
      sheet.className = 'hx-as';
      sheet.innerHTML = '<div class="hx-as-scrim"></div><aside class="hx-as-panel" role="dialog" aria-modal="true" aria-label="Agents in this chat"><header class="hx-as-head"><button type="button" class="hx-back hx-as-close" aria-label="Back to chat">' + icon('ArrowLeft01Icon', 18) + '<span>Back</span></button><div class="hx-as-title">Agents</div><div class="hx-as-sub"></div></header><div class="hx-as-body"></div></aside>';
      sheet.querySelector('.hx-as-scrim').addEventListener('click', () => openSheet(false));
      sheet.querySelector('.hx-as-close').addEventListener('click', () => (nav.length ? navBack() : openSheet(false)));
      sheet.addEventListener('click', (e) => {
        if (e.target.closest('.hx-ad-retry')) return onDetailClick(e);
        if (e.target.closest('.hx-as-allow')) return grantConsent();
        const retry = e.target.closest('.hx-as-retry');
        if (retry) { retry.disabled = true; retry.textContent = 'Trying…'; body.__html = ''; return refresh(true); }
        if (onDetailClick(e)) return;
        const head = e.target.closest('.hx-as-req-head[data-rid]');
        if (head) navPush({ type: 'req', rid: head.dataset.rid });
      });
      sheet.addEventListener('keydown', (e) => {
        const t = e.target.closest && e.target.closest('.hx-as-ag[role="button"], .hx-ad-step-h[role="button"]');
        if (t && e.target === t && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); t.click(); }
      });
      const body = sheet.querySelector('.hx-as-body');
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && sheet.classList.contains('is-open')) { if (nav.length) navBack(); else openSheet(false); } });
      document.body.appendChild(sheet);
    }
    sheet.classList.toggle('is-open', on);
    if (!on && nav.length) { nav.length = 0; stopDetailPoll(); }
    if (on) { refresh(true); renderSheet(); }
  }

  function renderSheet() {
    if (!sheet || !sheet.classList.contains('is-open')) return;
    const body = sheet.querySelector('.hx-as-body');
    const sub = sheet.querySelector('.hx-as-sub');
    setHead();
    if (nav.length) { renderDetail(body, sub); return; }
    let html;
    if (state === 'consent') {
      sub.textContent = '';
      html = '<div class="hx-as-empty"><p>Allow this panel to read Command Centre agent state through Tabbie (signed-in only).</p><button type="button" class="hx-as-allow">Allow</button></div>';
    } else if (state === 'down' && !data) {
      sub.textContent = '';
      html = `<div class="hx-as-empty"><p class="hx-as-down">${esc(DOWN)}</p><p class="hx-as-hint">It comes back by itself when the helper is running again. On the computer: <code>bin/hub health</code></p><button type="button" class="hx-as-allow hx-as-retry">Try again</button></div>`;
    } else {
      const reqs = (data ? data.requests : []).filter((r) => (r.agents_list || []).length).slice().reverse();
      sub.textContent = reqs.length ? summaryText(allAgents()) : '';
      html = reqs.length ? reqs.map((r) => `<section class="hx-as-req">
          <button type="button" class="hx-as-req-head" data-rid="${esc(r.id)}" title="${esc(r.prompt || r.id)}" aria-label="Request details: ${esc(r.prompt || r.id)}"><span class="hx-as-req-p">${esc(r.prompt || r.id)}</span><span class="hx-as-chip" data-s="${esc(r.status)}">${esc(reqLabel(r.status))}</span>${icon('ArrowRight01Icon', 12)}</button>
          <div class="hx-agents-list">${r.agents_list.map((a) => drillRow(a, r.agents_list, r.id)).join('')}</div></section>`).join('')
        : '<div class="hx-as-empty"><p>No agents have run in this chat yet.</p></div>';
    }
    if (body.__html !== html) { body.innerHTML = html; body.__html = html; }
  }
  // Sheet as a table of contents: tap a request, land on its reply (the turn its agents started in).
  function jumpTo(rid) {
    const r = data && data.requests.find((x) => x.id === rid);
    const first = r ? Math.min(...(r.agents_list || []).map((a) => a.started).filter(Boolean)) : NaN;
    const w = isFinite(first) ? turnWindows().find((x) => first >= x.start - 1 && first < x.end) : null;
    if (!w) { toast("That reply isn't loaded in this view. Scroll up in the chat to load earlier messages."); return; }
    openSheet(false);
    const row = w.row;
    requestAnimationFrame(() => {
      row.classList.remove('hx-flash'); void row.offsetWidth; row.classList.add('hx-flash');
      row.scrollIntoView({ block: 'start', behavior: reduced() ? 'auto' : 'smooth' });
      clearTimeout(row.__hxFlash);
      row.__hxFlash = setTimeout(() => row.classList.remove('hx-flash'), 2400);
    });
  }
  const reqLabel = (s) => ({ executing: 'Running', planning: 'Planning', done: 'Done', failed: 'Failed', awaiting_answer: 'Needs you', awaiting_answers: 'Needs you', awaiting_approval: 'Awaiting approval', answered: 'Resuming', approved: 'Approved', cancelled: 'Cancelled', claude_limited: 'Usage limit', limit_blocked: 'Usage limit', stopped: 'Stopped', unanswered: 'Skipped' }[s] || s || '');

  // ── 2c. sheet click-through: everything an agent did ────────────────────
  // Push navigation inside the sheet: request → agent → its sub-agents / hand-offs. The header's round
  // back button pops one level (closes the sheet at the top). One line per step, tap to expand; long
  // fields arrive in full only on "Show more". Data: sidecar agent/detail (paged steps), agent/part (one
  // field in full), request/detail + request/part. Polled while the agent / request is still running.
  const nav = [];                 // [{type: 'req'|'agent', rid, aid?, scroll}]
  let listScroll = 0;
  const dcache = new Map();       // view key -> {state: loading|ok|err, d, busy, at}
  const fullText = new Map();     // field key -> {state: loading|ok|err, text, len, truncated}
  const openSteps = new Set();    // `${view}|${i}` expanded steps
  const openBlocks = new Set();   // `${view}|${block}` expanded blocks (brief, output, files…)
  let pollT = null;
  const enc = encodeURIComponent;
  const viewKey = (v) => (v.type === 'req' ? 'req|' + v.rid : 'ag|' + v.rid + '|' + v.aid);
  const reqRowsOf = (rid) => { const r = data && data.requests.find((x) => x.id === rid); return r ? (r.agents_list || []) : null; };
  const flip = (set, k) => (set.has(k) ? set.delete(k) : set.add(k));
  const fmtChars = (n) => (n >= 1000 ? (n / 1000).toFixed(n >= 1e4 ? 0 : 1).replace(/\.0$/, '') + 'k' : String(n)) + ' chars';
  const clock = (t) => { try { return new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch (_) { return ''; } };
  const setTitle = (t) => { const el = sheet && sheet.querySelector('.hx-as-title'); if (el && el.textContent !== t) el.textContent = t; };

  async function getJ(path) {
    const r = await fetch(API + path, { credentials: 'same-origin', cache: 'no-store' });
    if (r.status === 401 || r.status === 403) throw new Error('consent');
    if (r.status === 404) throw new Error('missing');
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }

  async function loadDetail(v, mode) { // mode: undefined (first load / reload) | 'more' (next page) | 'poll'
    const k = viewKey(v);
    const cur = dcache.get(k) || { state: 'loading' };
    if (cur.busy) return;
    cur.busy = true; dcache.set(k, cur);
    try {
      let d;
      if (v.type === 'req') d = await getJ('request/detail?rid=' + enc(v.rid));
      else {
        const have = cur.d ? cur.d.steps.length : 0;
        const base = 'agent/detail?rid=' + enc(v.rid) + '&agent=' + enc(v.aid);
        if (cur.d && (mode === 'more' || mode === 'poll')) {
          // next page, or (live) the last few steps again (a running tool finishes) plus anything new
          const off = mode === 'more' ? have : Math.max(0, have - 5);
          const p = await getJ(`${base}&offset=${off}&limit=150`);
          d = Object.assign({}, p, { steps: cur.d.steps.slice(0, off).concat(p.steps) });
        } else d = await getJ(`${base}&offset=0&limit=150`);
      }
      cur.state = 'ok'; cur.d = d; cur.at = Date.now();
    } catch (e) { cur.err = e.message; if (!cur.d) cur.state = 'err'; }
    finally { cur.busy = false; renderSheet(); schedulePoll(); }
  }
  function stopDetailPoll() { clearTimeout(pollT); pollT = null; }
  function schedulePoll() {
    stopDetailPoll();
    const v = nav[nav.length - 1];
    if (!v || !sheet || !sheet.classList.contains('is-open')) return;
    const e = dcache.get(viewKey(v));
    const live = e && e.d && (v.type === 'req' ? /^(executing|planning|answered|approved)$/.test(e.d.status || '') : e.d.running);
    if (live) pollT = setTimeout(() => { if (!document.hidden && nav[nav.length - 1] === v) loadDetail(v, 'poll'); else schedulePoll(); }, 3000);
  }

  function navPush(v) {
    const body = sheet.querySelector('.hx-as-body');
    if (nav.length) nav[nav.length - 1].scroll = body.scrollTop; else listScroll = body.scrollTop;
    nav.push(v);
    const e = dcache.get(viewKey(v));
    if (!e || !e.d || Date.now() - (e.at || 0) > 2000) loadDetail(v);
    renderSheet();
    body.scrollTop = 0;
    schedulePoll();
    const back = sheet.querySelector('.hx-as-close'); if (back && document.activeElement && sheet.contains(document.activeElement)) back.focus({ preventScroll: true });
  }
  function navBack() {
    nav.pop();
    const body = sheet.querySelector('.hx-as-body');
    renderSheet();
    body.scrollTop = nav.length ? (nav[nav.length - 1].scroll || 0) : listScroll;
    schedulePoll();
  }
  function setHead() {
    const back = sheet.querySelector('.hx-as-close');
    const lbl = nav.length ? 'Back' : 'Back to chat';
    if (back && back.getAttribute('aria-label') !== lbl) back.setAttribute('aria-label', lbl);
    sheet.querySelector('.hx-as-panel').classList.toggle('is-detail', nav.length > 0);
    if (!nav.length) setTitle('Agents');
  }

  // A row in the sheet that opens its agent (the same row the reply's Agents block shows).
  const drillRow = (a, list, rid) => `<div class="hx-as-ag" role="button" tabindex="0" data-rid="${esc(rid)}" data-aid="${esc(a.id)}" aria-label="Details: ${esc(shown(a.name))}">${agentRow(a, list)}<span class="hx-as-ag-go" aria-hidden="true">${icon('ArrowRight01Icon', 12)}</span></div>`;

  // Text with "Show more" (fetches the field in full) / "Show less".
  function fieldBox(fk, prev, part, i, mono) {
    if (!prev) return '';
    const f = fullText.get(fk);
    const txt = f && f.state === 'ok' ? f.text : prev.text;
    const lbl = !f ? 'Show more · ' + fmtChars(prev.len) : (f.state === 'loading' ? 'Loading…' : (f.state === 'err' ? "Couldn't load. Try again" : 'Show less'));
    const btn = prev.more ? `<button type="button" class="hx-ad-sm" data-full="${esc(fk)}" data-part="${esc(part)}" data-i="${esc(i)}">${esc(lbl)}</button>` : '';
    const cut = f && f.state === 'ok' && f.truncated ? `<div class="hx-ad-cut">First ${fmtChars(f.text.length)} of ${fmtChars(f.len)}</div>` : '';
    return `<pre class="hx-ad-pre${mono ? ' is-mono' : ''}${f && f.state === 'ok' ? ' is-full' : ''}">${esc(txt)}${!f && prev.more ? '…' : ''}</pre>${cut}${btn}`;
  }
  // A titled block (brief, output, result…): clamped to a few lines until opened.
  function block(vk, name, title, prev, part, extra) {
    if (!prev) return '';
    const bk = vk + '|' + name;
    const open = openBlocks.has(bk);
    const fk = vk + '|' + part + '|-1';
    const long = prev.more || prev.text.length > 360 || (prev.text.match(/\n/g) || []).length > 5;
    return `<section class="hx-ad-sec"><h3 class="hx-ad-h">${esc(title)}${extra ? `<span>${esc(extra)}</span>` : ''}</h3>
      <div class="hx-ad-blk${open || !long ? ' is-open' : ''}">${open ? fieldBox(fk, prev, part, -1, false) : `<pre class="hx-ad-pre">${esc(prev.text)}</pre>`}</div>
      ${long ? `<button type="button" class="hx-ad-sm" data-block="${esc(bk)}" data-fk="${esc(fk)}" data-part="${esc(part)}" data-more="${prev.more ? 1 : ''}">${open ? 'Show less' : 'Show more' + (prev.more ? ' · ' + fmtChars(prev.len) : '')}</button>` : ''}</section>`;
  }

  const TAG = { text: 'Said', thinking: 'Thought', user: 'You', prompt: 'Brief', event: 'Event', error: 'Error' };
  const toolTag = (t) => { t = String(t || 'tool'); const m = /^mcp__[^_]+(?:_[^_]+)*?__(.+)$/.exec(t); return (m ? m[1] : t.split('.').pop()).slice(0, 14); };
  function stepHtml(vk, s, d) {
    const sk = vk + '|' + s.i;
    const open = openSteps.has(sk);
    const isTool = s.kind === 'tool';
    const first = isTool ? s.label : (s.label || (s.text ? s.text.text.split('\n').find((x) => x.trim()) || '' : ''));
    const bad = (isTool && s.ok === false) || s.kind === 'error';
    const live = isTool && !s.done && d.running;
    const rel = s.t && d.agent.started ? '+' + fmtDur(s.t - d.agent.started) : '';
    const go = s.agent ? `<button type="button" class="hx-ad-open" data-rid="${esc(d.rid)}" data-aid="${esc(s.agent)}" aria-label="Open this agent">${icon('ArrowRight01Icon', 12)}</button>` : '';
    let inner = '';
    if (open) {
      if (isTool) {
        inner = (s.in ? `<div class="hx-ad-io"><span>Input</span>${fieldBox(sk + '|in', s.in, 'in', s.i, true)}</div>` : '')
          + (s.out ? `<div class="hx-ad-io"><span>${bad ? 'Error' : 'Output'}</span>${fieldBox(sk + '|out', s.out, 'out', s.i, true)}</div>` : (live ? '<div class="hx-ad-io"><span>Running…</span></div>' : ''))
          + (s.files && s.files.length ? `<div class="hx-ad-io"><span>Files</span><pre class="hx-ad-pre is-mono">${esc(s.files.join('\n'))}</pre></div>` : '');
        if (!inner) inner = '<div class="hx-ad-io"><span>No input or output was recorded for this step.</span></div>';
      } else inner = fieldBox(sk + '|text', s.text, 'text', s.i, false);
    }
    return `<li class="hx-ad-step" data-k="${esc(s.kind)}"${bad ? ' data-bad="1"' : ''}${live ? ' data-live="1"' : ''}>
      <div class="hx-ad-step-h" role="button" tabindex="0" data-sk="${esc(sk)}" aria-expanded="${open}"><span class="hx-ad-tag">${esc(isTool ? toolTag(s.tool) : (TAG[s.kind] || s.kind))}</span><span class="hx-ad-lbl">${esc(first || '…')}</span><span class="hx-ad-t">${esc(rel)}</span>${go}</div>
      ${open ? `<div class="hx-ad-step-b">${inner}</div>` : ''}</li>`;
  }

  function agentView(v, d, sub) {
    const vk = viewKey(v);
    const a = d.agent;
    const st = statusOf(a);
    setTitle(shown(a.name));
    const dur = st === 'running' ? fmtDur(elapsed(a)) : (a.duration_s != null ? fmtDur(a.duration_s) : '');
    const c = d.counts || {};
    sub.textContent = [STATUS[st] || st, a.model || PROVIDER_LABEL(a.provider), a.tokens ? fmtTokens(a.tokens) + ' tokens' : '', dur,
      c.tools ? `${c.tools} tool call${c.tools === 1 ? '' : 's'}` : '', c.failed ? `${c.failed} failed` : ''].filter(Boolean).join(' · ');
    const list = reqRowsOf(v.rid) || [];
    const kids = list.filter((x) => x.parent === a.id);
    const kv = [['Role', a.kind === 'delegate' ? 'Hand-off' : (a.kind === 'background' ? 'Background task' : (a.description || (a.parent ? 'Sub-agent' : 'Main agent')))],
      ['Model', a.model || PROVIDER_LABEL(a.provider)], ['Started', a.started ? clock(a.started) : ''],
      ['Output', a.output_tokens ? fmtTokens(a.output_tokens) + ' tokens' : ''], ['Turns', a.turns || ''],
      ['Record', d.source && d.source !== 'none' ? d.source : 'none']].filter((x) => x[1]);
    const par = d.parent ? `<div class="hx-ad-parent">Started by <button type="button" class="hx-ad-link" data-rid="${esc(d.rid)}" data-aid="${esc(d.parent.id)}">${esc(shown(d.parent.name))}</button></div>` : '';
    const steps = d.steps || [];
    const more = d.total > steps.length ? `<button type="button" class="hx-ad-more-steps">Show ${d.total - steps.length} more step${d.total - steps.length === 1 ? '' : 's'}</button>` : '';
    const hidden = c.thinking_hidden ? `<p class="hx-ad-note">${c.thinking_hidden} reasoning block${c.thinking_hidden === 1 ? ' is' : 's are'} encrypted by the provider and can't be shown.</p>` : '';
    const fk = vk + '|files';
    const fopen = openBlocks.has(fk);
    const files = (d.files || []).length ? `<section class="hx-ad-sec"><h3 class="hx-ad-h">Files changed<span>${d.files_total}</span></h3><ul class="hx-ad-files">${(fopen ? d.files : d.files.slice(0, 8)).map((f) => { const i = f.lastIndexOf('/'); return `<li title="${esc(f)}"><b>${esc(f.slice(i + 1))}</b><span>${esc(i > 0 ? f.slice(0, i) : '')}</span></li>`; }).join('')}</ul>${d.files.length > 8 ? `<button type="button" class="hx-ad-sm" data-block="${esc(fk)}">${fopen ? 'Show fewer' : `Show all ${d.files.length}`}</button>` : ''}</section>` : '';
    return `<div class="hx-ad">
      <dl class="hx-ad-kv">${kv.map(([k, x]) => `<div><dt>${esc(k)}</dt><dd>${esc(x)}</dd></div>`).join('')}</dl>
      ${par}${d.note ? `<p class="hx-ad-note">${esc(d.note)}</p>` : ''}
      ${block(vk, 'prompt', a.parent ? 'Brief it was given' : 'Prompt', d.prompt, 'prompt')}
      ${kids.length ? `<section class="hx-ad-sec"><h3 class="hx-ad-h">Sub-agents and hand-offs<span>${kids.length}</span></h3><div class="hx-agents-list">${kids.map((x) => drillRow(x, list, v.rid)).join('')}</div></section>` : ''}
      ${!d.total && d.note && st !== 'running' ? '' : `<section class="hx-ad-sec"><h3 class="hx-ad-h">Steps<span>${d.total}</span></h3>${steps.length ? `<ol class="hx-ad-steps">${steps.map((s) => stepHtml(vk, s, d)).join('')}</ol>` : `<p class="hx-ad-note">${st === 'running' ? 'Nothing recorded yet.' : 'No steps were recorded.'}</p>`}${more}${hidden}</section>`}
      ${block(vk, 'output', st === 'running' ? 'Latest output' : 'Final output', d.output, 'output')}
      ${files}</div>`;
  }

  function reqView(v, d, sub) {
    const vk = viewKey(v);
    setTitle('Request');
    const ex = d.executor || {};
    const u = d.usage || {};
    sub.textContent = [reqLabel(d.status), d.created ? clock(Date.parse(d.created) / 1000) : '', (d.agents || []).length ? `${d.agents.length} agent${d.agents.length === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ');
    const route = { astra: 'Planned first', claude: 'Claude Code', codex: 'Codex', hermes: 'Answered by Tabbie' }[d.route] || d.route;
    const kv = [['Route', [route, d.route_reason].filter(Boolean).join(' · ')], ['Planner', d.planner || ''],
      ['Worker', [ex.model || ex.provider, ex.effort ? ex.effort + ' effort' : '', ex.resumed ? 'resumed' : ''].filter(Boolean).join(' · ')],
      ['Tokens', u.input || u.output ? [u.input ? fmtTokens(u.input) + ' in' : '', u.cached ? fmtTokens(u.cached) + ' cached' : '', u.output ? fmtTokens(u.output) + ' out' : ''].filter(Boolean).join(' · ') : ''],
      ['Project', d.project || '']].filter((x) => x[1]);
    const list = reqRowsOf(v.rid) || d.agents || [];
    return `<div class="hx-ad">
      <dl class="hx-ad-kv">${kv.map(([k, x]) => `<div><dt>${esc(k)}</dt><dd>${esc(x)}</dd></div>`).join('')}</dl>
      ${block(vk, 'prompt', 'You asked', d.prompt, 'prompt')}
      ${(d.modifications || []).length ? `<section class="hx-ad-sec"><h3 class="hx-ad-h">Changes you asked for</h3><pre class="hx-ad-pre">${esc(d.modifications.join('\n\n'))}</pre></section>` : ''}
      ${block(vk, 'brief', 'Brief', d.brief, 'brief')}
      ${list.length ? `<section class="hx-ad-sec"><h3 class="hx-ad-h">Agents<span>${list.filter((a) => !isBg(a)).length}</span></h3><div class="hx-agents-list">${list.map((a) => drillRow(a, list, v.rid)).join('')}</div></section>` : ''}
      ${d.error ? block(vk, 'error', 'Error', d.error, 'error') : ''}
      ${block(vk, 'result', 'Result', d.result, 'result')}
      <button type="button" class="hx-agents-btn hx-ad-jump" data-rid="${esc(v.rid)}">${icon('ArrowRight01Icon', 12)}Go to the reply</button></div>`;
  }

  function renderDetail(body, sub) {
    const v = nav[nav.length - 1];
    const e = dcache.get(viewKey(v));
    let html;
    if (e && e.d) html = v.type === 'req' ? reqView(v, e.d, sub) : agentView(v, e.d, sub);
    else {
      const row = v.type === 'agent' ? (reqRowsOf(v.rid) || []).find((x) => x.id === v.aid) : null;
      setTitle(v.type === 'req' ? 'Request' : (row ? shown(row.name) : 'Agent'));
      sub.textContent = '';
      html = e && e.state === 'err'
        ? `<div class="hx-as-empty"><p>${esc(e.err === 'missing' ? 'Nothing was recorded for this one.' : (e.err === 'consent' ? 'Allow this panel to read agent details first.' : DOWN))}</p><button type="button" class="hx-as-allow hx-ad-retry">Try again</button></div>`
        : '<div class="hx-ad-load" role="status">Loading…</div>';
    }
    if (body.__html !== html) { body.innerHTML = html; body.__html = html; }
  }

  async function fetchFull(fk, part, i) {
    const v = nav[nav.length - 1];
    if (!v) return;
    fullText.set(fk, { state: 'loading' }); renderSheet();
    try {
      const j = v.type === 'req' ? await getJ(`request/part?rid=${enc(v.rid)}&part=${enc(part)}`)
        : await getJ(`agent/part?rid=${enc(v.rid)}&agent=${enc(v.aid)}&part=${enc(part)}&i=${enc(i)}`);
      fullText.set(fk, { state: 'ok', text: j.text, len: j.len, truncated: j.truncated });
    } catch (_) { fullText.set(fk, { state: 'err' }); }
    renderSheet();
  }
  function onDetailClick(e) {
    const t = e.target;
    let el;
    if ((el = t.closest('.hx-ad-retry'))) { const v = nav[nav.length - 1]; if (v) { dcache.delete(viewKey(v)); loadDetail(v); renderSheet(); } return true; }
    if ((el = t.closest('.hx-ad-jump'))) { jumpTo(el.dataset.rid); return true; }
    if ((el = t.closest('[data-aid][data-rid]'))) { e.stopPropagation(); navPush({ type: 'agent', rid: el.dataset.rid, aid: el.dataset.aid }); return true; }
    if ((el = t.closest('.hx-ad-more-steps'))) { el.disabled = true; el.textContent = 'Loading…'; loadDetail(nav[nav.length - 1], 'more'); return true; }
    if ((el = t.closest('[data-full]'))) {
      const f = fullText.get(el.dataset.full);
      if (f && f.state === 'loading') return true;
      if (f && f.state === 'ok') { fullText.delete(el.dataset.full); renderSheet(); } else fetchFull(el.dataset.full, el.dataset.part, el.dataset.i);
      return true;
    }
    if ((el = t.closest('[data-block]'))) {
      const bk = el.dataset.block;
      flip(openBlocks, bk);
      if (el.dataset.fk && !openBlocks.has(bk)) fullText.delete(el.dataset.fk);
      if (openBlocks.has(bk) && el.dataset.more && !fullText.has(el.dataset.fk)) fetchFull(el.dataset.fk, el.dataset.part, -1);
      else renderSheet();
      return true;
    }
    if ((el = t.closest('.hx-ad-step-h'))) { flip(openSteps, el.dataset.sk); renderSheet(); return true; }
    return false;
  }

  // ── 3. question cards ───────────────────────────────────────────────────
  const drafts = new Map(); // `${rid}:${round}:${questions}` -> {sel: [[...labels]], other: [...]}
  // A half-answered card survives a chat switch or reload: kept in sessionStorage until it is sent.
  const DKEY = 'hx-q-draft:';
  const dkey = (k) => { let h = 5381; for (let i = 0; i < k.length; i++) h = ((h * 33) ^ k.charCodeAt(i)) >>> 0; return DKEY + k.split(':').slice(0, 2).join(':') + ':' + h.toString(36); };
  const store = {
    get(k) { try { return JSON.parse(sessionStorage.getItem(dkey(k)) || 'null'); } catch (_) { return null; } },
    set(k, d) { try { if (d.sel.some((x) => x.length) || d.other.some((x) => x.trim())) sessionStorage.setItem(dkey(k), JSON.stringify({ sel: d.sel, other: d.other })); else sessionStorage.removeItem(dkey(k)); } catch (_) {} },
    del(k) { try { sessionStorage.removeItem(dkey(k)); } catch (_) {} },
  };

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
        card.addEventListener('keydown', (e) => onCardKey(e, card));
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
    if (!drafts.has(k)) {
      const d = { k, sel: q.questions.map(() => []), other: q.questions.map(() => ''), sent: false };
      const saved = store.get(k);
      if (saved && Array.isArray(saved.sel) && saved.sel.length === d.sel.length) {
        q.questions.forEach((x, i) => {
          const labels = (x.options || []).map((o) => o.label);
          d.sel[i] = (Array.isArray(saved.sel[i]) ? saved.sel[i] : []).filter((l) => labels.includes(l));
          d.other[i] = String((saved.other || [])[i] || '');
        });
      }
      drafts.set(k, d);
    }
    return drafts.get(k);
  }

  function renderCard(card) {
    const q = card.__q;
    const st = cardState(card);
    const d = draftFor(card);
    const who = q.source === 'astra-plan' || q.source === 'astra' ? 'Codex' : 'Claude';
    const pending = st.pending && !d.sent;
    if (!pending && !d.sent && (st.answer || card.__answer)) store.del(d.k);
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
          <div class="hx-q-opts" role="${multi ? 'group' : 'radiogroup'}" aria-label="${esc(x.header || x.question)}">${opts}</div>
          <input class="hx-q-other" data-q="${qi}" type="text" placeholder="Other…" autocomplete="off" enterkeyhint="send" aria-label="Other answer" value="${esc(d.other[qi])}"></fieldset>`;
      }).join('');
      const ready = q.questions.every((_, i) => d.sel[i].length || d.other[i].trim());
      const mod = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent) ? '⌘' : 'Ctrl+';
      html += `<div class="hx-q-foot"><span class="hx-q-note">Your answer resumes ${esc(who)}<span class="hx-q-keys"> · 1–9 picks, ${mod}↵ sends</span></span><button type="button" class="hx-q-send" aria-keyshortcuts="${mod === '⌘' ? 'Meta+Enter' : 'Control+Enter'}"${ready ? '' : ' disabled'}>Send answer</button></div>`;
    }
    card.classList.toggle('is-pending', pending);
    if (card.__html !== html) {
      const a = document.activeElement && card.contains(document.activeElement) ? document.activeElement : null;
      const want = card.__want || (a && a.matches('.hx-q-other') ? { other: a.dataset.q } : a && a.matches('.hx-q-opt') ? { q: a.dataset.q, o: a.dataset.o } : a && a.matches('.hx-q-send') ? { send: 1 } : null);
      card.__want = null;
      card.innerHTML = html; card.__html = html;
      if (want && want.other != null) { const inp = card.querySelector(`.hx-q-other[data-q="${want.other}"]`); if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); } }
      else if (want && want.q != null) { const b = card.querySelector(`.hx-q-opt[data-q="${want.q}"][data-o="${want.o}"]`); if (b) b.focus({ preventScroll: true }); }
      else if (want && want.send) { const b = card.querySelector('.hx-q-send'); if (b && !b.disabled) b.focus({ preventScroll: true }); }
    } else card.__want = null;
  }

  function onCardInput(e, card) {
    const inp = e.target.closest('.hx-q-other');
    if (!inp) return;
    const d = draftFor(card); const qi = +inp.dataset.q;
    d.other[qi] = inp.value;
    if (inp.value.trim() && !card.__q.questions[qi].multiSelect) d.sel[qi] = [];
    store.set(d.k, d);
    renderCard(card);
  }

  function onCardClick(e, card) {
    const opt = e.target.closest('.hx-q-opt');
    if (opt) {
      toggle(card, +opt.dataset.q, +opt.dataset.o);
      return;
    }
    if (e.target.closest('.hx-q-send')) send(card);
  }

  function toggle(card, qi, oi) {
    const d = draftFor(card); const x = card.__q.questions[qi];
    const o = x && (x.options || [])[oi];
    if (!o) return;
    const label = o.label;
    if (x.multiSelect) d.sel[qi] = d.sel[qi].includes(label) ? d.sel[qi].filter((y) => y !== label) : [...d.sel[qi], label];
    else { d.sel[qi] = d.sel[qi][0] === label ? [] : [label]; d.other[qi] = ''; }
    store.set(d.k, d);
    card.__want = { q: qi, o: oi }; // Safari doesn't focus buttons on click: keep keys working on this card
    renderCard(card);
  }

  const isReady = (card) => { const d = draftFor(card); return card.__q.questions.every((_, i) => d.sel[i].length || d.other[i].trim()); };

  // Keyboard: 1-9 picks an option of the focused (else first unanswered) question, arrows move
  // between options, Enter in "Other…" or Cmd/Ctrl+Enter anywhere in the card sends.
  function onCardKey(e, card) {
    if (!card.classList.contains('is-pending') || e.isComposing) return;
    const t = e.target;
    const inOther = t.matches && t.matches('.hx-q-other');
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey || (inOther && !e.shiftKey))) {
      e.preventDefault();
      if (isReady(card)) send(card);
      return;
    }
    if (inOther || e.metaKey || e.ctrlKey || e.altKey) return;
    if (/^[1-9]$/.test(e.key)) {
      const d = draftFor(card); const qs = card.__q.questions;
      const fs = t.closest && t.closest('.hx-q-item');
      const own = fs && fs.querySelector('[data-q]');
      let qi = own ? +own.dataset.q : qs.findIndex((_, i) => !d.sel[i].length && !d.other[i].trim());
      if (qi < 0) qi = 0;
      e.preventDefault();
      toggle(card, qi, +e.key - 1);
      return;
    }
    const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
    if (step && t.matches && t.matches('.hx-q-opt')) {
      const sib = [...card.querySelectorAll(`.hx-q-opt[data-q="${t.dataset.q}"]`)];
      const n = sib[(sib.indexOf(t) + step + sib.length) % sib.length];
      if (n) { e.preventDefault(); n.focus(); }
    }
  }

  // Send text as a normal chat message without losing what the user was typing: stash the composer
  // (text + attached files), send, then put it back so upstream's draft save keeps it.
  async function sendKeeping(text) {
    const box = document.getElementById('msg');
    if (!box || typeof window.send !== 'function') return false;
    const keep = box.value;
    let files = null;
    try { if (typeof S !== 'undefined' && S && Array.isArray(S.pendingFiles) && S.pendingFiles.length) { files = S.pendingFiles; S.pendingFiles = []; if (typeof renderTray === 'function') renderTray(); } } catch (_) {}
    box.value = text;
    box.dispatchEvent(new Event('input', { bubbles: true }));
    let ok = false;
    try { await window.send(); ok = box.value !== text; } catch (e) { console.warn('cc-agents send', e); } finally {
      if (!ok && box.value === text) box.value = ''; // not taken (another send in flight): don't leave it there
      if (!box.value.trim() && keep) {
        box.value = keep;
        box.dispatchEvent(new Event('input', { bubbles: true }));
        try { if (typeof autoResize === 'function') autoResize(); } catch (_) {}
      }
      if (files) { try { S.pendingFiles = files.concat(S.pendingFiles || []); if (typeof renderTray === 'function') renderTray(); } catch (_) {} }
    }
    return ok;
  }

  function send(card) {
    const d = draftFor(card); const q = card.__q;
    const lines = q.questions.map((x, i) => {
      const parts = [...d.sel[i]]; if (d.other[i].trim()) parts.push(d.other[i].trim());
      return `${x.header}: ${parts.join(', ')}`;
    });
    const text = lines.join('\n');
    if (!document.getElementById('msg') || typeof window.send !== 'function') return;
    d.sent = true; d.sentText = text;
    store.del(d.k);
    renderCard(card);
    sendKeeping(text).then((ok) => {
      if (ok) return;
      d.sent = false; store.set(d.k, d); renderCard(card);
      toast("Couldn't send your answer. Try again in a moment.");
    });
  }

  // ── 4. plan approval buttons ────────────────────────────────────────────
  // Under the latest reply holding the planner's proposal footer, while the request awaits approval.
  // They send what the router already understands (router._APPROVE / _CANCEL): "yes, go" / "cancel".
  const PLAN_FOOT = 'Nothing executes until you approve';
  let planBusy = null; // reply row an Approve/Cancel was sent from
  function renderPlanActions() {
    const rows = [...document.querySelectorAll('#messages .msg-row.assistant-turn')];
    const last = rows[rows.length - 1];
    let target = null;
    if (last && isLatestReply(last) && (last.textContent || '').includes(PLAN_FOOT)) {
      const fresh = dataSid === sid() && state === 'ok' && data;
      const waiting = !fresh || data.requests.some((r) => r.status === 'awaiting_approval');
      if (waiting && planBusy !== last) {
        target = [...last.querySelectorAll('p')].reverse().find((p) => p.textContent.includes(PLAN_FOOT) && !p.closest('pre, .hx-q')) || null;
      }
    }
    document.querySelectorAll('#messages .hx-plan-act').forEach((el) => { if (!target || el.previousElementSibling !== target) el.remove(); });
    if (!target || (target.nextElementSibling && target.nextElementSibling.classList.contains('hx-plan-act'))) return;
    const bar = document.createElement('div');
    bar.className = 'hx-plan-act';
    bar.setAttribute('role', 'group');
    bar.setAttribute('aria-label', 'Plan');
    bar.innerHTML = `<button type="button" class="hx-pa-btn is-go" data-pa="go">${icon('Tick02Icon', 14)}<span>Approve</span></button>
      <button type="button" class="hx-pa-btn" data-pa="change"><span>Change…</span></button>
      <button type="button" class="hx-pa-btn is-quiet" data-pa="cancel"><span>Cancel</span></button>`;
    bar.addEventListener('click', (e) => onPlanClick(e, bar, last));
    target.after(bar);
  }

  function onPlanClick(e, bar, row) {
    const b = e.target.closest('[data-pa]');
    if (!b) return;
    const what = b.dataset.pa;
    if (what === 'change') {
      const box = document.getElementById('msg');
      if (!box) return;
      const cur = box.value.trim();
      box.value = /^yes\b/i.test(cur) ? box.value : 'yes but ' + cur;
      box.dispatchEvent(new Event('input', { bubbles: true }));
      try { if (typeof autoResize === 'function') autoResize(); } catch (_) {}
      box.focus();
      box.setSelectionRange(box.value.length, box.value.length);
      return;
    }
    bar.querySelectorAll('button').forEach((x) => { x.disabled = true; });
    b.classList.add('is-busy');
    planBusy = row;
    sendKeeping(what === 'go' ? 'yes, go' : 'cancel').then((ok) => {
      if (ok) return;
      planBusy = null;
      bar.querySelectorAll('button').forEach((x) => { x.disabled = false; });
      b.classList.remove('is-busy');
      toast("Couldn't send that. Try again in a moment.");
    });
  }

  // ── hub usage label ─────────────────────────────────────────────────────
  // Upstream's reply footer shows the HUB's tokens ("39.6k in · 95 out") next to the model chip, which
  // worker-label.js renames to the worker (e.g. Opus 5.5): those tokens were the hub's routing and relay,
  // not the worker's. On replies a worker did (a "Route: …" line that isn't Tabbie itself), label them.
  const ROUTE_RX = /Route:\s*([^—\n]+?)\s+—/;
  function labelHubUsage() {
    document.querySelectorAll('#messages .assistant-turn').forEach((turn) => {
      const m = ROUTE_RX.exec((turn.textContent || '').slice(0, 400));
      if (!m || /^Tabbie\b/.test(m[1].trim())) return;
      turn.classList.add('hx-wturn');  // a worker did this reply: its own line carries the tokens (CSS hides the hub's)
      turn.querySelectorAll('.lf-tokens, .msg-usage-inline').forEach((el) => {
        if (el.closest('.live-run-status')) return;  // the live "Running" strip: leave it alone
        const txt = el.textContent || '';
        if (!/\bin\b.*\bout\b/.test(txt) || txt.startsWith('Hub ')) return;
        el.textContent = 'Hub · ' + txt;
        el.title = 'Tokens the hub used to route and relay this reply. The worker’s own tokens are in the Agents block.';
      });
    });
  }

  // ── loop ────────────────────────────────────────────────────────────────
  function render() { try { renderBlocks(); renderPill(); renderSheet(); scanQuestions(); renderPlanActions(); labelHubUsage(); } catch (e) { console.warn('cc-agents', e); } }

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
    if (muts.every((m) => { const t = m.target.nodeType === 1 ? m.target : m.target.parentElement; return t && t.closest('.hx-agents, .hx-q, .hx-plan-act, .hx-narr, .hx-shot-box'); })) return;
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
