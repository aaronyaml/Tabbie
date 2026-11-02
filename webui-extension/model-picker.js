/* Hermes — composer model picker. Chooses the EXECUTOR model for this conversation (the worker
 * that runs each request), replacing upstream's hub-model chip + reasoning chip (hidden in CSS).
 *   step 1: Anthropic / OpenAI     step 2: that provider's models (+ optional effort)
 * Phone: bottom sheet. Desktop: popover above the chip.
 * Data: cc-agents sidecar via the WebUI's authenticated, consent-gated proxy:
 *   GET  api/extensions/cc-agents/sidecar/executor?id=<session>  -> {choice, catalog}
 *   GET  api/extensions/cc-agents/sidecar/executor  (no id: new-chat home) -> {choice: the default, catalog}
 *   POST api/extensions/cc-agents/sidecar/executor {id, provider, model, effort}
 *   POST api/extensions/cc-agents/sidecar/agents {enabled: ['claude', 'codex']}  (Agents row, step 1)
 *   GET  api/extensions/cc-agents/sidecar/agents -> {dynamic, dynamic_available, linked}; POST {dynamic: bool}
 *        (Agents row "Hand-offs": Claude Code and Codex may pass part of a job to each other; "Takeover": a job
 *        at one agent's usage limit carries on in the other; both on by default,
 *        greyed out unless both are enabled and signed in)
 * A pick made before the chat exists is held and saved as soon as the session id appears (retried
 * while the sidecar catches up). If the Mac can't be reached, the panel says so and offers Try again.
 */
