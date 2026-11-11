/* Hermes — composer model picker. Chooses the EXECUTOR model for this conversation (the worker
 * that runs each request), replacing upstream's hub-model chip + reasoning chip (hidden in CSS).
 *   step 1: Anthropic / OpenAI / linked providers   step 2: that provider's models (+ optional effort)
 *   step 3: Providers: add an API key (Anthropic, OpenAI, DeepSeek, Kimi, GLM...), a local Ollama or a
 *           custom endpoint. Keys go straight to the computer's keychain; the page never keeps or shows one.
 *   GET/POST api/extensions/cc-agents/sidecar/providers  (list | {action: key|check|enable|disable|remove|add})
 *   step 4: Agents & behaviour (Claude Code / Codex on-off, Hand-offs, Takeover, Small commits, Test before done),
 *           reached from one compact row under the models so step 1 stays about picking a model.
 * Phone: bottom sheet. Desktop: popover above (or below) the chip. Each pane scrolls on its own inside a
 * frame sized to the room on screen, so long lists (More models) always reach the bottom.
 * Data: cc-agents sidecar via the WebUI's authenticated, consent-gated proxy:
 *   GET  api/extensions/cc-agents/sidecar/executor?id=<session>  -> {choice, catalog}
 *   GET  api/extensions/cc-agents/sidecar/executor  (no id: new-chat home) -> {choice: the default, catalog}
 *   POST api/extensions/cc-agents/sidecar/executor {id, provider, model, effort}
 *   POST api/extensions/cc-agents/sidecar/agents {enabled: ['claude', 'codex']}  (Agents row, step 1)
 *   GET  api/extensions/cc-agents/sidecar/agents -> {dynamic, dynamic_available, linked}; POST {dynamic: bool}
 *        (Agents row "Hand-offs": Claude Code and Codex may pass part of a job to each other; "Takeover": a job
 *        at one agent's usage limit carries on in the other; both on by default,
 *        greyed out unless both are enabled and signed in; "Small commits": a git job commits each working
 *        step on its own; "Test before done": acceptance tests for every code change; both on by default,
 *        and a request that says otherwise wins for that request)
 * A pick made before the chat exists is held and saved as soon as the session id appears (retried
 * while the sidecar catches up). If the computer can't be reached, the panel says so and offers Try again.
 */
