/* Tabbie — honest Settings pages (plain JS, no build step). Pairs with settings-tidy.css.
 * Upstream's Settings describe the Hermes hub on its own; in Tabbie the work runs on Claude Code and
 * Codex with your subscriptions, so three panes said misleading things. This replaces those parts:
 *  1. Providers: upstream's list of ~60 API-key providers (incl. "Solstice", a pre-release Gemini
 *     sign-in that the hub lists as "API key configured" even when nobody signed in) is removed.
 *     In its place: Claude Code and Codex (plan, signed in or not, link to manage the plan), the hub's
 *     own login, services set up on this computer (Higgsfield, ElevenLabs, ... from MCP configs and
 *     CLIs), and model providers linked in the model picker. Data: GET …/sidecar/connections (names
 *     and plan labels only, never a key).
 *  2. Preferences: upstream's "Default Model" (the hub's, ChatGPT-only list) becomes two rows: "Model",
 *     the model new chats start on, kept in sync with the composer's picker both ways; and "Hub model"
 *     (secondary), newest Claude and ChatGPT models, picking Claude links Claude Code's login first (no
 *     API key). The hub-internal "Auxiliary Models" block is removed.
 *  3. System: upstream's version badges + "Check now" ("Agent: 7119 updates available", a raw count of
 *     upstream commits Tabbie deliberately doesn't install) is removed. Tabbie's own row (updates.js:
 *     Check for updates / See update) stays the one updater; a short line below it names the upstream
 *     versions Tabbie is built on and why they aren't updated from here.
 * Nodes are removed, not hidden, before the panes are ever shown, so upstream copy never flashes.
 */