(() => {
  'use strict';
  if (window.__hxModelPicker) return;
  window.__hxModelPicker = true;

  const API = 'api/extensions/cc-agents/sidecar/executor';
  const AGENTS_API = 'api/extensions/cc-agents/sidecar/agents';
  const AGENTS = [['claude', 'Claude'], ['codex', 'OpenAI']];
  const PROVIDER_ICON = { anthropic: 'AiBrain01Icon', openai: 'AiMagicIcon' };
  const EFFORT_LABEL = { '': 'Auto', low: 'Low', medium: 'Medium', high: 'High' };
  const FALLBACK = {
    providers: {
      anthropic: { name: 'Anthropic', worker: 'Claude Code', models: [
        { id: 'claude-opus-5-5', name: 'Opus 5.5', desc: 'Most capable' },
        { id: 'claude-fable-5-1', name: 'Fable 5.1', desc: 'Deepest reasoning, slower' },
        { id: 'claude-sonnet-5-5', name: 'Sonnet 5.5', desc: 'Fast and capable' },
        { id: 'claude-haiku-5-5', name: 'Haiku 5.5', desc: 'Quickest' }] },
      openai: { name: 'OpenAI', worker: 'Codex', models: [
        { id: 'gpt-6-astra', name: 'GPT-6 Astra', desc: 'Frontier intelligence' },
        { id: 'gpt-6.1-sol', name: 'GPT-6.1 Sol', desc: 'Everyday workhorse' },
        { id: 'gpt-6.1-terra', name: 'GPT-6.1 Terra', desc: 'Balanced' },
        { id: 'gpt-6-luna', name: 'GPT-6 Luna', desc: 'Quickest' }] },
    },
    efforts: ['low', 'medium', 'high'],
  };
  const DEFAULT = { provider: 'anthropic', model: 'claude-opus-5-5', effort: null };

  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const sid = () => { try { return (typeof S !== 'undefined' && S && S.session && S.session.session_id) || ''; } catch (_) { return ''; } };
  const phone = () => window.matchMedia('(max-width: 640px)').matches;

  let catalog = FALLBACK;
  let choice = Object.assign({}, DEFAULT);
  let loadedSid = null;
  let pending = null;      // pick made while no session exists yet
  let notice = '';
  let offline = false;     // last fetch failed (network / 5xx): notice + Try again, auto-retry in tick
  let retryAt = 0;
  let pendingTries = 0;
  const OFFLINE = 'Can’t reach Tabbie on your Mac right now. Showing the last model.';
  const NO_CONSENT = 'Allow “Agents & questions” in Settings › Extensions to switch models.';
  let handoffs = { dynamic: true, available: null, known: false };  // GET /agents (known: answered once)
  let step = 1;
  let browsing = 'anthropic';
  let open = false;

  const modelOf = (p, m) => ((catalog.providers[p] || {}).models || []).find((x) => x.id === m);
  const nameOf = (c) => (modelOf(c.provider, c.model) || {}).name || c.name || 'Model';
  const pick = (c) => ({ provider: c.provider, model: c.model, effort: c.effort || null });
  function setOffline(on) {
    offline = on;
    retryAt = on ? Date.now() + 10000 : 0;
    if (on) notice = OFFLINE; else if (notice === OFFLINE) notice = '';
  }

  // ── data ────────────────────────────────────────────────────────────────
  // s = session id, or '' for the new-chat home (the server answers with the default a new chat gets).
  async function load(s) {
    loadedSid = s || null;
    try {
      const r = await fetch(s ? API + '?id=' + encodeURIComponent(s) : API, { credentials: 'same-origin', cache: 'no-store' });
      if (r.status === 403 || r.status === 401) { setOffline(false); notice = NO_CONSENT; return; }
      if (r.status >= 500) { setOffline(true); return; }
      if (!r.ok) { if (s) setOffline(false); return; }  // 400 with no id = older sidecar: keep the fallback
      const j = await r.json();
      if (sid() !== s) return;
      setOffline(false);
      if (j.catalog && j.catalog.providers) catalog = j.catalog;
      if (j.choice && !(s === '' && pending)) choice = pick(j.choice);
      if (notice === NO_CONSENT) notice = '';
      if (pending && !modelOf(pending.provider, pending.model)) { pending = null; choice = pick(j.choice); }
    } catch (_) { setOffline(true); /* sidecar down: keep showing the last known pick */ }
    finally { paintChip(); if (open) paint(); }
  }
  const loadCatalog = () => load('');

  async function loadHandoffs() {
    try {
      const r = await fetch(AGENTS_API, { credentials: 'same-origin', cache: 'no-store' });
      if (!r.ok) return;
      const j = await r.json();
      if (typeof j.dynamic !== 'boolean') return;  // older sidecar: no Hand-offs pill
      handoffs = { dynamic: j.dynamic, available: !!j.dynamic_available, known: true, takeover: j.takeover !== false };
    } catch (_) { /* keep the last known state */ }
    finally { if (open) paint(); }
  }
  async function toggleHandoffs() {
    if (!handoffs.available) return;
    const next = !handoffs.dynamic;
    handoffs = Object.assign({}, handoffs, { dynamic: next });
    paint();
    try {
      const j = await post(AGENTS_API, { dynamic: next });
      if (j && typeof j.dynamic === 'boolean') handoffs = { dynamic: j.dynamic, available: !!j.dynamic_available, known: true, takeover: j.takeover !== false };
      notice = '';
    } catch (e) {
      handoffs = Object.assign({}, handoffs, { dynamic: !next });
      notice = statusOf(e) === 403 ? 'Allow “Agents & questions” in Settings › Extensions to change agents.' : 'Couldn’t save. Try again.';
    }
    paint();
  }

  // Takeover: a job at one agent's usage limit carries on in the other (same task). On by default.
  async function toggleTakeover() {
    const next = !handoffs.takeover;
    handoffs = Object.assign({}, handoffs, { takeover: next });
    paint();
    try {
      const j = await post(AGENTS_API, { takeover: next });
      if (j && typeof j.takeover === 'boolean') handoffs = Object.assign({}, handoffs, { takeover: j.takeover });
      notice = '';
    } catch (e) {
      handoffs = Object.assign({}, handoffs, { takeover: !next });
      notice = statusOf(e) === 403 ? 'Allow “Agents & questions” in Settings › Extensions to change agents.' : 'Couldn’t save. Try again.';
    }
    paint();
  }

  async function post(url, data) {
    const body = JSON.stringify(data);
    if (typeof window.api === 'function') {
      return window.api('/' + url, { method: 'POST', body, headers: { 'Content-Type': 'application/json' }, retries: 0, timeoutToast: false });
    }
    const r = await fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body });
    if (!r.ok) { const err = new Error(String(r.status)); err.status = r.status; throw err; }
    return r.json();
  }
  // HTTP status of a failed post(): WebUI api() sets err.status (its message is the server's error text).
  const statusOf = (e) => (e && Number(e.status)) || 0;

  // flushing = this is the held new-chat pick being saved now that the chat exists: on a transient
  // failure keep it held and retry from tick() instead of reverting it and showing "Couldn't save".
  async function save(next, flushing) {
    const prev = flushing ? next : choice;
    choice = next;
    paintChip();
    const s = sid();
    if (!s) { pending = next; return; }
    try {
      const j = await post(API, { id: s, provider: next.provider, model: next.model, effort: next.effort || '' });
      if (j && j.choice && sid() === s) choice = pick(j.choice);
      if (j && j.catalog && j.catalog.providers) catalog = j.catalog;
      notice = ''; setOffline(false); pendingTries = 0;
    } catch (e) {
      const code = statusOf(e);
      if (code === 401 || code === 403) { notice = NO_CONSENT; if (!flushing) choice = prev; }
      else if (flushing && code !== 400 && ++pendingTries < 8 && sid() === s) { pending = next; return; }
      else if (code === 400) { pendingTries = 0; notice = 'That model isn’t available any more. Pick another.'; await load(s); return; }
      else { pendingTries = 0; choice = prev; setOffline(true); notice = 'Couldn’t save. Check the connection to your Mac.'; }
      if (open) paint();
    }
    paintChip();
  }

  // Which coding agents this install uses (config/agents.yaml). At least one stays on; the server
  // drops the other provider from the catalog and moves this chat's pick if it was on that side.
  async function toggleAgent(a) {
    const on = (catalog.agents || AGENTS.map((x) => x[0])).slice();
    const next = on.includes(a) ? on.filter((x) => x !== a) : on.concat(a);
    if (!next.length) { notice = 'Keep at least one agent on.'; paint(); return; }
    try {
      await post(AGENTS_API, { enabled: next });
      catalog = Object.assign({}, catalog, { agents: next });
      notice = '';
      await load(sid());
    } catch (e) {
      notice = statusOf(e) === 403 ? 'Allow “Agents & questions” in Settings › Extensions to change agents.' : 'Couldn’t save. Try again.';
      paint();
    }
  }

  let busy = false;
  async function tick() {
    ensureChip();
    if (busy) return;
    const s = sid();
    if (!handoffs.known && !hoAsked) { hoAsked = true; loadHandoffs(); }  // so the first open already has the pill
    busy = true;
    try {
      if (s && pending) { const p = pending; pending = null; loadedSid = s; await save(p, true); return; }
      if (s && s !== loadedSid) { await load(s); return; }
      if (!s && (loadedSid || first)) { first = false; pending = null; await loadCatalog(); return; }  // new-chat home: the real default
      if (offline && Date.now() > retryAt) await load(s);  // recovers by itself once the sidecar is back
    } finally { busy = false; }
  }
  let first = true, hoAsked = false;

  // ── chip ────────────────────────────────────────────────────────────────
  let chip = null;
  function ensureChip() {
    if (chip && document.body.contains(chip)) return;
    const anchor = document.querySelector('.composer-model-wrap');
    if (!anchor || !anchor.parentNode) return;
    chip = document.createElement('button');
    chip.type = 'button';
    chip.id = 'hxModelChip';
    chip.className = 'hx-mp-chip';
    chip.setAttribute('aria-haspopup', 'dialog');
    chip.setAttribute('aria-expanded', 'false');
    chip.addEventListener('click', (e) => { e.stopPropagation(); open ? close() : show(); });
    anchor.parentNode.insertBefore(chip, anchor.nextSibling);
    paintChip();
  }
  function paintChip() {
    if (!chip) return;
    const label = nameOf(choice);
    chip.innerHTML = `<span class="hx-mp-chip-ic">${icon(PROVIDER_ICON[choice.provider], 15)}</span>`
      + `<span class="hx-mp-chip-label">${esc(label)}</span>`
      + `<span class="hx-mp-chip-chev">${icon('ArrowDown01Icon', 12)}</span>`;
    chip.title = 'Model for this conversation';
    chip.setAttribute('aria-label', 'Model: ' + label);
  }

  // ── panel ───────────────────────────────────────────────────────────────
  let root = null, panel = null, track = null, frame = null;
  function build() {
    root = document.createElement('div');
    root.className = 'hx-mp-root';
    root.innerHTML = '<div class="hx-mp-scrim"></div>'
      + '<div class="hx-mp-panel" role="dialog" aria-modal="true" aria-label="Choose a model">'
      + '<div class="hx-mp-grab" aria-hidden="true"></div>'
      + '<div class="hx-mp-frame"><div class="hx-mp-track"><section class="hx-mp-pane" data-step="1"></section>'
      + '<section class="hx-mp-pane" data-step="2"></section></div></div></div>';
    document.body.appendChild(root);
    panel = root.querySelector('.hx-mp-panel');
    frame = root.querySelector('.hx-mp-frame');
    track = root.querySelector('.hx-mp-track');
    root.querySelector('.hx-mp-scrim').addEventListener('click', close);
    panel.addEventListener('click', onClick);
    panel.addEventListener('keydown', onKey);
    let y0 = null;  // phone: drag the sheet down to dismiss
    panel.addEventListener('touchstart', (e) => { y0 = panel.scrollTop <= 0 ? e.touches[0].clientY : null; }, { passive: true });
    panel.addEventListener('touchmove', (e) => {
      if (y0 == null || !phone()) return;
      const dy = Math.max(0, e.touches[0].clientY - y0);
      panel.style.transition = 'none'; panel.style.transform = `translateY(${dy}px)`;
    }, { passive: true });
    panel.addEventListener('touchend', (e) => {
      if (y0 == null || !phone()) return;
      const dy = e.changedTouches[0].clientY - y0; y0 = null;
      panel.style.transition = ''; panel.style.transform = '';
      if (dy > 90) close();
    });
  }

  function paneOne() {
    const rows = Object.keys(catalog.providers).map((p) => {
      const pv = catalog.providers[p];
      const cur = choice.provider === p;
      const sub = cur ? nameOf(choice) : pv.worker;
      return `<button type="button" class="hx-mp-row hx-mp-prov${cur ? ' is-current' : ''}" data-provider="${esc(p)}">`
        + `<span class="hx-mp-tile">${icon(PROVIDER_ICON[p], 22)}</span>`
        + `<span class="hx-mp-text"><span class="hx-mp-name">${esc(pv.name)}</span><span class="hx-mp-desc">${esc(sub)}</span></span>`
        + `<span class="hx-mp-go">${icon('ArrowRight01Icon', 16)}</span></button>`;
    }).join('');
    const on = catalog.agents || AGENTS.map((x) => x[0]);
    const agents = AGENTS.map(([a, label]) => `<button type="button" class="hx-mp-seg${on.includes(a) ? ' is-on' : ''}" data-agent="${a}" aria-pressed="${on.includes(a)}">${label}</button>`).join('');
    // Hand-offs: same pill style. Unavailable (an agent off or signed out) = greyed, with a caption.
    const unavail = handoffs.known && handoffs.available === false;
    const hoOn = handoffs.dynamic && !unavail;
    const ho = !handoffs.known ? ''
      : `<button type="button" class="hx-mp-seg hx-mp-handoffs${hoOn ? ' is-on' : ''}${unavail ? ' is-unavail' : ''}" data-handoffs`
        + ` aria-pressed="${hoOn}"${unavail ? ' aria-disabled="true" disabled' : ''}`
        + ` title="${unavail ? 'Needs Claude Code and Codex signed in' : 'Claude Code and Codex may hand part of a job to each other'}">Hand-offs</button>`;
    const bothOn = on.includes('claude') && on.includes('codex');
    const tkOn = handoffs.takeover !== false && bothOn;
    const tk = !handoffs.known ? ''
      : `<button type="button" class="hx-mp-seg hx-mp-takeover${tkOn ? ' is-on' : ''}${bothOn ? '' : ' is-unavail'}" data-takeover`
        + ` aria-pressed="${tkOn}"${bothOn ? '' : ' aria-disabled="true" disabled'}`
        + ` title="${bothOn ? 'When Claude or Codex hits its usage limit, the other carries on with the same task' : 'Needs Claude Code and Codex both on'}">Takeover</button>`;
    return `<header class="hx-mp-head"><span class="hx-mp-title">Model</span></header>`
      + `<div class="hx-mp-list">${rows}</div>`
      + `<div class="hx-mp-effort hx-mp-agents"><span class="hx-mp-effort-label">Agents</span><div class="hx-mp-segs" role="group" aria-label="Coding agents on this Mac">${agents}${ho}${tk}</div></div>`
      + (unavail ? '<p class="hx-mp-cap hx-mp-ho-cap">Hand-offs: needs Claude Code and Codex signed in</p>' : '')
      + (notice ? noteHtml() : `<p class="hx-mp-cap">${sid() ? 'For this conversation' : 'For this new chat'}</p>`);
  }

  function paneTwo() {
    const pv = catalog.providers[browsing] || { models: [] };
    const rows = pv.models.map((m) => {
      const on = choice.provider === browsing && choice.model === m.id;
      return `<button type="button" class="hx-mp-row hx-mp-model${on ? ' is-on' : ''}" data-model="${esc(m.id)}" aria-pressed="${on}">`
        + `<span class="hx-mp-text"><span class="hx-mp-name">${esc(m.name)}</span><span class="hx-mp-desc">${esc(m.desc)}</span></span>`
        + `<span class="hx-mp-check">${on ? icon('Tick02Icon', 18) : ''}</span></button>`;
    }).join('');
    const levels = ['', ...(catalog.efforts || [])];
    const eff = choice.effort || '';
    const seg = levels.map((l) => `<button type="button" class="hx-mp-seg${eff === l ? ' is-on' : ''}" data-effort="${l}" aria-pressed="${eff === l}">${EFFORT_LABEL[l] || l}</button>`).join('');
    return `<header class="hx-mp-head hx-mp-head-2"><button type="button" class="hx-mp-back" data-back aria-label="Back">${icon('ArrowLeft01Icon', 18)}</button>`
      + `<span class="hx-mp-title">${esc(pv.name || '')}</span><span class="hx-mp-spacer"></span></header>`
      + `<div class="hx-mp-list">${rows}</div>`
      + `<div class="hx-mp-effort"><span class="hx-mp-effort-label">Effort</span><div class="hx-mp-segs" role="group" aria-label="Effort">${seg}</div></div>`
      + (notice ? noteHtml() : '');
  }
  function noteHtml() {
    return `<div class="hx-mp-note" role="status"><span>${esc(notice)}</span>`
      + (offline ? '<button type="button" class="hx-mp-retry" data-retry>Try again</button>' : '') + '</div>';
  }

  function paint() {
    const [a, b] = track.querySelectorAll('.hx-mp-pane');
    a.innerHTML = paneOne();
    b.innerHTML = paneTwo();
    a.inert = step !== 1; b.inert = step !== 2;
    track.style.transform = step === 2 ? 'translateX(-50%)' : '';
    fit();
  }
  function fit() {
    const pane = track.querySelectorAll('.hx-mp-pane')[step - 1];
    frame.style.height = pane.scrollHeight + 'px';
    place(pane.scrollHeight + 24);
  }
  // Desktop popover: above the chip, or below it when the composer sits high (new-chat home).
  function place(h) {
    if (phone()) { panel.style.left = panel.style.bottom = panel.style.top = panel.style.width = ''; return; }
    const r = chip.getBoundingClientRect();
    const w = 320;
    const need = h || panel.offsetHeight || 320;
    panel.style.width = w + 'px';
    panel.style.left = Math.max(12, Math.min(r.left, window.innerWidth - w - 12)) + 'px';
    const below = r.top - 8 < need + 12 && window.innerHeight - r.bottom > r.top;
    panel.classList.toggle('is-below', below);
    if (below) { panel.style.bottom = ''; panel.style.top = (r.bottom + 8) + 'px'; }
    else { panel.style.top = ''; panel.style.bottom = Math.max(12, window.innerHeight - r.top + 8) + 'px'; }
  }

  function go(n, focusSel) {
    step = n;
    paint();
    const pane = track.querySelectorAll('.hx-mp-pane')[n - 1];
    const f = pane.querySelector(focusSel || '.hx-mp-row.is-on, .hx-mp-row.is-current, .hx-mp-row');
    if (f) setTimeout(() => f.focus({ preventScroll: true }), 30);
  }

  function show() {
    if (!chip) return;
    if (!root) build();
    if (sid() ? sid() !== loadedSid : !pending) load(sid());
    loadHandoffs();
    browsing = choice.provider;
    step = 1;
    root.classList.toggle('is-sheet', phone());
    place();
    paint();
    root.classList.add('is-mounted');
    requestAnimationFrame(() => requestAnimationFrame(() => root.classList.add('is-open')));
    open = true;
    chip.setAttribute('aria-expanded', 'true');
    go(1);
    document.addEventListener('pointerdown', outside, true);
    window.addEventListener('resize', onResize);
  }
  function close() {
    if (!open) return;
    open = false;
    root.classList.remove('is-open');
    chip && chip.setAttribute('aria-expanded', 'false');
    document.removeEventListener('pointerdown', outside, true);
    window.removeEventListener('resize', onResize);
    setTimeout(() => { if (!open) root.classList.remove('is-mounted'); }, 260);
    if (chip && panel.contains(document.activeElement)) chip.focus({ preventScroll: true });
  }
  function outside(e) { if (!panel.contains(e.target) && !(chip && chip.contains(e.target))) close(); }
  function onResize() { root.classList.toggle('is-sheet', phone()); place(); fit(); }

  function onClick(e) {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.hasAttribute('data-retry')) { notice = ''; offline = false; t.disabled = true; t.textContent = 'Trying…'; load(sid()); return; }
    if (t.dataset.agent) { toggleAgent(t.dataset.agent).then(loadHandoffs); return; }
    if (t.hasAttribute('data-handoffs')) { toggleHandoffs(); return; }
    if (t.hasAttribute('data-takeover')) { toggleTakeover(); return; }
    if (t.dataset.provider) { browsing = t.dataset.provider; go(2); return; }
    if (t.hasAttribute('data-back')) { go(1, `[data-provider="${browsing}"]`); return; }
    if (t.dataset.model) {
      save({ provider: browsing, model: t.dataset.model, effort: choice.effort || null });
      paint();
      setTimeout(close, 140);
      return;
    }
    if (t.hasAttribute('data-effort')) {
      const effort = t.dataset.effort || null;
      const base = choice.provider === browsing ? choice : { provider: browsing, model: catalog.providers[browsing].models[0].id };
      save({ provider: base.provider, model: base.model, effort });
      paint();
    }
  }
  function onKey(e) {
    if (e.key === 'Escape') { e.preventDefault(); step === 2 ? go(1, `[data-provider="${browsing}"]`) : close(); return; }
    if (e.key === 'ArrowLeft' && step === 2 && !e.target.closest('.hx-mp-segs')) { go(1, `[data-provider="${browsing}"]`); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      const rows = [...track.querySelectorAll('.hx-mp-pane')[step - 1].querySelectorAll('.hx-mp-row')];
      const i = rows.indexOf(document.activeElement);
      if (i < 0) return;
      e.preventDefault();
      rows[(i + (e.key === 'ArrowDown' ? 1 : rows.length - 1)) % rows.length].focus();
    }
  }

  window.hxModelPicker = { open: show, close, get: () => Object.assign({}, choice) };
  tick();
  setInterval(tick, 700);
  setInterval(() => { if (pending && sid()) tick(); }, 150);  // save a held new-chat pick before the first request runs
})();