(() => {
  'use strict';
  if (window.__hxModelPicker) return;
  window.__hxModelPicker = true;

  const API = 'api/extensions/cc-agents/sidecar/executor';
  const AGENTS_API = 'api/extensions/cc-agents/sidecar/agents';
  const PROVIDERS_API = 'api/extensions/cc-agents/sidecar/providers';
  const AGENTS = [['claude', 'Claude'], ['codex', 'OpenAI']];
  const PROVIDER_ICON = { anthropic: 'AiBrain01Icon', openai: 'AiMagicIcon', 'anthropic-api': 'AiBrain01Icon', 'openai-api': 'AiMagicIcon' };
  const iconFor = (p, pv) => PROVIDER_ICON[p] || ((pv || {}).billing === 'local' ? 'CpuIcon' : 'ServerStack01Icon');
  const BILLING_NOTE = {
    'pay-per-use': (n) => `Billed by ${n} per use with your API key, separate from any Claude or ChatGPT plan.`,
    subscription: (n) => `Uses your ${n} plan.`,
    local: () => 'Runs on this computer. Free.',
  };
  const EFFORT_LABEL = { '': 'Auto', low: 'Low', medium: 'Medium', high: 'High' };
  const FALLBACK = {
    providers: {
      anthropic: { name: 'Anthropic', worker: 'Claude Code', models: [
        { id: 'claude-opus-5-5', name: 'Opus 5.5', desc: 'Most capable' },
        { id: 'claude-fable-5-2', name: 'Fable 5.2', desc: 'Deepest reasoning, slower' },
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
  const OFFLINE = 'Can’t reach Tabbie on your computer right now. Showing the last model.';
  const NO_CONSENT = 'Allow “Agents & questions” in Settings › Extensions to switch models.';
  let handoffs = { dynamic: true, available: null, known: false };  // GET /agents (known: answered once)
  let step = 1;
  let browsing = 'anthropic';
  let query = '';          // step 2 model search (long lists, e.g. OpenRouter)
  let provs = null;        // step 3: GET /providers
  let provOpen = '';       // step 3: the provider row that is expanded ('' none, '+custom' the custom form)
  let provMsg = {};        // step 3: last result per provider {ok, message}
  let provBusy = '';
  let open = false;

  const modelOf = (p, m) => ((catalog.providers[p] || {}).models || []).find((x) => x.id === m);
  const nameOf = (c) => (modelOf(c.provider, c.model) || {}).name || c.name || 'Model';
  const pick = (c) => ({ provider: c.provider, model: c.model, effort: c.effort || null });
  // The model new chats start on, shared with Settings → Preferences (settings-tidy.js): the last pick here,
  // or the one set there. The helper keeps it (executor_choice, POST /executor {default}); this browser
  // copy covers an older helper and other tabs. Window event 'hx:new-chat-model' {provider, model, effort, from}.
  const DKEY = 'hx-new-chat-model';
  const readDefault = () => { try { const d = JSON.parse(localStorage.getItem(DKEY) || 'null'); return d && d.provider && d.model ? pick(d) : null; } catch (_) { return null; } };
  const keepDefault = (c) => { try { localStorage.setItem(DKEY, JSON.stringify(pick(c))); } catch (_) {} };
  function shareDefault(c) {
    keepDefault(c);
    // in a chat, the helper saves the chat's pick as the default too; on the new-chat home there's no chat yet
    if (!sid()) post(API, Object.assign({ default: true }, pick(c), { effort: c.effort || '' })).catch(() => {});
    window.dispatchEvent(new CustomEvent('hx:new-chat-model', { detail: Object.assign(pick(c), { from: 'picker' }) }));
  }
  // On the new-chat home: show it. Not held as `pending` (that would also land on any chat opened from the
  // home): the helper starts new chats on its saved default, which Settings / a pick here keep current.
  function useDefault(c) {
    if (sid() || pending || !c || !modelOf(c.provider, c.model)) return;
    choice = pick(c);
    paintChip(); if (open) paint();
  }
  window.addEventListener('hx:new-chat-model', (e) => { if (e.detail && e.detail.from !== 'picker') useDefault(e.detail); });
  window.addEventListener('storage', (e) => { if (e.key === DKEY) useDefault(readDefault()); });
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
      // new-chat home: a default saved by the helper wins; without one (or an older helper) use this browser's
      if (s === '' && j.choice && !pending) {
        const d = readDefault();
        if (j.choice.default_saved) keepDefault(j.choice);
        else if (d && modelOf(d.provider, d.model)) { choice = pick(d); post(API, Object.assign({ default: true }, d, { effort: d.effort || '' })).catch(() => {}); }
      }
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
      handoffs = fromApi(j);
    } catch (_) { /* keep the last known state */ }
    finally { if (open) paint(); }
  }
  const fromApi = (j) => ({ dynamic: j.dynamic, available: !!j.dynamic_available, known: true, takeover: j.takeover !== false,
    step_commits: j.step_commits !== false, uat: j.uat !== false });
  async function toggleHandoffs() {
    if (!handoffs.available) return;
    const next = !handoffs.dynamic;
    handoffs = Object.assign({}, handoffs, { dynamic: next });
    paint();
    try {
      const j = await post(AGENTS_API, { dynamic: next });
      if (j && typeof j.dynamic === 'boolean') handoffs = fromApi(j);
      notice = '';
    } catch (e) {
      handoffs = Object.assign({}, handoffs, { dynamic: !next });
      notice = statusOf(e) === 403 ? 'Allow “Agents & questions” in Settings › Extensions to change agents.' : 'Couldn’t save. Try again.';
    }
    paint();
  }

  // Takeover (a job at one agent's usage limit carries on in the other), Small commits and Test before
  // done: plain on/off settings, all on by default.
  async function toggleFlag(key) {
    const next = handoffs[key] === false;
    handoffs = Object.assign({}, handoffs, { [key]: next });
    paint();
    try {
      const j = await post(AGENTS_API, { [key]: next });
      if (j && typeof j[key] === 'boolean') handoffs = Object.assign({}, handoffs, { [key]: j[key] });
      notice = '';
    } catch (e) {
      handoffs = Object.assign({}, handoffs, { [key]: !next });
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
    if (!flushing) shareDefault(next);  // a pick here is also what new chats start on
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
      else { pendingTries = 0; choice = prev; setOffline(true); notice = 'Couldn’t save. Check the connection to your computer.'; }
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
    chip.innerHTML = `<span class="hx-mp-chip-ic">${icon(iconFor(choice.provider, catalog.providers[choice.provider]), 15)}</span>`
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
      + '<div class="hx-mp-panel hx-pop" role="dialog" aria-modal="true" aria-label="Choose a model">'
      + '<div class="hx-mp-grab" aria-hidden="true"></div>'
      + '<div class="hx-mp-frame"><div class="hx-mp-track"><section class="hx-mp-pane" data-step="1"></section>'
      + '<section class="hx-mp-pane" data-step="2"></section><section class="hx-mp-pane" data-step="3"></section>'
      + '<section class="hx-mp-pane" data-step="4"></section></div></div></div>';
    document.body.appendChild(root);
    panel = root.querySelector('.hx-mp-panel');
    frame = root.querySelector('.hx-mp-frame');
    track = root.querySelector('.hx-mp-track');
    root.querySelector('.hx-mp-scrim').addEventListener('click', close);
    panel.addEventListener('click', onClick);
    panel.addEventListener('keydown', onKey);
    panel.addEventListener('input', onInput);
    let y0 = null;  // phone: drag the sheet down to dismiss
    panel.addEventListener('touchstart', (e) => {
      const p = activePane();
      const inInput = e.target.closest && e.target.closest('input');
      y0 = (!p || p.scrollTop <= 0) && !inInput ? e.touches[0].clientY : null;
    }, { passive: true });
    panel.addEventListener('touchmove', (e) => {
      if (y0 == null || !phone()) return;
      const dy = Math.max(0, e.touches[0].clientY - y0);
      const p = activePane();
      if (p && p.scrollTop > 0) { y0 = null; panel.style.transition = ''; panel.style.transform = ''; return; }
      panel.style.transition = 'none'; panel.style.transform = `translateY(${dy}px)`;
    }, { passive: true });
    panel.addEventListener('touchend', (e) => {
      if (y0 == null || !phone()) return;
      const dy = e.changedTouches[0].clientY - y0; y0 = null;
      panel.style.transition = ''; panel.style.transform = '';
      if (dy > 90) close();
    });
  }

  const BACK = () => `<button type="button" class="hx-back" data-back aria-label="Back">${icon('ArrowLeft01Icon', 20)}</button>`;
  const subHead = (title) => `<header class="hx-mp-head hx-mp-head-2">${BACK()}<span class="hx-mp-title">${esc(title)}</span><span class="hx-mp-spacer"></span></header>`;

  function paneOne() {
    const rows = Object.keys(catalog.providers).map((p) => {
      const pv = catalog.providers[p];
      const cur = choice.provider === p;
      const sub = cur ? nameOf(choice) : pv.worker;
      const badge = pv.billing_label ? `<span class="hx-mp-badge" data-billing="${esc(pv.billing)}">${esc(pv.billing_label)}</span>` : '';
      return `<button type="button" class="hx-mp-row hx-mp-prov${cur ? ' is-current' : ''}" data-provider="${esc(p)}">`
        + `<span class="hx-mp-tile">${icon(iconFor(p, pv), 18)}</span>`
        + `<span class="hx-mp-text"><span class="hx-mp-name">${esc(pv.name)}${badge}</span><span class="hx-mp-desc">${esc(sub)}</span></span>`
        + `<span class="hx-mp-go">${icon('ArrowRight01Icon', 15)}</span></button>`;
    }).join('');
    // Agents and behaviour switches live on their own page (step 4); here one row sums them up.
    const on = catalog.agents || AGENTS.map((x) => x[0]);
    const names = on.map((a) => (a === 'claude' ? 'Claude Code' : a === 'codex' ? 'Codex' : a)).join(' & ');
    const flags = handoffs.known ? flagStates() : [];
    const off = flags.filter((f) => !f.on).length;
    const sum = names + (flags.length ? (off ? ` · ${off} option${off > 1 ? 's' : ''} off` : ' · all options on') : '');
    return `<header class="hx-mp-head"><span class="hx-mp-title">Model</span><span class="hx-mp-scope">${sid() ? 'This chat' : 'New chat'}</span></header>`
      + `<div class="hx-mp-list">${rows}`
      + `<button type="button" class="hx-mp-row hx-mp-add" data-manage><span class="hx-mp-tile">${icon('Add01Icon', 17)}</span>`
      + `<span class="hx-mp-text"><span class="hx-mp-name">More models</span><span class="hx-mp-desc">DeepSeek, Kimi, GLM, OpenRouter, Ollama…</span></span>`
      + `<span class="hx-mp-go">${icon('ArrowRight01Icon', 15)}</span></button></div>`
      + `<div class="hx-mp-sep" role="separator"></div>`
      + `<button type="button" class="hx-mp-row hx-mp-agentsnav" data-agentsnav><span class="hx-mp-tile">${icon('SlidersHorizontalIcon', 17)}</span>`
      + `<span class="hx-mp-text"><span class="hx-mp-name">Agents &amp; behaviour</span><span class="hx-mp-desc">${esc(sum)}</span></span>`
      + `<span class="hx-mp-go">${icon('ArrowRight01Icon', 15)}</span></button>`
      + (notice ? noteHtml() : '');
  }

  // Hand-offs, Takeover, Small commits, Test before done: current state and whether each can be used.
  function flagStates() {
    const on = catalog.agents || AGENTS.map((x) => x[0]);
    const bothOn = on.includes('claude') && on.includes('codex');
    const extra = Object.values(catalog.providers || {}).some((p) => p.endpoint);
    const unavail = handoffs.known && handoffs.available === false;
    return [
      { attr: 'data-handoffs', ic: 'ShuffleIcon', name: 'Hand-offs', sub: unavail ? 'Needs Claude Code and Codex' : 'Agents share parts of a job',
        on: handoffs.dynamic && !unavail, off: unavail },
      { attr: 'data-takeover', ic: 'RefreshIcon', name: 'Takeover', sub: bothOn || extra ? 'Switch agent at a usage limit' : 'Needs a second agent or provider',
        on: handoffs.takeover !== false && (bothOn || extra), off: !(bothOn || extra) },
      { attr: 'data-flag="step_commits"', ic: 'GitBranchIcon', name: 'Small commits', sub: 'Commit each working step', on: handoffs.step_commits !== false, off: false },
      { attr: 'data-flag="uat"', ic: 'CheckListIcon', name: 'Test before done', sub: 'Test every code change', on: handoffs.uat !== false, off: false },
    ];
  }

  // step 4: Agents & behaviour. Same data attributes and handlers as before; unavailable ones are dimmed
  // with the reason as their one-line description.
  function paneFour() {
    const on = catalog.agents || AGENTS.map((x) => x[0]);
    const tog = (attr, ic, name, sub, isOn, off) => `<button type="button" class="hx-mp-tog${isOn ? ' is-on' : ''}${off ? ' is-unavail' : ''}" ${attr}`
      + ` role="switch" aria-checked="${isOn}"${off ? ' aria-disabled="true" disabled' : ''}>`
      + `<span class="hx-mp-tog-ic">${icon(ic, 15)}</span><span class="hx-mp-tog-tx"><span class="hx-mp-tog-name">${name}</span>`
      + `<span class="hx-mp-tog-sub">${esc(sub)}</span></span><span class="hx-mp-switch" aria-hidden="true"></span></button>`;
    const agents = tog('data-agent="claude"', 'AiBrain01Icon', 'Claude Code', 'Claude plan', on.includes('claude'), false)
      + tog('data-agent="codex"', 'AiMagicIcon', 'Codex', 'ChatGPT plan', on.includes('codex'), false);
    const flags = handoffs.known ? flagStates().map((f) => tog(f.attr, f.ic, f.name, f.sub, f.on, f.off)).join('') : '';
    return subHead('Agents & behaviour')
      + `<p class="hx-mp-group">Coding agents</p><div class="hx-mp-togs" role="group" aria-label="Coding agents on this computer">${agents}</div>`
      + (flags ? `<p class="hx-mp-group">How jobs run</p><div class="hx-mp-togs" role="group" aria-label="How jobs run">${flags}</div>` : '')
      + (notice ? noteHtml() : '<p class="hx-mp-cap">Applies to every chat. A request that says otherwise wins for that request.</p>');
  }

  function paneTwo() {
    const pv = catalog.providers[browsing] || { models: [] };
    const many = (pv.models || []).length > 8;
    const q = many ? query.trim().toLowerCase() : '';
    const rows = pv.models.map((m) => {
      const on = choice.provider === browsing && choice.model === m.id;
      const hide = q && !(m.name + ' ' + m.id).toLowerCase().includes(q);
      return `<button type="button" class="hx-mp-row hx-mp-model${on ? ' is-on' : ''}" data-model="${esc(m.id)}" aria-pressed="${on}"${hide ? ' hidden' : ''} data-q="${esc((m.name + ' ' + m.id).toLowerCase())}">`
        + `<span class="hx-mp-text"><span class="hx-mp-name">${esc(m.name)}</span><span class="hx-mp-desc">${esc(m.desc)}</span></span>`
        + `<span class="hx-mp-check">${on ? icon('Tick02Icon', 18) : ''}</span></button>`;
    }).join('');
    const levels = ['', ...(catalog.efforts || [])];
    const eff = choice.effort || '';
    const seg = levels.map((l) => `<button type="button" class="hx-mp-seg${eff === l ? ' is-on' : ''}" data-effort="${l}" aria-pressed="${eff === l}">${EFFORT_LABEL[l] || l}</button>`).join('');
    return subHead(pv.name || '')
      + (pv.billing_label ? `<p class="hx-mp-cap hx-mp-bill">${esc((BILLING_NOTE[pv.billing] || (() => ''))(pv.name))}</p>` : '')
      + (many ? `<div class="hx-mp-searchwrap">${icon('Search01Icon', 15)}<input type="search" class="hx-mp-search" data-search placeholder="Search ${(pv.models || []).length} models" value="${esc(query)}" autocomplete="off" spellcheck="false"></div>` : '')
      + `<div class="hx-mp-list${many ? ' is-long' : ''}">${rows}</div>`
      + (pv.effort === false ? '' : `<div class="hx-mp-effort"><span class="hx-mp-effort-label">Effort</span><div class="hx-mp-segs" role="group" aria-label="Effort">${seg}</div></div>`)
      + (notice ? noteHtml() : '');
  }
  // ── step 3: providers ───────────────────────────────────────────────────
  async function loadProviders() {
    try {
      const r = await fetch(PROVIDERS_API, { credentials: 'same-origin', cache: 'no-store' });
      provs = r.ok ? await r.json() : { error: r.status === 403 ? NO_CONSENT : 'Couldn’t load providers.' };
    } catch (_) { provs = { error: OFFLINE }; }
    if (open && step === 3) paint();
  }
  async function provAction(data, after) {
    provBusy = data.id || '+custom';
    paint();
    try {
      const j = await post(PROVIDERS_API, data);
      if (j && j.providers) provs = Object.assign({}, provs, { providers: j.providers });
      if (j && j.result) provMsg[j.result.id] = j.result;
      else delete provMsg[data.id];
      if (after) after(j);
      await load(sid());  // the picker's provider list changes with what's linked
    } catch (e) {
      provMsg[data.id || '+custom'] = { ok: false, message: (e && e.message && !/^\d+$/.test(e.message)) ? e.message : 'Couldn’t save. Try again.' };
    } finally { provBusy = ''; if (open) paint(); }
  }
  function provRow(p) {
    const isOpen = provOpen === p.id;
    const state = p.linked ? 'Linked' : (p.auth === 'none' ? (p.enabled ? 'Not running' : 'Off') : 'Add key');
    const msg = provMsg[p.id];
    let body = '';
    if (isOpen) {
      const note = (BILLING_NOTE[p.billing] || (() => ''))(p.name);
      const runs = p.worker === 'codex' ? 'Runs in Codex.' : 'Runs in Claude Code.';
      body = `<div class="hx-mp-form" data-for="${esc(p.id)}"><p class="hx-mp-cap">${esc(note)} ${runs}</p>`;
      if (p.auth !== 'none') {
        body += `<div class="hx-mp-keyrow"><input type="password" class="hx-mp-input" data-key="${esc(p.id)}" placeholder="${p.linked ? 'Replace key' : 'Paste API key'}" autocomplete="off" autocapitalize="off" spellcheck="false" aria-label="${esc(p.name)} API key">`
          + `<button type="button" class="hx-mp-btn is-primary" data-savekey="${esc(p.id)}"${provBusy === p.id ? ' disabled' : ''}>${provBusy === p.id ? 'Checking…' : 'Save'}</button></div>`
          + (p.key_url ? `<a class="hx-mp-link" href="${esc(p.key_url)}" target="_blank" rel="noopener noreferrer">Get a key ${icon('ArrowUpRight01Icon', 13)}</a>` : '');
      } else {
        body += `<div class="hx-mp-keyrow"><button type="button" class="hx-mp-btn is-primary" data-enable="${esc(p.id)}" data-on="${p.enabled ? '' : '1'}"${provBusy === p.id ? ' disabled' : ''}>${p.enabled ? 'Turn off' : 'Turn on'}</button></div>`;
      }
      if (p.linked || p.custom) {
        body += `<div class="hx-mp-keyrow hx-mp-quiet">${p.linked ? `<button type="button" class="hx-mp-btn" data-check="${esc(p.id)}">Check</button>` : ''}`
          + `<button type="button" class="hx-mp-btn is-danger" data-remove="${esc(p.id)}">${p.custom ? 'Remove' : 'Remove key'}</button></div>`;
      }
      if (msg) body += `<p class="hx-mp-result${msg.ok ? ' is-ok' : ''}" role="status">${esc(msg.ok ? (msg.message === 'works' ? 'Linked. Its models are in the picker now.' : msg.message) : msg.message)}</p>`;
      body += '</div>';
    }
    const badge = p.billing_label ? `<span class="hx-mp-badge" data-billing="${esc(p.billing)}">${esc(p.billing_label)}</span>` : '';
    return `<div class="hx-mp-provitem${isOpen ? ' is-open' : ''}"><button type="button" class="hx-mp-row" data-prov="${esc(p.id)}" aria-expanded="${isOpen}">`
      + `<span class="hx-mp-tile">${icon(iconFor(p.id, p), 20)}</span>`
      + `<span class="hx-mp-text"><span class="hx-mp-name">${esc(p.name)}${badge}</span><span class="hx-mp-desc">${p.linked ? icon('Tick02Icon', 12) + ' ' : ''}${esc(state)}</span></span>`
      + `<span class="hx-mp-go hx-mp-chev">${icon('ArrowDown01Icon', 14)}</span></button>${body}</div>`;
  }
  function customForm() {
    const isOpen = provOpen === '+custom';
    const msg = provMsg['+custom'];
    let body = '';
    if (isOpen) {
      body = '<div class="hx-mp-form">'
        + '<input class="hx-mp-input" data-c="name" placeholder="Name (e.g. My server)" autocomplete="off">'
        + '<input class="hx-mp-input" data-c="base_url" placeholder="Base URL (https://…)" autocomplete="off" autocapitalize="off" spellcheck="false" inputmode="url">'
        + '<input class="hx-mp-input" data-c="models" placeholder="Model ids, comma separated" autocomplete="off" autocapitalize="off" spellcheck="false">'
        + '<div class="hx-mp-segs hx-mp-worker" role="group" aria-label="API type">'
        + '<button type="button" class="hx-mp-seg is-on" data-cworker="claude" aria-pressed="true">Anthropic API</button>'
        + '<button type="button" class="hx-mp-seg" data-cworker="codex" aria-pressed="false">OpenAI Responses</button></div>'
        + '<input type="password" class="hx-mp-input" data-c="key" placeholder="API key (blank for a local server)" autocomplete="off" autocapitalize="off" spellcheck="false">'
        + `<div class="hx-mp-keyrow"><button type="button" class="hx-mp-btn is-primary" data-addcustom${provBusy === '+custom' ? ' disabled' : ''}>${provBusy === '+custom' ? 'Adding…' : 'Add'}</button></div>`
        + '<p class="hx-mp-cap">Anthropic API endpoints run in Claude Code; OpenAI Responses endpoints run in Codex.</p>'
        + (msg ? `<p class="hx-mp-result${msg.ok ? ' is-ok' : ''}" role="status">${esc(msg.message)}</p>` : '')
        + '</div>';
    }
    return `<div class="hx-mp-provitem${isOpen ? ' is-open' : ''}"><button type="button" class="hx-mp-row" data-prov="+custom" aria-expanded="${isOpen}">`
      + `<span class="hx-mp-tile">${icon('Link01Icon', 20)}</span>`
      + '<span class="hx-mp-text"><span class="hx-mp-name">Custom endpoint</span><span class="hx-mp-desc">Any Anthropic or OpenAI Responses compatible API</span></span>'
      + `<span class="hx-mp-go hx-mp-chev">${icon('ArrowDown01Icon', 14)}</span></button>${body}</div>`;
  }
  function paneThree() {
    const head = subHead('More models');
    if (!provs) return head + '<p class="hx-mp-cap">Loading…</p>';
    if (provs.error) return head + `<p class="hx-mp-cap">${esc(provs.error)}</p>`;
    const all = provs.providers || [];
    const keys = all.filter((p) => p.id === 'anthropic-api' || p.id === 'openai-api');
    const rest = all.filter((p) => !keys.includes(p) && !p.custom);
    const custom = all.filter((p) => p.custom);
    return head + '<div class="hx-mp-list hx-mp-provlist">'
      + '<p class="hx-mp-group">Claude or GPT with an API key</p>' + keys.map(provRow).join('')
      + '<p class="hx-mp-group">Other providers</p>' + rest.map(provRow).join('')
      + (custom.length ? '<p class="hx-mp-group">Yours</p>' + custom.map(provRow).join('') : '')
      + customForm() + '</div>'
      + '<p class="hx-mp-cap">Keys are stored in this computer’s keychain and never shown again. Only providers you link appear in the picker.</p>';
  }

  function noteHtml() {
    return `<div class="hx-mp-note" role="status"><span>${esc(notice)}</span>`
      + (offline ? '<button type="button" class="hx-mp-retry" data-retry>Try again</button>' : '') + '</div>';
  }

  function paint() {
    const [a, b, c, d] = track.querySelectorAll('.hx-mp-pane');
    // keep what's typed in step 3 across repaints (a key being pasted, the custom form)
    const kept = {};
    c.querySelectorAll('input').forEach((i) => { if (i.value) kept[(i.dataset.key || '') + '|' + (i.dataset.c || '')] = i.value; });
    const focused = c.contains(document.activeElement) && document.activeElement.tagName === 'INPUT'
      ? (document.activeElement.dataset.key || '') + '|' + (document.activeElement.dataset.c || '') : null;
    const worker = (c.querySelector('[data-cworker].is-on') || {}).dataset;
    const panes = [a, b, c, d];
    const scrolls = panes.map((p) => [p.scrollTop, (p.querySelector('.hx-mp-list.is-long') || {}).scrollTop || 0]);
    a.innerHTML = paneOne();
    b.innerHTML = paneTwo();
    c.innerHTML = step === 3 ? paneThree() : '';
    d.innerHTML = paneFour();
    c.querySelectorAll('input').forEach((i) => {
      const k = (i.dataset.key || '') + '|' + (i.dataset.c || '');
      if (kept[k]) i.value = kept[k];
      if (k === focused) i.focus({ preventScroll: true });
    });
    if (worker && worker.cworker) setWorker(c, worker.cworker);
    fit();
    // panes are the scrollers: put back where each was (a pane's own scroll survives a repaint)
    panes.forEach((p, i) => {
      p.scrollTop = scrolls[i][0];
      const l = p.querySelector('.hx-mp-list.is-long');
      if (l) l.scrollTop = scrolls[i][1];
    });
    a.inert = step !== 1; b.inert = step !== 2; c.inert = step !== 3; d.inert = step !== 4;
    track.style.transform = step > 1 ? `translateX(-${(step - 1) * 25}%)` : '';
  }
  const activePane = () => (track ? track.querySelectorAll('.hx-mp-pane')[step - 1] : null);
  // Every pane is capped at the room on screen and scrolls inside itself; the frame takes the active
  // pane's height (animated), so the popover/sheet never runs off-screen and its bottom is reachable.
  function fit() {
    const pane = activePane();
    if (!pane) return;
    const panes = track.querySelectorAll('.hx-mp-pane');
    pane.style.maxHeight = 'none';
    const natural = pane.scrollHeight;
    const room = Math.max(160, Math.floor(place(natural + chrome()) - chrome()));
    panes.forEach((p) => { p.style.maxHeight = room + 'px'; });
    frame.style.height = Math.min(natural, room) + 'px';
  }
  // vertical space the panel adds around the frame (sheet grab handle, safe-area padding, border)
  function chrome() {
    const cs = getComputedStyle(panel);
    const grab = root.classList.contains('is-sheet') ? 13 : 0;
    return grab + (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0) + 2;
  }
  // Desktop popover: above the chip, or below it when the composer sits high (new-chat home). Returns
  // the room on that side.
  function place(h) {
    if (phone()) {
      panel.style.left = panel.style.bottom = panel.style.top = panel.style.width = '';
      return window.innerHeight - safeTop() - 16;  // the sheet stops short of the status bar / Dynamic Island
    }
    const r = chip.getBoundingClientRect();
    const w = 320;
    const need = h || panel.offsetHeight || 320;
    panel.style.width = w + 'px';
    panel.style.left = Math.max(12, Math.min(r.left, window.innerWidth - w - 12)) + 'px';
    // pick the side once per opening (sub-pages are taller than step 1: don't flip mid-way); below only
    // when a typical sub-page wouldn't fit above and there's more room below (new-chat home)
    if (side == null) side = r.top - 8 < Math.max(need, 460) + 12 && window.innerHeight - r.bottom > r.top ? 'below' : 'above';
    const below = side === 'below';
    panel.classList.toggle('is-below', below);
    if (below) { panel.style.bottom = ''; panel.style.top = (r.bottom + 8) + 'px'; }
    else { panel.style.top = ''; panel.style.bottom = Math.max(12, window.innerHeight - r.top + 8) + 'px'; }
    return below ? window.innerHeight - r.bottom - 20 : r.top - 20;  // height the popover has on that side
  }

  let side = null;  // 'above' | 'below' the chip, fixed while the popover is open
  let probe = null;
  function safeTop() {
    if (!probe) { probe = document.createElement('div'); probe.style.cssText = 'position:fixed;top:0;left:0;width:0;visibility:hidden;pointer-events:none;height:env(safe-area-inset-top,0px)'; document.body.appendChild(probe); }
    return probe.offsetHeight || 0;
  }
  // where Back / Escape / ArrowLeft lands on step 1: the row that opened this page
  const backSel = () => (step === 3 ? '[data-manage]' : step === 4 ? '[data-agentsnav]' : `[data-provider="${browsing}"]`);

  function go(n, focusSel) {
    if (n !== step && track) { const p = track.querySelectorAll('.hx-mp-pane')[n - 1]; if (p) p.scrollTop = 0; }
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
    side = null;
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
  function onResize() { root.classList.toggle('is-sheet', phone()); side = null; fit(); }

  function onClick(e) {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.hasAttribute('data-retry')) { notice = ''; offline = false; t.disabled = true; t.textContent = 'Trying…'; load(sid()); return; }
    if (t.dataset.agent) { toggleAgent(t.dataset.agent).then(loadHandoffs); return; }
    if (t.hasAttribute('data-handoffs')) { toggleHandoffs(); return; }
    if (t.hasAttribute('data-takeover')) { toggleFlag('takeover'); return; }
    if (t.dataset.flag) { toggleFlag(t.dataset.flag); return; }
    if (t.dataset.provider) { if (browsing !== t.dataset.provider) query = ''; browsing = t.dataset.provider; go(2); return; }
    if (t.hasAttribute('data-back')) { go(1, backSel()); return; }
    if (t.hasAttribute('data-agentsnav')) { go(4, '.hx-mp-tog'); return; }
    if (t.hasAttribute('data-manage')) { loadProviders(); go(3, '[data-prov]'); return; }
    if (t.dataset.prov) {
      provOpen = provOpen === t.dataset.prov ? '' : t.dataset.prov;
      paint();
      const item = track.querySelector('.hx-mp-provitem.is-open');
      if (item) item.scrollIntoView({ block: 'nearest' });
      const f = item && item.querySelector('input');
      if (f && !phone()) f.focus({ preventScroll: true });
      return;
    }
    if (t.dataset.savekey) {
      const id = t.dataset.savekey;
      const inp = track.querySelector(`[data-key="${CSS.escape(id)}"]`);
      const key = inp ? inp.value.trim() : '';
      if (!key) { provMsg[id] = { ok: false, message: 'Paste the key first.' }; paint(); return; }
      if (inp) inp.value = '';  // never left in the page
      provAction({ action: 'key', id, key });
      return;
    }
    if (t.dataset.enable) { provAction({ action: t.dataset.on ? 'enable' : 'disable', id: t.dataset.enable }); return; }
    if (t.dataset.check) { provAction({ action: 'check', id: t.dataset.check }); return; }
    if (t.dataset.remove) { provAction({ action: 'remove', id: t.dataset.remove }, () => { if (provOpen === t.dataset.remove) provOpen = ''; }); return; }
    if (t.dataset.cworker) { setWorker(track, t.dataset.cworker); return; }
    if (t.hasAttribute('data-addcustom')) { addCustom(); return; }
    if (t.dataset.model) {
      save({ provider: browsing, model: t.dataset.model, effort: choice.effort || null });
      paint();
      setTimeout(close, 140);
      return;
    }
    if (t.hasAttribute('data-effort')) {
      const effort = t.dataset.effort || null;
      const base = choice.provider === browsing ? choice : { provider: browsing, model: (catalog.providers[browsing].models[0] || {}).id };
      save({ provider: base.provider, model: base.model, effort });
      paint();
    }
  }
  function setWorker(scope, w) {
    scope.querySelectorAll('[data-cworker]').forEach((b) => { const on = b.dataset.cworker === w; b.classList.toggle('is-on', on); b.setAttribute('aria-pressed', on); });
  }
  function addCustom() {
    const v = (k) => ((track.querySelector(`[data-c="${k}"]`) || {}).value || '').trim();
    const name = v('name'), url = v('base_url'), models = v('models'), key = v('key');
    const id = (name || url.replace(/^https?:\/\//, '')).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 30) || 'custom';
    const local = /^http:\/\/(127\.0\.0\.1|localhost)/.test(url);
    if (!url || !models) { provMsg['+custom'] = { ok: false, message: 'Add the base URL and at least one model id.' }; paint(); return; }
    const worker = (track.querySelector('[data-cworker].is-on') || {}).dataset;
    const keyInput = track.querySelector('[data-c="key"]');
    if (keyInput) keyInput.value = '';
    provAction({ action: 'add', id, name: name || id, base_url: url, models, worker: worker ? worker.cworker : 'claude',
      auth: key ? 'key' : 'none', billing: local ? 'local' : 'pay-per-use', ...(key ? { key } : {}) },
    (j) => { if (j && j.providers) { provOpen = id; track.querySelectorAll('[data-c]').forEach((i) => { i.value = ''; }); } });
  }
  function onInput(e) {
    const t = e.target;
    if (!t.hasAttribute || !t.hasAttribute('data-search')) return;
    query = t.value;
    const q = query.trim().toLowerCase();
    track.querySelectorAll('.hx-mp-pane[data-step="2"] .hx-mp-model').forEach((r) => { r.hidden = !!q && !r.dataset.q.includes(q); });
    fit();
  }
  function onKey(e) {
    if (e.key === 'Escape') { e.preventDefault(); step > 1 ? go(1, backSel()) : close(); return; }
    if (e.key === 'Enter' && e.target.dataset && e.target.dataset.key) {
      e.preventDefault();
      const b = track.querySelector(`[data-savekey="${CSS.escape(e.target.dataset.key)}"]`);
      if (b) b.click();
      return;
    }
    if (e.target.tagName === 'INPUT') return;  // typing a key or a search: arrows move the caret
    if (e.key === 'ArrowLeft' && step > 1 && !e.target.closest('.hx-mp-segs')) { go(1, backSel()); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      const rows = [...track.querySelectorAll('.hx-mp-pane')[step - 1].querySelectorAll('.hx-mp-row:not([hidden]), .hx-mp-tog:not([disabled])')];
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