(() => {
  'use strict';
  if (window.__hxSettingsTidy) return;
  window.__hxSettingsTidy = true;

  const API = 'api/extensions/cc-agents/sidecar/connections';
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const deI18n = (el) => { if (el) { el.removeAttribute('data-i18n'); el.removeAttribute('data-i18n-title'); } return el; };
  const ext = (url, label) => url ? `<a class="hx-st-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}<span aria-hidden="true"> ↗</span></a>` : '';

  async function getJSON(url) {
    const r = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(j.error || String(r.status)); e.status = r.status; throw e; }
    return j;
  }

  // ── 1. Providers ────────────────────────────────────────────────────────────────────────────────
  const HUB_LOGIN = { 'openai-codex': 'ChatGPT sign-in (the same one Codex uses)', anthropic: 'Claude sign-in (from Claude Code)' };
  let provBody = null, provLoading = false;

  function tidyProviders() {
    const pane = $('settingsPaneProviders');
    if (!pane || pane.dataset.hxTidy) return;
    pane.dataset.hxTidy = '1';
    // Removing #providersList makes upstream's loadProvidersPanel() a no-op (it returns when the list is missing).
    ['providersList', 'providersEmpty'].forEach((id) => { const n = $(id); if (n) n.remove(); });
    const title = deI18n(pane.querySelector('.settings-section-title'));
    const meta = deI18n(pane.querySelector('.settings-section-meta'));
    if (title) title.textContent = 'Accounts';
    if (meta) meta.textContent = 'What Tabbie runs on and what it can use. Your subscriptions do the work, so there are no API keys to manage here.';
    provBody = document.createElement('div');
    provBody.id = 'hxAccounts';
    provBody.innerHTML = skeleton();
    pane.appendChild(provBody);
    const item = document.querySelector('.side-menu-item[data-settings-section="providers"] span');
    if (item) { deI18n(item); item.textContent = 'Accounts'; }
    const opt = document.querySelector('#settingsSectionDropdown option[value="providers"]');
    if (opt) { deI18n(opt); opt.textContent = 'Accounts'; }
    provBody.addEventListener('click', (e) => {
      const b = e.target.closest('[data-hx-act]');
      if (!b) return;
      if (b.dataset.hxAct === 'refresh') loadProviders(true);
      if (b.dataset.hxAct === 'prefs' && typeof window.switchSettingsSection === 'function') window.switchSettingsSection('preferences');
    });
    if (pane.classList.contains('active')) loadProviders(false);
  }

  const skeleton = () => `<section class="hx-st-group"><h3>Does the work</h3>${'<div class="hx-st-card hx-st-skel"><i></i><i></i></div>'.repeat(2)}</section>`;

  function pill(w) {
    if (!w.installed) return '<span class="hx-st-pill is-off">Not installed</span>';
    if (w.signed_in === null || w.signed_in === undefined) return '<span class="hx-st-pill">Couldn’t check</span>';
    if (!w.signed_in) return '<span class="hx-st-pill is-warn">Not signed in</span>';
    if (w.enabled === false) return '<span class="hx-st-pill is-off">Off in Tabbie</span>';
    return '<span class="hx-st-pill is-ok">Signed in</span>';
  }

  function workerCard(w) {
    const role = w.id === 'claude' ? 'Runs Claude models (Opus, Sonnet, Haiku…)' : 'Runs GPT models (Sol, Terra, Luna…)';
    let sub = w.plan ? `${esc(w.plan)} · your ${esc(w.account)} subscription` : `Uses your ${esc(w.account)} subscription`;
    let fix = '';
    if (!w.installed) fix = `Install ${esc(w.name)} on the computer to use it.`;
    else if (w.signed_in === false) fix = `${esc(w.note || 'Not signed in.')} Run <code>${esc(w.signin)}</code> in the Terminal on the computer.`;
    else if (w.enabled === false) fix = 'Turned off for Tabbie. Turn it on from the model picker in the composer.';
    return `<div class="hx-st-card"><div class="hx-st-main"><b>${esc(w.name)}</b><span>${sub}</span><span class="hx-st-dim">${role}</span>`
      + (fix ? `<span class="hx-st-fix">${fix}</span>` : '') + `</div><div class="hx-st-side">${pill(w)}${ext(w.manage_url, 'Manage plan')}</div></div>`;
  }

  async function hubCard() {
    let s = {};
    try { s = await getJSON('api/settings'); } catch (_) {}
    let model = s.default_model || '';
    let named = false;
    try {  // Tabbie's own names (same as the Preferences picker), else upstream's label
      const h = await getJSON('api/extensions/cc-agents/sidecar/hub');
      const cur = h.current || {};
      if (cur.provider) { s.default_model_provider = cur.provider; model = cur.model || model; }
      for (const r of ((h.models || {})[cur.provider] || [])) if (r.id === model) { model = r.name; named = true; }
    } catch (_) {}
    if (!named) try {
      const m = await getJSON('api/models');
      for (const g of (m.groups || [])) for (const x of (g.models || [])) if (x.id === model && x.label) model = x.label;
    } catch (_) {}
    const prov = s.default_model_provider || '';
    const login = HUB_LOGIN[prov] || prov || 'not signed in';
    return `<div class="hx-st-card"><div class="hx-st-main"><b>Hub</b><span>${esc(model || 'No model set')} · ${esc(login)}</span>`
      + `<span class="hx-st-dim">Reads each message and hands the work to Claude Code or Codex. It doesn’t do the work itself.</span></div>`
      + `<div class="hx-st-side"><button type="button" class="hx-st-btn" data-hx-act="prefs">Change</button></div></div>`;
  }

  function serviceRow(s) {
    return `<div class="hx-st-row"><div class="hx-st-main"><b>${esc(s.name)}</b>${s.what ? `<span>${esc(s.what)}</span>` : ''}`
      + `<span class="hx-st-dim">Set up in ${esc((s.where || []).join(' · '))}</span></div><div class="hx-st-side">${ext(s.url, 'Open')}</div></div>`;
  }

  async function loadProviders(force) {
    if (!provBody || provLoading) return;
    provLoading = true;
    const btn = provBody.querySelector('[data-hx-act="refresh"]');
    if (btn) { btn.disabled = true; btn.textContent = 'Checking…'; }
    let j = null, err = '';
    try { j = await getJSON(API + (force ? '?force=1' : '')); }
    catch (e) { err = e.status === 404 ? 'Tabbie’s helper is out of date: restart it (bin/hub restart) to see your accounts.' : 'Allow Tabbie’s helper (Settings → Extensions) to see your accounts here.'; }
    const hub = await hubCard();
    provLoading = false;
    if (!j) {
      provBody.innerHTML = `<section class="hx-st-group"><h3>Does the work</h3><p class="hx-st-note">${esc(err)}</p></section>`
        + `<section class="hx-st-group"><h3>Hub</h3>${hub}</section>`;
      return;
    }
    const services = j.services || [], linked = j.providers || [];
    provBody.innerHTML =
      `<section class="hx-st-group"><div class="hx-st-head"><h3>Does the work</h3><button type="button" class="hx-st-btn" data-hx-act="refresh">Check again</button></div>`
      + (j.workers || []).map(workerCard).join('')
      + `<p class="hx-st-note">Pick which model runs a chat from the model picker in the composer.</p></section>`
      + `<section class="hx-st-group"><h3>Hub</h3>${hub}</section>`
      + `<section class="hx-st-group"><h3>Connected services</h3>`
      + (services.length ? `<div class="hx-st-card hx-st-list">${services.map(serviceRow).join('')}</div>`
        : '<p class="hx-st-note">None set up yet.</p>')
      + `<p class="hx-st-note">Claude Code and Codex can use these while they work. They’re added in Claude Code (<code>claude mcp</code>), Codex, the hub’s config or with each service’s own command-line tool, and their sign-ins stay there.</p></section>`
      + (linked.length ? `<section class="hx-st-group"><h3>Other model providers</h3><div class="hx-st-card hx-st-list">`
        + linked.map((p) => `<div class="hx-st-row"><div class="hx-st-main"><b>${esc(p.name)}</b><span class="hx-st-dim">${esc([p.billing, p.worker && `runs in ${p.worker}`].filter(Boolean).join(' · '))}</span></div></div>`).join('')
        + `</div><p class="hx-st-note">Add or remove these from the model picker in the composer.</p></section>` : '');
  }

  // ── 2. Preferences ──────────────────────────────────────────────────────────────────────────────
  // Two rows replace upstream's "Default Model" (whose list only held logins the hub was linked to, so
  // no Claude):
  //  • Model: what new chats start on. The SAME choice as the composer's model picker (model-picker.js):
  //    GET/POST …/sidecar/executor (POST {default: true}), plus the shared browser copy + window event
  //    'hx:new-chat-model', so a pick in either place shows in the other at once.
  //  • Hub model (secondary): the model that reads each message and routes / plans it. Newest model per
  //    family for both subscription logins (GET …/sidecar/hub, or built-in names on an older helper).
  //    Picking Claude links Claude Code's login to the hub first (WebUI onboarding flow, no API key), then
  //    POST …/sidecar/hub {provider, model} saves it (hub_setup.py backs Hermes' config up first).
  // Upstream's picker never comes back: if the helper can't answer, the rows say so.
  const SIDE = 'api/extensions/cc-agents/sidecar/';
  const EXEC = SIDE + 'executor';
  const DKEY = 'hx-new-chat-model';
  const LOGIN = { anthropic: 'Claude', 'openai-codex': 'ChatGPT' };
  const HUB_GROUP = { anthropic: 'anthropic', 'openai-codex': 'openai' };  // hub login -> composer catalog key
  let workRow = null, workState = null, hubRow = null, hubState = null, claudeWorker = null;

  async function postJSON(url, body) {
    const r = await fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false) { const e = new Error(j.error || String(r.status)); e.status = r.status; throw e; }
    return j;
  }
  const readDefault = () => { try { const d = JSON.parse(localStorage.getItem(DKEY) || 'null'); return d && d.provider && d.model ? { provider: d.provider, model: d.model, effort: d.effort || null } : null; } catch (_) { return null; } };
  const keepDefault = (c) => { try { localStorage.setItem(DKEY, JSON.stringify({ provider: c.provider, model: c.model, effort: c.effort || null })); } catch (_) {} };

  function field(id, label, desc) {
    const f = document.createElement('div');
    f.className = 'settings-field';
    f.id = id;
    f.innerHTML = `<label for="${id}Sel">${esc(label)}</label><select id="${id}Sel" disabled><option>Loading…</option></select>`
      + `<div class="hx-st-hubmsg" role="status" aria-live="polite"></div><div class="hx-st-desc hx-st-fdesc">${desc}</div>`;
    return f;
  }
  const msgIn = (row, html, warn) => { const m = row && row.querySelector('.hx-st-hubmsg'); if (m) { m.innerHTML = html; m.classList.toggle('is-warn', !!warn); } };

  function tidyPreferences() {
    const pane = $('settingsPanePreferences');
    if (!pane || pane.dataset.hxTidy) return;
    const chip = $('settingsModelChip');
    if (!chip) return;
    pane.dataset.hxTidy = '1';
    const meta = deI18n(pane.querySelector('.settings-section-meta'));
    if (meta) meta.textContent = 'Defaults and behaviour for Tabbie.';
    const old = chip.closest('.settings-field');
    workRow = field('hxWorkModel', 'Model', 'Used for new chats. Picking a model in a chat’s model picker changes this too, and changing it here changes the picker.');
    hubRow = field('hxHubModel', 'Hub model', 'The model that reads each message and routes or plans it before Claude Code or Codex does the work. Most people never need to change it.');
    hubRow.classList.add('hx-st-secondary');
    if (old) { old.replaceWith(workRow); workRow.after(hubRow); }  // upstream's picker row is gone for good
    else { const head = pane.querySelector('.settings-section-head'); (head || pane.firstChild).after(workRow); workRow.after(hubRow); }
    workRow.querySelector('select').addEventListener('change', onWorkPick);
    hubRow.querySelector('select').addEventListener('change', onHubPick);
    hubRow.addEventListener('click', onHubClick);
    loadWork();
    loadHub();
    // Auxiliary models are the hub's side tasks (vision, compression, titles…), all "auto". Without the
    // container upstream's _loadAuxiliaryModels() returns early; the hub config isn't changed.
    const aux = $('auxModelsContainer');
    const auxField = aux && aux.closest('.settings-field');
    if (auxField) auxField.remove();
  }

  // ── Model (new chats): same choice as the composer picker ──
  async function loadWork() {
    try {
      const j = await getJSON(EXEC);
      const local = readDefault();
      const inCat = (c) => c && ((j.catalog.providers[c.provider] || {}).models || []).some((m) => m.id === c.model);
      // a default saved by the helper wins; otherwise this browser's (set here or in the picker) if still runnable
      const cur = j.choice && j.choice.default_saved ? j.choice : (inCat(local) ? local : j.choice);
      workState = { catalog: j.catalog, choice: { provider: cur.provider, model: cur.model, effort: cur.effort || null } };
      paintWork();
    } catch (e) {
      const sel = workRow.querySelector('select');
      sel.innerHTML = '<option>Unavailable</option>';
      msgIn(workRow, e.status === 401 || e.status === 403 ? 'Allow “Agents & questions” in Settings › Extensions to choose a model here.' : 'Can’t reach Tabbie’s helper on your computer right now. Pick a model from the composer instead.', true);
    }
  }
  function paintWork() {
    const sel = workRow && workRow.querySelector('select');
    if (!sel || !workState) return;
    const provs = workState.catalog.providers || {}, cur = workState.choice;
    sel.innerHTML = Object.entries(provs).map(([pid, p]) => {
      const title = pid === 'anthropic' ? 'Claude · Claude Code' : pid === 'openai' ? 'OpenAI · Codex' : `${p.name || pid}${p.worker ? ' · ' + p.worker : ''}`;
      return `<optgroup label="${esc(title)}">${(p.models || []).map((m) => `<option value="${esc(pid + '|' + m.id)}"${pid === cur.provider && m.id === cur.model ? ' selected' : ''}>${esc(m.name || m.id)}</option>`).join('')}</optgroup>`;
    }).join('');
    sel.disabled = false;
    sel.dataset.prev = sel.value;
  }
  async function onWorkPick(e) {
    const sel = e.target, [provider, model] = String(sel.value).split('|');
    if (!provider || !model) return;
    const prev = workState.choice;
    const effort = prev.provider === provider ? prev.effort : null;
    workState.choice = { provider, model, effort };
    keepDefault(workState.choice);
    window.dispatchEvent(new CustomEvent('hx:new-chat-model', { detail: Object.assign({}, workState.choice, { from: 'settings' }) }));
    sel.dataset.prev = sel.value;
    const name = sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : model;
    try {
      await postJSON(EXEC, { default: true, provider, model, effort: effort || '' });
      msgIn(workRow, '');
    } catch (err) {
      // an older helper only stores per-chat picks: this browser keeps it and new chats here start on it
      if (err.status === 400 && /session/i.test(err.message)) msgIn(workRow, '');
      else msgIn(workRow, `Saved on this device only (${esc(err.message)}).`, true);
    }
    if (typeof window.showToast === 'function') window.showToast(`New chats: ${name}`, 2200);
  }
  function syncWork(c) {
    if (!c || !workState) return;
    workState.choice = { provider: c.provider, model: c.model, effort: c.effort || null };
    paintWork();
  }
  window.addEventListener('hx:new-chat-model', (e) => { if (e.detail && e.detail.from !== 'settings') syncWork(e.detail); });
  window.addEventListener('storage', (e) => { if (e.key === DKEY) syncWork(readDefault()); });

  // ── Hub model ──
  async function loadHub() {
    let j = null;
    try { j = await getJSON(SIDE + 'hub'); } catch (_) {}
    if (!j) {
      // Older helper (no GET /hub): the newest names from the composer's catalog, the current hub model
      // from the WebUI's settings. It can only save each login's default model; onHubPick says so.
      let cat = null, s = {};
      try { cat = (await getJSON(EXEC)).catalog; } catch (_) {}
      try { s = await getJSON('api/settings'); } catch (_) {}
      const fromCat = (k) => (((cat || {}).providers || {})[k] || {}).models || [];
      j = { current: { provider: s.default_model_provider || '', model: s.default_model || '' }, claude_linked: false, legacy: true,
        models: { anthropic: fromCat('anthropic').map((m) => ({ id: m.id, name: m.name })),
          'openai-codex': fromCat('openai').map((m) => ({ id: m.id, name: m.name })) } };
      if (!j.models.anthropic.length && !j.models['openai-codex'].length) {
        const sel = hubRow.querySelector('select');
        sel.innerHTML = '<option>Unavailable</option>';
        msgIn(hubRow, 'Can’t reach Tabbie’s helper on your computer right now.', true);
        return;
      }
    }
    hubState = j;
    try { claudeWorker = ((await getJSON(API)).workers || []).find((w) => w.id === 'claude') || null; } catch (_) { claudeWorker = null; }
    paintHub();
  }

  function paintHub() {
    const sel = hubRow && hubRow.querySelector('select');
    if (!sel || !hubState) return;
    const cur = hubState.current || {}, models = hubState.models || {};
    const claudeOk = !claudeWorker || (claudeWorker.installed && claudeWorker.signed_in !== false);
    const group = (prov, title) => {
      const rows = (models[prov] || []).slice();
      if (cur.provider === prov && cur.model && !rows.some((r) => r.id === cur.model)) rows.unshift({ id: cur.model, name: cur.model });
      const off = prov === 'anthropic' && !claudeOk ? ' disabled' : '';
      return `<optgroup label="${esc(title)}">${rows.map((r) => `<option value="${esc(prov + '|' + r.id)}"${off}${cur.provider === prov && cur.model === r.id ? ' selected' : ''}>${esc(r.name)}</option>`).join('')}</optgroup>`;
    };
    sel.innerHTML = group('anthropic', 'Claude · your Claude plan') + group('openai-codex', 'ChatGPT · your ChatGPT plan');
    if (!sel.value || !cur.model) sel.insertAdjacentHTML('afterbegin', '<option value="" selected disabled>Not set</option>');
    sel.disabled = false;
    sel.dataset.prev = sel.value;
    if (!claudeOk) {
      msgIn(hubRow, claudeWorker && !claudeWorker.installed
        ? 'Claude models need Claude Code on the computer. Run <code>bin/setup</code> there.'
        : 'To use a Claude model here, sign Claude Code in first. <button type="button" class="hx-st-btn" data-hx-claude>Open Claude sign-in on the computer</button>', true);
    } else msgIn(hubRow, '');
  }

  async function linkClaude() {
    // The WebUI links Claude Code's own login (Keychain / ~/.claude) to the hub. Waits until it's there.
    let f = await postJSON('api/onboarding/oauth/start', { provider: 'anthropic' });
    const until = Date.now() + 90 * 1000;
    while (f.status === 'pending' || !f.status) {
      if (Date.now() > until) {
        try { await postJSON('api/onboarding/oauth/cancel', { flow_id: f.flow_id, provider: 'anthropic' }); } catch (_) {}
        throw new Error('Claude Code isn’t signed in on the computer yet.');
      }
      msgIn(hubRow, 'Linking your Claude login…');
      await new Promise((r) => setTimeout(r, Math.max(2, +f.poll_interval_seconds || 3) * 1000));
      try { Object.assign(f, await getJSON('api/onboarding/oauth/poll?flow_id=' + encodeURIComponent(f.flow_id))); } catch (_) {}
    }
    if (f.status !== 'success') throw new Error(f.error || `Linking ${f.status}.`);
  }

  async function onHubPick(e) {
    const sel = e.target, [prov, model] = String(sel.value).split('|');
    if (!prov || !model) return;
    sel.disabled = true;
    try {
      if (prov === 'anthropic' && !hubState.claude_linked) { await linkClaude(); hubState.claude_linked = true; }
      msgIn(hubRow, 'Switching…');
      const r = await postJSON(SIDE + 'hub', { provider: prov, model });
      hubState.current = { provider: r.provider, model: r.model };
      const want = sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : model;
      if (r.model && r.model !== model) {
        // an older helper saves each login's standard model only
        const opt = [...sel.options].find((o) => o.value === prov + '|' + r.model);
        if (opt) sel.value = opt.value;
        msgIn(hubRow, `The hub now runs on ${esc(opt ? opt.textContent : r.model)} with your ${esc(LOGIN[prov] || prov)} plan. To pick ${esc(want)}, restart Tabbie’s helper (<code>bin/hub restart</code>) and choose it again.`, true);
      } else {
        msgIn(hubRow, `The hub now runs on ${esc(want)} with your ${esc(LOGIN[prov] || prov)} plan.`);
      }
      sel.dataset.prev = sel.value;
      if (typeof window.showToast === 'function') window.showToast(`Hub model: ${sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : r.model}`, 2500);
    } catch (err) {
      sel.value = sel.dataset.prev || '';
      const m = (err && err.message) || 'Try again.';
      msgIn(hubRow, prov === 'anthropic'
        ? `Couldn’t switch to Claude: ${esc(m)} <button type="button" class="hx-st-btn" data-hx-claude>Open Claude sign-in on the computer</button>`
        : `Couldn’t switch: ${esc(m)}`, true);
    }
    sel.disabled = false;
  }

  async function onHubClick(e) {
    const b = e.target.closest('[data-hx-claude]');
    if (!b) return;
    b.disabled = true;
    try { await postJSON(SIDE + 'hub/claude-login', {}); b.textContent = 'Opened on the computer'; msgIn(hubRow, 'Finish signing in in the browser on the computer, then pick a Claude model again.'); }
    catch (err) { b.disabled = false; msgIn(hubRow, `Couldn’t open it: ${esc((err && err.message) || '')}`, true); }
  }

  // ── 3. System ───────────────────────────────────────────────────────────────────────────────────
  const shortVer = (v) => {
    const m = String(v || '').match(/v?\d+(?:\.\d+)+/);
    return m ? (m[0][0] === 'v' ? m[0] : 'v' + m[0]) : '';
  };

  function tidySystem() {
    const pane = $('settingsPaneSystem');
    if (!pane) return;
    const block = $('checkUpdatesBlock');
    if (block) block.remove();  // upstream's checkUpdatesNow() returns early without its button
    if ($('hxUpstreamVer')) return;
    const row = document.createElement('div');
    row.id = 'hxUpstreamVer';
    row.innerHTML = '<span data-ver>Built on Hermes</span>';
    const anchor = $('hxUpdSettings') || pane.querySelector('.settings-section-head');
    if (anchor) anchor.after(row); else pane.prepend(row);
    getJSON('api/settings').then((s) => {
      const agent = shortVer(s.agent_version), webui = shortVer(s.update_channel_version || s.webui_version);
      const parts = [agent && `Hermes Agent ${agent} (the hub)`, webui && `Hermes WebUI ${webui} (this interface)`].filter(Boolean);
      const v = row.querySelector('[data-ver]');
      v.textContent = `Built on ${parts.join(' and ') || 'Hermes'}. Tabbie doesn’t update these upstream parts from here, because an untested upstream version can break Tabbie. Tabbie updates come from the Check for updates button above.`;
      v.title = [s.agent_version && `Agent ${s.agent_version}`, s.update_channel_version && `WebUI ${s.update_channel_version}`].filter(Boolean).join('\n');
    }).catch(() => {});
  }
  // updates.js adds its Tabbie row after load: keep our line right below it.
  function placeSystemLine() {
    const row = $('hxUpstreamVer'), upd = $('hxUpdSettings');
    if (row && upd && upd.nextElementSibling !== row) upd.after(row);
  }

  function run() {
    tidyProviders();
    tidyPreferences();
    tidySystem();
    placeSystemLine();
  }

  run();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
  // Open Providers → load (and reload each time it's opened, like upstream did).
  const provPane = () => $('settingsPaneProviders');
  let wasActive = false;
  const mo = new MutationObserver(() => {
    run();
    const p = provPane();
    const active = !!(p && p.classList.contains('active'));
    if (active && !wasActive) loadProviders(false);
    wasActive = active;
  });
  const watch = () => {
    const root = $('mainSettings') || document.body;
    mo.observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
  };
  if (document.body) watch(); else document.addEventListener('DOMContentLoaded', watch);
})();
